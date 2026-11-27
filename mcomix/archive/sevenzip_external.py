# -*- coding: utf-8 -*-

""" 7z archive extractor. """

import contextlib
import re
import os
import subprocess
import tempfile
import zlib
from typing import Iterator, List, Optional

from mcomix import log
from mcomix import process
from mcomix.archive import archive_base
from mcomix.archive.archive_base import ArchiveError
from mcomix.i18n import _

# Filled on-demand by SevenZipArchive
_7z_executable = -1


class SevenZipArchive(archive_base.NonUnicodeArchive):
    """ 7z file extractor using the 7z executable. 7z lists each member's
    size and streams every member, in listing order, to standard output.
    It also reads RAR, LHA and the ZIP archives zipfile can't. """

    # Since we're using an external program for extraction,
    # concurrent calls are supported.
    support_concurrent_extractions = True

    STATE_HEADER, STATE_LISTING, STATE_FOOTER = 1, 2, 3

    class EncryptedHeader(Exception):
        pass

    def __init__(self, archive):
        super(SevenZipArchive, self).__init__(archive)
        self._is_solid = False
        #: (member, size, CRC32 or None) of each non-empty member.
        self._contents = []
        #: Member names of the listing being parsed.
        self._filenames = []
        self._lists_crc32 = False
        self._member_encrypted = False

    def _get_executable(self):
        return SevenZipArchive._find_7z_executable()

    def _get_password_argument(self):
        if self._is_encrypted:
            self._get_password()
            return '-p' + self._password
        else:
            # Add an empty password anyway, to prevent deadlock on reading for
            # input if we did not correctly detect the archive is encrypted.
            return '-p'

    def _get_list_arguments(self):
        args = [self._get_executable(), 'l', '-slt', '-sccUTF-8']
        args.append(self._get_password_argument())
        args.extend(('--', self.archive))
        return args

    def _get_extract_arguments(self, list_file=None):
        args = [self._get_executable(), 'x', '-so', '-sccUTF-8']
        if list_file is not None:
            args.append('-i@' + list_file)
        args.append(self._get_password_argument())
        args.extend(('--', self.archive))
        return args

    def _parse_list_output_line(self, line):
        """ Start parsing after the first delimiter (bunch of - characters),
        and end when delimiters appear again. Format:
        Date <space> Time <space> Attr <space> Size <space> Compressed <space> Name"""

        if line.startswith('----------'):
            if self._state == self.STATE_HEADER:
                # First delimiter reached, start reading from next line.
                self._state = self.STATE_LISTING
            elif self._state == self.STATE_LISTING:
                # Last delimiter read, stop reading from now on.
                self._state = self.STATE_FOOTER

            return None

        if self._state == self.STATE_HEADER:
            if re.match(r'^error:.+?can\s?not open encrypted archive\. wrong password\?$', line,
                        re.IGNORECASE):
                self._is_encrypted = True
                raise self.EncryptedHeader()
            if 'Solid = +' == line:
                self._is_solid = True
            if line.startswith('Type = '):
                # Other formats list another checksum in the CRC field
                # (LHA has CRC-16).
                self._lists_crc32 = line[7:] in ('7z', 'Rar', 'Rar5')

        if self._state == self.STATE_LISTING:
            if line.startswith('Path = '):
                self._path = line[7:]
                self._member_encrypted = False
                return self._path
            # Only some formats list Folder; Attributes has D for a
            # directory before any Unix mode string (lowercase d).
            if 'Folder = +' == line or \
                    (line.startswith('Attributes = ') and 'D' in line[13:].split(' ')[0]):
                self._drop_directory()
            elif line.startswith('Size = '):
                filesize = int(line[7:])
                if filesize > 0:
                    self._contents.append((self._path, filesize, None))
            elif line.startswith('CRC = ') and self._lists_crc32 and not self._member_encrypted:
                self._set_crc32(line[6:])
            elif 'Encrypted = +' == line:
                self._is_encrypted = True
                # RAR5 stores a password-dependent checksum for encrypted
                # members; 7z lists this line before or after the CRC.
                self._member_encrypted = True
                self._set_crc32(None)

        return None

    def _set_crc32(self, value: Optional[str]) -> None:
        """Attach a listed CRC32, or None for no check, to the member just
        listed in _contents."""
        if self._contents and self._contents[-1][0] == self._path:
            path, size, crc = self._contents[-1]
            crc = int(value, 16) if value and re.fullmatch(r'[0-9A-Fa-f]{8}', value) else None
            self._contents[-1] = (path, size, crc)

    @staticmethod
    def _check_crc32(filename: str, data: bytes, crc: Optional[int]) -> None:
        # 7z's stream keeps the full size on a data error, so only the
        # listed CRC tells a damaged or shifted member apart.
        if crc is not None and zlib.crc32(data) != crc:
            raise ArchiveError('Extraction of %s failed (CRC mismatch)' % filename)

    def _drop_directory(self) -> None:
        """Remove the entry just listed, a directory, from the listing: its
        name would otherwise be a member and reserve the folder's name."""
        if self._filenames and self._filenames[-1] == self._path:
            self._filenames.pop()
            self._path = None

    def is_solid(self):
        return self._is_solid

    def supports_random_access(self) -> bool:
        return not self._is_solid

    def get_entry_size(self, filename: str) -> Optional[int]:
        original = self._original_filename(filename)
        for path, size, crc in self._contents:
            if path == original:
                return size
        return None

    def iter_contents(self):
        if not self._get_executable():
            return

        # We'll try at most 2 times:
        # - the first time without a password
        # - a second time with a password if the header is encrypted
        for retry_count in range(2):
            #: Indicates which part of the file listing has been read.
            self._state = self.STATE_HEADER
            #: Current path while listing contents.
            self._path = None
            self._contents = []
            try:
                proc = subprocess.run(
                    self._get_list_arguments(), stdin=process.NULL,
                    stdout=process.PIPE, stderr=process.STDOUT,
                    encoding='utf-8', errors='strict',
                    creationflags=process._get_creationflags())
            except UnicodeDecodeError as exc:
                raise ArchiveError(_('The archive tool returned a non-UTF-8 file listing. '
                                     'Update 7-Zip.')) from exc
            self._filenames = []
            try:
                for line in proc.stdout.splitlines():
                    filename = self._parse_list_output_line(line.rstrip(os.linesep))
                    if filename is not None:
                        self._filenames.append(filename)
            except self.EncryptedHeader:
                # The header is encrypted, try again
                # if it was our first attempt.
                if 0 == retry_count:
                    continue
            if proc.returncode not in (0, 1):
                raise ArchiveError(
                    _('%(tool)s could not list the archive (exit status %(status)d). '
                      'Check the archive or update %(tool)s: %(error)s') %
                    {'tool': '7-Zip', 'status': proc.returncode,
                     'error': proc.stdout.strip()[-2000:]})
            for filename in self._filenames:
                yield self._unicode_filename(filename)
            # Last and/or successful attempt.
            break

    def iter_extract(self, entries, destination_dir):
        if not self._get_executable():
            return

        proc = process.popen(self._get_extract_arguments())
        try:
            wanted = {self._original_filename(name): name for name in entries}
            for filename, filesize, crc in self._contents:
                data = proc.stdout.read(filesize)
                if len(data) < filesize:
                    raise ArchiveError(
                        'Extraction of %s failed (exit status %d)' % (filename, proc.wait()))
                if filename not in wanted:
                    continue
                self._check_crc32(filename, data, crc)
                name = wanted.pop(filename)
                with self._create_file(self._destination_path(destination_dir, name)) as new:
                    new.write(data)
                yield name
                if not wanted:
                    break
        finally:
            proc.stdout.close()
            proc.wait()

    def extract(self, filename, destination_dir):
        """ Extract <filename> from the archive to <destination_dir>. """
        assert isinstance(filename, str) and \
               isinstance(destination_dir, str)

        with self._member_list_file(filename) as list_file:
            # Stream to the file: <filename> may be a large nested archive.
            self._extract_to_file(self._get_extract_arguments(list_file=list_file),
                                  filename, destination_dir)

    def read(self, filename: str) -> bytes:
        """ Read <filename> from the archive into memory. """
        assert isinstance(filename, str)

        with self._member_list_file(filename) as list_file:
            return self._run_extract(self._get_extract_arguments(list_file=list_file),
                                     filename)

    def _run_extract(self, args: List, filename: str, output=None) -> bytes:
        """ Runs <args>, a command extracting <filename> to standard output,
        and returns that output, or writes it to the file object <output>
        (and returns b''). Raises ArchiveError if the command failed. """
        proc = subprocess.run(args, stdin=process.NULL,
                              stdout=process.PIPE if output is None else output,
                              stderr=process.PIPE,
                              creationflags=process._get_creationflags())
        error = proc.stderr.decode('utf-8', 'replace').strip()
        # For 7z, 1 is a warning: the data is still usable.
        if proc.returncode not in (0, 1):
            raise ArchiveError('Extraction of %s failed (exit status %d): %s'
                               % (filename, proc.returncode, error))
        if error:
            log.warning(_('Extraction of %(archivefile)s might have failed: %(error)s'),
                        {'archivefile': filename, 'error': error})
        return proc.stdout if output is None else b''

    def _extract_to_file(self, args: List, filename: str,
                         destination_dir: str) -> None:
        """ Runs <args> (see _run_extract()) to write <filename> to
        <destination_dir>. No partial file is left if extraction fails. """
        path = self._destination_path(destination_dir, filename)
        try:
            with self._create_file(path) as output:
                self._run_extract(args, filename, output)
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise

    @contextlib.contextmanager
    def _member_list_file(self, filename: str) -> Iterator[str]:
        """ Yields the path of a temporary list file selecting <filename>. """
        if not self._get_executable():
            raise ArchiveError('No executable found to extract from %s'
                               % self.archive)

        tmplistfile = tempfile.NamedTemporaryFile(prefix='mcomix.7z.', delete=False)
        try:
            desired_filename = self._original_filename(filename)
            if isinstance(desired_filename, str):
                desired_filename = desired_filename.encode('utf-8')

            tmplistfile.write(desired_filename + os.linesep.encode('utf-8'))
            tmplistfile.close()
            yield tmplistfile.name
        finally:
            tmplistfile.close()
            os.unlink(tmplistfile.name)

    @staticmethod
    def _find_7z_executable():
        """ Tries to start 7z, and returns either '7z' if
        it was started successfully or None otherwise. """
        global _7z_executable
        if _7z_executable == -1:
            _7z_executable = process.find_executable(('7z',))
        return _7z_executable

    @staticmethod
    def is_available():
        return bool(SevenZipArchive._find_7z_executable())

# vim: expandtab:sw=4:ts=4
