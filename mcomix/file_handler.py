"""file_handler.py - File handler that takes care of opening archives and images."""


import os
import shutil
import tempfile
import threading
import re
import pickle
from typing import Callable, Iterable, List, Optional, Tuple, Union
from gi.repository import Gtk, GLib

from mcomix.atomic_file import atomic_write
from mcomix.preferences import prefs
from mcomix import archive_extractor
from mcomix import archive_tools
from mcomix import image_tools
from mcomix import tools
from mcomix import constants
from mcomix import file_provider
from mcomix import callback
from mcomix import i18n
from mcomix import log
from mcomix import last_read_page
from mcomix import message_dialog
from mcomix.library import backend
from mcomix.i18n import _


class _RetiredArchive(object):
    """Keep an abandoned book's inputs alive until its workers finish."""

    def __init__(self, extractor: archive_extractor.Extractor,
                 temporary: Optional[str], delete: Callable[[str], None]) -> None:
        self.extractor = extractor
        self.source = extractor._src
        self.temporary = temporary
        self.delete = delete
        self.error = None
        self.thread = threading.Thread(target=self._run, name='archive-retirement')
        self.thread.daemon = False

    def _finish(self) -> None:
        self.extractor.close()
        if self.temporary is not None:
            self.delete(self.temporary)
            self.temporary = None

    def _run(self) -> None:
        try:
            self._finish()
        except Exception as error:
            self.error = error
            log.error(_('! Could not release archive resources: %s'), error)

    def wait(self) -> None:
        self.thread.join()
        if self.error is not None:
            # Retain ownership after failure and allow a later explicit retry.
            self._finish()
            self.error = None


class FileHandler(object):

    """The FileHandler keeps track of the actual files/archives opened.

    While ImageHandler takes care of pages/images, this class provides
    the raw file names for archive members and image files, extracts
    archives, and lists directories for image files.
    """

    def __init__(self, window):
        #: Indicates if files/archives are currently loaded/loading.
        self.file_loaded = False
        self.file_loading = False
        #: None if current file is not an archive, or unrecognized format.
        self.archive_type = None

        #: Either path to the current archive, or first file in image list.
        #: This is B{not} the path to the currently open page.
        self._current_file = None
        #: Reference to L{MainWindow}.
        self._window = window
        #: Path to opened archive file, or directory containing current images.
        self._base_path = None
        #: Temporary directory used for extracting archives.
        self._tmp_dir = None
        #: If C{True}, no longer wait for files to get extracted.
        self._stop_waiting = False
        #: Nested GTK loops may request another open/close during notification.
        self._file_operation_active = False
        self._pending_file_operation = None
        self._shutting_down = False
        self._shutdown_ready = None
        self._resume_dialog = None
        #: List of comment files inside of the currently opened archive.
        self._comment_files = []
        #: Mapping of absolute paths to archive path names.
        self._name_table = {}
        #: Archive extractor.
        self._extractor = self._new_extractor()
        self._retired_archives = []
        #: Condition to wait on when extracting archives and waiting on files.
        self._condition = None
        #: Provides a list of available files/archives in the open directory.
        self._file_provider = None
        #: Keeps track of the last read page in archives
        self.last_read_page = last_read_page.LastReadPage(backend.LibraryBackend())
        #: Regexp used for determining which archive files are comment files.
        self._comment_re = None
        self.update_comment_extensions()

        self.last_read_page.set_enabled(bool(prefs['store recent file info']))

    def _new_extractor(self) -> archive_extractor.Extractor:
        extractor = archive_extractor.Extractor()
        extractor.file_extracted += self._extracted_file
        extractor.contents_listed += self._listed_contents
        return extractor

    def wait_for_file_release(self, path: str) -> None:
        """Release retired readers before a caller deletes/replaces this file."""
        for retired in list(self._retired_archives):
            same = os.path.normcase(os.path.abspath(retired.source)) == os.path.normcase(os.path.abspath(path))
            if not same and os.path.exists(retired.source) and os.path.exists(path):
                same = os.path.samefile(retired.source, path)
            if same:
                retired.wait()
                self._retired_archives.remove(retired)

    def _retire_archive(self, temporary: Optional[str]) -> None:
        """Transfer archive/storage ownership only after cleanup can start."""
        extractor = self._new_extractor()
        retired = _RetiredArchive(self._extractor, temporary, self.thread_delete)
        archives = self._retired_archives
        # Completed successful jobs no longer own handles or storage.
        archives[:] = [job for job in archives if job.thread.is_alive() or job.error is not None]
        archives.append(retired)
        try:
            retired.thread.start()
        except Exception:
            archives.remove(retired)
            raise
        self._extractor = extractor

    def refresh_file(self, *args, **kwargs):
        """ Closes the current file(s)/archive and reloads them. """
        if self.file_loaded:
            current_file = os.path.abspath(self._window.imagehandler.get_real_path())
            if self.archive_type is not None:
                start_page = self._window.imagehandler.get_current_page()
            else:
                start_page = 0
            self.open_file(current_file, start_page, keep_fileprovider=True)

    def _run_file_operation(self, operation: Callable[[], bool],
                            replace_pending: bool = True) -> bool:
        """Serialize lifecycle changes; the latest nested request wins.

        A deferred open returns True to mean accepted, rather than reporting
        an unopened recent item as invalid before it has been tried.
        """
        if self._file_operation_active:
            if replace_pending or self._pending_file_operation is None:
                self._pending_file_operation = operation
            return True
        self._file_operation_active = True
        try:
            result = operation()
            while self._pending_file_operation is not None:
                pending = self._pending_file_operation
                self._pending_file_operation = None
                pending()
            return result
        finally:
            self._pending_file_operation = None
            self._file_operation_active = False
            if self._shutdown_ready is not None:
                ready = self._shutdown_ready
                self._shutdown_ready = None
                ready()

    def begin_shutdown(self, on_ready: Callable[[], None]) -> None:
        """Reject future opens and finish shutdown after active ownership ends."""
        self._shutting_down = True
        self._pending_file_operation = None
        if self._resume_dialog is not None:
            self._resume_dialog.response(Gtk.ResponseType.CANCEL)
        if self._file_operation_active:
            self._shutdown_ready = on_ready
        else:
            on_ready()

    def open_file(self, path, start_page=0, keep_fileprovider=False,
                  start_image: str = '', start_entry: str = ''):
        """Open a file, or accept the latest request during a nested GTK loop.

        Positive start_page chooses that page, zero uses the normal resume
        policy, and a negative value selects the last page. A folder opens at
        start_image when it is one of its images, before start_page.
        start_entry, a page's name as stored in the archive or its path in
        the folder, wins over both; when no page has that name, the book
        opens at start_page, or page 1. The return value reports success, or
        acceptance when the request must be deferred.
        """
        if self._shutting_down:
            return False
        if isinstance(path, list):
            path = path[:]
        return self._run_file_operation(
            lambda: self._open_file(path, start_page, keep_fileprovider,
                                    start_image, start_entry))

    def _open_file(self, path: Union[str, List[str]], start_page: int = 0,
                   keep_fileprovider: bool = False, start_image: str = '',
                   start_entry: str = '') -> bool:
        self._close()
        if self._shutting_down:
            return False

        try:
            path = self._initialize_fileprovider(path, keep_fileprovider)
        except ValueError as ex:
            self._window.statusbar.set_message(str(ex))
            self._window.osd.show(str(ex))
            return False

        error_message = self._check_access(path)
        if error_message:
            self._window.statusbar.set_message(error_message)
            self._window.osd.show(error_message)
            return False

        self.archive_type = archive_tools.archive_mime_type(path)
        # A requested entry is an explicit position even when it is missing.
        self._start_page = start_page or (1 if start_entry else 0)
        self._start_image = start_image
        self._start_entry = start_entry
        self._current_file = os.path.abspath(path)
        self._stop_waiting = False

        # Actually open the file(s)/archive passed in path.
        if self.archive_type is not None:
            try:
                self._open_archive(self._current_file)
            except Exception as ex:
                self._close(wait_for_release=True)
                self._window.statusbar.set_message(str(ex))
                self._window.osd.show(str(ex))
                return False
            self.file_loading = True
            self._window.uimanager.set_sensitivities()
        else:
            self._base_path = self._file_provider.get_directory()
            self._archive_opened(self._file_provider.list_files())

        return True

    def _archive_opened(self, image_files):
        """ Called once the archive has been opened and its contents listed.
        """

        self._window.imagehandler._image_files = image_files
        self.file_opened()
        if self._shutting_down:
            return

        if not image_files:
            msg = _("No images in '%s'") % os.path.basename(self._current_file)
            self._window.statusbar.set_message(msg)
            self._window.osd.show(msg)

        else:
            ask_resume = False
            entry_index = self._find_start_entry(image_files)
            if self.archive_type is None:
                # If no extraction is required, mark all files as available.
                self.file_available(image_files)
                current_image_index = entry_index
                if current_image_index is None:
                    current_image_index = file_provider.find_path(image_files, self._current_file)
                if current_image_index is None and self._start_image:
                    current_image_index = file_provider.find_path(
                        image_files, self._start_image)
                if current_image_index is None:
                    current_image_index = self._get_index_for_page(
                        self._start_page or 1, len(image_files), self._current_file)
            else:
                if self._extractor.is_lazy():
                    # Nothing to extract, all files can be read right away.
                    self.file_available(image_files + self._comment_files)
                last_image_index = self._get_index_for_page(self._start_page,
                                                            len(image_files),
                                                            self._current_file)
                stored_choice = prefs['stored dialog choices'].get(
                    'resume-from-last-read-page')
                if entry_index is not None:
                    current_image_index = entry_index
                elif self._start_page or stored_choice == int(Gtk.ResponseType.YES):
                    current_image_index = last_image_index
                else:
                    current_image_index = 0
                    ask_resume = last_image_index != 0 and stored_choice != int(Gtk.ResponseType.NO)
                if ask_resume:
                    # Bump last page closer to the front of the extractor queue.
                    self._window.set_page(last_image_index + 1)
                    if self._shutting_down:
                        return

            self._window.set_page(current_image_index + 1)
            if self._shutting_down:
                return

            if self.archive_type is not None:
                self._extractor.extract()
                if ask_resume:
                    resume = self._ask_goto_last_read_page(
                        self._current_file, last_image_index + 1)
                    if self._shutting_down:
                        return
                    if resume:
                        self._window.set_page(last_image_index + 1)

            if self._shutting_down:
                return
            self.write_fileinfo_file()

        self._window.uimanager.recent.add(self._current_file)

    @callback.Callback
    def file_opened(self):
        """ Called when a new set of files has successfully been opened. """
        self.file_loaded = True

    @callback.Callback
    def file_closed(self):
        """ Called when the current file has been closed. """
        pass

    def close_file(self, keep_temporary: bool = False,
                   wait_for_release: bool = False) -> Optional[str]:
        """Close this book; optionally transfer its temporary directory.

        A caller retaining storage owns its eventual removal. Such a close
        must finish synchronously before the caller replaces the archive.
        Disk operations can also request synchronous release without storage.
        """
        if (keep_temporary or wait_for_release) and (self._file_operation_active or self._shutting_down):
            raise RuntimeError(_('The current book is busy. Please try again.'))
        temporary = self._tmp_dir if keep_temporary else None
        completed = False

        def close() -> None:
            nonlocal completed
            if (keep_temporary or wait_for_release) and self._base_path is not None:
                self.wait_for_file_release(self._base_path)
            self._close(close_provider=True, keep_temporary=keep_temporary,
                        wait_for_release=wait_for_release)
            if keep_temporary:
                self._tmp_dir = None  # Transfer only after all close callbacks succeed.
            completed = True

        try:
            self._run_file_operation(close)
        except Exception as error:
            if not keep_temporary or not completed:
                raise
            # A queued open/shutdown callback failed after this close succeeded.
            # Still return the transferred storage so its caller can own it.
            log.error(_('! A queued file operation failed: %s'), error)
        return temporary

    def _close(self, close_provider: bool = False,
               keep_temporary: bool = False, wait_for_release: bool = False) -> None:
        """Release this book's resources without dispatching unrelated input."""
        tmp_dir = self._tmp_dir
        self._tmp_dir = None
        was_open = self.file_loaded or self.file_loading
        resources_released = False
        try:
            if was_open:
                try:
                    self.update_last_read_page()
                except Exception as ex:
                    log.warning(_('Could not save the last read page: %s'), ex)
            self._stop_waiting = True
            # Also close after partial setup failures, before deleting storage.
            retire = not keep_temporary and not wait_for_release and self._extractor.is_busy()
            if retire:
                self._extractor.request_stop()
            else:
                self._extractor.close()
            if was_open:
                self._window.imagehandler.cleanup()
            if retire:
                self._retire_archive(tmp_dir)
                tmp_dir = None  # The retired job now owns the old directory.
            resources_released = True
            self.file_loaded = False
            self.file_loading = False
            self.archive_type = None
            self._current_file = None
            self._base_path = None
            self._condition = None
            self._comment_files = []
            self._name_table.clear()
            if close_provider:
                self._file_provider = None
            if was_open:
                self.file_closed()
            tools.garbage_collect()
        finally:
            if tmp_dir is not None:
                if resources_released and keep_temporary:
                    self._tmp_dir = tmp_dir
                elif resources_released:
                    try:
                        self.thread_delete(tmp_dir)
                    except Exception:
                        self._tmp_dir = tmp_dir
                        raise
                else:
                    # A failed stop/join still owns its storage. Keep it for
                    # a later close attempt instead of deleting live inputs.
                    self._tmp_dir = tmp_dir

    def _initialize_fileprovider(self, path, keep_fileprovider):
        """ Creates the L{file_provider.FileProvider} for C{path}.

        If C{path} is a list, assumes that only the files in the list
        should be available. If C{path} is a string, assume that it is
        either a directory or an image file, and all files in that directory
        should be opened.

        @param path: List of file names, or single file/directory as string.
        @param keep_fileprovider: If C{True}, no new provider is constructed.
        @return: If C{path} was a list, returns the first list element.
            Otherwise, C{path} is not modified."""

        paths = path if isinstance(path, list) else [path]
        if self._file_provider is None or not keep_fileprovider:
            self._file_provider = file_provider.get_file_provider(paths)
        return paths[0]

    def _check_access(self, path):
        """ Checks for various error that could occur when opening C{path}.

        @param path: Path to file that should be opened.
        @return: An appropriate error string, or C{None} if no error was found.
        """
        if not os.path.exists(path):
            return _('Could not open %s: No such file.') % path

        elif not os.access(path, os.R_OK):
            return _('Could not open %s: Permission denied.') % path

        else:
            return None

    def _open_archive(self, path):
        """ Opens the archive passed in C{path}.

        Creates an L{archive_extractor.Extractor} and extracts all images
        found within the archive. """

        self._tmp_dir = tempfile.mkdtemp(prefix='mcomix.', suffix=os.sep)
        self._base_path = path
        self._condition = self._extractor.setup(self._base_path,
                                                self._tmp_dir,
                                                self.archive_type)

    def _listed_contents(self, archive, files) -> None:
        # Listing completion can show a modal resume dialog. Keep ownership
        # stable until its callbacks and page initialization have returned.
        if self._shutting_down or archive is not self._extractor:
            return
        directory = archive.get_directory()
        self._run_file_operation(
            lambda: self._finish_listed_contents(directory), replace_pending=False)

    def _finish_listed_contents(self, directory: str) -> bool:
        if directory != self._tmp_dir or not self.file_loading:
            return False
        self.file_loading = False

        error = self._extractor.get_listing_error()
        if error is not None:
            # A partial index cannot safely be used by every backend.
            self._archive_opened([])
            message = _('Could not open archive: %s') % error
            self._window.statusbar.set_message(message)
            self._window.osd.show(message)
            return True

        files = self._extractor.get_files()
        archive_images = [image for image in files
            if image_tools.is_image_file(image)
            # Remove MacOS meta files from image list
            and not '__MACOSX' in os.path.normpath(image).split(os.sep)]

        self._sort_archive_images(archive_images)
        image_files = [ os.path.join(self._tmp_dir, f)
                        for f in archive_images ]

        comment_files = list(filter(self._comment_re.search, files))
        tools.alphanumeric_sort(comment_files)
        self._comment_files = [ os.path.join(self._tmp_dir, f)
                                for f in comment_files ]

        self._name_table = dict(list(zip(image_files, archive_images)))
        self._name_table.update(list(zip(self._comment_files, comment_files)))

        self._extractor.set_files(archive_images + comment_files)

        self._archive_opened(image_files)
        return True

    def _sort_archive_images(self, filelist):
        """ Sorts the image list passed in C{filelist} based on the sorting
        preference option. """

        if prefs['sort archive by'] == constants.SORT_NAME:
            tools.alphanumeric_sort(filelist)
        elif prefs['sort archive by'] == constants.SORT_NAME_LITERAL:
            filelist.sort()
        elif prefs['sort archive by'] == constants.SORT_NAME_GLIB:
            filelist.sort(key=lambda filename: [
                GLib.utf8_collate_key_for_filename(part, -1)
                for part in os.path.normpath(filename).split(os.sep)])
        else:
            # No sorting
            pass

        if prefs['sort archive order'] == constants.SORT_DESCENDING:
            filelist.reverse()

    def _find_start_entry(self, image_files: List[str]) -> Optional[int]:
        """Index of the page named by start_entry, reporting a missing one."""
        if not self._start_entry:
            return None
        # Callers name pages as stored, so compare codepoints exactly, also
        # where the filesystem ignores case.
        if self.archive_type is None:
            wanted = os.path.abspath(os.path.join(self._base_path, self._start_entry))
            index = next((i for i, image in enumerate(image_files)
                          if os.path.abspath(image) == wanted), None)
        else:
            wanted = self._start_entry.replace('\\', '/')
            index = next((i for i, image in enumerate(image_files)
                          if (self._extractor.stored_name(self._name_table[image]) or '')
                          .replace('\\', '/') == wanted), None)
        if index is None:
            self._window.show_notice(_("No page '%(entry)s' in '%(book)s'") % {
                'entry': self._start_entry,
                'book': os.path.basename(self._current_file)})
        return index

    def _get_index_for_page(self, start_page, num_of_pages, path):
        """ Returns the page that should be displayed for an archive.
        @param start_page: If -1, show last page. If 0, show either first page
                           or last read page. If > 0, show C{start_page}.
        @param num_of_pages: Page count.
        @param path: Archive path.
        """
        if start_page < 0 and prefs['default double page']:
            current_image_index = num_of_pages - 2
        elif start_page < 0 and not prefs['default double page']:
            current_image_index = num_of_pages - 1
        elif start_page == 0:
            current_image_index = (self.last_read_page.get_page(path) or 1) - 1
        else:
            current_image_index = start_page - 1

        return min(max(0, current_image_index), num_of_pages - 1)

    def _ask_goto_last_read_page(self, path, last_read_page):
        """ If the user read an archive previously, ask to continue from
        that time, or from page 1. This method returns a page index, that is,
        index + 1. """

        read_date = self.last_read_page.get_date(path)

        dialog = message_dialog.MessageDialog(self._window, Gtk.DialogFlags.MODAL, Gtk.MessageType.INFO,
            Gtk.ButtonsType.YES_NO)
        dialog.set_default_response(Gtk.ResponseType.YES)
        dialog.set_should_remember_choice('resume-from-last-read-page',
            (Gtk.ResponseType.YES, Gtk.ResponseType.NO))
        dialog.set_text(
            (_('Continue reading from page %d?') % last_read_page),
            _('You stopped reading here on %(date)s, %(time)s. '
            'If you choose "Yes", reading will resume on page %(page)d. Otherwise, '
            'the first page will be loaded.') % {'date': read_date.date().strftime("%x"),
                'time': read_date.time().strftime("%X"), 'page': last_read_page})
        self._resume_dialog = dialog
        try:
            result = dialog.run()
        finally:
            self._resume_dialog = None
            dialog.destroy()

        return result == Gtk.ResponseType.YES

    def get_file_number(self):
        if self.archive_type is None:
            # No file numbers for images.
            return 0, 0
        file_list = self._file_provider.list_files(file_provider.FileProvider.ARCHIVES)
        current_index = file_provider.find_path(file_list, self._current_file)
        if current_index is None:
            current_index = 0
        return current_index + 1, len(file_list)

    def get_number_of_comments(self):
        """Return the number of comments in the current archive."""
        return len(self._comment_files)

    def get_comment_text(self, num):
        """Return the text in comment <num> or None if comment <num> is not
        readable.
        """
        try:
            text = i18n.to_unicode(self.read_file(self._comment_files[num - 1]))
        except Exception:
            text = None
        return text

    def get_comment_name(self, num):
        """Return the filename of comment <num>."""
        return self._comment_files[num - 1]

    def update_comment_extensions(self):
        """Update the regular expression used to filter out comments in
        archives by their filename.
        """
        exts = []
        for ext in prefs['comment extensions']:
            ext = ext[2:] if ext.startswith('*.') else ext.lstrip('.')
            if ext:
                exts.append(re.escape(ext))
        # (?!) never matches: no extension makes no member a comment.
        self._comment_re = re.compile(r'\.(%s)\s*$' % '|'.join(exts) if exts else '(?!)', re.I)

    def get_comment_regex(self) -> 're.Pattern[str]':
        """Return the expression matching comment file names in archives."""
        return self._comment_re

    def get_path_to_base(self):
        """Return the full path to the current base (path to archive or
        image directory.)
        """
        if self.archive_type is not None:
            return self._base_path
        elif self._window.imagehandler._image_files:
            img_index = self._window.imagehandler._current_image_index
            filename = self._window.imagehandler._image_files[img_index]
            return os.path.dirname(filename)
        else:
            return None

    def get_base_filename(self):
        """Return the filename of the current base (archive filename or
        directory name).
        """
        return os.path.basename(self.get_path_to_base())

    def _open_next_archive(self, *args):
        """Open the archive that comes directly after the currently loaded
        archive in that archive's directory listing, sorted alphabetically.
        Returns True if a new archive was opened, False otherwise.
        """
        return self._open_sibling_archive(1)

    def _open_previous_archive(self, *args):
        """Open the archive that comes directly before the currently loaded
        archive in that archive's directory listing, sorted alphabetically.
        Returns True if a new archive was opened, False otherwise.
        """
        return self._open_sibling_archive(-1)

    def _open_sibling_archive(self, step: int) -> bool:
        if self.archive_type is None:
            return False
        files = self._file_provider.list_files(file_provider.FileProvider.ARCHIVES)
        current_index = file_provider.find_path(files, self._base_path)
        if current_index is None:
            return False
        if step > 0:
            candidates = files[current_index + 1:]
            start_page = 0
        else:
            candidates = list(reversed(files[:current_index]))
            start_page = prefs['open first file in prev archive'] - 1
        for path in candidates:
            if archive_tools.archive_mime_type(path) is not None:
                self.open_file(path, start_page, keep_fileprovider=True)
                return True
        return False

    def open_next_directory(self, *args):
        """ Opens the next sibling directory of the current file, as specified by
        file provider. Returns True if a new directory was opened and files found. """
        return self._open_sibling_directory(1)

    def open_previous_directory(self, *args):
        """ Opens the previous sibling directory of the current file, as specified by
        file provider. Returns True if a new directory was opened and files found. """
        return self._open_sibling_directory(-1)

    def _open_sibling_directory(self, step: int) -> bool:
        if self._file_provider is None:
            return False

        if self.archive_type is not None:
            listmode = file_provider.FileProvider.ARCHIVES
        else:
            listmode = file_provider.FileProvider.IMAGES

        if step > 0:
            moved = self._file_provider.next_directory()
            index = start_page = 0
        else:
            moved = self._file_provider.previous_directory()
            index = prefs['open first file in prev directory'] - 1
            start_page = (prefs['open first file in prev archive'] or
                          prefs['open first file in prev directory']) - 1
        if not moved:
            return False

        files = self._file_provider.list_files(listmode)
        path = files[index] if files else self._file_provider.get_directory()
        self.open_file(path, start_page, keep_fileprovider=True)
        return True

    def file_is_available(self, filepath):
        """ Returns True if the file specified by "filepath" is available
        for reading, i.e. extracted to harddisk. """

        name = self._archive_name(filepath)
        if name is not None:
            with self._condition:
                return self._extractor.is_ready(name)

        elif filepath is None:
            return False

        elif os.path.isfile(filepath):
            return True

        else:
            return False

    def _archive_name(self, path: str) -> Optional[str]:
        """ Returns the archive member name for <path>, or None if <path>
        is not a file from the current archive. """
        if self.archive_type is None:
            return None
        return self._name_table.get(path)

    def get_image_source(self, path: str) -> Union[str, bytes]:
        """ Returns what to load the image file <path> from: its contents
        if it is read on demand from the current archive, otherwise <path>
        itself (so that it can be loaded from disk directly). """
        name = self._archive_name(path)
        if name is not None:
            self._extractor.raise_if_failed(name)
            if self._extractor.is_lazy():
                return self._extractor.read(name)
        return path

    def read_file(self, path: str) -> bytes:
        """ Returns the contents of file <path>, waiting for it to be
        extracted if necessary. """
        name = self._archive_name(path)
        if name is None:
            with open(path, 'rb') as fp:
                return fp.read()
        self._wait_on_file(path)
        return self._extractor.read(name)

    def materialize(self, paths: Iterable[str]) -> List[str]:
        """ Makes sure archive files in <paths> exist on disk, for use by
        features that need real files (e.g. external programs). Archive
        files read on demand are written to the temporary directory;
        otherwise this waits for them to be extracted. Returns the archive
        files that are not available on disk. """
        failed = []
        for path in paths:
            name = self._archive_name(path)
            if name is None:
                continue
            self._wait_on_file(path)
            try:
                self._extractor.materialize(name)
            except Exception as ex:
                log.error(_('! Extraction error: %s'), ex)
                failed.append(path)
                continue
            if not os.path.isfile(path):
                failed.append(path)
        return failed

    def get_file_size(self, path: str) -> Optional[int]:
        """ Returns the size of file <path> in bytes, or None if unknown. """
        name = self._archive_name(path)
        if name is not None:
            return self._extractor.get_entry_size(name)
        try:
            return os.stat(path).st_size
        except OSError:
            return None

    @callback.Callback
    def file_available(self, filepaths):
        """ Called every time a new file from the Filehandler's opened
        files becomes available. C{filepaths} is a list of now available files.
        """
        pass

    def _extracted_file(self, extractor, name):
        """ Called when the extractor finishes extracting the file at
        <name>. This name is relative to the temporary directory
        the files were extracted to. """
        if not self.file_loaded or extractor is not self._extractor:
            return
        filepath = os.path.join(extractor.get_directory(), name)
        self.file_available([filepath])

    def _wait_on_file(self, path):
        """Block the running (main) thread if the file <path> is from an
        archive and has not yet been extracted. Return when the file is
        ready.
        """
        if self.archive_type == None or path == None:
            return

        try:
            name = self._name_table[path]
            with self._condition:
                while not self._extractor.is_ready(name) and not self._stop_waiting:
                    self._condition.wait()
        except Exception as ex:
            log.error('Waiting on extraction of "%s" failed: %s', path, ex)
            return

    def _ask_for_files(self, files):
        """Ask for <files> to be given priority for extraction.
        """
        if self.archive_type == None:
            return

        with self._condition:
            extractor_files = self._extractor.get_files()
            for path in reversed(files):
                name = self._name_table.get(path)
                if name in extractor_files and not self._extractor.is_ready(name):
                    extractor_files.remove(name)
                    extractor_files.insert(0, name)
            self._extractor.set_files(extractor_files)

    def thread_delete(self, path):
        """Start a threaded removal of the directory tree rooted at <path>.
        This is to avoid long blockings when removing large temporary dirs.
        """
        del_thread = threading.Thread(target=shutil.rmtree, args=(path, True))
        del_thread.name += '-delete'
        del_thread.daemon = False
        del_thread.start()

    def get_book_position(self) -> Tuple[str, int, str]:
        """Return (path, page, image) that reopens the open book here.

        A folder book is its folder with the image shown, since reopening an
        image would make that image's own folder the book. Other books are
        their real path, with no image.
        """
        images = self._window.imagehandler
        page = images.get_current_page()
        if self.archive_type is None and \
                isinstance(self._file_provider, file_provider.OrderedFileProvider):
            return self._base_path, page, images.get_real_path()
        return images.get_real_path(), page, ''

    def write_fileinfo_file(self):
        """Write current open file information."""

        if self.file_loaded:
            path, page, image = self.get_book_position()
            # An archive keeps the two-element form an older build reads.
            current_file_info = [path, page - 1, image] if image else [path, page - 1]
            with atomic_write(constants.FILEINFO_PICKLE_PATH, 'wb') as config:
                pickle.dump(current_file_info, config, pickle.HIGHEST_PROTOCOL)

    def read_fileinfo_file(self) -> Optional[Tuple[str, int, str]]:
        """Read last loaded file info from disk: (path, page index, image)."""

        fileinfo = None

        if os.path.isfile(constants.FILEINFO_PICKLE_PATH):
            config = None
            try:
                config = open(constants.FILEINFO_PICKLE_PATH, 'rb')

                fileinfo = pickle.load(config)
                if not isinstance(fileinfo, (list, tuple)) or len(fileinfo) not in (2, 3) or \
                        not isinstance(fileinfo[0], str) or not fileinfo[0] or \
                        type(fileinfo[1]) is not int or fileinfo[1] < 0 or \
                        not all(isinstance(image, str) for image in fileinfo[2:]):
                    raise ValueError('Invalid saved file position')
                fileinfo = (fileinfo[0], fileinfo[1],
                            fileinfo[2] if len(fileinfo) == 3 else '')

                config.close()

            except Exception as ex:
                fileinfo = None
                log.error(_('! Corrupt preferences file "%s", deleting...'),
                        constants.FILEINFO_PICKLE_PATH )
                log.info('Error was: %s', ex)
                if config is not None:
                    config.close()
                try:
                    os.remove(constants.FILEINFO_PICKLE_PATH)
                except OSError as cleanup_error:
                    log.warning(_('Could not remove corrupt file position: %s'), cleanup_error)

        return fileinfo

    def update_last_read_page(self):
        """ Stores the currently viewed page. """
        if self.archive_type is None or not self.file_loaded:
            return

        archive_path = self.get_path_to_base()
        page = self._window.imagehandler.get_current_page()
        visible = page + 1 if self._window.displayed_double() else page
        finished = visible >= self._window.imagehandler.get_number_of_pages()
        # Do not store the first page (the default); an archive without
        # pages must not be added to the library either.
        try:
            if page <= 1:
                self.last_read_page.clear_page(archive_path)
            elif finished:
                self.last_read_page.set_finished(archive_path)
            else:
                self.last_read_page.set_page(archive_path, page)
        except ValueError:
            # The book no longer exists in the library and has been deleted
            pass


# vim: expandtab:sw=4:ts=4
