""" Data class for library books and collections. """

import os
import re
from functools import lru_cache
import threading
import datetime
from typing import List, Optional, Tuple

from gi.repository import GLib

from mcomix import callback
from mcomix import archive_tools
from mcomix import log
from mcomix.i18n import _


@lru_cache(maxsize=128)
def _search_segments(filter_string: str) -> Tuple['re.Pattern[str]', ...]:
    """Compile the parts between % wildcards; _ matches one character.

    The parts contain no repetition, so matching never backtracks.
    """
    return tuple(re.compile(''.join('.' if char == '_' else re.escape(char)
                                    for char in part),
                            re.IGNORECASE | re.DOTALL)
                 for part in filter_string.split('%') if part)


def _matches_segments(value: str, segments: Tuple['re.Pattern[str]', ...]) -> bool:
    # Each % may skip any text, so the leftmost match of every part suffices.
    position = 0
    for segment in segments:
        match = segment.search(value, position)
        if match is None:
            return False
        position = match.end()
    return True


def matches_filter(name: Optional[str], path: Optional[str],
                   filter_string: Optional[str]) -> bool:
    """Search names and paths with case-insensitive LIKE substrings.

    Percent matches any run of characters and underscore exactly one. Other
    characters, including brackets and backslashes, are literal.
    """
    if not filter_string:
        return True
    segments = _search_segments(filter_string)
    return any(value is not None and _matches_segments(value, segments)
               for value in (name, path))


class _BackendObject(object):

    def get_backend(self):
        # XXX: Delayed import to avoid circular import
        from mcomix.library.backend import LibraryBackend
        return LibraryBackend()


class _Book(_BackendObject):
    """ Library book instance. """

    def __init__(self, id, name, path, pages, format, size, added):
        """ Creates a book instance.
        @param id: Book id
        @param name: Base name of the book
        @param path: Full path to the book
        @param pages: Number of pages
        @param format: One of the archive formats in L{constants}
        @param size: File size in bytes
        @param added: Datetime when book was added to library """

        self.id = id
        self.name = name
        self.path = path
        self.pages = pages
        self.format = format
        self.size = size
        self.added = added

    def get_collections(self):
        """ Gets a list of collections this book is part of. If it
        belongs to no collections, [DefaultCollection] is returned. """
        cursor = self.get_backend().execute(
            '''SELECT id, name, supercollection FROM collection
               JOIN contain on contain.collection = collection.id
               WHERE contain.book = ?''', (self.id,))
        rows = cursor.fetchall()
        if rows:
            return [_Collection(*row) for row in rows]
        else:
            return [DefaultCollection]

    def get_last_read_page(self):
        """ Gets the page of this book that was last read when the book was
        closed. Returns C{None} if no such page exists. """
        cursor = self.get_backend().execute(
            '''SELECT page FROM recent WHERE book = ?''', (self.id,))
        row = cursor.fetchone()
        cursor.close()
        if row:
            return row
        else:
            return None

    def get_last_read_date(self):
        """ Gets the datetime the book was most recently read. Returns
        C{None} if no information was set, or a datetime object otherwise. """
        cursor = self.get_backend().execute(
            """SELECT time_set FROM recent WHERE book = ?""", (self.id,))
        date = cursor.fetchone()
        cursor.close()

        if date:
            try:
                return datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                # Certain operating systems do not store fractions
                return datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
        else:
            return None

    def set_last_read_page(self, page, time=None):
        """ Sets the page that was last read when the book was closed.
        Passing C{None} as argument clears the recent information.

        @param page: Page number, starting from 1 (page 1 throws ValueError)
        @param time: Time of reading. If None, current time is used. """

        if page is not None and page < 1:
            # Avoid wasting memory by creating a recently viewed entry when
            # an archive was opened on page 1.
            raise ValueError('Invalid page (must start from 1)')

        # Remove any old recent row for this book
        cursor = self.get_backend().execute(
            '''DELETE FROM recent WHERE book = ?''', (self.id,))
        # If a new page was passed, set it as recently read
        if page is not None:
            if not time:
                time = datetime.datetime.now()
            cursor.execute('''INSERT INTO recent (book, page, time_set)
                              VALUES (?, ?, ?)''',
                           (self.id, page, time))

        cursor.close()


class _Collection(_BackendObject):
    """ Library collection instance.
    This class should NOT be instianted directly, but only with methods from
    L{LibraryBackend} instead. """

    def __init__(self, id, name, supercollection=None):
        """ Creates a collection instance.
        @param id: Collection id
        @param name: Name of the collection
        @param supercollection: Parent collection, or C{None} """

        self.id = id
        self.name = name
        self.supercollection = supercollection

    def __eq__(self, other):
        if isinstance(other, _Collection):
            return self.id == other.id
        elif isinstance(other, int):
            return self.id == other
        else:
            return False

    def get_books(self, filter_string=None):
        """ Returns all books that are part of this collection,
        including subcollections. """

        backend = self.get_backend()
        collections = [self.id] + backend.get_all_collections_in_collection(self.id)
        sql = '''SELECT book.id, book.name, book.path, book.pages, book.format,
                        book.size, book.added
                 FROM book
                 WHERE book.id IN (SELECT book FROM contain
                                   WHERE collection IN (%s))
              ''' % ','.join('?' * len(collections))
        cursor = backend.execute(sql, collections)
        rows = cursor.fetchall()
        cursor.close()

        return [_Book(*cols) for cols in rows
                if matches_filter(cols[1], cols[2], filter_string)]


class _DefaultCollection(_Collection):
    """ Represents the default collection that books belong to if
    no explicit collection was specified. """

    def __init__(self):

        self.id = None
        self.name = _("All books")
        self.supercollection = None

    def get_books(self, filter_string=None):
        """ Returns all books in the library """
        sql = '''SELECT book.id, book.name, book.path, book.pages, book.format,
                        book.size, book.added
                 FROM book
              '''

        sql_args = []
        cursor = self.get_backend().execute(sql, sql_args)
        rows = cursor.fetchall()
        cursor.close()

        return [_Book(*cols) for cols in rows
                if matches_filter(cols[1], cols[2], filter_string)]


DefaultCollection = _DefaultCollection()


class _WatchList(object):
    """ Scans watched directories and updates the database when new books have
    been added. This object is part of the library backend, i.e.
    C{library.backend.watchlist}. """

    def __init__(self, backend):
        self.backend = backend
        self._scan_lock = threading.Lock()
        self._scan_thread = None
        self._scan_cancel = threading.Event()
        self._scan_generation = 0
        self._scan_closed = False
        self._scan_stopping = False

    def add_directory(self, path, collection=DefaultCollection, recursive=False):
        """ Adds a new watched directory. """

        directory = os.path.normpath(os.path.abspath(path))
        sql = """INSERT OR IGNORE INTO watchlist (path, collection, recursive)
                 VALUES (?, ?, ?)"""
        cursor = self.backend.execute(sql, [directory, collection.id, recursive])
        cursor.close()

    def get_watchlist(self):
        """ Returns a list of watched directories.
        @return: List of L{_WatchListEntry} objects. """

        sql = """SELECT watchlist.path,
                        watchlist.recursive,
                        collection.id, collection.name,
                        collection.supercollection
                 FROM watchlist
                 LEFT JOIN collection ON watchlist.collection = collection.id"""

        cursor = self.backend.execute(sql)
        entries = [self._result_row_to_watchlist_entry(row) for row in cursor.fetchall()]
        cursor.close()

        return entries

    def get_watchlist_entry(self, path):
        """ Returns a single watchlist entry, specified by C{path} """
        sql = """SELECT watchlist.path,
                        watchlist.recursive,
                        collection.id, collection.name,
                        collection.supercollection
                 FROM watchlist
                 LEFT JOIN collection ON watchlist.collection = collection.id
                 WHERE watchlist.path = ?"""

        cursor = self.backend.execute(sql, (os.path.normpath(path), ))
        result = cursor.fetchone()
        cursor.close()

        if result:
            return self._result_row_to_watchlist_entry(result)
        else:
            raise ValueError("Watchlist entry doesn't exist")

    def scan_for_new_files(self) -> bool:
        """Start one scan, snapshotting database state before filesystem work."""
        with self._scan_lock:
            if self._scan_closed or self._scan_stopping:
                return False
            if self._scan_thread is not None:
                if self._scan_thread.is_alive():
                    return False
            books = DefaultCollection.get_books()
            # A Recent-only book is new to a collection entry, which files
            # it there, but not to an All books entry, which adds nothing.
            all_books = [book.path for book in books]
            filed_books = [book.path for book in books
                           if book.get_collections() != [-2]]
            entries = [(entry, all_books if entry.collection.id is None else filed_books)
                       for entry in self.get_watchlist()]
            self._scan_generation += 1
            generation = self._scan_generation
            cancel = threading.Event()
            self._scan_cancel = cancel
            self._scan_thread = threading.Thread(
                target=self._scan_for_new_files_thread,
                args=(generation, cancel, entries),
                name='scan_for_new_files', daemon=False)
            self._scan_thread.start()
            return True

    def stop_scan(self, shutdown: bool = False) -> None:
        """Cancel and join the owned scan before releasing database resources.

        Cancellation is checked between filesystem operations. A blocked OS
        directory read must return before its worker can safely be joined.
        """
        with self._scan_lock:
            self._scan_stopping = True
            self._scan_closed = self._scan_closed or shutdown
            self._scan_generation += 1
            self._scan_cancel.set()
            thread = self._scan_thread
        if thread is not None:
            thread.join()
        with self._scan_lock:
            if self._scan_thread is thread:
                self._scan_thread = None
            self._scan_stopping = False

    def _scan_for_new_files_thread(self, generation: int, cancel: threading.Event,
                                   entries: List[Tuple['_WatchListEntry', List[str]]]) -> None:
        """Only filesystem work runs here; database snapshots belong to GTK."""
        for entry, existing_books in entries:
            if cancel.is_set():
                return
            try:
                new_files = entry.get_new_files(existing_books, cancel)
            except OSError as error:
                log.warning(_('Could not scan directory %(directory)s: %(error)s'),
                            {'directory': entry.directory, 'error': error})
                continue
            if cancel.is_set():
                return
            GLib.idle_add(self._publish_new_files, generation, cancel,
                          new_files, entry)

    def _publish_new_files(self, generation: int, cancel: threading.Event,
                           paths: List[str], entry: '_WatchListEntry') -> bool:
        """Discard queued scan results after cancellation, restart or shutdown."""
        if self._scan_closed or cancel.is_set():
            return False
        if generation == self._scan_generation:
            self.new_files_found(paths, entry)
        return False

    def _result_row_to_watchlist_entry(self, row):
        """ Converts the result of a SELECT statement to a WatchListEntry. """
        collection_id = row[2]
        if collection_id:
            collection = _Collection(*row[2:])
        else:
            collection = DefaultCollection

        return _WatchListEntry(row[0], row[1], collection)


    @callback.Callback
    def new_files_found(self, paths, watchentry):
        """ Called after scan_for_new_files finishes.
        @param paths: List of filenames for newly added files. This list
                      may be empty if no new files were found during the scan.
        @param watchentry: Watchentry for files/directory.
        """
        pass


class _WatchListEntry(_BackendObject):
    """ A watched directory. """

    def __init__(self, directory, recursive, collection):
        self.directory = os.path.normpath(os.path.abspath(directory))
        self.recursive = bool(recursive)
        self.collection = collection

    def get_new_files(self, filelist: List[str],
                      cancel: Optional[threading.Event] = None) -> List[str]:
        """Find new archives, checking cancellation between filesystem calls."""
        if cancel is None:
            cancel = threading.Event()
        if cancel.is_set() or not self.is_valid() or cancel.is_set():
            return []

        old_files = frozenset(os.path.normcase(os.path.abspath(path)) for path in filelist)
        available_files = set()
        if not self.recursive:
            filenames = os.listdir(self.directory)
            for filename in filenames:
                if cancel.is_set():
                    return []
                if archive_tools.is_archive_file(filename):
                    available_files.add(os.path.join(self.directory, filename))
        else:
            for dirpath, dirnames, filenames in os.walk(self.directory):
                if cancel.is_set():
                    return []
                for filename in filenames:
                    if cancel.is_set():
                        return []
                    if archive_tools.is_archive_file(filename):
                        available_files.add(os.path.join(dirpath, filename))

        return [path for path in available_files
                if os.path.normcase(path) not in old_files]

    def is_valid(self):
        """ Check if the watched directory is a valid directory and exists. """
        return os.path.isdir(self.directory)

    def remove(self):
        """ Removes this entry from the watchlist, deleting its associated
        path from the database. """
        sql = """DELETE FROM watchlist WHERE path = ?"""
        cursor = self.get_backend().execute(sql, (self.directory,))
        cursor.close()

        self.directory = ""
        self.collection = None

    def set_collection(self, new_collection):
        """ Updates the collection associated with this watchlist entry. """
        if new_collection != self.collection:
            sql = """UPDATE watchlist SET collection = ? WHERE path = ?"""
            cursor = self.get_backend().execute(sql,
                    (new_collection.id, self.directory))
            cursor.close()
            self.collection = new_collection

    def set_recursive(self, recursive):
        """ Enables or disables recursive scanning. """
        if recursive != self.recursive:
            sql = """UPDATE watchlist SET recursive = ? WHERE path = ?"""
            cursor = self.get_backend().execute(sql,
                    (recursive, self.directory))
            cursor.close()
            self.recursive = recursive


# vim: expandtab:sw=4:ts=4
