"""The last read page of each book, kept in the library database."""

from pathlib import Path
from typing import Any, Iterator

import pytest

from mcomix import last_read_page
from mcomix.library import backend
from ._app_fakes import isolated_library

ARCHIVE1 = str(Path('test/files/archives/01-ZIP-Normal.zip').resolve())
ARCHIVE2 = str(Path('test/files/archives/02-TAR-Normal.tar').resolve())


def _open() -> Any:
    reader = last_read_page.LastReadPage(backend.LibraryBackend())
    reader.set_enabled(True)
    return reader


@pytest.fixture
def lastread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    isolated_library(monkeypatch, tmp_path)
    reader = _open()
    yield reader
    reader.backend.close()


def test_pages_are_kept_per_book_and_survive_a_restart(lastread: Any) -> None:
    assert lastread.count() == 0
    lastread.set_page(ARCHIVE1, 1)
    lastread.set_page(ARCHIVE2, 1)
    lastread.set_page(ARCHIVE2, 2)

    assert lastread.count() == 2
    assert lastread.get_date(ARCHIVE1) is not None

    lastread.backend.close()
    reopened = _open()
    try:
        assert (reopened.get_page(ARCHIVE1), reopened.get_page(ARCHIVE2)) == (1, 2)
    finally:
        reopened.backend.close()


def test_clearing_forgets_pages(lastread: Any) -> None:
    lastread.set_page(ARCHIVE1, 1)
    lastread.set_page(ARCHIVE2, 2)

    lastread.clear_page(ARCHIVE1)
    assert lastread.get_page(ARCHIVE1) is None
    assert lastread.get_page(ARCHIVE2) == 2
    lastread.clear_all()
    assert lastread.count() == 0


def test_disabled_reader_records_nothing(lastread: Any) -> None:
    lastread.set_enabled(False)
    lastread.set_page(ARCHIVE1, 1)

    assert lastread.get_page(ARCHIVE1) is None


@pytest.mark.parametrize('path, page', [
    ('/root/mcomix/file-that-should-not-exist-hopefully', 1),
    (ARCHIVE1, 0),
])
def test_rejects_missing_files_and_pages_below_one(lastread: Any, path: str, page: int) -> None:
    with pytest.raises(ValueError):
        lastread.set_page(path, page)
