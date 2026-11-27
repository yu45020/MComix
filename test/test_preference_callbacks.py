"""Preferences dialog widgets store their preference and apply it at once."""
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import gi
import pytest

gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

from mcomix import constants, upscale  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402
from mcomix.preferences_dialog import _PreferencesDialog as Dialog  # noqa: E402

DRAW = ['draw_image']
REFRESH = ['filehandler.refresh_file']
UPSCALER = ['imagehandler.upscaler.configure', 'draw_image']


def window_calls(window: Mock) -> list:
    return [name for name, args, kwargs in window.mock_calls]


@pytest.fixture
def dialog() -> Any:
    result = SimpleNamespace(_window=Mock())
    result._combobox_cb = partial(Dialog._combobox_cb, result)
    result._store_recent_changed = partial(Dialog._store_recent_changed, result)
    return result


@pytest.mark.parametrize('preference,values,effect,only_if_changed', [
    ('language', ('en', 'ja'), [], False),
    ('virtual double page for fitting images', (0, constants.SHOW_DOUBLE_AS_ONE_TITLE),
     ['imagehandler.do_cacheing', 'draw_image'], False),
    ('double page autoresize', (constants.DOUBLE_PAGE_AUTORESIZE_SCALE,
                                constants.DOUBLE_PAGE_AUTORESIZE_SIZE), DRAW, False),
    ('sort by', (0, constants.SORT_NAME), REFRESH, False),
    ('store recent file info', (False, True), ['filehandler.last_read_page.set_enabled'], False),
    ('scaling quality', (int(GdkPixbuf.InterpType.TILES),
                         int(GdkPixbuf.InterpType.BILINEAR)), DRAW, True),
    ('animation mode', (constants.ANIMATION_DISABLED, constants.ANIMATION_NORMAL), REFRESH, True),
    ('neural upscaling runtime', (upscale.RUNTIME_AUTO, upscale.RUNTIME_DIRECTML), UPSCALER, True),
])
def test_combobox_stores_value_and_applies_it(
        dialog: Any, monkeypatch: pytest.MonkeyPatch, preference: str,
        values: tuple, effect: list, only_if_changed: bool) -> None:
    monkeypatch.setitem(prefs, preference, values[0])
    combo = Dialog._create_combobox(dialog, (('first', values[0]), ('second', values[1])),
                                    preference)
    assert combo.get_active() == 0
    combo.set_active(1)
    assert prefs[preference] == values[1]
    assert window_calls(dialog._window) == effect
    # Selecting the stored value again.
    Dialog._combobox_cb(dialog, combo, preference)
    assert window_calls(dialog._window) == effect * (1 if only_if_changed else 2)


@pytest.mark.parametrize('preference,value,stored,effect', [
    ('lens size', 100, 100, []),
    ('lens magnification', 2.5, 2.5, []),
    ('slideshow delay', 3.5, 3500, ['slideshow.update_delay']),
    ('smart scroll percentage', 50, 0.5, []),
    ('thumbnail size', 80, 80, ['thumbnailsidebar.resize', 'draw_image']),
    ('max pages to cache', 7, 7, ['imagehandler.do_cacheing']),
    ('animation cache limit', 64, 64, DRAW),
    ('animated webp max size', 2048, 2048, DRAW),
    ('number of key presses before page turn', 3, 3, []),
    ('fit to size width wide', 900, 900, ['change_zoom_mode']),
    ('space between two pages', 5, 5, ['update_space']),
    ('neural upscaling max input size', 1024, 1024, UPSCALER),
])
def test_spinner_stores_value_and_applies_it(
        dialog: Any, monkeypatch: pytest.MonkeyPatch, preference: str,
        value: float, stored: Any, effect: list) -> None:
    monkeypatch.setitem(prefs, preference, prefs[preference])
    dialog._window._event_handler._extra_scroll_events = 2
    spinner = Gtk.SpinButton.new_with_range(0, 10000, 0.1)
    spinner.set_value(value)
    Dialog._spinner_cb(dialog, spinner, preference)
    assert prefs[preference] == stored and type(prefs[preference]) is type(stored)
    assert window_calls(dialog._window) == effect
    expected_extra = 0 if preference == 'number of key presses before page turn' else 2
    assert dialog._window._event_handler._extra_scroll_events == expected_extra


@pytest.mark.parametrize('preference,active,effect,cleared', [
    ('color box bg', True, ['set_bg_colour'], None),
    ('color box bg', False, [], None),
    ('smart bg', True, DRAW, None),
    ('smart bg', False, [], None),
    ('color box thumb bg', True,
     ['thumbnailsidebar.change_thumbnail_background_color'], 'smart thumb bg'),
    ('color box thumb bg', False, [], None),
    ('smart thumb bg', True,
     ['thumbnailsidebar.change_thumbnail_background_color'], 'color box thumb bg'),
    ('smart thumb bg', False, [], None),
    ('checkered bg for transparent images', False, DRAW, None),
    ('auto rotate from exif', False, ['imagehandler.do_cacheing', 'draw_image'], None),
    ('enlarge animated webp', False, DRAW, None),
    ('hide all in fullscreen', False, DRAW, None),
    ('neural upscaling', False, UPSCALER, None),
    ('neural upscaling always', False, UPSCALER, None),
    ('show page numbers on thumbnails', False,
     ['thumbnailsidebar.toggle_page_numbers_visible'], None),
    ('archive thumbnail as icon', False, ['update_icon'], None),
])
def test_check_button_stores_value_and_applies_it(
        dialog: Any, monkeypatch: pytest.MonkeyPatch, preference: str,
        active: bool, effect: list, cleared: Any) -> None:
    for key in (preference, 'smart thumb bg', 'color box thumb bg'):
        monkeypatch.setitem(prefs, key, True)
    monkeypatch.setitem(prefs, 'smart bg', False)
    window = dialog._window
    window.displayed_double.return_value = False
    # Page queries are not effects; the background they yield is.
    window.imagehandler.page_is_available.return_value = True
    window.imagehandler.get_pixbuf_auto_background.return_value = (1, 2, 3)
    queries = ('displayed_double', 'imagehandler.page_is_available',
               'imagehandler.get_pixbuf_auto_background')
    button = Gtk.CheckButton()
    button.set_active(active)
    Dialog._check_button_cb(dialog, button, preference)
    assert prefs[preference] is active
    assert [name for name in window_calls(window) if name not in queries] == effect
    if preference == 'smart thumb bg' and active:
        window.thumbnailsidebar.change_thumbnail_background_color.assert_called_once_with(
            (1, 2, 3))
    if cleared is not None:
        assert prefs[cleared] is False
