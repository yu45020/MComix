"""Defensive errors remain useful without claiming an ordinary UI trigger."""

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import gi
import pytest

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('PangoCairo', '1.0')

from mcomix import box, scrolling  # noqa: E402
from ._page_fakes import png  # noqa: E402
from ._shells import image_handler_shell  # noqa: E402


@pytest.mark.parametrize('destination,axis,value', [
    ((2, 0), 0, 2), ((0, -5), 1, -5),
])
def test_invalid_numeric_destination_reports_value_and_axis(
        destination: Any, axis: int, value: int) -> None:
    viewport = box.Box((100, 100), (10, 20))
    with pytest.raises(ValueError) as error:
        scrolling.Scrolling().scroll_to_predefined(
            box.Box((300, 400)), viewport, (1, 1), destination)
    assert str(error.value) == 'invalid destination %s at index %s' % (value, axis)
    assert viewport.get_position() == (10, 20)


def test_wait_diagnostic_formats_resolved_current_page(caplog: Any) -> None:
    handler = image_handler_shell()
    handler._current_image_index = 1
    handler._image_files = ['page-one.png', 'page-two.png']
    handler._available_images = set()
    handler._window = SimpleNamespace(
        filehandler=Mock(get_image_source=Mock(return_value=png((4, 5)))))
    with caplog.at_level(logging.DEBUG, logger='mcomix'):
        assert handler.get_size() == (4, 5)
    handler._window.filehandler._wait_on_file.assert_called_once_with('page-two.png')
    messages = [record.getMessage() for record in caplog.records]
    assert 'Waiting for page 2' in messages
