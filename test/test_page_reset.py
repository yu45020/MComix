"""Edited page identities survive imports, reordering and old cache work."""

import os
import threading
from types import SimpleNamespace
from typing import Any, Iterator, List, Set
from unittest.mock import Mock

import pytest
from PIL import Image

from ._page_fakes import dispatch, until
from ._shells import file_handler_shell
from gi.repository import GdkPixbuf, Gtk
from mcomix import constants, edit_dialog, edit_image_area, image_handler, image_tools
from mcomix.preferences import prefs
from mcomix.worker_thread import WorkerThread


class _Extractor:
    def __init__(self, names: List[str], ready: Set[str]) -> None:
        self.names = list(names)
        self.ready = ready

    def is_ready(self, name: str) -> bool:
        return name in self.ready

    def get_files(self) -> List[str]:
        return list(self.names)

    def set_files(self, names: List[str]) -> None:
        self.names = list(names)

    def is_lazy(self) -> bool:
        return False

    def raise_if_failed(self, name: str) -> None:
        # This fixture models ready or pending members, without read errors.
        pass


class _ImageArea:
    get_file_listing = edit_image_area._ImageArea.get_file_listing
    add_extra_image = edit_image_area._ImageArea.add_extra_image

    def __init__(self, paths: List[str]) -> None:
        self._filler = None
        self._liststore = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str, bool)
        for path in paths:
            self.add_extra_image(path)


class _Window:
    def __init__(self) -> None:
        self.thumbnailsidebar = Mock()
        self.page_changed = Mock()
        self.new_page = Mock()
        self.clear = Mock()

    def set_page(self, page: int) -> None:
        # MainWindow's early exit matters when Apply replaces page one.
        if page == self.imagehandler.get_current_page():
            return
        self.imagehandler.set_page(page)
        self.page_changed()
        self.new_page()

    def displayed_double(self) -> bool:
        return False


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Window]:
    monkeypatch.setitem(prefs, 'max pages to cache', 7)
    monkeypatch.setitem(prefs, 'default double page', False)
    monkeypatch.setattr(image_handler.upscale, 'UpscaleService', Mock)
    result = _Window()
    handler = file_handler_shell()
    handler.file_loaded = True
    handler.archive_type = 'application/zip'
    handler._condition = threading.Condition()
    handler._name_table = {}
    handler._extractor = _Extractor([], set())
    handler.update_comment_extensions()
    result.filehandler = handler
    result.imagehandler = image_handler.ImageHandler(result)
    # Unless a case opts into the real worker, keep no background work alive.
    result.imagehandler._thread = Mock()
    yield result
    result.imagehandler._thread.stop()
    dispatch()


def _pages(window: _Window, paths: List[str], available: Set[int]) -> None:
    handler = window.imagehandler
    handler._image_files = list(paths)
    handler._current_image_index = 0 if paths else None
    handler._available_images = set(available)
    names = ['page-%d' % index for index in range(len(paths))]
    window.filehandler._name_table = dict(zip(paths, names))
    ready = {names[index] for index in available}
    window.filehandler._extractor = _Extractor(names, ready)


def _apply(window: _Window, paths: List[str]) -> None:
    dialog = SimpleNamespace(_window=window, _image_area=_ImageArea(paths))
    edit_dialog._EditArchiveDialog._response(dialog, None, Gtk.ResponseType.APPLY)


def _import_dialog(window: _Window, old: List[str], imported: str,
                   monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return an edit dialog after the real Import response added imported."""
    Image.new('RGB', (3, 4), 'red').save(imported)
    dialog = SimpleNamespace(_window=window, _image_area=_ImageArea(old))
    chooser = Mock()
    chooser.get_paths.return_value = [imported]
    monkeypatch.setattr(edit_dialog.file_chooser_simple_dialog,
                        'SimpleFileChooserDialog', Mock(return_value=chooser))
    edit_dialog._EditArchiveDialog._response(dialog, None, constants.RESPONSE_IMPORT)
    assert dialog._image_area.get_file_listing() == old + [imported]
    return dialog


def test_import_and_apply_appended_page(
        window: _Window, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    old = ['a.png', 'b.png', 'c.png']
    _pages(window, old, {0, 1, 2})
    imported = str(tmp_path / 'imported.png')
    dialog = _import_dialog(window, old, imported, monkeypatch)
    edit_dialog._EditArchiveDialog._response(dialog, None, Gtk.ResponseType.APPLY)
    assert window.imagehandler._image_files == old + [imported]
    assert window.imagehandler._available_images == {0, 1, 2, 3}
    assert window.filehandler.file_is_available(imported)
    assert not window.filehandler.file_is_available(str(tmp_path / 'missing.png'))
    window.thumbnailsidebar.clear.assert_called_once()
    window.thumbnailsidebar.load_thumbnails.assert_called_once()
    window.page_changed.assert_called_once()
    window.new_page.assert_called_once()
    assert window.imagehandler.get_current_page() == 1


def test_import_deleted_before_apply_uses_missing_image_and_can_reload(
        window: _Window, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _pages(window, ['original.png'], {0})
    imported = str(tmp_path / 'imported.png')
    dialog = _import_dialog(window, ['original.png'], imported, monkeypatch)
    os.unlink(imported)
    edit_dialog._EditArchiveDialog._response(dialog, None, Gtk.ResponseType.APPLY)
    assert not window.filehandler.file_is_available(imported)
    assert window.imagehandler.page_is_available(2)
    window.set_page(2)
    assert window.imagehandler.get_pixbufs(1) == [image_tools.MISSING_IMAGE_ICON]
    # Apply replaces the generation and drops the failed decode, so recreating
    # an imported source does not keep the cached missing-image placeholder.
    Image.new('RGB', (3, 4), 'green').save(imported)
    edit_dialog._EditArchiveDialog._response(dialog, None, Gtk.ResponseType.APPLY)
    window.set_page(2)
    pixbuf = window.imagehandler.get_pixbufs(1)[0]
    assert pixbuf is not image_tools.MISSING_IMAGE_ICON
    assert list(pixbuf.get_pixels()[:3]) == [0, 128, 0]


def test_apply_reorders_availability_by_path_and_removes_page(
        window: _Window) -> None:
    _pages(window, ['a.png', 'b.png', 'c.png'], {0})
    _apply(window, ['b.png', 'a.png'])
    assert window.imagehandler._available_images == {1}
    assert not window.imagehandler.page_is_available(1)
    assert window.imagehandler.page_is_available(2)
    # An old availability callback is mapped to the retained source, while
    # removed paths and duplicate notifications are harmless.
    window.filehandler.file_available(['b.png', 'c.png'])
    window.filehandler.file_available(['b.png'])
    assert window.imagehandler._available_images == {0, 1}


def test_apply_removes_all_pages(window: _Window) -> None:
    _pages(window, ['a.png'], {0})
    _apply(window, [])
    assert window.imagehandler.get_current_page() == 0
    assert window.imagehandler._available_images == set()
    assert window.imagehandler._raw_pixbufs == {}
    window.clear.assert_called_once()
    window.page_changed.assert_not_called()
    window.new_page.assert_not_called()


def test_priority_requests_ignore_imported_and_unqueued_paths(window: _Window) -> None:
    _pages(window, ['a.png', 'b.png'], set())
    window.filehandler._extractor.names = ['page-1']
    window.filehandler._ask_for_files(['imported.png', 'a.png', 'b.png'])
    assert window.filehandler._extractor.names == ['page-1']


def test_inflight_decode_cannot_publish_after_apply(
        window: _Window, monkeypatch: pytest.MonkeyPatch) -> None:
    _pages(window, ['old.png', 'new.png'], {0, 1})
    handler = window.imagehandler
    handler._thread = WorkerThread(handler._cache_pixbuf, name='page-reset')
    started = threading.Event()
    release = threading.Event()
    notified = []
    handler.page_cached += notified.append

    def decode(source: str) -> str:
        if source == 'old.png':
            started.set()
            assert release.wait(5)
        return 'pixels:' + source

    monkeypatch.setattr(image_tools, 'load_pixbuf', decode)
    handler.do_cacheing()
    assert started.wait(5)
    try:
        _apply(window, ['new.png'])
        assert handler._raw_pixbufs == {}
    finally:
        release.set()
    until(lambda: handler._raw_pixbufs == {0: 'pixels:new.png'})
    dispatch()
    assert notified == [1]
    assert handler._raw_pixbufs == {0: 'pixels:new.png'}


def test_queued_decode_keeps_original_generation(
        window: _Window, monkeypatch: pytest.MonkeyPatch) -> None:
    _pages(window, ['old.png'], {0})
    handler = window.imagehandler
    handler.do_cacheing()
    order = handler._thread.extend_orders.call_args[0][0][0]
    _apply(window, ['replacement.png'])
    decode = Mock()
    monkeypatch.setattr(image_tools, 'load_pixbuf', decode)
    handler._cache_pixbuf(order)
    decode.assert_not_called()
    assert handler._raw_pixbufs == {}


def test_queued_callback_is_discarded_even_when_same_path_returns(
        window: _Window, monkeypatch: pytest.MonkeyPatch) -> None:
    _pages(window, ['a.png'], {0})
    handler = window.imagehandler
    notified = []
    handler.page_cached += notified.append
    monkeypatch.setattr(image_tools, 'load_pixbuf', Mock(return_value='pixels'))
    handler._wanted_pixbufs = [0]
    worker = threading.Thread(target=handler._get_pixbuf, args=(0,))
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    assert handler._raw_pixbufs == {0: 'pixels'}
    _apply(window, ['a.png'])
    dispatch()
    assert notified == []
    assert handler._raw_pixbufs == {}


def test_reset_transient_state_allows_page_queries(window: _Window) -> None:
    _pages(window, ['a.png'], {0})
    replacement = ['a.png']
    window.imagehandler.reset_page_list(replacement)
    replacement.append('external-mutation.png')
    handler = window.imagehandler
    assert handler._image_files == ['a.png']
    assert handler.get_path_to_page() is None
    assert handler.get_pixbufs(1) == []
    assert handler.get_size() == (0, 0)
    assert handler.get_mime_name() is None
    assert handler.get_thumbnail() is None
    assert not handler.page_is_available()
    handler.do_cacheing()
    handler._thread.extend_orders.assert_not_called()
    handler._thread.stop.assert_not_called()
