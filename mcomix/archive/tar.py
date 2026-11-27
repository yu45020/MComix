# -*- coding: utf-8 -*-

""" Unicode-aware wrapper for tarfile.TarFile. """

import io
import tarfile
from typing import Optional

from . import archive_base

class TarArchive(archive_base.NonUnicodeArchive):
    def __init__(self, archive):
        super(TarArchive, self).__init__(archive)
        # Track if archive contents have been listed at least one time: this
        # must be done before attempting to extract contents.
        self._contents_listed = False
        self._contents = []
        # Map Unicode names to their TarInfo.
        self._members = {}
        self.tar = None

    def is_solid(self):
        return True

    def supports_random_access(self) -> bool:
        # Entries of an uncompressed tar can be read directly
        # at their offset; compressed streams must be decompressed
        # from the start.
        return type(self.tar.fileobj) is io.BufferedReader

    def iter_contents(self):
        if self._contents_listed:
            for name in self._contents:
                yield name
            return
        # Make sure we start back at the beginning of the tar.
        self.tar = tarfile.open(self.archive, 'r')
        self._contents = []
        self._members = {}
        while True:
            info = self.tar.next()
            if info is None:
                break
            if info.isdir():
                continue
            name = self._unicode_filename(info.name)
            self._contents.append(name)
            self._members[name] = info
            yield name
        self._contents_listed = True

    def extract(self, filename, destination_dir):
        content = self.read(filename)
        with self._create_file(self._destination_path(destination_dir, filename)) as new:
            new.write(content)

    def read(self, filename: str) -> bytes:
        file_object = self.tar.extractfile(self._members[filename])
        try:
            return file_object.read()
        finally:
            file_object.close()

    def get_entry_size(self, filename: str) -> Optional[int]:
        return self._members[filename].size

    def iter_extract(self, entries, destination_dir):
        if not self._contents_listed:
            self.list_contents()
        for f in super(TarArchive, self).iter_extract(entries, destination_dir):
            yield f

    def close(self):
        if self.tar is not None:
            self.tar.close()
            self.tar = None

# vim: expandtab:sw=4:ts=4
