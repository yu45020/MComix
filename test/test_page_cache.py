"""Displayed pages lead the live page-cache and extraction working set."""

import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock

import pytest
from PIL import Image

from ._page_fakes import dispatch, make_image_handler, set_prefs
from gi.repository import GdkPixbuf, Gtk
from mcomix import constants, image_tools, main, preferences_dialog
from mcomix.preferences import prefs
from mcomix.worker_thread import WorkerThread


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[Any]:
    result = make_image_handler(monkeypatch, tmp_path,
                                ['page-%02d.png' % page for page in range(1, 31)], 7)
    result._thread = Mock()
    result._current_image_index = 9
    yield result
    result._thread.stop()
    dispatch()


@pytest.fixture
def wide_double(monkeypatch: pytest.MonkeyPatch) -> None:
    set_prefs(monkeypatch, {
        'default double page': True, 'max pages to cache': 0,
        'virtual double page for fitting images': constants.SHOW_DOUBLE_AS_ONE_WIDE})


def _blank(width: int, height: int) -> Any:
    return GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)


@pytest.mark.parametrize('double,limit,expected', [
    (False, 0, [10]), (False, 2, [10, 11]), (False, 3, [10, 11, 9]),
    (True, 1, [10, 11]), (True, 3, [10, 11, 12]),
])
def test_visible_pages_lead_cache_and_extraction(
        handler: Any, monkeypatch: pytest.MonkeyPatch,
        double: bool, limit: int, expected: Any) -> None:
    set_prefs(monkeypatch, {'default double page': double, 'max pages to cache': limit})
    handler._raw_pixbufs = dict.fromkeys(range(30), 'cached')
    handler._get_pixbuf = Mock(side_effect=AssertionError('scheduler decoded a page'))
    handler.do_cacheing()
    assert handler._wanted_pixbufs == [page - 1 for page in expected]
    assert set(handler._raw_pixbufs) == {page - 1 for page in expected}
    handler._window.filehandler._ask_for_files.assert_called_once_with(
        [handler._image_files[page - 1] for page in expected])
    handler._get_pixbuf.assert_not_called()


@pytest.mark.parametrize('page,total,double,limit', [
    (1, 2, False, 0), (1, 30, True, -1), (2, 30, True, 7), (29, 30, False, 7),
    (29, 30, True, 3), (30, 30, False, 3), (30, 30, True, 0),
])
def test_window_boundaries_do_not_drop_visible_pages(
        handler: Any, monkeypatch: pytest.MonkeyPatch,
        limit: int, double: bool, page: int, total: int) -> None:
    set_prefs(monkeypatch, {'max pages to cache': limit, 'default double page': double})
    handler._image_files = handler._image_files[:total]
    handler.set_page(page)
    visible = [page - 1]
    if double and page < total:
        visible.append(page)
    wanted = handler._wanted_pixbufs
    assert wanted[:len(visible)] == visible
    assert len(wanted) == (total if limit == -1 else min(total, max(limit, len(visible))))
    assert len(wanted) == len(set(wanted))
    assert all(0 <= index < total for index in wanted)


@pytest.mark.parametrize('archive', [False, True])
def test_cover_policy_uses_single_page_only_for_archives(
        handler: Any, monkeypatch: pytest.MonkeyPatch, archive: bool) -> None:
    set_prefs(monkeypatch, {
        'default double page': True, 'max pages to cache': 0,
        'virtual double page for fitting images': constants.SHOW_DOUBLE_AS_ONE_TITLE})
    handler._window.filehandler.archive_type = 'zip' if archive else None
    handler.set_page(1)
    assert handler._wanted_pixbufs == ([0] if archive else [0, 1])


@pytest.mark.parametrize('wide_offset,exif,rotation', [(0, True, 90), (1, False, 90), (1, True, 0)])
def test_known_wide_geometry_respects_exif_and_survives_pixbuf_eviction(
        handler: Any, monkeypatch: pytest.MonkeyPatch, wide_double: None,
        wide_offset: int, exif: bool, rotation: int) -> None:
    monkeypatch.setitem(prefs, 'auto rotate from exif', exif)
    handler._available_images = {9, 10}
    handler._raw_pixbufs = {9: _blank(2, 3), 10: _blank(2, 3)}
    handler._raw_pixbufs[9 + wide_offset] = _blank(4, 2)
    monkeypatch.setattr(image_tools, 'get_implied_rotation',
                        lambda pixbuf: rotation if pixbuf.get_width() == 4 else 0)
    handler.do_cacheing()
    wide = not (exif and rotation == 90)
    assert handler._wanted_pixbufs == ([9] if wide else [9, 10])
    handler._get_pixbuf = Mock(side_effect=AssertionError('known geometry decoded again'))
    assert handler.get_virtual_double_page() is wide
    handler._get_pixbuf.assert_not_called()
    # Keep the raw orientation, so changing the preference reinterprets it.
    monkeypatch.setitem(prefs, 'auto rotate from exif', not exif)
    handler.do_cacheing()
    assert handler._wanted_pixbufs == ([9, 10] if not exif and rotation == 90 else [9])


def test_unknown_geometry_never_decodes_during_scheduling(
        handler: Any, wide_double: None) -> None:
    handler._available_images = {9, 10}
    handler._get_pixbuf = Mock(side_effect=AssertionError('cache planning blocked'))
    handler.do_cacheing()
    assert handler._wanted_pixbufs == [9, 10]
    handler._get_pixbuf.assert_not_called()
    handler._raw_pixbufs[10] = _blank(4, 2)
    handler._page_decoded(handler._page_generation, 10, handler._image_files[10])
    assert handler._wanted_pixbufs == [9]


def test_old_geometry_cannot_publish_after_apply_or_cleanup(
        handler: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    handler._raw_pixbufs[9] = _blank(4, 2)
    entered = threading.Event()
    release = threading.Event()

    def rotation(pixbuf: Any) -> int:
        entered.set()
        assert release.wait(5)
        return 0

    monkeypatch.setattr(image_tools, 'get_implied_rotation', rotation)
    results = []
    worker = threading.Thread(target=lambda: results.append(handler._page_is_wide(10, True)))
    worker.start()
    assert entered.wait(5)
    try:
        handler.reset_page_list(list(reversed(handler._image_files)))
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results == [None]
    assert handler._page_dimensions == {}
    handler._page_dimensions[0] = (4, 2, 0)
    handler.cleanup()
    assert handler._page_dimensions == {}


def test_real_preferences_spinner_updates_open_book_immediately(
        handler: Any) -> None:
    handler._available_images = set(range(30))
    dialog = SimpleNamespace(_window=handler._window)
    spinner = Gtk.SpinButton.new_with_range(-1, 500, 1)
    for limit in (0, -1, 2, 7):
        spinner.set_value(limit)
        handler._raw_pixbufs = dict.fromkeys(range(30), 'cached')
        preferences_dialog._PreferencesDialog._spinner_cb(
            dialog, spinner, 'max pages to cache')
        assert handler._cache_pages == limit
        assert len(handler._wanted_pixbufs) == (30 if limit == -1 else max(1, limit))
        assert set(handler._raw_pixbufs) == set(handler._wanted_pixbufs)


def test_double_and_virtual_preferences_rebuild_working_set(handler: Any) -> None:
    prefs['max pages to cache'] = 0
    toggle = Gtk.ToggleAction.new('double', 'double', None, None)
    toggle.set_active(True)
    main.MainWindow.change_double_page(handler._window, toggle)
    assert handler._wanted_pixbufs == [9, 10]
    handler._current_image_index = 0
    model = Gtk.ListStore(str, int)
    model.append(['title', constants.SHOW_DOUBLE_AS_ONE_TITLE])
    combo = Gtk.ComboBox.new_with_model(model)
    combo.set_active(0)
    preferences_dialog._PreferencesDialog._combobox_cb(
        SimpleNamespace(_window=handler._window), combo,
        'virtual double page for fitting images')
    assert handler._wanted_pixbufs == [0]
    handler.set_page(10)
    assert handler._wanted_pixbufs == [9, 10]
    toggle.set_active(False)
    main.MainWindow.change_double_page(handler._window, toggle)
    assert handler._wanted_pixbufs == [9]


def test_real_exif_checkbox_reinterprets_geometry(handler: Any, wide_double: None) -> None:
    handler._available_images = {9, 10}
    handler._page_dimensions = {9: (4, 2, 90), 10: (2, 3, 0)}
    checkbox = Gtk.CheckButton()
    for active, expected in [(False, [9]), (True, [9, 10])]:
        checkbox.set_active(active)
        preferences_dialog._PreferencesDialog._check_button_cb(
            SimpleNamespace(_window=handler._window), checkbox, 'auto rotate from exif')
        assert handler._wanted_pixbufs == expected


def test_unlimited_caches_already_available_pages_with_real_worker(
        handler: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = []
    for page in range(13):
        path = tmp_path / ('%02d.png' % page)
        Image.new('RGB', (2, 3), (page, 0, 0)).save(path)
        paths.append(str(path))
    handler._image_files = paths
    handler._available_images = set(range(len(paths)))
    handler._thread = WorkerThread(handler._cache_pixbuf, name='cache-regression', sort_orders=True)
    monkeypatch.setitem(prefs, 'max pages to cache', -1)
    handler.set_page(10)
    deadline = time.monotonic() + 5
    while len(handler._raw_pixbufs) < len(paths) and time.monotonic() < deadline:
        time.sleep(0.005)
    handler._thread.stop()
    dispatch()
    assert set(handler._raw_pixbufs) == set(range(len(paths)))


def test_missing_or_failed_image_geometry_remains_usable(
        handler: Any, monkeypatch: pytest.MonkeyPatch, wide_double: None) -> None:
    handler._available_images = {9, 10}
    monkeypatch.setattr(image_tools, 'load_pixbuf', Mock(side_effect=ValueError('decode failed')))
    assert not handler.get_virtual_double_page()
    handler.do_cacheing()
    assert handler._wanted_pixbufs == [9, 10]
    assert set(handler._page_dimensions) == {9, 10}


@pytest.mark.parametrize('auto_exif,raw_exif', [
    (False, '\nexif\nbad\n00'), (True, '\nexif\n8\n6761726261676521'),
    (False, '\nexif\n4\n49492a00')])
def test_malformed_exif_does_not_break_cache_geometry(
        handler: Any, monkeypatch: pytest.MonkeyPatch, wide_double: None,
        auto_exif: bool, raw_exif: str) -> None:
    monkeypatch.setitem(prefs, 'auto rotate from exif', auto_exif)
    portrait = _blank(2, 3)
    wide = _blank(4, 2)
    for pixbuf in (portrait, wide):
        assert pixbuf.set_option('tEXt::Raw profile type exif', raw_exif)
    handler._available_images = {9, 10}
    handler._raw_pixbufs = {9: portrait, 10: wide}
    handler.do_cacheing()
    assert handler._wanted_pixbufs == [9]
    assert handler._page_dimensions == {9: (2, 3, 0), 10: (4, 2, 0)}
    assert handler.get_virtual_double_page()
    handler.reset_page_list(list(reversed(handler._image_files)))
    assert handler._page_dimensions == {}
