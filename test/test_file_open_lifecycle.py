"""File ownership across queued GTK input, modal loops and partial opens."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock

import pytest
from gi.repository import GLib, Gtk

from mcomix import archive_tools, event, file_chooser_main_dialog, file_handler, ui
from mcomix.preferences import prefs
from ._app_fakes import PNG, fake_app, loaded, pump, write_archive


@pytest.fixture
def book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    reader = Mock()
    reader.get_page.return_value = 1
    reader.get_date.return_value = datetime(2026, 1, 1)
    monkeypatch.setattr(file_handler.last_read_page, 'LastReadPage', lambda *args: reader)
    app = fake_app(monkeypatch, tmp_path)
    app.handler.write_fileinfo_file = Mock()
    archives = []
    factory = archive_tools.get_recursive_archive_handler

    def archive_factory(*args: Any, **kwargs: Any) -> Any:
        archives.append(factory(*args, **kwargs))
        return archives[-1]

    monkeypatch.setattr(archive_tools, 'get_recursive_archive_handler', archive_factory)
    app.paths = [write_archive(tmp_path / (name + '.tar'), ['%s-0.png' % name, '%s-1.png' % name])
                 for name in 'abc']
    app.archives, app.reader = archives, reader
    yield app
    app.close()
    assert all(archive._main_archive.tar is None for archive in archives)


def _drop(book: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Gtk, 'drag_get_source_widget', lambda context: None)
    target = SimpleNamespace(_window=book.window)
    selection = SimpleNamespace(get_uris=lambda: [Path(path).as_uri()])
    event.EventHandler.drag_n_drop_event(target, None, None, 0, 0, selection, 0, 0)


def _choose(book: Any, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_chooser_main_dialog, '_close_main_filechooser_dialog', Mock())
    target = SimpleNamespace(_window=book.window, filechooser=Mock())
    file_chooser_main_dialog._MainFileChooserDialog.files_chosen(target, [path])


@pytest.mark.parametrize('source,lazy', [(_drop, False), (_choose, True)])
def test_queued_input_waits_until_outer_open_owns_its_book(
        book: Any, monkeypatch: pytest.MonkeyPatch, lazy: bool, source: Any) -> None:
    monkeypatch.setitem(prefs, 'lazy archive reading', lazy)
    handler = book.handler
    a, b, c = book.paths
    assert handler.open_file(a)
    loaded(handler, a)
    old_temp = handler._tmp_dir
    old_worker = handler._extractor._list_thread
    GLib.idle_add(lambda: (source(book, b, monkeypatch), False)[1])
    assert handler.open_file(c)
    assert handler._current_file == c, 'closing dispatched unrelated GTK input'
    c_temp = handler._tmp_dir
    loaded(handler, b)
    assert old_temp in book.deleted and c_temp in book.deleted
    assert handler._tmp_dir not in book.deleted
    assert Path(handler._tmp_dir).is_dir()
    assert not old_worker._threads
    assert handler.read_file(book.images._image_files[0]) == PNG
    assert book.archives[0]._main_archive.tar is None
    moved = Path(a).with_suffix('.released')
    Path(a).rename(moved)  # Prove the old Windows archive handle is released.
    moved.rename(a)


def test_file_closed_reentry_uses_latest_copied_request(book: Any) -> None:
    handler = book.handler
    a, b, c = book.paths
    handler.open_file(a)
    loaded(handler, a)
    calls = []

    def changed() -> None:
        if calls:
            return
        calls.append(True)
        assert handler.open_file(c)
        requested = [b, c]
        assert handler.open_file(requested, start_page=2)
        requested[:] = [a]
        assert handler._current_file is None

    handler.file_closed += changed
    handler.open_file(c)
    loaded(handler, b)
    assert handler._start_page == 2
    assert handler._file_provider.list_files() == [b, c]
    assert handler._tmp_dir not in book.deleted


def test_shutdown_discards_pending_opens_and_runs_after_lifecycle(book: Any) -> None:
    handler = book.handler
    a, b, _ = book.paths
    calls = []

    def changed() -> None:
        handler.open_file(b)
        handler.begin_shutdown(lambda: (calls.append(handler._file_operation_active),
                                        handler.close_file()))
        assert handler.open_file(b) is False
        assert not calls

    handler.file_opened += changed
    handler.open_file(a)
    pump(lambda: bool(calls))
    assert calls == [False]
    assert len(book.archives) == 1
    assert handler._tmp_dir is None and handler._file_provider is None
    handler.write_fileinfo_file.assert_not_called()
    book.window.uimanager.recent.add.assert_not_called()


def test_pending_input_is_not_replaced_by_listing(
        book: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = book.handler
    a, b, _ = book.paths
    extractor = handler._extractor
    setup = extractor.setup
    listed = []

    def setup_then_nested_loop(*args: Any, **kwargs: Any) -> Any:
        # A modal loop inside the open of a: the user opens b, then a's listing arrives.
        condition = setup(*args, **kwargs)
        extractor.contents_listed += lambda *args: listed.append(args)
        assert handler.open_file(b)
        pump(lambda: listed)
        return condition

    monkeypatch.setattr(extractor, 'setup', setup_then_nested_loop)
    assert handler.open_file(a)
    loaded(handler, b)
    # The listing of a never shows a in place of the queued open.
    assert [call.args for call in book.window.uimanager.recent.add.call_args_list] == [(b,)]


def test_failed_setup_releases_partial_archive_and_storage(
        book: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = book.handler
    setup = handler._extractor.setup

    def fail(*args: Any, **kwargs: Any) -> None:
        setup(*args, **kwargs)
        raise OSError('setup failed after acquiring resources')

    monkeypatch.setattr(handler._extractor, 'setup', fail)
    assert handler.open_file(book.paths[0]) is False
    assert not handler.file_loaded
    assert handler._tmp_dir is None
    assert handler._extractor._archive_closed
    assert not handler._extractor._list_thread._threads
    assert book.deleted


def test_missing_file_leaves_no_book(book: Any, tmp_path: Path) -> None:
    assert book.handler.open_file(book.paths[0])
    loaded(book.handler, book.paths[0])
    assert book.handler.open_file(str(tmp_path / 'missing.cbz')) is False
    assert not book.handler.file_loaded


class _Actions(dict):
    def get_action(self, name: str) -> Gtk.Action:
        return self.setdefault(name, Gtk.Action(name=name))


def test_close_and_archive_navigation_stay_available_while_loading(book: Any) -> None:
    handler = book.handler
    actions = _Actions()
    menus = SimpleNamespace(_window=book.window, _actiongroup=actions,
                            bookmarks=Mock(), bookmarks_popup=Mock())

    def refresh() -> None:
        ui.MainUI.set_sensitivities(menus)

    book.window.uimanager.set_sensitivities = refresh
    handler.file_opened += refresh  # As MainWindow subscribes.
    handler.file_closed += refresh

    leaving = ['close', 'next_archive', 'previous_archive', 'next_directory',
               'previous_directory']

    def sensitive() -> list:
        return [name for name in leaving + ['delete'] if actions[name].is_sensitive()]

    assert handler.open_file(book.paths[0])
    assert handler.file_loading
    assert sensitive() == leaving
    loaded(handler)
    assert sensitive() == leaving + ['delete']
    handler.close_file()
    assert sensitive() == []


@pytest.mark.parametrize('extensions,comments', [
    (['*.txt'], ['a.txt', 'd.TXT']), (['.txt', 'nfo'], ['a.txt', 'd.TXT', 'e.nfo']), ([], [])])
def test_comment_extensions_match_literally(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extensions: list, comments: list) -> None:
    monkeypatch.setitem(prefs, 'comment extensions', extensions)
    app = fake_app(monkeypatch, tmp_path)  # The next start builds a FileHandler.
    try:
        regex = app.handler.get_comment_regex()
        assert [name for name in ['a.txt', 'b.', 'c.atxt', 'd.TXT', 'e.nfo']
                if regex.search(name)] == comments
    finally:
        app.close()


def test_unexpected_error_releases_operation_guard(
        book: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = book.handler
    with monkeypatch.context() as patch:
        patch.setattr(handler, '_initialize_fileprovider',
                      Mock(side_effect=RuntimeError('unexpected')))
        with pytest.raises(RuntimeError, match='unexpected'):
            handler.open_file(book.paths[0])
    assert not handler._file_operation_active
    assert handler._pending_file_operation is None
    assert handler.open_file(book.paths[1])
    loaded(handler, book.paths[1])


@pytest.mark.parametrize('shutdown', [False, True])
def test_real_resume_dialog_defers_open_and_unwinds_shutdown(
        book: Any, shutdown: bool) -> None:
    handler = book.handler
    a, b, _ = book.paths
    book.reader.get_page.return_value = 2
    dialog_parent = Gtk.Window()
    for name, value in vars(book.window).items():
        setattr(dialog_parent, name, value)
    handler._window = dialog_parent
    callbacks = []

    def during_dialog() -> bool:
        if handler._resume_dialog is None:
            return True
        assert handler._file_operation_active
        assert handler.open_file(b)
        assert handler._current_file == a
        if shutdown:
            handler.begin_shutdown(lambda: (callbacks.append('stopped'),
                                            handler.close_file()))
        else:
            callbacks.append('opened')
            book.reader.get_page.return_value = 1
            handler._resume_dialog.response(Gtk.ResponseType.YES)
        return False

    def timeout() -> bool:
        if not callbacks:
            GLib.source_remove(source)
        if handler._resume_dialog is not None:
            handler._resume_dialog.response(Gtk.ResponseType.CANCEL)
        return False

    source = GLib.idle_add(during_dialog)
    safety = GLib.timeout_add(3000, timeout)
    try:
        handler.open_file(a)
        pump(lambda: bool(callbacks))
        if shutdown:
            assert callbacks == ['stopped']
            assert handler._tmp_dir is None
            assert len(book.archives) == 1
            handler.write_fileinfo_file.assert_not_called()
            book.window.uimanager.recent.add.assert_not_called()
        else:
            # The second book must not prompt again during this bounded test.
            book.reader.get_page.return_value = 1
            loaded(handler, b)
            assert callbacks == ['opened']
        assert handler._resume_dialog is None
    finally:
        GLib.source_remove(safety)
        dialog_parent.destroy()


def test_last_page_write_error_still_releases_real_archive(
        book: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = book.handler
    handler.open_file(book.paths[0])
    loaded(handler, book.paths[0])
    old_temp = handler._tmp_dir
    archive = handler._extractor._archive._main_archive
    worker = handler._extractor._list_thread
    monkeypatch.setattr(book.reader, 'clear_page', Mock(side_effect=OSError('disk full')))
    handler.close_file()
    assert archive.tar is None and not worker._threads
    assert old_temp in book.deleted and not Path(old_temp).exists()
    assert handler._tmp_dir is None and not handler.file_loaded
    assert not handler._file_operation_active


@pytest.mark.parametrize('owner', ['extractor', 'imagehandler'])
def test_close_failure_preserves_owned_storage_for_retry(
        book: Any, monkeypatch: pytest.MonkeyPatch, owner: str) -> None:
    handler = book.handler
    handler.open_file(book.paths[0])
    loaded(handler, book.paths[0])
    old_temp = handler._tmp_dir
    target = handler._extractor if owner == 'extractor' else book.images
    method = 'close' if owner == 'extractor' else 'cleanup'
    with monkeypatch.context() as patch:
        patch.setattr(target, method, Mock(side_effect=RuntimeError('close failed')))
        with pytest.raises(RuntimeError, match='close failed'):
            handler.close_file()
    assert handler._tmp_dir == old_temp and Path(old_temp).is_dir()
    assert old_temp not in book.deleted
    assert not handler._file_operation_active
    handler.close_file()
    assert handler._tmp_dir is None and not Path(old_temp).exists()
    assert handler._extractor._archive_closed
    assert not handler._extractor._list_thread._threads
