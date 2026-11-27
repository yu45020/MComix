""" A series of a few simple linear transforms. """

# Allow class name in annotations while still defining class
from __future__ import annotations

from typing import Tuple


class Matrix:
    """Simple linear transformations represented as a 2x2 matrix.

    Note that the current implementation does not support arbitrary
    matrices. Instead, the predefined Matrix objects and conversion
    functions in the Transform class should be used to create Matrix
    objects."""

    def __init__(self, m1: float, m2: float, m3: float, m4: float) -> None:
        """Initialize a row-major transformation matrix."""
        self.m: Tuple[float, float, float, float] = (m1, m2, m3, m4)

    def __add__(self, other: object) -> Matrix:
        """Combine Matrices: self + other."""
        if not isinstance(other, Matrix):
            return NotImplemented
        a = self.m
        b = other.m
        return Matrix(
            b[0] * a[0] + b[1] * a[2],
            b[0] * a[1] + b[1] * a[3],
            b[2] * a[0] + b[3] * a[2],
            b[2] * a[1] + b[3] * a[3],
        )

    def swaps_axes(self) -> bool:
        """ Determines whether this matrix includes a transpose of the axes. """
        return self.m[0] == 0

    def to_image_transforms(self) -> Tuple[int, Tuple[bool, bool]]:
        """ Decomposes the transform to hints to basic transform instructions
        typically found in image processing libraries: at most one rotation,
        followed by at most one flip.
        @return a tuple (r, f) where r is one of (0, 90, 180, 270), referring
        to the clockwise rotation to be applied, and f is a sequence of bools
        where True refers to the corresponding axis to be flipped. """
        r: int = 90 if self.swaps_axes() else 0
        f: Tuple[bool, bool] = (
            self.swaps_axes() ^ (self.m[0] < 0 or self.m[1] < 0),
            (self.m[2] < 0 or self.m[3] < 0)
        )
        if all(f):
            f = (False, False)
            r += 180
        if f[0] and r == 90:
            # Try to avoid horizontal flips because they tend to be slow, given
            # a certain combination of hardware, memory layout and libraries.
            f = (False, True)
            r = 270
        return (r, f)


class Transform(Matrix):

    ID = Matrix(1, 0, 0, 1)
    ROT90 = Matrix(0, -1, 1, 0)
    ROT180 = Matrix(-1, 0, 0, -1)
    ROT270 = Matrix(0, 1, -1, 0)
    INVX = Matrix(-1, 0, 0, 1)
    INVY = Matrix(1, 0, 0, -1)

    @classmethod
    def from_rotation(cls, deg: int) -> Matrix:
        """Get the predefined Matrix for a supported rotation."""
        if abs(deg) not in (0, 90, 180, 270):
            raise ValueError("illegal rotation angle: " + str(deg))
        if deg < 0: deg = 360 + deg
        if deg == 90:
            return cls.ROT90
        if deg == 180:
            return cls.ROT180
        if deg == 270:
            return cls.ROT270
        return cls.ID

    @classmethod
    def from_flips(cls, x: bool, y: bool) -> Matrix:
        """Get the predefined Matrix representing the given x and y flips."""
        if x and y:
            return cls.ROT180
        if x:
            return cls.INVX
        if y:
            return cls.INVY
        return cls.ID

# vim: expandtab:sw=4:ts=4
