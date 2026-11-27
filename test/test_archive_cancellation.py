"""Cancellation retains live nested archive resources without pinning GTK."""

import contextlib
import io
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock
import zipfile

import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk  # noqa: E402

from mcomix import edit_dialog, main  # noqa: E402
from mcomix.archive import archive_base, archive_recursive, password  # noqa: E402
from mcomix.archive.zip import ZipArchive  # noqa: E402
from mcomix.library import book_area  # noqa: E402
from ._archive_fakes import pump, real_file_handler  # noqa: E402


@pytest.fixture
def nested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    handler, window = real_file_handler(monkeypatch)
    deleted = []

    def delete(path: str) -> None:
        deleted.append(path)
        if Path(path).exists():
            shutil.rmtree(path)

    handler.thread_delete = delete
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as output:
        output.writestr('page.png', b'inner-page')
    outer = tmp_path / 'a.cbz'
    with zipfile.ZipFile(str(outer), 'w') as output:
        output.writestr('first.cbz', inner.getvalue())
        output.writestr('second.cbz', inner.getvalue())
    other = tmp_path / 'b.cbz'
    with zipfile.ZipFile(str(other), 'w') as output:
        output.writestr('other.png', b'other-page')
    entered, release = threading.Event(), threading.Event()
    extracted = []
    original = ZipArchive.extract

    def extract(archive: Any, filename: str, destination: str) -> None:
        if archive.archive == str(outer):
            extracted.append(filename)
            if filename == 'first.cbz':
                entered.set()
                assert release.wait(5), 'nested fixture not released'
        original(archive, filename, destination)

    monkeypatch.setattr(ZipArchive, 'extract', extract)
    yield SimpleNamespace(handler=handler, window=window, outer=outer, other=other,
                          entered=entered, release=release, extracted=extracted,
                          deleted=deleted)
    release.set()
    handler.close_file()
    handler.wait_for_file_release(str(outer))
    handler.wait_for_file_release(str(other))
    pump(lambda: True)


def _start(nested: Any) -> tuple:
    assert nested.handler.open_file(str(nested.outer))
    assert nested.entered.wait(2)
    return nested.handler._extractor, nested.handler._tmp_dir


def _switch(nested: Any) -> None:
    nested.handler.open_file(str(nested.other))
    pump(lambda: nested.handler.file_loaded)


@contextlib.contextmanager
def _released_soon(nested: Any) -> Iterator[None]:
    timer = threading.Timer(.1, nested.release.set)
    timer.start()
    try:
        yield
    finally:
        timer.join()


def test_switch_and_close_keep_gtk_live_and_retain_storage(nested: Any) -> None:
    handler = nested.handler
    old, temporary = _start(nested)
    _switch(nested)
    assert not nested.release.is_set()
    assert handler.read_file(nested.window.imagehandler._image_files[0]) == b'other-page'
    assert old._archive._main_archive.zip.fp is not None
    assert Path(temporary).is_dir() and temporary not in nested.deleted
    ticks = []
    GLib.idle_add(lambda: (ticks.append(True), False)[1])
    pump(lambda: ticks)
    assert not nested.release.is_set()
    handler.close_file()
    assert old._archive._main_archive.zip.fp is not None
    nested.release.set()
    handler.wait_for_file_release(str(nested.outer))
    assert old._archive._main_archive.zip.fp is None
    assert not old._list_thread._threads
    assert temporary in nested.deleted and not Path(temporary).exists()
    assert nested.extracted == ['first.cbz'], 'cancelled discovery extracted the next archive'
    moved = nested.outer.with_suffix('.released')
    nested.outer.rename(moved)
    moved.rename(nested.outer)


def test_shutdown_retains_finite_worker_ownership(nested: Any) -> None:
    handler = nested.handler
    old, temporary = _start(nested)
    handler.begin_shutdown(handler.close_file)
    assert handler._shutting_down and not handler.file_loading
    assert not handler.open_file(str(nested.other))
    assert Path(temporary).is_dir()
    job = handler._retired_archives[0]
    assert job.thread.is_alive() and not job.thread.daemon
    nested.release.set()
    handler.wait_for_file_release(str(nested.outer))
    assert old._archive_closed and not Path(temporary).exists()


@pytest.mark.parametrize('keep', [False, True])
def test_explicit_release_waits_and_transfers_storage_only_after_join(
        nested: Any, keep: bool) -> None:
    old, temporary = _start(nested)
    with _released_soon(nested):
        result = nested.handler.close_file(keep_temporary=keep, wait_for_release=not keep)
        assert nested.release.is_set() and old._archive_closed
        assert result == (temporary if keep else None)
        assert Path(temporary).exists() == keep
        if keep:
            shutil.rmtree(temporary)


def test_cancelled_publications_and_old_extractor_identity_are_rejected(nested: Any) -> None:
    handler = nested.handler
    old, temporary = _start(nested)
    _switch(nested)
    events = []
    old.contents_listed += lambda *args: events.append('listing')
    old.file_extracted += lambda *args: events.append('extraction')
    # A producer can load generation after cancellation. The cancelled flag
    # still rejects even a matching/new generation.
    old._publish_listing(old._generation, ['stale.png'])
    old._publish_extraction(old._generation, 'stale.png')
    handler.file_available += lambda paths: events.append(paths)
    handler._listed_contents(old, ['stale.png'])
    handler._extracted_file(old, 'stale.png')
    assert events == []
    assert handler._tmp_dir != temporary and handler.file_loaded


def test_retirement_launch_failure_retains_cancelled_inputs_for_retry(
        nested: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = nested.handler
    old, temporary = _start(nested)
    start = threading.Thread.start

    def fail(thread: threading.Thread) -> None:
        if thread.name == 'archive-retirement':
            raise RuntimeError('cannot launch')
        start(thread)

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, 'start', fail)
        with pytest.raises(RuntimeError, match='cannot launch'):
            handler.close_file()
    assert handler._tmp_dir == temporary and handler._extractor is old
    assert old._archive._main_archive.zip.fp is not None
    nested.release.set()
    handler.close_file(wait_for_release=True)
    assert old._archive_closed and not Path(temporary).exists()


@pytest.mark.parametrize('stage', ['close', 'delete'])
def test_retirement_failure_retains_storage_and_explicit_wait_retries(
        nested: Any, monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    handler = nested.handler
    old, temporary = _start(nested)
    target, attribute = (old._archive, 'close') if stage == 'close' else (handler, 'thread_delete')
    original = getattr(target, attribute)
    failures = [True]

    def fail_once(*args: Any) -> None:
        if failures:
            failures.pop()
            raise OSError('%s failed' % stage)
        original(*args)

    monkeypatch.setattr(target, attribute, fail_once)
    handler.open_file(str(nested.other))
    job = handler._retired_archives[0]
    nested.release.set()
    job.thread.join(3)
    assert isinstance(job.error, OSError) and Path(temporary).exists()
    assert old._archive_closed == (stage == 'delete')
    handler.wait_for_file_release(str(nested.outer))
    assert old._archive_closed and not Path(temporary).exists()
    assert job not in handler._retired_archives


def test_cancel_before_recursive_listing_closes_main_handle(tmp_path: Path) -> None:
    path = tmp_path / 'book.cbz'
    with zipfile.ZipFile(str(path), 'w') as output:
        output.writestr('page.png', b'page')
    archive = ZipArchive(str(path))
    recursive = archive_recursive.RecursiveArchive(archive, str(tmp_path / 'out'))
    recursive.cancel()
    assert list(recursive.iter_contents()) == []
    recursive.close()
    assert archive.zip.fp is None


def test_cancel_releases_queued_password_wait_without_display(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = archive_base.BaseArchive(str(tmp_path / 'secret.cbz'))
    archive._is_encrypted = True
    asked = Mock(side_effect=AssertionError('obsolete password dialog'))
    monkeypatch.setattr(password, 'ask_for_password', asked)
    worker = threading.Thread(target=archive.resolve_password)
    worker.start()
    # Wait until the callback has been queued, keeping GTK deliberately idle.
    end = time.monotonic() + 2
    while not GLib.MainContext.default().pending():
        assert time.monotonic() < end
        time.sleep(.001)
    archive.cancel()
    worker.join(2)
    pump(lambda: True)
    assert not worker.is_alive() and archive._password == ''
    asked.assert_not_called()


def test_password_cancellation_uses_owner_identity_and_is_idempotent(tmp_path: Path) -> None:
    path = str(tmp_path / 'same.cbz')
    old = archive_base.BaseArchive(path)
    replacement = archive_base.BaseArchive(path)
    first_dialog, replacement_dialog = Mock(), Mock()
    first = (old, first_dialog)
    second = (replacement, replacement_dialog)
    password._active_dialogs.extend([first, second])
    try:
        old.cancel()
        old.cancel()
        first_dialog.response.assert_called_once_with(Gtk.ResponseType.CANCEL)
        replacement_dialog.response.assert_not_called()
    finally:
        password._active_dialogs.remove(first)
        password._active_dialogs.remove(second)


def test_cancel_during_password_dialog_construction_does_not_run_modal(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        password_dialog: Any) -> None:
    archive = archive_base.BaseArchive(str(tmp_path / 'secret.cbz'))
    dialog = Mock()

    def create(*args: Any) -> Mock:
        # Cancellation from a backend can arrive before the dialog is added
        # to the live registry; it must still prevent the subsequent run().
        worker = threading.Thread(target=archive.cancel)
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        return dialog

    monkeypatch.setattr(password.message_dialog, 'MessageDialog', create)
    archive._password_required()
    dialog.run.assert_not_called()
    dialog.destroy.assert_called_once_with()
    assert not password._active_dialogs


def test_viewer_delete_waits_for_switched_away_archive(
        nested: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    old, temporary = _start(nested)
    dialog = Mock()
    dialog.run.return_value = Gtk.ResponseType.OK
    monkeypatch.setattr(main.message_dialog, 'MessageDialog', lambda *args: dialog)
    nested.window.imagehandler.get_real_path.return_value = str(nested.outer)
    with _released_soon(nested):
        main.MainWindow.delete(nested.window)
        assert old._archive_closed and not nested.outer.exists()
        assert not Path(temporary).exists()


def test_save_as_waits_for_old_destination_retirement(
        nested: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edit_dialog, '_close_dialog', Mock())
    handler = nested.handler
    old, temporary = _start(nested)
    _switch(nested)
    dialog = SimpleNamespace(file_handler=handler, _window=nested.window, kill=False,
                             _retained_directories=[], set_sensitive=Mock())
    dialog._image_area = SimpleNamespace(
        get_file_listing=lambda: list(nested.window.imagehandler._image_files))
    dialog._comment_area = SimpleNamespace(get_file_listing=lambda: [])
    with _released_soon(nested):
        edit_dialog._EditArchiveDialog._pack_archive(dialog, str(nested.outer))
        assert old._archive_closed and not Path(temporary).exists()
        with zipfile.ZipFile(str(nested.outer)) as archive:
            assert [archive.read(name) for name in archive.namelist()] == [b'other-page']


def test_library_delete_waits_for_previously_retired_book(nested: Any) -> None:
    old, temporary = _start(nested)
    _switch(nested)
    store = Gtk.ListStore(int, str, str)
    store.append([1, 'old book', str(nested.outer)])
    library = SimpleNamespace(_file_handler=nested.handler, _closed=False, backend=Mock())
    library.backend.get_book_path.return_value = str(nested.outer)
    area = SimpleNamespace(_library=library, _liststore=store, _iconview=Mock(),
                           get_book_at_path=lambda path: 1, _remove_book_ids_from_library=Mock())
    area._iconview.get_selected_items.return_value = [Gtk.TreePath.new_from_indices([0])]
    with _released_soon(nested):
        book_area._BookArea._completely_remove_book(area, request_response=False)
        assert old._archive_closed and not nested.outer.exists()
        assert not Path(temporary).exists()
        area._remove_book_ids_from_library.assert_called_once_with([1])


def test_retired_source_release_matches_hardlink_alias(nested: Any) -> None:
    alias = nested.outer.with_name('alias.cbz')
    os.link(str(nested.outer), str(alias))
    old, temporary = _start(nested)
    _switch(nested)
    with _released_soon(nested):
        nested.handler.wait_for_file_release(str(alias))
        assert old._archive_closed and not Path(temporary).exists()
        alias.unlink()


def test_multiple_same_source_retirements_all_release(nested: Any) -> None:
    handler = nested.handler
    first, first_tmp = _start(nested)
    _switch(nested)
    assert handler.open_file(str(nested.outer))
    pump(lambda: len(nested.extracted) >= 2)
    second, second_tmp = handler._extractor, handler._tmp_dir
    _switch(nested)
    assert len(handler._retired_archives) == 2
    nested.release.set()
    handler.wait_for_file_release(str(nested.outer))
    assert first._archive_closed and second._archive_closed
    assert not Path(first_tmp).exists() and not Path(second_tmp).exists()
    assert handler._current_file == str(nested.other)
