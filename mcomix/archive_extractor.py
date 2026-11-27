"""archive_extractor.py - Archive extraction class."""


import os
import threading
import traceback
from typing import Optional

from mcomix import archive_tools
from mcomix.archive import archive_base
from mcomix import callback
from mcomix import log
from mcomix.preferences import prefs
from mcomix.worker_thread import WorkerThread
from mcomix.i18n import _


class Extractor(object):

    """Extractor is a threaded class for extracting different archive formats.

    The Extractor can be loaded with paths to archives and a path to a
    destination directory. Once an archive has been set and its contents
    listed, it is possible to filter out the files to be extracted and set the
    order in which they should be extracted.  The extraction can then be
    started in a new thread in which files are extracted one by one, and a
    signal is sent on a condition after each extraction, so that it is possible
    for other threads to wait on specific files to be ready.

    Note: Support for gzip/bzip2 compressed tar archives is limited, see
    set_files() for more info.

    When the 'lazy archive reading' preference is set and the archive
    supports random access, nothing is extracted: once listed, every file
    is ready, and is read on demand with read() (or written to the
    destination directory with materialize() when a real file is needed).
    """

    def __init__(self):
        self._setupped = False
        self._archive = None
        self._lazy = False
        self._archive_closed = False
        self._generation = 0
        self._cancelled = False
        self._condition = threading.Condition()
        self._failed = {}
        self._listing_error = None
        #: Serializes access to the archive in lazy mode.
        self._archive_lock = threading.Lock()

    def setup(self, src, dst, type=None):
        """Setup the extractor with archive <src> and destination dir <dst>.
        Return a threading.Condition related to the is_ready() method, or
        None if the format of <src> isn't supported.
        """
        self.close()
        self._src = src
        self._dst = dst
        self._files = []
        self._extracted = set()
        self._failed = {}
        self._listing_error = None
        self._archive = archive_tools.get_recursive_archive_handler(src, dst, type=type)
        if self._archive is None:
            msg = _('Non-supported archive format: %s') % os.path.basename(src)
            log.warning(msg)
            raise ArchiveException(msg)

        self._cancelled = False
        self._contents_listed = False
        self._extract_started = False
        self._lazy = False
        self._archive_closed = False
        self._materialized = set()
        self._condition = threading.Condition()
        self._list_thread = WorkerThread(self._list_contents, name='list')
        self._setupped = True
        self._list_thread.append_order(self._archive)

        return self._condition

    def get_files(self):
        """Return a list of names of all the files the extractor is currently
        set for extracting. After a call to setup() this is by default all
        files found in the archive. The paths in the list are relative to
        the archive root and are not absolute for the files once extracted.
        """
        with self._condition:
            if not self._contents_listed:
                return
            return self._files[:]

    def get_directory(self):
        """Returns the root extraction directory of this extractor."""
        return self._dst

    def set_files(self, files):
        """Set the files that the extractor should extract from the archive in
        the order of extraction. Normally one would get the list of all files
        in the archive using get_files(), then filter and/or permute this
        list before sending it back using set_files().

        Note: Random access on gzip or bzip2 compressed tar archives is
        no good idea. These formats are supported *only* for backwards
        compability. They are fine formats for some purposes, but should
        not be used for scanned comic books. So, we cheat and ignore the
        ordering applied with this method on such archives.
        """
        with self._condition:
            if not self._contents_listed:
                return
            if self._lazy:
                # Nothing to extract, keep the list for get_files().
                self._files = list(files)
                return
            self._files = [f for f in files
                           if f not in self._extracted and f not in self._failed]
            if not self._files:
                # Nothing to do!
                return
            if self._extract_started:
                self.extract()

    def is_ready(self, name):
        """Return True once <name> can be read, or extraction has failed.

        Failed members are terminal too: waiters must wake and readers will
        raise the recorded error instead of consuming a partial file.
        """
        with self._condition:
            if self._lazy:
                return True
            return name in self._extracted or name in self._failed

    def get_error(self, name: str) -> Optional[str]:
        """Return the extraction error for a completed failed member."""
        with self._condition:
            return self._failed.get(name)

    def get_listing_error(self) -> Optional[str]:
        """Return the error for a failed listing, whose published list is empty."""
        with self._condition:
            return self._listing_error

    def raise_if_failed(self, name: str) -> None:
        """Reject a failed member before a caller uses its disk path."""
        error = self.get_error(name)
        if error is not None:
            raise ArchiveException(error)

    def is_lazy(self) -> bool:
        """Return True if files are read on demand instead of being
        extracted. Only meaningful once the contents have been listed."""
        with self._condition:
            return self._lazy

    def read(self, name: str) -> bytes:
        """Return the contents of file <name>, which must be ready (see
        is_ready()). In lazy mode it is read from the archive, otherwise
        from the extracted file."""
        path = archive_base.BaseArchive._destination_path(self._dst, name)
        self.raise_if_failed(name)
        if self._lazy:
            with self._archive_lock:
                self._check_open()
                if not self._is_materialized(name):
                    return self._archive.read(name)
        # A materialized file might have been edited by an external
        # program: it is what other features (e.g. repacking) use too.
        with open(path, 'rb') as fp:
            return fp.read()

    def materialize(self, name: str) -> str:
        """Make sure file <name>, which must be ready (see is_ready()),
        exists in the destination directory, and return its path."""
        # Validate before the exception cleanup: rejecting an unsafe member
        # must never unlink a file outside the extraction directory.
        path = archive_base.BaseArchive._destination_path(self._dst, name)
        self.raise_if_failed(name)
        if self._lazy:
            with self._archive_lock:
                self._check_open()
                if not self._is_materialized(name):
                    try:
                        self._archive.extract(name, self._dst)
                    except Exception:
                        # Don't leave a partial file behind.
                        if os.path.isfile(path):
                            os.unlink(path)
                        raise
                    self._materialized.add(name)
        return path

    def stored_name(self, name: str) -> Optional[str]:
        """Return the name <name> has inside the archive (see BaseArchive)."""
        return self._archive.stored_name(name)

    def get_entry_size(self, name: str) -> Optional[int]:
        """Return the uncompressed size of file <name>, if known."""
        path = archive_base.BaseArchive._destination_path(self._dst, name)
        # A member still being extracted has a partial file.
        if self.get_error(name) is not None or not self.is_ready(name):
            return None
        if self._lazy:
            with self._archive_lock:
                self._check_open()
                if not self._is_materialized(name):
                    return self._archive.get_entry_size(name)
        try:
            return os.stat(path).st_size
        except OSError:
            return None

    def _is_materialized(self, name: str) -> bool:
        """Return True if materialize() wrote file <name> and it still
        exists. Must be called with <_archive_lock> held."""
        path = archive_base.BaseArchive._destination_path(self._dst, name)
        if name not in self._materialized:
            return False
        if os.path.isfile(path):
            return True
        # Removed (e.g. by an external program): read the archive again.
        self._materialized.discard(name)
        return False

    def _check_open(self) -> None:
        if self._archive_closed or self._cancelled:
            raise ArchiveException('Archive is closed: %s' % self._src)

    def is_busy(self) -> bool:
        """Return whether listing or eager extraction can still use storage."""
        with self._condition:
            return self._setupped and (not self._contents_listed or (self._extract_started and bool(self._files)))

    def request_stop(self) -> None:
        """Fence callbacks and wake readers without joining backend work."""
        with self._condition:
            if self._cancelled:
                return
            self._cancelled = True
            self._generation += 1
            if self._setupped:
                self._list_thread.request_stop()
                if self._extract_started:
                    self._extract_thread.request_stop()
                self._archive.cancel()
                if not self._contents_listed:
                    self._listing_error = _('Archive listing stopped.')
                    self._contents_listed = True
                pending = list(self._files)
                for name in pending:
                    self._extraction_finished(name, _('Extraction stopped.'), publish=False)
            self._condition.notify_all()

    def stop(self):
        """Cancel and wait until workers no longer use archive resources."""
        self.request_stop()
        if self._setupped:
            self._list_thread.stop()
            if self._extract_started:
                self._extract_thread.stop()
                self._extract_started = False
            self._setupped = False

    def extract(self):
        """Start extracting the files in the file list one by one using a
        new thread. Every time a new file is extracted a notify() will be
        signalled on the Condition that was returned by setup().
        """
        with self._condition:
            if self._cancelled or not self._setupped or not self._contents_listed or self._lazy or not self._files:
                return
            if not self._extract_started:
                if self._archive.support_concurrent_extractions \
                   and not self._archive.is_solid():
                    max_threads = prefs['max extract threads']
                else:
                    max_threads = 1
                if self._archive.is_solid():
                    fn = self._extract_all_files
                else:
                    fn = self._extract_file
                self._extract_thread = WorkerThread(fn,
                                                    name='extract',
                                                    max_threads=max_threads,
                                                    unique_orders=True)
                self._extract_started = True
            else:
                self._extract_thread.clear_orders()
            if self._archive.is_solid():
                # Sort files so we don't queue the same batch multiple times.
                self._extract_thread.append_order(sorted(self._files))
            else:
                self._extract_thread.extend_orders(self._files)

    @callback.Callback
    def contents_listed(self, extractor, files):
        """ Called after the contents of the archive has been listed. """
        pass

    @callback.Callback
    def file_extracted(self, extractor, filename):
        """Called whenever a member finishes, including failed extractions."""
        pass

    @callback.Callback
    def _publish_listing(self, generation: int, files: list) -> None:
        if not self._cancelled and generation == self._generation:
            self.contents_listed(self, files)

    @callback.Callback
    def _publish_extraction(self, generation: int, name: str) -> None:
        if not self._cancelled and generation == self._generation:
            self.file_extracted(self, name)

    def close(self):
        """Close any open file objects, need only be called manually if the
        extract() method isn't called.
        """
        self.stop()
        if self._archive and not self._archive_closed:
            # Wait for any on-demand read in progress.
            with self._archive_lock:
                self._archive.close()
                self._archive_closed = True

    def _extraction_finished(self, name: str, error: Optional[str] = None,
                             publish: bool = True) -> None:
        with self._condition:
            if name in self._extracted or name in self._failed:
                return
            if name in self._files:
                self._files.remove(name)
            if error is None:
                self._extracted.add(name)
            else:
                self._failed[name] = error
            self._condition.notify_all()
        if publish and not self._cancelled:
            self._publish_extraction(self._generation, name)

    def _check_extracted(self, name: str) -> None:
        path = archive_base.BaseArchive._destination_path(self._dst, name)
        if not os.path.isfile(path):
            raise ArchiveException(_('Archive member was not extracted: %s') % name)

    def _extract_all_files(self, files):

        # With multiple extractions for each pass, some of the files might have
        # already been extracted.
        with self._condition:
            files = list(set(files) - self._extracted - set(self._failed))
            files.sort()

        pending = set(files)
        error = _('Archive member was not extracted.')
        try:
            # log.debug('Extracting from "%s" to "%s": "%s"', self._src, self._dst, '", "'.join(files))
            for f in self._archive.iter_extract(files, self._dst):
                if self._extract_thread.must_stop():
                    return
                if f in pending:
                    self._check_extracted(f)
                    self._extraction_finished(f)
                    pending.remove(f)

        except Exception as ex:
            # Better to ignore any failed extractions (e.g. from a corrupt
            # archive) than to crash here and leave the main thread in a
            # possible infinite block. Damaged or missing files *should* be
            # handled gracefully by the main program anyway.
            log.error(_('! Extraction error: %s'), ex)
            log.debug('Traceback:\n%s', traceback.format_exc())
            error = str(ex) or type(ex).__name__
        finally:
            for name in pending:
                self._extraction_finished(name, error)

    def _extract_file(self, name):
        """Extract the file named <name> to the destination directory,
        mark the file as "ready", then signal a notify() on the Condition
        returned by setup().
        """

        error = None
        try:
            # log.debug('Extracting from "%s" to "%s": "%s"', self._src, self._dst, name)
            self._archive.extract(name, self._dst)
            self._check_extracted(name)

        except Exception as ex:
            # Better to ignore any failed extractions (e.g. from a corrupt
            # archive) than to crash here and leave the main thread in a
            # possible infinite block. Damaged or missing files *should* be
            # handled gracefully by the main program anyway.
            log.error(_('! Extraction error: %s'), ex)
            log.debug('Traceback:\n%s', traceback.format_exc())
            error = str(ex) or type(ex).__name__

        self._extraction_finished(name, error)

    def _list_contents(self, archive):
        files = []
        try:
            for f in archive.iter_contents():
                if self._list_thread.must_stop():
                    return
                files.append(f)
            # Ask now, while no lock is held: an on-demand read waiting for
            # the password dialog would block the main thread.
            if self._cancelled:
                return
            archive.resolve_password()
        except Exception as ex:
            log.error(_('! Could not list archive: %s'), ex)
            log.debug('Traceback:\n%s', traceback.format_exc())
            self._listing_error = str(ex) or type(ex).__name__
            files = []
        lazy = False
        if self._listing_error is None and prefs['lazy archive reading']:
            try:
                lazy = archive.supports_random_access()
            except Exception as ex:
                log.debug('Random access check failed: %s', ex)
        log.debug('Archive "%s" will be %s', self._src,
                  'read on demand' if lazy else 'extracted')
        with self._condition:
            if self._cancelled:
                return
            self._files = files
            self._lazy = lazy
            self._contents_listed = True
            self._condition.notify_all()
        self._publish_listing(self._generation, files)

class ArchiveException(Exception):
    """ Indicate error during extraction operations. """
    pass

# vim: expandtab:sw=4:ts=4
