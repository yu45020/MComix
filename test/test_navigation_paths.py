"""Native path identity, directory boundaries and real adjacent-book navigation."""

import ntpath
import os
from pathlib import Path
import posixpath
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock

import pytest

from mcomix import constants, file_provider
from mcomix.preferences import prefs
from ._app_fakes import PNG, fake_app, loaded, write_archive
from ._shells import file_handler_shell


@pytest.fixture(autouse=True)
def sorting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(prefs, 'sort by', constants.SORT_NAME)
    monkeypatch.setitem(prefs, 'sort order', constants.SORT_ASCENDING)


@pytest.mark.parametrize('module,paths,path,expected', [
    (ntpath, ['D:/Comics/A/Book.cbz'], 'd:\\comics\\a\\.\\book.CBZ', 0),
    (ntpath, ['D:/Comics/A/Book.cbz'], 'D:/Comics/Other/Book.cbz', None),
    (ntpath, ['//server/share/Vol1'], '\\\\SERVER\\SHARE\\vol1', 0),
    (posixpath, ['/Comics/Book.cbz'], '/Comics/book.cbz', None),
    (posixpath, ['/Comics/Book.cbz'], '/Comics/Other/../Book.cbz', 0),
])
def test_path_comparison_preserves_platform_rules(
        monkeypatch: pytest.MonkeyPatch, module: Any, paths: list,
        path: str, expected: Any) -> None:
    monkeypatch.setattr(file_provider, 'os', SimpleNamespace(path=module))
    assert file_provider.find_path(paths, path) == expected


@pytest.mark.parametrize('root,module', [('D:\\', ntpath), ('\\\\server\\share\\', ntpath),
                                         ('/', posixpath)])
def test_platform_roots_do_not_enumerate_children_as_siblings(
        monkeypatch: pytest.MonkeyPatch, root: str, module: Any) -> None:
    provider = file_provider.OrderedFileProvider.__new__(file_provider.OrderedFileProvider)
    provider.base_dir = root
    listing = Mock(side_effect=AssertionError('must not enumerate root'))
    monkeypatch.setattr(file_provider, 'os', SimpleNamespace(path=module, listdir=listing))
    assert provider.next_directory() is False
    assert provider.previous_directory() is False
    listing.assert_not_called()


@pytest.mark.parametrize('change', ['renamed', 'parent_deleted'])
def test_missing_or_inaccessible_directory_is_a_noop(tmp_path: Path, change: str) -> None:
    parent = tmp_path / 'Parent'
    current = parent / 'Vol 2'
    current.mkdir(parents=True)
    provider = file_provider.OrderedFileProvider(str(current))
    if change == 'renamed':
        current.rename(parent / 'Changed')
    else:
        current.rmdir()
        parent.rmdir()
    for method in ('next_directory', 'previous_directory'):
        assert getattr(provider, method)() is False
        assert provider.get_directory() == str(current)


def test_real_sibling_navigation_is_natural_and_bounded(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ['Vol 1', 'Vol 1²', 'Vol 2', 'Vol 10']]
    for path in paths:
        path.mkdir()
    provider = file_provider.OrderedFileProvider(str(paths[0]))
    assert provider.previous_directory() is False
    for path in paths[1:]:
        assert provider.next_directory() is True
        assert provider.get_directory() == str(path)
    assert provider.next_directory() is False
    for path in reversed(paths[:-1]):
        assert provider.previous_directory() is True
        assert provider.get_directory() == str(path)
    assert provider.previous_directory() is False


@pytest.mark.parametrize('sort_order,expected', [
    (constants.SORT_ASCENDING,
     ['01.png', '02/01.png', '02/03/02.png', '02/10.png', '03.png', '10.png']),
    (constants.SORT_DESCENDING,
     ['10.png', '03.png', '02/10.png', '02/03/02.png', '02/01.png', '01.png']),
])
def test_image_listing_walks_nested_directories_depth_first(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sort_order: int, expected: list) -> None:
    monkeypatch.setitem(prefs, 'sort order', sort_order)
    (tmp_path / '01.png').touch()
    (tmp_path / '02' / '03').mkdir(parents=True)
    (tmp_path / '02' / '01.png').touch()
    (tmp_path / '02' / '03' / '02.png').touch()
    (tmp_path / '02' / '10.png').touch()
    (tmp_path / '03.png').touch()
    (tmp_path / '10.png').touch()

    provider = file_provider.OrderedFileProvider(str(tmp_path))

    assert provider.list_files() == [
        str(tmp_path.joinpath(*name.split('/'))) for name in expected]


def test_archive_listing_does_not_descend_into_directories(tmp_path: Path) -> None:
    (tmp_path / 'outer.cbz').touch()
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / 'inner.cbz').touch()

    provider = file_provider.OrderedFileProvider(str(tmp_path))

    assert provider.list_files(file_provider.FileProvider.ARCHIVES) == [
        str(tmp_path / 'outer.cbz'),
    ]


@pytest.mark.skipif(os.name != 'nt', reason='real native Windows case aliases')
def test_real_windows_different_case_and_dot_segments(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ['Book 1', 'Book 2', 'Book 10']]
    for path in paths:
        path.mkdir()
    alias = os.path.join(str(paths[1]).swapcase(), '.', '')
    provider = file_provider.OrderedFileProvider(alias)
    assert provider.next_directory() is True
    assert os.path.normcase(provider.get_directory()) == os.path.normcase(str(paths[2]))
    provider.set_directory(alias)
    assert provider.previous_directory() is True
    assert os.path.normcase(provider.get_directory()) == os.path.normcase(str(paths[0]))


@pytest.fixture
def books(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setitem(prefs, 'lazy archive reading', True)
    app = fake_app(monkeypatch, tmp_path)
    comics = tmp_path / 'Comics'
    app.paths = []
    for name in ['Vol 1', 'Vol 1²', 'Vol 2', 'Vol 10']:
        directory = comics / name
        directory.mkdir(parents=True)
        app.paths.append([write_archive(directory / filename, ['00.png', '01.png', '02.png'])
                          for filename in ['Book 1.cbz', 'Book 2.cbz', 'Book 10.cbz']])
    yield app
    app.close()


@pytest.mark.parametrize('first,mixed_case', [(False, False), (True, True)])
def test_real_adjacent_archives_keep_order_and_previous_page_policy(
        books: Any, monkeypatch: pytest.MonkeyPatch, first: bool,
        mixed_case: bool) -> None:
    if mixed_case and os.name != 'nt':
        pytest.skip('real native Windows case aliases')
    handler = books.handler
    path = books.paths[0][1]
    if mixed_case:
        path = path.swapcase()
    monkeypatch.setitem(prefs, 'open first file in prev archive', first)
    assert handler.open_file(path, start_page=1)
    loaded(handler)
    assert handler.get_file_number() == (2, 3)
    assert handler._open_next_archive() is True
    loaded(handler)
    assert os.path.normcase(handler._current_file) == os.path.normcase(books.paths[0][2])
    assert books.window.pages[-1] == 1
    assert handler._open_next_archive() is False
    assert handler._open_previous_archive() is True
    loaded(handler)
    assert os.path.normcase(handler._current_file) == os.path.normcase(books.paths[0][1])
    assert books.window.pages[-1] == (1 if first else 3)
    assert handler.read_file(books.images._image_files[0]) == PNG


@pytest.mark.parametrize('first_directory,first_archive', [(False, False), (True, False),
                                                           (False, True)])
def test_real_sibling_archives_preserve_previous_directory_and_page_policies(
        books: Any, monkeypatch: pytest.MonkeyPatch, first_directory: bool,
        first_archive: bool) -> None:
    handler = books.handler
    monkeypatch.setitem(prefs, 'open first file in prev directory', first_directory)
    monkeypatch.setitem(prefs, 'open first file in prev archive', first_archive)
    assert handler.open_file(books.paths[0][1], start_page=1)
    loaded(handler)
    assert handler.open_previous_directory() is False
    assert handler.open_next_directory() is True
    loaded(handler)
    assert handler._current_file == books.paths[1][0]
    assert books.window.pages[-1] == 1
    assert handler.open_previous_directory() is True
    loaded(handler)
    assert handler._current_file == books.paths[0][0 if first_directory else -1]
    assert books.window.pages[-1] == (1 if first_archive or first_directory else 3)


@pytest.mark.parametrize('method', ['_open_next_archive', '_open_previous_archive'])
def test_missing_archive_noop_is_false_not_an_exception(method: str) -> None:
    handler = file_handler_shell()
    handler._file_provider = SimpleNamespace(list_files=lambda mode: ['present.cbz'])
    handler.archive_type = constants.ZIP
    handler._base_path = 'deleted.cbz'
    handler.open_file = Mock(side_effect=AssertionError('no open on missing book'))
    assert getattr(handler, method)() is False


def test_glib_archive_order_keeps_subfolders_together(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(prefs, 'sort archive by', constants.SORT_NAME_GLIB)
    monkeypatch.setitem(prefs, 'sort archive order', constants.SORT_ASCENDING)
    app = fake_app(monkeypatch, tmp_path)
    try:
        path = write_archive(tmp_path / 'chapters.cbz', ['ch2/01.png', 'ch1/02.png', 'ch1/01.png'])
        assert app.handler.open_file(path)
        loaded(app.handler)
        assert [os.path.relpath(image, app.handler._tmp_dir) for image in app.images._image_files] \
            == [os.path.join('ch1', '01.png'), os.path.join('ch1', '02.png'),
                os.path.join('ch2', '01.png')]
    finally:
        app.close()


@pytest.mark.skipif(os.name != 'nt', reason='real native Windows case aliases')
def test_image_typed_in_another_case_opens_at_that_image(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(1, 4):
        (tmp_path / ('%02d.png' % index)).write_bytes(PNG)
    app = fake_app(monkeypatch, tmp_path)
    try:
        assert app.handler.open_file(str(tmp_path / '03.PNG'))
        loaded(app.handler)
        assert app.window.pages == [3]
    finally:
        app.close()


def test_folder_given_first_lists_only_images(tmp_path: Path) -> None:
    folder = tmp_path / 'Folder'
    folder.mkdir()
    (folder / '01.png').write_bytes(PNG)
    book = write_archive(tmp_path / 'book.cbz', ['00.png'])
    provider = file_provider.get_file_provider([str(folder), book])
    assert provider.list_files() == [str(folder / '01.png')]
    assert provider.get_directory() == str(folder)
