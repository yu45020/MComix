"""Archive replacement and disk deletion preserve originals on failure."""

import os
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock
import zipfile

import pytest
from gi.repository import GdkPixbuf, Gtk

from mcomix import edit_dialog, file_handler
from mcomix.library import backend, book_area
from mcomix.preferences import prefs
from ._app_fakes import fake_app, loaded


@pytest.fixture
def book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    app = fake_app(monkeypatch, tmp_path)
    app.handler.write_fileinfo_file = Mock()
    app.messages = []
    monkeypatch.setattr(edit_dialog.message_dialog, 'MessageDialog',
                        lambda *args: app.messages.append(Mock()) or app.messages[-1])
    monkeypatch.setattr(edit_dialog, '_close_dialog', Mock())
    app.archive = tmp_path / 'book.cbz'
    with zipfile.ZipFile(str(app.archive), 'w') as output:
        output.writestr('1.png', b'first page')
        output.writestr('2.png', b'second page')
    yield app
    app.close()


def _open(book: Any) -> None:
    assert book.handler.open_file(str(book.archive), start_page=1)
    loaded(book.handler)


def _pages(path: Path) -> list:
    with zipfile.ZipFile(str(path)) as archive:
        return [archive.read(name) for name in archive.namelist()]


def _editor(book: Any) -> Any:
    dialog = SimpleNamespace(file_handler=book.handler, _window=book.window, kill=False,
                             _retained_directories=[], set_sensitive=Mock())
    dialog._image_area = SimpleNamespace(get_file_listing=lambda: list(book.images._image_files))
    dialog._comment_area = SimpleNamespace(get_file_listing=lambda: [])
    dialog._delete_retained_directory = lambda path: (
        edit_dialog._EditArchiveDialog._delete_retained_directory(dialog, path))
    return dialog


def _pack(dialog: Any, destination: Path) -> None:
    edit_dialog._EditArchiveDialog._pack_archive(dialog, str(destination))


def _release(dialog: Any) -> None:
    for directory in dialog._retained_directories:
        shutil.rmtree(directory)


@pytest.mark.parametrize('lazy', [False, True])
def test_overwrite_current_real_zip_releases_handle_only_after_pack(
        book: Any, monkeypatch: pytest.MonkeyPatch, lazy: bool) -> None:
    monkeypatch.setitem(prefs, 'lazy archive reading', lazy)
    _open(book)
    old_temp = book.handler._tmp_dir
    old_archive = book.handler._extractor._archive
    dialog = _editor(book)
    _pack(dialog, book.archive)
    loaded(book.handler)
    assert old_archive._main_archive.zip.fp is None
    assert _pages(book.archive) == [b'first page', b'second page']
    assert dialog._retained_directories == [old_temp]
    assert os.path.isdir(old_temp)
    assert not list(book.archive.parent.glob('tmp.*'))
    assert not book.messages
    assert dialog.set_sensitive.call_args.args == (True,)
    assert book.window.set_cursor.call_args.args == (None,)
    _release(dialog)


@pytest.mark.parametrize('stage', ['materialize', 'pack', 'chmod', 'replace'])
def test_save_failure_preserves_original_restores_ui_and_removes_output(
        book: Any, monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    monkeypatch.setitem(prefs, 'lazy archive reading', True)
    _open(book)
    original = book.archive.read_bytes()
    dialog = _editor(book)
    original_inputs = list(book.images._image_files)
    if stage == 'materialize':
        monkeypatch.setattr(book.handler, 'materialize', Mock(side_effect=OSError('extract')))
    elif stage == 'pack':
        wait = edit_dialog.archive_packer.Packer.wait
        monkeypatch.setattr(edit_dialog.archive_packer.Packer, 'wait',
                            lambda packer: (wait(packer), False)[1])
    else:
        monkeypatch.setattr(edit_dialog.os, stage, Mock(side_effect=PermissionError(stage)))
    _pack(dialog, book.archive)
    if stage == 'replace':
        loaded(book.handler)
        assert all(Path(path).read_bytes() for path in original_inputs)
    assert book.archive.read_bytes() == original
    assert not list(book.archive.parent.glob('tmp.*'))
    assert len(book.messages) == 1
    assert dialog.set_sensitive.call_args.args == (True,)
    assert book.window.set_cursor.call_args.args == (None,)
    edit_dialog._close_dialog.assert_not_called()
    _release(dialog)


def test_replacement_failure_can_retry_original_inputs(
        book: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(prefs, 'lazy archive reading', True)
    _open(book)
    dialog = _editor(book)
    paths = list(dialog._image_area.get_file_listing())
    dialog._image_area.get_file_listing = lambda: paths
    with monkeypatch.context() as patch:
        patch.setattr(edit_dialog.os, 'replace', Mock(side_effect=PermissionError('locked')))
        _pack(dialog, book.archive)
        loaded(book.handler)
    _pack(dialog, book.archive)
    loaded(book.handler)
    assert _pages(book.archive) == [b'first page', b'second page']
    assert edit_dialog._close_dialog.called
    _release(dialog)


@pytest.mark.parametrize('busy', ['_file_operation_active', '_shutting_down'])
def test_retaining_close_rejects_deferred_ownership(book: Any, busy: str) -> None:
    _open(book)
    directory = book.handler._tmp_dir
    setattr(book.handler, busy, True)
    try:
        with pytest.raises(RuntimeError, match='busy'):
            book.handler.close_file(keep_temporary=True)
        assert book.handler._tmp_dir == directory
        assert book.handler.file_loaded
    finally:
        setattr(book.handler, busy, False)


def test_failed_retaining_close_keeps_temp_ownership(
        book: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _open(book)
    directory = book.handler._tmp_dir
    with monkeypatch.context() as patch:
        patch.setattr(book.handler, 'file_closed', Mock(side_effect=OSError('subscriber')))
        with pytest.raises(OSError):
            book.handler.close_file(keep_temporary=True)
        assert book.handler._tmp_dir == directory
        assert os.path.isdir(directory)


def test_reopening_from_close_callback_keeps_transferred_inputs(book: Any) -> None:
    _open(book)
    directory = book.handler._tmp_dir

    def nested_open() -> None:
        book.handler.open_file(str(book.archive), start_page=1)

    book.handler.file_closed += nested_open
    try:
        retained = book.handler.close_file(keep_temporary=True)
        loaded(book.handler)
        assert retained == directory
        assert book.handler._tmp_dir != directory
        assert os.path.isdir(retained)
        shutil.rmtree(retained)
    finally:
        book.handler.file_closed -= nested_open


def test_shutdown_from_close_aborts_replace_and_cleans_transferred_inputs(book: Any) -> None:
    _open(book)
    original = book.archive.read_bytes()
    directory = book.handler._tmp_dir
    dialog = _editor(book)
    dialog._image_area.cleanup = Mock()

    def shutdown() -> None:
        book.handler.begin_shutdown(
            lambda: edit_dialog._EditArchiveDialog._cleanup_retained_inputs(dialog, None))

    book.handler.file_closed += shutdown
    try:
        _pack(dialog, book.archive)
        assert dialog.kill
        assert not os.path.exists(directory)
        assert not dialog._retained_directories
        assert book.archive.read_bytes() == original
        assert not list(book.archive.parent.glob('tmp.*'))
        assert not book.messages
        assert dialog.set_sensitive.call_args.args == (False,)
    finally:
        book.handler.file_closed -= shutdown


def test_native_destroy_cleans_retained_inputs_once_without_waiting(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / 'retained'
    directory.mkdir()
    entered, release, deleted = threading.Event(), threading.Event(), threading.Event()
    remove = shutil.rmtree

    def gated_remove(path: str, ignore_errors: bool = False) -> None:
        entered.set()
        assert release.wait(5), 'cleanup gate timed out'
        remove(path, ignore_errors)
        deleted.set()

    monkeypatch.setattr(file_handler.shutil, 'rmtree', gated_remove)
    dialog = edit_dialog._EditArchiveDialog.__new__(edit_dialog._EditArchiveDialog)
    Gtk.Dialog.__init__(dialog)
    dialog.kill = False
    dialog._image_area = Mock()
    dialog.file_handler = SimpleNamespace(
        thread_delete=lambda path: file_handler.FileHandler.thread_delete(None, path))
    dialog._retained_directories = [str(directory)]
    dialog.connect('destroy', dialog._cleanup_retained_inputs)
    try:
        Gtk.Widget.destroy(dialog)
        assert dialog.kill
        assert entered.wait(2)
        assert not deleted.is_set()
        assert directory.is_dir()
        assert not dialog._retained_directories
        dialog._cleanup_retained_inputs(dialog)
    finally:
        release.set()
        assert deleted.wait(3)
    assert not directory.exists()


def test_directory_cleanup_launch_failure_falls_back_to_owned_cleanup(tmp_path: Path) -> None:
    directory = tmp_path / 'retained'
    directory.mkdir()
    dialog = SimpleNamespace(file_handler=SimpleNamespace(
        thread_delete=Mock(side_effect=RuntimeError('cannot start thread'))))
    edit_dialog._EditArchiveDialog._delete_retained_directory(dialog, str(directory))
    assert not directory.exists()


@pytest.fixture
def area(book: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A BookArea shell over a separate real library listing the given books."""
    monkeypatch.setattr(backend.constants, 'LIBRARY_DATABASE_PATH',
                        str(book.archive.parent / 'library.db'))
    library_backend = backend._LibraryBackend()
    store = Gtk.ListStore(GdkPixbuf.Pixbuf, int, str)
    library = SimpleNamespace(backend=library_backend, _file_handler=book.handler,
                              _closed=False, set_status_message=Mock())
    result = SimpleNamespace(_library=library, _liststore=store, _cache=Mock())
    result._iconview = SimpleNamespace(get_selected_items=lambda: [row.path for row in store])
    for name in ('get_book_at_path', 'remove_book_at_path', '_remove_book_ids_from_library'):
        setattr(result, name, getattr(book_area._BookArea, name).__get__(result))

    def add(*paths: Path) -> None:
        for path in paths:
            index = len(store) + 1
            library_backend.execute('insert into Book(id,name,path,pages,format,size,added) '
                                    'values(?,?,?,?,?,?,?)',
                                    (index, path.name, str(path), 2, 'ZIP', 1, '2026-10-18'))
            store.append([None, index, str(path)])

    result.add = add
    yield result
    library_backend.close()


def _confirm(monkeypatch: pytest.MonkeyPatch, effect: Any) -> None:
    message = Mock()
    message.run.side_effect = lambda: (effect(), Gtk.ResponseType.YES)[1]
    monkeypatch.setattr(book_area.message_dialog, 'MessageDialog', lambda *args: message)


def test_delete_current_real_zip_before_removing_record(book: Any, area: Any) -> None:
    _open(book)
    area.add(book.archive)
    book_area._BookArea._completely_remove_book(area, False)
    assert not book.archive.exists()
    assert area._library.backend.get_book_by_id(1) is None
    assert len(area._liststore) == 0
    assert not book.handler.file_loaded
    assert not book.messages


@pytest.mark.skipif(os.name != 'nt', reason='Windows delete sharing')
def test_partial_delete_keeps_locked_book_and_removes_successes(book: Any, area: Any) -> None:
    other = book.archive.parent / 'other.cbz'
    other.write_bytes(book.archive.read_bytes())
    area.add(book.archive, other)
    with zipfile.ZipFile(str(book.archive)):
        book_area._BookArea._completely_remove_book(area, False)
    assert book.archive.exists()
    assert not other.exists()
    assert area._library.backend.get_book_by_id(1) is not None
    assert area._library.backend.get_book_by_id(2) is None
    assert [row[1] for row in area._liststore] == [1]
    assert book.messages[-1].set_text.call_args.args[0] == 'Some books could not be deleted.'


def test_modal_library_close_prevents_delete(
        book: Any, area: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    area.add(book.archive)
    _confirm(monkeypatch, lambda: setattr(area._library, '_closed', True))
    book_area._BookArea._completely_remove_book(area, True)
    assert book.archive.exists()
    assert area._library.backend.get_book_by_id(1) is not None


def test_missing_unrelated_source_does_not_block_delete(
        book: Any, area: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    book.handler.archive_type = 0
    monkeypatch.setattr(book.handler, 'get_path_to_base',
                        lambda: str(book.archive.parent / 'moved.cbz'))
    area.add(book.archive)
    book_area._BookArea._completely_remove_book(area, False)
    assert not book.archive.exists()
    assert area._library.backend.get_book_by_id(1) is None
    assert not book.messages


def test_confirmation_row_reorder_does_not_change_delete_targets(
        book: Any, area: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = [book.archive, book.archive.parent / 'b.cbz', book.archive.parent / 'c.cbz']
    for path in paths[1:]:
        path.write_bytes(book.archive.read_bytes())
    area.add(*paths)
    area._iconview.get_selected_items = lambda: [
        Gtk.TreePath.new_from_indices([0]), Gtk.TreePath.new_from_indices([1])]
    _confirm(monkeypatch, lambda: area._liststore.reorder([2, 1, 0]))
    book_area._BookArea._completely_remove_book(area, True)
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()
    assert [row[1] for row in area._liststore] == [3]
    assert area._library.backend.get_books_in_collection() == [3]
