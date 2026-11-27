"""Shared scaffolding for archive extraction and lifecycle regression tests."""

import os
from pathlib import Path
from types import SimpleNamespace
import threading
from typing import Any, Iterator, List, Sequence, Tuple
from unittest.mock import Mock

import pytest

from mcomix import archive_extractor, archive_tools, file_handler
from mcomix.archive import archive_base
from mcomix.preferences import prefs
from ._app_fakes import pump
from ._shells import file_handler_shell


class FakeArchive(archive_base.BaseArchive):
    """Backend double whose mode selects listing/extraction failures."""

    def __init__(self, mode: str = 'success', solid: bool = True, lazy: bool = False,
                 members: Sequence[str] = ('first.png', 'empty.txt', 'last.png')) -> None:
        super().__init__('fixture')
        self.mode = mode
        self.solid = solid
        self.lazy = lazy
        self.members = members
        self.closed = 0
        self.calls = 0
        self.batches: List[List[str]] = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def iter_contents(self) -> Iterator[str]:
        if self.mode == 'list-block':
            self.entered.set()
            assert self.release.wait(3)
        for name in self.members:
            yield name
            if self.mode == 'list-error':
                raise OSError('broken index')

    def resolve_password(self) -> None:
        if self.mode == 'password-error':
            raise OSError('password failed')

    def supports_random_access(self) -> bool:
        return self.lazy

    def is_solid(self) -> bool:
        return self.solid

    def extract(self, filename: str, destination_dir: str) -> None:
        self.calls += 1
        if self.mode == 'missing':
            return
        data = b'PARTIAL' if self.mode == 'extract-error' else b'COMPLETE'
        Path(destination_dir, filename).write_bytes(b'' if filename == 'empty.txt' else data)
        if self.mode == 'extract-error':
            raise OSError('bad data')

    def iter_extract(self, entries: List[str], destination_dir: str) -> Iterator[str]:
        self.batches.append(list(entries))
        if self.mode == 'batch-block':
            self.entered.set()
            assert self.release.wait(3)
        for filename in entries:
            if self.mode in ('omit', 'early-error') and filename == 'last.png':
                Path(destination_dir, filename).write_bytes(b'PARTIAL')
                if self.mode == 'early-error':
                    raise OSError('bad tail')
                return
            self.extract(filename, destination_dir)
            yield filename
            if self.mode == 'duplicate':
                yield filename
        if self.mode == 'duplicate':
            yield 'unexpected.png'

    def close(self) -> None:
        self.closed += 1


class Extractors:
    """Open real Extractors over a fake backend archive; close them all later."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._tmp_path = tmp_path
        self._monkeypatch = monkeypatch
        self._opened: List[archive_extractor.Extractor] = []

    def open(self, archive: archive_base.BaseArchive,
             lazy: bool = False) -> archive_extractor.Extractor:
        destination = self._tmp_path / str(len(self._opened))
        destination.mkdir()
        self._monkeypatch.setattr(archive_tools, 'get_recursive_archive_handler',
                                  lambda *args, **kwargs: archive)
        self._monkeypatch.setitem(prefs, 'lazy archive reading', lazy)
        self._monkeypatch.setitem(prefs, 'max extract threads', 2)
        extractor = archive_extractor.Extractor()
        self._opened.append(extractor)
        extractor.setup('fixture', str(destination))
        return extractor

    def close(self) -> None:
        for extractor in self._opened:
            extractor.close()
        pump(lambda: True)


def extractor_handler(extractor: archive_extractor.Extractor) -> file_handler.FileHandler:
    """Return a FileHandler shell serving the extractor's listed members."""
    handler = file_handler_shell()
    handler.archive_type = 1
    handler.file_loaded = True
    handler._condition = extractor._condition
    handler._extractor = extractor
    handler._stop_waiting = False
    handler._name_table = {
        os.path.join(extractor.get_directory(), name): name
        for name in extractor.get_files()}
    return handler


def real_file_handler(monkeypatch: pytest.MonkeyPatch) -> Tuple[file_handler.FileHandler, Any]:
    """Return a real FileHandler with persistence and window collaborators mocked."""
    monkeypatch.setitem(prefs, 'store recent file info', False)
    monkeypatch.setitem(prefs, 'stored dialog choices', {})
    monkeypatch.setattr(file_handler.backend, 'LibraryBackend', Mock())
    reader = Mock()
    reader.get_page.return_value = 1
    monkeypatch.setattr(file_handler.last_read_page, 'LastReadPage', lambda *args: reader)
    image = Mock(_image_files=[], _current_image_index=None)
    image.get_current_page.return_value = 1
    window = SimpleNamespace(imagehandler=image, statusbar=Mock(), osd=Mock(),
                             uimanager=Mock(), set_page=Mock(), set_cursor=Mock())
    handler = file_handler.FileHandler(window)
    window.filehandler = handler
    handler.write_fileinfo_file = Mock()
    return handler, window
