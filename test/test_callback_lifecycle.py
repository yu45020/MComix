"""Real GTK subscriber destruction while Python wrappers remain referenced."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk  # noqa: E402

from mcomix import callback, comment_dialog, edit_image_area  # noqa: E402
from mcomix import enhance_dialog  # noqa: E402
from mcomix import openwith, openwith_menu, properties_dialog  # noqa: E402
from mcomix.library import book_area  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402


class Events:
    file_loaded = False
    archive_type = None

    @callback.Callback
    def file_opened(self) -> None:
        pass

    @callback.Callback
    def file_closed(self) -> None:
        pass

    @callback.Callback
    def file_available(self, paths: Any) -> None:
        pass

    @callback.Callback
    def page_available(self, page: Any) -> None:
        pass

    @callback.Callback
    def book_added_to_collection(self, book: Any, collection: Any) -> None:
        pass

    @callback.Callback
    def books_removed(self) -> None:
        pass

    def page_is_available(self, page: Any = None) -> Any:
        return False

    def get_number_of_comments(self) -> Any:
        return 0

    def get_current_page(self) -> Any:
        return None


class Window(Gtk.Window):
    @callback.Callback
    def page_changed(self) -> None:
        pass


@pytest.fixture
def window(monkeypatch: Any) -> Iterator[Window]:
    commands = deepcopy(prefs['openwith commands'])
    monkeypatch.setattr(openwith_menu, '_openwith_manager',
                        openwith.OpenWithManager())
    parent = Window()
    parent.filehandler = Events()
    parent.imagehandler = Events()
    parent.enhancer = SimpleNamespace(
        brightness=1, contrast=1, saturation=1, sharpness=1,
        autocontrast=False, invert_color=False)
    yield parent
    parent.destroy()
    prefs['openwith commands'] = commands


def watch_methods(
        monkeypatch: Any, cls: Any, names: Any, calls: Any) -> None:
    for name in names:
        original = getattr(cls, name)

        def watched(self, *args: Any, _original: Any = original,
                    _name: Any = name, **kwargs: Any) -> Any:
            calls.append(_name)
            # Keep real constructors/update paths active; events used here
            # deliberately represent a window with no loaded book.
            if _name in ('_new_book_added', '_books_removed'):
                return
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, watched)


_WATCHED = {
    'properties': (properties_dialog._PropertiesDialog,
                   ['_on_page_change', '_on_book_change', '_on_page_available']),
    'enhance': (enhance_dialog._EnhanceImageDialog,
                ['_on_page_change', '_on_book_close', '_on_page_available']),
    'comments': (comment_dialog._CommentsDialog, ['_update_comments', '_on_file_available']),
    'edit-images': (edit_image_area._ImageArea, ['_on_page_available']),
    'openwith-editor': (openwith.OpenWithEditor, ['test_command']),
    'openwith-menu': (openwith_menu.OpenWithMenu, ['_construct_menu', '_set_sensitivity']),
    'book-area': (book_area._BookArea, ['_new_book_added', '_books_removed']),
}


def make_widget(
        kind: Any, window: Any, monkeypatch: Any, calls: Any) -> Gtk.Widget:
    cls, names = _WATCHED[kind]
    watch_methods(monkeypatch, cls, names, calls)
    if kind == 'edit-images':
        return cls(None, window)
    if kind == 'openwith-editor':
        return cls(window, openwith.OpenWithManager())
    if kind == 'openwith-menu':
        return cls(Gtk.UIManager(), window)
    if kind == 'book-area':
        window.backend = Events()
        window._statusbar = Gtk.Statusbar()
        window.get_status_bar = lambda: window._statusbar
    return cls(window)


def fire_events(window: Any, kind: Any) -> None:
    window.page_changed()
    window.filehandler.file_opened()
    window.filehandler.file_closed()
    window.filehandler.file_available([])
    window.imagehandler.page_available(1)
    if kind == 'openwith-menu':
        # Avoid persisting or changing commands; dispatch the real manager's
        # decorated no-op body with an unchanged command list.
        manager = openwith_menu._openwith_manager
        manager.set_commands(manager.get_commands())
    if kind == 'book-area':
        window.backend.book_added_to_collection(None, None)
        window.backend.books_removed()


@pytest.mark.parametrize('kind', list(_WATCHED))
def test_destroyed_widgets_detach_even_when_kept_alive(
        kind: Any, window: Any, monkeypatch: Any) -> None:
    calls = []
    errors = []
    monkeypatch.setattr('mcomix.callback.log.error',
                        lambda *args: errors.append(args))
    widget = make_widget(kind, window, monkeypatch, calls)
    try:
        before = len(calls)
        fire_events(window, kind)
        assert len(calls) > before
        widget.destroy()
        widget.destroy()
        after_destroy = calls[:]
        fire_events(window, kind)
        assert calls == after_destroy
        assert errors == []
    finally:
        widget.destroy()


def test_enhance_parent_destroy_resets_singleton(
        window: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(enhance_dialog, '_dialog', None)
    enhance_dialog.open_dialog(None, window)
    dialog = enhance_dialog._dialog
    dialog.destroy()
    assert enhance_dialog._dialog is None
    enhance_dialog.open_dialog(None, window)
    assert enhance_dialog._dialog is not dialog
    enhance_dialog._close_dialog()
