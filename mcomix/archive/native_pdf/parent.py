# -*- coding: utf-8 -*-

"""Multiprocessing PDF handler."""

import os
import threading
from mcomix.archive import archive_base
from mcomix.archive.native_pdf.manager import MuPDFProcessWrangler

from typing import Generator, List


class MuPDFArchive(archive_base.BaseArchive):
    """PDF file reader/extractor using a serialized child process."""

    # Concurrent callers are serialized by the child owner.
    support_concurrent_extractions = True

    def __init__(self, archive: str) -> None:
        super().__init__(archive)
        self._mgr = None
        self._mgr_lock = threading.Lock()

    @staticmethod
    def is_available() -> bool:
        """Report whether this extractor is available (always true)."""
        return True

    def cancel(self) -> None:
        """Stop the child too: a request blocked on a hung MuPDF call holds
        the locks close() needs, so only ending the process frees them."""
        super().cancel()
        mgr = self._mgr
        if mgr is not None:
            mgr.terminate()

    def close(self) -> None:
        """Release the document and child before returning to file operations."""
        with self._mgr_lock:
            if self._mgr is not None:
                self._mgr.close()
                self._mgr = None

    @property
    def mgr(self) -> MuPDFProcessWrangler:
        """Lazily own one child shared by listing and extraction callers."""
        with self._mgr_lock:
            if self._mgr is None:
                self._mgr = MuPDFProcessWrangler(self.archive)
            return self._mgr

    def iter_contents(self) -> Generator[str, None, None]:
        """Generate page filenames."""
        return self.mgr.iter_contents()

    def extract(self, filename: str, destination_dir: str) -> None:
        """Extract the named page file to a directory."""
        self._destination_path(destination_dir, filename)
        os.makedirs(destination_dir, exist_ok=True)
        list(self.mgr.extract_pages([filename], destination_dir))

    def iter_extract(self, entries: List[str], destination_dir: str) -> Generator[str, None, None]:
        """Return a generator of extracted filepaths."""
        os.makedirs(destination_dir, exist_ok=True)
        return self.mgr.extract_pages(entries, destination_dir)
