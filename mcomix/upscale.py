"""upscale.py - Optional neural upscaling of small pages.

A neural model enlarges each eligible page once, in a worker thread; the
result is cached and resized (LANCZOS) to whatever size the current
reading mode needs. Until the result is available, and whenever anything
fails, callers render the original page conventionally. Layout always
uses the original page dimensions.

Upscaling is only offered when a GPU can run the model; it never runs on
the CPU (see upscale_backends). A GPU failure disables it until the
preferences change.
"""

import collections
import functools
import itertools
import os
import threading
from typing import Any, Callable, Dict, FrozenSet, Iterable, Optional, Sequence, Set, Tuple

import numpy as np
from gi.repository import GdkPixbuf, GLib
from PIL import Image

from mcomix import callback
from mcomix import constants
from mcomix import image_tools
from mcomix import log
from mcomix import tools
from mcomix.i18n import _
from mcomix.preferences import prefs
from mcomix.upscale_backends import Cancelled, ModelFailure, onnx_sr
from mcomix.worker_thread import WorkerThread

#: Identifies a page: (generation, page path).
PageKey = Tuple[int, Optional[str]]

#: Bytes of upscaled images to keep in memory.
CACHE_BYTES = 256 * 1024 * 1024
#: Larger results are not computed, so that the cache holds a few pages.
MAX_OUTPUT_BYTES = CACHE_BYTES // 3
#: Larger inputs are never upscaled, whatever the size preference says.
MAX_INPUT_PIXELS = 6000000
#: Smaller inputs are not worth upscaling.
MIN_INPUT_SIZE = 16
#: Release the model after this long without requests.
IDLE_RELEASE_SECONDS = 60
#: Only the two displayed pages need final renderings.
MAX_RENDER_PAGES = 2
MAX_RENDER_BYTES = CACHE_BYTES
#: Tile size in input pixels, without overlap. Bounds device memory: the
#: session keeps its peak (on DirectML about 0.7 GB with the former SPAN-F,
#: 1.4 GB with SAFMN) until it is released.
TILE_SIZE = 768
#: Context added on each side of a tile, in input pixels.
TILE_OVERLAP = 32

# Worker order priorities (lower first).
_VISIBLE = 0
_PREFETCH = 1
_RELEASE = 2


def _image_bytes(image: Image.Image) -> int:
    return image.width * image.height * len(image.getbands())


def _is_grayscale(image: Image.Image) -> bool:
    """ Returns whether all pixels of the RGB(A) <image> have R = G = B. """
    pixels = np.asarray(image)
    # A sparse sample rejects most colour pages without a full pass.
    for sample in (pixels[::16, ::16], pixels):
        red, green, blue = sample[..., 0], sample[..., 1], sample[..., 2]
        if not (np.array_equal(red, green) and np.array_equal(green, blue)):
            return False
    return True


#: Model used when the preference is empty (see upscale_models/README.md).
DEFAULT_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'upscale_models', 'SPANF_YC_2x.fp16.onnx')


def model_path() -> str:
    """ Returns the model chosen in the preferences, else the bundled
    default model, or '' if there is none. """
    if prefs['neural upscaling model']:
        return prefs['neural upscaling model']
    return DEFAULT_MODEL if os.path.isfile(DEFAULT_MODEL) else ''


#: Compiled engines of Windows ML providers (see upscale_backends/windows_ml.py).
ENGINE_CACHE_DIR = os.path.join(constants.DATA_DIR, 'upscale_cache')
#: 'neural upscaling runtime' values.
RUNTIME_AUTO, RUNTIME_DIRECTML = onnx_sr.RUNTIME_AUTO, onnx_sr.RUNTIME_DIRECTML


def _create_adapter(path: str, runtime: str) -> Any:
    return onnx_sr.OnnxSrAdapter(path, tile_size=TILE_SIZE, tile_overlap=TILE_OVERLAP,
                                 runtime=runtime, cache_dir=ENGINE_CACHE_DIR)


@functools.lru_cache(maxsize=None)
def gpu_available() -> bool:
    """ Returns whether a GPU runtime for the models is installed. Imports
    it the first time, which takes a moment. """
    available = onnx_sr.gpu_provider() is not None
    if not available:
        log.debug('Neural upscaling unavailable: no GPU execution provider')
    return available


class UpscaleService(object):
    """ Neural upscaling cache. All public methods must be called from
    the main thread; the model is only used from the worker thread. """

    def __init__(self,
                 adapter_factory: Callable[[str, str], Any] = _create_adapter,
                 available: Callable[[], bool] = gpu_available) -> None:
        self._adapter_factory = adapter_factory
        self._available = available
        self._worker = WorkerThread(self._process, name='upscale',
                                    sort_orders=True)
        #: Job payloads by job ID, shared with the worker.
        self._jobs: Dict[int, Tuple[Any, ...]] = {}
        self._jobs_lock = threading.Lock()
        self._job_ids = itertools.count()
        self._generation = 0
        self._policy_generation = 0
        #: Bumped by configure() so the worker loads a new adapter.
        self._adapter_generation = 0
        self._configuration = self._preferences()
        self._stopped = False
        # Final resizing must not queue behind a model build or inference.
        self._render_worker = WorkerThread(self._process_render, name='upscale-render')
        self._render_lock = threading.Lock()
        self._render_jobs: Dict[PageKey, Tuple[Any, ...]] = {}
        # One GTK idle owns a bounded completion mailbox, not each buffer.
        self._render_results: Dict[PageKey, Tuple[Any, ...]] = {}
        self._render_idle: Optional[int] = None
        self._render_pending: 'collections.OrderedDict[PageKey, Tuple[Any, ...]]' = collections.OrderedDict()
        self._render_errors: Dict[PageKey, Tuple[Any, ...]] = {}
        self._render_ids = itertools.count()

        #: Upscaled images, least recently used first.
        self._cache: 'collections.OrderedDict[PageKey, Image.Image]' = \
            collections.OrderedDict()
        self._cache_bytes = 0
        #: Last rendering of wanted cached pages: (target size, rotation, pixbuf).
        self._rendered: Dict[PageKey, Tuple[Tuple[int, int], int, GdkPixbuf.Pixbuf]] = {}
        #: Displayed and upcoming pages (see set_wanted); None: all pages.
        #: Replaced, never modified, so that the worker can read it.
        self._wanted: Optional[FrozenSet[PageKey]] = None
        #: Queued or running page jobs: key -> (job ID, priority).
        self._pending: Dict[PageKey, Tuple[int, int]] = {}
        #: Pages that could not be upscaled.
        self._failed: Set[PageKey] = set()
        #: Model path that could not be loaded.
        self._failed_model: Optional[str] = None
        #: Whether a displayed page needed enlarging (for prefetch).
        self._enlarging = False
        self._idle_timer: Optional[int] = None
        #: User-visible name of the GPU runtime last loaded ('' before).
        #: Written by the worker, read by the preferences dialog.
        self.runtime_label = ''
        # Worker thread only.
        self._adapter: Any = None
        #: (model path, runtime, adapter generation) of the loaded adapter.
        self._adapter_config: Optional[Tuple[str, str, int]] = None
        #: Whether a Windows ML provider failed while running, in which
        #: case DirectML is used until the preferences change.
        self._windows_ml_failed = False
        #: (model path, book generation, policy generation) of the last failed load.
        self._load_failure: Optional[Tuple[str, int, int]] = None

    def page_key(self, path: Optional[str]) -> PageKey:
        """ Returns the key identifying the page at <path> in the current book. """
        return (self._generation, path)

    def enabled(self) -> bool:
        if self._stopped or not prefs['neural upscaling']:
            return False
        model = model_path()
        return bool(model and model != self._failed_model and self._available())

    def set_wanted(self, keys: Iterable[PageKey]) -> None:
        """ Declares the displayed and upcoming pages, before rendering
        them: work and renderings for other pages are dropped. """
        wanted = frozenset(keys)
        self._wanted = wanted
        self._enlarging = False
        self._cancel(key for key in list(self._pending) if key not in wanted)
        for key in set(self._rendered) | set(self._render_pending) | set(self._render_errors):
            if key not in wanted:
                self._forget_render(key)

    def render(self, key: PageKey, pixbuf: GdkPixbuf.Pixbuf,
               target_size: Sequence[int], rotation: int) -> Optional[GdkPixbuf.Pixbuf]:
        """ Returns <pixbuf> upscaled, resized to <target_size> and rotated by
        <rotation> (like image_tools.fit_pixbuf_to_rectangle), or None if the
        caller should render it conventionally. Missing results are requested. """
        if not self._qualifies(key, pixbuf) or (self._wanted is not None and key not in self._wanted):
            self._forget_render(key)
            return None
        if self._is_enlarged(pixbuf, target_size, rotation):
            self._enlarging = True
        elif not prefs['neural upscaling always']:
            self._forget_render(key)
            return None  # Keep any result for when the page is enlarged.
        image = self._cache.get(key)
        if image is None:
            if self._eligible(key):
                self._request(key, pixbuf, _VISIBLE)
            return None
        self._cache.move_to_end(key)
        target = (int(target_size[0]), int(target_size[1]))
        rendered = self._rendered.get(key)
        if rendered is not None and rendered[:2] == (target, rotation):
            return rendered[2]
        descriptor = (target, rotation, id(image))
        if max(target[0], 1) * max(target[1], 1) * 3 > MAX_RENDER_BYTES // MAX_RENDER_PAGES:
            self._forget_render(key)
            self._render_errors[key] = descriptor
            return None
        if self._render_errors.get(key) == descriptor:
            return None
        pending = self._render_pending.get(key)
        if pending is None or pending[1:3] != descriptor[:2] or pending[3] is not image:
            # Bound both retained output and queued work to displayed pages.
            if key not in self._render_pending and key not in self._rendered:
                while len(set(self._rendered) | set(self._render_pending)) >= MAX_RENDER_PAGES:
                    old_key = next(iter(self._render_pending or self._rendered))
                    self._forget_render(old_key)
            job = (next(self._render_ids), target, rotation, image)
            self._render_pending[key] = job
            self._rendered.pop(key, None)
            with self._render_lock:
                self._render_jobs[key] = job
                self._render_worker.clear_orders()
                self._render_worker.extend_orders(list(self._render_jobs))
        return None

    def prefetch(self, key: PageKey, pixbuf: GdkPixbuf.Pixbuf) -> None:
        """ Requests <pixbuf> in the background if the visible pages needed it. """
        if not self._qualifies(key, pixbuf):
            return
        if (self._enlarging or prefs['neural upscaling always']) and self._eligible(key):
            self._request(key, pixbuf, _PREFETCH)

    def invalidate(self) -> None:
        """ Forgets all pages, e.g. because the book was closed. Does not
        wait for the worker; late results are ignored. """
        self._generation += 1
        self._wanted = None
        self._worker.clear_orders()
        with self._jobs_lock:
            self._jobs.clear()
        self._pending.clear()
        self._cache.clear()
        self._cache_bytes = 0
        self._clear_renderings()
        self._failed.clear()

    def configure(self) -> None:
        """ Applies changed preferences. """
        configuration = self._preferences()
        old = self._configuration
        self._configuration = configuration
        if configuration[:3] == old[:3] and configuration[3:] != old[3:]:
            # Eligibility changes do not alter already computed pixels.
            # Fence active work separately from the book/model generation.
            self._policy_generation += 1
            self._worker.clear_orders()
            with self._jobs_lock:
                self._jobs.clear()
            self._pending.clear()
            self._failed.clear()
            self._clear_renderings()
        else:
            # Includes an explicit retry with unchanged preferences, e.g.
            # after a provider install: query the catalog and load again.
            self._failed_model = None
            self._windows_ml_failed = False
            self._adapter_generation += 1
            onnx_sr.windows_ml.refresh()
            self.invalidate()
        if not self.enabled():
            self._queue_release()

    def stop(self) -> None:
        """ Stops the worker; only call on exit. """
        if self._idle_timer is not None:
            GLib.source_remove(self._idle_timer)
            self._idle_timer = None
        self._stopped = True
        self.invalidate()
        # Don't wait for an engine build to finish.
        onnx_sr.windows_ml.abort_builds()
        self._render_worker.stop()
        self._worker.stop()
        self._release_adapter()

    @staticmethod
    def _preferences() -> Tuple[Any, ...]:
        return (prefs['neural upscaling'], model_path(),
                prefs['neural upscaling runtime'], prefs['neural upscaling always'],
                prefs['neural upscaling max input size'])

    def _forget_render(self, key: PageKey) -> None:
        self._rendered.pop(key, None)
        self._render_pending.pop(key, None)
        self._render_errors.pop(key, None)
        with self._render_lock:
            self._render_jobs.pop(key, None)
            self._render_results.pop(key, None)

    def _clear_renderings(self) -> None:
        self._rendered.clear()
        self._render_pending.clear()
        self._render_errors.clear()
        self._render_worker.clear_orders()
        with self._render_lock:
            self._render_jobs.clear()
            self._render_results.clear()
            if self._render_idle is not None:
                GLib.source_remove(self._render_idle)
                self._render_idle = None

    def _process_render(self, key: PageKey) -> None:
        with self._render_lock:
            job = self._render_jobs.pop(key, None)
        if job is None:
            return
        _token, target, rotation, image = job
        try:
            result = image_tools.fit_pil_to_rectangle(image, target, rotation)
        except Exception as error:
            log.warning('Could not resize upscaled %s: %r', key[1], error)
            result = None
        with self._render_lock:
            pending = self._render_pending.get(key)
            if self._stopped or pending is None or pending[0] != job[0]:
                return
            self._render_results[key] = (job, result)
            if self._render_idle is None:
                self._render_idle = GLib.idle_add(self._flush_render_results)

    def _flush_render_results(self) -> bool:
        """GTK thread: take the latest bounded results, releasing obsolete ones."""
        with self._render_lock:
            results = list(self._render_results.items())
            self._render_results.clear()
            self._render_idle = None
        for key, (job, result) in results:
            self._render_complete(key, job, result)
        return False

    def _render_complete(self, key: PageKey, job: Tuple[Any, ...],
                         result: Optional[GdkPixbuf.Pixbuf]) -> None:
        pending = self._render_pending.get(key)
        if (self._stopped or key[0] != self._generation or pending is None or pending[0] != job[0] or self._cache.get(key) is not job[3]):
            return
        self._render_pending.pop(key, None)
        if result is None:
            self._render_errors[key] = job[1:3] + (id(job[3]),)
            return
        size = result.get_rowstride() * result.get_height()
        if size > MAX_RENDER_BYTES // MAX_RENDER_PAGES:
            self._render_errors[key] = job[1:3] + (id(job[3]),)
            return
        self._rendered[key] = (job[1], job[2], result)
        self.rendered_ready(key)

    @callback.Callback
    def rendered_ready(self, key: PageKey) -> None:
        """Notifies GTK that the latest requested final rendering is ready."""

    @callback.Callback
    def _page_processed(self, key: PageKey, image: Optional[Image.Image],
                        policy_generation: int) -> None:
        if (self._stopped or key[0] != self._generation or policy_generation != self._policy_generation):
            return
        if self._wanted is not None and key not in self._wanted:
            self._pending.pop(key, None)
            return
        self.upscaled(key, image)

    @callback.Callback
    def upscaled(self, key: PageKey, image: Optional[Image.Image]) -> None:
        """ Called in the main thread when a page has been processed;
        <image> is None if it could not be upscaled. """
        self._pending.pop(key, None)
        if image is None:
            self._failed.add(key)
            return
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._cache_bytes -= _image_bytes(previous)
            self._forget_render(key)
        self._cache[key] = image
        self._cache_bytes += _image_bytes(image)
        while self._cache_bytes > CACHE_BYTES:
            old_key, old = self._cache.popitem(last=False)
            self._cache_bytes -= _image_bytes(old)
            self._forget_render(old_key)

    @callback.Callback
    def model_failed(self, path: str, message: str) -> None:
        """ Called in the main thread when upscaling has been disabled
        because the model at <path> cannot be loaded or run. """
        log.error(_('Neural upscaling disabled, cannot use "%(path)s": %(error)s'),
                  {'path': path, 'error': message})
        self._failed_model = path
        self.invalidate()

    @callback.Callback
    def _model_unusable(self, generation: int, policy_generation: int,
                        path: str, message: str) -> None:
        """ Called in the main thread when a job of <generation> could not
        load or run the model at <path>. """
        # Otherwise obsolete: the preferences or the book changed (a later
        # job retries), or already reported by an earlier job.
        if generation == self._generation and policy_generation == self._policy_generation:
            self.model_failed(path, message)

    @callback.Callback
    def _dropped(self, key: PageKey, pixbuf: GdkPixbuf.Pixbuf, priority: int,
                 policy_generation: int) -> None:
        """ Called in the main thread when the running job for <key> was
        cancelled because the page was no longer wanted. """
        if key[0] != self._generation or policy_generation != self._policy_generation:
            return
        self._pending.pop(key, None)
        if ((self._wanted is None or key in self._wanted) and self.enabled() and self._input_allowed(pixbuf)):
            # Wanted again in the meantime.
            self._request(key, pixbuf, priority)

    def _is_enlarged(self, pixbuf: GdkPixbuf.Pixbuf, target_size: Sequence[int],
                     rotation: int) -> bool:
        width, height = target_size
        if tools.rotation_swaps_axes(rotation):
            width, height = height, width
        return width > pixbuf.get_width() or height > pixbuf.get_height()

    def _qualifies(self, key: PageKey, pixbuf: GdkPixbuf.Pixbuf) -> bool:
        return (self.enabled() and key[0] == self._generation and key[1] is not None
                and not image_tools.is_animation(pixbuf)
                and getattr(pixbuf, 'webp_animation', None) is None
                and self._input_allowed(pixbuf))

    def _eligible(self, key: PageKey) -> bool:
        return key not in self._cache and key not in self._failed

    def _input_allowed(self, pixbuf: GdkPixbuf.Pixbuf) -> bool:
        width, height = pixbuf.get_width(), pixbuf.get_height()
        return all((MIN_INPUT_SIZE <= min(width, height),
                    max(width, height) < prefs['neural upscaling max input size'],
                    width * height <= MAX_INPUT_PIXELS))

    def _request(self, key: PageKey, pixbuf: GdkPixbuf.Pixbuf, priority: int) -> None:
        if priority == _VISIBLE:
            # Visible pages make queued prefetching obsolete.
            self._cancel(k for k, (job_id, prio) in list(self._pending.items())
                         if prio == _PREFETCH)
        if key in self._pending:
            return  # Queued with a higher priority, or already running.
        self._pending[key] = (self._add_job(('page', key, pixbuf, model_path(),
                                             prefs['neural upscaling runtime'],
                                             priority, self._policy_generation), priority),
                              priority)
        self._touch_idle_timer()

    def _cancel(self, keys: Any) -> None:
        """ Cancels queued jobs for <keys>; running jobs are left alone. """
        with self._jobs_lock:
            for key in list(keys):
                job_id, priority = self._pending[key]
                if self._jobs.pop(job_id, None) is not None:
                    del self._pending[key]

    def _add_job(self, job: Tuple[Any, ...], priority: int) -> int:
        job_id = next(self._job_ids)
        with self._jobs_lock:
            self._jobs[job_id] = job
        self._worker.append_order((priority, job_id))
        return job_id

    def _queue_release(self) -> None:
        self._add_job(('release',), _RELEASE)

    def _touch_idle_timer(self) -> None:
        if self._idle_timer is not None:
            GLib.source_remove(self._idle_timer)
        self._idle_timer = GLib.timeout_add_seconds(IDLE_RELEASE_SECONDS, self._idle)

    def _idle(self) -> bool:
        self._idle_timer = None
        if self._pending:
            self._touch_idle_timer()
        else:
            self._queue_release()
        return False

    def _process(self, order: Tuple[int, int]) -> None:
        """ Worker thread: runs one job. """
        with self._jobs_lock:
            job = self._jobs.pop(order[1], None)
        if job is None:
            return  # Cancelled.
        if job[0] == 'release':
            self._release_adapter()
            return
        kind, key, pixbuf, path, runtime, priority, policy_generation = job

        def stale() -> bool:
            return (self._worker.must_stop() or self._stopped or key[0] != self._generation or policy_generation != self._policy_generation)

        if stale():
            return
        if (path, key[0], policy_generation) == self._load_failure:
            return  # Already reported for this job's batch.
        if self._windows_ml_failed:
            runtime = RUNTIME_DIRECTML
        config = (path, runtime, self._adapter_generation)
        if self._adapter is None or self._adapter_config != config:
            self._release_adapter()
            try:
                adapter = self._adapter_factory(path, runtime)
                adapter.load()
            except Exception as e:
                if not self._worker.must_stop():  # Else aborted on exit.
                    self._fail_model(key, path, e, policy_generation)
                return
            self._adapter, self._adapter_config = adapter, config
            self.runtime_label = getattr(adapter, 'provider_label', '')
            log.debug('Loaded upscaling model %s on %s',
                      getattr(adapter, 'loaded_path', path), self.runtime_label)
        if stale():
            return
        size = tuple(self._adapter.output_size(pixbuf.get_width(), pixbuf.get_height()))
        if size[0] * size[1] * 3 > MAX_OUTPUT_BYTES:
            log.debug('Not upscaling %s: result too large', key[1])
            self._page_processed(key, None, policy_generation)
            return
        image = image_tools.pixbuf_to_pil(pixbuf)
        if image.mode == 'RGBA' and image.getextrema()[3][0] < 255:
            # Actually transparent: keep the existing compositing path.
            self._page_processed(key, None, policy_generation)
            return

        def unwanted() -> bool:
            wanted = self._wanted
            return wanted is not None and key not in wanted
        image = image.convert('RGB')
        try:
            result = self._adapter.upscale(image, lambda: stale() or unwanted())
            if result.size != size:
                raise ValueError('result is %ux%u, expected %ux%u' % (result.size + size))
            if _is_grayscale(image):
                # Models trained on colour photos tint grey pages; also
                # stores a third of the bytes.
                result = result.convert('L')
        except Cancelled:
            if not stale():
                self._dropped(key, pixbuf, priority, policy_generation)
            return
        except ModelFailure as e:
            fall_back = getattr(self._adapter, 'can_fall_back', False)
            self._release_adapter()
            if fall_back and not stale():
                # The faster provider broke: retry the page with DirectML.
                log.warning('Neural upscaling falls back to DirectML: %s', e)
                self._windows_ml_failed = True
                self._dropped(key, pixbuf, priority, policy_generation)
                return
            self._fail_model(key, path, e, policy_generation)
            return
        except Exception as e:
            if not stale():
                log.warning('Could not upscale %s: %r', key[1], e)
            result = None
        if not self._worker.must_stop():
            self._page_processed(key, result, policy_generation)

    def _fail_model(self, key: PageKey, path: str, error: Exception,
                    policy_generation: int) -> None:
        """ Worker thread: reports that the model at <path> is unusable. """
        self._load_failure = (path, key[0], policy_generation)
        self._model_unusable(key[0], policy_generation, path, str(error))

    def _release_adapter(self) -> None:
        if self._adapter is not None:
            self._adapter.close()
            self._adapter = self._adapter_config = None
            log.debug('Released upscaling model')

# vim: expandtab:sw=4:ts=4
