"""Application-owned history in private, real GTK recent managers."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
import xml.etree.ElementTree as ET

import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk  # noqa: E402

from mcomix import constants, recent  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402


APP = constants.APPNAME
OTHER = 'OtherGtkApp'


def seed(path: Path, entries: Any) -> None:
    bookmarks = GLib.BookmarkFile()
    stamp = GLib.DateTime.new_now_utc().add_seconds(-3600)
    for uri, applications in entries:
        bookmarks.set_title(uri, 'A title & unicode: \u00e9')
        bookmarks.set_description(uri, 'Description')
        bookmarks.set_mime_type(uri, 'image/png')
        bookmarks.set_groups(uri, ['Comic', 'Shared'])
        bookmarks.set_is_private(uri, True)
        bookmarks.set_icon(uri, 'file:///C:/icon.png', 'image/png')
        for application in applications:
            bookmarks.set_application_info(
                uri, application, '"C:/Program Files/%s.exe" %%u' % application,
                7, stamp)
        bookmarks.set_added_date_time(uri, stamp)
        bookmarks.set_modified_date_time(uri, stamp)
        bookmarks.set_visited_date_time(uri, stamp)
    bookmarks.to_file(str(path))


def xml_entries(path: Path) -> Any:
    entries = {element.attrib['href']: element
               for element in ET.parse(path).getroot().findall('bookmark')}
    for element in entries.values():
        element.tail = None
    return entries


@pytest.fixture
def history(tmp_path: Path, monkeypatch: Any) -> Iterator[Any]:
    path = tmp_path / 'recent.xbel'
    own = (tmp_path / 'own & \u00e9.png').as_uri()
    other = (tmp_path / 'other.png').as_uri()
    shared = (tmp_path / 'shared.png').as_uri()
    seed(path, [(own, [APP]), (other, [OTHER]), (shared, [APP, OTHER])])
    manager = Gtk.RecentManager(filename=str(path))
    window = SimpleNamespace(filehandler=SimpleNamespace(
        open_file=lambda path: False))
    menus = [recent.RecentFilesMenu(window, manager) for _ in range(2)]
    for menu in menus:
        menu.set_show_not_found(True)
        menu.set_show_private(True)
    monkeypatch.setitem(prefs, 'store recent file info', True)
    yield SimpleNamespace(path=path, own=own, other=other, shared=shared,
                          manager=manager, menus=menus, menu=menus[0])
    for menu in menus:
        menu.destroy()
    manager.run_dispose()


def test_purge_removes_exclusive_and_keeps_shared_metadata(history: Any) -> None:
    before = xml_entries(history.path)
    history.menu.remove_all()
    history.manager.emit('changed')
    after = xml_entries(history.path)
    assert set(after) == {history.other, history.shared}
    assert ET.tostring(after[history.shared]) == ET.tostring(before[history.shared])
    assert ET.tostring(after[history.other]) == ET.tostring(before[history.other])
    # GTK cannot drop only our registration from a shared entry.
    assert set(history.manager.lookup_item(history.shared).get_applications()) == {
        APP, OTHER}
    assert history.menu.count() == 1
    for menu in history.menus:
        assert {item.get_uri() for item in menu.get_items()} == {history.shared}


def test_pending_add_purge_then_immediate_add(history: Any, tmp_path: Path) -> None:
    new = tmp_path / 'pending.png'
    history.menu.add(str(new))
    assert history.menu.count() == 3
    history.menu.remove_all()
    assert history.menu.count() == 1
    history.menu.add(str(new))
    assert history.menu.count() == 2
    history.manager.emit('changed')
    reloaded = Gtk.RecentManager(filename=str(history.path))
    try:
        assert not reloaded.has_item(history.own)
        assert reloaded.lookup_item(new.as_uri()).get_applications() == [APP]
        assert set(reloaded.lookup_item(history.shared).get_applications()) == {
            APP, OTHER}
        assert reloaded.lookup_item(history.other).get_applications() == [OTHER]
    finally:
        reloaded.run_dispose()


def test_failed_remove_item_does_not_raise(
        history: Any, monkeypatch: Any) -> None:
    def fail(uri: str) -> None:
        raise GLib.Error('injected removal failure')

    monkeypatch.setattr(history.manager, 'remove_item', fail)
    history.menu.remove_all()
    history.menu.remove(GLib.filename_from_uri(history.own)[0])
    assert history.menu.count() == 2


def test_never_still_clears_our_history(history: Any, monkeypatch: Any) -> None:
    monkeypatch.setitem(prefs, 'store recent file info', False)
    history.menu.remove_all()
    assert not history.manager.has_item(history.own)
    assert history.manager.has_item(history.other)
    assert history.manager.has_item(history.shared)


def test_never_disables_add_and_failed_open_removal(
        history: Any, monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setitem(prefs, 'store recent file info', False)
    before = history.path.read_bytes()
    history.menu.add(str(tmp_path / 'new.png'))
    history.menu.remove(GLib.filename_from_uri(history.own)[0])
    assert history.path.read_bytes() == before
    assert history.menu.count() == 2


def test_add_explicit_identity_and_file_uri(
        history: Any, tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / 'book # & \u00e9.png'
    history.menu.add(str(path))
    item = history.manager.lookup_item(path.as_uri())
    assert item.get_applications() == [APP]
    assert item.get_mime_type() == 'image/png'
    calls = []
    monkeypatch.setattr(history.menu, 'get_current_uri', lambda: path.as_uri())
    history.menu._window.filehandler.open_file = lambda value: calls.append(value) or True
    history.menu._load()
    assert calls == [str(path)]


def test_failed_open_removes_exclusive_entry(
        history: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(history.menu, 'get_current_uri', lambda: history.own)
    history.menu._load()
    assert not history.manager.has_item(history.own)
    assert history.manager.has_item(history.shared)


@pytest.mark.parametrize('uri', [None, 'https://example.test/book.png'])
def test_load_nonlocal_or_missing_selection_is_noop(
        history: Any, monkeypatch: Any, uri: Any) -> None:
    calls = []
    history.menu._window.filehandler.open_file = lambda path: calls.append(path)
    monkeypatch.setattr(history.menu, 'get_current_uri', lambda: uri)
    history.menu._load()
    assert calls == []


def test_menu_ownership_is_and_not_or(history: Any, tmp_path: Path) -> None:
    path = tmp_path / 'unsupported.unknownextension'
    data = Gtk.RecentData()
    data.app_name = APP
    data.app_exec = 'MComix %u'
    data.mime_type = 'text/plain'
    history.manager.add_full(path.as_uri(), data)
    assert history.menu.count() == 3
    assert {item.get_uri() for item in history.menu.get_items()} == {
        history.own, history.shared}


def test_first_pending_add_can_be_purged_before_any_history_file(
        tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / 'initial.xbel'
    manager = Gtk.RecentManager(filename=str(path))
    menu = recent.RecentFilesMenu(None, manager)
    monkeypatch.setitem(prefs, 'store recent file info', True)
    try:
        menu.add(str(tmp_path / 'first.png'))
        assert menu.count() == 1
        assert not path.exists()
        menu.remove_all()
        assert menu.count() == 0
        manager.emit('changed')
        assert not path.exists() or xml_entries(path) == {}
    finally:
        menu.destroy()
        manager.run_dispose()
