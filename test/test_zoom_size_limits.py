"""Configured Fit to size limits must not depend on the physical viewport."""

from typing import Tuple

import pytest

from mcomix import constants, zoom
from mcomix.preferences import prefs


@pytest.fixture(autouse=True)
def size_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        'fit to size width wide': 400,
        'fit to size height wide': 600,
        'fit to size width other': 300,
        'fit to size height other': 500,
    }.items():
        monkeypatch.setitem(prefs, name, value)


@pytest.mark.parametrize('viewport,source,axis,expected', [
    ((1800, 1200), (800, 1200), 0, (300, 450)),
    ((1800, 1200), (1200, 800), 1, (450, 300)),
    ((250, 200), (1200, 800), 0, (400, 267)),
])
def test_size_limits_apply_even_when_viewport_has_extra_room(
        viewport: Tuple[int, int], source: Tuple[int, int], axis: int,
        expected: Tuple[int, int], monkeypatch: pytest.MonkeyPatch) -> None:
    if axis == 1:
        monkeypatch.setitem(prefs, 'fit to size width wide', 900)
        monkeypatch.setitem(prefs, 'fit to size height wide', 300)
    model = zoom.ZoomModel()
    model.set_fit_mode(constants.ZoomMode.SIZE)
    sizes, distorted = model.get_zoomed_size(
        [source], viewport, axis, [False], False, False)
    assert sizes == [list(expected)]
    assert distorted == [False]


def test_explicit_user_zoom_still_scales_relative_to_fixed_size() -> None:
    model = zoom.ZoomModel()
    model.set_fit_mode(constants.ZoomMode.SIZE)
    for _ in range(4):
        model.zoom_in()
    assert model.get_zoomed_size(
        [(800, 1200)], (1800, 1200), 0, [False], False, False) == (
            [[600, 900]], [False])
