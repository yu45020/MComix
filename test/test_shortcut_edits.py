"""Shortcut edits agree across GTK cells, dispatch and persisted configuration."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk  # noqa: E402

from mcomix import constants, event, keybindings, keybindings_editor  # noqa: E402


def _binding(value: str) -> tuple:
    return tuple(Gtk.accelerator_parse(value))


@pytest.fixture
def shortcuts(tmp_path: Path, monkeypatch: Any) -> Any:
    path = tmp_path / 'keys.conf'
    path.write_text('{}')
    monkeypatch.setattr(constants, 'KEYBINDINGS_CONF_PATH', str(path))
    monkeypatch.setattr(constants, 'CONFIG_DIR', str(tmp_path))
    window = SimpleNamespace(emit_stop_by_name=Mock(), actiongroup=Gtk.ActionGroup(name='test'))
    manager = keybindings._KeybindingManager(window)
    calls = []
    previous = {}
    for name, bindings in [('next_page', ['F2', 'F3', 'F4', 'F5']),
                           ('previous_page', ['F6', 'F7'])]:
        accel_path = '<Actions>/mcomix-main/' + name
        present, key = Gtk.AccelMap.lookup_entry(accel_path)
        previous[accel_path] = ((key.accel_key, key.accel_mods)
                                if present else (0, 0))
        Gtk.AccelMap.add_entry(accel_path, 0, 0)
        manager.register(name, bindings, calls.append, args=[name])
    editor = keybindings_editor.KeybindingEditorWindow(manager)
    yield SimpleNamespace(manager=manager, editor=editor, calls=calls,
                          path=path, window=window)
    editor.destroy()
    for accel_path, (key, modifiers) in previous.items():
        Gtk.AccelMap.change_entry(accel_path, key, modifiers, True)


def _cells(state: Any, name: str) -> list:
    iterator = state.editor.action_treeiter_map[name]
    return [state.editor.treestore.get_value(iterator, col + 3)
            for col in range(state.editor.accel_column_num)]


def _assert_consistent(state: Any, expected: dict) -> None:
    manager = state.manager
    reverse = {}
    for name, values in expected.items():
        bindings = [_binding(value) for value in values]
        assert manager.get_bindings_for_action(name) == bindings
        assert _cells(state, name) == values + [''] * (
            state.editor.accel_column_num - len(values))
        reverse.update((binding, name) for binding in bindings)
        present, accel = Gtk.AccelMap.lookup_entry(
            '<Actions>/mcomix-main/' + name)
        assert present
        assert (accel.accel_key, accel.accel_mods) == (
            bindings[0] if bindings else (0, 0))
    for value in ['F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8']:
        before = len(state.calls)
        manager.execute(_binding(value))
        action = reverse.get(_binding(value))
        assert state.calls[before:] == ([action] if action else [])
    stored = json.loads(state.path.read_text())
    for name, values in expected.items():
        assert stored[name] == values
    reloaded = keybindings._KeybindingManager(state.window)
    for name, values in expected.items():
        Gtk.AccelMap.change_entry('<Actions>/mcomix-main/' + name,
                                  *_binding('F8'), True)
        reloaded.register(name, ['F8'], lambda: None)
        accel = Gtk.AccelMap.lookup_entry('<Actions>/mcomix-main/' + name)[1]
        assert (accel.accel_key, accel.accel_mods) == (
            _binding(values[0]) if values else (0, 0))
        assert reloaded.get_bindings_for_action(name) == [
            _binding(value) for value in values]
    # Reload registration changes the shared map; restore author manager labels.
    for name in expected:
        manager._update_menu_accel(name)


def _edit(state: Any, name: str, column: int, value: str) -> None:
    iterator = state.editor.action_treeiter_map[name]
    path = state.editor.treestore.get_path(iterator).to_string()
    key, modifiers = _binding(value)
    state.editor.get_on_accel_edited(column)(None, path, key, modifiers, 0)


@pytest.mark.parametrize('column,value,remaining', [
    (0, 'F3', ['F3', 'F4', 'F5']),
    (2, 'F2', ['F3', 'F2', 'F5']),
    (3, 'F4', ['F2', 'F3', 'F4']),
])
def test_replace_with_same_action_key_removes_both_old_occurrences(
        shortcuts: Any, column: int, value: str, remaining: list) -> None:
    _edit(shortcuts, 'next_page', column, value)
    _assert_consistent(shortcuts, {'next_page': remaining,
                                   'previous_page': ['F6', 'F7']})


def test_noop_does_not_write_or_reorder(shortcuts: Any) -> None:
    shortcuts.manager.save()
    before = shortcuts.path.read_bytes()
    _edit(shortcuts, 'next_page', 1, 'F3')
    assert shortcuts.path.read_bytes() == before
    _assert_consistent(shortcuts, {'next_page': ['F2', 'F3', 'F4', 'F5'],
                                   'previous_page': ['F6', 'F7']})


@pytest.mark.parametrize('column,value,next_keys,previous_keys', [
    (0, 'F6', ['F6', 'F3', 'F4', 'F5'], ['F7']),
    (1, 'F7', ['F2', 'F7', 'F4', 'F5'], ['F6']),
    (3, 'F8', ['F2', 'F3', 'F4', 'F8'], ['F6', 'F7']),
])
def test_cross_action_or_new_replacement_updates_both_menus(
        shortcuts: Any, column: int, value: str, next_keys: list,
        previous_keys: list) -> None:
    _edit(shortcuts, 'next_page', column, value)
    _assert_consistent(shortcuts, {'next_page': next_keys,
                                   'previous_page': previous_keys})


def test_clear_compacts_cells_and_remains_empty_after_reload(shortcuts: Any) -> None:
    for count in range(4, 0, -1):
        iterator = shortcuts.editor.action_treeiter_map['next_page']
        path = shortcuts.editor.treestore.get_path(iterator).to_string()
        shortcuts.editor.get_on_accel_cleared(0)(None, path)
        assert len(shortcuts.manager.get_bindings_for_action('next_page')) == count - 1
    _assert_consistent(shortcuts, {'next_page': [],
                                   'previous_page': ['F6', 'F7']})
    shortcuts.manager.register('next_page', ['F8'], lambda: None)
    assert shortcuts.manager.get_bindings_for_action('next_page') == []
    shortcuts.manager.clear_all()
    shortcuts.manager.register('next_page', ['F8'], lambda: None)
    assert shortcuts.manager.get_bindings_for_action('next_page') == [_binding('F8')]


def test_transfer_last_donor_binding_clears_its_menu(shortcuts: Any) -> None:
    shortcuts.manager.clear_accel('previous_page', 'F7')
    shortcuts.editor._refresh_action('previous_page')
    _edit(shortcuts, 'next_page', 0, 'F6')
    _assert_consistent(shortcuts, {'next_page': ['F6', 'F3', 'F4', 'F5'],
                                   'previous_page': []})


def test_shortcut_of_insensitive_action_does_nothing(shortcuts: Any) -> None:
    # ui.MainUI.set_sensitivities makes Delete insensitive with no file open.
    action = Gtk.Action(name='delete')
    action.set_sensitive(False)
    shortcuts.window.actiongroup.add_action(action)
    shortcuts.manager.register('delete', ['Delete'], shortcuts.calls.append, args=['delete'])
    shortcuts.manager.execute(_binding('Delete'))
    assert shortcuts.calls == []
    action.set_sensitive(True)
    shortcuts.manager.execute(_binding('Delete'))
    assert shortcuts.calls == ['delete']


class _Keymap:
    """A keyboard layout: (hardware key, Shift held) -> typed key."""

    def __init__(self, keys: dict) -> None:
        self._keys = keys

    def translate_keyboard_state(self, hardware_keycode: int, state: Any, group: int) -> tuple:
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        keyval = self._keys[hardware_keycode, shift]
        # As GDK reports it: Shift is consumed when it selects the key's level.
        consumed = (Gdk.ModifierType.SHIFT_MASK
                    if shift and keyval != self._keys[hardware_keycode, False]
                    else Gdk.ModifierType(0))
        return True, keyval, group, int(shift), consumed


@pytest.mark.parametrize('hardware_key,modifiers,expected', [
    # AZERTY: Shift+à types 0, so Ctrl+Shift+à is <Control>0.
    ('agrave', '<Control><Shift>', 'zoom_original'),
    ('w', '<Control><Shift>', None),
    ('w under Caps Lock', '<Control><Shift>', None),
    ('space', '<Shift>', 'smart_scroll_up'),
])
def test_key_press_runs_the_binding_with_exactly_its_modifiers(
        shortcuts: Any, monkeypatch: Any, hardware_key: str, modifiers: str,
        expected: Any) -> None:
    keys = {('agrave', False): Gdk.KEY_agrave, ('agrave', True): Gdk.KEY_0,
            ('w', False): Gdk.KEY_w, ('w', True): Gdk.KEY_W,
            ('w under Caps Lock', False): Gdk.KEY_W, ('w under Caps Lock', True): Gdk.KEY_w,
            ('space', False): Gdk.KEY_space, ('space', True): Gdk.KEY_space}
    monkeypatch.setattr(Gdk.Keymap, 'get_default', lambda: _Keymap(keys))
    monkeypatch.setattr(keybindings, '_manager', shortcuts.manager)
    for name, key in [('zoom_original', '<Control>0'), ('close', '<Control>W'),
                      ('smart_scroll_up', '<Shift>space')]:
        shortcuts.manager.register(name, [key], shortcuts.calls.append, args=[name])
    state = Gtk.accelerator_parse(modifiers + 'a')[1]
    pressed = SimpleNamespace(hardware_keycode=hardware_key, group=0,
                              get_state=lambda: state, keyval=keys[hardware_key, False])
    event.EventHandler(shortcuts.window).key_press_event(None, pressed)
    assert shortcuts.calls == ([expected] if expected else [])


def test_first_run_without_keybindings_file_logs_no_error(
        tmp_path: Path, monkeypatch: Any, caplog: Any) -> None:
    monkeypatch.setattr(constants, 'KEYBINDINGS_CONF_PATH', str(tmp_path / 'missing.conf'))
    manager = keybindings._KeybindingManager(SimpleNamespace())
    assert manager.get_bindings_for_action('next_page') == []
    assert caplog.records == []


def test_add_in_empty_editor_cell_survives_reload(shortcuts: Any) -> None:
    shortcuts.manager.clear_accel('next_page', 'F5')
    shortcuts.editor._refresh_action('next_page')
    _edit(shortcuts, 'next_page', 3, 'F8')
    _assert_consistent(shortcuts, {'next_page': ['F2', 'F3', 'F4', 'F8'],
                                   'previous_page': ['F6', 'F7']})
