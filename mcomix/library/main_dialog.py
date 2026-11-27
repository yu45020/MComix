"""library_main_dialog.py - The library dialog window."""

import os
from gi.repository import Gdk, Gtk
from typing import Any, List

from mcomix.preferences import prefs
from mcomix import i18n
from mcomix import tools
from mcomix import file_chooser_library_dialog
from mcomix import status
from mcomix.library import backend as library_backend
from mcomix.library import book_area as library_book_area
from mcomix.library import collection_area as library_collection_area
from mcomix.library import control_area as library_control_area
from mcomix.library import add_progress_dialog as library_add_progress_dialog
from mcomix.i18n import _

_dialog = None
# The "All books" collection is not a real collection stored in the library,
# but is represented by this ID in the library's TreeModels.
_COLLECTION_ALL = -1
_USE_SELECTED_COLLECTION = object()

class _LibraryDialog(Gtk.Window):

    """The library window. Automatically creates and uses a new
    library_backend.LibraryBackend when opened.
    """

    def __init__(self, window, file_handler):
        super(_LibraryDialog, self).__init__(Gtk.WindowType.TOPLEVEL)

        self._window = window
        self._closed = False

        self.resize(prefs['lib window width'], prefs['lib window height'])
        self.set_title(_('Library'))
        self.connect('delete_event', self.close)
        self.connect('key-press-event', self._key_press_event)

        self.filter_string = None
        self._file_handler = file_handler
        self._statusbar = Gtk.Statusbar()
        self.backend = library_backend.LibraryBackend()
        self.book_area = library_book_area._BookArea(self)
        self.control_area = library_control_area._ControlArea(self)
        self.collection_area = library_collection_area._CollectionArea(self)

        self.backend.watchlist.new_files_found += self._new_files_found

        table = Gtk.Table(2, 2, False)
        table.attach(self.collection_area, 0, 1, 0, 1, Gtk.AttachOptions.FILL,
            Gtk.AttachOptions.EXPAND|Gtk.AttachOptions.FILL)
        table.attach(self.book_area, 1, 2, 0, 1, Gtk.AttachOptions.EXPAND|Gtk.AttachOptions.FILL,
            Gtk.AttachOptions.EXPAND|Gtk.AttachOptions.FILL)
        table.attach(self.control_area, 0, 2, 1, 2, Gtk.AttachOptions.EXPAND|Gtk.AttachOptions.FILL,
            Gtk.AttachOptions.FILL)

        if prefs['show statusbar']:
            table.attach(self._statusbar, 0, 2, 2, 3, Gtk.AttachOptions.FILL, Gtk.AttachOptions.FILL)

        self.add(table)
        self.show_all()
        self.present()

    def open_book(self, books, keep_library_open=False):
        """Open the book with ID <book>."""

        # A book removed since it was listed has no path.
        paths = [path for path in map(self.backend.get_book_path, books)
                 if path is not None]
        if not paths:
            return

        if not keep_library_open:
            self.hide()

        self._window.present()

        if len(paths) > 1:
            self._file_handler.open_file(paths)
        elif len(paths) == 1:
            self._file_handler.open_file(paths[0])

    def scan_for_new_files(self):
        """ Start scanning for new files from the watch list. """

        if not self._closed and len(self.backend.watchlist.get_watchlist()) > 0:
            self.set_status_message(_("Scanning for new books..."))
            self.backend.watchlist.scan_for_new_files()

    def _new_files_found(self, filelist, watchentry):
        """ Called after the scan for new files finished. """

        if self._closed:
            return
        if len(filelist) > 0:
            self.add_books(filelist, collection_id=watchentry.collection.id)
            if self._closed:
                return

            if len(filelist) == 1:
                message = _("Added new book '%(bookname)s' "
                    "from directory '%(directory)s'.")
            else:
                message = _("Added %(count)d new books "
                    "from directory '%(directory)s'.")

            self.set_status_message(message % {'directory': watchentry.directory,
                'count': len(filelist), 'bookname': os.path.basename(filelist[0])})
        else:
            self.set_status_message(
                _("No new books found in directory '%s'.") % watchentry.directory)

    def get_status_bar(self):
        """ Returns the window's status bar. """
        return self._statusbar

    def set_status_message(self, message):
        """Set a specific message on the statusbar, replacing whatever was
        there earlier.
        """
        self._statusbar.pop(0)
        self._statusbar.push(0,
            ' ' * status.Statusbar.SPACING + '%s' % i18n.to_unicode(message))

    def close(self, *args):
        """Close the library and do required cleanup tasks."""
        if self._closed:
            return
        self._closed = True
        prefs['lib window width'], prefs['lib window height'] = self.get_size()
        self.backend.watchlist.new_files_found -= self._new_files_found
        self.backend.watchlist.stop_scan()
        for dialog in Gtk.Window.list_toplevels():
            if dialog.get_transient_for() is self:
                dialog.destroy()
        self.book_area.stop_update()
        self.book_area.close()
        file_chooser_library_dialog.close_library_filechooser_dialog()
        _close_dialog()

    def add_books(self, paths: List[str], *,
                  collection_id: Any = _USE_SELECTED_COLLECTION) -> None:
        """Add books to an explicit destination or the current selection.

        None or the All books model ID means no membership. Omitting the
        destination uses the currently selected collection.
        """
        if self._closed or self.backend._con is None:
            return
        if collection_id is _USE_SELECTED_COLLECTION:
            collection_id = self.collection_area.get_current_collection()
        if collection_id == _COLLECTION_ALL:
            collection_id = None

        library_add_progress_dialog._AddLibraryProgressDialog(self, self._window, paths, collection_id)

        if not self._closed and self.backend._con is not None and collection_id is not None:
            if self.backend.get_collection_name(collection_id) is not None:
                prefs['last library collection'] = collection_id

    def _key_press_event(self, widget, event, *args):
        """ Handle key press events for closing the library on Escape press. """

        if event.keyval == Gdk.KEY_Escape:
            self.hide()


def open_dialog(action, window):
    """ Shows the library window and returns True. """
    global _dialog

    if _dialog is None:
        _dialog = _LibraryDialog(window, window.filehandler)
        if prefs['scan for new books on library startup']:
            _dialog.scan_for_new_files()

    else:
        _dialog.present()

    return True


def _close_dialog(*args):
    global _dialog

    if _dialog is not None:
        _dialog.destroy()
        _dialog = None
        tools.garbage_collect()

# vim: expandtab:sw=4:ts=4
