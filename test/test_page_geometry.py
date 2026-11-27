"""Representative hand-derived zoom, layout and box geometry rules."""

from typing import List, Tuple

import pytest

from mcomix import box, constants, layout, zoom


def _model(mode: int = constants.ZoomMode.BEST, scale_up: bool = False) -> zoom.ZoomModel:
    model = zoom.ZoomModel()
    model.set_fit_mode(mode)
    model.set_scale_up(scale_up)
    return model


@pytest.mark.parametrize('mode,scale_up,source,expected', [
    (constants.ZoomMode.BEST, False, (800, 1200), (267, 400)),
    (constants.ZoomMode.WIDTH, False, (800, 1200), (600, 900)),
    (constants.ZoomMode.MANUAL, False, (800, 1200), (800, 1200)),
    (constants.ZoomMode.BEST, False, (100, 200), (100, 200)),
    (constants.ZoomMode.BEST, True, (100, 200), (200, 400)),
])
def test_single_page_fit_and_stretch(
        mode: int, scale_up: bool, source: Tuple[int, int], expected: Tuple[int, int]) -> None:
    assert _model(mode, scale_up).get_zoomed_size(
        [source], (600, 400), 0, [False], False, False) == ([list(expected)], [False])


def test_user_zoom_is_relative_bounded_and_resettable() -> None:
    model = zoom.ZoomModel()
    args = ([(80, 120)], (600, 400), 0, [False], False, False)
    for _ in range(4):
        model.zoom_in()
    # Four user steps double native size even with automatic stretch disabled.
    assert model.get_zoomed_size(*args)[0] == [[160, 240]]
    for _ in range(100):
        model.zoom_out()
    assert model.get_zoomed_size(*args)[0] == [[2, 4]]
    model.reset_user_zoom()
    assert model.get_zoomed_size(*args)[0] == [[80, 120]]


def test_double_page_equal_height_and_native_neighbor() -> None:
    assert _model().get_zoomed_size(
        [(100, 200), (200, 400)], (600, 400), 0, [False, False], True, False) == (
            [[100, 200], [100, 200]], [False, False])
    # A native page keeps its width; only its neighbour is scaled.
    assert _model().get_zoomed_size(
        [(100, 200), (100, 200)], (150, 200), 0, [True, False], False, False) == (
            [[100, 200], [50, 100]], [False, False])


def test_layout_retains_page_identity_in_manga_order() -> None:
    sizes: List[Tuple[int, int]] = [(100, 200), (200, 100)]
    result = layout.FiniteLayout(sizes, [False, True], (400, 300),
                                 (-1, 1), 10, False, 0, 1)
    assert [b.get_position() for b in result.get_content_boxes()] == [(255, 50), (45, 100)]
    assert [b.get_size() for b in result.get_content_boxes()] == sizes
    assert result.get_content_distorted() == [False, True]
    assert result.get_union_box() == box.Box((400, 300))


def test_layout_refits_after_vertical_scrollbar_reduces_available_width() -> None:
    model = _model(constants.ZoomMode.WIDTH)
    requests = []
    screens = []

    def sizes(screen: Tuple[int, int]) -> Tuple[List[List[int]], List[bool]]:
        screens.append(tuple(screen))
        return model.get_zoomed_size([(600, 1200)], screen, 0, [False], False, False)

    result = layout.FiniteLayout.create_finite_layout(
        1, (1, 1), 0, 0, 1, lambda value: requests.append(tuple(value)),
        lambda: (285 if requests[-1][1] else 300, 200), sizes)
    assert screens == [(300, 200), (285, 200)]
    assert requests == [(False, False), (False, True), (False, True)]
    assert result.get_content_boxes() == [box.Box((285, 570))]


def test_box_distance_and_center_follow_pixel_and_reading_rules() -> None:
    page = box.Box((3, 2), (10, 20))
    assert [page.distance_point_squared(p) for p in [(12, 21), (13, 21), (14, 23)]] == [0, 1, 8]
    assert box.Box((4, 2), (10, 20)).get_center((1, 1)) == [11, 20]
    assert box.Box((4, 2), (10, 20)).get_center((-1, -1)) == [12, 21]
