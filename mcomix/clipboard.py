"""clipboard.py - Clipboard handler"""

from typing import Any

from gi.repository import Gdk, GdkPixbuf, Gtk

from mcomix import image_tools
from mcomix.i18n import _

_IMAGE, _TEXT = 1, 2
_TEXT_TARGETS = ('UTF8_STRING', 'TEXT', 'STRING', 'text/plain;charset=utf-8', 'text/plain')


class Clipboard(object):

    """The Clipboard takes care of all necessary copy-paste functionality
    """

    def __init__(self, window: Any, selection: str = 'CLIPBOARD') -> None:
        self._selection = Gdk.Atom.intern(selection, False)
        self._clipboard = Gtk.Clipboard.get(self._selection)
        self._window = window
        # PyGObject has no Gtk.Clipboard.set_with_data, so one entry with an
        # image and a text target needs a widget that owns the selection.
        self._owner = Gtk.Invisible()
        self._owner.connect('selection-get', self._selection_get)
        self._owner.connect('selection-clear-event', self._selection_clear)
        # The targets Gtk.TargetList.add_image_targets and add_text_targets
        # would add: Gtk.target_table_new_from_list frees its result twice in
        # PyGObject and corrupts the heap.
        images = sorted(dict.fromkeys(mime for format in GdkPixbuf.Pixbuf.get_formats()
                                      if format.is_writable() for mime in format.get_mime_types()),
                        key=lambda mime: mime != 'image/png')
        Gtk.selection_add_targets(self._owner, self._selection,
            [Gtk.TargetEntry.new(mime, 0, _IMAGE) for mime in images] +
            [Gtk.TargetEntry.new(name, 0, _TEXT) for name in _TEXT_TARGETS])
        self._page = None

    def copy_text(self, text: str) -> None:
        """ Copies C{text} to clipboard. """
        self._clipboard.set_text(text, -1)

    def copy_page(self, *args: Any) -> None:
        """ Copies the current page image and its file path to clipboard. """

        if self._window.filehandler.file_loaded:
            # Get pixbuf for current page
            current_page_pixbufs = [image_tools.static_image(pixbuf) for pixbuf in
                self._window.imagehandler.get_pixbufs(
                    2 if self._window.displayed_double() else 1)] # XXX limited to at most 2 pages

            if len(current_page_pixbufs) == 1:
                pixbuf = current_page_pixbufs[ 0 ]
            else:
                pixbuf = image_tools.combine_pixbufs(
                        current_page_pixbufs[ 0 ],
                        current_page_pixbufs[ 1 ],
                        self._window.is_manga_mode )

            path = self._window.imagehandler.get_path_to_page()
            # Archive pages might be read on demand: write the file to disk.
            if self._window.filehandler.materialize([path]):
                self._window.osd.show(_('Could not extract the selected page.'))
                return
            if Gtk.selection_owner_set(self._owner, self._selection, Gdk.CURRENT_TIME):
                self._page = (path, pixbuf)

    def _selection_get(self, widget: Gtk.Widget, data: Gtk.SelectionData,
                       info: int, time: int) -> None:
        path, pixbuf = self._page
        if info == _IMAGE:
            data.set_pixbuf(pixbuf)
        else:
            data.set_text(path, -1)

    def _selection_clear(self, widget: Gtk.Widget, event: Gdk.EventSelection) -> bool:
        # Another owner took the clipboard: release the page image.
        self._page = None
        return False

# vim: expandtab:sw=4:ts=4
