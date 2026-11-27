"""Bookmark editor batching with real persistence, GTK models and both menus."""

import datetime
import os
import pickle
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock

import gi
import pytest

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk  # noqa: E402

from mcomix import bookmark_backend, bookmark_dialog, bookmark_menu  # noqa: E402
from mcomix import bookmark_menu_item, constants, message_dialog  # noqa: E402
from ._app_fakes import fake_app, folder_book, loaded  # noqa: E402


class CountingMenu(bookmark_menu.BookmarksMenu):
    def _create_bookmark_menuitems(self) -> None:
        self.rebuilds = getattr(self, 'rebuilds', 0) + 1
        super()._create_bookmark_menuitems()


@pytest.fixture
def editor(monkeypatch: Any, tmp_path: Any) -> Iterator[Any]:
    path = tmp_path / 'bookmarks.pickle'
    monkeypatch.setattr(constants, 'BOOKMARK_PICKLE_PATH', str(path))
    store = new_store()
    monkeypatch.setattr(bookmark_backend, 'BookmarksStore', store)
    window = Gtk.Window()
    window.filehandler = SimpleNamespace()
    window.imagehandler = SimpleNamespace()
    store.initialize(window)
    menus = [CountingMenu(Gtk.UIManager(), window) for _ in range(2)]
    dialogs = []
    state = SimpleNamespace(store=store, path=path, menus=menus, writes=0,
                            errors=[], fail=False)
    writer = bookmark_backend.atomic_write

    def write(*args: Any) -> Any:
        if state.fail:
            raise OSError('disk full')
        state.writes += 1
        return writer(*args)

    monkeypatch.setattr(bookmark_backend, 'atomic_write', write)
    monkeypatch.setattr(message_dialog.MessageDialog, 'run',
                        lambda dialog: state.errors.append(dialog) or dialog.destroy())

    def make(index: int) -> Any:
        return bookmark_menu_item._Bookmark(
            window, window.filehandler, 'Book %03d' % index,
            str(tmp_path / ('book%d.cbz' % index)), 1, 10, constants.ZIP,
            datetime.datetime(2026, 1, 1))

    def seed(count: int) -> list:
        items = [make(index) for index in range(count)]
        # Seed in one write; add_bookmark would rebuild both menus per item.
        store_file(path, items)
        assert store.get_bookmarks() == items
        reset()
        return items

    def reset() -> None:
        state.writes = 0
        for menu in menus:
            menu.rebuilds = 0

    def dialog() -> Any:
        result = bookmark_dialog._BookmarksDialog(window, store)
        dialogs.append(result)
        return result

    state.make, state.seed, state.reset, state.dialog = make, seed, reset, dialog
    yield state
    for item in dialogs + menus:
        item.destroy()
    window.destroy()


def new_store() -> Any:
    return bookmark_backend.__dict__['__BookmarksStore']()


#: Distinct mtimes within one second, so that change detection can't rely on
#: the clock ticking between two saves.
_mtimes = iter(range(1_700_000_000_000_000_000, 1_700_000_000_900_000_000, 100))


def tick(path: Any) -> None:
    """Give the file a new mtime, as a save by another instance would."""
    mtime = next(_mtimes)
    os.utime(path, ns=(mtime, mtime))


def store_file(path: Any, items: list) -> None:
    with path.open('wb') as stream:
        pickle.dump(constants.VERSION, stream)
        pickle.dump([item.pack() for item in items], stream)
    tick(path)


def rebuilds(editor: Any) -> list:
    return [menu.rebuilds for menu in editor.menus]


def persisted(editor: Any) -> list:
    with editor.path.open('rb') as stream:
        assert pickle.load(stream) == constants.VERSION
        return pickle.load(stream)


def test_unchanged_close_does_nothing(editor: Any) -> None:
    items = editor.seed(0)
    editor.dialog()._close()
    assert editor.writes == 0
    assert rebuilds(editor) == [0, 0]
    assert editor.store.get_bookmarks() == items


def test_reorder_300_writes_and_rebuilds_once(editor: Any) -> None:
    items = editor.seed(300)
    dialog = editor.dialog()
    dialog._liststore.reorder(list(reversed(range(300))))
    dialog._close()
    assert editor.writes == 1
    assert rebuilds(editor) == [1, 1]
    assert editor.store.get_bookmarks() == items[::-1]
    assert persisted(editor) == [item.pack() for item in items[::-1]]


def test_window_close_saves_order(editor: Any) -> None:
    items = editor.seed(4)
    dialog = editor.dialog()
    dialog._name_col.clicked()
    dialog.emit('delete-event', Gdk.Event.new(Gdk.EventType.DELETE))
    assert editor.store.get_bookmarks() == items[::-1]
    assert editor.writes == 1


def test_remove_then_close_writes_only_for_remove(editor: Any) -> None:
    items = editor.seed(3)
    dialog = editor.dialog()
    dialog._selection.select_path(0)
    dialog._remove_selected()
    assert editor.store.get_bookmarks() == items[:-1]
    assert editor.writes == 1
    editor.reset()
    dialog._close()
    assert editor.writes == 0
    assert rebuilds(editor) == [0, 0]


def test_untouched_second_editor_does_not_undo_first(editor: Any) -> None:
    items = editor.seed(3)
    first, second = editor.dialog(), editor.dialog()
    first._liststore.reorder([2, 1, 0])
    first._close()
    second._close()
    assert editor.store.get_bookmarks() == items[::-1]
    assert editor.writes == 1


def test_reorder_keeps_bookmarks_added_meanwhile(editor: Any) -> None:
    items = editor.seed(3)
    dialog = editor.dialog()
    added = editor.make(10)
    editor.store.add_bookmark(added)
    dialog._liststore.reorder([2, 1, 0])
    dialog._close()
    assert editor.store.get_bookmarks() == items[::-1] + [added]


def test_reorder_applies_to_another_instances_file(editor: Any) -> None:
    items = editor.seed(3)
    extra = editor.make(10)
    # Another instance removed two bookmarks and added one.
    store_file(editor.path, [items[0], extra])
    editor.store.reorder_bookmarks(items[::-1])
    assert editor.store.get_bookmarks() == [items[0], extra]
    assert persisted(editor) == [items[0].pack(), extra.pack()]


def test_two_instances_keep_each_others_changes(editor: Any) -> None:
    items = editor.seed(2)
    other = new_store()
    assert other.get_bookmarks() == items
    editor.store.remove_bookmark(items[0])
    tick(editor.path)
    added = editor.make(10)
    other.add_bookmark(added)
    tick(editor.path)
    # Both saves fall within one second; each instance still sees the other's.
    later = editor.make(11)
    editor.store.add_bookmark(later)
    tick(editor.path)
    assert persisted(editor) == [item.pack() for item in [items[1], added, later]]
    assert other.get_bookmarks() == [items[1], added, later]
    assert editor.errors == []


def test_failed_save_keeps_stored_order_and_closes(editor: Any) -> None:
    items = editor.seed(3)
    dialog = editor.dialog()
    dialog._liststore.reorder([2, 1, 0])
    before = editor.path.read_bytes()
    editor.fail = True
    dialog._close()
    assert editor.store.get_bookmarks() == items
    assert editor.path.read_bytes() == before
    assert rebuilds(editor) == [1, 1]
    assert len(editor.errors) == 1


def test_failed_add_and_remove_raise_nothing_and_keep_the_file(editor: Any) -> None:
    items = editor.seed(2)
    dialog = editor.dialog()
    before = editor.path.read_bytes()
    editor.fail = True
    editor.store.add_bookmark(editor.make(10))
    dialog._selection.select_path(0)
    dialog._remove_selected()
    assert editor.store.get_bookmarks() == items
    assert len(dialog._liststore) == 2
    assert editor.path.read_bytes() == before
    assert len(editor.errors) == 2
    assert rebuilds(editor) == [2, 2]


def test_failed_replace_shows_one_error_and_adds_nothing(
        editor: Any, monkeypatch: Any) -> None:
    items = editor.seed(1)
    before = editor.path.read_bytes()
    editor.store._image_handler = SimpleNamespace(
        get_pretty_current_filename=lambda: 'Book 000',
        get_number_of_pages=lambda: 10)
    editor.store._file_handler = SimpleNamespace(
        archive_type=constants.ZIP, get_book_position=lambda: (items[0]._path, 5, ''))
    monkeypatch.setattr(editor.store, 'show_replace_bookmark_dialog',
                        lambda *args: Gtk.ResponseType.YES)
    editor.fail = True
    editor.store.add_current_to_bookmarks()
    assert editor.store.get_bookmarks() == items
    assert editor.path.read_bytes() == before
    assert len(editor.errors) == 1


def test_unreadable_file_is_never_replaced(
        editor: Any, monkeypatch: Any) -> None:
    items = editor.seed(2)
    before = editor.path.read_bytes()

    def locked(*args: Any) -> None:
        raise PermissionError('locked')

    with monkeypatch.context() as patch:
        patch.setattr(bookmark_backend, 'open', locked, raising=False)
        starting = new_store()
        starting.add_bookmark(editor.make(10))
        assert editor.path.read_bytes() == before
        assert len(editor.errors) == 1
    # Once the file reads again, the next save keeps what it held.
    added = editor.make(11)
    starting.add_bookmark(added)
    assert persisted(editor) == [item.pack() for item in items + [added]]


def test_unparseable_file_is_set_aside(editor: Any) -> None:
    editor.path.write_bytes(b'not a pickle')
    tick(editor.path)
    assert editor.store.get_bookmarks() == []
    broken = editor.path.with_name(editor.path.name + '.broken')
    assert broken.read_bytes() == b'not a pickle'
    added = editor.make(10)
    editor.store.add_bookmark(added)
    assert persisted(editor) == [added.pack()]
    assert editor.errors == []


def test_folder_bookmark_reopens_its_folder_and_matches_the_open_book(
        editor: Any, monkeypatch: Any, tmp_path: Any) -> None:
    app = fake_app(monkeypatch, tmp_path)
    root = folder_book(tmp_path / 'Root')
    app.images.get_pretty_current_filename = lambda: 'b\\1.png'
    app.window.toolbar = Mock()
    store = editor.store
    store._window, store._file_handler, store._image_handler = app.window, app.handler, app.images
    try:
        assert app.handler.open_file(root)
        loaded(app.handler)
        app.window.set_page(2)
        store.add_current_to_bookmarks()
        [bookmark] = store.get_bookmarks()
        assert (bookmark._path, bookmark._page) == (root, 2)
        with monkeypatch.context() as patch:
            patch.setattr(app.handler, 'open_file',
                          Mock(side_effect=AssertionError('the open book must not reopen')))
            bookmark._load()
        app.handler.close_file()
        bookmark._load()
        loaded(app.handler)
        assert app.handler._base_path == root
        assert app.images.get_real_path() == str(tmp_path / 'Root' / 'b' / '1.png')
    finally:
        app.close()
