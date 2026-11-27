"""The title-bar buttons' vector icons resolve at their drawn size."""

import pytest
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk  # noqa: E402

from mcomix import icons  # noqa: E402


@pytest.fixture(scope='module')
def loaded() -> None:
    icons.load_icons()


@pytest.mark.parametrize('name', ['minimize', 'maximize', 'restore', 'close'])
def test_titlebar_icon_loads_at_32px(loaded: None, name: str) -> None:
    pixbuf = Gtk.IconTheme.get_default().load_icon(
        f'mcomix-window-{name}-symbolic', 32, 0)
    assert (pixbuf.get_width(), pixbuf.get_height()) == (32, 32)
