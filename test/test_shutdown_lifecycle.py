"""Owned previews and library scans finish before shutdown releases resources."""

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock

import gi
import pytest

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GLib, Gtk  # noqa: E402

from mcomix import callback, main, message_dialog, pageselect  # noqa: E402
from mcomix.library import add_progress_dialog, backend, backend_types  # noqa: E402
from mcomix.library import main_dialog  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402
from ._app_fakes import isolated_library  # noqa: E402


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    result = isolated_library(monkeypatch, tmp_path)
    yield result
    result.close()


def test_scan_single_active_and_database_snapshot_on_caller(
        library: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()
    library.watchlist.add_directory(str(tmp_path), recursive=True)
    original_execute = library.execute
    db_threads = []

    def execute(*args: Any, **kwargs: Any) -> Any:
        db_threads.append(threading.current_thread())
        return original_execute(*args, **kwargs)

    def walk(*args: Any) -> Iterator[Any]:
        entered.set()
        assert release.wait(3)
        yield str(tmp_path), [], ['new.cbz']

    monkeypatch.setattr(library, 'execute', execute)
    monkeypatch.setattr(backend_types.os, 'walk', walk)
    try:
        assert library.watchlist.scan_for_new_files()
        assert entered.wait(3)
        worker = library.watchlist._scan_thread
        assert not worker.daemon
        assert not library.watchlist.scan_for_new_files()
        assert library.watchlist._scan_thread is worker
        assert db_threads and all(t is threading.current_thread() for t in db_threads)
    finally:
        release.set()
        library.watchlist.stop_scan()
    assert not worker.is_alive()


def test_backend_close_cancels_before_database_and_discards_queued_result(
        library: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library.watchlist.add_directory(str(tmp_path), recursive=True)
    entered, release = threading.Event(), threading.Event()
    queued = []
    errors = []
    closed = threading.Event()
    result = Mock()
    library.watchlist.new_files_found += lambda *args: result(*args)

    def walk(*args: Any) -> Iterator[Any]:
        entered.set()
        assert release.wait(3)
        yield str(tmp_path), ['more'], ['new.cbz']
        raise AssertionError('cancelled walk was advanced')

    def close() -> None:
        try:
            library.close()
        except Exception as error:
            errors.append(error)
        finally:
            closed.set()

    monkeypatch.setattr(backend_types.os, 'walk', walk)
    monkeypatch.setattr(backend_types.GLib, 'idle_add',
                        lambda *args: queued.append(args))
    assert library.watchlist.scan_for_new_files()
    assert entered.wait(3)
    worker = library.watchlist._scan_thread
    # An already queued publication must also be invalidated by shutdown.
    queued.append((library.watchlist._publish_new_files,
                   library.watchlist._scan_generation, library.watchlist._scan_cancel,
                   ['old.cbz'], object()))
    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert library.watchlist._scan_cancel.wait(3)
        assert not closed.is_set()
        assert library._con.execute('select 1').fetchone() == 1
        assert not library.watchlist.scan_for_new_files()
    finally:
        release.set()
        closer.join(3)
    assert closed.is_set() and not errors
    assert not worker.is_alive() and library._con is None
    for function, *args in queued:
        assert function(*args) is False
    result.assert_not_called()
    assert not library.watchlist.scan_for_new_files()


@pytest.mark.parametrize('recursive', [False, True])
def test_entry_cancellation_stops_before_archive_probe(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recursive: bool) -> None:
    cancel = threading.Event()
    probes = []
    entry = backend_types._WatchListEntry(
        str(tmp_path), recursive, backend_types.DefaultCollection)

    def probe(name: str) -> bool:
        probes.append(name)
        cancel.set()
        return True

    monkeypatch.setattr(backend_types.archive_tools, 'is_archive_file', probe)
    monkeypatch.setattr(backend_types.os, 'listdir', lambda path: ['a.cbz', 'b.cbz'])
    monkeypatch.setattr(backend_types.os, 'walk',
                        lambda path: iter([(str(tmp_path), [], ['a.cbz', 'b.cbz'])]))
    assert entry.get_new_files([], cancel) == []
    assert probes == ['a.cbz']


def test_library_represent_does_not_start_another_automatic_scan(
        monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = Mock()
    monkeypatch.setattr(main_dialog, '_dialog', None)
    monkeypatch.setattr(main_dialog, '_LibraryDialog', Mock(return_value=dialog))
    monkeypatch.setitem(prefs, 'scan for new books on library startup', True)
    window = Mock()
    for _ in range(3):
        assert main_dialog.open_dialog(None, window)
    assert dialog.scan_for_new_files.call_count == 1
    assert dialog.present.call_count == 2


class _SelectorImages:
    get_number_of_pages = Mock(return_value=3)
    get_current_page = Mock(return_value=1)
    get_thumbnail = Mock(return_value=None)

    @callback.Callback
    def page_available(self, page: int) -> None:
        pass


class _ShutdownWindow(Gtk.Window):
    """MainWindow's shutdown entry points on a bare window with their state."""

    terminate_program = main.MainWindow.terminate_program
    _close_transient_dialogs = main.MainWindow._close_transient_dialogs

    def __init__(self) -> None:
        super().__init__()
        self._terminating = False


def test_shutdown_stops_owned_selector_and_does_not_join_unrelated_threads(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Window(_ShutdownWindow):
        _terminate_program = main.MainWindow._terminate_program

    window = Window()
    window.imagehandler = _SelectorImages()
    window.imagehandler.upscaler = SimpleNamespace(stop=lambda: calls.append('upscaler'))
    window.write_config_files = lambda: calls.append('config')
    window.filehandler = SimpleNamespace(
        file_loaded=False, begin_shutdown=lambda ready: ready(),
        close_file=lambda: calls.append('file'), _resume_dialog=None)
    monkeypatch.setitem(prefs, 'hide all', False)
    monkeypatch.setattr(main_dialog, '_dialog', None)
    monkeypatch.setattr(backend, 'LibraryBackend',
                        lambda: SimpleNamespace(close=lambda: calls.append('database')))
    selector = pageselect.Pageselector(window)
    workers = list(selector._thread._threads)
    assert workers
    # A daemon job which is not owned by the viewer must not enter a global join.
    release = threading.Event()
    other = threading.Thread(target=release.wait, daemon=True)
    other.start()
    monkeypatch.setattr(other, 'join', Mock(side_effect=AssertionError('unowned join')))
    try:
        window.terminate_program()
        window.terminate_program()
        assert selector._closed and all(not t.is_alive() for t in workers)
        assert calls == ['config', 'file', 'upscaler', 'database']
        # Completions that arrive after shutdown must not touch the preview.
        selector._image_preview.set_from_pixbuf = Mock()
        selector._thumbnail_finished(1, None)
        window.imagehandler.page_available(1)
        selector._image_preview.set_from_pixbuf.assert_not_called()
    finally:
        release.set()
        window.destroy()


def test_failing_config_writes_still_save_the_rest_and_stop_workers(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Window(_ShutdownWindow):
        _terminate_program = main.MainWindow._terminate_program
        write_config_files = main.MainWindow.write_config_files

    def fail() -> None:
        raise PermissionError('read-only')

    window = Window()
    window.imagehandler = SimpleNamespace(
        upscaler=SimpleNamespace(stop=lambda: calls.append('upscaler')))
    window.filehandler = SimpleNamespace(
        file_loaded=False, begin_shutdown=lambda ready: ready(),
        write_fileinfo_file=fail, close_file=lambda: calls.append('file'),
        _resume_dialog=None)
    for key in ('auto load last file', 'path to last file', 'page of last file',
                'image of last file'):
        monkeypatch.setitem(prefs, key, prefs[key])
    monkeypatch.setattr(main.preferences, 'write_preferences_file',
                        lambda: calls.append('preferences'))
    monkeypatch.setattr(main.keybindings, 'keybinding_manager',
                        lambda owner: SimpleNamespace(save=fail))
    monkeypatch.setattr(main_dialog, '_dialog', None)
    monkeypatch.setattr(backend, 'LibraryBackend',
                        lambda: SimpleNamespace(close=lambda: calls.append('database')))
    try:
        window.terminate_program()
        assert calls == ['preferences', 'file', 'upscaler', 'database']
    finally:
        window.destroy()


def test_deferred_shutdown_unwinds_real_modal_dialog(
        monkeypatch: pytest.MonkeyPatch) -> None:
    callbacks, calls = [], []

    class Window(_ShutdownWindow):
        def _terminate_program(self) -> None:
            calls.append('cleanup')

    window = Window()
    window.filehandler = SimpleNamespace(begin_shutdown=callbacks.append,
                                         _resume_dialog=None)
    modal = Gtk.Dialog(transient_for=window, modal=True)
    GLib.idle_add(window.terminate_program)
    try:
        response = modal.run()
        assert response == Gtk.ResponseType.NONE
        assert len(callbacks) == 1
        assert not calls
        callbacks[0]()  # The active FileHandler frame invokes this synchronously.
        assert calls == ['cleanup']
        window.terminate_program()
        assert len(callbacks) == 1
    finally:
        modal.destroy()
        window.destroy()


def test_add_progress_stops_after_library_close_during_event_pump(
        monkeypatch: pytest.MonkeyPatch) -> None:
    library = Gtk.Window()
    library._closed = False
    added = []
    library.backend = SimpleNamespace(add_book=lambda path, collection: added.append(path))
    window = Gtk.Window()

    def close_library() -> bool:
        library._closed = True
        for dialog in Gtk.Window.list_toplevels():
            if dialog.get_transient_for() is library:
                dialog.destroy()
        return False

    GLib.idle_add(close_library)
    try:
        progress = add_progress_dialog._AddLibraryProgressDialog(
            library, window, ['first.cbz', 'second.cbz'], None)
        assert progress._destroy
        assert added == ['first.cbz']
    finally:
        library.destroy()
        window.destroy()


def test_shutdown_cancels_resume_without_destroying_remember_checkbox(
        monkeypatch: pytest.MonkeyPatch) -> None:
    callbacks, remembered = [], []

    class Window(_ShutdownWindow):
        def _terminate_program(self) -> None:
            remembered.append('cleanup')

    window = Window()
    dialog = message_dialog.MessageDialog(window, Gtk.DialogFlags.MODAL,
                                          Gtk.MessageType.INFO, Gtk.ButtonsType.YES_NO)
    dialog.set_should_remember_choice('wave3-resume-test', (Gtk.ResponseType.YES,))
    destroyed = []
    dialog.connect('destroy', lambda *args: destroyed.append(True))
    original = dialog.should_remember_choice

    def remember() -> bool:
        assert not destroyed
        remembered.append('checkbox')
        return original()

    monkeypatch.setattr(dialog, 'should_remember_choice', remember)

    def begin(ready: Any) -> None:
        callbacks.append(ready)
        dialog.response(Gtk.ResponseType.CANCEL)

    window.filehandler = SimpleNamespace(begin_shutdown=begin, _resume_dialog=dialog)
    GLib.idle_add(window.terminate_program)
    try:
        assert dialog.run() == Gtk.ResponseType.CANCEL
        assert remembered == ['checkbox']
        assert destroyed
        window.filehandler._resume_dialog = None
        callbacks[0]()
        assert remembered == ['checkbox', 'cleanup']
    finally:
        dialog.destroy()
        window.destroy()
