"""Clearing a sidebar resets the row used when thumbnails are loaded again."""

from types import SimpleNamespace
import pytest
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GdkPixbuf  # noqa: E402

from mcomix import callback, thumbbar  # noqa: E402


@pytest.mark.parametrize('old_row,pages', [(99, 2), (1, 4)])
def test_clear_resets_selected_row_before_reload(old_row: int, pages: int) -> None:
    imagehandler = SimpleNamespace(
        get_number_of_pages=lambda: pages,
        page_available=callback.CallbackList(None, lambda *args: None))
    window = SimpleNamespace(imagehandler=imagehandler,
                             page_changed=callback.CallbackList(None, lambda *args: None),
                             filehandler=SimpleNamespace(file_loaded=False))
    sidebar = thumbbar.ThumbnailSidebar(window)
    try:
        filler = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 2)
        sidebar._thumbnail_liststore.append([1, filler, True])
        sidebar._currently_selected_row = old_row
        sidebar._loaded = True
        sidebar.clear()
        assert not sidebar._loaded
        assert len(sidebar._thumbnail_liststore) == 0
        window.filehandler.file_loaded = True
        sidebar.load_thumbnails()
        assert len(sidebar._thumbnail_liststore) == pages
        assert sidebar._get_selected_row() == 0
    finally:
        sidebar.destroy()
