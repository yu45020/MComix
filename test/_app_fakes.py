"""Shared scaffolding: isolated storage/library and a FileHandler on a fake window."""

import base64
import io
from pathlib import Path
import shutil
import tarfile
import time
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional
from unittest.mock import Mock
import zipfile

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GLib  # noqa: E402

from mcomix import constants, file_handler  # noqa: E402
from mcomix.library import backend  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZl0AAAAASUVORK5CYII=')


def pump(predicate: Callable[[], Any] = lambda: True, timeout: float = 5) -> None:
    """Iterate the GTK main context until predicate() holds, then drain it."""
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not predicate():
        assert time.monotonic() < deadline, 'main loop wait timed out'
        context.iteration(False)
        time.sleep(0.001)
    while context.pending():
        context.iteration(False)


def write_archive(path: Path, names: Iterable[str], data: bytes = PNG) -> str:
    """Write a ZIP (or TAR for a .tar path) whose members all contain data."""
    if path.suffix == '.tar':
        with tarfile.open(str(path), 'w') as archive:
            for name in names:
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
    else:
        with zipfile.ZipFile(str(path), 'w') as archive:
            for name in names:
                archive.writestr(name, data)
    return str(path)


def folder_book(root: Path) -> str:
    """Root holds a/1.png, b/1.png and b/2.png: three pages in two folders."""
    for name in ['a/1.png', 'b/1.png', 'b/2.png']:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(PNG)
    return str(root)


def isolated_library(monkeypatch: Any, tmp_path: Path) -> backend._LibraryBackend:
    """A real SQLite library and temporary storage that never touch user data."""
    monkeypatch.setattr(file_handler.tempfile, 'tempdir', str(tmp_path))
    for name, filename in [('LIBRARY_DATABASE_PATH', 'books.db'),
                           ('LIBRARY_COVERS_PATH', 'covers'),
                           ('FILEINFO_PICKLE_PATH', 'file.pickle')]:
        monkeypatch.setattr(constants, name, str(tmp_path / filename))
    monkeypatch.setitem(prefs, 'last library collection', None)
    library = backend._LibraryBackend()
    monkeypatch.setattr(backend, '_backend', library)
    return library


def fake_app(monkeypatch: Any, tmp_path: Path, window: Optional[Any] = None,
             recent: bool = False) -> SimpleNamespace:
    """A real FileHandler on a fake window that records set_page() calls."""
    library = isolated_library(monkeypatch, tmp_path)
    monkeypatch.setitem(prefs, 'store recent file info', recent)
    monkeypatch.setitem(prefs, 'stored dialog choices', {})
    monkeypatch.setitem(prefs, 'default double page', False)
    images = SimpleNamespace(_image_files=[], _current_image_index=None, cleanup=Mock())
    images.get_current_page = lambda: (0 if images._current_image_index is None
                                       else images._current_image_index + 1)
    images.get_number_of_pages = lambda: len(images._image_files)
    window = SimpleNamespace() if window is None else window
    window.imagehandler = images
    window.displayed_double = lambda: False
    window.pages = []
    window.statusbar, window.osd, window.uimanager = Mock(), Mock(), Mock()
    window.set_cursor = Mock()
    window.show_notice = Mock()

    def set_page(page: int) -> None:
        window.pages.append(page)
        images._current_image_index = page - 1

    window.set_page = set_page
    handler = file_handler.FileHandler(window)
    window.filehandler = handler
    images.get_real_path = lambda: (handler._current_file if handler.archive_type is not None
                                    else images._image_files[images._current_image_index])
    deleted = []

    def delete(path: str) -> None:
        deleted.append(path)
        shutil.rmtree(path)

    handler.thread_delete = delete

    def close() -> None:
        handler.close_file()
        for retired in handler._retired_archives:
            retired.wait()
        pump()
        library.close()

    return SimpleNamespace(handler=handler, window=window, images=images,
                           library=library, deleted=deleted, close=close)


def loaded(handler: Any, path: Optional[str] = None) -> None:
    """Wait until handler finished opening (path, when given)."""
    pump(lambda: (handler.file_loaded and not handler.file_loading
                  and (path is None or handler._current_file == path)))
