"""Archive editing uses the IconView drag API and a visible icon hotspot."""

from types import SimpleNamespace
from typing import Any

import cairo
import pytest
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GdkPixbuf, Gtk, Pango  # noqa: E402

from mcomix import callback, edit_image_area  # noqa: E402


@pytest.fixture
def area() -> Any:
    window = SimpleNamespace(imagehandler=SimpleNamespace(
        page_available=callback.CallbackList(None, lambda *args: None)))
    widget = edit_image_area._ImageArea(None, window)
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 24, 32)
    pixbuf.fill(0xDA2211FF)
    widget._liststore.append([pixbuf, 'page.png', 'page.png', True])
    host = Gtk.Window()
    host.add(widget)
    host.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
    yield widget
    host.destroy()


def test_real_iconview_cursor_and_cairo_surface(area: Any, monkeypatch: Any) -> None:
    iconview = area._iconview
    path = Gtk.TreePath.new_from_indices([0])
    iconview.set_cursor(path, None, False)
    calls = []
    context = object()
    monkeypatch.setattr(Gtk, 'drag_set_icon_pixbuf',
                        lambda *args: calls.append(args))
    area._drag_begin(iconview, context)
    assert len(calls) == 1
    actual_context, pixbuf, x, y = calls[0]
    assert actual_context is context
    assert pixbuf.get_width() >= 24 and pixbuf.get_height() >= 32
    assert any(pixbuf.get_pixels())
    assert (x, y) == (-5, -5)
    # The image list remains reorderable and drag imagery does not mutate it.
    assert iconview.get_reorderable()
    assert area.get_file_listing() == ['page.png']


def test_drag_without_cursor_is_noop(area: Any, monkeypatch: Any) -> None:
    assert not area._iconview.get_cursor()[0]
    monkeypatch.setattr(Gtk, 'drag_set_icon_pixbuf',
                        lambda *args: pytest.fail('unexpected drag icon'))
    area._drag_begin(area._iconview, object())


@pytest.mark.parametrize('scale', [1, 2])
def test_device_scaled_icon_keeps_lower_and_right_edges(
        area: Any, monkeypatch: Any, scale: int) -> None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 24 * scale, 32 * scale)
    surface.set_device_scale(scale, scale)
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 0, 0)
    cr.paint()
    cr.set_source_rgb(0, 0, 1)
    cr.rectangle(0, 16, 24, 16)
    cr.fill()
    cr.set_source_rgb(0, 1, 0)
    cr.rectangle(20, 0, 4, 32)
    cr.fill()
    area._iconview.set_cursor(Gtk.TreePath.new_from_indices([0]), None, False)
    monkeypatch.setattr(area._iconview, 'create_drag_icon', lambda path: surface)
    calls = []
    monkeypatch.setattr(Gtk, 'drag_set_icon_pixbuf',
                        lambda *args: calls.append(args))
    area._drag_begin(area._iconview, object())
    pixbuf = calls[0][1]
    assert (pixbuf.get_width(), pixbuf.get_height()) == (24, 32)
    pixels = pixbuf.get_pixels()
    stride, channels = pixbuf.get_rowstride(), pixbuf.get_n_channels()

    def color(x: int, y: int) -> bytes:
        offset = y * stride + x * channels
        return bytes(pixels[offset:offset + 3])

    assert color(4, 4) == bytes((255, 0, 0))
    assert color(4, 28) == bytes((0, 0, 255))
    assert color(22, 28) == bytes((0, 255, 0))
    assert calls[0][2:] == (-5, -5)


def test_imported_image_with_markup_characters_has_its_tooltip(area: Any) -> None:
    # The tooltip column is markup: an unescaped '&' leaves the image without one.
    area.add_extra_image('imports/Tom & Jerry <01>.jpg')
    tooltip = area._liststore[-1][1]
    assert Pango.parse_markup(tooltip, -1, '\0')[2] == 'Tom & Jerry <01>.jpg'
