"""library_backend.py - Comic book library backend using sqlite."""

import os
import datetime

from typing import Any, List, Optional, TYPE_CHECKING

from mcomix import archive_tools
from mcomix import constants
from mcomix import thumbnail_tools
from mcomix import log
from mcomix import callback
from mcomix.library import backend_types
from mcomix.i18n import _

from sqlite3 import dbapi2

if TYPE_CHECKING:
    from gi.repository import GdkPixbuf


#: Identifies the 'Recent' collection that stores recently read books.
COLLECTION_RECENT = -2


class _LibraryBackend(object):

    """The LibraryBackend handles the storing and retrieval of library
    data to and from disk.
    """

    #: Current version of the library database structure.
    DB_VERSION = 7

    def __init__(self) -> None:

        def row_factory(cursor, row):
            """Return rows as sequences only when they have more than
            one element.
            """
            if len(row) == 1:
                return row[0]
            return row

        self._con = dbapi2.connect(constants.LIBRARY_DATABASE_PATH,
            check_same_thread=False, isolation_level=None)
        self._con.row_factory = row_factory
        self._con.create_function(
            'normcase', 1, lambda path: path and os.path.normcase(path), deterministic=True)

        self.watchlist = backend_types._WatchList(self)

        version = self._library_version()
        if version == -1:
            self._create_tables()
        elif version != _LibraryBackend.DB_VERSION:
            # No upgrade steps: the installed MComix 1.2.1 shares this
            # database at version 6 (docs/requirements/config-files.md).
            log.warning(_('Library database version %(found)d is not %(current)d; using it unchanged.'),
                        {'found': version, 'current': _LibraryBackend.DB_VERSION})

    def get_books_in_collection(self, collection: Optional[int] = None) -> List[int]:
        """Return the book IDs in a collection and its descendants, or all
        book IDs if <collection> is None."""
        collections = [collection]
        if collection is not None:
            collections.extend(self.get_all_collections_in_collection(collection))
        books = []
        for coll in collections:
            sql = 'SELECT id FROM book'
            args = ()
            if coll is not None:
                sql += (' WHERE id IN (SELECT book FROM contain '
                        'WHERE collection = ?)')
                args = (coll,)
            books.extend(self._con.execute(sql, args).fetchall())
        return books

    def get_book_by_path(self, path: str) -> Optional[backend_types._Book]:
        """ Retrieves a book from the library, specified by C{path}.
        If the book doesn't exist, None is returned. Otherwise, a
        L{backend_types._Book} instance is returned. """

        book_id = self._find_book_id(path)
        if book_id is None:
            return None
        return self.get_book_by_id(book_id)

    def _find_book_id(self, path: str) -> Optional[int]:
        """Return the ID of the book at <path>, matched as the filesystem
        compares paths (case-insensitively on Windows)."""
        book_id: Optional[int] = self._con.execute(
            'select id from book where normcase(path) = ?',
            (os.path.normcase(os.path.abspath(path)),)).fetchone()
        return book_id

    def get_book_by_id(self, id: int) -> Optional[backend_types._Book]:
        """ Retrieves a book from the library, specified by C{id}.
        If the book doesn't exist, C{None} is returned. Otherwise, a
        L{backend_types._Book} instance is returned. """

        cur = self.execute('''select id, name, path, pages, format,
                                     size, added
                              from book where id = ?''', (id,))
        book = cur.fetchone()
        cur.close()

        if book:
            return backend_types._Book(*book)
        else:
            return None

    def get_book_cover(self, book: int) -> Optional[str]:
        """Return a pixbuf with a thumbnail of the cover of <book>, or
        None if the cover can not be fetched.
        """
        try:
            path = self._con.execute('''select path from Book
                where id = ?''', (book,)).fetchone()
        except Exception:
            log.error(_('! Non-existant book #%i'), book)
            return None

        return self.get_book_thumbnail(path)

    def get_book_path(self, book: int) -> Optional[str]:
        """Return the filesystem path to <book>, or None if <book> isn't
        in the library.
        """
        try:
            path: Optional[str] = self._con.execute('''select path from Book
                where id = ?''', (book,)).fetchone()
        except Exception:
            log.error(_('! Non-existant book #%i'), book)
            return None

        return path

    def get_book_thumbnail(self, path: str) -> Optional["GdkPixbuf.Pixbuf"]:
        """ Returns a pixbuf with a thumbnail of the cover of the book at <path>,
        or None, if no thumbnail could be generated. """

        # Use the maximum image size allowed by the library, so that thumbnails
        # might be downscaled, but never need to be upscaled (and look ugly).
        thumbnailer = thumbnail_tools.Thumbnailer(dst_dir=constants.LIBRARY_COVERS_PATH,
                                                  store_on_disk=True,
                                                  archive_support=True,
                                                  size=(constants.MAX_LIBRARY_COVER_SIZE,
                                                        constants.MAX_LIBRARY_COVER_SIZE))
        thumb = thumbnailer.thumbnail(path)

        if thumb is None: log.warning(_('! Could not get cover for book "%s"'), path)
        return thumb

    def get_collections_in_collection(self, collection: Optional[int] = None) -> List[int]:
        """Return a sequence with all the subcollections in <collection>,
        or all top-level collections if <collection> is None.
        """
        if collection is None:
            cur = self._con.execute('''select id from Collection
                where supercollection isnull
                order by case when id = ? then ? else name end''',
                                    (COLLECTION_RECENT, _('Recent')))
        else:
            cur = self._con.execute('''select id from Collection
                where supercollection = ?
                order by name''', (collection,))
        return cur.fetchall()

    def get_all_collections_in_collection(self, collection: int) -> List[int]:
        """ Returns a sequence of <all> subcollections in <collection>,
        that is, even subcollections that are again a subcollection of one
        of the previous subcollections. """

        if collection is None:
            raise ValueError("Collection must not be <None>")

        to_search = [collection]
        collections = []
        # This assumes that the library is built like a tree, so no circular references.
        while len(to_search) > 0:
            collection = to_search.pop()
            subcollections = self.get_collections_in_collection(collection)
            collections.extend(subcollections)
            to_search.extend(subcollections)

        return collections

    def get_all_collections(self) -> List[int]:
        """Return a sequence with all collections (flattened hierarchy).
        The sequence is sorted alphabetically by collection name.
        """
        cur = self._con.execute('''select id from Collection
            order by case when id = ? then ? else name end''',
                                (COLLECTION_RECENT, _('Recent')))
        return cur.fetchall()

    def get_collection_name(self, collection: int) -> Optional[str]:
        """Return the name field of the <collection>, or None if the
        collection does not exist.
        """
        cur = self._con.execute('''select case when id = ? then ? else name end from Collection
            where id = ?''', (COLLECTION_RECENT, _('Recent'), collection,))
        name: Optional[str] = cur.fetchone()
        return name

    def get_collection_by_name(self, name: str) -> Optional[backend_types._Collection]:
        """Return the collection called <name>, or None if no such
        collection exists. Names are unique, so at most one such collection
        can exist.
        """
        cur = self._con.execute('''select id, name, supercollection
            from collection
            where name = ?''', (name,))
        result = cur.fetchone()
        cur.close()
        if result:
            return backend_types._Collection(*result)
        else:
            return None

    def get_collection_by_id(self, id: int) -> Optional[backend_types._Collection]:
        """ Returns the collection with ID C{id}.
        @param id: Integer value. May be C{-1} or C{None} for default collection.
        @return: L{_Collection} if found, None otherwise.
        """
        if id is None or id == -1:
            return backend_types.DefaultCollection
        elif id == COLLECTION_RECENT:
            return backend_types._Collection(COLLECTION_RECENT, _('Recent'))
        else:
            cur = self._con.execute('''select id, name, supercollection
                from collection
                where id = ?''', (id,))
            result = cur.fetchone()
            cur.close()

            if result:
                return backend_types._Collection(*result)
            else:
                return None

    def get_recent_collection(self) -> backend_types._Collection:
        """ Returns the "Recent" collection, especially created for
        storing recently opened files. """
        collection = self.get_collection_by_id(COLLECTION_RECENT)
        assert collection is not None
        return collection

    def get_supercollection(self, collection: int) -> Optional[int]:
        """Return the supercollection of <collection>."""
        cur = self._con.execute('''select supercollection from Collection
            where id = ?''', (collection,))
        supercollection: Optional[int] = cur.fetchone()
        return supercollection

    def add_book(self, path: str, collection: Optional[int] = None) -> bool:
        """Add the archive at <path> to the library. If <collection> is
        not None, it is the collection that the books should be put in.
        Return True if the book was successfully added (or was already
        added).
        """
        if self._con is None:
            return False
        path = os.path.abspath(path)
        name = os.path.basename(path)
        try:
            info = archive_tools.get_archive_info(path)
        except Exception as error:
            # A damaged archive must not stop the rest of an add or a scan.
            log.warning(_('! Could not read book "%(path)s": %(error)s'),
                        {'path': path, 'error': error})
            return False
        if info is None or self._con is None:
            return False
        format, pages, size = info

        # Thumbnail for the newly added book will be generated once it
        # is actually needed with get_book_thumbnail().
        old = self._find_book_id(path)
        try:
            cursor = self._con.cursor()
            if old is not None:
                cursor.execute('''update Book set
                    name = ?, pages = ?, format = ?, size = ?
                    where id = ?''', (name, pages, format, size, old))
                book_id = old
            else:
                cursor.execute('''insert into Book
                    (name, path, pages, format, size)
                    values (?, ?, ?, ?, ?)''',
                               (name, path, pages, format, size))
                book_id = cursor.lastrowid

                book = backend_types._Book(book_id, name, path, pages,
                                           format, size, datetime.datetime.now().isoformat())
            cursor.close()

            if old is None:
                self.book_added(book)
                # Subscribers can close the library or remove the book/destination.
                if self._con is None:
                    return True
                book = self.get_book_by_id(book_id)
                if book is None or book.path != path:
                    return True
                self.book_added_to_collection(book, None)
                if self._con is None or self.get_book_path(book_id) != path:
                    return True

            self.add_book_to_collection(book_id, collection)

            return True
        except dbapi2.Error:
            log.error(_('! Could not add book "%s" to the library'), path)
            return False

    @callback.Callback
    def book_added(self, book: backend_types._Book) -> None:
        """ Event that triggers when a new book is successfully added to the
        library.
        @param book: L{_Book} instance of the newly added book.
        """
        pass

    @callback.Callback
    def book_added_to_collection(self, book: backend_types._Book, collection_id: Optional[int]) -> None:
        """ Event that triggers when a book is added to the
        specified collection.
        @param book: L{_Book} instance of the added book.
        @param collection_id: ID of the collection, or None for a new library book.
        """
        pass

    @callback.Callback
    def books_removed(self) -> None:
        """ Event that triggers after books were removed from the library
        outside the library window. """
        pass

    def add_collection(self, name: str) -> bool:
        """Add a new collection with <name> to the library. Return True
        if the collection was successfully added.
        """
        try:
            # The Recent pseudo collection initializes the lowest rowid
            # with -2, meaning that instead of starting from 1,
            # auto-incremental will start from -1. Avoid this.
            cur = self._con.execute('''select max(id) from collection''')
            maxid = cur.fetchone()
            if maxid is not None and maxid < 1:
                self._con.execute('''insert into collection
                    (id, name) values (?, ?)''', (1, name))
            else:
                self._con.execute('''insert into Collection
                    (name) values (?)''', (name,))
            return True
        except dbapi2.Error:
            log.error(_('! Could not add collection "%s"'), name)
        return False

    def add_book_to_collection(self, book: int, collection: Optional[int]) -> None:
        """Put a real book into an existing collection; All books needs no row."""
        if self._con is None or collection is None or collection == -1:
            return
        try:
            cursor = self._con.execute('''insert into Contain (collection, book)
                select id, ? from Collection where id = ?
                and exists (select 1 from Book where id = ?)''', (book, collection, book))
            inserted = cursor.rowcount
            cursor.close()
            if inserted:
                self.book_added_to_collection(self.get_book_by_id(book), collection)
        except dbapi2.DatabaseError:  # E.g. book already in collection.
            pass
        except dbapi2.Error:
            log.error(_('! Could not add book %(book)s to collection %(collection)s'),
                      {"book": book, "collection": collection})

    def add_collection_to_collection(self, subcollection: int, supercollection: Optional[int]) -> None:
        """Put <subcollection> into <supercollection>, or put
        <subcollection> in the root if <supercollection> is None.
        """
        if supercollection is None:
            self._con.execute('''update Collection
                set supercollection = NULL
                where id = ?''', (subcollection,))
        else:
            self._con.execute('''update Collection
                set supercollection = ?
                where id = ?''', (supercollection, subcollection))

    def rename_collection(self, collection: int, name: str) -> bool:
        """Rename the <collection> to <name>. Return True if the renaming
        was successful.
        """
        try:
            self._con.execute('''update Collection set name = ?
                where id = ?''', (name, collection))
            return True
        except dbapi2.DatabaseError:  # E.g. name taken.
            pass
        except dbapi2.Error:
            log.error(_('! Could not rename collection to "%s"'), name)
        return False

    def duplicate_collection(self, collection: int) -> bool:
        """Duplicate the <collection> by creating a new collection
        containing the same books. Return True if the duplication was
        successful.
        """
        name = self.get_collection_name(collection)
        if name is None:  # Original collection does not exist.
            return False
        copy_name = name + ' ' + _('(Copy)')
        while self.get_collection_by_name(copy_name):
            copy_name = copy_name + ' ' + _('(Copy)')
        if not self.add_collection(copy_name):
            return False
        copy_collection = self._con.execute('''select id from Collection
            where name = ?''', (copy_name,)).fetchone()
        self._con.execute('''insert or ignore into Contain (collection, book)
            select ?, book from Contain
            where collection = ?''', (copy_collection, collection))
        return True

    def clean_collection(self, collection: Optional[int] = None) -> int:
        """ Removes files from <collection> that no longer exist. If <collection>
        is None, all collections are cleaned. Returns the number of deleted books. """
        book_ids = self.get_books_in_collection(collection)
        deleted = 0
        for id in book_ids:
            path = self.get_book_path(id)
            if path and not os.path.isfile(path):
                self.remove_book(id)
                deleted += 1

        return deleted

    def remove_book(self, book: int) -> None:
        """Remove the <book> from the library."""
        path = self.get_book_path(book)
        if path is not None:
            thumbnailer = thumbnail_tools.Thumbnailer(dst_dir=constants.LIBRARY_COVERS_PATH)
            thumbnailer.delete(path)
        self._con.execute('delete from Book where id = ?', (book,))
        self._con.execute('delete from Contain where book = ?', (book,))
        # SQLite reuses the highest deleted id for the next book.
        self._con.execute('delete from recent where book = ?', (book,))

    def remove_collection(self, collection: int) -> None:
        """Remove the <collection> (sans books) from the library."""
        self._con.execute('''update watchlist set collection = NULL
            where collection = ?''', (collection,))
        self._con.execute('delete from Collection where id = ?', (collection,))
        self._con.execute('delete from Contain where collection = ?',
                          (collection,))
        self._con.execute('''update Collection set supercollection = NULL
            where supercollection = ?''', (collection,))

    def remove_book_from_collection(self, book: int, collection: int) -> None:
        """Remove <book> from <collection>."""
        self._con.execute('''delete from Contain
            where book = ? and collection = ?''', (book, collection))

    def execute(self, *args) -> Any:
        """ Passes C{args} directly to the C{execute} method of the SQL
        connection. """
        return self._con.execute(*args)

    def begin_transaction(self) -> None:
        """ Normally, the connection is in auto-commit mode. Calling
        this method will switch to transactional mode, automatically
        starting a transaction when a DML statement is used. """
        self._con.isolation_level = 'IMMEDIATE'

    def end_transaction(self) -> None:
        """ Commits any changes to the database and switches back
        to auto-commit mode. """
        self._con.commit()
        self._con.isolation_level = None

    def close(self) -> None:
        """Commit changes and close cleanly."""
        self.watchlist.stop_scan(shutdown=True)
        if self._con is not None:
            self._con.commit()
            self._con.close()
            self._con = None

        global _backend
        _backend = None

    def _table_exists(self, table: str) -> bool:
        """ Checks if C{table} exists in the database. """
        cursor = self._con.cursor()
        exists = cursor.execute('pragma table_info(%s)' % table).fetchone() is not None
        cursor.close()
        return exists

    def _library_version(self) -> int:
        """ Examines the library database structure to determine
        which version of MComix created it.

        @return C{version} from the table C{Info} if available,
        C{0} if there is no such table. C{-1} if the database has not been
        created yet."""

        tables = ('book', 'collection', 'contain')
        for table in tables:
            if not self._table_exists(table):
                return -1

        if self._table_exists('info'):
            cursor = self._con.cursor()
            version = cursor.execute('''select value from info
                where key = 'version' ''').fetchone()
            cursor.close()

            if not version:
                log.warning(_('Could not determine library database version!'))
                return -1
            else:
                return int(version)
        else:
            return 0

    def _create_tables(self) -> None:
        """ Creates all required tables in the database. """
        self._create_table_book()
        self._create_table_collection()
        self._create_table_contain()
        self._create_table_info()
        self._create_table_watchlist()
        self._create_table_recent()

    def _create_table_book(self) -> None:
        self._con.execute('''create table if not exists book (
            id integer primary key,
            name text,
            path text unique,
            pages integer,
            format integer,
            size integer,
            added datetime default current_timestamp)''')

    def _create_table_collection(self) -> None:
        self._con.execute('''create table if not exists collection (
            id integer primary key,
            name text unique,
            supercollection integer)''')

    def _create_table_contain(self) -> None:
        self._con.execute('''create table if not exists contain (
            collection integer not null,
            book integer not null,
            primary key (collection, book))''')

    def _create_table_info(self) -> None:
        self._con.execute('''create table if not exists info (
            key text primary key,
            value text)''')
        self._con.execute('''insert into info
            (key, value) values ('version', ?)''',
                          (str(_LibraryBackend.DB_VERSION),))

    def _create_table_watchlist(self) -> None:
        self._con.execute('''create table if not exists watchlist (
            path text primary key,
            collection integer references collection (id) on delete set null,
            recursive boolean not null)''')

    def _create_table_recent(self) -> None:
        self._con.execute('''create table if not exists recent (
            book integer primary key,
            page integer,
            time_set datetime)''')
        self._con.execute('''insert or ignore into collection (id, name)
            values (?, ?)''', (COLLECTION_RECENT, 'RECENT'))


_backend = None


def LibraryBackend() -> _LibraryBackend:
    """ Returns the singleton instance of the library backend. """
    global _backend
    if _backend is not None:
        return _backend
    else:
        _backend = _LibraryBackend()
        return _backend

# vim: expandtab:sw=4:ts=4
