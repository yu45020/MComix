# -*- coding: utf-8 -*-

""" Unicode-aware wrapper for zipfile.ZipFile. """

import zipfile
from typing import Dict, Optional

from mcomix import log
from mcomix import i18n
from mcomix.archive import archive_base
from mcomix.i18n import _


def is_py_supported_zipfile(path: str) -> bool:
    """Check that every member's compression is readable by this Python build."""
    supported = {zipfile.ZIP_STORED}
    for method, codec in ((zipfile.ZIP_DEFLATED, zipfile.zlib),
                          (zipfile.ZIP_BZIP2, zipfile.bz2),
                          (zipfile.ZIP_LZMA, zipfile.lzma)):
        if codec is not None:
            supported.add(method)
    with zipfile.ZipFile(path, 'r') as zip_file:
        return all(info.compress_type in supported for info in zip_file.infolist())

def _member_name(info: zipfile.ZipInfo) -> str:
    """Decode a name without the UTF-8 flag as UTF-8, then cp932, else cp437.

    Old Japanese tools wrote cp932 names without the flag; the manga manager
    that opens books by member name decodes them by the same rule.
    """
    if info.flag_bits & 0x800:
        return info.filename
    # orig_filename keeps the raw bytes: filename has '\' replaced on Windows,
    # which would corrupt cp932 characters whose second byte is 0x5C.
    raw = info.orig_filename.encode('cp437')
    for encoding in ('utf-8', 'cp932'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return info.orig_filename


class ZipArchive(archive_base.NonUnicodeArchive):
    def __init__(self, archive):
        super(ZipArchive, self).__init__(archive)
        self.zip = zipfile.ZipFile(archive, 'r')
        self._members: Dict[str, zipfile.ZipInfo] = {}

    def iter_contents(self):
        if self._has_encryption():
            self._get_password()
            self.zip.setpassword(i18n.to_utf8(self._password))

        for info in self.zip.infolist():
            name = _member_name(info)
            if not info.is_dir() and not name.endswith(('/', '\\')):
                self._members[name] = info
                yield self._unicode_filename(name)

    def extract(self, filename, destination_dir):
        content = self.read(filename)
        with self._create_file(self._destination_path(destination_dir, filename)) as new:
            new.write(content)

    def read(self, filename: str) -> bytes:
        zipinfo = self._members[self._original_filename(filename)]
        content = self.zip.read(zipinfo)
        if len(content) != zipinfo.file_size:
            log.warning(_('%(filename)s\'s extracted size is %(actual_size)d bytes,'
                ' but should be %(expected_size)d bytes.'
                ' The archive might be corrupt or in an unsupported format.'),
                { 'filename' : filename, 'actual_size' : len(content),
                  'expected_size' : zipinfo.file_size })

        return content

    def supports_random_access(self) -> bool:
        return True

    def get_entry_size(self, filename: str) -> Optional[int]:
        return self._members[self._original_filename(filename)].file_size

    def close(self):
        self.zip.close()

    def _has_encryption(self):
        """ Checks all files in the archive for encryption.
        Returns True if at least one encrypted file was found. """
        for zipinfo in self.zip.infolist():
            if zipinfo.flag_bits & 0x1: # File is encrypted
                return True

        return False

# vim: expandtab:sw=4:ts=4
