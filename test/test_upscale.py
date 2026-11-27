import importlib.util
import os
import sys
import threading
import time
import types
from unittest import mock

import numpy as np
from gi.repository import GdkPixbuf, GLib
from PIL import Image

from . import MComixTest

from mcomix import image_tools
from mcomix import upscale
from mcomix.preferences import prefs
from mcomix.upscale_backends import Cancelled, ModelFailure


class FakeAdapter(object):
    """ Upscales 2x with NEAREST; optionally waits for <gate> first, and
    then checks <cancelled> like a tiled adapter. """

    def __init__(self, path, calls, runtime='directml', fail_load=False, fail=False,
                 fail_gpu=False, fail_windows_ml=False, gate=None, load_gate=None,
                 scale=2, closed=None, done=None, runtimes=None, tint=None):
        self.path, self.calls, self.runtime = path, calls, runtime
        self.tint = tint
        self.fail_load, self.fail, self.fail_gpu = fail_load, fail, fail_gpu
        self.gate, self.load_gate, self.scale = gate, load_gate, scale
        self.closed, self.done = closed, done
        # With 'auto', the fake runs on a (failing) Windows ML provider.
        self.can_fall_back = runtime == 'auto' and fail_windows_ml
        self.provider_label = 'Fast' if self.can_fall_back else 'DirectML'
        if runtimes is not None:
            runtimes.append(runtime)

    def load(self):
        if self.load_gate is not None:
            self.load_gate.wait(5)
        if self.fail_load:
            raise RuntimeError('no GPU')

    def output_size(self, width, height):
        return width * self.scale, height * self.scale

    def upscale(self, im, cancelled=None):
        self.calls.append(im.size)
        try:
            if self.gate is not None:
                self.gate.wait(5)
                if cancelled is not None and cancelled():
                    raise Cancelled()
            if self.fail_gpu or self.can_fall_back:
                raise ModelFailure('device removed')
            if self.fail:
                raise ValueError('broken')
            result = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
            if self.tint is not None:
                # Like models trained on colour photos.
                pixels = np.asarray(result).astype(np.int16) + self.tint
                result = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
            return result
        finally:
            if self.done is not None:
                self.done.set()

    def close(self):
        if self.closed is not None:
            self.closed.append(self.path)


def make_pixbuf(width, height, mode='RGB', alpha=255):
    color = (10, 20, 30, alpha) if mode == 'RGBA' else (10, 20, 30)
    return image_tools.pil_to_pixbuf(Image.new(mode, (width, height), color))


class UpscaleTest(MComixTest):

    def setUp(self):
        super().setUp()
        prefs['neural upscaling'] = True
        prefs['neural upscaling model'] = 'model.onnx'
        self.calls = []
        self.options = {}
        self.service = upscale.UpscaleService(
            lambda path, runtime: FakeAdapter(path, self.calls, runtime, **self.options),
            available=lambda: True)
        self.failures = []
        self.service.model_failed += lambda path, message: self.failures.append(message)

    def tearDown(self):
        for name in ('gate', 'load_gate'):
            if self.options.get(name) is not None:
                self.options[name].set()
        self.service.stop()
        super().tearDown()

    def pump(self, condition=None):
        """ Runs main loop callbacks until <condition> (default: no pending
        jobs) is true. """
        condition = condition or (lambda: not self.service._pending)
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5
        while not condition():
            self.assertLess(time.monotonic(), deadline, 'timeout')
            if not context.iteration(False):
                time.sleep(0.005)

    def render(self, key, pixbuf, target, rotation=0):
        result = self.service.render(key, pixbuf, target, rotation)
        if key in self.service._render_pending:
            self.pump(lambda: key not in self.service._render_pending)
            result = self.service.render(key, pixbuf, target, rotation)
        return result

    def drain(self):
        """ Runs main loop callbacks the worker may still post. """
        time.sleep(0.1)
        while GLib.MainContext.default().iteration(False):
            pass

    def upscaled_pixels(self, im):
        key = self.service.page_key('p1')
        pixbuf = image_tools.pil_to_pixbuf(im)
        self.render(key, pixbuf, (64, 48))
        self.pump()
        result = self.render(key, pixbuf, (64, 48))
        return np.asarray(image_tools.pixbuf_to_pil(result))

    def test_grayscale_stays_grayscale(self):
        self.options['tint'] = (-3, 4, -1)
        pixels = np.random.default_rng(0).integers(0, 256, (24, 32), np.uint8)
        result = self.upscaled_pixels(Image.fromarray(pixels).convert('RGB'))
        np.testing.assert_array_equal(result[..., 0], result[..., 1])
        np.testing.assert_array_equal(result[..., 1], result[..., 2])
        self.assertLessEqual(np.abs(result[::2, ::2, 0].astype(int) - pixels).max(), 3)
        self.assertEqual(self.service._cache_bytes, 64 * 48)

    def test_colour_unchanged(self):
        self.options['tint'] = (-3, 4, -1)
        pixels = np.random.default_rng(0).integers(0, 256, (24, 32), np.uint8)
        rgb = np.stack([pixels] * 3, axis=-1)
        rgb[5, 7] = (10, 200, 30)  # One coloured pixel makes a colour page.
        result = self.upscaled_pixels(Image.fromarray(rgb))
        expected = np.clip(rgb.repeat(2, 0).repeat(2, 1).astype(int) + (-3, 4, -1), 0, 255)
        np.testing.assert_array_equal(result, expected)
        self.assertEqual(self.service._cache_bytes, 64 * 48 * 3)

    def test_disabled_or_gpu_unavailable(self):
        unavailable = upscale.UpscaleService(
            lambda path, runtime: FakeAdapter(path, self.calls), available=lambda: False)
        self.addCleanup(unavailable.stop)
        for service, enabled in ((unavailable, True), (self.service, False)):
            with self.subTest(enabled=enabled):
                prefs['neural upscaling'] = enabled
                self.assertFalse(service.enabled())
                self.assertIsNone(service.render(service.page_key('p1'), make_pixbuf(100, 150),
                                                 (200, 300), 0))
                self.assertFalse(service._pending)

    def test_upscale_and_reuse(self):
        key = self.service.page_key('p1')
        pixbuf = make_pixbuf(100, 150)
        self.assertIsNone(self.render(key, pixbuf, (200, 300)))
        self.pump()
        result = self.render(key, pixbuf, (200, 300))
        self.assertEqual((result.get_width(), result.get_height()), (200, 300))
        self.assertIs(self.render(key, pixbuf, (200, 300)), result)
        other = self.render(key, pixbuf, (150, 225))
        self.assertEqual((other.get_width(), other.get_height()), (150, 225))
        rotated = self.render(key, pixbuf, (300, 200), 90)
        self.assertEqual((rotated.get_width(), rotated.get_height()), (300, 200))
        self.assertEqual(self.calls, [(100, 150)])

    def test_not_enlarged(self):
        key = self.service.page_key('p1')
        pixbuf = make_pixbuf(100, 150)
        self.assertIsNone(self.render(key, pixbuf, (100, 150)))
        self.assertFalse(self.service._pending)
        prefs['neural upscaling always'] = True
        self.render(key, pixbuf, (50, 75))
        self.pump()
        self.assertEqual(self.calls, [(100, 150)])

    def test_size_limits(self):
        prefs['neural upscaling max input size'] = 150
        self.render(self.service.page_key('big'), make_pixbuf(100, 150), (200, 300))
        self.render(self.service.page_key('tiny'), make_pixbuf(8, 8), (80, 80))
        self.assertFalse(self.service._pending)

    def test_animation(self):
        animation = GdkPixbuf.PixbufSimpleAnim.new(100, 150, 10)
        self.assertIsNone(self.render(self.service.page_key('anim'), animation, (200, 300)))
        # An animated WebP page is a static poster carrying its animation.
        poster = make_pixbuf(100, 150)
        poster.webp_animation = types.SimpleNamespace(frames=3)
        prefs['neural upscaling always'] = True
        self.assertIsNone(self.render(self.service.page_key('webp'), poster, (200, 300)))
        self.service.prefetch(self.service.page_key('webp'), poster)
        self.assertFalse(self.service._pending)

    def test_alpha(self):
        opaque = self.service.page_key('opaque')
        transparent = self.service.page_key('transparent')
        self.render(opaque, make_pixbuf(100, 150, 'RGBA', 255), (200, 300))
        self.render(transparent, make_pixbuf(100, 150, 'RGBA', 128), (200, 300))
        self.pump()
        self.assertIsNotNone(self.render(opaque, make_pixbuf(100, 150), (200, 300)))
        self.assertIn(transparent, self.service._failed)
        self.assertEqual(self.calls, [(100, 150)])

    def test_stale_result_ignored(self):
        gate = self.options['gate'] = threading.Event()
        key = self.service.page_key('p1')
        self.render(key, make_pixbuf(100, 150), (200, 300))
        self.pump(lambda: self.calls)
        self.service.invalidate()
        gate.set()
        self.pump(lambda: not self.service._worker._orders_queue)
        self.drain()
        self.assertFalse(self.service._cache)
        self.assertNotEqual(self.service.page_key('p1'), key)

    def test_model_failure(self):
        self.options['fail_load'] = True
        pixbuf = make_pixbuf(100, 150)
        self.render(self.service.page_key('p1'), pixbuf, (200, 300))
        self.service.prefetch(self.service.page_key('p0'), pixbuf)
        self.pump(lambda: self.failures)
        self.drain()
        self.assertEqual(len(self.failures), 1)
        self.assertFalse(self.service.enabled())
        self.assertIsNone(self.render(self.service.page_key('p2'), pixbuf, (200, 300)))
        self.assertFalse(self.service._pending)
        self.service.configure()
        self.assertTrue(self.service.enabled())

    def test_page_failure(self):
        self.options['fail'] = True
        key = self.service.page_key('p1')
        pixbuf = make_pixbuf(100, 150)
        self.render(key, pixbuf, (200, 300))
        self.pump()
        self.render(key, pixbuf, (200, 300))
        self.assertFalse(self.service._pending)
        self.assertEqual(len(self.calls), 1)

    def test_visible_before_prefetch(self):
        gate = self.options['gate'] = threading.Event()
        self.render(self.service.page_key('p1'), make_pixbuf(100, 150), (200, 300))
        self.pump(lambda: self.calls)
        self.service.prefetch(self.service.page_key('p2'), make_pixbuf(101, 150))
        self.render(self.service.page_key('p3'), make_pixbuf(102, 150), (204, 300))
        gate.set()
        self.pump()
        self.assertEqual(self.calls, [(100, 150), (102, 150)])

    def test_gpu_failure_disables(self):
        closed = []
        self.options.update(fail_gpu=True, closed=closed)
        pixbuf = make_pixbuf(100, 150)
        self.render(self.service.page_key('p1'), pixbuf, (200, 300))
        self.service.prefetch(self.service.page_key('p2'), pixbuf)
        self.pump(lambda: self.failures)
        self.drain()
        self.assertEqual(self.failures, ['device removed'])
        self.assertEqual(self.calls, [(100, 150)])  # Not retried for p2.
        self.assertEqual(closed, ['model.onnx'])
        self.assertFalse(self.service.enabled())
        self.assertFalse(self.service._pending)

    def test_stale_model_failure_ignored(self):
        load_gate = threading.Event()
        self.options.update(fail_load=True, load_gate=load_gate)
        pixbuf = make_pixbuf(100, 150)
        self.render(self.service.page_key('p1'), pixbuf, (200, 300))
        self.service.configure()
        self.options = {}
        key = self.service.page_key('p2')
        self.render(key, pixbuf, (200, 300))
        load_gate.set()
        self.pump()
        self.drain()
        self.assertEqual(self.failures, [])
        self.assertTrue(self.service.enabled())
        self.assertIsNotNone(self.render(key, pixbuf, (200, 300)))

    def test_navigation_cancels_unwanted_jobs(self):
        gate = self.options['gate'] = threading.Event()
        keys = [self.service.page_key('p%u' % i) for i in range(10)]
        self.service.set_wanted(keys[:1])
        self.render(keys[0], make_pixbuf(100, 150), (200, 300))
        self.pump(lambda: self.calls)
        for i, key in enumerate(keys[1:], 1):
            self.service.set_wanted([key])
            self.render(key, make_pixbuf(100 + i, 150), (300, 300))
        # Only the last page is queued, with its pixbuf.
        self.assertEqual(len(self.service._jobs), 1)
        gate.set()
        self.pump()
        self.assertEqual(self.calls, [(100, 150), (109, 150)])
        # The running job was cancelled, not failed.
        self.assertNotIn(keys[0], self.service._failed)
        self.assertNotIn(keys[0], self.service._cache)
        self.assertIn(keys[-1], self.service._cache)

    def test_cancelled_job_wanted_again(self):
        gate = self.options['gate'] = threading.Event()
        done = self.options['done'] = threading.Event()
        key = self.service.page_key('p1')
        pixbuf = make_pixbuf(100, 150)
        self.service.set_wanted([key])
        self.render(key, pixbuf, (200, 300))
        self.pump(lambda: self.calls)
        self.service.set_wanted([])
        gate.set()
        self.assertTrue(done.wait(5))
        # Back to the page before the cancellation reaches the main thread.
        self.service.set_wanted([key])
        self.pump(lambda: len(self.calls) == 2 and not self.service._pending)
        self.assertIn(key, self.service._cache)

    def test_renderings_only_kept_for_wanted_pages(self):
        key = self.service.page_key('p1')
        pixbuf = make_pixbuf(100, 150)
        self.render(key, pixbuf, (200, 300))
        self.pump()
        self.assertIsNotNone(self.render(key, pixbuf, (800, 1200)))
        self.assertIn(key, self.service._rendered)
        self.service.set_wanted([self.service.page_key('p2')])
        self.assertFalse(self.service._rendered)
        self.assertIn(key, self.service._cache)

    def test_policy_on_cache_hit(self):
        key = self.service.page_key('p1')
        pixbuf = make_pixbuf(100, 150)
        self.render(key, pixbuf, (200, 300))
        self.pump()
        self.assertIsNotNone(self.render(key, pixbuf, (200, 300)))
        # Back to 100%: conventional, and no prefetching.
        self.service.set_wanted([key])
        self.assertIsNone(self.render(key, pixbuf, (100, 150)))
        self.service.prefetch(self.service.page_key('p2'), make_pixbuf(101, 150))
        self.assertFalse(self.service._pending)
        prefs['neural upscaling always'] = True
        self.assertIsNotNone(self.render(key, pixbuf, (100, 150)))

    def test_output_too_large(self):
        with mock.patch.object(upscale, 'MAX_OUTPUT_BYTES', 200 * 300 * 3 - 1):
            key = self.service.page_key('p1')
            self.render(key, make_pixbuf(100, 150), (200, 300))
            self.pump()
        self.assertEqual(self.calls, [])
        self.assertIn(key, self.service._failed)

    def test_wrong_output_size(self):
        self.options['scale'] = 3
        key = self.service.page_key('p1')
        self.render(key, make_pixbuf(100, 150), (200, 300))
        self.pump()
        self.assertIn(key, self.service._failed)
        self.assertNotIn(key, self.service._cache)

    def test_runtime_preference(self):
        runtimes = self.options['runtimes'] = []
        prefs['neural upscaling runtime'] = 'directml'
        self.service.configure()
        self.render(self.service.page_key('p1'), make_pixbuf(100, 150), (200, 300))
        self.pump()
        self.assertEqual(runtimes, ['directml'])

    def test_windows_ml_failure_falls_back(self):
        # A failing faster provider is replaced by DirectML, without
        # disabling upscaling, until the preferences change.
        runtimes = self.options['runtimes'] = []
        self.options['fail_windows_ml'] = True
        key = self.service.page_key('p1')
        self.render(key, make_pixbuf(100, 150), (200, 300))
        self.pump(lambda: key in self.service._cache)
        self.assertEqual(runtimes, ['auto', 'directml'])
        self.assertEqual(self.failures, [])
        self.assertEqual(self.service.runtime_label, 'DirectML')
        self.render(self.service.page_key('p2'), make_pixbuf(101, 150), (202, 300))
        self.pump()
        self.assertEqual(runtimes, ['auto', 'directml'])
        self.service.configure()
        self.render(self.service.page_key('p3'), make_pixbuf(102, 150), (204, 300))
        self.pump()
        self.assertEqual(runtimes, ['auto', 'directml', 'auto', 'directml'])

    def test_retry_reloads_adapter(self):
        # After a provider install, the retry loads the adapter again so
        # that the next page runs on the new provider.
        runtimes = self.options['runtimes'] = []
        self.render(self.service.page_key('p1'), make_pixbuf(100, 150), (200, 300))
        self.pump()
        self.service.configure()
        self.render(self.service.page_key('p2'), make_pixbuf(100, 150), (200, 300))
        self.pump()
        self.assertEqual(runtimes, ['auto', 'auto'])

    def test_default_model(self):
        prefs['neural upscaling model'] = ''
        self.assertEqual(os.path.basename(upscale.DEFAULT_MODEL), 'SPANF_YC_2x.fp16.onnx')
        self.assertTrue(os.path.isfile(upscale.DEFAULT_MODEL))
        self.assertEqual(upscale.model_path(), upscale.DEFAULT_MODEL)
        self.assertTrue(self.service.enabled())
        prefs['neural upscaling model'] = 'other.onnx'
        self.assertEqual(upscale.model_path(), 'other.onnx')


def structured_image(width, height):
    """ Returns a Mandelbrot test image (noise is out of distribution for
    super-resolution models). """
    im = Image.effect_mandelbrot((width, height), (-2.0, -1.2, 0.8, 1.2), 64)
    return Image.merge('RGB', (im, im.rotate(180), im.transpose(Image.FLIP_LEFT_RIGHT)))


def psnr(a, b):
    mse = np.mean((np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)) ** 2)
    return 10 * np.log10(255 ** 2 / max(mse, 1e-12))


class FakeSession(object):
    """ Upscales input 2x by repeating pixels, and records input sizes as
    (height, width). Input and output are float32 NCHW in [0, 1] or, with
    <nhwc>, uint8 NHWC. """

    def __init__(self, nhwc=False):
        self.nhwc = nhwc
        self.shapes = []

    def run(self, outputs, feed):
        x = feed['input']
        assert x.flags.c_contiguous
        if self.nhwc:
            assert x.dtype == np.uint8 and x.shape[0] == 1 and x.shape[3] == 3
            self.shapes.append(x.shape[1:3])
            return [x.repeat(2, axis=1).repeat(2, axis=2)]
        assert x.dtype == np.float32 and x.shape[:2] == (1, 3)
        self.shapes.append(x.shape[2:])
        return [x.repeat(2, axis=2).repeat(2, axis=3)]


class OnnxAdapterTest(MComixTest):

    IO_CONTRACTS = ('float32-nchw', 'uint8-nhwc')

    def fake_adapter(self, io='float32-nchw', **tiling):
        from mcomix.upscale_backends.onnx_sr import OnnxSrAdapter
        adapter = OnnxSrAdapter('model.onnx', **tiling)
        adapter.scale, adapter.pad_multiple, adapter.io = 2, 8, io
        adapter._input, adapter._session = 'input', FakeSession(io == 'uint8-nhwc')
        return adapter

    def assertOneTileShape(self, shapes, shape):
        self.assertEqual(set(shapes), {shape})

    def onnx_adapter(self, **tiling):
        if importlib.util.find_spec('onnxruntime') is None:
            self.skipTest('needs onnxruntime')
        from mcomix.upscale_backends import onnx_sr
        with mock.patch.object(onnx_sr, 'GPU_PROVIDERS', (onnx_sr.CPU_PROVIDER,)):
            adapter = onnx_sr.OnnxSrAdapter(upscale.DEFAULT_MODEL, **tiling)
            adapter.load()
        self.addCleanup(adapter.close)
        return adapter

    def fake_ort(self, available=('DmlExecutionProvider', 'CPUExecutionProvider'),
                 used=None, meta=None, metas=None):
        """ Installs a fake onnxruntime; returns the list of created sessions. """
        sessions = []

        class SessionOptions(object):
            def __init__(self):
                self.config = {}

            def add_session_config_entry(self, key, value):
                self.config[key] = value

        class InferenceSession(object):
            def __init__(self, path, options=None, providers=None, **kwargs):
                self.path, self.options, self.providers = path, options, providers
                self.kwargs = kwargs
                sessions.append(self)

            def get_providers(self):
                return list(used or self.providers) + ['CPUExecutionProvider']

            def get_modelmeta(self):
                return types.SimpleNamespace(custom_metadata_map=(metas or {}).get(
                    self.path, meta or {'mcomix.scale': '2', 'mcomix.pad_multiple': '8'}))

            def get_inputs(self):
                return [types.SimpleNamespace(name='input')]

        ort = types.SimpleNamespace(
            __version__='0', get_available_providers=lambda: list(available),
            SessionOptions=SessionOptions, InferenceSession=InferenceSession,
            ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL=0))
        patcher = mock.patch.dict(sys.modules, {'onnxruntime': ort})
        patcher.start()
        self.addCleanup(patcher.stop)
        return sessions

    def test_session_gpu_only(self):
        from mcomix.upscale_backends.onnx_sr import OnnxSrAdapter, gpu_provider
        sessions = self.fake_ort()
        self.assertEqual(gpu_provider(), 'DmlExecutionProvider')
        adapter = OnnxSrAdapter('model.onnx')
        adapter.load()
        session, = sessions
        self.assertEqual(session.providers, ['DmlExecutionProvider'])
        self.assertEqual(session.kwargs.get('enable_fallback'), 0)
        self.assertEqual(session.options.config.get('session.disable_cpu_ep_fallback'), '1')
        self.assertEqual(adapter.output_size(10, 20), (20, 40))

    def test_no_gpu_provider(self):
        from mcomix.upscale_backends.onnx_sr import OnnxSrAdapter, gpu_provider
        sessions = self.fake_ort(available=['CPUExecutionProvider'])
        self.assertIsNone(gpu_provider())
        with self.assertRaises(RuntimeError):
            OnnxSrAdapter('model.onnx').load()
        self.assertEqual(sessions, [])

    def test_session_on_cpu_refused(self):
        from mcomix.upscale_backends.onnx_sr import OnnxSrAdapter
        self.fake_ort(used=['CPUExecutionProvider'])
        with self.assertRaises(RuntimeError):
            OnnxSrAdapter('model.onnx').load()

    def test_invalid_metadata(self):
        from mcomix.upscale_backends.onnx_sr import OnnxSrAdapter
        for meta in ({'mcomix.scale': '16'}, {'mcomix.scale': '2', 'mcomix.pad_multiple': '0'},
                     {'mcomix.scale': '2', 'mcomix.io': 'float16-nchw'}):
            self.fake_ort(meta=meta)
            with self.assertRaises(ValueError):
                OnnxSrAdapter('model.onnx').load()

    def test_io_metadata(self):
        from mcomix.upscale_backends.onnx_sr import OnnxSrAdapter
        for meta, io in (({'mcomix.scale': '2'}, 'float32-nchw'),
                         ({'mcomix.scale': '2', 'mcomix.io': 'uint8-nhwc'}, 'uint8-nhwc')):
            self.fake_ort(meta=meta)
            adapter = OnnxSrAdapter('model.onnx')
            adapter.load()
            self.assertEqual(adapter.io, io)

    def test_pad_and_crop(self):
        adapter = self.onnx_adapter()
        self.assertEqual(adapter.io, 'uint8-nhwc')
        result = adapter.upscale(Image.new('RGB', (37, 53), (200, 100, 50)))
        self.assertEqual(result.size, (74, 106))
        self.assertEqual(result.mode, 'RGB')

    def test_tile_spans(self):
        from mcomix.upscale_backends.onnx_sr import tile_spans
        self.assertEqual(tile_spans(100, 128, 16, 8), [(0, 0, 100)])
        for length in (136, 200, 1000, 1608, 2000):
            spans = tile_spans(length, 128, 16, 8)
            self.assertEqual(spans[0][1], 0)
            self.assertEqual(spans[-1][2], length)
            for (start, kept_start, kept_end), following in zip(spans, spans[1:] + [None]):
                self.assertEqual(start % 8, 0)
                self.assertTrue(0 <= start and start + 128 <= length)
                if following is not None:
                    self.assertEqual(kept_end, following[1])
                    # Some context on both sides of every cut.
                    self.assertGreaterEqual(kept_end - following[0], 12)
                    self.assertGreaterEqual(start + 128 - kept_end, 12)
            self.assertEqual(len(spans), max(1, -(-(length - 32) // 96)))

    def test_tiles_stitched_exactly(self):
        # With a pixel-repeating model, tiles must give the full-frame result.
        im = structured_image(203, 157)
        expected = im.resize((406, 314), Image.NEAREST).tobytes()
        for io in self.IO_CONTRACTS:
            with self.subTest(io=io):
                self.assertEqual(self.fake_adapter(io).upscale(im).tobytes(), expected)
                adapter = self.fake_adapter(io, tile_size=48, tile_overlap=16)
                tiled = adapter.upscale(im)
                self.assertEqual(tiled.size, (406, 314))
                self.assertEqual(tiled.tobytes(), expected)
                shapes = adapter._session.shapes
                self.assertGreater(len(shapes), 4)
                self.assertOneTileShape(shapes, (80, 80))

    def test_narrow_image(self):
        im = structured_image(300, 20)
        for io in self.IO_CONTRACTS:
            with self.subTest(io=io):
                adapter = self.fake_adapter(io, tile_size=48, tile_overlap=16)
                tiled = adapter.upscale(im)
                self.assertEqual(tiled.tobytes(), self.fake_adapter(io).upscale(im).tobytes())
                self.assertOneTileShape(adapter._session.shapes, (24, 80))

    def test_side_below_pad_multiple(self):
        # Reflect padding handles a side shorter than the model's multiple.
        im = structured_image(20, 1000)
        expected = im.resize((40, 2000), Image.NEAREST).tobytes()
        for io in self.IO_CONTRACTS:
            with self.subTest(io=io):
                adapter = self.fake_adapter(io, tile_size=upscale.TILE_SIZE,
                                            tile_overlap=upscale.TILE_OVERLAP)
                adapter.pad_multiple = 32
                self.assertEqual(adapter.upscale(im).tobytes(), expected)

    def test_full_frame(self):
        # Full frames: no side longer than a tile with its overlap
        # (48 + 2 x 16), which bounds the input shapes a Windows ML provider
        # must support.
        for io in self.IO_CONTRACTS:
            with self.subTest(io=io):
                adapter = self.fake_adapter(io, tile_size=48, tile_overlap=16)
                adapter.upscale(structured_image(60, 70))
                self.assertEqual(adapter._session.shapes, [(72, 64)])
                adapter._session.shapes.clear()
                adapter.upscale(structured_image(30, 90))
                self.assertEqual(adapter._session.shapes, [(64, 32), (64, 32)])

    def test_tiles_cancelled(self):
        from mcomix.upscale_backends.onnx_sr import Cancelled
        for io in self.IO_CONTRACTS:
            with self.subTest(io=io):
                adapter = self.fake_adapter(io, tile_size=48, tile_overlap=16)
                with self.assertRaises(Cancelled):
                    adapter.upscale(structured_image(200, 150),
                                    lambda: len(adapter._session.shapes) >= 2)
                self.assertEqual(len(adapter._session.shapes), 2)

    def model_variants(self, fp16_runtimes, fp32_runtimes):
        """ Creates x.fp16.onnx and x.fp32.onnx; returns their paths and
        fake metadata ('mcomix.runtimes' omitted for None). """
        paths, metas = [], {}
        for name, runtimes in (('x.fp16.onnx', fp16_runtimes), ('x.fp32.onnx', fp32_runtimes)):
            path = os.path.join(self.tmp_dir, name)
            open(path, 'wb').close()
            meta = {'mcomix.scale': '2', 'mcomix.pad_multiple': '8', 'mcomix.io': 'uint8-nhwc'}
            if runtimes is not None:
                meta['mcomix.runtimes'] = runtimes
            paths.append(path)
            metas[path] = meta
        return paths, metas

    def test_variants(self):
        from mcomix.upscale_backends.onnx_sr import variants
        (fp16, fp32), metas = self.model_variants(None, None)
        os.remove(fp32)
        self.assertEqual(variants(fp16), [fp16])

    def test_directml_variant(self):
        # Whichever variant is chosen, DirectML loads the one marked for it.
        from mcomix.upscale_backends import onnx_sr
        (fp16, fp32), metas = self.model_variants('windowsml', 'directml')
        for chosen in (fp16, fp32):
            sessions = self.fake_ort(metas=metas)
            adapter = onnx_sr.OnnxSrAdapter(chosen, runtime='directml')
            adapter.load()
            self.assertEqual(adapter.loaded_path, fp32)
            self.assertEqual(sessions[-1].path, fp32)
            self.assertEqual(sessions[-1].providers, ['DmlExecutionProvider'])
        # Unmarked files suit every runtime: the chosen one is used.
        (fp16, fp32), metas = self.model_variants(None, None)
        sessions = self.fake_ort(metas=metas)
        adapter = onnx_sr.OnnxSrAdapter(fp16, runtime='directml')
        adapter.load()
        self.assertEqual(adapter.loaded_path, fp16)

    def test_windows_ml_variant(self):
        from mcomix.upscale_backends import onnx_sr, windows_ml
        (fp16, fp32), metas = self.model_variants('windowsml', 'directml')
        self.fake_ort(metas=metas)
        fast = types.SimpleNamespace(
            get_modelmeta=lambda: types.SimpleNamespace(custom_metadata_map=metas[fp16]),
            get_inputs=lambda: [types.SimpleNamespace(name='input')])
        used = []

        def create_session(path, nhwc, multiple, max_side, cache_dir):
            used.append(path)
            return fast, 'NvTensorRTRTXExecutionProvider'
        with mock.patch.object(windows_ml, 'create_session', create_session):
            adapter = onnx_sr.OnnxSrAdapter(fp32, runtime='auto', cache_dir='cache')
            adapter.load()
        self.assertEqual(used, [fp16])
        self.assertEqual(adapter.loaded_path, fp16)
        # Falling back to DirectML loads the variant marked for it.
        self.fake_ort(metas=metas)
        with mock.patch.object(windows_ml, 'create_session',
                               side_effect=windows_ml.Unavailable('none')):
            adapter = onnx_sr.OnnxSrAdapter(fp16, runtime='auto', cache_dir='cache')
            adapter.load()
        self.assertEqual(adapter.loaded_path, fp32)

    def test_auto_runtime_uses_windows_ml(self):
        from mcomix.upscale_backends import onnx_sr, windows_ml
        meta = {'mcomix.scale': '2', 'mcomix.pad_multiple': '8', 'mcomix.io': 'uint8-nhwc'}
        sessions = self.fake_ort(meta=meta)
        fast = types.SimpleNamespace(
            get_modelmeta=lambda: types.SimpleNamespace(custom_metadata_map=meta),
            get_inputs=lambda: [types.SimpleNamespace(name='input')])
        calls = []

        def create_session(path, nhwc, multiple, max_side, cache_dir):
            calls.append((nhwc, multiple, max_side, cache_dir))
            return fast, 'NvTensorRTRTXExecutionProvider'
        unregistered = []
        with mock.patch.object(windows_ml, 'create_session', create_session), \
                mock.patch.object(windows_ml, 'unregister', unregistered.append):
            adapter = onnx_sr.OnnxSrAdapter('model.onnx', tile_size=768, tile_overlap=32,
                                            runtime='auto', cache_dir='cache')
            adapter.load()
            # The CPU session only reads the metadata for the shape profile.
            self.assertEqual([s.providers for s in sessions], [['CPUExecutionProvider']])
            self.assertEqual(calls, [(True, 8, 832, 'cache')])
            self.assertIs(adapter._session, fast)
            self.assertEqual(adapter.provider_label, 'NVIDIA TensorRT-RTX')
            self.assertTrue(adapter.can_fall_back)

            def fail(outputs, feed):
                raise RuntimeError('engine lost')
            fast.run = fail
            with self.assertRaises(ModelFailure):
                adapter.upscale(Image.new('RGB', (16, 16)))
            adapter.close()
            self.assertEqual(unregistered, ['NvTensorRTRTXExecutionProvider'])

    def test_auto_runtime_falls_back_to_directml(self):
        from mcomix.upscale_backends import onnx_sr, windows_ml
        # The 'directml' runtime does not even look for Windows ML.
        self.fake_ort()
        with mock.patch.object(windows_ml, 'create_session') as create:
            onnx_sr.OnnxSrAdapter('model.onnx', runtime='directml', cache_dir='c').load()
            create.assert_not_called()

    def test_tiles_without_seams(self):
        # A tile's edge lacks the neighbouring context a full frame has
        # (about 48 dB without overlap); overlap must hide the difference.
        im = structured_image(403, 285)
        full = self.onnx_adapter().upscale(im)
        tiled = self.onnx_adapter(tile_size=96, tile_overlap=32).upscale(im)
        self.assertEqual(tiled.size, full.size)
        self.assertGreater(psnr(tiled, full), 60)


class FakeEp(object):
    """ A windowsml.ExecutionProvider. """

    def __init__(self, name, state, certification=1):
        self.name, self.ready_state, self.certification = name, state, certification
        self.library_path = 'C:/EP/%s_2.30.49.0/ep.dll' % name
        self.ensured = 0

    def ensure_ready(self):
        self.ensured += 1
        self.ready_state = 0


class WindowsMlTest(MComixTest):

    TRT = 'NvTensorRTRTXExecutionProvider'

    def setUp(self):
        super().setUp()
        from mcomix.upscale_backends import windows_ml
        self.windows_ml = windows_ml
        self.eps = [FakeEp('OpenVINOExecutionProvider', 2), FakeEp(self.TRT, 1)]
        self.sessions = []
        self.fail_load = set()
        #: Engines another viewer removes while this one fails to load them.
        self.vanish = set()
        self.catalog_error = False
        eps = self.eps
        test = self

        class EpCatalog(object):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def find_all_providers(self):
                if test.catalog_error:
                    raise OSError('catalog unavailable')
                return eps

        class SessionOptions(object):
            def __init__(self):
                self.config, self.provider_options = {}, None

            def add_session_config_entry(self, key, value):
                self.config[key] = value

            def add_provider_for_devices(self, devices, options):
                self.provider_options = options

        class InferenceSession(object):
            def __init__(self, path, sess_options=None, **kwargs):
                if path in test.vanish:
                    test.vanish.remove(path)
                    os.remove(path)
                    raise RuntimeError('cannot load %s' % path)
                if path in test.fail_load:
                    raise RuntimeError('cannot load %s' % path)
                self.path, self.options = path, sess_options
                test.sessions.append(self)

        gpu = types.SimpleNamespace(type='GPU', vendor_id=4318, device_id=11141)
        self.registered = []
        ort = types.SimpleNamespace(
            OrtHardwareDeviceType=types.SimpleNamespace(GPU='GPU'),
            get_ep_devices=lambda: [types.SimpleNamespace(ep_name=name, device=gpu)
                                    for name in self.registered],
            register_execution_provider_library=lambda name, path: self.registered.append(name),
            unregister_execution_provider_library=self.registered.remove,
            SessionOptions=SessionOptions, InferenceSession=InferenceSession)
        patcher = mock.patch.dict(sys.modules, {
            'onnxruntime': ort,
            'windowsml': types.SimpleNamespace(EpCatalog=EpCatalog)})
        patcher.start()
        self.addCleanup(patcher.stop)
        windows_ml._registered.clear()
        self.addCleanup(windows_ml._registered.clear)
        windows_ml.refresh()
        self.addCleanup(windows_ml.refresh)
        self.model = os.path.join(self.tmp_dir, 'model.onnx')
        with open(self.model, 'wb') as f:
            f.write(b'model')
        self.cache = os.path.join(self.tmp_dir, 'cache')
        self.builds = []

    def build(self, code=0):
        """ Returns a fake _build_in_child that exits with <code>. """
        def build(model, name, options, runtime_cache, context_file, warmup, nhwc):
            self.builds.append((name, options, warmup, nhwc))
            if code == 0:
                with open(context_file, 'wb') as f:
                    f.write(b'engine')
            return code
        return mock.patch.object(self.windows_ml, '_build_in_child', build)

    def test_catalog(self):
        wml = self.windows_ml
        self.assertEqual(wml.installed_provider(), self.TRT)
        self.assertIsNone(wml.installable_provider())
        self.eps[1].ready_state = 2
        self.assertIsNone(wml.installable_provider())  # Cached for the process.
        wml.refresh()
        self.assertIsNone(wml.installed_provider())
        self.assertEqual(wml.installable_provider(), self.TRT)
        self.eps[1].certification = 2  # Uncertified providers are ignored.
        wml.refresh()
        self.assertIsNone(wml.installable_provider())
        # Only providers tested with MComix are used.
        self.eps[:] = [FakeEp('OpenVINOExecutionProvider', 0)]
        wml.refresh()
        self.assertIsNone(wml.installed_provider())

    def test_no_download_without_request(self):
        wml = self.windows_ml
        self.eps[1].ready_state = 2
        with self.assertRaises(wml.Unavailable):
            wml.register(self.TRT)
        self.assertEqual(self.eps[1].ensured, 0)
        wml.install(self.TRT)
        self.assertEqual(self.eps[1].ensured, 1)

    def test_without_windows_ml(self):
        with mock.patch.dict(sys.modules, {'windowsml': None}):
            self.assertIsNone(self.windows_ml.installed_provider())
            with self.assertRaises(self.windows_ml.Unavailable):
                self.windows_ml.create_session(self.model, True, 8, 832, self.cache)

    def test_profile(self):
        options = self.windows_ml.provider_options(self.TRT, True, 8, 830)
        self.assertEqual(options, {'nv_profile_min_shapes': 'input:1x8x8x3',
                                   'nv_profile_opt_shapes': 'input:1x832x832x3',
                                   'nv_profile_max_shapes': 'input:1x832x832x3'})
        options = self.windows_ml.provider_options(self.TRT, False, 4, 64)
        self.assertEqual(options['nv_profile_min_shapes'], 'input:1x3x4x4')

    def test_engine_built_once(self):
        wml = self.windows_ml
        with self.build():
            session, name = wml.create_session(self.model, True, 8, 832, self.cache)
            self.assertEqual(name, self.TRT)
            self.assertEqual(len(self.builds), 1)
            self.assertEqual(self.builds[0][2:], ((1, 832, 832, 3), True))
            self.assertTrue(session.path.endswith('_ctx.onnx'))
            options = session.options
            self.assertEqual(options.config['session.disable_cpu_ep_fallback'], '1')
            self.assertIn('nv_runtime_cache_path', options.provider_options)
            session = None
            wml.unregister(name)
            self.assertEqual(self.registered, [])
            session, name = wml.create_session(self.model, True, 8, 832, self.cache)
            self.assertEqual(len(self.builds), 1)
            session = None
            wml.unregister(name)
            # Another model, or another profile, needs another engine.
            session, name = wml.create_session(self.model, False, 8, 832, self.cache)
            self.assertEqual(len(self.builds), 2)
            session = None
            wml.unregister(name)

    def test_failed_build_remembered(self):
        wml = self.windows_ml
        with self.build(code=1):
            with self.assertRaises(wml.Unavailable):
                wml.create_session(self.model, True, 8, 832, self.cache)
        with self.build():
            with self.assertRaises(wml.Unavailable):
                wml.create_session(self.model, True, 8, 832, self.cache)
        self.assertEqual(len(self.builds), 1)

    def test_unloadable_engine_rebuilt_once(self):
        wml = self.windows_ml
        with self.build():
            session, name = wml.create_session(self.model, True, 8, 832, self.cache)
            context_file = session.path
            self.fail_load.add(context_file)
            with self.assertRaises(wml.Unavailable):
                wml.create_session(self.model, True, 8, 832, self.cache)
            self.assertEqual(len(self.builds), 2)
            self.fail_load.clear()
            with self.assertRaises(wml.Unavailable):
                wml.create_session(self.model, True, 8, 832, self.cache)
        self.assertEqual(len(self.builds), 2)

    def test_engine_removal_failure_remembered(self):
        wml = self.windows_ml
        with self.build():
            session, name = wml.create_session(self.model, True, 8, 832, self.cache)
            context_file = session.path
            session = None
            wml.unregister(name)
            self.fail_load.add(context_file)
            with mock.patch.object(wml.os, 'remove', side_effect=PermissionError('in use')):
                with self.assertRaises(wml.Unavailable):
                    wml.create_session(self.model, True, 8, 832, self.cache)
            self.fail_load.clear()
            with self.assertRaises(wml.Unavailable):
                wml.create_session(self.model, True, 8, 832, self.cache)
        self.assertEqual(len(self.builds), 1)

    def test_engine_removed_by_another_viewer_rebuilt(self):
        wml = self.windows_ml
        with self.build():
            session, name = wml.create_session(self.model, True, 8, 832, self.cache)
            context_file = session.path
            session = None
            wml.unregister(name)
            self.vanish.add(context_file)
            session, name = wml.create_session(self.model, True, 8, 832, self.cache)
        self.assertEqual(session.path, context_file)
        self.assertEqual(len(self.builds), 2)

    def test_retry_queries_failed_catalog_again(self):
        # A failed catalog query is kept until the upscaling retry.
        self.catalog_error = True
        self.assertIsNone(self.windows_ml.installed_provider())
        self.catalog_error = False
        self.assertIsNone(self.windows_ml.installed_provider())
        service = upscale.UpscaleService(available=lambda: True)
        self.addCleanup(service.stop)
        service.configure()
        self.assertEqual(self.windows_ml.installed_provider(), self.TRT)

# vim: expandtab:sw=4:ts=4
