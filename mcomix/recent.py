"""recent.py - Application-scoped shared recent files handler."""

import sys
import urllib.request
from typing import Iterable, Optional

from gi.repository import Gio, GLib, Gtk

from mcomix import archive_tools
from mcomix import constants
from mcomix import image_tools
from mcomix import log
from mcomix import portability
from mcomix import preferences


class RecentFilesMenu(Gtk.RecentChooserMenu):

    def __init__(self, window,
                 manager: Optional[Gtk.RecentManager] = None):
        self._manager = (manager if manager is not None
                         else Gtk.RecentManager.get_default())
        super(RecentFilesMenu, self).__init__(recent_manager=self._manager)
        self._window = window

        self.set_sort_type(Gtk.RecentSortType.MRU)
        self.set_show_tips(True)
        # Missing icons crash GTK on Win32
        if sys.platform == 'win32':
            self.set_show_icons(False)
            self.set_show_numbers(True)

        formats = Gtk.RecentFilter()
        supported_formats = {}
        supported_formats.update(image_tools.get_supported_formats())
        supported_formats.update(archive_tools.get_supported_formats())
        for name in sorted(supported_formats):
            mime_types, extensions = supported_formats[name]
            for mime in mime_types:
                formats.add_mime_type(mime)
            for ext in extensions:
                formats.add_pattern('*.%s' % ext)

        # Rules in one RecentFilter are ORed. Apply ownership AND the
        # existing supported-format filter, rather than adding another rule.
        rfilter = Gtk.RecentFilter()
        rfilter.add_custom(
            formats.get_needed() | Gtk.RecentFilterFlags.APPLICATION,
            lambda info: all((constants.APPNAME in (info.applications or []),
                              formats.filter(info))))
        self.add_filter(rfilter)
        self.connect('item_activated', self._load)

    def _load(self, *args) -> None:
        uri = self.get_current_uri()
        if uri is None:
            return
        path = Gio.File.new_for_uri(uri).get_path()
        if path is None:
            return
        if not self._window.filehandler.open_file(path):
            self.remove(path)

    def count(self) -> int:
        """Return the number of entries registered by MComix."""
        return sum(item.has_application(constants.APPNAME)
                   for item in self._manager.get_items())

    def add(self, path: str) -> None:
        if not preferences.prefs['store recent file info']:
            return
        uri = portability.uri_prefix() + urllib.request.pathname2url(path)
        content_type, _ = Gio.content_type_guess(path, None)
        data = Gtk.RecentData()
        data.app_name = constants.APPNAME
        data.app_exec = '%s %%u' % (GLib.get_prgname() or constants.APPNAME)
        data.mime_type = Gio.content_type_get_mime_type(content_type)
        self._manager.add_full(uri, data)

    def remove(self, path: str) -> None:
        if preferences.prefs['store recent file info']:
            uri = portability.uri_prefix() + urllib.request.pathname2url(path)
            self._remove_uris([uri])

    def remove_all(self) -> None:
        """Forget MComix history while preserving other applications."""
        self._remove_uris(item.get_uri() for item in self._manager.get_items())

    def _remove_uris(self, uris: Iterable[str]) -> None:
        """Remove entries registered only by MComix.

        Gtk.RecentManager cannot drop a single application's registration,
        so entries shared with another application are left untouched.
        """
        exclusive = {item.get_uri() for item in self._manager.get_items()
                     if item.get_applications() == [constants.APPNAME]}
        for uri in exclusive.intersection(uris):
            try:
                self._manager.remove_item(uri)
            except GLib.Error as error:
                log.debug(error)

# vim: expandtab:sw=4:ts=4
