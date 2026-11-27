"""Explicit library import destinations never create pseudo/orphan memberships."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock
import tempfile

import pytest
from gi.repository import GLib, Gtk

from mcomix.library import add_progress_dialog, main_dialog
from ._app_fakes import isolated_library, write_archive


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    result = isolated_library(monkeypatch, tmp_path)
    assert result.add_collection('Destination')
    assert result.add_collection('Selected')
    yield result
    result.close()


@pytest.fixture
def archive(tmp_path: Path) -> str:
    return write_archive(tmp_path / 'book.cbz', ['page.png'])


def memberships(library: Any) -> list:
    return library.execute('select collection, book from Contain order by collection, book').fetchall()


def dialog(library: Any, selected: Any) -> Any:
    return SimpleNamespace(
        _closed=False, backend=library, _window=None,
        collection_area=SimpleNamespace(get_current_collection=lambda: selected),
        set_status_message=Mock())


@pytest.mark.parametrize('selected', [-1])
def test_manual_add_uses_selection_without_pseudo_membership(
        library: Any, archive: str, selected: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    target = dialog(library, selected)
    monkeypatch.setattr(add_progress_dialog, '_AddLibraryProgressDialog',
                        lambda owner, window, paths, collection: library.add_book(paths[0], collection))
    main_dialog._LibraryDialog.add_books(target, [archive])
    assert library.get_book_by_path(archive) is not None
    assert memberships(library) == []


@pytest.mark.parametrize('selected,destination', [(2, None), (-2, -1), (2, -2)])
def test_watch_import_uses_saved_id_not_sidebar_or_translated_name(
        library: Any, archive: str, selected: int, destination: Any,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target = dialog(library, selected)
    target.add_books = lambda *args, **kwargs: main_dialog._LibraryDialog.add_books(target, *args, **kwargs)
    monkeypatch.setattr(add_progress_dialog, '_AddLibraryProgressDialog',
                        lambda owner, window, paths, collection: library.add_book(paths[0], collection))
    entry = SimpleNamespace(collection=SimpleNamespace(id=destination, name='outdated name'), directory='watched')
    main_dialog._LibraryDialog._new_files_found(target, [archive], entry)
    expected = [] if destination in (None, -1) else [(destination, 1)]
    assert memberships(library) == expected
    assert library.get_collection_by_name('outdated name') is None


@pytest.mark.parametrize('destination', [-1, 999])
def test_direct_backend_rejects_nonexistent_memberships(
        library: Any, archive: str, destination: Any) -> None:
    assert library.add_book(archive, destination)
    assert memberships(library) == []
    library.add_book_to_collection(1, destination)
    assert memberships(library) == []


def test_deleted_destination_from_scan_snapshot_stays_deleted(
        library: Any, archive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    target = dialog(library, 2)
    target.add_books = lambda *args, **kwargs: main_dialog._LibraryDialog.add_books(target, *args, **kwargs)
    destination = library.get_collection_by_id(1)
    library.remove_collection(1)
    monkeypatch.setattr(add_progress_dialog, '_AddLibraryProgressDialog',
                        lambda owner, window, paths, collection: library.add_book(paths[0], collection))
    entry = SimpleNamespace(collection=destination, directory='watched')
    main_dialog._LibraryDialog._new_files_found(target, [archive], entry)
    assert library.get_collection_by_id(1) is None
    assert memberships(library) == []
    assert library.get_book_by_path(archive) is not None


@pytest.mark.parametrize('close', [False, True])
def test_book_added_subscriber_can_delete_destination_or_close_database(
        library: Any, archive: str, close: bool) -> None:
    library.book_added += lambda book: library.close() if close else library.remove_collection(1)
    assert library.add_book(archive, 1)
    if close:
        assert library._con is None
        assert not library.add_book(archive, 1)
        library.add_book_to_collection(1, 1)
        assert library._con is None
    else:
        assert memberships(library) == []


def test_membership_requires_actual_recent_row_and_live_book(library: Any, archive: str) -> None:
    assert library.add_book(archive)
    library.add_book_to_collection(999, 1)
    library.remove_collection(-2)
    assert library.get_collection_by_id(-2) is not None  # Synthetic UI object is not validation.
    library.add_book_to_collection(1, -2)
    assert memberships(library) == []


def test_all_books_notification_occurs_once_for_new_book(
        library: Any, archive: str) -> None:
    seen = []
    library.book_added_to_collection += lambda book, collection: seen.append((book.id, collection))
    assert library.add_book(archive, -1)
    assert library.add_book(archive, -1)
    assert seen == [(1, None)]
    assert memberships(library) == []
    assert library.add_book(archive, -2)
    assert seen == [(1, None), (1, -2)]


def test_all_notification_close_prevents_membership_write(library: Any, archive: str) -> None:
    library.book_added_to_collection += lambda *args: library.close()
    assert library.add_book(archive, 1)
    assert library._con is None


def test_real_gtk_progress_rechecks_destination_between_books(
        library: Any, archive: str, tmp_path: Path) -> None:
    owner = Gtk.Window()
    owner.backend = library
    owner._closed = False
    second = str(tmp_path / 'second.cbz')
    Path(second).write_bytes(Path(archive).read_bytes())
    GLib.idle_add(lambda: library.remove_collection(1))
    try:
        add_progress_dialog._AddLibraryProgressDialog(owner, owner, [archive, second], 1)
        assert library.get_book_by_path(archive) is not None
        assert library.get_book_by_path(second) is not None
        assert memberships(library) == []
    finally:
        owner.destroy()


def test_book_added_subscriber_can_remove_book_before_membership(
        library: Any, archive: str) -> None:
    seen = []
    library.book_added += lambda book: library.remove_book(book.id)
    library.book_added_to_collection += lambda *args: seen.append(args)
    assert library.add_book(archive, 1)
    assert library.get_book_by_path(archive) is None
    assert memberships(library) == []
    assert seen == []


def test_closed_backend_rejects_import_without_reopening(
        library: Any, archive: str) -> None:
    target = dialog(library, 1)
    library.close()
    main_dialog._LibraryDialog.add_books(target, [archive])
    assert library._con is None


@pytest.mark.parametrize('phase', ['book_added', 'book_added_to_collection'])
def test_reentrant_import_cannot_reuse_original_book_id_for_membership(
        library: Any, archive: str, tmp_path: Path, phase: str) -> None:
    replacement = str(tmp_path / 'replacement.cbz')
    Path(replacement).write_bytes(Path(archive).read_bytes())
    published = []

    def replace(book: Any, *args: Any) -> None:
        if book.path == archive:
            library.remove_book(book.id)
            assert library.add_book(replacement)

    if phase == 'book_added':
        library.book_added += replace
    else:
        library.book_added_to_collection += replace
    library.book_added_to_collection += lambda book, collection: published.append((book.path, collection))
    assert library.add_book(archive, 1)
    assert library.get_book_by_path(archive) is None
    assert library.get_book_by_path(replacement).id == 1  # SQLite recycles the deleted row ID.
    assert memberships(library) == []
    if phase == 'book_added':
        assert published == [(replacement, None)]
