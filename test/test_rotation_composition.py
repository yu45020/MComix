"""Rotation composition stays valid across EXIF, automatic and manual turns."""

from typing import Tuple

import pytest
from PIL import Image

from mcomix import image_tools, tools
from mcomix.transform import Transform


@pytest.mark.parametrize('rotations,expected', [
    ((90, 270), 0), ((270, 270), 180), ((-450, 810), 0),
    ((270, 270, 270, 270, 270), 270),
])
def test_normalize_entire_sum(rotations: Tuple[int, ...], expected: int) -> None:
    assert tools.compile_rotations(*rotations) == expected


# Combinations whose plain sum leaves 0-270 (each failed before rotations
# were normalized).
@pytest.mark.parametrize('exif,auto,manual', [
    (90, 0, 270), (180, 90, 180), (270, 270, 270)])
def test_three_stage_composition_matches_real_pixel_rotation(
        exif: int, auto: int, manual: int) -> None:
    source = Image.new('RGB', (2, 3))
    source.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255),
                    (255, 255, 0), (0, 255, 255), (255, 0, 255)])
    pixbuf = image_tools.pil_to_pixbuf(source)
    page_angle = tools.compile_rotations(auto, manual)
    angle = tools.compile_rotations(exif, page_angle)
    # Pillow's positive direction is counterclockwise; MComix's is clockwise.
    expected = source.rotate(-(exif + auto + manual), expand=True)
    target = expected.size
    result = image_tools.pixbuf_to_pil(
        image_tools.fit_pixbuf_to_rectangle(pixbuf, target, angle))
    assert result.size == expected.size
    assert result.tobytes() == expected.tobytes()
    assert tools.rotation_swaps_axes(angle) == (expected.size != source.size)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('horizontal,vertical', [
    (False, False), (True, False), (False, True), (True, True)])
def test_magnifier_transform_matches_main_view(
        rotation: int, horizontal: bool, vertical: bool) -> None:
    source = Image.new('RGB', (2, 3))
    source.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255),
                    (255, 255, 0), (0, 255, 255), (255, 0, 255)])
    pixbuf = image_tools.pil_to_pixbuf(source)
    # The main view rotates, then flips horizontally, then vertically, and
    # records the same steps in the matrix the magnifier decomposes.
    shown = image_tools.rotate_pixbuf(pixbuf, rotation)
    matrix = Transform.ID + Transform.from_rotation(rotation)
    if horizontal:
        shown = image_tools.flip_pixbuf(shown, 0)
        matrix += Transform.from_flips(True, False)
    if vertical:
        shown = image_tools.flip_pixbuf(shown, 1)
        matrix += Transform.from_flips(False, True)
    lens_rotation, flips = matrix.to_image_transforms()
    magnified = image_tools.rotate_pixbuf(pixbuf, lens_rotation)
    for axis, flip in enumerate(flips):
        if flip:
            magnified = image_tools.flip_pixbuf(magnified, axis)
    assert (image_tools.pixbuf_to_pil(magnified).tobytes()
            == image_tools.pixbuf_to_pil(shown).tobytes())
