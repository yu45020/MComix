"""The magnifying lens over the pages the window draws."""

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import Mock

import pytest
from PIL import Image

from mcomix import image_tools, lens
from ._page_fakes import DRAW_PREFS, DrawHarness, dispatch_draw, set_prefs
from .test_redraw_recovery import _animated_page

LENS_SIZE = 20


@pytest.fixture(autouse=True)
def _lens_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    set_prefs(monkeypatch, {**DRAW_PREFS, 'brightness': 1.0,
                            'lens size': LENS_SIZE, 'lens magnification': 2.0,
                            'checkered bg for transparent images': False})


@pytest.fixture
def painted(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """Collect the lens pixbufs painted onto the (fake) layout window."""
    pixbufs: List[Any] = []

    def surface(pixbuf: Any, scale: int, window: Any) -> Any:
        pixbufs.append(pixbuf)
        return Mock()
    monkeypatch.setattr(lens.Gdk, 'cairo_surface_create_from_pixbuf', surface)
    return pixbufs


def _window(pages: List[Any]) -> DrawHarness:
    window = DrawHarness(pages)
    window._pending_decode_redraw = None
    window._hadjust = window._vadjust = Mock(get_value=Mock(return_value=0))
    window.cursor_handler = Mock()
    window.get_bg_colour = Mock(return_value=[0, 65535, 0])
    dispatch_draw(window)
    return window


def _move(window: DrawHarness, magnifier: Any = None) -> None:
    if magnifier is None:
        magnifier = lens.MagnifyingLens(window)
        magnifier.set_enabled(True)
    page = window.layout.get_content_boxes()[0]
    x, y = (p + s // 2 for p, s in zip(page.get_position(), page.get_size()))
    magnifier._motion_event(None, SimpleNamespace(x=x, y=y))


def _centre(pixbuf: Any) -> Any:
    return image_tools.pixbuf_to_pil(pixbuf).getpixel((LENS_SIZE // 2, LENS_SIZE // 2))[:3]


def test_lens_draws_over_an_animated_gif_page(painted: List[Any]) -> None:
    window = _window([_animated_page()])
    _move(window)
    assert len(painted) == 1
    assert painted[0].get_width() == LENS_SIZE + 2


def test_lens_shows_the_displayed_webp_frame(painted: List[Any]) -> None:
    window = _window([image_tools.pil_to_pixbuf(Image.new('RGB', (24, 32), 'red'))])
    size = window.layout.get_content_boxes()[0].get_size()
    frame = image_tools.pil_to_pixbuf(Image.new('RGB', tuple(size), 'blue'))
    window.imagehandler._animations = SimpleNamespace(
        displayed=lambda slot: frame if slot == 0 else None)
    _move(window)
    assert _centre(painted[0]) == (0, 0, 255)


@pytest.mark.parametrize('state', ['decode pending', 'redraw queued'])
def test_lens_waits_for_the_redraw_of_a_new_page(state: str, painted: List[Any]) -> None:
    window = _window([image_tools.pil_to_pixbuf(Image.new('RGB', (24, 32), 'red'))])
    window.filehandler._wait_on_file = Mock(side_effect=AssertionError('lens waited'))
    magnifier = lens.MagnifyingLens(window)
    magnifier.set_enabled(True)
    _move(window, magnifier)
    painted.clear()
    bin_window = window._main_layout.get_bin_window.return_value
    bin_window.invalidate_rect.reset_mock()
    if state == 'decode pending':
        # The next page of a solid archive is not extracted yet.
        window.imagehandler._raw_pixbufs.clear()
        window.imagehandler._available_images.clear()
        dispatch_draw(window)
    else:
        window.imagehandler._raw_pixbufs[0] = image_tools.pil_to_pixbuf(
            Image.new('RGB', (64, 8), 'blue'))
        window.draw_image()
    _move(window, magnifier)
    assert painted == []
    # The lens of the old page doesn't stay on screen.
    bin_window.invalidate_rect.assert_called_once()
    window.filehandler._wait_on_file.assert_not_called()
    dispatch_draw(window)
