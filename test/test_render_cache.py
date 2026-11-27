"""Exact conventional redraw caching, bounded storage and ownership."""

import gc
from io import BytesIO
import tempfile
from typing import Any
from unittest.mock import Mock
import weakref

import pytest
from PIL import Image

from ._page_fakes import DRAW_PREFS, DrawHarness as _DrawHarness, dispatch_draw as _dispatch_draw, set_prefs
from gi.repository import GdkPixbuf
from mcomix import constants, image_tools
from mcomix.render_cache import RenderCache
from mcomix.preferences import prefs


@pytest.fixture(autouse=True)
def _isolated_preferences(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    set_prefs(monkeypatch, dict(DRAW_PREFS, **{
        'brightness': 1.1, 'scaling quality': int(GdkPixbuf.InterpType.BILINEAR),
        'checkered bg for transparent images': True, 'neural upscaling': False}))


def _page(alpha: bool = False, size: tuple = (48, 64)) -> Any:
    im = Image.new('RGBA' if alpha else 'RGB', size)
    for x in range(size[0]):
        for y in range(size[1]):
            rgb = ((x * 13 + y) % 256, (y * 7) % 256, (x * y) % 256)
            im.putpixel((x, y), rgb + ((x * 5) % 256,) if alpha else rgb)
    return image_tools.pil_to_pixbuf(im)


def _pixels(pixbuf: Any) -> bytes:
    return image_tools.pixbuf_to_pil(pixbuf).tobytes()


@pytest.mark.parametrize('rotation,alpha,quality', [
    (0, False, int(GdkPixbuf.InterpType.BILINEAR)), (90, True, image_tools.SCALING_LANCZOS)])
def test_cached_pixels_match_existing_scaler(
        rotation: int, alpha: bool, quality: int,
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(prefs, 'scaling quality', quality)
    source = _page(alpha)
    expected = image_tools.fit_pixbuf_to_rectangle(source, (31, 39), rotation)
    cache = RenderCache()
    scaler = Mock(wraps=image_tools.fit_pixbuf_to_rectangle)
    monkeypatch.setattr(image_tools, 'fit_pixbuf_to_rectangle', scaler)
    first = cache.render(0, source, (31, 39), rotation)
    second = cache.render(0, source, (31, 39), rotation)
    assert _pixels(first) == _pixels(second) == _pixels(expected)
    assert (second.get_width(), second.get_height()) == (31, 39)
    assert scaler.call_count == 1
    # Cached pixels are shared read-only; the flip/enhance steps make new images.
    assert second is first
    flipped = image_tools.flip_pixbuf(second, 0)
    assert flipped is not second
    assert _pixels(cache.render(0, source, (31, 39), rotation)) == _pixels(expected)


def test_identity_resize_preserves_existing_no_op() -> None:
    source = _page()
    cache = RenderCache()
    assert cache.render(0, source, (48, 64), 0) is source
    assert cache.render(0, source, (48, 64), 0) is source


def test_resize_keys_cover_generation_source_geometry_and_preferences(
        monkeypatch: pytest.MonkeyPatch) -> None:
    source = _page(True)
    cache = RenderCache()
    scaler = Mock(wraps=image_tools.fit_pixbuf_to_rectangle)
    monkeypatch.setattr(image_tools, 'fit_pixbuf_to_rectangle', scaler)
    cache.render(0, source, (20, 30), 0)
    cache.render(0, source, (20, 30), 0)
    cache.render(1, source, (20, 30), 0)
    cache.render(1, source.copy(), (20, 30), 0)
    cache.render(1, source, (21, 30), 0)
    cache.render(1, source, (20, 30), 90)
    monkeypatch.setitem(prefs, 'scaling quality', int(GdkPixbuf.InterpType.NEAREST))
    cache.render(1, source, (20, 30), 90)
    monkeypatch.setitem(prefs, 'checkered bg for transparent images', False)
    actual = cache.render(1, source, (20, 30), 90)
    expected = image_tools.fit_in_rectangle(
        source, 20, 30, keep_ratio=False, scale_up=True, rotation=90,
        scaling_quality=prefs['scaling quality'])
    assert _pixels(actual) == _pixels(expected)
    assert scaler.call_count == 7


def test_cache_budget_eviction_overbudget_and_source_release(
        monkeypatch: pytest.MonkeyPatch) -> None:
    source = _page()
    probe = image_tools.fit_pixbuf_to_rectangle(source, (11, 13), 0)
    budget = probe.get_rowstride() * probe.get_height()
    cache = RenderCache(max_bytes=budget)
    scaler = Mock(wraps=image_tools.fit_pixbuf_to_rectangle)
    monkeypatch.setattr(image_tools, 'fit_pixbuf_to_rectangle', scaler)
    cache.render(0, source, (11, 13), 0)
    assert cache.bytes_used == budget
    cache.render(0, source, (10, 13), 0)
    assert 0 < cache.bytes_used <= budget
    cache.render(0, source, (11, 13), 0)
    assert scaler.call_count == 3
    cache.render(0, source, (100, 100), 0)
    cache.render(0, source, (100, 100), 0)
    assert scaler.call_count == 5
    assert cache.bytes_used <= budget
    scaler.reset_mock()
    reference = weakref.ref(source)
    del source
    gc.collect()
    assert reference() is None
    cache.prune([])
    assert cache.bytes_used == 0
    assert not cache._scaled


@pytest.mark.parametrize('double,manga,setting', [
    (False, False, 'contrast'), (True, False, 'autocontrast'), (True, True, 'sharpness')])
def test_background_preserves_full_image_enhancement_and_order(
        double: bool, manga: bool, setting: str,
        monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [_page()]
    if double:
        pages.append(_page(size=(32, 48)))
    window = _DrawHarness(pages, manga)
    setattr(window.enhancer, setting, True if setting == 'autocontrast' else 1.4)
    samples = [window.enhancer.enhance(page) for page in pages]
    if manga:
        samples.reverse()
    expected = image_tools.get_most_common_edge_colour(tuple(samples) if double else samples[0])
    operation = Mock(wraps=window.enhancer.enhance)
    window.enhancer.enhance = operation
    assert window.imagehandler.get_pixbuf_auto_background(len(pages)) == expected
    assert window.imagehandler.get_pixbuf_auto_background(len(pages)) == expected
    assert operation.call_count == len(pages)
    # A result is a private list, not the cached color tuple.
    result = window.imagehandler.get_pixbuf_auto_background(len(pages))
    result[0] = -1
    assert window.imagehandler.get_pixbuf_auto_background(len(pages)) == expected
    for field, value in [('brightness', 0.8), ('contrast', 0.6),
                         ('saturation', 0.7), ('sharpness', 0.8),
                         ('autocontrast', not window.enhancer.autocontrast),
                         ('invert_color', True)]:
        setattr(window.enhancer, field, value)
        window.imagehandler.get_pixbuf_auto_background(len(pages))
    assert operation.call_count == 7 * len(pages)
    window.imagehandler._page_generation += 1
    window.imagehandler.get_pixbuf_auto_background(len(pages))
    assert operation.call_count == 8 * len(pages)


def test_background_animation_static_poster() -> None:
    stream = BytesIO()
    Image.new('RGB', (24, 32), 'red').save(
        stream, format='GIF', save_all=True,
        append_images=[Image.new('RGB', (24, 32), 'blue')], duration=100, loop=0)
    animation = image_tools.load_pixbuf(stream.getvalue())
    assert image_tools.is_animation(animation)
    window = _DrawHarness([animation])
    enhancer = window.enhancer
    enhancer.enhance = Mock(wraps=enhancer.enhance)
    first = window.imagehandler.get_pixbuf_auto_background(1)
    assert window.imagehandler.get_pixbuf_auto_background(1) == first
    assert enhancer.enhance.call_count == 1
    assert window.imagehandler._raw_pixbufs[0] is animation


def test_actual_draw_reuses_resize_and_background_but_reapplies_enhancement(
        monkeypatch: pytest.MonkeyPatch) -> None:
    window = _DrawHarness([_page()])
    window.zoom.set_fit_mode(constants.ZoomMode.BEST)
    window.zoom.set_scale_up(True)
    scaler = Mock(wraps=image_tools.fit_pixbuf_to_rectangle)
    enhancer = Mock(wraps=window.enhancer.enhance)
    monkeypatch.setattr(image_tools, 'fit_pixbuf_to_rectangle', scaler)
    window.enhancer.enhance = enhancer
    _dispatch_draw(window)
    pixels = _pixels(window.images[0].get_pixbuf())
    _dispatch_draw(window)
    assert _pixels(window.images[0].get_pixbuf()) == pixels
    assert scaler.call_count == 1
    assert enhancer.call_count == 3  # Two visible enhancements, one raw edge sample.
    for hflip, vflip, rotation in [(True, False, 0), (False, True, 0), (True, True, 90)]:
        monkeypatch.setitem(prefs, 'horizontal flip', hflip)
        monkeypatch.setitem(prefs, 'vertical flip', vflip)
        monkeypatch.setitem(prefs, 'rotation', rotation)
        _dispatch_draw(window)
        rect = window.layout.get_content_boxes()[0].get_size()
        source = image_tools.fit_in_rectangle(
            window.imagehandler._raw_pixbufs[0], *rect,
            keep_ratio=False, scale_up=True,
            rotation=rotation, scaling_quality=prefs['scaling quality'])
        if hflip:
            source = image_tools.flip_pixbuf(source, 0)
        if vflip:
            source = image_tools.flip_pixbuf(source, 1)
        assert _pixels(window.images[0].get_pixbuf()) == _pixels(window.enhancer.enhance(source))
    # The shared cached resize must survive the flipped/enhanced draws intact.
    calls = scaler.call_count
    for name, value in (('horizontal flip', False), ('vertical flip', False), ('rotation', 0)):
        monkeypatch.setitem(prefs, name, value)
    _dispatch_draw(window)
    assert scaler.call_count == calls
    assert _pixels(window.images[0].get_pixbuf()) == pixels


def test_handler_reset_cleanup_and_decoded_eviction_drop_intermediates(
        monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [_page(), _page()]
    window = _DrawHarness(pages)
    handler = window.imagehandler
    handler._inflight_decodes = {}
    handler._thread = Mock()
    handler._page_dimensions = {}
    handler._wanted_pixbufs = [0, 1]
    handler._wanted_page_width = 1
    handler.render_conventional(pages[0], (10, 10), 0)
    handler.render_conventional(pages[1], (10, 10), 0)
    handler.get_pixbuf_auto_background(2)
    monkeypatch.setattr(handler, '_ask_for_pages', Mock(return_value=[0]))
    handler.do_cacheing()
    assert len(handler._render_cache._scaled) == 1
    assert not handler._render_cache._colors
    handler.reset_page_list(['replacement.png'])
    assert handler._render_cache.bytes_used == 0
    handler.render_conventional(pages[0], (10, 10), 0)
    handler.cleanup()
    assert handler._render_cache.bytes_used == 0
    assert not handler._render_cache._scaled


def test_background_source_replacement_and_manga_order_are_distinct() -> None:
    pages = [_page(), _page(size=(32, 49))]
    window = _DrawHarness(pages)
    enhance = Mock(wraps=window.enhancer.enhance)
    window.enhancer.enhance = enhance
    window.imagehandler.get_pixbuf_auto_background(2)
    window.imagehandler.get_pixbuf_auto_background(2)
    assert enhance.call_count == 2
    window.is_manga_mode = True
    window.imagehandler.get_pixbuf_auto_background(2)
    assert enhance.call_count == 4
    window.imagehandler._raw_pixbufs[0] = pages[0].copy()
    window.imagehandler.get_pixbuf_auto_background(2)
    assert enhance.call_count == 6
