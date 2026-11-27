"""file_chooser_library_dialog.py - Custom FileChooserDialog implementations."""

from gi.repository import Gtk

from mcomix.preferences import prefs
from mcomix import file_chooser_base_dialog
from mcomix.i18n import _

_library_filechooser_dialog = None

class _LibraryFileChooserDialog(file_chooser_base_dialog._BaseFileChooserDialog):

    """The filechooser dialog used when adding books to the library."""

    def __init__(self, library):
        super(_LibraryFileChooserDialog, self).__init__()
        self.set_transient_for(library)
        self.set_title(_('Add books'))

        self._library = library

        self.filechooser.set_select_multiple(True)
        self.add_archive_filters()

        # Remove 'All files' filter from base class
        self.filechooser.remove_filter(self.filechooser.list_filters()[0])
        # files_chosen saves an index into this list, without "All files".
        filters = self.filechooser.list_filters()
        index = prefs['last filter in library filechooser']
        if not isinstance(index, int) or not 0 <= index < len(filters):
            index = 0
        self.filechooser.set_filter(filters[index])

        # Remove default buttons and add buttons that make more sense
        for widget in self.get_action_area().get_children():
            self.get_action_area().remove(widget)

        self.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        self.add_button(Gtk.STOCK_ADD, Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

    def should_open_recursive(self):
        return True

    def files_chosen(self, paths):
        if paths:
            try: # For some reason this fails sometimes (GTK+ bug?)
                filter_index = self.filechooser.list_filters().index(
                    self.filechooser.get_filter())
                prefs['last filter in library filechooser'] = filter_index

            except Exception:
                pass

            close_library_filechooser_dialog()
            self._library.add_books(paths)

        else:
            close_library_filechooser_dialog()

def open_library_filechooser_dialog(library):
    """Open the library filechooser dialog."""
    global _library_filechooser_dialog

    if _library_filechooser_dialog is None:
        _library_filechooser_dialog = _LibraryFileChooserDialog(library)
    else:
        _library_filechooser_dialog.present()

def close_library_filechooser_dialog(*args):
    """Close the library filechooser dialog."""
    global _library_filechooser_dialog

    if _library_filechooser_dialog is not None:
        _library_filechooser_dialog.destroy()
        _library_filechooser_dialog = None


# vim: expandtab:sw=4:ts=4
