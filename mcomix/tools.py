"""tools.py - Contains various helper functions."""

import bisect
import gc
import itertools
import math
import operator
import os
import re
import sys
from functools import reduce
from typing import Any, List, Mapping, Sequence, TypeVar, Union

Numeric = TypeVar('Numeric', int, float)

NUMERIC_REGEXP = re.compile(r"\d+|\D+")  # Split into numerics and characters
PREFIXED_BYTE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB", "RiB", "QiB")


def cmp(a: str, b: str) -> int:
    """ Forward port of Python2's cmp function """
    return (a > b) - (a < b)


class AlphanumericSortKey:
    """ Compares two strings by their natural order (i.e. 1 before 10) """
    def __init__(self, filename: str) -> None:
        self.filename_parts: List[Union[int, str]] = [
            int(part) if part.isdecimal() else part
            for part in NUMERIC_REGEXP.findall(filename.lower())
        ]

    def __lt__(self, other: 'AlphanumericSortKey') -> bool:
        for left, right in itertools.zip_longest(self.filename_parts, other.filename_parts, fillvalue=''):
            if not isinstance(left, type(right)):
                left_str = str(left)
                right_str = str(right)
                if left_str < right_str:
                    return True
                elif left_str > right_str:
                    return False
            else:
                if left < right:
                    return True
                elif left > right:
                    return False

        return False


def alphanumeric_sort(filenames: List[str]) -> None:
    """Do an in-place alphanumeric sort of the strings in <filenames>,
    such that for an example "1.jpg", "2.jpg", "10.jpg" is a sorted
    ordering.
    """

    filenames.sort(key=AlphanumericSortKey)


def bin_search(lst: List, value: Any) -> int:
    """ Binary search for sorted list C{lst}, looking for C{value}.
    @return: List index on success. On failure, it returns the 1's
    complement of the index where C{value} would be inserted.
    This implies that the return value is non-negative if and only if
    C{value} is contained in C{lst}. """

    index = bisect.bisect_left(lst, value)
    if index != len(lst) and lst[index] == value:
        return index
    else:
        return ~index


def get_home_directory() -> str:
    """Return the user's home folder, e.g. /home/username or C:\\Users\\username."""
    return os.path.expanduser('~')


def get_config_directory() -> str:
    """Return the path to the MComix config directory. On UNIX, this will
    be $XDG_CONFIG_HOME/mcomix, on Windows it will be in %APPDATA%/MComix.

    See http://standards.freedesktop.org/basedir-spec/latest/ for more
    information on the $XDG_CONFIG_HOME environmental variable.
    """
    if sys.platform == 'win32':
        return os.path.join(os.path.expandvars('%APPDATA%'), 'MComix')
    else:
        base_path = os.getenv('XDG_CONFIG_HOME',
                              os.path.join(get_home_directory(), '.config'))
        return os.path.join(base_path, 'mcomix')


def get_data_directory() -> str:
    """Return the path to the MComix data directory. On UNIX, this will
    be $XDG_DATA_HOME/mcomix, on Windows it will be the same directory as
    get_config_directory().

    See http://standards.freedesktop.org/basedir-spec/latest/ for more
    information on the $XDG_DATA_HOME environmental variable.
    """
    if sys.platform == 'win32':
        return os.path.join(os.path.expandvars('%APPDATA%'), 'MComix')
    else:
        base_path = os.getenv('XDG_DATA_HOME',
                              os.path.join(get_home_directory(), '.local/share'))
        return os.path.join(base_path, 'mcomix')


def get_thumbnail_directory() -> str:
    """Return the path to the MComix thumbnail directory. On UNIX, this will
    be in $XDG_CACHE_HOME. On Windows, DATA_DIR will be used. Refer to
    https://specifications.freedesktop.org/thumbnail/latest/directory.html
    for more information.
    """
    if sys.platform == 'win32':
        cache_dir = os.path.join(get_data_directory(), '.thumbnails')
    else:
        cache_dir = os.getenv('XDG_CACHE_HOME',
                              os.path.join(get_home_directory(), '.cache'))
        cache_dir = os.path.join(cache_dir, 'thumbnails')

    return os.path.join(cache_dir, 'normal')


def number_of_digits(n: int) -> int:
    if 0 == n:
        return 1
    return int(math.log10(abs(n))) + 1


def format_byte_size(n: float) -> str:
    e = 0
    while n > 1024.0:
        n /= 1024.0
        e += 1
    return ('%d %s' if n == int(n) else '%.1f %s') % \
        (n, PREFIXED_BYTE_UNITS[min(e, len(PREFIXED_BYTE_UNITS) - 1)])


def garbage_collect() -> None:
    """ Runs the garbage collector. """
    gc.collect(0)


def div(a: Numeric, b: Numeric) -> float:
    return float(a) / float(b)


def volume(t: List[int]) -> int:
    return reduce(operator.mul, t, 1)


def relerr(approx: Numeric, ideal: Numeric) -> float:
    return abs(div(approx - ideal, ideal))


def smaller(a: List, b: List) -> List:
    """ Returns a list with the i-th element set to True if and only if the i-th
    element in a is less than the i-th element in b. """
    return list(map(operator.lt, a, b))


def smaller_or_equal(a: List, b: List) -> List:
    """ Returns a list with the i-th element set to True if and only if the i-th
    element in a is less than or equal to the i-th element in b. """
    return list(map(operator.le, a, b))


def scale(t: Sequence[Numeric], factor: Numeric) -> List[Numeric]:
    return [x * factor for x in t]


def vector_sub(a: List[Numeric], b: List[Numeric]) -> List[Numeric]:
    """ Subtracts vector b from vector a. """
    return list(map(operator.sub, a, b))


def vector_add(a: List[Numeric], b: List[Numeric]) -> List[Numeric]:
    """ Adds vector a to vector b. """
    return list(map(operator.add, a, b))


def vector_opposite(a: List[Numeric]) -> List[Numeric]:
    """ Returns the opposite vector -a. """
    return list(map(operator.neg, a))


def remap_axes(vector, order):
    return [vector[i] for i in order]


def compile_rotations(*rotations: int) -> int:
    """Compose rotations into a single clockwise angle below one full turn."""
    return sum(rotations) % 360


def rotation_swaps_axes(rotation: int) -> bool:
    return rotation in (90, 270)


def formats_to_regex(formats: Mapping) -> re.Pattern:
    """ Returns a compiled regular expression that can be used to search for
    file extensions specified in C{formats}. """
    extensions = set(itertools.chain.from_iterable([e[1] for e in formats.values()]))
    return re.compile(r'\.(%s)$' % '|'.join(sorted(map(re.escape, extensions))), re.I)


def append_number_to_filename(filename: str, number: int) -> str:
    """ Generate a new string from filename with an appended number right
    before the extension. """
    file_no_ext = os.path.splitext(filename)[0]
    ext = os.path.splitext(filename)[1]
    return file_no_ext + (" (%s)" % (number)) + ext

# vim: expandtab:sw=4:ts=4
