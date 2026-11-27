"""File-derived dialog text stays literal while headings retain formatting."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango  # noqa: E402
from mcomix import file_chooser_base_dialog, main, message_dialog  # noqa: E402


@pytest.mark.parametrize('text', [
    'Tom & Jerry.cbz', '<b>book</b>.cbz', 'already &amp; literal.cbz',
])
def test_dialog_plain_text_is_rendered_once(text: str) -> None:
    parent = Gtk.Window()
    dialog = message_dialog.MessageDialog(parent)
    try:
        dialog.set_text(text, text)
        assert dialog.props.secondary_text == text
        assert not dialog.props.secondary_use_markup
        success, attrs, visible, accel = Pango.parse_markup(dialog.props.text, -1, '\x00')
        assert success and visible == text
        assert len(attrs.get_attributes()) > 0
    finally:
        dialog.destroy()
        parent.destroy()


def test_delete_confirmation_renders_filename_before_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = Gtk.Window()
    text = 'Tom & Jerry.cbz'
    checked = []

    def cancel(dialog: Any) -> int:
        try:
            assert Pango.parse_markup(dialog.props.text, -1, '\x00')[2] == 'Delete "' + text + '"?'
            checked.append(True)
            return Gtk.ResponseType.CANCEL
        finally:
            dialog.destroy()

    monkeypatch.setattr(message_dialog.MessageDialog, 'run', cancel)
    target = SimpleNamespace(imagehandler=SimpleNamespace(get_real_path=lambda: text))
    # The unbound handler only uses self as the transient parent before Cancel.
    parent.imagehandler = target.imagehandler
    try:
        main.MainWindow.delete(parent)
        assert checked == [True]
    finally:
        parent.destroy()


def test_overwrite_confirmation_renders_filename_before_cancel(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'Tom & Jerry.cbz'
    path.write_bytes(b'original')
    checked = []
    parent = Gtk.Window()
    monkeypatch.setattr(main, 'main_window', lambda: parent)

    def cancel(dialog: Any) -> int:
        try:
            visible = Pango.parse_markup(dialog.props.text, -1, '\x00')[2]
            assert path.name in visible and '&amp;' not in visible
            checked.append(True)
            return Gtk.ResponseType.CANCEL
        finally:
            dialog.destroy()

    monkeypatch.setattr(message_dialog.MessageDialog, 'run', cancel)
    chooser = SimpleNamespace(get_filenames=lambda: [str(path)], get_filter=lambda: None)
    target = SimpleNamespace(
        _action=Gtk.FileChooserAction.SAVE, filechooser=chooser,
        emit_stop_by_name=Mock(), files_chosen=Mock())
    try:
        file_chooser_base_dialog._BaseFileChooserDialog._response(target, None, Gtk.ResponseType.OK)
        assert checked == [True]
        assert path.read_bytes() == b'original'
        target.emit_stop_by_name.assert_called_once_with('response')
        target.files_chosen.assert_not_called()
    finally:
        parent.destroy()


def test_library_delete_failure_is_not_double_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcomix.library import book_area
    parent = Gtk.Window()
    parent._closed = False
    path = 'C:/Tom & Jerry.cbz'
    parent.backend = SimpleNamespace(get_book_path=lambda ident: path)
    handler = SimpleNamespace(_shutting_down=False, archive_type=None,
                              wait_for_file_release=Mock(side_effect=PermissionError('denied & blocked')))
    parent._file_handler = handler
    model = Gtk.ListStore(int, int, str)
    model.append([0, 1, path])
    removed = Mock()
    area = SimpleNamespace(
        _library=parent, _liststore=model,
        _iconview=SimpleNamespace(get_selected_items=lambda: [Gtk.TreePath.new_from_indices([0])]),
        get_book_at_path=lambda item: 1, _remove_book_ids_from_library=removed)
    checked = []

    def close(dialog: Any) -> int:
        try:
            assert dialog.props.secondary_text == path + ': denied & blocked'
            assert not dialog.props.secondary_use_markup
            checked.append(True)
            return Gtk.ResponseType.CLOSE
        finally:
            dialog.destroy()

    monkeypatch.setattr(message_dialog.MessageDialog, 'run', close)
    try:
        book_area._BookArea._completely_remove_book(area, False)
        assert checked == [True]
        removed.assert_called_once_with([])
    finally:
        parent.destroy()
