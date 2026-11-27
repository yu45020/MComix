"""Representative smart-scroll reading order, endpoints and preference routing."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from mcomix import box, constants, event, layout, scrolling
from mcomix.preferences import prefs


def test_manga_reading_route_visits_each_panel_then_stops() -> None:
    scroller = scrolling.Scrolling()
    content = box.Box((300, 250), (20, 30))
    viewport = box.Box((100, 100), (220, 30))
    positions = []
    while len(positions) < 20:
        position = scroller.scroll_smartly(content, viewport, (-1, 1), (100, 100))
        if not position:
            break
        positions.append(tuple(position))
        viewport = viewport.set_position(position)
    assert positions == [(120, 30), (20, 30), (220, 105), (120, 105),
                         (20, 105), (220, 180), (120, 180), (20, 180)]


def test_off_content_viewport_returns_to_reading_edge() -> None:
    assert scrolling.Scrolling().scroll_smartly(
        box.Box((300, 250), (20, 30)), box.Box((100, 100), (500, 500)),
        (-1, -1), (100, 100)) == [220, 180]


def test_predefined_destinations_respect_manga_and_odd_centers() -> None:
    scroller = scrolling.Scrolling()
    content = box.Box((301, 251), (20, 30))
    viewport = box.Box((100, 100), (67, 89))
    for destination, expected in [
            (constants.SCROLL_TO_START, [221, 30]), (constants.SCROLL_TO_END, [20, 181]),
            (constants.SCROLL_TO_CENTER, [121, 105])]:
        assert scroller.scroll_to_predefined(
            content, viewport, (-1, 1), (destination, destination)) == expected
    assert scroller.scroll_to_predefined(content, viewport, (-1, 1), (0, 0)) == [67, 89]


def _window(model: layout.FiniteLayout, **extra: Any) -> Any:
    return SimpleNamespace(layout=model, get_visible_area_size=lambda: (100, 100),
                           update_layout_position=Mock(), displayed_double=lambda: False,
                           update_viewport_position=Mock(), **extra)


@pytest.mark.parametrize('invert', [False, True])
def test_event_preferences_scroll_interior_without_turning_page(
        monkeypatch: Any, invert: bool) -> None:
    monkeypatch.setitem(prefs, 'smart scroll percentage', 0.5)
    monkeypatch.setitem(prefs, 'invert smart scroll', invert)
    model = layout.FiniteLayout([(500, 500)], [False], (100, 100), (-1, 1), 0, False, 0, 1)
    model.set_viewport_position((200, 200))
    window = _window(model)
    handler = event.EventHandler(window)
    handler._next_page_with_protection = Mock()
    handler._smart_scrolling(None, False)
    assert model.get_viewport_box().get_position() == ((200, 250) if invert else (150, 200))
    window.update_viewport_position.assert_called_once_with()
    handler._next_page_with_protection.assert_not_called()


def test_endpoint_turns_page_after_protection_attempts(monkeypatch: Any) -> None:
    monkeypatch.setitem(prefs, 'flip with wheel', True)
    monkeypatch.setitem(prefs, 'number of key presses before page turn', 3)
    model = layout.FiniteLayout([(300, 300)], [False], (100, 100), (1, 1), 0, False, 0, 1)
    model.set_viewport_position((200, 200))
    window = _window(model, flip_page=Mock(), is_scrollable=lambda: True)
    handler = event.EventHandler(window)
    handler._scroll_protection = True
    for _ in range(2):
        handler._smart_scrolling(20, False)
        window.flip_page.assert_not_called()
    handler._smart_scrolling(20, False)
    window.flip_page.assert_called_once_with(1, single_step=False)
    assert model.get_viewport_box().get_position() == (200, 200)
    window.update_viewport_position.assert_not_called()
