"""Library collection trees, book records and watched-folder scans."""

import io
import logging
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
from typing import Any, Iterator

import pytest

from mcomix import constants, last_read_page
from mcomix.library import backend, backend_types
from ._app_fakes import isolated_library, pump

ARCHIVES = Path('test/files/archives')


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    result = isolated_library(monkeypatch, tmp_path)
    result.add_collection('Test')
    result.add_collection('Subtest')
    result.add_collection_to_collection(result.get_collection_by_name('Subtest').id,
                                        result.get_collection_by_name('Test').id)
    yield result
    result.close()


def test_database_at_another_version_opens_unchanged(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    # The installed MComix 1.2.1 shares the library at version 6, where the
    # Recent collection still has its localized name.
    isolated_library(monkeypatch, tmp_path).close()
    with sqlite3.connect(constants.LIBRARY_DATABASE_PATH) as con:
        con.execute("update info set value = '6' where key = 'version'")
        con.execute("update collection set name = 'Récents' where id = ?",
                    (backend.COLLECTION_RECENT,))
        before = con.execute('select sql from sqlite_master order by name').fetchall()
    con.close()

    with caplog.at_level(logging.WARNING, logger='mcomix'):
        library = backend._LibraryBackend()
    library.close()

    con = sqlite3.connect(constants.LIBRARY_DATABASE_PATH)
    try:
        assert con.execute('select sql from sqlite_master order by name').fetchall() == before
        assert con.execute("select value from info where key = 'version'").fetchall() == [('6',)]
        assert con.execute('select name from collection where id = ?',
                           (backend.COLLECTION_RECENT,)).fetchall() == [('Récents',)]
    finally:
        con.close()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_added_book_records_its_page_count(library: Any) -> None:
    assert library.add_book(str(ARCHIVES / '01-ZIP-Normal.zip'), None)

    [book] = backend_types.DefaultCollection.get_books('zip')
    assert book.pages == 4


def test_collection_tree(library: Any) -> None:
    test_col = library.get_collection_by_name('Test')
    sub_col = library.get_collection_by_name('Subtest')

    assert test_col.supercollection is None
    assert sub_col.supercollection == test_col.id
    # The default 'Recent' collection is the other root.
    assert library.get_collections_in_collection(None)[1] == test_col.id
    assert library.get_collections_in_collection(test_col.id) == [sub_col.id]
    assert library.get_collection_by_id(None) == backend_types.DefaultCollection
    assert library.get_collection_by_name('Test') == test_col

    library.add_collection('New test')
    library.add_collection_to_collection(library.get_collection_by_name('New test').id,
                                         sub_col.id)

    assert len(library.get_all_collections()) == 4
    assert len(library.get_all_collections_in_collection(test_col.id)) == 2


def test_book_in_collection_and_subcollection_is_listed_once(library: Any) -> None:
    assert library.add_book(str(ARCHIVES / '01-ZIP-Normal.zip'),
                            library.get_collection_by_name('Subtest').id)
    [book] = backend_types.DefaultCollection.get_books()
    library.add_book_to_collection(book.id, library.get_collection_by_name('Test').id)

    assert [b.id for b in library.get_collection_by_name('Test').get_books()] == [book.id]


def test_failed_collection_copy_reports_failure(
        library: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, 'add_collection', lambda name: False)
    assert not library.duplicate_collection(library.get_collection_by_name('Test').id)


def test_removed_books_reading_position_does_not_pass_to_a_new_book(
        library: Any, tmp_path: Path) -> None:
    first, second = (str(tmp_path / name) for name in ('first.zip', 'second.zip'))
    for path in (first, second):
        shutil.copy(ARCHIVES / '01-ZIP-Normal.zip', path)
    pages = last_read_page.LastReadPage(library)
    pages.set_enabled(True)
    pages.set_page(first, 3)
    book = library.get_book_by_path(first)

    library.remove_book(book.id)
    assert library.add_book(second)

    assert library.get_book_by_path(second).id == book.id
    assert pages.get_page(second) is None


def test_damaged_book_is_skipped(library: Any, tmp_path: Path) -> None:
    # A bad checksum on the header after a pax header makes the listing raise.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name in ('a.png', 'b' * 120 + '.png'):
            member = tarfile.TarInfo(name)
            member.size = 4
            archive.addfile(member, io.BytesIO(b'page'))
    data = bytearray(buffer.getvalue())
    data[2048 + 148:2048 + 156] = b'0000000\0'
    damaged = tmp_path / 'damaged.cbt'
    damaged.write_bytes(bytes(data))

    assert not library.add_book(str(damaged))
    assert library.add_book(str(ARCHIVES / '01-ZIP-Normal.zip'))
    assert len(backend_types.DefaultCollection.get_books()) == 1


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows paths ignore case')
def test_book_found_by_another_case_is_the_same_book(library: Any, tmp_path: Path) -> None:
    path = tmp_path / 'Watch' / 'Z.cbz'
    path.parent.mkdir()
    shutil.copy(ARCHIVES / '01-ZIP-Normal.zip', path)
    other_case = str(path).lower()
    assert library.add_book(other_case, backend.COLLECTION_RECENT)

    entry = backend_types._WatchListEntry(str(path.parent), False, None)
    assert entry.get_new_files([other_case]) == []
    assert library.add_book(str(path))
    assert len(backend_types.DefaultCollection.get_books()) == 1
    assert library.get_book_by_path(str(path)) is not None


@pytest.mark.parametrize('collection,reports', [('Test', [1, 0]), (None, [0, 0])])
def test_watch_scans_report_a_recent_only_book_until_it_is_filed(
        library: Any, tmp_path: Path, collection: Any, reports: list) -> None:
    path = str(tmp_path / 'read.cbz')
    shutil.copy(ARCHIVES / '01-ZIP-Normal.zip', path)
    assert library.add_book(path, backend.COLLECTION_RECENT)
    destination = (library.get_collection_by_name(collection) if collection
                   else backend_types.DefaultCollection)
    library.watchlist.add_directory(str(tmp_path), destination)
    found = []

    def add_found(paths: list, entry: Any) -> None:
        found.append(len(paths))
        for found_path in paths:
            library.add_book(found_path, entry.collection.id)

    library.watchlist.new_files_found += add_found
    for scan in range(2):
        assert library.watchlist.scan_for_new_files()
        pump(lambda: len(found) > scan)
        library.watchlist.stop_scan()

    assert found == reports


def test_watched_folder_reports_only_unknown_archives(tmp_path: Path) -> None:
    known = [str(tmp_path / name) for name in ('a.zip', 'b.tar')]
    new = [str(tmp_path / name) for name in ('c.cbz', 'd.cbt')]
    for path in known + new:
        shutil.copy(ARCHIVES / '01-ZIP-Normal.zip', path)
    (tmp_path / 'notes.txt').write_text('')

    entry = backend_types._WatchListEntry(str(tmp_path), True, None)

    assert sorted(entry.get_new_files(known)) == new
    missing = backend_types._WatchListEntry(str(tmp_path / 'missing'), False, None)
    assert not missing.is_valid()
    assert missing.get_new_files([]) == []
