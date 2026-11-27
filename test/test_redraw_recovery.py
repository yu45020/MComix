"""Regression coverage for failed redraws and animated smart backgrounds."""

from io import BytesIO
from typing import Any

import pytest
from PIL import Image, PngImagePlugin

from mcomix import constants, image_tools
from mcomix.preferences import prefs
from ._page_fakes import DRAW_PREFS, dispatch as _dispatch, set_prefs
# Other test modules build on the draw harness through this module.
from ._page_fakes import DrawHarness as _DrawHarness, dispatch_draw as _dispatch_draw


@pytest.fixture(autouse=True)
def _draw_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    set_prefs(monkeypatch, DRAW_PREFS)


def _animated_page() -> Any:
    stream = BytesIO()
    Image.new('RGB', (24, 32), (60, 80, 100)).save(
        stream, format='GIF', save_all=True,
        append_images=[Image.new('RGB', (24, 32), (180, 40, 20))],
        duration=100, loop=0)
    animation = image_tools.load_pixbuf(stream.getvalue())
    assert image_tools.is_animation(animation)
    assert not animation.is_static_image()
    return animation


@pytest.mark.parametrize('stage', ['before_render', 'after_freeze'])
def test_failed_draw_can_render_the_next_page(
        stage: str, caplog: Any) -> None:
    page = image_tools.pil_to_pixbuf(Image.new('RGB', (24, 32), 'red'))
    window = _DrawHarness([page])
    bin_window = window._main_layout.get_bin_window.return_value
    if stage == 'before_render':
        failing = window._update_toggles_visibility
    else:
        failing = window._main_layout.move
    failing.side_effect = RuntimeError('injected redraw failure')

    _dispatch_draw(window)
    assert 'Could not draw page' in caplog.text
    assert 'injected redraw failure' in caplog.text
    assert not window._waiting_for_redraw
    if stage == 'after_freeze':
        bin_window.freeze_updates.assert_called_once()
        bin_window.thaw_updates.assert_called_once()
    else:
        bin_window.freeze_updates.assert_not_called()

    failing.side_effect = None
    next_page = image_tools.pil_to_pixbuf(Image.new('RGB', (24, 32), 'blue'))
    window.imagehandler._raw_pixbufs[0] = next_page
    _dispatch_draw(window)
    assert window.images[0].get_visible()
    rendered = image_tools.pixbuf_to_pil(window.images[0].get_pixbuf())
    assert rendered.getpixel((0, 0)) == (0, 0, 255)
    assert bin_window.freeze_updates.call_count == (2 if stage == 'after_freeze' else 1)
    assert bin_window.thaw_updates.call_count == bin_window.freeze_updates.call_count


@pytest.mark.parametrize('profile', [
    '\nexif\nbad\n00', '\nexif\n4\n49492a00', '\nexif\n8\n6761726261676521'])
def test_png_with_malformed_exif_profile_draws(profile: str, caplog: Any) -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text('Raw profile type exif', profile)
    stream = BytesIO()
    Image.new('RGB', (24, 32), 'red').save(stream, format='PNG', pnginfo=info)
    window = _DrawHarness([image_tools.load_pixbuf(stream.getvalue())])

    _dispatch_draw(window)
    assert 'Could not draw page' not in caplog.text
    assert window.images[0].get_visible()
    assert window.images[0].get_pixbuf().get_height() > window.images[0].get_pixbuf().get_width()


@pytest.mark.parametrize('smartbg,smartthumbbg,double,manga', [
    (True, False, False, False), (False, True, True, False), (True, True, True, True)])
def test_animated_smart_background_preserves_page_animation(
        smartbg: bool, smartthumbbg: bool, double: bool, manga: bool,
        monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    monkeypatch.setitem(prefs, 'smart bg', smartbg)
    monkeypatch.setitem(prefs, 'smart thumb bg', smartthumbbg)
    animation = _animated_page()
    pages = [animation]
    if double:
        pages.append(image_tools.pil_to_pixbuf(Image.new('RGB', (24, 32), 'red')))
    window = _DrawHarness(pages, manga)
    samples = [window.enhancer.enhance(image_tools.static_image(p)) for p in pages]
    if manga:
        samples.reverse()
    expected = image_tools.get_most_common_edge_colour(
        tuple(samples) if double else samples[0])

    _dispatch_draw(window)
    assert 'Could not draw page' not in caplog.text
    assert window.images[0].get_animation() is animation
    assert window.images[0].get_visible()
    assert window.imagehandler._raw_pixbufs[0] is animation
    assert not animation.is_static_image()
    if smartbg:
        window.set_bg_colour.assert_called_once_with(expected)
    if smartthumbbg:
        window.thumbnailsidebar.change_thumbnail_background_color.assert_called_once_with(expected)

    # A second real idle draw still displays the animation, and can process
    # both background options after enhancement preferences are changed.
    window.enhancer.brightness = 0.8
    _dispatch_draw(window)
    assert window.images[0].get_animation() is animation
    assert 'Could not draw page' not in caplog.text


def test_page_turn_queued_behind_a_redraw_draws_at_the_start() -> None:
    page = image_tools.pil_to_pixbuf(Image.new('RGB', (24, 32), 'red'))
    window = _DrawHarness([page])
    # A resize queues a redraw; a PageDown in the same event batch follows.
    window.draw_image()
    _dispatch_draw(window)
    window.scroll_to_predefined.assert_called_once_with(
        (constants.SCROLL_TO_START,) * 2, constants.FIRST_INDEX)
    # The next redraw without a request keeps the reader's position.
    window.draw_image()
    _dispatch()
    window.scroll_to_predefined.assert_called_once()


def test_unloaded_and_unavailable_draws_release_pending_request() -> None:
    page = image_tools.pil_to_pixbuf(Image.new('RGB', (24, 32), 'red'))
    window = _DrawHarness([page])
    window.filehandler.file_loaded = False
    _dispatch_draw(window)
    window._clear_main_area.assert_called_once()
    window.filehandler.file_loaded = True
    window.imagehandler._available_images.clear()
    _dispatch_draw(window)
    assert not window.images[0].get_visible()
    window.imagehandler._available_images.add(0)
    _dispatch_draw(window)
    assert window.images[0].get_visible()
