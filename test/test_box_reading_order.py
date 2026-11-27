"""Unequal page sizes must not change the reading-order tie breaker."""

from typing import Tuple

import pytest

from mcomix import box, constants, layout, zoom


@pytest.mark.parametrize('axis,direction,reverse_input', [
    (0, -1, False), (1, -1, True), (0, 1, True)])
def test_equidistant_boxes_follow_reading_origin(
        axis: int, direction: int, reverse_input: bool) -> None:
    # Pixel 400 is the one-pixel gap: the nearest pixel of either box is
    # exactly one pixel away. The large right/bottom box starts at 401.
    small_size = [400, 20]
    large_size = [1000, 20]
    large_position = [401, 0]
    point = [400, 9]
    orientation = [direction, 1]
    if axis == 1:
        small_size.reverse()
        large_size.reverse()
        large_position.reverse()
        point.reverse()
        orientation.reverse()
    small = box.Box(small_size)
    large = box.Box(large_size, large_position)
    boxes = [large, small] if reverse_input else [small, large]
    expected = small if direction == 1 else large
    assert small.distance_point_squared(point) == 1
    assert large.distance_point_squared(point) == 1
    assert box.Box.closest_boxes(point, boxes, orientation) == [
        boxes.index(expected)]


def test_native_manga_layout_selects_earlier_page_at_gap() -> None:
    model = zoom.ZoomModel()
    sizes = [(1000, 1600), (400, 1600)]
    viewport: Tuple[int, int] = (301, 600)
    result = layout.FiniteLayout.create_finite_layout(
        2, constants.MANGA_ORIENTATION, 1, 0, 1,
        lambda requests: None, lambda: viewport,
        lambda screen: model.get_zoomed_size(
            sizes, screen, 0, [False, False], True, False))
    assert result.wrap_individually
    assert [(b.get_position(), b.get_size())
            for b in result.get_content_boxes()] == [
                ((401, 0), (1000, 1600)), ((0, 0), (400, 1600))]
    result.set_viewport_position((250, 0))
    assert result.get_viewport_box().get_center(
        constants.MANGA_ORIENTATION) == [400, 299]
    # Smart scrolling uses this page index to choose the current wrapper.
    assert result.get_current_index() == 0
