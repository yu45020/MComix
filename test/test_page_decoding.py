"""Shared page decoding, terminal completion and nonblocking redraw requests."""

import inspect
from io import BytesIO
import sys
import threading
import time
import weakref
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock

import pytest
from PIL import Image

from ._page_fakes import DRAW_PREFS, DrawHarness, Gate, dispatch, dispatch_draw, make_image_handler, png, pixbuf, set_prefs, until
from gi.repository import GLib, Gtk
from mcomix import constants, image_handler, image_tools, main, webp_animation
from mcomix.preferences import prefs
from mcomix.worker_thread import WorkerThread


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[Any]:
    result = make_image_handler(monkeypatch, tmp_path,
                                ['page-%d.png' % index for index in range(8)], 1)
    result._available_images = set(range(8))
    result._current_image_index = 0
    result._thread = WorkerThread(result._cache_pixbuf, name='decode-test', sort_orders=True)
    yield result
    result._thread.stop()
    dispatch()


@pytest.fixture
def gate() -> Iterator[Gate]:
    result = Gate()
    yield result
    result.close()


def _decoder(monkeypatch: pytest.MonkeyPatch, gate: Gate, block: Any, result: Any) -> list:
    """Patch decoding to return result, blocking on sources matching block."""
    calls = []

    def decode(source: Any) -> Any:
        calls.append(source)
        if block is None or source == block:
            gate.block()
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(image_tools, 'load_pixbuf', decode)
    return calls


def _start_consumer(handler: Any, gate: Gate, index: int, results: list) -> threading.Thread:
    ready = next(iter(handler._inflight_decodes.values())).ready
    ready.wait = Mock(wraps=ready.wait)
    consumer = gate.start(lambda: results.append(handler._get_pixbuf(index)))
    until(lambda: ready.wait.call_count > 0)
    return consumer


@pytest.mark.parametrize('source_kind,result_kind', [
    ('bytes', 'image'), ('path', 'error')])
def test_prefetch_current_page_shares_read_and_decode_without_blocking_gtk(
        handler: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, gate: Gate,
        result_kind: str, source_kind: str) -> None:
    source = png()
    if source_kind == 'path':
        path = tmp_path / 'actual.png'
        path.write_bytes(source)
        source = str(path)
    handler._window.filehandler.get_image_source.side_effect = lambda path: source
    original = image_tools.load_pixbuf
    calls = []

    def decode(value: Any) -> Any:
        calls.append(value)
        gate.block()
        if result_kind == 'error':
            raise ValueError('injected decode failure')
        return original(value)

    monkeypatch.setattr(image_tools, 'load_pixbuf', decode)
    handler._current_image_index = 1
    handler._wanted_pixbufs = [1]
    handler._thread.append_order((0, 0, 1, handler._image_files[1]))
    assert gate.entered.wait(5)
    results = []
    consumer = _start_consumer(handler, gate, 1, results)
    notified = []
    handler.page_cached += notified.append
    beats = []

    def heartbeat() -> bool:
        beats.append(1)
        if len(beats) == 10:
            gate.release.set()
            return False
        return True

    timer = GLib.timeout_add(1, heartbeat)
    # This redraw demand must not join the active prefetch on the GTK thread.
    try:
        for _ in range(20):
            assert handler.get_pixbufs(1, block=False) is None
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5
        while len(beats) < 10 and time.monotonic() < deadline:
            context.iteration(False)
        assert len(beats) == 10
        consumer.join(5)
        assert not consumer.is_alive()
        until(lambda: handler.get_cached_pixbuf(2) is not None)
        # Cache publication precedes the worker enqueueing its GTK idle.
        deadline = time.monotonic() + 5
        while not notified and time.monotonic() < deadline:
            dispatch()
            time.sleep(0.001)
        assert len(calls) == 1
        assert handler._window.filehandler.get_image_source.call_count == 1
        assert results == handler.get_pixbufs(1, block=False)
        assert notified == [2]
        if result_kind != 'image':
            assert results == [image_tools.MISSING_IMAGE_ICON]
        else:
            assert bytes(results[0].get_pixels()[:3]) == bytes((255, 0, 0))
    finally:
        gate.close()
        if len(beats) < 10:
            GLib.source_remove(timer)


@pytest.mark.parametrize('cleanup', [False, True])
def test_reset_and_cleanup_release_shared_waiters_before_old_decode_finishes(
        handler: Any, monkeypatch: pytest.MonkeyPatch, gate: Gate, cleanup: bool) -> None:
    _decoder(monkeypatch, gate, None, image_tools.load_pixbuf(png()))
    handler.do_cacheing()
    assert gate.entered.wait(5)
    results = []
    consumer = _start_consumer(handler, gate, 0, results)
    operation = None
    if cleanup:
        operation = gate.start(handler.cleanup)
    else:
        handler.reset_page_list(['replacement.png'])
    consumer.join(1)
    assert not consumer.is_alive()
    assert results == [image_tools.MISSING_IMAGE_ICON]
    assert handler._raw_pixbufs == {}
    # Cleanup retains the existing release contract for an active native
    # read; only reset and the cancelled consumers are nonblocking.
    if cleanup:
        assert operation.is_alive()
    gate.close()
    if operation is not None:
        assert not operation.is_alive()
    dispatch()
    assert handler._raw_pixbufs == {}
    assert handler._inflight_decodes == {}


def test_unwanted_queued_worker_does_not_read_or_decode(
        handler: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    loader = Mock(side_effect=AssertionError('unwanted decode'))
    monkeypatch.setattr(image_tools, 'load_pixbuf', loader)
    handler._wanted_pixbufs = [7]
    handler._cache_pixbuf((0, 0, 0, handler._image_files[0]))
    loader.assert_not_called()
    handler._window.filehandler.get_image_source.assert_not_called()


def test_producer_base_exception_always_releases_shared_waiter(
        handler: Any, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    class Interrupted(BaseException):
        pass

    failures = []

    def produce() -> None:
        try:
            handler._get_pixbuf(0)
        except Interrupted:
            failures.append(True)

    _decoder(monkeypatch, gate, None, Interrupted())
    producer = gate.start(produce)
    assert gate.entered.wait(5)
    results = []
    consumer = _start_consumer(handler, gate, 0, results)
    gate.close()
    assert not producer.is_alive()
    assert not consumer.is_alive()
    assert failures == [True]
    assert results == [image_tools.MISSING_IMAGE_ICON]
    assert handler._inflight_decodes == {}


@pytest.mark.parametrize('limit', [0, -1])
def test_cold_display_request_runs_decode_only_on_worker(
        handler: Any, monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    monkeypatch.setitem(prefs, 'max pages to cache', limit)
    threads = []
    page = image_tools.load_pixbuf(png())

    def decode(source: str) -> Any:
        threads.append(threading.current_thread())
        return page

    monkeypatch.setattr(image_tools, 'load_pixbuf', decode)
    assert handler.get_pixbufs(1, block=False) is None
    until(lambda: handler.get_cached_pixbuf(1) is page)
    dispatch()
    assert handler.get_pixbufs(1, block=False) == [page]
    assert all(thread is not threading.main_thread() for thread in threads)


def test_display_geometry_does_not_decode_but_backward_jump_keeps_wide_page(
        handler: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    set_prefs(monkeypatch, {
        'default double page': True, 'double step in double page mode': True,
        'virtual double page for fitting images': constants.SHOW_DOUBLE_AS_ONE_WIDE})
    handler._thread.stop()
    handler._thread = Mock()
    handler._current_image_index = 5
    window = handler._window
    window.set_page = Mock(side_effect=lambda page, at_bottom=False: handler.set_page(page))
    window.next_book = Mock()
    window.previous_book = Mock()
    portrait = image_tools.load_pixbuf(png())
    wide = pixbuf((4, 2), 'blue')
    decode = Mock(side_effect=lambda path: wide if path == 'page-4.png' else portrait)
    monkeypatch.setattr(image_tools, 'load_pixbuf', decode)
    assert main.MainWindow.displayed_double(window)
    decode.assert_not_called()
    # From page 6, backward double-step must land on the wide page 5, rather
    # than treating its unknown geometry as portrait and skipping to page 4.
    main.MainWindow.flip_page(window, -1)
    assert handler.get_current_page() == 5
    assert decode.call_count == 2
    main.MainWindow.flip_page(window, 1)
    assert handler.get_current_page() == 6


def test_synchronous_owner_cannot_reinsert_page_evicted_during_decode(
        handler: Any, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    page = image_tools.load_pixbuf(png())
    calls = _decoder(monkeypatch, gate, 'page-0.png', page)
    handler._wanted_pixbufs = [0]
    results = []
    owner = gate.start(lambda: results.append(handler._get_pixbuf(0)))
    assert gate.entered.wait(5)
    consumer = _start_consumer(handler, gate, 0, results)
    ready = next(iter(handler._inflight_decodes.values())).ready
    # A queued worker for the same page joins the in-flight decode too.
    handler._thread.append_order((0, 0, 0, handler._image_files[0]))
    until(lambda: ready.wait.call_count == 2)
    handler.set_page(8)
    gate.close()
    assert not owner.is_alive()
    assert not consumer.is_alive()
    until(lambda: handler.get_cached_pixbuf(8) is page)
    dispatch()
    assert results == [page, page]
    assert handler._raw_pixbufs == {7: page}
    assert calls.count('page-0.png') == 1
    assert calls.count('page-7.png') == 1


def test_flip_away_and_back_during_completion_cannot_lose_redraw(
        handler: Any, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    completing = threading.Event()
    retire = threading.Event()
    page = image_tools.load_pixbuf(png())
    calls = _decoder(monkeypatch, gate, 'page-0.png', page)
    code = image_handler.ImageHandler._get_pixbuf
    source, first = inspect.getsourcelines(code)
    completion_line = first + next(index for index, line in enumerate(source)
                                   if line.strip() == 'completed = True')

    def trace(frame: Any, event: str, arg: Any) -> Any:
        if event == 'line' and frame.f_code is code.__code__ and frame.f_lineno == completion_line:
            completing.set()
            assert retire.wait(5)
        return trace

    def produce() -> None:
        sys.settrace(trace)
        handler._cache_pixbuf((0, 0, 0, handler._image_files[0]))

    handler._wanted_pixbufs = [0]
    notified = []
    handler.page_cached += notified.append
    owner = gate.start(produce)
    assert gate.entered.wait(5)
    try:
        handler.set_page(8)
        gate.release.set()
        assert completing.wait(5)
        handler.set_page(1)
        assert handler.get_pixbufs(1, block=False) is None
    finally:
        retire.set()
    owner.join(5)
    assert not owner.is_alive()
    until(lambda: handler.get_cached_pixbuf(1) is page)
    dispatch()
    assert handler.get_pixbufs(1, block=False) == [page]
    assert calls.count('page-0.png') == 1
    assert 1 in notified


@pytest.mark.parametrize('late_page', [1, 2])
def test_late_completion_never_replays_scroll_after_completed_wide_draw(
        monkeypatch: pytest.MonkeyPatch, late_page: int) -> None:
    set_prefs(monkeypatch, {'default double page': True, 'smart bg': False,
                            'smart thumb bg': False})
    window = DrawHarness([pixbuf((4, 2), 'blue')])
    window._update_page_information = Mock()
    window._pending_decode_redraw = None
    window.draw_image(scroll_to=constants.SCROLL_TO_END)
    dispatch()
    assert not window._waiting_for_redraw
    window.scroll_to_predefined.assert_called_once_with(
        (constants.SCROLL_TO_END,) * 2, constants.LAST_INDEX)
    # A cached backward jump has rendered END already; a late notification
    # must not replay the previous START destination or undo manual scrolling.
    main.MainWindow._page_cached(window, late_page)
    dispatch()
    assert not window._waiting_for_redraw
    assert window.scroll_to_predefined.call_count == 1
    window.scroll_to_predefined.reset_mock()
    main.MainWindow._page_cached(window, late_page)
    dispatch()
    window.scroll_to_predefined.assert_not_called()


@pytest.mark.parametrize('destination', [constants.SCROLL_TO_END, None])
def test_pending_display_completion_preserves_only_its_exact_destination(
        handler: Any, destination: Any) -> None:
    window = handler._window
    window._pending_decode_redraw = (handler._page_generation, 1, destination)
    window._upscale_keys = []
    window._prefetch_upscaling = Mock()
    main.MainWindow._page_cached(window, 1)
    window.draw_image.assert_called_once_with(scroll_to=destination)
    window._update_page_information.assert_called_once()
    window.draw_image.reset_mock()
    handler._page_generation += 1
    main.MainWindow._page_cached(window, 1)
    window.draw_image.assert_not_called()


@pytest.mark.parametrize('pending, expected', [
    (None, None),
    ('current', constants.SCROLL_TO_END),
    ('old generation', None),
    ('other page', None),
])
def test_page_available_takes_destination_only_from_matching_pending_redraw(
        handler: Any, pending: Any, expected: Any) -> None:
    window = handler._window
    window.displayed_double = lambda: False
    window.update_icon = Mock()
    generation = handler._page_generation
    window._pending_decode_redraw = {
        None: None,
        'current': (generation, 1, constants.SCROLL_TO_END),
        'old generation': (generation - 1, 1, constants.SCROLL_TO_END),
        'other page': (generation, 2, constants.SCROLL_TO_END),
    }[pending]
    main.MainWindow._page_available(window, 1)
    window.draw_image.assert_called_once_with(scroll_to=expected)
    window._update_page_information.assert_called_once()


def test_notice_shows_once_with_the_first_drawn_page(monkeypatch: pytest.MonkeyPatch) -> None:
    set_prefs(monkeypatch, {'smart bg': False, 'smart thumb bg': False})
    window = DrawHarness([pixbuf((4, 2), 'blue')])
    window._update_page_information = Mock()
    window.imagehandler._available_images = set()
    window.show_notice('No page')
    dispatch_draw(window)  # The page isn't extracted yet: nothing is drawn.
    window.osd.show.assert_not_called()
    window.imagehandler._available_images = {0}
    dispatch_draw(window)
    window.statusbar.set_message.assert_called_once_with('No page')
    window.osd.show.assert_called_once_with('No page')
    window.osd.clear.reset_mock()
    dispatch_draw(window)
    window.osd.clear.assert_called_once()
    window.osd.show.assert_called_once()
    # A book closed before its page was drawn takes its notice along.
    window.show_notice('Stale')
    window.set_title = Mock()
    main.MainWindow.clear(window)
    dispatch_draw(window)
    window.osd.show.assert_called_once()


def test_wide_current_decode_redraws_before_unavailable_partner_is_extracted(
        monkeypatch: pytest.MonkeyPatch) -> None:
    set_prefs(monkeypatch, {
        'default double page': True, 'max pages to cache': 0,
        'virtual double page for fitting images': constants.SHOW_DOUBLE_AS_ONE_WIDE,
        'smart bg': False, 'smart thumb bg': False})
    wide = pixbuf((4, 2), 'blue')
    window = DrawHarness([wide])
    window.displayed_double = lambda: main.MainWindow.displayed_double(window)
    window._update_page_information = Mock()
    handler = window.imagehandler
    handler._image_files = ['wide.png', 'pending.png']
    handler._raw_pixbufs = {}
    handler._available_images = {0}
    handler._wanted_pixbufs = [0, 1]
    handler._wanted_page_width = 2
    handler._page_dimensions = {}
    handler._inflight_decodes = {}
    handler._thread = Mock()
    handler.page_cached += lambda page: main.MainWindow._page_cached(window, page)
    assert window.displayed_double()
    assert not handler.page_is_available()
    window.draw_image(scroll_to=constants.SCROLL_TO_END)
    dispatch()
    assert not window.images[0].get_visible()
    assert window._pending_decode_redraw == (0, 1, constants.SCROLL_TO_END)
    # The current file was already extracted. Its decode discovers a wide
    # page while the prospective partner remains genuinely unavailable.
    handler._raw_pixbufs[0] = wide
    handler._page_decoded(0, 0, 'wide.png')
    dispatch()
    assert not handler.page_is_available(2)
    assert handler._wanted_pixbufs == [0]
    assert window.images[0].get_visible()
    assert not window.displayed_double()
    assert window._pending_decode_redraw is None
    window.scroll_to_predefined.assert_called_once_with(
        (constants.SCROLL_TO_END,) * 2, constants.LAST_INDEX)


@pytest.fixture
def webp(monkeypatch: pytest.MonkeyPatch) -> bytes:
    set_prefs(monkeypatch, dict(DRAW_PREFS, **{
        'brightness': 1.0, 'smart bg': False, 'smart thumb bg': False,
        'checkered bg for transparent images': False}))
    monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: 100.0))
    monkeypatch.setattr(webp_animation, 'LOOKAHEAD', 0.2)
    frames = [Image.new('RGBA', (3, 2), color) for color in
              ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 0))]
    return _encode_webp(frames, [40, 60, 80], loop=2, minimize_size=True)


def _encode_webp(frames: list, duration: Any, loop: int = 0, **options: Any) -> bytes:
    stream = BytesIO()
    frames[0].save(stream, format='WEBP', save_all=True, append_images=frames[1:],
                   duration=duration, loop=loop, lossless=True, **options)
    return stream.getvalue()


def _webp_spec(poster: Any, slot: int = 0, size: tuple = (6, 4),
               rotation: int = 0) -> tuple:
    return (slot, (0, slot + 1), poster,
            (size, rotation, False, False, (1.0, 1.0, 1.0, 1.0, False, False),
             image_tools.SCALING_LANCZOS, False))


def _webp_pixbuf(frame: Any) -> Any:
    if isinstance(frame, Image.Image):
        if frame.info.get('webp opaque alpha'):
            frame = frame.convert('RGBA')
        return image_tools.pil_to_pixbuf(frame)
    return frame


@pytest.mark.parametrize('alpha', [0, 254, 255])
def test_webp_capture_checks_each_canvas_and_owns_opaque_pixels(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, alpha: int) -> None:
    page = webp_animation._Page(0, image_tools.load_pixbuf(webp))
    page.pillow_frames = True
    source = Image.new('RGBA', (16, 12), (90, 50, 30, alpha))
    export = Mock(wraps=image_tools.pil_to_pixbuf)
    monkeypatch.setattr(image_tools, 'pil_to_pixbuf', export)
    try:
        captured = page.capture(source)
        if alpha == 255:
            assert isinstance(captured, Image.Image) and captured.mode == 'RGB'
            export.assert_not_called()
            source.putpixel((0, 0), (0, 0, 0, 0))
            assert captured.getpixel((0, 0)) == (90, 50, 30)
            # Opacity belongs to each composed frame, not to the animation.
            transparent = page.capture(source)
            assert transparent.get_has_alpha() and transparent.get_pixels()[3] == 0
            source.close()
            assert captured.getpixel((0, 0)) == (90, 50, 30)
        else:
            assert not isinstance(captured, Image.Image)
            assert captured.get_has_alpha() and captured.get_pixels()[3] == alpha
            export.assert_called_once_with(source)
    finally:
        source.close()
        page.close()


@pytest.mark.parametrize('rotation', [0, 90])
@pytest.mark.parametrize('alpha_mode', [False, True])
@pytest.mark.parametrize('quality', [2, image_tools.SCALING_LANCZOS])
def test_webp_opaque_render_exports_only_resized_pixels(
        webp: bytes, monkeypatch: pytest.MonkeyPatch,
        rotation: int, alpha_mode: bool, quality: int) -> None:
    page = webp_animation._Page(0, image_tools.load_pixbuf(webp))
    page.pillow_frames = True
    source = Image.new('RGBA' if alpha_mode else 'RGB', (16, 12), 'red')
    source.paste('green', (8, 0, 16, 12))
    captured = page.capture(source)
    size = (6, 8) if rotation else (8, 6)
    resampling = Image.Resampling.BILINEAR if alpha_mode or quality == 2 else Image.Resampling.LANCZOS
    expected = image_tools.rotate_pixbuf(
        image_tools.pil_to_pixbuf(source.convert('RGB').resize(size, resampling)), rotation)
    export = Mock(wraps=image_tools.pil_to_pixbuf)
    monkeypatch.setattr(image_tools, 'pil_to_pixbuf', export)
    try:
        target = _webp_spec(page.poster, size=(8, 6), rotation=rotation)[3]
        target = (*target[:5], quality, target[6])
        output = page.render(captured, target, False)
        assert output.get_pixels() == expected.get_pixels()
        assert export.call_count == 1 and export.call_args.args[0].size == size
        pending, originals = webp_animation._render_memory(target, captured)
        assert pending >= 3 * 8 * 6 * 4 and originals == 0
    finally:
        source.close()
        page.close()


@pytest.mark.parametrize('shared', [False, True])
@pytest.mark.parametrize('optimized', [False, True])
def test_webp_opaque_scratch_fits_shared_budget_or_uses_pixbufs(
        webp: bytes, monkeypatch: pytest.MonkeyPatch,
        shared: bool, optimized: bool) -> None:
    poster = image_tools.load_pixbuf(webp)
    count = 2 if shared else 1
    outputs = 4 if optimized else 2
    share = 5 * 24 + outputs * 96
    monkeypatch.setitem(prefs, 'animation cache limit', count * share / (1024 * 1024))
    playback = webp_animation.WebPPlayback([Gtk.Image() for _ in range(count)])
    try:
        playback.configure([_webp_spec(poster, slot=slot) for slot in range(count)])
        for page in playback.pages.values():
            until(lambda: len(page.frames) == 2)
            assert page.pillow_frames is optimized
            assert isinstance(page.frames[1][2], Image.Image) is optimized
            assert (page.capacity + 3) * 24 + outputs * 96 <= share
        assert not playback.limited
    finally:
        playback.clear()


@pytest.mark.parametrize('rotation', [0, 90])
@pytest.mark.parametrize('optimized', [False, True])
def test_webp_deep_downscale_budget_includes_pillow_intermediate(
        webp: bytes, monkeypatch: pytest.MonkeyPatch,
        rotation: int, optimized: bool) -> None:
    frames = [Image.new('RGBA', (16, 12), color) for color in
              ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 0, 0))]
    poster = image_tools.load_pixbuf(_encode_webp(frames, 100))
    pending = (192 if rotation else 240) if optimized else 3 * 4 * 3 * 4
    share = 5 * 16 * 12 * 4 + 4 * 3 * 4 + pending
    monkeypatch.setitem(prefs, 'animation cache limit', share / (1024 * 1024))
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster, size=(4, 3), rotation=rotation)])
        page = playback.pages[0]
        until(lambda: len(page.frames) == 2)
        assert page.pillow_frames is optimized
        assert page.capacity == 2
        assert not playback.limited
    finally:
        playback.clear()


@pytest.mark.parametrize('alpha_mode', [False, True])
@pytest.mark.parametrize('contrast', [1.0, 1.5])
def test_webp_opaque_snapshot_preserves_alpha_origin_autocontrast_policy(
        webp: bytes, alpha_mode: bool, contrast: float) -> None:
    page = webp_animation._Page(0, image_tools.load_pixbuf(webp))
    page.pillow_frames = True
    source = Image.new('RGBA' if alpha_mode else 'RGB', (16, 12), (50, 60, 70))
    source.paste((100, 110, 120, 255) if alpha_mode else (100, 110, 120), (8, 0, 16, 12))
    captured = page.capture(source)
    target = list(_webp_spec(page.poster, size=source.size)[3])
    target[4] = (1.0, contrast, 1.0, 1.0, True, False)
    try:
        actual = page.render(captured, tuple(target), False)
        reference = page.render(image_tools.pil_to_pixbuf(source), tuple(target), False)
        actual_rgb = image_tools.pixbuf_to_pil(actual).convert('RGB').tobytes()
        reference_rgb = image_tools.pixbuf_to_pil(reference).convert('RGB').tobytes()
        # GDK's opaque alpha blend has a one-level rounding difference.
        assert max(abs(left - right) for left, right in zip(actual_rgb, reference_rgb)) <= 2
    finally:
        source.close()
        page.close()


@pytest.mark.parametrize('headroom', [False, True])
def test_webp_live_downscale_rebudgets_pillow_intermediate(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, headroom: bool) -> None:
    frames = [Image.new('RGBA', (16, 12), color) for color in
              ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 0, 0))]
    poster = image_tools.load_pixbuf(_encode_webp(frames, 100))
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster, size=(8, 6))])
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        playback.tick()
        until(lambda: page.ready is not None and page.ready[0][0] == 1)
        assert page.pillow_frames
        # Keep the older displayed output reserved alongside the new scratch.
        pending = 240 if headroom else 144
        budget = 5 * 16 * 12 * 4 + 8 * 6 * 4 + pending
        monkeypatch.setitem(prefs, 'animation cache limit', budget / (1024 * 1024))
        playback.configure([_webp_spec(poster, size=(4, 3))])
        if headroom:
            assert playback.pages[0] is page and page.capacity == 2
            assert page.started == 100.0 and not playback.limited
        else:
            assert playback.pages == {} and playback.limited
    finally:
        playback.clear()


@pytest.mark.parametrize('archive', [False, True])
def test_webp_poster_path_bytes_transparency_and_disabled_animation(
        webp: bytes, tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
        archive: bool) -> None:
    path = tmp_path / 'page.webp'
    path.write_bytes(webp)
    monkeypatch.setitem(prefs, 'animation mode', constants.ANIMATION_DISABLED)
    poster = image_tools.load_pixbuf(webp if archive else str(path))
    assert (poster.get_width(), poster.get_height()) == (3, 2)
    assert bytes(poster.get_pixels()[:4]) == bytes((255, 0, 0, 255))
    assert poster.webp_animation.frames == 3
    assert poster.webp_animation.loops == 2
    assert poster.webp_animation.duration == 0.04
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    playback.configure([_webp_spec(poster)])
    assert playback.pages == {} and playback.timer is None
    thumb = image_tools.load_pixbuf_size(webp, 3, 2)
    assert not hasattr(thumb, 'webp_animation')
    monkeypatch.setitem(prefs, 'animation mode', constants.ANIMATION_NORMAL)
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: len(page.frames) == 5)
        # Pillow supplies fully composed canvases, including transparent pixels.
        assert bytes(_webp_pixbuf(page.frames[1][2]).get_pixels()[:4]) == bytes((0, 255, 0, 255))
        assert bytes(_webp_pixbuf(page.frames[2][2]).get_pixels()[3:4]) == b'\x00'
    finally:
        playback.clear()


@pytest.mark.parametrize('loops', [0, 1, 2])
def test_webp_late_playback_preserves_durations_and_respects_loops(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, loops: int) -> None:
    poster = image_tools.load_pixbuf(webp)
    metadata = poster.webp_animation
    poster.webp_animation = image_tools.WebPAnimation(
        metadata.source, metadata.frames, loops, metadata.duration)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: len(page.frames) == (3 if loops == 1 else 5))
        until(lambda: page.ready is not None and page.ready[0][0] == 0)
        playback.tick()
        now = [100.0]
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: now[0]))
        count = 3 * (loops or 3)
        for serial in range(1, count):
            until(lambda: page.ready is not None and page.ready[0][0] == serial)
            prepared = page.ready[1]
            duration = [0.04, 0.06, 0.08][(serial - 1) % 3]
            # Even after a long stall, each frame receives its encoded hold.
            due = now[0] + duration
            now[0] = due - 0.001
            playback.tick()
            assert page.presented == serial - 1
            now[0] = due + (1.0 if serial == 1 else 0.001)
            playback.tick()
            assert page.presented == page.frames[0][0] == serial
            assert playback.images[0].get_pixbuf() is page.display is prepared
        if loops:
            until(lambda: page.finished)
            now[0] += 10
            playback.tick()
            assert page.presented == count - 1
        assert page.started == 100.0
    finally:
        playback.clear()


@pytest.mark.parametrize('durations, expected', [
    ([10] * 30, 21),
    ([400, 10, 20, 30, 40, 100, 150], 6),
    ([10, 300, 10, 10], 2),
])
def test_webp_lookahead_uses_forward_encoded_durations(
        webp: bytes, monkeypatch: pytest.MonkeyPatch,
        durations: list, expected: int) -> None:
    frames = [Image.new('RGBA', (3, 2), (index, 0, 0, 128))
              for index in range(len(durations))]
    poster = image_tools.load_pixbuf(_encode_webp(frames, durations))
    opened = image_tools._open_pil
    decoders = []

    def decoder(source: Any) -> Any:
        result = opened(source)
        result.seek = Mock(wraps=result.seek)
        decoders.append(result)
        return result

    monkeypatch.setattr(image_tools, '_open_pil', decoder)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: len(page.frames) >= expected)
        with page.condition:
            assert len(page.frames) == expected < page.capacity
            assert [frame[3] for frame in page.frames] == pytest.approx(
                [duration / 1000 for duration in durations[:expected]])
            assert sum(frame[3] for frame in page.frames[1:]) == pytest.approx(
                sum(durations[1:expected]) / 1000)
            assert len(decoders) == 1
            assert [call.args[0] for call in decoders[0].seek.call_args_list] == list(range(1, expected))
    finally:
        playback.clear()


def test_webp_prepares_one_frame_ahead_and_waits_for_its_deadline(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(image_tools.load_pixbuf(webp))])
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        playback.tick()
        displayed = page.display
        until(lambda: page.ready is not None and page.ready[0][0] == 1)
        prepared = page.ready[1]
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: 100.039))
        playback.tick()
        assert page.display is displayed and page.ready[1] is prepared
        assert page.rendering_bytes == 0 and page.rendering_frame is None
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: 100.041))
        playback.tick()
        assert page.display is prepared
        assert page.started == 100.0
    finally:
        playback.clear()


def test_webp_decode_lookahead_continues_while_rendering_is_blocked(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    render = webp_animation._Page.render
    owners = []

    def blocked(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        owners.append(threading.get_ident())
        gate.block()
        return render(page, frame, target, fast)

    monkeypatch.setattr(webp_animation._Page, 'render', blocked)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(image_tools.load_pixbuf(webp))])
        page = playback.pages[0]
        assert gate.entered.wait(5)
        until(lambda: len(page.frames) == 5 and not page.decoding)
        assert page.ready is None and page.rendering_frame[0] == 0
        assert owners == [page.renderer._threads[0].ident]
        assert owners[0] != page.worker._threads[0].ident
    finally:
        gate.release.set()
        playback.clear()


def test_webp_renderer_continues_while_decoder_is_blocked(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    poster = image_tools.load_pixbuf(webp)
    decoder = image_tools._open_pil(webp)
    load = decoder.load

    def blocked() -> Any:
        if decoder.tell() == 1 and not gate.release.is_set():
            gate.block()
        return load()

    decoder.load = blocked
    monkeypatch.setattr(image_tools, '_open_pil', lambda source: decoder)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        assert gate.entered.wait(5)
        until(lambda: page.ready is not None)
        playback.tick()
        assert page.presented == 0 and page.display is not None
        assert page.decoding and len(page.frames) == 1
    finally:
        gate.release.set()
        playback.clear()


@pytest.mark.parametrize('count', [1, 2])
def test_webp_rebudget_counts_capture_and_pinned_render_together(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate, count: int) -> None:
    capture_gate = Gate()
    render = webp_animation._Page.render
    capture = webp_animation._Page.capture
    captures = []

    def blocked_render(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        if isinstance(frame, Image.Image) and frame.info.get('timestamp') == 40 and not gate.release.is_set():
            gate.block()
        return render(page, frame, target, fast)

    def blocked_capture(page: Any, decoder: Any) -> Any:
        if page.identity == (0, 1):
            captures.append(decoder.tell())
            if len(captures) == 3:
                capture_gate.block()
        return capture(page, decoder)

    monkeypatch.setattr(webp_animation._Page, 'render', blocked_render)
    monkeypatch.setattr(webp_animation._Page, 'capture', blocked_capture)
    poster = image_tools.load_pixbuf(webp)
    playback = webp_animation.WebPPlayback([Gtk.Image() for _ in range(count)])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        assert capture_gate.entered.wait(5)
        until(lambda: page.ready is not None)
        playback.tick()
        assert gate.entered.wait(5)
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: 100.181))
        playback.tick()
        assert page.frames[0][0] == 0 and page.rendering_frame[0] == 1
        share = 5 * 24 + 4 * 96
        monkeypatch.setitem(prefs, 'animation cache limit', count * share / (1024 * 1024))
        playback.configure([_webp_spec(poster, slot=slot) for slot in range(count)])
        with page.condition:
            assert page.capacity == page.source_count() == 2
            assert page.decoding and page.frames == []
        capture_gate.release.set()
        until(lambda: not page.decoding)
        with page.condition:
            assert page.source_count() == 2 and page.frames[0][0] == 1
            assert captures[:3] == [1, 2, 0]
        gate.release.set()
        until(lambda: page.ready is not None)
        playback.tick()
        assert page.presented == 1
        until(lambda: page.ready is not None and page.ready[0][0] == 2)
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: 100.242))
        playback.tick()
        assert page.presented == 2 and not playback.limited
    finally:
        gate.release.set()
        capture_gate.release.set()
        playback.clear()


def test_webp_close_joins_both_active_workers_and_fences_publication(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    capture_gate = Gate()
    render = webp_animation._Page.render
    capture = webp_animation._Page.capture

    def blocked_render(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        gate.block()
        return render(page, frame, target, fast)

    def blocked_capture(page: Any, decoder: Any) -> Any:
        capture_gate.block()
        return capture(page, decoder)

    monkeypatch.setattr(webp_animation._Page, 'render', blocked_render)
    monkeypatch.setattr(webp_animation._Page, 'capture', blocked_capture)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    closer = None
    try:
        playback.configure([_webp_spec(image_tools.load_pixbuf(webp))])
        page = playback.pages[0]
        assert gate.entered.wait(5) and capture_gate.entered.wait(5)
        closer = gate.start(playback.clear)
        until(lambda: page.stopped)
        assert closer.is_alive()
        capture_gate.release.set()
        gate.release.set()
        closer.join(5)
        assert not closer.is_alive()
        assert not page.worker._threads and not page.renderer._threads
        assert not page.decoding and page.rendering_frame is None
        assert page.frames == [] and page.ready is None and page.display is None
    finally:
        gate.release.set()
        capture_gate.release.set()
        if closer is not None:
            closer.join(5)
        playback.clear()


def test_webp_decode_failure_waits_for_active_render_before_poster_fallback(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    poster = image_tools.load_pixbuf(webp)
    render = webp_animation._Page.render
    decoder = image_tools._open_pil(webp)
    entered = []

    def blocked(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        entered.append(page.failed)
        if len(entered) == 1:
            gate.block()
        return render(page, frame, target, fast)

    def broken(index: int) -> Any:
        assert gate.entered.wait(5)
        raise ValueError('broken frame')

    decoder.seek = broken
    monkeypatch.setattr(image_tools, '_open_pil', lambda source: decoder)
    monkeypatch.setattr(webp_animation._Page, 'render', blocked)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: page.failed)
        assert entered == [False] and page.ready is None
        gate.release.set()
        until(lambda: page.ready is not None)
        assert entered == [False, True]
        playback.tick()
        expected = image_tools.fit_pixbuf_to_rectangle(poster, (6, 4), 0)
        assert playback.images[0].get_pixbuf().get_pixels() == expected.get_pixels()
    finally:
        gate.release.set()
        playback.clear()


def test_webp_render_failure_stops_decoder_and_rejects_inflight_capture(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    render = webp_animation._Page.render
    capture = webp_animation._Page.capture
    entered = []

    def broken(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        entered.append(page.failed)
        if len(entered) == 1:
            assert gate.entered.wait(5)
            raise ValueError('broken render')
        return render(page, frame, target, fast)

    def blocked(page: Any, decoder: Any) -> Any:
        gate.block()
        return capture(page, decoder)

    monkeypatch.setattr(webp_animation._Page, 'render', broken)
    monkeypatch.setattr(webp_animation._Page, 'capture', blocked)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(image_tools.load_pixbuf(webp))])
        page = playback.pages[0]
        until(lambda: page.failed and page.ready is not None)
        assert entered == [False, True] and page.decoding
        gate.release.set()
        until(lambda: not page.decoding)
        assert page.frames == []
        playback.tick()
        assert playback.images[0].get_pixbuf() is not None
    finally:
        gate.release.set()
        playback.clear()


def test_webp_second_worker_failure_preserves_prepared_poster(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    render = webp_animation._Page.render

    def broken_render(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        if not page.failed:
            assert gate.entered.wait(5)
            raise ValueError('render failed first')
        return render(page, frame, target, fast)

    def broken_capture(page: Any, decoder: Any) -> Any:
        gate.block()
        raise ValueError('decode failed second')

    monkeypatch.setattr(webp_animation._Page, 'render', broken_render)
    monkeypatch.setattr(webp_animation._Page, 'capture', broken_capture)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(image_tools.load_pixbuf(webp))])
        page = playback.pages[0]
        until(lambda: page.failed and page.ready is not None)
        prepared = page.ready
        gate.release.set()
        until(lambda: not page.decoding)
        assert page.ready is prepared
        playback.tick()
        assert playback.images[0].get_pixbuf() is prepared[1]
    finally:
        gate.release.set()
        playback.clear()


@pytest.mark.parametrize('count', [1, 2])
def test_webp_budget_includes_raw_capture_bytes_during_render(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate, count: int) -> None:
    copy_gate = Gate()
    poster = image_tools.load_pixbuf(webp)
    render = webp_animation._Page.render
    copy = GLib.Bytes.new

    def blocked_render(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        gate.block()
        return render(page, frame, target, fast)

    def blocked_copy(data: bytes) -> Any:
        if threading.current_thread().name.endswith('-webp-decode'):
            assert len(data) == 24  # Full-size RGBA raw bytes coexist with GLib's copy.
            copy_gate.block()
        return copy(data)

    monkeypatch.setattr(webp_animation._Page, 'render', blocked_render)
    monkeypatch.setattr(GLib.Bytes, 'new', blocked_copy)
    share = 5 * 24 + 2 * 96
    monkeypatch.setitem(prefs, 'animation cache limit', count * share / (1024 * 1024))
    playback = webp_animation.WebPPlayback([Gtk.Image() for _ in range(count)])
    try:
        playback.configure([_webp_spec(poster, slot=slot) for slot in range(count)])
        assert gate.entered.wait(5) and copy_gate.entered.wait(5)
        until(lambda: all(page.decoding for page in playback.pages.values()))
        for page in playback.pages.values():
            with page.condition:
                assert page.capacity == page.source_count() == 2
                assert not page.pillow_frames
                assert (page.capacity + 3) * 24 + 2 * 96 == share
        copy_gate.release.set()
        until(lambda: all(not page.decoding and len(page.frames) == 2
                          for page in playback.pages.values()))
        # Below the minimum source and export reserves, retain poster fallback.
        monkeypatch.setitem(prefs, 'animation cache limit', count * (share - 1) / (1024 * 1024))
        gate.release.set()
        playback.configure([_webp_spec(poster, slot=slot) for slot in range(count)])
        assert playback.limited and playback.pages == {}
    finally:
        gate.release.set()
        copy_gate.release.set()
        playback.clear()


def test_webp_resize_releases_discarded_ready_before_new_workers_start(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    class Output:
        def __init__(self, size: tuple) -> None:
            self.size = size

        def get_rowstride(self) -> int:
            return self.size[0] * 4

        def get_height(self) -> int:
            return self.size[1]

    monkeypatch.setattr(webp_animation._Page, 'render',
                        lambda page, frame, target, fast: Output(target[0]))
    poster = image_tools.load_pixbuf(webp)
    playback = webp_animation.WebPPlayback([Gtk.Image(), Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        discarded = weakref.ref(page.ready[1])
        start = webp_animation.WorkerThread.append_order
        starts = []

        def checked_start(worker: Any, order: Any) -> None:
            assert discarded() is None
            starts.append(worker)
            start(worker, order)

        monkeypatch.setattr(webp_animation.WorkerThread, 'append_order', checked_start)
        playback.configure([_webp_spec(poster, size=(12, 8)), _webp_spec(poster, slot=1)])
        assert starts == [playback.pages[1].renderer, playback.pages[1].worker]
    finally:
        playback.clear()


def test_webp_slow_preparation_presents_completed_work_without_starving(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    render = webp_animation._Page.render

    def blocked(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        if bytes(_webp_pixbuf(frame).get_pixels()[:4]) == bytes((0, 255, 0, 255)) and not gate.release.is_set():
            gate.block()
        return render(page, frame, target, fast)

    monkeypatch.setattr(webp_animation._Page, 'render', blocked)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(image_tools.load_pixbuf(webp))])
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        playback.tick()
        displayed = page.display
        assert gate.entered.wait(5)
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: 100.101))
        playback.tick()
        assert page.display is displayed
        assert page.rendering_frame[0] == 1
        gate.release.set()
        until(lambda: page.ready is not None and page.ready[0][0] == 1)
        playback.tick()
        assert page.presented == 1 and page.display is not displayed
        until(lambda: page.ready is not None and page.ready[0][0] == 2)
        playback.tick()
        assert page.presented == 1
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: 100.162))
        playback.tick()
        assert page.presented == 2
        assert page.started == 100.0
    finally:
        gate.release.set()
        playback.clear()


def test_webp_rebudget_redecodes_unseen_frames_without_skipping(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(webp_animation, 'LOOKAHEAD', 10)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    poster = image_tools.load_pixbuf(webp)
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: page.finished and not page.decoding)
        assert len(page.frames) == 6
        # Trim to current + next; discarded frames must be decoded again,
        # including after the decoder has already reached the final loop.
        share = 5 * 24 + 4 * 96
        monkeypatch.setitem(prefs, 'animation cache limit', share / (1024 * 1024))
        playback.configure([_webp_spec(poster)])
        assert page.capacity == len(page.frames) == 2
        for serial in range(6):
            until(lambda: page.ready is not None and page.ready[0][0] == serial)
            if serial:
                clock[0] += [0.04, 0.06, 0.08][(serial - 1) % 3] + 0.001
            playback.tick()
            assert page.presented == serial and not playback.limited
            with page.condition:
                assert page.source_count() <= page.capacity
        until(lambda: page.finished)
        assert page.started == 100.0
    finally:
        playback.clear()


def test_webp_decoder_slower_than_source_preserves_frame_order(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    poster = image_tools.load_pixbuf(webp)
    clock = [100.0]
    monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    opened = image_tools._open_pil

    def slow_decoder(source: Any) -> Any:
        decoder = opened(source)
        load = decoder.load
        loaded = [-1]

        def slow_load() -> Any:
            if decoder.tell() != loaded[0]:
                if decoder.tell() == 1 and not gate.release.is_set():
                    gate.block()
                loaded[0] = decoder.tell()
                clock[0] += 0.12
            return load()

        decoder.load = slow_load
        return decoder

    monkeypatch.setattr(image_tools, '_open_pil', slow_decoder)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        assert gate.entered.wait(5)
        playback.tick()
        assert page.presented == 0
        gate.release.set()
        until(lambda: page.ready is not None and page.ready[0][0] == 1)
        clock[0] = max(clock[0], page.next_due + 0.001)
        playback.tick()
        assert page.presented == 1 and page.started == 100.0
        until(lambda: page.ready is not None and page.ready[0][0] == 2)
        clock[0] = max(clock[0], page.next_due + 0.001)
        playback.tick()
        assert page.presented == 2
    finally:
        gate.release.set()
        playback.clear()


def test_webp_resize_invalidates_prepared_future_frame(
        webp: bytes) -> None:
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    poster = image_tools.load_pixbuf(webp)
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        playback.tick()
        until(lambda: page.ready is not None and page.ready[0][0] == 1)
        old = page.ready[1]
        playback.configure([_webp_spec(poster, size=(12, 8))])
        until(lambda: page.ready is not None and page.ready[0] == (0, page.revision))
        playback.tick()
        until(lambda: page.ready is not None and page.ready[0] == (1, page.revision))
        assert page.ready[1] is not old
        assert (page.ready[1].get_width(), page.ready[1].get_height()) == (12, 8)
    finally:
        playback.clear()


def test_webp_resize_during_long_hold_refreshes_without_restarting_timing(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    poster = image_tools.load_pixbuf(webp)
    metadata = poster.webp_animation
    poster.webp_animation = image_tools.WebPAnimation(
        metadata.source, metadata.frames, metadata.loops, 1.0)
    render = webp_animation._Page.render

    def blocked(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        if target[0] == (12, 8) and not gate.release.is_set():
            gate.block()
        return render(page, frame, target, fast)

    monkeypatch.setattr(webp_animation._Page, 'render', blocked)
    timeout = Mock(wraps=GLib.timeout_add)
    monkeypatch.setattr(GLib, 'timeout_add', timeout)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        playback.tick()
        assert page.next_due == 101.0
        assert timeout.call_args.args[0] == 1000
        playback.configure([_webp_spec(poster, size=(12, 8))])
        assert timeout.call_args.args[0] == 10
        assert gate.entered.wait(5)
        playback.tick()
        assert timeout.call_args.args[0] == 10
        gate.release.set()
        until(lambda: page.ready is not None and page.ready[0] == (0, page.revision))
        playback.tick()
        assert page.display.get_width() == 12 and page.presented == 0
        assert page.next_due == 101.0
    finally:
        gate.release.set()
        playback.clear()


def test_webp_memory_limit_reduces_lookahead_and_recovers_in_place(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    poster = image_tools.load_pixbuf(webp)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    # Three frame slots, poster/decoder/export reserves, and two outputs.
    budget = 6 * 24 + 2 * 96
    monkeypatch.setitem(prefs, 'animation cache limit', budget / (1024 * 1024))
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: len(page.frames) == 3)
        assert page.capacity == 3
        assert sum(frame[3] for frame in page.frames[1:]) == pytest.approx(0.14)
        assert not playback.limited
        started = page.started
        monkeypatch.setitem(prefs, 'animation cache limit', 128)
        playback.configure([_webp_spec(poster)])
        until(lambda: len(page.frames) == 5)
        assert playback.pages[0] is page and page.started == started
    finally:
        playback.clear()


def test_webp_two_pages_share_budget_and_single_page_takes_released_share(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    poster = image_tools.load_pixbuf(webp)
    playback = webp_animation.WebPPlayback([Gtk.Image(), Gtk.Image()])
    # Five RGBA originals (poster, active, queued, decoder and export scratch) and two
    # target buffers per page: only one upcoming frame fits in this budget.
    monkeypatch.setitem(prefs, 'animation cache limit',
                        2 * (5 * 24 + 2 * 96) / (1024 * 1024))
    try:
        playback.configure([_webp_spec(poster, slot=slot) for slot in range(2)])
        assert len(playback.pages) == 2
        for page in playback.pages.values():
            until(lambda: len(page.frames) == page.capacity)
            assert page.capacity == 2
        assert sum(page.capacity * 24 + 3 * 24 + 2 * 96
                   for page in playback.pages.values()) == 2 * (5 * 24 + 2 * 96)
        # A single visible page can use the released share, up to the time target.
        page = playback.pages[0]
        playback.configure([_webp_spec(poster)])
        until(lambda: len(page.frames) == 5)
        assert playback.pages[0] is page and not playback.limited
    finally:
        playback.clear()


def test_webp_new_worker_waits_for_retained_page_to_release_shared_budget(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    frames = [Image.new('RGBA', (3, 2), (index, 0, 0, 128))
              for index in range(30)]
    poster = image_tools.load_pixbuf(_encode_webp(frames, 10))
    playback = webp_animation.WebPPlayback([Gtk.Image(), Gtk.Image()])
    budget = 1008  # Duration-limited solo queue; two original slots per shared page.
    monkeypatch.setitem(prefs, 'animation cache limit', budget / (1024 * 1024))
    try:
        playback.configure([_webp_spec(poster, slot=1)])
        retained = playback.pages[1]
        until(lambda: len(retained.frames) == 21)
        append_order = webp_animation.WorkerThread.append_order
        starts = []

        def start(worker: Any, order: Any) -> None:
            starts.append(worker)
            reserved = 0
            for page in playback.pages.values():
                with page.condition:
                    assert len(page.frames) <= page.capacity
                    reserved += page.capacity * 24 + 3 * 24 + 4 * 96
            assert retained.capacity == 2
            assert reserved <= budget
            append_order(worker, order)

        monkeypatch.setattr(webp_animation.WorkerThread, 'append_order', start)
        # The newly visible earlier slot must not start against the old queue.
        playback.configure([_webp_spec(poster, slot=slot) for slot in range(2)])
        assert starts == [playback.pages[0].renderer, playback.pages[0].worker]
        assert playback.pages[1] is retained
        for page in playback.pages.values():
            until(lambda: len(page.frames) == 2)
    finally:
        playback.clear()


def test_webp_enlarged_playback_budget_notice_and_live_recovery(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    poster = image_tools.load_pixbuf(webp)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    notices = []
    playback.notice_changed += notices.append
    spec = _webp_spec(poster, size=(2200, 1467))
    try:
        playback.configure([spec])
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        playback.tick()
        assert page.display.get_width() == 2200
        assert notices == []
        started = page.started
        monkeypatch.setitem(prefs, 'animation cache limit', 64)
        playback.configure([spec])
        assert playback.pages[0] is page and page.started == started
        monkeypatch.setitem(prefs, 'animation cache limit', 8)
        playback.configure([spec])
        assert playback.pages == {} and notices == [True]
        playback.configure([spec])
        assert notices == [True]
        monkeypatch.setitem(prefs, 'animation cache limit', 32)
        playback.configure([spec])
        assert 0 in playback.pages and notices == [True, False]
        monkeypatch.setitem(prefs, 'animation cache limit', 8)
        playback.configure([spec])
        playback.clear()
        assert notices == [True, False, True, False]
    finally:
        playback.clear()


@pytest.mark.parametrize('operation', ['rotation', 'enhancement'])
def test_webp_budget_reserves_transform_intermediates(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    poster = image_tools.load_pixbuf(webp)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    # Fits four originals, three ownership/export reserves, and two plain outputs.
    monkeypatch.setitem(prefs, 'animation cache limit', (7 * 24 + 2 * 96) / (1024 * 1024))
    spec = _webp_spec(poster)
    try:
        playback.configure([spec])
        assert playback.pages[0].capacity == 4
        target = list(spec[3])
        if operation == 'rotation':
            target[1] = 180
        else:
            target[4] = (1.2, 1.0, 1.0, 1.0, False, False)
        playback.configure([(*spec[:3], tuple(target))])
        assert not playback.pages and playback.limited
    finally:
        playback.clear()


@pytest.mark.parametrize('rotation', [0, 90])
def test_webp_enlargement_setting_preserves_static_pages_and_shrinking(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, rotation: int) -> None:
    set_prefs(monkeypatch, {'enlarge animated webp': False, 'rotation': rotation})
    window = DrawHarness([image_tools.load_pixbuf(webp), pixbuf((3, 2))])
    window.zoom.set_fit_mode(constants.ZoomMode.BEST)
    window.zoom.set_scale_up(True)
    original_size = (2, 3) if rotation else (3, 2)
    try:
        dispatch_draw(window)
        boxes = window.layout.get_content_boxes()
        assert boxes[0].get_size() == original_size
        assert boxes[1].get_size()[0] > original_size[0]
        window.get_visible_area_size.return_value = (3, 3)
        dispatch_draw(window)
        size = window.layout.get_content_boxes()[0].get_size()
        assert all(value <= original for value, original in zip(size, original_size))
        assert size != original_size
    finally:
        window.imagehandler.stop_animations()


@pytest.mark.parametrize('rotation', [0, 90])
@pytest.mark.parametrize('limit', [0, 2048])
def test_webp_display_cap_limits_layout_and_preserves_static_pages(
        webp: bytes, monkeypatch: pytest.MonkeyPatch,
        rotation: int, limit: int) -> None:
    set_prefs(monkeypatch, {'animated webp max size': limit,
                            'enlarge animated webp': True, 'rotation': rotation})
    poster = pixbuf((2400, 1600))
    poster.webp_animation = image_tools.load_pixbuf(webp).webp_animation
    window = DrawHarness([poster, pixbuf((2400, 1600))])
    window.get_visible_area_size.return_value = (6000, 6000)
    window.zoom.set_fit_mode(constants.ZoomMode.BEST)
    window.zoom.set_scale_up(True)
    # Exercise production layout and poster drawing without starting large workers.
    playback = Mock(pages={}, displayed=Mock(return_value=None))
    window.imagehandler._animations = playback
    dispatch_draw(window)
    animated, static = [box.get_size() for box in window.layout.get_content_boxes()]
    assert max(animated) == (2048 if limit else max(static))
    assert max(static) > 2400
    assert animated[0] / animated[1] == pytest.approx(2 / 3 if rotation else 3 / 2, abs=0.002)
    specification = playback.configure.call_args.args[0][0]
    assert specification[3][0] == animated
    displayed = window.images[0].get_pixbuf()
    assert (displayed.get_width(), displayed.get_height()) == animated


def test_webp_opaque_lanczos_reserves_pillow_conversion_buffers(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    frames = [Image.new('RGB', (3, 2), color) for color in ('red', 'green', 'blue')]
    poster = image_tools.load_pixbuf(_encode_webp(frames, 100))
    assert not poster.get_has_alpha()
    original = poster.get_rowstride() * poster.get_height()
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    target = list(_webp_spec(poster)[3])
    target[5] = 2  # Bilinear uses GDK, without Pillow conversion buffers.
    monkeypatch.setitem(prefs, 'animation cache limit', (7 * original + 2 * 96) / (1024 * 1024))
    try:
        playback.configure([(0, (0, 1), poster, tuple(target))])
        assert playback.pages[0].capacity == 4
        target[5] = image_tools.SCALING_LANCZOS
        playback.configure([(0, (0, 1), poster, tuple(target))])
        assert not playback.pages and playback.limited
        monkeypatch.setitem(prefs, 'animation cache limit', (9 * original + 4 * 96) / (1024 * 1024))
        playback.configure([(0, (0, 1), poster, tuple(target))])
        assert playback.pages[0].capacity == 4 and not playback.limited
    finally:
        playback.clear()


@pytest.mark.parametrize('rotation', [0, 90, 180])
def test_webp_native_size_lanczos_keeps_playback_and_queue_capacity(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, rotation: int) -> None:
    frames = [Image.new('RGB', (3, 2), color) for color in ('red', 'blue')]
    poster = image_tools.load_pixbuf(_encode_webp(frames, 100))
    assert not poster.get_has_alpha()
    size = (2, 3) if rotation in (90, 270) else (3, 2)
    target = list(_webp_spec(poster, size=size, rotation=rotation)[3])
    target[5] = 2
    original = poster.get_rowstride() * poster.get_height()
    outputs = 3 if rotation else 2
    monkeypatch.setitem(prefs, 'animation cache limit', (7 * original + outputs * 24) / (1024 * 1024))
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([(0, (0, 1), poster, tuple(target))])
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        playback.tick()
        started = page.started
        target[5] = image_tools.SCALING_LANCZOS
        playback.configure([(0, (0, 1), poster, tuple(target))])
        assert playback.pages[0] is page and page.started == started
        assert page.capacity == 4 and not playback.limited
        until(lambda: page.ready is not None and page.ready[0][1] == page.revision)
        playback.tick()
        assert (page.display.get_width(), page.display.get_height()) == size
    finally:
        playback.clear()


def test_webp_nonmodal_notice_opens_settings_and_hides_on_recovery() -> None:
    window = Gtk.Window()
    window.actiongroup = Mock()
    window._event_box = Gtk.EventBox()
    window._event_box.set_size_request(300, 200)
    window._event_box.show()
    overlay = main.MainWindow._create_image_overlay(window)
    window.add(overlay)
    window.show()
    dispatch()
    notice = window._animation_notice
    try:
        assert overlay.get_mapped() and window._event_box.get_mapped()
        size = window._event_box.get_allocated_width(), window._event_box.get_allocated_height()
        assert not notice.get_visible() and notice.get_no_show_all()
        main.MainWindow._animation_notice_changed(window, True)
        dispatch()
        assert notice.get_visible()
        assert size == (window._event_box.get_allocated_width(), window._event_box.get_allocated_height())
        notice.response(Gtk.ResponseType.ACCEPT)
        window.actiongroup.get_action.assert_called_once_with('preferences')
        window.actiongroup.get_action.return_value.activate.assert_called_once_with()
        main.MainWindow._animation_notice_changed(window, False)
        assert not notice.get_visible()
    finally:
        window.destroy()


def test_webp_late_decoder_queues_every_frame(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    poster = image_tools.load_pixbuf(webp)
    opened = image_tools._open_pil
    decoders = []

    def decoder(source: Any) -> Any:
        result = opened(source)
        seek = result.seek

        def blocked(index: int) -> Any:
            if not gate.release.is_set():
                gate.block()
            return seek(index)

        result.seek = Mock(side_effect=blocked)
        decoders.append(result)
        return result

    monkeypatch.setattr(image_tools, '_open_pil', decoder)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        assert gate.entered.wait(5)
        page = playback.pages[0]
        until(lambda: page.ready is not None)
        clock = [101.0]
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
        gate.release.set()
        until(lambda: len(page.frames) == 5)
        assert [frame[0] for frame in page.frames] == list(range(5))
        for serial in range(6):
            until(lambda: page.ready is not None and page.ready[0][0] == serial)
            clock[0] += 0.1
            playback.tick()
            assert page.presented == serial
        until(lambda: page.finished)
        assert [[call.args[0] for call in result.seek.call_args_list]
                for result in decoders] == [[1, 2], [0, 1, 2]]
        assert page.frames[0][0] == 5
    finally:
        gate.release.set()
        playback.clear()


def test_webp_resize_revision_rejects_stale_worker_output(
        webp: bytes, monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    poster = image_tools.load_pixbuf(webp)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    render = webp_animation._Page.render
    qualities = []

    def blocked(page: Any, frame: Any, target: tuple, fast: bool) -> Any:
        qualities.append(fast)
        if len(qualities) == 1:
            gate.block()
        return render(page, frame, target, fast)

    monkeypatch.setattr(webp_animation._Page, 'render', blocked)
    try:
        playback.configure([_webp_spec(poster)])
        assert gate.entered.wait(5)
        page = playback.pages[0]
        started = page.started
        playback.configure([_webp_spec(poster, size=(12, 8))])
        playback.configure([_webp_spec(poster, size=(18, 12), rotation=180)])
        assert page.started == started and page.revision == 3
        gate.release.set()
        until(lambda: page.ready is not None)
        assert page.ready[0][1] == 3
        playback.tick()
        assert playback.displayed(0).get_width() == 18
        assert True in qualities
        page.settle = 0
        playback.tick()
        until(lambda: page.ready is not None and page.ready[0][1] == 4)
        assert qualities[-1] is False
    finally:
        gate.release.set()
        playback.clear()


@pytest.mark.parametrize('operation', ['navigation', 'cleanup', 'preference'])
def test_webp_cleanup_releases_decoder_and_fences_results(
        webp: bytes, handler: Any, monkeypatch: pytest.MonkeyPatch,
        operation: str) -> None:
    handler._window.images = [Gtk.Image()]
    poster = image_tools.load_pixbuf(webp)
    real_open = image_tools._open_pil
    decoders = []

    def opened(source: Any) -> Any:
        decoder = real_open(source)
        decoder.close = Mock(wraps=decoder.close)
        decoders.append(decoder)
        return decoder

    monkeypatch.setattr(image_tools, '_open_pil', opened)
    playback = handler.animations
    playback.configure([_webp_spec(poster)])
    page = playback.pages[0]
    until(lambda: page.ready is not None)
    if operation == 'navigation':
        handler.set_page(2)
    elif operation == 'cleanup':
        handler.cleanup()
    else:
        monkeypatch.setitem(prefs, 'animation mode', constants.ANIMATION_DISABLED)
        playback.configure([_webp_spec(poster)])
    assert page.stopped and not page.frames and page.ready is None
    assert all(decoder.close.called for decoder in decoders)
    assert playback.pages == {} and playback.timer is None
    dispatch()
    assert handler._window.images[0].get_pixbuf() is None


def test_webp_decode_failure_retains_poster_and_logs(
        webp: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    poster = image_tools.load_pixbuf(webp)
    decoder = image_tools._open_pil(webp)
    seek = decoder.seek

    def broken(index: int) -> Any:
        if index == 2:
            raise ValueError('broken frame')
        return seek(index)

    decoder.seek = broken
    monkeypatch.setitem(prefs, 'animation cache limit', (5 * 24 + 2 * 96) / (1024 * 1024))
    closed = Mock(wraps=decoder.close)
    decoder.close = closed
    monkeypatch.setattr(image_tools, '_open_pil', lambda source: decoder)
    error = Mock()
    monkeypatch.setattr(webp_animation.log, 'error', error)
    playback = webp_animation.WebPPlayback([Gtk.Image()])
    try:
        playback.configure([_webp_spec(poster)])
        page = playback.pages[0]
        until(lambda: len(page.frames) == 2 and page.ready is not None)
        playback.tick()
        until(lambda: page.ready is not None and page.ready[0][0] == 1)
        monkeypatch.setattr(webp_animation, 'time', SimpleNamespace(monotonic=lambda: 100.041))
        playback.tick()
        assert page.presented == 1
        until(lambda: page.failed and closed.called)
        until(lambda: page.ready is not None)
        playback.tick()
        expected = image_tools.fit_pixbuf_to_rectangle(poster, (6, 4), 0)
        assert playback.images[0].get_pixbuf().get_pixels() == expected.get_pixels()
        error.assert_called_once()
    finally:
        playback.clear()


@pytest.mark.parametrize('mode', [constants.ZoomMode.BEST, constants.ZoomMode.WIDTH,
                                  constants.ZoomMode.HEIGHT, constants.ZoomMode.MANUAL])
def test_webp_draw_fit_resize_transform_and_double_page_continuity(
        webp: bytes, mode: int, monkeypatch: pytest.MonkeyPatch) -> None:
    poster = image_tools.load_pixbuf(webp)
    window = DrawHarness([poster, image_tools.load_pixbuf(webp)])
    window.zoom.set_fit_mode(mode)
    window.zoom.set_scale_up(True)
    window.imagehandler.render_conventional = Mock(side_effect=AssertionError('frame cached'))
    window.imagehandler.upscaler.render = Mock(return_value=None)
    try:
        dispatch_draw(window)
        playback = window.imagehandler.animations
        assert len(playback.pages) == 2
        originals = dict(playback.pages)
        started = [page.started for page in originals.values()]
        for page in originals.values():
            until(lambda: page.ready is not None)
        playback.tick()
        for slot, page in originals.items():
            assert window.images[slot].get_pixbuf() is page.display
        current = [page.frames[0][0] for page in originals.values()]
        window.get_visible_area_size.return_value = (600, 500)
        dispatch_draw(window)
        assert playback.pages == originals
        assert [page.started for page in originals.values()] == started
        assert [page.frames[0][0] for page in originals.values()] == current
        assert all(page.target[0] == tuple(window.layout.get_content_boxes()[slot].get_size())
                   for slot, page in originals.items())
        set_prefs(monkeypatch, {'rotation': 90, 'horizontal flip': True,
                                'vertical flip': True})
        dispatch_draw(window)
        for page in originals.values():
            until(lambda: page.ready is not None and page.ready[0][1] == page.revision)
        playback.tick()
        for page in originals.values():
            expected = image_tools.fit_pixbuf_to_rectangle(
                _webp_pixbuf(page.frames[0][2]), page.target[0], 90)
            expected = image_tools.flip_pixbuf(image_tools.flip_pixbuf(expected, 0), 1)
            assert page.display.get_pixels() == expected.get_pixels()
            assert page.target[1:4] == (90, True, True)
        window.imagehandler.render_conventional.assert_not_called()
        window.imagehandler.upscaler.render.assert_not_called()
    finally:
        window.imagehandler.stop_animations()
