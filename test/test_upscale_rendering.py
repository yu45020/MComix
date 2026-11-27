"""Neural final-resize ownership and eligibility policy regressions."""

import tempfile
import threading
import time
from typing import Any, Callable, Iterator
from unittest import mock
import weakref

import pytest
from gi.repository import GLib
from PIL import Image

from mcomix import image_tools, upscale
from mcomix.preferences import prefs
from .test_upscale import FakeAdapter, make_pixbuf


def pump(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    context = GLib.MainContext.default()
    while not condition():
        assert time.monotonic() < deadline, 'GTK completion timed out'
        if not context.iteration(False):
            time.sleep(0.002)


@pytest.fixture
def service(tmp_path: Any, monkeypatch: Any) -> Iterator[Any]:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    saved = dict(prefs)
    prefs.update({'neural upscaling': True,
                  'neural upscaling model': 'model.onnx',
                  'neural upscaling runtime': 'auto',
                  'neural upscaling max input size': 2048,
                  'neural upscaling always': False})
    calls = []
    instance = upscale.UpscaleService(
        lambda path, runtime: FakeAdapter(path, calls, runtime),
        available=lambda: True)
    instance.test_calls = calls
    try:
        yield instance
    finally:
        instance.stop()
        prefs.clear()
        prefs.update(saved)
        while GLib.MainContext.default().iteration(False):
            pass


def seed(service: Any, name: str = 'p1') -> Any:
    key = service.page_key(name)
    image = Image.new('RGB', (128, 192), (20, 40, 60))
    service.upscaled(key, image)
    return key, make_pixbuf(64, 96), image


def ready(service: Any, key: Any, pixbuf: Any,
          target: Any = (128, 192), rotation: int = 0) -> Any:
    result = service.render(key, pixbuf, target, rotation)
    if result is None:
        pump(lambda: key not in service._render_pending)
        result = service.render(key, pixbuf, target, rotation)
    assert result is not None
    return result


def test_real_resize_runs_off_gtk_and_heartbeat_continues(service: Any) -> None:
    key, pixbuf, image = seed(service)
    entered, release = threading.Event(), threading.Event()
    original = Image.Image.resize
    threads = []

    def gated_resize(self: Any, *args: Any, **kwargs: Any) -> Any:
        threads.append(threading.current_thread())
        entered.set()
        assert release.wait(5)
        return original(self, *args, **kwargs)

    beats = []
    notification_threads = []
    service.rendered_ready += lambda key: notification_threads.append(threading.current_thread())
    with mock.patch.object(Image.Image, 'resize', gated_resize):
        try:
            assert service.render(key, pixbuf, (100, 150), 0) is None
            assert entered.wait(5)
            GLib.idle_add(lambda: beats.append(True) and False)
            pump(lambda: bool(beats))
            assert not service._rendered
        finally:
            release.set()
        result = ready(service, key, pixbuf, (100, 150))
    assert result.get_width() == 100
    assert all(thread is not threading.main_thread() for thread in threads)
    assert notification_threads == [threading.main_thread()]
    assert not service.test_calls


def test_latest_target_rotation_only_and_bounded_queue(service: Any) -> None:
    key, pixbuf, image = seed(service)
    entered, release = threading.Event(), threading.Event()
    original = image_tools.fit_pil_to_rectangle
    calls = []

    def gated(source: Any, target: Any, rotation: int) -> Any:
        calls.append((target, rotation))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return original(source, target, rotation)

    notifications = []
    service.rendered_ready += notifications.append
    with mock.patch.object(image_tools, 'fit_pil_to_rectangle', gated):
        try:
            service.render(key, pixbuf, (100, 150), 0)
            assert entered.wait(5)
            for width in range(101, 151):
                assert service.render(key, pixbuf, (width, 200), 90) is None
                assert len(service._render_jobs) <= 1
                assert len(service._render_worker._orders_queue) <= 1
        finally:
            release.set()
        result = ready(service, key, pixbuf, (150, 200), 90)
    assert calls == [((100, 150), 0), ((150, 200), 90)]
    assert (result.get_width(), result.get_height()) == (150, 200)
    assert notifications == [key]


@pytest.mark.parametrize('failure', ['exception', 'oversized'])
def test_failed_target_is_terminal_conventional_fallback(service: Any, failure: str) -> None:
    key, pixbuf, image = seed(service)
    notifications = []
    service.rendered_ready += notifications.append
    target = (100, 150) if failure == 'exception' else (100000, 100000)
    with mock.patch.object(image_tools, 'fit_pil_to_rectangle', side_effect=ValueError('bad resize')) as fit:
        assert service.render(key, pixbuf, target, 0) is None
        pump(lambda: not service._render_pending)
        for unused in range(5):
            assert service.render(key, pixbuf, target, 0) is None
        assert fit.call_count == (1 if failure == 'exception' else 0)
    assert not notifications
    assert not service._failed


def test_policy_retains_pixels_but_enforces_eligibility(service: Any) -> None:
    key, pixbuf, image = seed(service)
    ready(service, key, pixbuf)
    generation, cache_bytes = service._generation, service._cache_bytes
    prefs['neural upscaling max input size'] = 65
    service.configure()
    assert service._generation == generation
    assert service._cache[key] is image
    assert service._cache_bytes == cache_bytes
    assert not service._rendered
    assert service.render(key, pixbuf, (128, 192), 0) is None
    assert not service._pending and not service._render_pending
    prefs['neural upscaling max input size'] = 2048
    service.configure()
    assert ready(service, key, pixbuf).get_width() == 128
    assert not service.test_calls


def test_policy_does_not_retry_failed_provider(service: Any) -> None:
    runtimes, adapters = [], []

    def create(path: str, runtime: str) -> Any:
        adapters.append(FakeAdapter(path, service.test_calls, runtime,
                                    fail_windows_ml=True, runtimes=runtimes))
        return adapters[-1]

    service._adapter_factory = create
    pixbuf = make_pixbuf(64, 96)

    def upscale(name: str) -> None:
        key = service.page_key(name)
        pump(lambda: service.render(key, pixbuf, (128, 192), 0) is not None)

    upscale('p1')
    assert runtimes == ['auto', 'directml']
    prefs['neural upscaling always'] = True
    service.configure()
    upscale('p2')
    assert runtimes == ['auto', 'directml']
    adapters[-1].fail_gpu = True
    service.render(service.page_key('p3'), pixbuf, (128, 192), 0)
    pump(lambda: not service.enabled())
    prefs['neural upscaling always'] = False
    service.configure()
    assert not service.enabled()


def test_stop_releases_adapter_and_owned_threads_once(service: Any) -> None:
    key, pixbuf, image = seed(service)
    closed = []
    service._adapter = FakeAdapter('model.onnx', [], closed=closed)
    ready(service, key, pixbuf)
    render_threads = list(service._render_worker._threads)
    service.stop()
    service.stop()
    assert closed == ['model.onnx']
    assert all(not thread.is_alive() for thread in render_threads)
    assert not service._render_jobs and not service._render_pending
    assert service.render(key, pixbuf, (128, 192), 0) is None


def test_two_renderings_respect_total_bytes_without_redraw_eviction_loop(service: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(upscale, 'MAX_RENDER_BYTES', 120000)
    first, first_pixbuf, unused = seed(service, 'p1')
    second, second_pixbuf, unused = seed(service, 'p2')
    service.set_wanted([first, second])
    ready(service, first, first_pixbuf, (100, 150))
    ready(service, second, second_pixbuf, (100, 150))
    assert set(service._rendered) == {first, second}
    total = sum(value[2].get_rowstride() * value[2].get_height()
                for value in service._rendered.values())
    assert total <= upscale.MAX_RENDER_BYTES
    # One page cannot evict its displayed neighbour and cause endless retries.
    with mock.patch.object(image_tools, 'fit_pil_to_rectangle') as fit:
        for unused in range(5):
            assert service.render(first, first_pixbuf, (300, 400), 0) is None
            assert service.render(second, second_pixbuf, (100, 150), 0) is not None
        fit.assert_not_called()


def test_real_draw_completion_refreshes_without_replaying_navigation(service: Any, monkeypatch: Any) -> None:
    from mcomix import main, constants
    from ._page_fakes import DrawHarness as _DrawHarness

    for name, value in {'neural upscaling always': True, 'smart bg': False,
                        'smart thumb bg': False, 'brightness': 1.0,
                        'contrast': 1.0, 'saturation': 1.0, 'sharpness': 1.0,
                        'auto contrast': False, 'invert color': False,
                        'rotation': 0, 'auto rotate from exif': False,
                        'auto rotate depending on size': constants.AUTOROTATE_NEVER,
                        'horizontal flip': False, 'vertical flip': False}.items():
        monkeypatch.setitem(prefs, name, value)

    class DrawHarness(_DrawHarness):
        _page_rendered = main.MainWindow._page_rendered
        _page_upscaled = main.MainWindow._page_upscaled

    raw = make_pixbuf(64, 96)
    window = DrawHarness([raw])
    window._pending_decode_redraw = None
    window.imagehandler.upscaler = service
    key = service.page_key('1')
    service.upscaled(key, Image.new('RGB', (128, 192), 'blue'))
    service.rendered_ready += window._page_rendered
    service.upscaled += window._page_upscaled
    window.draw_image(scroll_to=constants.SCROLL_TO_START)
    pump(lambda: key in service._rendered and not window._waiting_for_redraw)
    result = image_tools.pixbuf_to_pil(window.images[0].get_pixbuf())
    assert result.getpixel((0, 0)) == (0, 0, 255)
    # Initial navigation is applied once; neural completion preserves scrolling.
    destinations = [call.args[0] for call in window.scroll_to_predefined.call_args_list]
    assert destinations.count((constants.SCROLL_TO_START,) * 2) == 1

    window.scroll_to_predefined.reset_mock()
    window.imagehandler._current_image_index = 1
    window._pending_decode_redraw = (0, 2, constants.SCROLL_TO_END)
    service.rendered_ready(key)
    worker = threading.Thread(target=lambda: service.upscaled(key, Image.new('RGB', (128, 192), 'blue')))
    worker.start()
    worker.join(5)
    while GLib.MainContext.default().iteration(False):
        pass
    assert not window._waiting_for_redraw
    assert window._pending_decode_redraw == (0, 2, constants.SCROLL_TO_END)
    window.scroll_to_predefined.assert_not_called()
    # A formerly visible neighbour after changing back to single-page mode
    # cannot request another draw merely because _upscale_keys is stale.
    window._pending_decode_redraw = None
    window._upscale_keys = [service.page_key('1'), service.page_key('2')]
    window.displayed_double.return_value = False
    service.rendered_ready(key)
    assert not window._waiting_for_redraw


@pytest.mark.parametrize('change', ['invalidate', 'source_replaced', 'oversize'])
def test_active_resize_is_not_published_after_change(service: Any, change: str) -> None:
    key, pixbuf, image = seed(service)
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    original = image_tools.fit_pil_to_rectangle
    notifications = []
    service.rendered_ready += notifications.append

    def gated(*args: Any) -> Any:
        entered.set()
        assert release.wait(5)
        result = original(*args)
        done.set()
        return result

    with mock.patch.object(image_tools, 'fit_pil_to_rectangle', gated):
        try:
            service.render(key, pixbuf, (100, 150), 0)
            assert entered.wait(5)
            if change == 'invalidate':
                service.invalidate()
            elif change == 'source_replaced':
                service.upscaled(key, image.copy())
            else:  # A conventional fallback target supersedes the resize.
                assert service.render(key, pixbuf, (100000, 100000), 0) is None
        finally:
            release.set()
        assert done.wait(5)
        service._render_worker.stop()
        while GLib.MainContext.default().iteration(False):
            pass
    assert not notifications
    assert not service._render_pending and not service._rendered
    if change == 'oversize':
        assert service._cache[key] is image


def test_starved_gtk_publication_keeps_latest_payload_only(service: Any) -> None:
    key, pixbuf, image = seed(service)
    # The main context is not dispatched while each resize finishes. Old code
    # retained every completed pixbuf inside a separate GLib idle closure.
    original = image_tools.fit_pil_to_rectangle
    resized = []

    def resize(source: Any, target: Any, rotation: int) -> Any:
        result = original(source, target, rotation)
        resized.append(weakref.ref(result))
        return result

    def wait(condition: Callable[[], bool], message: str) -> None:
        deadline = time.monotonic() + 5
        while not condition():
            assert time.monotonic() < deadline, message
            time.sleep(0.002)

    with mock.patch.object(image_tools, 'fit_pil_to_rectangle', resize):
        for count, width in enumerate(range(100, 120), 1):
            assert service.render(key, pixbuf, (width, 150), 0) is None
            wait(lambda: len(resized) == count, 'resize did not run')
            if count > 1:
                # Storing the newer result releases the older one.
                wait(lambda: resized[-2]() is None, 'superseded rendering is still retained')
        result = ready(service, key, pixbuf, (119, 150))
    assert result.get_width() == 119 and result is resized[-1]()


def test_retargeting_ready_page_does_not_cancel_visible_neighbour(service: Any) -> None:
    first, first_pixbuf, first_image = seed(service, 'p1')
    second, second_pixbuf, second_image = seed(service, 'p2')
    service.set_wanted([first, second])
    ready(service, first, first_pixbuf, (100, 150))
    entered, release = threading.Event(), threading.Event()
    original = image_tools.fit_pil_to_rectangle

    def gated(source: Any, target: Any, rotation: int) -> Any:
        if source is second_image:
            entered.set()
            assert release.wait(5)
        return original(source, target, rotation)

    with mock.patch.object(image_tools, 'fit_pil_to_rectangle', gated):
        try:
            service.render(second, second_pixbuf, (100, 150), 0)
            assert entered.wait(5)
            pending = service._render_pending[second]
            service.render(first, first_pixbuf, (120, 180), 0)
            assert service._render_pending[second] is pending
        finally:
            release.set()
        ready(service, second, second_pixbuf, (100, 150))
        ready(service, first, first_pixbuf, (120, 180))
    assert set(service._rendered) == {first, second}
