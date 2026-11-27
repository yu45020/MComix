"""onnx_sr.py - Super-resolution models exported to ONNX (tools/export_onnx.py).

The model's 'mcomix.io' metadata gives its input and output: 'uint8-nhwc'
(uint8 RGB, converted to and from floats on the device) or, for models
without it, 'float32-nchw' (float32 RGB in [0, 1], converted on the CPU).
Its metadata also gives the scale ('mcomix.scale') and the multiple the
input size must have ('mcomix.pad_multiple'); inputs are reflect-padded and
outputs cropped.

Large inputs can be processed in overlapping tiles to bound device memory
(DirectML needs about 2.5 GB per input megapixel with SAFMN). All tiles of
an image have the same shape, so the runtime compiles one shape per page
size, as it does for full frames.

Sessions run on a GPU execution provider only. ONNX Runtime normally
retries on the CPU when a provider fails, and assigns nodes the provider
does not support to the CPU; both are disabled, so such models fail to load
and provider failures raise ModelFailure.

With the 'auto' runtime, a faster vendor provider from the Windows ML
catalog (windows_ml.py) is used when one is installed and works; otherwise,
and with the 'directml' runtime, DirectML is used. Everything but creating
the session (padding, tiling, conversion) is the same for all providers.

A model can come as precision variants side by side ('x.fp16.onnx',
'x.fp32.onnx'); whichever one is chosen, the runtime uses the variant whose
'mcomix.runtimes' metadata lists it (see tools/export_onnx.py).
"""

import os
import traceback
from typing import Any, Callable, List, Mapping, Optional, Tuple

import numpy as np
from PIL import Image

from mcomix import log
from mcomix.upscale_backends import Cancelled, ModelFailure
from mcomix.upscale_backends import windows_ml

#: GPU execution providers. There is deliberately
#: no CPU fallback: CPU inference is too slow for interactive reading.
GPU_PROVIDERS = ('DmlExecutionProvider',)
#: Reads model metadata; tests patch it into GPU_PROVIDERS.
CPU_PROVIDER = 'CPUExecutionProvider'
#: Accepted 'mcomix.scale' and 'mcomix.pad_multiple' metadata.
MAX_SCALE = 8
MAX_PAD_MULTIPLE = 64
#: Accepted 'mcomix.io' metadata; the first is the default.
IO_FLOAT32_NCHW = 'float32-nchw'
IO_UINT8_NHWC = 'uint8-nhwc'
#: Runtimes: try Windows ML providers first, or only use DirectML.
RUNTIME_AUTO = 'auto'
RUNTIME_DIRECTML = 'directml'
#: Largest input side for Windows ML providers without tiling.
UNTILED_MAX_SIDE = 2048
#: 'mcomix.runtimes' metadata values (written by tools/export_onnx.py).
RUNTIMES_WINDOWS_ML = 'windowsml'
RUNTIMES_DIRECTML = 'directml'
#: Precision variants of one model, side by side (see variants()).
VARIANT_SUFFIXES = ('.fp16.onnx', '.fp32.onnx')


def variants(path: str) -> List[str]:
    """ Returns <path> followed by its existing precision siblings: for
    'x.fp16.onnx', 'x.fp32.onnx', and the reverse. """
    for suffix in VARIANT_SUFFIXES:
        if path.lower().endswith(suffix):
            base = path[:-len(suffix)]
            return [path] + [base + other for other in VARIANT_SUFFIXES
                             if other != suffix and os.path.isfile(base + other)]
    return [path]


def gpu_provider() -> Optional[str]:
    """ Returns the preferred GPU execution provider of the installed
    onnxruntime, or None if there is none (or no onnxruntime). """
    try:
        import onnxruntime as ort
    except Exception:  # Also DLL load errors.
        return None
    available = ort.get_available_providers()
    return next((p for p in GPU_PROVIDERS if p in available), None)


def _clear_exception_frames(error: BaseException) -> None:
    """ Clears the finished frames of <error> and its chained exceptions.
    A failing session method's frame references the session, which must not
    outlive the release of its provider (windows_ml.unregister()). """
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        pending += [current.__cause__, current.__context__]


def tile_window(length: int, max_window: int, overlap: int, multiple: int) -> int:
    """ Returns the smallest window, at most <max_window> pixels and a
    multiple of <multiple>, that needs as few windows as <max_window> to
    cover <length> pixels (a multiple of <multiple>) with <overlap> pixels
    of context. """
    if length <= max_window:
        return length
    count = -(-(length - 2 * overlap) // (max_window - 2 * overlap))
    window = -(-(length - 2 * overlap) // count) + 2 * overlap
    return min(max_window, -(-window // multiple) * multiple)


def tile_spans(length: int, window: int, overlap: int,
               multiple: int) -> List[Tuple[int, int, int]]:
    """ Splits <length> pixels into windows of <window> pixels, each
    starting at a multiple of <multiple>. Returns (window start, kept start,
    kept end) for each window: kept parts cover <length> exactly, and keep
    about <overlap> pixels of context from neighbouring windows. Windows
    never extend past <length>, which must be at least <window>. """
    if length <= window:
        return [(0, 0, length)]
    count = max(2, -(-(length - 2 * overlap) // (window - 2 * overlap)))
    last = length - window
    starts = sorted({min(last, round(i * last / (count - 1) / multiple) * multiple)
                     for i in range(count)})
    spans = []
    kept_start = 0
    for i, start in enumerate(starts):
        if i + 1 < len(starts):
            # Cut in the middle of the overlap with the next window.
            kept_end = (starts[i + 1] + start + window) // 2
        else:
            kept_end = length
        spans.append((start, kept_start, kept_end))
        kept_start = kept_end
    return spans


class OnnxSrAdapter(object):

    def __init__(self, path: str, tile_size: int = 0, tile_overlap: int = 0,
                 runtime: str = RUNTIME_DIRECTML, cache_dir: str = '') -> None:
        """ Inputs with a side longer than a tile with its overlap are
        processed in tiles of <tile_size> pixels plus <tile_overlap> pixels
        of context on each side; a <tile_size> of 0 disables tiling.
        Windows ML engines are cached in <cache_dir>. """
        self._path = path
        self._runtime = runtime
        self._cache_dir = cache_dir
        self._provider = ''
        self._session: Any = None
        #: The variant actually loaded (see variants()).
        self.loaded_path = path
        self.scale = 1
        self.pad_multiple = 1
        self.io = IO_FLOAT32_NCHW
        self.tile_size = tile_size
        self.tile_overlap = tile_overlap

    @property
    def provider_label(self) -> str:
        """ User-visible name of the execution provider in use. """
        return windows_ml.label(self._provider)

    @property
    def can_fall_back(self) -> bool:
        """ Whether a failing session could be replaced by DirectML. """
        return self._provider in windows_ml.PROVIDERS

    def load(self) -> None:
        import onnxruntime as ort

        self.close()
        try:
            if self._runtime == RUNTIME_AUTO and self._cache_dir:
                try:
                    self._session = self._windows_ml_session(ort)
                except windows_ml.Aborted:
                    raise  # Exiting: don't load DirectML either.
                except windows_ml.Unavailable as e:
                    log.debug('Upscaling with DirectML: %s', e)
                except Exception as e:
                    log.warning('Upscaling with DirectML, Windows ML failed: %r', e)
            if self._session is None:
                self._session = self._directml_session(ort)
            self._use_session()
        except BaseException as error:
            # A failing runtime accessor can retain its session in a traceback.
            _clear_exception_frames(error)
            self.close()
            raise

    def _variant(self, ort: Any, runtime: str) -> Tuple[str, Optional[Mapping[str, str]]]:
        """ Returns the variant of the model (the chosen file or its
        .fp16/.fp32.onnx sibling) whose 'mcomix.runtimes' metadata lists
        <runtime>, and its metadata if it was read. Files without that
        metadata suit every runtime. """
        candidates = variants(self._path)
        if len(candidates) == 1:
            return self._path, None
        metadata = []
        for path in candidates:
            meta = ort.InferenceSession(path, providers=[CPU_PROVIDER]) \
                .get_modelmeta().custom_metadata_map
            runtimes = meta.get('mcomix.runtimes')
            if runtimes is None or runtime in runtimes.split(','):
                return path, meta
            metadata.append(meta)
        return self._path, metadata[0]

    def _windows_ml_session(self, ort: Any) -> Any:
        path, meta = self._variant(ort, RUNTIMES_WINDOWS_ML)
        if meta is None:
            meta = ort.InferenceSession(path, providers=[CPU_PROVIDER]) \
                .get_modelmeta().custom_metadata_map
        # The profile depends on the model's input layout and multiple.
        self._parse_metadata(meta)
        session, self._provider = windows_ml.create_session(
            path, self.io == IO_UINT8_NHWC, self.pad_multiple,
            self._max_window() if self.tile_size > 0 else UNTILED_MAX_SIDE,
            self._cache_dir)
        self.loaded_path = path
        return session

    def _directml_session(self, ort: Any) -> Any:
        available = ort.get_available_providers()
        providers = [p for p in GPU_PROVIDERS if p in available]
        if not providers:
            raise RuntimeError('no GPU execution provider in onnxruntime %s '
                               '(available: %s)' % (ort.__version__, ', '.join(available)))
        provider = providers[0]
        options = ort.SessionOptions()
        # Shape-related nodes on the CPU are expected; don't warn about it.
        options.log_severity_level = 3
        # Required by the DirectML execution provider.
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        if provider != CPU_PROVIDER:
            # Fail to load rather than run unsupported nodes on the CPU (ONNX
            # Runtime rejects this option with the CPU provider of tests).
            options.add_session_config_entry('session.disable_cpu_ep_fallback', '1')
        path = self._variant(ort, RUNTIMES_DIRECTML)[0]
        # Without enable_fallback=0, a provider failure while creating or
        # running the session silently switches it to the CPU.
        session = ort.InferenceSession(path, options, providers=[provider],
                                       enable_fallback=0)
        if session.get_providers()[0] != provider:
            raise RuntimeError('%s is not used (session providers: %s)'
                               % (provider, ', '.join(session.get_providers())))
        self._provider = provider
        self.loaded_path = path
        return session

    def _use_session(self) -> None:
        self._parse_metadata(self._session.get_modelmeta().custom_metadata_map)
        self._input = self._session.get_inputs()[0].name

    def _parse_metadata(self, meta: Mapping[str, str]) -> None:
        self.scale = int(meta.get('mcomix.scale', 0))
        if not 1 <= self.scale <= MAX_SCALE:
            raise ValueError('%s: missing or invalid mcomix.scale metadata' % self._path)
        self.pad_multiple = int(meta.get('mcomix.pad_multiple', 1))
        if not 1 <= self.pad_multiple <= MAX_PAD_MULTIPLE:
            raise ValueError('%s: invalid mcomix.pad_multiple metadata' % self._path)
        self.io = meta.get('mcomix.io', IO_FLOAT32_NCHW)
        if self.io not in (IO_FLOAT32_NCHW, IO_UINT8_NHWC):
            raise ValueError('%s: unsupported mcomix.io metadata' % self._path)

    def _overlap(self) -> int:
        return -(-self.tile_overlap // self.pad_multiple) * self.pad_multiple

    def _max_window(self) -> int:
        """ Returns the longest tile side, overlap included. """
        multiple = self.pad_multiple
        return -(-(self.tile_size + 2 * self._overlap()) // multiple) * multiple

    def output_size(self, width: int, height: int) -> Tuple[int, int]:
        """ Returns the size of the result for a <width> x <height> image. """
        return width * self.scale, height * self.scale

    def upscale(self, im: Image.Image,
                cancelled: Optional[Callable[[], bool]] = None) -> Image.Image:
        """ Returns <im> upscaled. Between tiles, raises Cancelled if
        <cancelled> returns True. """
        width, height = im.size
        # HWC uint8, padded once; tiles are views of it.
        x = np.asarray(im if im.mode == 'RGB' else im.convert('RGB'))
        pad_h, pad_w = -height % self.pad_multiple, -width % self.pad_multiple
        if pad_h or pad_w:
            x = np.pad(x, ((0, pad_h), (0, pad_w), (0, 0)), mode='reflect')
        if self.tile_size <= 0 or max(x.shape[:2]) <= self._max_window():
            y = self._run(x)[:height * self.scale, :width * self.scale]
        else:
            y = self._run_tiles(x, width, height, cancelled)
        return Image.fromarray(np.ascontiguousarray(y), 'RGB')

    def _run(self, x: np.ndarray) -> np.ndarray:
        """ Runs the model on HWC uint8 <x>; returns HWC uint8. """
        if self.io == IO_UINT8_NHWC:
            feed = np.ascontiguousarray(x)[None]
        else:
            feed = np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)
            feed /= 255
        try:
            y = self._session.run(None, {self._input: feed})[0][0]
        except Exception as e:
            # Native accessors may retain the session in their traceback.
            _clear_exception_frames(e)
            # Device resets and competing GPU allocations can be transient.
            # The service falls back for this configuration in memory; a
            # preference change or restart may retry the same cached engine.
            raise ModelFailure('%s failed: %s' % (self.provider_label, e)) from e
        if self.io == IO_UINT8_NHWC:
            return y
        if not np.isfinite(y).all():
            raise ValueError('non-finite model output')
        np.clip(y, 0, 1, out=y)
        y *= 255
        y += 0.5
        return y.astype(np.uint8).transpose(1, 2, 0)

    def _run_tiles(self, x: np.ndarray, width: int, height: int,
                   cancelled: Optional[Callable[[], bool]]) -> np.ndarray:
        """ Runs the model on tiles of padded HWC uint8 <x>; returns the
        <width> x <height> part of the result. """
        multiple, scale = self.pad_multiple, self.scale
        overlap, max_window = self._overlap(), self._max_window()
        padded_height, padded_width = x.shape[:2]
        tile_height = tile_window(padded_height, max_window, overlap, multiple)
        tile_width = tile_window(padded_width, max_window, overlap, multiple)
        y = np.empty((height * scale, width * scale, 3), dtype=np.uint8)
        rows = tile_spans(padded_height, tile_height, overlap, multiple)
        columns = tile_spans(padded_width, tile_width, overlap, multiple)
        for top, kept_top, kept_bottom in rows:
            kept_bottom = min(kept_bottom, height)
            for left, kept_left, kept_right in columns:
                kept_right = min(kept_right, width)
                if cancelled is not None and cancelled():
                    raise Cancelled()
                out = self._run(x[top:top + tile_height, left:left + tile_width])
                y[kept_top * scale:kept_bottom * scale,
                  kept_left * scale:kept_right * scale] = \
                    out[(kept_top - top) * scale:(kept_bottom - top) * scale,
                        (kept_left - left) * scale:(kept_right - left) * scale]
        return y

    def close(self) -> None:
        """ Releases the session and its device memory. """
        self._session = None
        provider, self._provider = self._provider, ''
        if provider in windows_ml.PROVIDERS:
            windows_ml.unregister(provider)

# vim: expandtab:sw=4:ts=4
