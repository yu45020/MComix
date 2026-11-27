# -*- coding: utf-8 -*-
""" file_provider.py - Handles listing files for the current directory and
    switching to the next/previous directory. """

import os
from typing import Optional, Sequence
from gi.repository import GLib

from mcomix import image_tools
from mcomix import archive_tools
from mcomix import tools
from mcomix import constants
from mcomix import preferences
from mcomix import i18n
from mcomix import log
from mcomix.i18n import _


def find_path(paths: Sequence[str], path: str) -> Optional[int]:
    """Find a normalized native path, retaining case sensitivity on POSIX."""
    key = os.path.normcase(os.path.abspath(path))
    return next((index for index, candidate in enumerate(paths)
                 if os.path.normcase(os.path.abspath(candidate)) == key), None)


def get_file_provider(filelist):
    """ Initialize a FileProvider with the files in the non-empty <filelist>.
    If len(filelist) is 1, a OrderedFileProvider will be constructed, which
    will simply open all files in the passed directory, or None if the path
    doesn't exist.
    If len(filelist) is greater 1, a PreDefinedFileProvider will be created,
    which will only ever list the files that were passed into it. """

    if len(filelist) > 1:
        return PreDefinedFileProvider(filelist)
    if os.path.exists(filelist[0]):
        return OrderedFileProvider(filelist[0])
    return None

class FileProvider(object):
    """ Base class for various file listing strategies. """

    # Constants for determining which files to list.
    IMAGES, ARCHIVES = 1, 2

    def next_directory(self):
        return False

    def previous_directory(self):
        return False

    @staticmethod
    def sort_files(files):
        """ Sorts a list of C{files} depending on the current preferences.
        The list is sorted in-place. """
        if preferences.prefs['sort by'] == constants.SORT_NAME:
            tools.alphanumeric_sort(files)
        elif preferences.prefs['sort by'] == constants.SORT_NAME_GLIB:
            files.sort(key=lambda filename: GLib.utf8_collate_key_for_filename(os.path.basename(filename), -1))
        elif preferences.prefs['sort by'] == constants.SORT_LAST_MODIFIED:
            # Most recently modified file first
            files.sort(key=lambda filename: os.path.getmtime(filename)*-1)
        elif preferences.prefs['sort by'] == constants.SORT_SIZE:
            # Smallest file first
            files.sort(key=lambda filename: os.stat(filename).st_size)
        # else: don't sort at all: use OS ordering.

        # Default is ascending.
        if preferences.prefs['sort order'] == constants.SORT_DESCENDING:
            files.reverse()


class OrderedFileProvider(FileProvider):
    """List images in the directory tree and archives in its top directory."""

    def __init__(self, file_or_directory):
        """ Initializes the file listing. If <file_or_directory> is a file,
            directory will be used as base path. If it is a directory, that
            will be used as base file. """

        self.set_directory(file_or_directory)

    def set_directory(self, file_or_directory):
        """ Sets the base directory. """

        if os.path.isdir(file_or_directory):
            dir = file_or_directory
        elif os.path.isfile(file_or_directory):
            dir = os.path.dirname(file_or_directory)
        else:
            # Passed file doesn't exist
            raise ValueError(_("Invalid path: '%s'") % file_or_directory)

        self.base_dir = os.path.abspath(dir)

    def get_directory(self):
        return self.base_dir

    def list_files(self, mode=FileProvider.IMAGES):
        """List files for the current directory, in display order."""

        if mode == FileProvider.IMAGES:
            return self._list_images_recursively(self.base_dir, image_tools.is_image_file)

        try:
            files = [os.path.join(self.base_dir, i18n.to_unicode(filename))
                     for filename in os.listdir(self.base_dir)]
            files = [filename for filename in files if archive_tools.is_archive_file(filename)]
            FileProvider.sort_files(files)
            return files
        except OSError:
            log.warning('! ' + _('Could not open %s: Permission denied.'), self.base_dir)
            return []

    def _list_images_recursively(self, directory, should_accept):
        """List images depth first, sorting files and folders together."""
        try:
            entries = [os.path.join(directory, i18n.to_unicode(filename))
                       for filename in os.listdir(directory)]
            FileProvider.sort_files(entries)
        except OSError:
            log.warning('! ' + _('Could not open %s: Permission denied.'), directory)
            return []

        files = []
        for entry in entries:
            if os.path.isdir(entry) and not os.path.islink(entry):
                files.extend(self._list_images_recursively(entry, should_accept))
            elif should_accept(entry):
                files.append(entry)
        return files

    def next_directory(self):
        """ Switches to the next sibling directory. Next call to
            list_file() returns files in the new directory.
            Returns True if the directory was changed, otherwise False. """

        return self._change_directory(1)

    def previous_directory(self):
        """ Switches to the previous sibling directory. Next call to
            list_file() returns files in the new directory.
            Returns True if the directory was changed, otherwise False. """

        return self._change_directory(-1)

    def _change_directory(self, step: int) -> bool:
        directories = self.__get_sibling_directories(self.base_dir)
        current_index = find_path(directories, self.base_dir)
        if current_index is None:
            return False
        target = current_index + step
        if not 0 <= target < len(directories):
            return False
        self.base_dir = directories[target]
        return True

    def __get_sibling_directories(self, directory: str) -> list:
        """Return naturally ordered siblings, or no candidates at a root/error."""
        parent_dir = os.path.dirname(directory)
        if os.path.normcase(parent_dir) == os.path.normcase(directory):
            return []
        try:
            directories = [os.path.join(parent_dir, sibling)
                           for sibling in os.listdir(parent_dir)
                           if os.path.isdir(os.path.join(parent_dir, sibling))]
        except OSError:
            return []
        tools.alphanumeric_sort(directories)
        return directories


class PreDefinedFileProvider(FileProvider):
    """ Returns only a list of files as passed to the constructor. """

    def __init__(self, files):
        """ <files> is a list of files that should be shown. The list is filtered
            to contain either only images (a folder adds its images), or only archives,
            depending on what the first path is, since FileHandler will probably have
            problems if archives and images are mixed in a file list. """

        self.__first = os.path.abspath(files[0])
        archives = os.path.isfile(files[0]) and archive_tools.is_archive_file(files[0])
        should_accept = archive_tools.is_archive_file if archives else image_tools.is_image_file

        self.__files = [ ]

        for file in files:
            if os.path.isdir(file):
                if not archives:
                    provider = OrderedFileProvider(file)
                    self.__files.extend(provider.list_files())

            elif should_accept(file):
                self.__files.append(os.path.abspath(file))

    def get_directory(self):
        if os.path.isdir(self.__first):
            return self.__first
        return os.path.dirname(self.__first)

    def list_files(self, mode=FileProvider.IMAGES):
        """ Returns the files as passed to the constructor. """

        return self.__files


# vim: expandtab:sw=4:ts=4
