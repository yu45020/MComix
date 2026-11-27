"""Library Copy and file URI drops use GTK strings and explicit destinations."""

from pathlib import Path
import posixpath
import tempfile
from types import SimpleNamespace
from typing import Any, Callable, Iterator, List
from unittest.mock import Mock
import urllib.parse

import gi
import pytest

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from mcomix import clipboard, last_read_page  # noqa: E402
from mcomix.library import add_progress_dialog, book_area, main_dialog  # noqa: E402
from ._app_fakes import isolated_library, pump, write_archive  # noqa: E402


@pytest.fixture
def selection() -> Iterator[Callable[[List[str]], Gtk.SelectionData]]:
    # A private same-process selection avoids overwriting the user's clipboard.
    owner = Gtk.Window()
    owner.realize()
    atom = Gdk.Atom.intern('_MCOMIX_LIBRARY_DROP_TEST', False)
    target = Gdk.Atom.intern('text/uri-list', False)
    Gtk.selection_add_target(owner, atom, target, 1)
    payload = []
    received = []
    owner.connect('selection-get', lambda widget, data, info, time: data.set_uris(payload))
    owner.connect('selection-received', lambda widget, data, time: received.append(data.copy()))
    assert Gtk.selection_owner_set(owner, atom, Gdk.CURRENT_TIME)

    def convert(uris: List[str]) -> Gtk.SelectionData:
        payload[:] = uris
        received.clear()
        assert Gtk.selection_convert(owner, atom, target, Gdk.CURRENT_TIME)
        assert len(received) == 1
        return received[0]

    yield convert
    owner.destroy()


def test_copy_selected_puts_only_the_unicode_path_on_the_clipboard() -> None:
    path = 'C:/comics/日本語 été 100% #.cbz'
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 12, 18)
    model = Gtk.ListStore(GdkPixbuf.Pixbuf, int, str)
    model.append([pixbuf, 1, path])
    iconview = Gtk.IconView.new_with_model(model)
    iconview.select_path(Gtk.TreePath.new_from_indices([0]))
    # A private selection avoids overwriting the user's clipboard.
    selection = '_MCOMIX_LIBRARY_COPY_TEST'
    area = SimpleNamespace(_iconview=iconview, _library=SimpleNamespace(
        _window=SimpleNamespace(clipboard=clipboard.Clipboard(None, selection))))
    received = Gtk.Clipboard.get(Gdk.Atom.intern(selection, False))
    try:
        book_area._BookArea._copy_selected(area)
        assert received.wait_for_text() == path
        assert not received.wait_is_image_available()
        received.set_text('other', -1)
        iconview.unselect_all()
        book_area._BookArea._copy_selected(area)
        assert received.wait_for_text() == 'other'
    finally:
        iconview.destroy()


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    result = isolated_library(monkeypatch, tmp_path)
    assert result.add_collection('Target')
    yield result
    result.close()


def test_real_gtk_uri_drop_imports_once_into_selected_id(
        library: Any, selection: Callable, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(write_archive(tmp_path / '日本語 été 100%20 #.cbz', ['page.png']))
    target = SimpleNamespace(
        _closed=False, backend=library, _window=None,
        collection_area=SimpleNamespace(get_current_collection=lambda: 1))
    target.add_books = lambda *args, **kwargs: main_dialog._LibraryDialog.add_books(target, *args, **kwargs)
    monkeypatch.setattr(add_progress_dialog, '_AddLibraryProgressDialog',
                        lambda owner, window, paths, collection: [library.add_book(path, collection) for path in paths])
    data = selection([path.as_uri(), 'https://example.com/not-a-book.cbz'])
    book_area._BookArea._drag_data_received(SimpleNamespace(_library=target), None, None, 0, 0, data)
    assert library.execute('select path from Book').fetchall() == [str(path)]
    assert library.execute('select collection, book from Contain').fetchall() == [(1, 1)]


@pytest.mark.parametrize('uri', [
    'https://example.com/book.cbz', 'file:relative.cbz', 'file:///C:/book.cbz?download=1',
    'file://user@server/share/book.cbz', 'file://[broken/share/book.cbz',
    'file:///C:/bad%00name.cbz'])
def test_invalid_drop_is_ignored(selection: Callable, uri: str) -> None:
    target = SimpleNamespace(_closed=False, add_books=Mock(),
                             collection_area=SimpleNamespace(get_current_collection=lambda: 1))
    book_area._BookArea._drag_data_received(SimpleNamespace(_library=target), None, None, 0, 0, selection([uri]))
    target.add_books.assert_not_called()


@pytest.mark.skipif(book_area.os.name != 'nt', reason='Windows path semantics')
@pytest.mark.parametrize('uri, expected', [
    ('file:///C:/comics/%E6%97%A5%E6%9C%AC%20%2520%20%23.cbz', r'C:\comics\日本 %20 #.cbz'),
    ('file://LOCALHOST/C:/comics/book%20one.cbz', r'C:\comics\book one.cbz'),
    ('file:C:/comics/book.cbz', r'C:\comics\book.cbz'),
    ('file://server/share/book%2520.cbz', r'\\server\share\book%20.cbz'),
    ('file:///C:/book%2523%252F.cbz', r'C:\book%23%2F.cbz')])
def test_windows_drive_localhost_and_unc_uris(uri: str, expected: str) -> None:
    assert book_area._local_file_uri_to_path(uri) == expected


def test_posix_local_paths_and_rejected_remote_authorities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(book_area, 'os', SimpleNamespace(name='posix', path=posixpath))
    monkeypatch.setattr(book_area.urllib.request, 'url2pathname', urllib.parse.unquote)
    assert book_area._local_file_uri_to_path('file:///comics/%C3%A9t%C3%A9%20%2520%23.cbz') == '/comics/été %20#.cbz'
    assert book_area._local_file_uri_to_path('file://localhost/comics/book.cbz') == '/comics/book.cbz'
    assert book_area._local_file_uri_to_path('file://server/share/book.cbz') is None
    assert book_area._local_file_uri_to_path('file:///comics/book%3F%23.cbz') == '/comics/book?#.cbz'


@pytest.mark.skipif(book_area.os.name != 'nt', reason='Windows path semantics')
def test_windows_drive_less_root_is_not_resolved_against_current_drive() -> None:
    assert book_area._local_file_uri_to_path('file:///book.cbz') is None


def test_drop_after_library_close_does_not_access_destroyed_widgets() -> None:
    target = SimpleNamespace(_closed=True, add_books=Mock())
    data = Mock()
    book_area._BookArea._drag_data_received(SimpleNamespace(_library=target), None, None, 0, 0, data)
    data.get_uris.assert_not_called()
    target.add_books.assert_not_called()


def test_drag_of_book_whose_cover_fails_shows_missing_image(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_cover(book: int) -> None:
        raise OSError('damaged cover')

    path = Gtk.TreePath.new_from_indices([0])
    area = SimpleNamespace(
        _library=SimpleNamespace(backend=SimpleNamespace(get_book_cover=broken_cover)),
        get_book_at_path=lambda path: 1)
    iconview = SimpleNamespace(get_cursor=lambda: (True, path, None),
                               get_selected_items=lambda: [path])
    icons = []
    monkeypatch.setattr(Gtk, 'drag_set_icon_pixbuf', lambda *args: icons.append(args))
    book_area._BookArea._drag_begin(area, iconview, None)
    assert len(icons) == 1


def test_activating_a_book_removed_elsewhere_keeps_the_library(library: Any) -> None:
    target = SimpleNamespace(backend=library, hide=Mock(), _window=Mock(),
                             _file_handler=Mock())
    main_dialog._LibraryDialog.open_book(target, [999])
    target.hide.assert_not_called()
    target._file_handler.open_file.assert_not_called()


def test_clearing_reading_history_refreshes_open_library(
        library: Any, tmp_path: Path) -> None:
    filed = write_archive(tmp_path / 'filed.cbz', ['page.png'])
    read = write_archive(tmp_path / 'read.cbz', ['page.png'])
    assert library.add_book(filed)
    pages = last_read_page.LastReadPage(library)
    pages.set_enabled(True)
    pages.set_page(read, 2)
    window = Gtk.Window()
    window.backend = library
    window._window = SimpleNamespace(enhancer=SimpleNamespace(enhance=lambda pixbuf: pixbuf))
    window.filter_string = None
    window.collection_area = SimpleNamespace(get_current_collection=lambda: -1)
    statusbar = Gtk.Statusbar()
    window.get_status_bar = lambda: statusbar
    area = book_area._BookArea(window)
    try:
        area.display_covers(-1)
        pump()
        listed = lambda: sorted(row[2] for row in area._iconview.get_model())  # noqa: E731
        assert listed() == sorted([filed, read])

        pages.clear_all()
        pump()
        assert listed() == [filed]
    finally:
        area.destroy()
        window.destroy()
