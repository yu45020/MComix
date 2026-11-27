"""edit_dialog.py - The dialog for the archive editing window."""

import os
import tempfile
import shutil
import stat
from gi.repository import Gdk, GLib, Gtk

from mcomix import archive_packer
from mcomix import file_chooser_simple_dialog
from mcomix import image_tools
from mcomix import edit_image_area
from mcomix import edit_comment_area
from mcomix import constants
from mcomix import message_dialog
from mcomix import log
from mcomix.i18n import _

_dialog = None

class _EditArchiveDialog(Gtk.Dialog):

    """The _EditArchiveDialog lets users edit archives (or directories) by
    reordering images and removing and adding images or comment files. The
    result can be saved as a ZIP archive.
    """

    def __init__(self, window):
        super(_EditArchiveDialog, self).__init__(_('Edit archive'), window, Gtk.DialogFlags.MODAL,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL))

        self.add_button(Gtk.STOCK_APPLY, Gtk.ResponseType.APPLY)

        self.kill = False # Dialog is killed.
        self.file_handler = window.filehandler
        self._window = window
        self._retained_directories = []

        self._save_button = self.add_button(Gtk.STOCK_SAVE_AS, constants.RESPONSE_SAVE_AS)

        self._import_button = self.add_button(_('_Import'), constants.RESPONSE_IMPORT)
        self._import_button.set_image(Gtk.Image.new_from_stock(Gtk.STOCK_ADD,
            Gtk.IconSize.BUTTON))

        self.set_border_width(4)
        self.resize(min(Gdk.Screen.get_default().get_width() - 50, 750),
            min(Gdk.Screen.get_default().get_height() - 50, 600))

        self.connect('response', self._response)
        self.connect('destroy', self._cleanup_retained_inputs)

        self._image_area = edit_image_area._ImageArea(self, window)
        self._comment_area = edit_comment_area._CommentArea(self)

        notebook = Gtk.Notebook()
        notebook.set_border_width(6)
        notebook.append_page(self._image_area, Gtk.Label(label=_('Images')))
        notebook.append_page(self._comment_area, Gtk.Label(label=_('Comment files')))
        self.vbox.pack_start(notebook, True, True, 0)

        self.show_all()

        GLib.idle_add(self._load_original_files)

    def _load_original_files(self):
        """Load the original files from the archive or directory into
        the edit dialog.
        """
        if self.kill:
            return False
        self._save_button.set_sensitive(False)
        self._import_button.set_sensitive(False)
        self._window.set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))
        self._image_area.fetch_images()

        if self.kill: # fetch_images() allows pending events to be handled.
            return False

        self._comment_area.fetch_comments()
        self._window.set_cursor(None)
        self._save_button.set_sensitive(True)
        self._import_button.set_sensitive(True)

        return False

    def _pack_archive(self, archive_path: str) -> None:
        """Pack completely before releasing inputs, then replace atomically."""
        if self.kill:
            return
        self.set_sensitive(False)
        self._window.set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))
        temporary = None
        source = None
        released = False
        saved = False
        page = self._window.imagehandler.get_current_page() or 1
        try:
            handler = self.file_handler
            source = handler.get_path_to_base() if handler.archive_type is not None else None
            replacing_current = (source is not None and os.path.exists(source) and os.path.exists(archive_path) and os.path.samefile(source, archive_path))
            image_files = self._image_area.get_file_listing()
            comment_files = self._comment_area.get_file_listing()
            if handler.materialize(image_files + comment_files):
                raise OSError(_('Could not extract the selected files.'))
            fd, temporary = tempfile.mkstemp(
                suffix='.%s' % os.path.basename(archive_path), prefix='tmp.',
                dir=os.path.dirname(os.path.abspath(archive_path)))
            os.close(fd)
            packer = archive_packer.Packer(image_files, comment_files, temporary,
                os.path.splitext(os.path.basename(archive_path))[0])
            packer.pack()
            if not packer.wait():
                raise OSError(_('Could not pack the selected files.'))
            # Set permissions before replacement so any failure preserves the
            # old target. Never unlink a target before its replacement exists.
            if source is not None and os.path.exists(source):
                os.chmod(temporary, os.stat(source).st_mode)
            if replacing_current:
                directory = handler.close_file(keep_temporary=True)
                released = True
                if directory is not None:
                    if self.kill:
                        _EditArchiveDialog._delete_retained_directory(self, directory)
                    else:
                        self._retained_directories.append(directory)
                if self.kill or handler._shutting_down:
                    raise RuntimeError(_('The current book is closing.'))
            handler.wait_for_file_release(archive_path)
            os.replace(temporary, archive_path)
            temporary = None
            saved = True
        except Exception as error:
            log.error(_('! Could not save archive: %s'), error)
        finally:
            if temporary is not None:
                try:
                    try:
                        os.unlink(temporary)
                    except PermissionError:
                        # A read-only source may have made our temporary file
                        # read-only too. It is ours, so make cleanup possible.
                        os.chmod(temporary, os.stat(temporary).st_mode | stat.S_IWUSR)
                        os.unlink(temporary)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    log.error(_('! Could not remove temporary archive: %s'), error)
            if not self.kill:
                self._window.set_cursor(None)
                self.set_sensitive(True)
        if self.kill:
            return
        if released and not self.file_handler._shutting_down:
            # Failed replacement leaves the old archive intact. The dialog
            # retains its extracted inputs even when reopening uses new storage.
            try:
                self.file_handler.open_file(archive_path if saved else source,
                                            start_page=page)
            except Exception as error:
                log.error(_('! Could not reopen archive: %s'), error)
        if saved:
            _close_dialog()
        else:
            dialog = message_dialog.MessageDialog(self._window, 0, Gtk.MessageType.ERROR,
                Gtk.ButtonsType.CLOSE)
            dialog.set_text(
                _('The new archive could not be saved!'),
                _('The original files have not been removed.'))
            dialog.run()

    def _response(self, dialog, response):

        if response == constants.RESPONSE_SAVE_AS:

            dialog = file_chooser_simple_dialog.SimpleFileChooserDialog(
                Gtk.FileChooserAction.SAVE)

            src_path = self.file_handler.get_path_to_base()

            dialog.set_current_directory(os.path.dirname(src_path))
            dialog.set_save_name('%s.cbz' % os.path.splitext(
                os.path.basename(src_path))[0])
            dialog.filechooser.set_extra_widget(Gtk.Label(label=
                _('Archives are stored as ZIP files.')))
            dialog.add_archive_filters()
            dialog.run()

            paths = dialog.get_paths()
            dialog.destroy()

            if paths:
                self._pack_archive(paths[0])

        elif response == constants.RESPONSE_IMPORT:

            dialog = file_chooser_simple_dialog.SimpleFileChooserDialog()
            dialog.add_image_filters()
            dialog.run()
            paths = dialog.get_paths()
            dialog.destroy()

            comment_re = self._window.filehandler.get_comment_regex()

            for path in paths:

                if image_tools.is_image_file(path):
                    self._image_area.add_extra_image(path)

                elif os.path.isfile(path):

                    if comment_re.search( path ):
                        self._comment_area.add_extra_file(path)

        elif response == Gtk.ResponseType.APPLY:

            new_image_array = self._image_area.get_file_listing()

            self._window.imagehandler.reset_page_list(new_image_array)
            self._window.thumbnailsidebar.clear()
            if new_image_array:
                self._window.set_page(1)
            else:
                self._window.clear()
            self._window.thumbnailsidebar.load_thumbnails()

        else:
            _close_dialog()
            self.kill = True

    def _delete_retained_directory(self, directory: str) -> None:
        try:
            self.file_handler.thread_delete(directory)
        except Exception as error:
            log.error(_('! Could not start temporary directory cleanup: %s'), error)
            # If a worker cannot start, do not abandon this owner's storage.
            shutil.rmtree(directory, ignore_errors=True)

    def _cleanup_retained_inputs(self, widget: Gtk.Widget) -> None:
        self.kill = True
        self._image_area.cleanup()
        for directory in self._retained_directories:
            self._delete_retained_directory(directory)
        self._retained_directories.clear()

    def destroy(self):
        self._cleanup_retained_inputs(self)
        Gtk.Dialog.destroy(self)

def open_dialog(action, window):
    global _dialog

    if _dialog is None:
        _dialog = _EditArchiveDialog(window)
    else:
        _dialog.present()


def _close_dialog(*args):
    global _dialog

    if _dialog is not None:
        _dialog.destroy()
        _dialog = None

# vim: expandtab:sw=4:ts=4
