# -*- coding: utf-8 -*-

""" Base class for unified handling of various archive formats. Used for simplifying
extraction and adding new archive formats. """

import os
import ntpath
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional

from mcomix import portability
from mcomix import i18n
from mcomix import callback
from mcomix.archive import password as archive_password


class ArchiveError(Exception):
    """ Reading or extracting an archive member failed. """
    pass


class BaseArchive(object):
    """ Base archive interface. All filenames passed from and into archives
    are expected to be Unicode objects. Archive files are converted to
    Unicode with some guess-work. """

    """ True if concurrent calls to extract is supported. """
    support_concurrent_extractions = False

    def __init__(self, archive):
        assert isinstance(archive, str), "File should be an Unicode string."

        self.archive = archive
        self._password = None
        #: Set by subclasses when listing finds encrypted entries.
        self._is_encrypted = False
        self._event = threading.Event()
        self._cancelled = threading.Event()
        if self.support_concurrent_extractions:
            # When multiple concurrent extractions are supported,
            # we need a lock to handle concurent calls to _get_password.
            self._lock = threading.Lock()
            self._waiting_for_password = False

    def iter_contents(self):
        """ Generator for listing the archive contents.
        """
        return
        yield

    def list_contents(self):
        """ Returns a list of unicode filenames relative to the archive root.
        These names do not necessarily exist in the actual archive since they
        need to saveable on the local filesystems, so some characters might
        need to be replaced. """

        return [f for f in self.iter_contents()]

    def extract(self, filename, destination_dir):
        """ Extracts the file specified by <filename>. This filename must
        be obtained by calling list_contents(). The file is saved to
        <destination_dir>. """

        assert isinstance(filename, str) and \
            isinstance(destination_dir, str)

    def iter_extract(self, entries, destination_dir):
        """ Generator to extract <entries> from archive to <destination_dir>. """
        wanted = set(entries)
        for filename in self.iter_contents():
            if not filename in wanted:
                continue
            self.extract(filename, destination_dir)
            yield filename
            wanted.remove(filename)
            if 0 == len(wanted):
                break

    def supports_random_access(self) -> bool:
        """ Returns True if read() of an arbitrary entry is cheap, i.e. does
        not require decompressing unrelated entries. Only meaningful after the
        contents have been listed. """
        return False

    def read(self, filename: str) -> bytes:
        """ Returns the contents of <filename>, a name obtained by calling
        list_contents(). This default implementation extracts the file to a
        temporary directory and reads it back. """
        assert isinstance(filename, str)
        tmp_dir = tempfile.mkdtemp(prefix='mcomix.read.')
        try:
            path = self._destination_path(tmp_dir, filename)
            self.extract(filename, tmp_dir)
            with open(path, 'rb') as fp:
                return fp.read()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def get_entry_size(self, filename: str) -> Optional[int]:
        """ Returns the uncompressed size of <filename> if it is known
        from the listing, or None otherwise. """
        return None

    def stored_name(self, filename: str) -> Optional[str]:
        """ Returns the name <filename> has inside the archive, before it was
        made safe for the filesystem, or None for a member of a nested archive. """
        return filename

    def close(self):
        """ Closes the archive and releases held resources. """

        pass

    def is_solid(self):
        """ Returns True if the archive is solid and extraction should be done
        in one pass. """
        return False

    def _replace_invalid_filesystem_chars(self, filename: str) -> str:
        """Return a relative member path, preserving ordinary dots and folders.

        Treat both archive separator conventions as directories, even on Unix.
        Colons are never paths or streams on the extraction filesystem.
        The name mapping separately resolves collisions introduced here.
        """
        unsafe = set(portability.invalid_filesystem_chars()) | {'\x00', ':'}
        parts = []
        for part in re.split(r'[/\\]', filename):
            if part in ('', '.', '..'):
                continue
            part = ''.join('_' if c in unsafe else c for c in part)
            if os.name == 'nt':
                # Win32 aliases trailing spaces/dots and DOS device basenames.
                part = part.rstrip(' .') or '_'
                if self._is_windows_device(part):
                    part = '_' + part
            parts.append(part)
        return os.path.join(*parts) if parts else '_'

    @staticmethod
    def _is_windows_device(component: str) -> bool:
        stem = component.split('.')[0].rstrip(' ').upper()
        return stem in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} or \
            re.fullmatch(r'(COM|LPT)[1-9\u00b9\u00b2\u00b3]', stem) is not None

    @staticmethod
    def _destination_path(destination_dir: str, filename: str) -> str:
        """Check a public member path before opening or passing it to a tool.

        Check Win32 path syntax on every platform, and resolved containment to
        reject existing symlink/junction parents pointing outside the root.
        This is independent of the listing sanitizer.
        """
        parts = re.split(r'[/\\]', filename)
        if not filename or ntpath.splitdrive(filename)[0] or \
                filename.startswith(('/', '\\')) or \
                any(p == '..' or ':' in p or '\x00' in p for p in parts):
            raise ArchiveError('Unsafe archive member path: %s' % filename)
        if os.name == 'nt' and any(
                p not in ('', '.') and (p.endswith((' ', '.')) or BaseArchive._is_windows_device(p))
                for p in parts):
            raise ArchiveError('Unsafe archive member path: %s' % filename)
        # Path.resolve also follows Windows junctions on Python 3.7, where
        # os.path.realpath is only abspath.
        root = Path(destination_dir).resolve()
        path = root.joinpath(*parts).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ArchiveError('Archive member escapes destination: %s' % filename)
        if path == root:
            raise ArchiveError('Archive member escapes destination: %s' % filename)
        return str(path)

    def _create_file(self, dst_path):
        """ Open <dst_path> for writing, making sure base directory exists. """
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        return open(dst_path, 'wb')

    def cancel(self) -> None:
        """Wake password waiters and dismiss only this archive's dialogs."""
        if self._cancelled.is_set():
            return
        self._cancelled.set()
        self._password = ''
        self._event.set()
        archive_password.cancel_password(self)

    @callback.Callback
    def _password_required(self):
        """ Asks the user for a password and sets <self._password>.
        If <self._password> is None, no password has been requested yet.
        If an empty string is set, assume that the user did not provide
        a password. """

        if self._cancelled.is_set():
            self._event.set()
            return
        password = archive_password.ask_for_password(self)
        if password is None:
            password = ""

        self._password = password
        self._event.set()

    def resolve_password(self) -> None:
        """ Asks for the password now if listing found encrypted entries, so
        that reading them later does not have to wait for the user. Call it
        after listing, from a thread holding no lock the main thread needs. """
        if self._is_encrypted and self._password is None:
            self._get_password()

    def _get_password(self):
        if self._cancelled.is_set():
            return
        ask_for_password = self._password is None
        # Don't trigger concurrent password dialogs.
        if ask_for_password and self.support_concurrent_extractions:
            with self._lock:
                if self._waiting_for_password:
                    ask_for_password = False
                else:
                    self._waiting_for_password = True
        if ask_for_password:
            self._password_required()
        self._event.wait()

class NonUnicodeArchive(BaseArchive):
    """ Base class for archives that map raw member names to safe, distinct
    relative paths internally, and map those back for the archive format. """

    def __init__(self, archive):
        super(NonUnicodeArchive, self).__init__(archive)
        # Maps Unicode names to regular names as expected by the original archive format
        self.unicode_mapping = {}
        self._safe_names = {}
        self._directory_names = {}
        self._allocated_paths = set()

    @staticmethod
    def _fit_component(component: str, suffix: str = '') -> str:
        """Keep a collision suffix and extension within a filesystem component.

        Windows counts UTF-16 units; Unix filesystems commonly allow 255
        encoded bytes. Do not cut a character while making room for a suffix.
        """
        def length(text: str) -> int:
            if os.name == 'nt':
                return len(text.encode('utf-16-le', 'surrogatepass')) // 2
            return len(os.fsencode(text))

        def trim(text: str, budget: int) -> str:
            if length(text) <= budget:
                return text
            used = 0
            for index, char in enumerate(text):
                used += length(char)
                if used > budget:
                    return text[:index]
            return text

        stem, ext = os.path.splitext(component)
        if not ext and component.startswith('.'):
            # splitext('.jpg') has no extension, but page filtering does.
            stem, ext = '', component
        if suffix and not stem:
            stem = '_'
        ext = trim(ext, 255 - length(suffix) - bool(stem))
        ending = suffix + ext
        result = trim(stem, 255 - length(ending)) + ending
        if os.name == 'nt':
            result = result.rstrip(' .') or '_'
            if BaseArchive._is_windows_device(result):
                result = '_' + result[1:]
        return result

    def _unicode_filename(self, filename: str) -> str:
        """ Instead of returning archive members directly, map each filename
        through this function first to a safe, distinct relative path. """

        if filename in self._safe_names:
            return self._safe_names[filename]
        parts = [p for p in re.split(r'[/\\]', filename)
                 if p not in ('', '.', '..')] or ['_']
        parent = ''
        for index, part in enumerate(parts):
            directory = index < len(parts) - 1
            original_prefix = tuple(parts[:index + 1])
            if directory and original_prefix in self._directory_names:
                parent = self._directory_names[original_prefix]
                continue
            safe_part = self._replace_invalid_filesystem_chars(part)
            candidate = os.path.join(parent, self._fit_component(safe_part))
            suffix = 1
            while os.path.normcase(candidate) in self._allocated_paths:
                candidate = os.path.join(
                    parent, self._fit_component(safe_part, ' (%d)' % suffix))
                suffix += 1
            self._allocated_paths.add(os.path.normcase(candidate))
            if directory:
                self._directory_names[original_prefix] = candidate
            parent = candidate
        safe_name = parent
        self._safe_names[filename] = safe_name
        self.unicode_mapping[safe_name] = filename
        return safe_name

    def stored_name(self, filename: str) -> Optional[str]:
        # MobiArchive generates its names and never fills the mapping.
        return self.unicode_mapping.get(filename, filename)

    def _original_filename(self, filename):
        """ Map Unicode filename back to original archive name. """
        if filename in self.unicode_mapping:
            return self.unicode_mapping[filename]
        else:
            return i18n.to_utf8(filename)

# vim: expandtab:sw=4:ts=4
