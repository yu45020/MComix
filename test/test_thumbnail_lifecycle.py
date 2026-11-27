"""Real GTK thumbnail rows and worker/main-thread ownership regressions."""

import queue
import threading
from types import SimpleNamespace
from typing import Any, Iterator

import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

from mcomix import callback, thumbnail_view  # noqa: E402
from mcomix.library import book_area, pixbuf_cache  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402
from ._app_fakes import pump  # noqa: E402


def _pixbuf(color: int, width: int = 8, height: int = 8) -> GdkPixbuf.Pixbuf:
    result = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8,
                                  width, height)
    result.fill(color)
    return result


@pytest.fixture(params=[thumbnail_view.ThumbnailIconView,
                        thumbnail_view.ThumbnailTreeView])
def pending(request: Any, monkeypatch: Any) -> Iterator[Any]:
    monkeypatch.setitem(prefs, 'max threads', 1)
    model = Gtk.ListStore(str, GdkPixbuf.Pixbuf, bool)
    model.append(['target', None, False])
    model.append(['other', None, False])
    view = request.param(model, 0, 1, 2)
    view.get_visible_range = lambda: ((0,), (0,))
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    idles = queue.Queue()
    pixbuf = _pixbuf(0xFF0000FF)
    workers = []

    def decode(uid: str) -> GdkPixbuf.Pixbuf:
        workers.append(threading.current_thread())
        entered.set()
        assert release.wait(5)
        return pixbuf

    def idle_add(function: Any, *args: Any) -> int:
        idles.put((function, args))
        completed.set()
        return 1

    view.generate_thumbnail = decode
    monkeypatch.setattr(thumbnail_view.GLib, 'idle_add', idle_add)
    view.draw_thumbnails_on_screen()
    assert entered.wait(5)
    job = SimpleNamespace(model=model, view=view, release=release,
                          completed=completed, entered=entered, idles=idles, pixbuf=pixbuf,
                          workers=workers)
    yield job
    release.set()
    view.destroy()


def _finish(job: Any) -> None:
    job.release.set()
    assert job.completed.wait(5)
    function, args = job.idles.get(timeout=5)
    assert function(*args) == 0


@pytest.mark.parametrize('mutation', ['remove', 'uid', 'model'])
def test_removed_or_replaced_rows_drop_pending_thumbnail(
        pending: Any, mutation: str) -> None:
    job = pending
    job.release.set()
    assert job.completed.wait(5)
    if mutation == 'remove':
        job.model.remove(job.model.get_iter(0))
        job.model.append(['target', None, False])
    elif mutation == 'uid':
        job.model.set_value(job.model.get_iter(0), 0, 'replacement')
    else:
        replacement = Gtk.ListStore(str, GdkPixbuf.Pixbuf, bool)
        replacement.append(['target', None, False])
        job.view.set_model(replacement)
        # Detaching and restoring the same model must also fence old work.
        job.view.set_model(job.model)
        assert not replacement[0][2]
    _finish(job)
    assert all(row[1] is None and not row[2] for row in job.model)


def test_reordered_row_receives_its_own_thumbnail(pending: Any) -> None:
    job = pending
    job.release.set()
    assert job.completed.wait(5)
    job.model.reorder([1, 0])
    _finish(job)
    assert job.model[0][:] == ['other', None, False]
    assert job.model[1][0] == 'target'
    assert job.model[1][1] is job.pixbuf
    assert job.model[1][2]
    assert job.workers == [job.workers[0]]


def test_stop_restart_discards_queued_idle(pending: Any) -> None:
    job = pending
    job.release.set()
    assert job.completed.wait(5)
    job.view.stop_update()
    job.model.clear()
    job.model.append(['target', None, False])
    job.view.draw_thumbnails_on_screen()
    _finish(job)
    assert job.model[0][1] is None
    function, args = job.idles.get(timeout=5)
    assert function(*args) == 0
    assert job.model[0][1] is job.pixbuf
    assert job.model[0][2]


@pytest.mark.parametrize('phase', ['decoding', 'idle'])
def test_destroy_stops_workers_and_rejects_idle(
        pending: Any, phase: str) -> None:
    job = pending
    if phase == 'idle':
        job.release.set()
        assert job.completed.wait(5)
    else:
        # destroy() joins the decode; release it from a separate thread.
        threading.Timer(0.03, job.release.set).start()
    job.view.destroy()
    _finish(job)
    job.view.draw_thumbnails_on_screen()
    assert not job.workers[0].is_alive()
    assert job.model[0][1] is None
    assert not job.model[0][2]


def test_real_idle_model_access_stays_on_main_thread(monkeypatch: Any) -> None:
    model = Gtk.ListStore(str, GdkPixbuf.Pixbuf, bool)
    model.append(['target', None, False])
    view = thumbnail_view.ThumbnailTreeView(model, 0, 1, 2)
    view.get_visible_range = lambda: ((0,), (0,))
    accesses = []
    main_ident = threading.get_ident()
    for obj, name in [(view, 'get_model'), (model, 'get_iter'),
                      (model, 'get_path'), (model, 'get_value'),
                      (model, 'set')]:
        original = getattr(obj, name)

        def watched(*args: Any, _original: Any = original) -> Any:
            accesses.append(threading.get_ident())
            return _original(*args)

        monkeypatch.setattr(obj, name, watched)
    decode_ids = []

    def decode(uid: str) -> GdkPixbuf.Pixbuf:
        decode_ids.append(threading.get_ident())
        return _pixbuf(0xFF0000FF)

    view.generate_thumbnail = decode
    try:
        view.draw_thumbnails_on_screen()
        pump(lambda: model[0][2])
        assert accesses and set(accesses) == {main_ident}
        assert decode_ids and main_ident not in decode_ids
    finally:
        view.destroy()


class _Backend:
    @callback.Callback
    def book_added_to_collection(self, book: Any, collection: Any) -> None:
        pass

    @callback.Callback
    def books_removed(self) -> None:
        pass


@pytest.mark.parametrize('finished,size,missing_icon', [
    (True, 96, False), (False, 96, False), (True, 49, False), (True, 96, True)])
def test_finished_icon_is_created_on_main_thread_and_cache_is_unchanged(
        monkeypatch: Any, finished: bool, size: int, missing_icon: bool) -> None:
    monkeypatch.setitem(prefs, 'library cover size', size)
    library = Gtk.Window()
    library._statusbar = Gtk.Statusbar()
    library.get_status_bar = lambda: library._statusbar
    library.backend = _Backend()
    library._window = SimpleNamespace(
        enhancer=SimpleNamespace(enhance=lambda pixbuf: pixbuf))
    cover = _pixbuf(0x0000FFFF, 64, 90)
    icon = _pixbuf(0xFF0000FF, 2, 2)
    book = SimpleNamespace(path='cover', pages=2,
                           get_last_read_page=lambda: 2 if finished else 1)
    library.backend.get_book_by_id = lambda uid: book
    cache = pixbuf_cache._PixbufCache(2)
    cache.add(book.path, cover)
    cached_cover = cache.get(book.path)
    monkeypatch.setattr(book_area, 'get_pixbuf_cache', lambda: cache)
    icon_threads = []

    def render_icon(self: Any, stock: Any, icon_size: Any) -> Any:
        icon_threads.append(threading.get_ident())
        return None if missing_icon else icon

    monkeypatch.setattr(book_area._BookArea, 'render_icon', render_icon)
    area = book_area._BookArea(library)
    cached_bytes = bytes(cover.get_pixels())
    icon_bytes = bytes(icon.get_pixels())
    results = []
    errors = []

    def render() -> None:
        try:
            results.append(area._get_pixbuf(1))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=render)
    thread.start()
    thread.join(5)
    try:
        assert not thread.is_alive()
        assert errors == []
        assert icon_threads == [threading.get_ident()]
        result = results[0]
        offset = (result.get_height() - 2) * result.get_rowstride()
        offset += (result.get_width() - 2) * result.get_n_channels()
        rgb = result.get_pixels()[offset:offset + 3]
        assert rgb == (b'\xff\x00\x00'
                       if finished and size >= 50 and not missing_icon else b'\x00\x00\xff')
        assert bytes(cover.get_pixels()) == cached_bytes
        assert bytes(icon.get_pixels()) == icon_bytes
        assert cache.get(book.path) is cached_cover
        assert bytes(cached_cover.get_pixels()) == cached_bytes
        library.backend.get_book_by_id = lambda uid: None
        assert area._get_pixbuf(1) is None
    finally:
        area.destroy()
        library.destroy()


def test_scrolling_past_slow_decodes_keeps_row_bookkeeping_bounded(
        monkeypatch: Any) -> None:
    monkeypatch.setitem(prefs, 'max threads', 1)
    model = Gtk.ListStore(str, GdkPixbuf.Pixbuf, bool)
    for index in range(100):
        model.append(['row%d' % index, None, False])
    view = thumbnail_view.ThumbnailIconView(model, 0, 1, 2)
    visible = [((0,), (2,))]
    view.get_visible_range = lambda: visible[0]
    entered = threading.Event()
    release = threading.Event()
    idles = queue.Queue()
    pixbuf = _pixbuf(0x00FF00FF)

    def decode(uid: str) -> GdkPixbuf.Pixbuf:
        entered.set()
        assert release.wait(5)
        if uid == 'row1':
            raise RuntimeError('undecodable')
        return pixbuf

    view.generate_thumbnail = decode
    monkeypatch.setattr(thumbnail_view.GLib, 'idle_add',
                        lambda function, *args: idles.put((function, args)) or 1)
    try:
        view.draw_thumbnails_on_screen()
        assert entered.wait(5)
        # The first decode is stuck; scrolling discards every queued order.
        for start in range(10, 90, 4):
            visible[0] = ((start,), (start + 2,))
            view.draw_thumbnails_on_screen()
            assert len(view._thumbnail_rows) <= 5
        visible[0] = ((0,), (2,))
        view.draw_thumbnails_on_screen()
        release.set()
        # Returning to the stuck row may decode it once more; that is the
        # accepted cost of not retaining bookkeeping for discarded orders.
        while not (all(model[index][2] for index in (0, 2, 3))
                   and not view._thumbnail_rows):
            function, args = idles.get(timeout=5)
            function(*args)
        # Successful rows render; the failing row still retires its entry.
        assert [model[index][2] for index in range(4)] == [True, False, True, True]
        assert model[0][1] is pixbuf
        assert view._thumbnail_rows == {}
    finally:
        release.set()
        view.destroy()


def test_replacement_draw_before_old_worker_releases_unique_order(
        pending: Any, monkeypatch: Any) -> None:
    job = pending
    posted = threading.Event()
    unblock = threading.Event()
    original = thumbnail_view.GLib.idle_add
    first = [True]

    def gated_idle(function: Any, *args: Any) -> int:
        result = original(function, *args)
        if first[0]:
            first[0] = False
            posted.set()
            assert unblock.wait(5)
        return result

    monkeypatch.setattr(thumbnail_view.GLib, 'idle_add', gated_idle)
    job.model.remove(job.model.get_iter(0))
    job.model.prepend(['target', None, False])
    job.release.set()
    assert posted.wait(5)
    # Force exactly the dangerous ordering: completion and redraw before the
    # worker removes the old order's unique key.
    job.view.queue_draw = job.view.draw_thumbnails_on_screen
    try:
        function, args = job.idles.get(timeout=5)
        function(*args)
    finally:
        unblock.set()
    function, args = job.idles.get(timeout=5)
    function(*args)
    assert job.model[0][1] is job.pixbuf
    assert job.model[0][2]
