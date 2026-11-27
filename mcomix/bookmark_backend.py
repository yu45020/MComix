"""bookmark_backend.py - Bookmarks handler."""

import os
import pickle
from gi.repository import Gtk
import operator
import datetime

from mcomix import constants
from mcomix.atomic_file import atomic_write
from mcomix import log
from mcomix import bookmark_menu_item
from mcomix import callback
from mcomix import i18n
from mcomix import message_dialog
from mcomix.i18n import _

class __BookmarksStore(object):

    """The _BookmarksStore is a backend for both the bookmarks menu and dialog.
    Changes in the _BookmarksStore are mirrored in both.
    """

    def __init__(self):
        self._initialized = False
        self._window = None
        self._file_handler = None
        self._image_handler = None

        #: List of bookmarks, as the file held them at the last load or write
        self._bookmarks = []
        #: Whether the file was read or written since it last failed to load
        self._loaded = False
        #: The file's st_mtime_ns after that load or write (None: no file)
        self._mtime = None
        self._reload()

    def initialize(self, window):
        """ Initializes references to the main window and file/image handlers. """
        if not self._initialized:
            self._window = window
            self._file_handler = window.filehandler
            self._image_handler = window.imagehandler
            self._initialized = True

            # Update already loaded bookmarks with window and file handler information
            for bookmark in self._bookmarks:
                bookmark._window = window
                bookmark._file_handler = window.filehandler

    @callback.Callback
    def add_bookmark(self, bookmark) -> bool:
        """Add the <bookmark> to the list; return whether it was saved."""
        return self._apply(lambda bookmarks: bookmarks + [bookmark])

    @callback.Callback
    def remove_bookmark(self, bookmark) -> bool:
        """Remove the <bookmark> from the list; return whether it was saved."""
        # Not `!=`: GObject's own comparison would compare identities.
        return self._apply(lambda bookmarks: [
            item for item in bookmarks if not item == bookmark])

    def reorder_bookmarks(self, ordering: list) -> None:
        """Store <ordering> for the bookmarks the file still holds (entries
        added meanwhile stay at the end), then notify listeners once."""
        def reorder(bookmarks: list) -> list:
            stored = {bookmark: bookmark for bookmark in bookmarks}
            ordered = set(ordering)
            return [stored[bookmark] for bookmark in ordering
                    if bookmark in stored] + [
                bookmark for bookmark in bookmarks if bookmark not in ordered]

        self._apply(reorder)
        self.bookmarks_reordered()

    @callback.Callback
    def bookmarks_reordered(self) -> None:
        """Notify listeners once after the bookmarks were reordered."""

    def add_current_to_bookmarks(self):
        """Add the currently viewed page to the list."""
        name = self._image_handler.get_pretty_current_filename()
        # A folder book's bookmark is its folder at the page, so it reopens
        # that folder and matches the open book (_Bookmark._load).
        path, page, _image = self._file_handler.get_book_position()
        numpages = self._image_handler.get_number_of_pages()
        archive_type = self._file_handler.archive_type
        date_added = datetime.datetime.now()

        same_file_bookmarks = []

        for bookmark in self.get_bookmarks():
            if bookmark._path == path:
                if bookmark._page == page:
                    # Do not create identical bookmarks
                    return
                else:
                    same_file_bookmarks.append(bookmark)

        # If the same file was already bookmarked, ask to replace
        # the existing bookmarks before deleting them.
        if len(same_file_bookmarks) > 0:
            response = self.show_replace_bookmark_dialog(same_file_bookmarks, page)

            # Delete old bookmarks
            if response == Gtk.ResponseType.YES:
                for bookmark in same_file_bookmarks:
                    # A failed save already showed its error; adding now
                    # would keep both bookmarks after "Replace".
                    if not self.remove_bookmark(bookmark):
                        return
            # Perform no action
            elif response not in (Gtk.ResponseType.YES, Gtk.ResponseType.NO):
                return

        self.add_bookmark(bookmark_menu_item._Bookmark(self._window, self._file_handler,
            i18n.to_display_string(name), path, page, numpages, archive_type, date_added))

    def get_bookmarks(self):
        """Return the bookmarks the file holds (the last list read while it
        can't be read)."""
        self._reload()
        return self._bookmarks

    def _reload(self) -> bool:
        """Read the file when another instance changed it since this one's
        last load or write. Return False when it can't be read."""
        path = constants.BOOKMARK_PICKLE_PATH
        try:
            mtime = os.stat(path).st_mtime_ns
        except FileNotFoundError:
            mtime = None
        except OSError as error:
            return self._load_failed(path, error)
        if self._loaded and mtime == self._mtime:
            return True

        bookmarks = []
        if mtime is not None:
            try:
                with open(path, 'rb') as fd:
                    pickle.load(fd)  # Consume the legacy version record.
                    packs = pickle.load(fd)
                bookmarks = [bookmark_menu_item._Bookmark(
                    self._window, self._file_handler, *pack) for pack in packs]
            except OSError as error:
                return self._load_failed(path, error)
            except Exception:
                # Keep the bytes for the owner; the store starts empty.
                broken = path + '.broken'
                try:
                    os.replace(path, broken)
                except OSError as error:
                    return self._load_failed(path, error)
                log.error(_('! Could not parse bookmarks file %(path)s, '
                            'moved it to %(broken)s'),
                          {'path': path, 'broken': broken})
                bookmarks, mtime = [], None

        self._bookmarks, self._mtime, self._loaded = bookmarks, mtime, True
        return True

    def _load_failed(self, path: str, error: OSError) -> bool:
        log.error(_('! Could not read bookmarks file %(path)s: %(error)s'),
                  {'path': path, 'error': error})
        self._loaded = False
        return False

    def _apply(self, change) -> bool:
        """Reload the file, apply <change> (list -> list) to what it holds
        and write the result, so another instance's changes stay. While the
        file can't be read or written, the list stays as the file has it and
        False is returned."""
        path = constants.BOOKMARK_PICKLE_PATH
        if self._reload():
            bookmarks = change(list(self._bookmarks))
            try:
                with atomic_write(path, 'wb') as fd:
                    pickle.dump(constants.VERSION, fd, pickle.HIGHEST_PROTOCOL)
                    pickle.dump([bookmark.pack() for bookmark in bookmarks],
                                fd, pickle.HIGHEST_PROTOCOL)
                self._bookmarks = bookmarks
                self._mtime = os.stat(path).st_mtime_ns
                return True
            except OSError as error:
                log.error(_('! Could not save bookmarks: %s'), error)
        self._show_save_error(path)
        return False

    def _show_save_error(self, path: str) -> None:
        dialog = message_dialog.MessageDialog(
            self._window, Gtk.DialogFlags.MODAL, Gtk.MessageType.ERROR,
            Gtk.ButtonsType.CLOSE)
        dialog.set_text(_('Could not save bookmarks'),
                        _('The bookmarks file "%s" could not be read or '
                          'written. The change was not saved.') % path)
        dialog.run()


    def show_replace_bookmark_dialog(self, old_bookmarks, new_page):
        """ Present a confirmation dialog to replace old bookmarks.
        @return RESPONSE_YES to create replace bookmarks,
            RESPONSE_NO to create a new bookmark, RESPONSE_CANCEL to abort creating
            a new bookmark. """
        dialog = message_dialog.MessageDialog(self._window, Gtk.DialogFlags.MODAL, Gtk.MessageType.INFO)
        dialog.add_buttons(Gtk.STOCK_YES, Gtk.ResponseType.YES,
             Gtk.STOCK_NO, Gtk.ResponseType.NO,
             Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.set_default_response(Gtk.ResponseType.YES)
        dialog.set_should_remember_choice('replace-existing-bookmark',
            (Gtk.ResponseType.YES, Gtk.ResponseType.NO))

        pages = list(map(str, sorted(map(operator.attrgetter('_page'), old_bookmarks))))
        dialog.set_text(
            i18n.get_translation().ngettext(
                'Replace existing bookmark on page %s?',
                'Replace existing bookmarks on pages %s?',
                len(pages)
            ) % ", ".join(pages),

            _('The current book already contains marked pages. '
              'Do you want to replace them with a new bookmark on page %d?') % new_page +
              '\n\n' +
            _('Selecting "No" will create a new bookmark without affecting the other bookmarks.'))

        return dialog.run()


# Singleton instance of the bookmarks store.
BookmarksStore = __BookmarksStore()

# vim: expandtab:sw=4:ts=4
