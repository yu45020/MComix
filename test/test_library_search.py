"""Library queries and live publications share case-insensitive LIKE semantics."""

from pathlib import Path
import random
import sqlite3
from types import SimpleNamespace
from typing import Any, Iterator

import gi
import pytest

gi.require_version('Gtk', '3.0')
from mcomix.library import backend_types, book_area  # noqa: E402
from ._app_fakes import isolated_library  # noqa: E402


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    result = isolated_library(monkeypatch, tmp_path)
    result.execute('INSERT INTO collection (id, name) VALUES (1, ?)', ('Parent',))
    result.execute('INSERT INTO collection (id, name, supercollection) VALUES (2, ?, 1)', ('Child',))
    result.execute('INSERT INTO collection (id, name) VALUES (3, ?)', ('Other',))
    values = [
        (1, 'ÉTÉ.cbz', 'C:/été/one.cbz'),
        (2, 'Straße.cbz', 'C:/Elsewhere/two.cbz'),
        (3, 'aßb.cbz', 'C:/秘密/three.cbz'),
        (4, 'aİb.cbz', 'C:/plain/four.cbz'),
        (5, '[literal]100%.cbz', 'C:/plain/five.cbz'),
        (6, 'a_b.cbz', 'C:/other/six.cbz'),
    ]
    for ident, name, path in values:
        result.execute('INSERT INTO book (id, name, path, pages, size) VALUES (?, ?, ?, 1, 1)',
                       (ident, name, path))
        result.execute('INSERT INTO contain (collection, book) VALUES (?, ?)',
                       (1 if ident < 3 else 2 if ident < 6 else 3, ident))
    yield result
    result.close()


@pytest.mark.parametrize('query, expected', [
    ('ÉTÉ', {1}), ('STRAßE', {2}), ('秘密', {3}), ('elseWHERE', {2}),
    ('a_b', {3, 4, 6}), ('a%b', {2, 3, 4, 5, 6}), ('_', {1, 2, 3, 4, 5, 6}),
    ('[literal]', {5}), ('100%', {5}), ('AßB', {3}), ('', {1, 2, 3, 4, 5, 6}),
    (None, {1, 2, 3, 4, 5, 6}),
])
def test_all_queries_and_live_additions_agree(
        library: Any, query: str, expected: set) -> None:
    assert {book.id for book in backend_types.DefaultCollection.get_books(query)} == expected
    parent = library.get_collection_by_id(1)
    assert {book.id for book in parent.get_books(query)} == expected - {6}
    for current, allowed in [(-1, expected), (1, expected - {6}),
                             (2, expected & {3, 4, 5})]:
        added = []
        area = SimpleNamespace(
            _library=SimpleNamespace(
                backend=library, filter_string=query,
                collection_area=SimpleNamespace(get_current_collection=lambda: current)),
            is_book_displayed=lambda book: book.id in added,
            add_books=lambda books: added.extend(book.id for book in books))
        for book in backend_types.DefaultCollection.get_books():
            coll = 1 if book.id < 3 else 2 if book.id < 6 else 3
            book_area._BookArea._new_book_added(area, book, coll)
            book_area._BookArea._new_book_added(area, book, coll)
        assert set(added) == allowed
        assert len(added) == len(allowed)


def test_collection_books_include_subcollections(library: Any) -> None:
    assert set(library.get_books_in_collection(1)) == {1, 2, 3, 4, 5}
    assert set(library.get_books_in_collection(2)) == {3, 4, 5}


@pytest.mark.parametrize('value, pattern', [
    ('a\nb', 'a_b'), ('[abc]', '[a]'), ('a\\b', '\\'), ('abc', 'a.c'),
])
def test_wildcard_width_and_literal_syntax_match_sqlite(value: str, pattern: str) -> None:
    connection = sqlite3.connect(':memory:')
    try:
        expected = connection.execute('SELECT ? LIKE ?', (value, '%' + pattern + '%')).fetchone()[0]
    finally:
        connection.close()
    assert backend_types.matches_filter(value, '', pattern) == bool(expected)


def test_random_wildcard_patterns_match_sqlite() -> None:
    generator = random.Random(20261020)
    connection = sqlite3.connect(':memory:')
    try:
        for _ in range(2000):
            value = ''.join(generator.choice('abAB') for _ in range(generator.randint(0, 8)))
            pattern = ''.join(generator.choice('ab%_') for _ in range(generator.randint(1, 6)))
            expected = connection.execute(
                'SELECT ? LIKE ?', (value, '%' + pattern + '%')).fetchone()[0]
            assert backend_types.matches_filter(value, None, pattern) == bool(expected), \
                (value, pattern)
    finally:
        connection.close()


@pytest.mark.parametrize('query', ['%' * 12 + 'q', 'a%' * 12 + 'q', '%a' * 12 + '%q',
                                   '_%' * 12 + 'q'])
def test_repeated_wildcards_without_final_literal_finish_quickly(
        library: Any, query: str) -> None:
    # A backtracking regex took over a minute for one of these values; if it
    # comes back, this test hangs until check.py's pytest limit stops the run.
    long_path = 'C:/Comics/Series ' + 'a' * 2000 + '.cbr'
    library.execute('INSERT INTO book (id, name, path, pages, size) VALUES (7, ?, ?, 1, 1)',
                    ('a' * 2000, long_path))
    assert not backend_types.matches_filter('a' * 2000, long_path, query)
    assert backend_types.DefaultCollection.get_books(query) == []
    assert backend_types.matches_filter(long_path[:-1] + 'q', None, query)


def test_nullable_database_fields_skip_null_and_match_the_other_field(library: Any) -> None:
    for ident, name, path in [(7, None, 'C:/ÉTÉ/path.cbz'),
                              (8, 'ÉTÉ name', None), (9, None, None)]:
        library.execute('INSERT INTO book (id, name, path, pages, size) VALUES (?, ?, ?, 1, 1)',
                        (ident, name, path))
        library.execute('INSERT INTO contain (collection, book) VALUES (1, ?)', (ident,))
    assert {book.id for book in backend_types.DefaultCollection.get_books('été')} == {1, 7, 8}
    assert {book.id for book in library.get_collection_by_id(1).get_books('été')} == {1, 7, 8}
    assert 9 not in {book.id for book in backend_types.DefaultCollection.get_books('%')}
    assert 9 in {book.id for book in backend_types.DefaultCollection.get_books()}
    added = []
    target = SimpleNamespace(
        _library=SimpleNamespace(filter_string='été', collection_area=SimpleNamespace(
            get_current_collection=lambda: -1)),
        is_book_displayed=lambda book: False,
        add_books=lambda books: added.extend(book.id for book in books))
    for ident in (7, 8, 9):
        book_area._BookArea._new_book_added(target, library.get_book_by_id(ident), 1)
    assert added == [7, 8]
