# -*- coding: utf-8 -*-

""" Class for transparently handling an archive containing sub-archives. """

from mcomix.archive import archive_base
from mcomix import archive_tools
from mcomix import log

import os
import tempfile
from pathlib import Path
from typing import Optional

class RecursiveArchive(archive_base.BaseArchive):

    def __init__(self, archive, destination_dir):
        super(RecursiveArchive, self).__init__(archive.archive)
        self._main_archive = archive
        self._destination_dir = destination_dir
        self._archive_list = [archive]
        # Map entry name to its archive+name.
        self._entry_mapping = {}
        # Map archive to its root.
        self._archive_root = {}
        self._contents_listed = False
        self._contents = []
        self._sub_archive_dir = None
        #: Names the cached sub-archive files.
        self._sub_archive_count = 0

    def cancel(self) -> None:
        """Stop discovery between backend operations; retain live inputs."""
        super(RecursiveArchive, self).cancel()
        for archive in self._archive_list[:]:
            archive.cancel()

    def _iter_contents(self, archive, root=None):
        if archive not in self._archive_list:
            self._archive_list.append(archive)
        if self._cancelled.is_set():
            archive.cancel()
            return
        self._archive_root[archive] = root
        sub_archive_list = []
        for f in archive.iter_contents():
            if self._cancelled.is_set():
                return
            # Backends must publish safe names, including nested archive names
            # that are extracted during this listing, before Extractor sees them.
            self._destination_path(self._destination_dir, f)
            if archive_tools.is_archive_file(f):
                # We found a sub-archive, don't try to extract it now, as we
                # must finish listing the containing archive contents before
                # any extraction can be done.
                sub_archive_list.append(f)
                continue
            name = f
            if root is not None:
                name = os.path.join(root, name)
            self._entry_mapping[name] = (archive, f)
            yield name
        for f in sub_archive_list:
            if self._cancelled.is_set():
                return
            # Extract sub-archive.
            destination_dir = self._destination_dir
            if root is not None:
                destination_dir = self._destination_path(destination_dir, root)
            try:
                source_path = self._destination_path(destination_dir, f)
                archive.extract(f, destination_dir)
                if self._cancelled.is_set():
                    return
            except Exception as ex:
                log.warning('Could not extract sub-archive %s: %s', f, ex)
                continue
            sub_archive_ext = os.path.splitext(f)[1].lower()[1:]
            if self._sub_archive_dir is None:
                # Keep cached archive files out of the public member namespace.
                # A sibling also avoids collisions with any archive member.
                self._sub_archive_dir = tempfile.TemporaryDirectory(
                    prefix='mcomix.sub-archives.',
                    dir=str(Path(self._destination_dir).resolve().parent))
            self._sub_archive_count += 1
            sub_archive_path = self._destination_path(
                self._sub_archive_dir.name,
                '%04u.%s' % (self._sub_archive_count, sub_archive_ext))
            os.rename(source_path, sub_archive_path)
            sub_root = f
            if root is not None:
                sub_root = os.path.join(root, sub_root)
            # A damaged nested archive must not cost the book its other
            # pages, so list it completely before publishing any of it.
            listed = len(self._archive_list)
            try:
                sub_archive = archive_tools.get_archive_handler(sub_archive_path)
                if sub_archive is None:
                    log.warning('Non-supported archive format: %s', f)
                    continue
                names = list(self._iter_contents(sub_archive, sub_root))
            except Exception as ex:
                log.warning('Could not list sub-archive %s: %s', f, ex)
                # It and the archives inside it are no part of the book
                # (password, solidity, random access).
                failed = self._archive_list[listed:]
                del self._archive_list[listed:]
                for failed_archive in failed:
                    failed_archive.close()
                continue
            yield from names

    def iter_contents(self):
        if self._contents_listed:
            for f in self._contents:
                yield f
            return
        self._contents = []
        for f in self._iter_contents(self._main_archive):
            self._contents.append(f)
            yield f
        if self._cancelled.is_set():
            return
        self._contents_listed = True
        # We need all archives to support concurrent extractions.
        self.support_concurrent_extractions = all(
            archive.support_concurrent_extractions for archive in self._archive_list)

    def extract(self, filename, destination_dir):
        self._destination_path(destination_dir, filename)
        archive, name = self._entry_mapping[filename]
        root = self._archive_root[archive]
        if root is not None:
            destination_dir = self._destination_path(destination_dir, root)
        log.debug('extracting from %s to %s: %s',
                  archive.archive, destination_dir, filename)
        archive.extract(name, destination_dir)

    def iter_extract(self, entries, destination_dir):
        # Unfortunately we can't just rely on BaseArchive default
        # implementation if solid archives are to be correctly supported:
        # we need to call iter_extract (not extract) for each archive ourselves.
        wanted = set(entries)
        for name in wanted:
            self._destination_path(destination_dir, name)
        for archive in self._archive_list:
            archive_wanted = {}
            for name in wanted:
                name_archive, name_archive_name = self._entry_mapping[name]
                if name_archive == archive:
                    archive_wanted[name_archive_name] = name
            if 0 == len(archive_wanted):
                continue
            root = self._archive_root[archive]
            archive_destination_dir = destination_dir
            if root is not None:
                archive_destination_dir = self._destination_path(destination_dir, root)
            log.debug('extracting from %s to %s: %s',
                      archive.archive, archive_destination_dir,
                      ' '.join(list(archive_wanted.keys())))
            for f in archive.iter_extract(list(archive_wanted.keys()), archive_destination_dir):
                yield archive_wanted[f]
            wanted -= set(archive_wanted.values())
            if 0 == len(wanted):
                break

    def read(self, filename: str) -> bytes:
        archive, name = self._entry_mapping[filename]
        return archive.read(name)

    def get_entry_size(self, filename: str) -> Optional[int]:
        archive, name = self._entry_mapping[filename]
        return archive.get_entry_size(name)

    def stored_name(self, filename: str) -> Optional[str]:
        archive, name = self._entry_mapping[filename]
        return archive.stored_name(name) if archive is self._main_archive else None

    def resolve_password(self) -> None:
        for archive in self._archive_list:
            archive.resolve_password()

    def supports_random_access(self) -> bool:
        # Only if every archive involved supports it.
        return all(archive.supports_random_access()
                   for archive in self._archive_list)

    def is_solid(self):
        # We're solid if at least one archive is solid.
        for archive in self._archive_list:
            if archive.is_solid():
                return True
        return False

    def close(self):
        for archive in self._archive_list:
            archive.close()
        if self._sub_archive_dir is not None:
            self._sub_archive_dir.cleanup()
            self._sub_archive_dir = None

