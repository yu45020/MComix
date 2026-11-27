"""Shared scaffolding for the page cache, decoding and drawing tests."""

from io import BytesIO
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Dict, List
from unittest.mock import Mock

import gi
import pytest
from PIL import Image

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import GLib, Gtk  # noqa: E402

from mcomix import callback, constants, enhance_backend  # noqa: E402
from mcomix import image_handler, image_tools, main, zoom  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402

DRAW_PREFS = {
    'animation mode': constants.ANIMATION_NORMAL,
    'brightness': 1.2, 'contrast': 1.0, 'saturation': 1.0,
    'sharpness': 1.0, 'auto contrast': False, 'invert color': False,
    'smart bg': True, 'smart thumb bg': True, 'show thumbnails': True,
    'auto rotate from exif': True, 'rotation': 0,
    'auto rotate depending on size': constants.AUTOROTATE_NEVER,
    'horizontal flip': False, 'vertical flip': False,
}


def set_prefs(monkeypatch: pytest.MonkeyPatch, values: Dict[str, Any]) -> None:
    for name, value in values.items():
        monkeypatch.setitem(prefs, name, value)


def dispatch() -> None:
    """Run the GTK idles that are pending now."""
    context = GLib.MainContext.default()
    for _ in range(1000):
        if not context.pending():
            return
        context.iteration(False)
    pytest.fail('GTK idles did not settle')


def until(predicate: Callable[[], Any]) -> None:
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.002)
    assert predicate()


def png(size: Any = (2, 3), colour: Any = 'red') -> bytes:
    stream = BytesIO()
    Image.new('RGB', size, colour).save(stream, format='PNG')
    return stream.getvalue()


def pixbuf(size: Any = (2, 3), colour: Any = 'red') -> Any:
    return image_tools.pil_to_pixbuf(Image.new('RGB', size, colour))


def make_image_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
                       files: List[str], cache_limit: int) -> Any:
    """Return a real ImageHandler with a mocked FileHandler and no upscaler."""
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    set_prefs(monkeypatch, {
        'max pages to cache': cache_limit, 'default double page': False,
        'virtual double page for fitting images': 0,
        'auto rotate from exif': True, 'neural upscaling': False,
    })
    monkeypatch.setattr(image_handler.upscale, 'UpscaleService', Mock)
    filehandler = Mock(file_loaded=True, archive_type='zip')
    filehandler.file_available = callback.CallbackList(None, lambda paths: None)
    filehandler._archive_name.return_value = None
    filehandler.get_image_source.side_effect = lambda path: path
    window = SimpleNamespace(filehandler=filehandler, draw_image=Mock(),
                             _update_page_information=Mock())
    handler = image_handler.ImageHandler(window)
    window.imagehandler = handler
    handler._image_files = list(files)
    return handler


class Gate:
    """Blocks decodes until released; joins the threads it started on close."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._threads: List[threading.Thread] = []

    def block(self) -> None:
        self.entered.set()
        assert self.release.wait(5)

    def start(self, target: Callable[[], Any]) -> threading.Thread:
        thread = threading.Thread(target=target)
        self._threads.append(thread)
        thread.start()
        return thread

    def close(self) -> None:
        self.release.set()
        for thread in self._threads:
            thread.join(5)


class DrawHarness:
    """Production draw methods with real layout/zoom/images and inert UI."""

    draw_image = main.MainWindow.draw_image
    _draw_image = main.MainWindow._draw_image
    _draw_image_impl = main.MainWindow._draw_image_impl
    show_notice = main.MainWindow.show_notice

    def __init__(self, pages: List[Any], manga: bool = False) -> None:
        self._pending_notice = ''
        self._waiting_for_redraw = False
        self._redraw_scroll_to = None
        self._scroll = [Mock(), Mock()]
        self._main_layout = Mock()
        self._update_toggles_visibility = Mock()
        self._clear_main_area = Mock()
        self._show_scrollbars = Mock()
        self.osd = Mock()
        self.filehandler = Mock(file_loaded=True)
        self.images = [Gtk.Image(), Gtk.Image()]
        self.statusbar = Mock()
        self.thumbnailsidebar = Mock()
        self.set_bg_colour = Mock()
        self.get_visible_area_size = Mock(return_value=(300, 400))
        self.displayed_double = Mock(return_value=len(pages) == 2)
        self.is_manga_mode = manga
        self.zoom = zoom.ZoomModel()
        self._upscale_key = Mock(side_effect=lambda page: (0, str(page)))
        self._prefetch_upscaling = Mock()
        self.scroll_to_predefined = Mock()
        self.enhancer = enhance_backend.ImageEnhancer(self)
        from ._shells import image_handler_shell
        self.imagehandler = image_handler_shell()
        self.imagehandler._window = self
        self.imagehandler._page_lock = threading.RLock()
        self.imagehandler._page_generation = 0
        self.imagehandler._current_image_index = 0
        self.imagehandler._image_files = [str(i) for i in range(len(pages))]
        self.imagehandler._raw_pixbufs = dict(enumerate(pages))
        self.imagehandler._available_images = set(range(len(pages)))
        self.imagehandler.upscaler = Mock()
        self.imagehandler.upscaler.render.return_value = None


def dispatch_draw(window: DrawHarness) -> None:
    window.draw_image(scroll_to=constants.SCROLL_TO_START)
    context = GLib.MainContext.default()
    for _ in range(1000):
        if not window._waiting_for_redraw:
            return
        context.iteration(False)
    pytest.fail('redraw callback did not finish')
