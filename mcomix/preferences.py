""" preferences.py - Contains the preferences and the functions to read and
write them.  """

import json
import os

from mcomix import constants
from mcomix.atomic_file import atomic_write

# All the preferences are stored here.
prefs = {
    'comment extensions': constants.ACCEPTED_COMMENT_EXTENSIONS,
    'auto load last file': False,
    'page of last file': 1,
    'path to last file': '',
    'image of last file': '',
    'number of key presses before page turn': 3,
    'auto open next archive': True,
    'auto open next directory': True,
    'open first file in prev archive': False,
    'open first file in prev directory': False,
    'sort by': constants.SORT_NAME,  # Normal files obtained by directory listing
    'sort order': constants.SORT_ASCENDING,
    'sort archive by': constants.SORT_NAME,  # Files in archives
    'sort archive order': constants.SORT_ASCENDING,
    'bg colour': [5000, 5000, 5000],
    'thumb bg colour': [5000, 5000, 5000],
    'smart bg': False,
    'smart thumb bg': False,
    'checkered bg for transparent images': True,
    'stretch': False,
    'default double page': False,
    'default fullscreen': False,
    'zoom mode': constants.ZoomMode.BEST,
    'default manga mode': False,
    'lens magnification': 2,
    'lens size': 200,
    'virtual double page for fitting images': constants.SHOW_DOUBLE_AS_ONE_TITLE | \
                                              constants.SHOW_DOUBLE_AS_ONE_WIDE,
    'double step in double page mode': True,
    'show page numbers on thumbnails': True,
    'thumbnail size': 80,
    'create thumbnails': True,
    'archive thumbnail as icon' : False,
    'number of pixels to scroll per key event': 50,
    'number of pixels to scroll per mouse wheel event': 50,
    'slideshow delay': 3000,
    'slideshow can go to next archive': True,
    'number of pixels to scroll per slideshow event': 50,
    'smart scroll': True,
    'invert smart scroll': False,
    'smart scroll percentage': 0.5,
    'flip with wheel': True,
    'store recent file info': True,
    'hide all': False,
    'hide all in fullscreen': True,
    'path of last browsed in filechooser': constants.HOME_DIR,
    'store last saved in directory': True,
    'path of last saved in filechooser': constants.HOME_DIR,
    'last filter in main filechooser': 0,
    'last filter in library filechooser': 0,
    'show menubar': True,
    'previous quit was quit and save': False,
    'show scrollbar': True,
    'show statusbar': True,
    'show toolbar': True,
    'show thumbnails': True,
    'rotation': 0,
    'auto rotate from exif': True,
    'auto rotate depending on size': constants.AUTOROTATE_NEVER,
    'vertical flip': False,
    'horizontal flip': False,
    'keep transformation': False,
    'stored dialog choices': {},
    'brightness': 1.0,
    'contrast': 1.0,
    'saturation': 1.0,
    'sharpness': 1.0,
    'auto contrast': False,
    'invert color': False,
    'max pages to cache': 7,
    'window x': 0,
    'window y': 0,
    'window height': 600,
    'window width': 640,
    'window maximized': False,
    'pageselector height': -1,
    'pageselector width': -1,
    'library cover size': 125,
    'last library collection': None,
    'lib window height': 600,
    'lib window width': 500,
    'lib sort key': constants.SORT_PATH,
    'lib sort order': constants.SORT_ASCENDING,
    'language': 'auto',
    'statusbar fields': constants.STATUS_PAGE | constants.STATUS_RESOLUTION | \
                        constants.STATUS_PATH | constants.STATUS_FILENAME | constants.STATUS_FILESIZE,
    'max threads': 3,
    'max extract threads': 1,
    'lazy archive reading': True,
    'wrap mouse scroll': False,
    'scaling quality': 2,  # GdkPixbuf.InterpType.BILINEAR
    'neural upscaling': False,
    'neural upscaling model': '',  # ONNX model path; '' = bundled default
    'neural upscaling max input size': 2048,  # Only pages smaller than this
    'neural upscaling always': False,  # Also when the page is not enlarged
    'neural upscaling runtime': 'auto',  # 'auto' (faster Windows ML provider if any) or 'directml'
    'escape quits': False,
    'fit to size width wide': 3790,
    'fit to size height wide': 960,
    'fit to size width other': 1450,
    'fit to size height other': 1800,
    'scan for new books on library startup': True,
    'openwith commands': [],  # (label, command) pairs
    'animation mode': constants.ANIMATION_NORMAL,
    'animation cache limit': 128,  # MiB shared by visible WebP animations
    'enlarge animated webp': True,
    'animated webp max size': 2048,  # Longest display edge in pixels; 0 is unlimited
    'double page autoresize': constants.DOUBLE_PAGE_AUTORESIZE_SIZE,
    'space between two pages': 2,
}


#: Loaded values outside these ranges (the Preferences spinners' limits)
#: break scrolling, so the default is kept instead.
_RANGES = {
    'smart scroll percentage': (0.01, 1.0),
    'number of pixels to scroll per mouse wheel event': (1, 500),
}

#: False when preferences.conf couldn't be read; the run then keeps it.
_file_readable = True


def read_preferences_file() -> None:
    """Read preferences data from disk."""
    global _file_readable
    path = constants.PREFERENCE_PATH
    if not os.path.isfile(path):
        return
    # Gettext might not be installed yet at this point, so messages are
    # printed untranslated.
    try:
        with open(path, 'rb') as config_file:
            text = config_file.read()
    except OSError as error:
        print('! Could not read preferences file "%s": %s' % (path, error))
        _file_readable = False
        return
    try:
        saved_prefs = json.loads(text)
    except ValueError:
        saved_prefs = None
    if not isinstance(saved_prefs, dict):
        corrupt_name = path + '.broken'
        print('! Corrupt preferences file, moving to "%s".' % corrupt_name)
        try:
            os.replace(path, corrupt_name)
        except OSError as error:
            print('! Could not move preferences file: %s' % error)
            _file_readable = False
        return

    for key, value in saved_prefs.items():
        if key not in prefs:
            continue
        if key in _RANGES:
            low, high = _RANGES[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not low <= value <= high:
                continue
        prefs[key] = value


def write_preferences_file():
    """Write preference data to disk, unless the file couldn't be read."""
    if not _file_readable:
        return
    # TODO: it might be better to save only those options that were (ever)
    # explicitly changed by the used, leaving everything else as default
    # and available (if really needed) to change of defaults on upgrade.
    # XXX: constants.VERSION? It's *preferable* to not complicate the YAML
    # file by adding a `{'version': constants.VERSION, 'prefs': config}`
    # dict or a list.  Adding an extra init line sounds bad too.
    with atomic_write(constants.PREFERENCE_PATH) as config_file:
        json.dump(prefs, config_file, indent=2)

# vim: expandtab:sw=4:ts=4
