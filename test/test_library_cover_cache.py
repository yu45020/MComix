"""Library covers obey count, byte and LRU limits and the library lifetime."""

import threading
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock

import gi
import pytest

gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

from mcomix import callback, constants  # noqa: E402
from mcomix.library import book_area, pixbuf_cache  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402


def cover(width: int = 3, height: int = 4,
          alpha: bool = False) -> GdkPixbuf.Pixbuf:
    result = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, alpha, 8, width, height)
    result.fill(0x123456FF)
    return result


def cached(cache: pixbuf_cache._PixbufCache, count: int) -> list:
    return [key for key in range(count) if cache.exists(key)]


def test_byte_limit_counts_rowstride_padding() -> None:
    source = cover(3, 7)
    size = source.get_rowstride() * 7
    assert size > 3 * 3 * 7
    cache = pixbuf_cache._PixbufCache(10, max_bytes=size * 2)
    for index in range(3):
        cache.add(index, source)
    assert cached(cache, 3) == [1, 2]


def test_subpixbuf_is_stored_as_compact_copy() -> None:
    parent = cover(80, 100)
    sub = parent.new_subpixbuf(7, 9, 3, 4)
    cache = pixbuf_cache._PixbufCache(2, max_bytes=1024)
    cache.add('cover', sub)
    stored = cache.get('cover')
    assert stored.get_rowstride() == 12
    before = bytes(stored.get_pixels())
    parent.fill(0xFF0000FF)
    assert bytes(stored.get_pixels()) == before


def test_get_refreshes_lru_age() -> None:
    cache = pixbuf_cache._PixbufCache(2)
    cache.add('first', cover())
    cache.add('second', cover())
    cache.get('first')
    cache.add('third', cover())
    assert cache.exists('first') and not cache.exists('second')


def test_oversized_cover_replaces_old_entry_without_evicting_others() -> None:
    cache = pixbuf_cache._PixbufCache(2, max_bytes=48)
    cache.add('first', cover(1, 1))
    cache.add('second', cover(1, 1))
    cache.add('first', cover(10, 10))
    assert not cache.exists('first')
    assert cache.exists('second')


def test_invalidate_removes_one_entry_and_invalidate_all_fences_loads() -> None:
    cache = pixbuf_cache._PixbufCache(3)
    generation = cache.generation()
    cache.add('kept', cover())
    cache.add('removed', cover())
    cache.invalidate('removed')
    assert not cache.exists('removed') and cache.exists('kept')
    cache.add('loading', cover(), generation)
    assert cache.exists('loading')
    cache.invalidate_all()
    cache.add('stale', cover(), generation)
    assert not cache.exists('stale') and not cache.exists('kept')
    cache.add('fresh', cover(), cache.generation())
    assert cache.exists('fresh')


class Backend:
    @callback.Callback
    def book_added_to_collection(self, book: Any, collection: Any) -> None:
        pass

    @callback.Callback
    def books_removed(self) -> None:
        pass


@pytest.fixture
def area(monkeypatch: Any) -> Iterator[Any]:
    monkeypatch.setitem(prefs, 'library cover size', 48)
    library = Gtk.Window()
    library.get_status_bar = Gtk.Statusbar
    library.backend = Backend()
    library._window = SimpleNamespace(
        enhancer=SimpleNamespace(enhance=lambda pixbuf: pixbuf))
    cache = pixbuf_cache._PixbufCache(2)
    monkeypatch.setattr(book_area, 'get_pixbuf_cache', lambda: cache)
    book = SimpleNamespace(path='cover', pages=2,
                           get_last_read_page=lambda: 1)
    library.backend.get_book_by_id = lambda uid: book
    library.backend.get_book_thumbnail = lambda path: cover()
    result = book_area._BookArea(library)
    yield result
    result.destroy()
    library.destroy()


def test_sort_and_cover_size_menus_store_their_values(
        area: Any, monkeypatch: Any) -> None:
    monkeypatch.setitem(prefs, 'lib sort key', prefs['lib sort key'])
    monkeypatch.setitem(prefs, 'lib sort order', prefs['lib sort order'])
    load_covers = Mock()
    monkeypatch.setattr(area, 'load_covers', load_covers)
    [actions] = area._ui_manager.get_action_groups()
    orders = {'ascending': Gtk.SortType.ASCENDING, 'descending': Gtk.SortType.DESCENDING}
    for order, sort_type in orders.items():
        for key, value in [('by name', constants.SORT_NAME), ('by path', constants.SORT_PATH),
                           ('by size', constants.SORT_SIZE),
                           ('by date added', constants.SORT_LAST_MODIFIED)]:
            actions.get_action(order).activate()
            actions.get_action(key).activate()
            assert prefs['lib sort key'] == value
            assert prefs['lib sort order'] == (constants.SORT_ASCENDING if order == 'ascending'
                                               else constants.SORT_DESCENDING)
            assert tuple(area._liststore.get_sort_column_id()) == (value, sort_type)
    for name, size in [('huge', constants.SIZE_HUGE), ('large', constants.SIZE_LARGE),
                       ('normal', constants.SIZE_NORMAL), ('small', constants.SIZE_SMALL),
                       ('tiny', constants.SIZE_TINY)]:
        load_covers.reset_mock()
        actions.get_action(name).activate()
        assert prefs['library cover size'] == size
        load_covers.assert_called_once_with()


def test_bookarea_close_clears_cache(area: Any) -> None:
    area._get_pixbuf(1)
    assert area._cache.exists('cover')
    area.close()
    assert not area._cache.exists('cover')


def test_size_invalidation_cannot_be_repopulated_by_old_worker(
        area: Any, monkeypatch: Any) -> None:
    entered = threading.Event()
    release = threading.Event()

    def decode(path: str) -> GdkPixbuf.Pixbuf:
        entered.set()
        assert release.wait(5)
        return cover()

    area._library.backend.get_book_thumbnail = decode
    worker = threading.Thread(target=area._get_pixbuf, args=(1,))
    worker.start()
    assert entered.wait(5)
    monkeypatch.setitem(prefs, 'library cover size', 24)
    area._cache.invalidate_all()
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert area._cache.get('cover') is None
    area._get_pixbuf(1)
    assert area._cache.get('cover').get_height() == 21
