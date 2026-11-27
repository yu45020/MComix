"""Bounded chooser previews, cancellation, and terminal thumbnail outcomes."""

import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock

import gi
import pytest
from PIL import Image

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk  # noqa: E402

from mcomix import file_chooser_base_dialog as chooser  # noqa: E402
from mcomix import file_chooser_library_dialog, thumbnail_tools  # noqa: E402
from mcomix.archive import archive_base, password  # noqa: E402
from mcomix.i18n import _  # noqa: E402
from mcomix.preferences import prefs  # noqa: E402
from ._app_fakes import pump  # noqa: E402


class PreviewDialog(chooser._BaseFileChooserDialog):
    def _current_file(self) -> None:
        return None

    def files_chosen(self, paths: list) -> None:
        pass


@pytest.fixture
def dialog(tmp_path: Path, monkeypatch: Any) -> Iterator[PreviewDialog]:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setitem(prefs, 'path of last browsed in filechooser', str(tmp_path))
    monkeypatch.setitem(prefs, 'store recent file info', False)
    monkeypatch.setitem(prefs, 'create thumbnails', False)
    monkeypatch.setattr(PreviewDialog, '_last_activated_file', None)
    result = PreviewDialog()
    result.filechooser.disconnect_by_func(result._update_preview)
    pump()
    # Real GTK labels/image; deterministic selection avoids platform chooser timing.
    selected = SimpleNamespace(path=None)
    result.filechooser.get_preview_filename = lambda: selected.path
    result.select = lambda path: (setattr(selected, 'path', path), result._update_preview())
    result.idle = lambda: pump(lambda: result._preview_worker is None)
    yield result
    worker = result._preview_worker
    result.destroy()
    if worker is not None:
        worker.join(4)
        assert not worker.is_alive()
    pump()


def make_image(path: Path) -> str:
    Image.new('RGB', (16, 8), 'red').save(path)
    return str(path)


def protected_archive(path: Path, monkeypatch: Any) -> Any:
    path.write_bytes(b'archive')
    archive = archive_base.BaseArchive(str(path))
    archive.close = Mock()
    archive.read = Mock(return_value=b'')
    monkeypatch.setattr(thumbnail_tools.archive_tools, 'archive_mime_type', lambda path: 'zip')
    monkeypatch.setattr(thumbnail_tools.archive_tools, 'get_recursive_archive_handler',
                        lambda *args, **kwargs: archive)
    return archive


def test_burst_runs_one_active_and_only_latest_pending(
        dialog: PreviewDialog, tmp_path: Path, monkeypatch: Any) -> None:
    paths = [make_image(tmp_path / ('%s.png' % index)) for index in range(30)]
    entered, release = threading.Event(), threading.Event()
    calls, threads = [], set()
    original = thumbnail_tools.Thumbnailer.thumbnail

    def thumbnail(self: Any, path: str) -> Any:
        calls.append(path)
        threads.add(threading.current_thread())
        if len(calls) == 1:
            entered.set()
            assert release.wait(4)
        return original(self, path)

    monkeypatch.setattr(thumbnail_tools.Thumbnailer, 'thumbnail', thumbnail)
    dialog.select(paths[0])
    assert entered.wait(3)
    try:
        for path in paths[1:]:
            dialog.select(path)
        pump()
        assert not dialog._namelabel.get_text()
    finally:
        release.set()
    dialog.idle()
    assert calls == [paths[0], paths[-1]] and len(threads) == 1
    assert dialog._namelabel.get_text() == '29.png'
    assert dialog._preview_image.get_pixbuf() is not None


def test_stale_or_vanished_results_are_not_shown(
        dialog: PreviewDialog, tmp_path: Path, monkeypatch: Any) -> None:
    path = make_image(tmp_path / 'same.png')
    queued = []

    def idle_add(function: Any, *args: Any) -> None:
        if function == dialog._preview_thumbnail_finished:
            queued.append(lambda: function(*args))

    monkeypatch.setattr(chooser.GLib, 'idle_add', idle_add)
    dialog.select(path)
    dialog.idle()
    dialog.select(path)
    dialog.idle()
    old, new = queued
    old()
    assert not dialog._namelabel.get_text()
    new()
    assert dialog._namelabel.get_text() == 'same.png'
    dialog.select(None)
    new()
    assert not dialog._namelabel.get_text()
    dialog.select(path)
    dialog.idle()
    Path(path).unlink()
    queued[-1]()
    assert dialog._preview_image.get_pixbuf() is None
    assert not dialog._namelabel.get_text() and not dialog._sizelabel.get_text()


def test_failure_clears_previous_image_and_all_details(
        dialog: PreviewDialog, tmp_path: Path) -> None:
    dialog.select(make_image(tmp_path / 'valid.png'))
    dialog.idle()
    assert dialog._namelabel.get_text() == 'valid.png'
    bad = tmp_path / 'broken.jpg'
    bad.write_bytes(b'\xff\xd8\xff\xe0\x00')
    dialog.select(str(bad))
    dialog.idle()
    assert dialog._preview_image.get_pixbuf() is None
    assert not dialog._namelabel.get_text() and not dialog._sizelabel.get_text()


def test_close_does_not_join_running_preview_or_start_pending(
        dialog: PreviewDialog, tmp_path: Path, monkeypatch: Any) -> None:
    path = make_image(tmp_path / 'good.png')
    entered, release = threading.Event(), threading.Event()
    calls = []

    def thumbnail(self: Any, path: str) -> None:
        calls.append(path)
        entered.set()
        assert release.wait(4)

    monkeypatch.setattr(thumbnail_tools.Thumbnailer, 'thumbnail', thumbnail)
    dialog.select(path)
    assert entered.wait(3)
    worker = dialog._preview_worker
    dialog.select(path)
    dialog.destroy()
    try:
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(4)
    assert not worker.is_alive() and calls == [path]


def test_widget_methods_only_run_in_main_thread(
        dialog: PreviewDialog, tmp_path: Path, monkeypatch: Any) -> None:
    calls = []
    for widget, name in [(dialog._preview_image, 'set_from_pixbuf'),
                         (dialog._namelabel, 'set_text'),
                         (dialog.filechooser, 'get_preview_filename')]:
        original = getattr(widget, name)

        def checked(*args: Any, _original: Any = original) -> Any:
            calls.append(threading.current_thread())
            return _original(*args)

        monkeypatch.setattr(widget, name, checked)
    dialog.select(make_image(tmp_path / 'good.png'))
    dialog.idle()
    assert calls and all(item is threading.main_thread() for item in calls)


def test_thumbnailer_setup_error_does_not_strand_later_selection(
        dialog: PreviewDialog, tmp_path: Path, monkeypatch: Any) -> None:
    path = make_image(tmp_path / 'good.png')
    original = thumbnail_tools.Thumbnailer
    monkeypatch.setattr(thumbnail_tools, 'Thumbnailer', Mock(side_effect=ValueError('setup failed')))
    dialog.select(path)
    dialog.idle()
    assert not dialog._namelabel.get_text()
    monkeypatch.setattr(thumbnail_tools, 'Thumbnailer', original)
    dialog.select(path)
    dialog.idle()
    assert dialog._namelabel.get_text() == 'good.png'


def test_real_archive_preview_shows_archive_name(
        dialog: PreviewDialog, tmp_path: Path) -> None:
    import zipfile
    image = make_image(tmp_path / 'cover.png')
    archive = tmp_path / 'book.cbz'
    with zipfile.ZipFile(str(archive), 'w') as stream:
        stream.write(image, 'cover.png')
    dialog.select(str(archive))
    dialog.idle()
    assert dialog._namelabel.get_text() == 'book.cbz'


def test_destroy_cancels_worker_password_wait_without_gtk_join(
        dialog: PreviewDialog, tmp_path: Path, monkeypatch: Any) -> None:
    archive = protected_archive(tmp_path / 'protected.zip', monkeypatch)
    entered = threading.Event()

    def listing() -> list:
        entered.set()
        archive._get_password()
        return []

    archive.list_contents = listing
    # Destruction happens before the queued main-thread prompt can run.
    monkeypatch.setattr(password, 'ask_for_password',
                        Mock(side_effect=AssertionError('stale password dialog')))
    dialog.select(str(archive.archive))
    assert entered.wait(3)
    worker = dialog._preview_worker
    dialog.destroy()
    worker.join(4)
    assert not worker.is_alive() and archive._cancelled.is_set()
    pump()
    password.ask_for_password.assert_not_called()
    archive.close.assert_called_once()


def test_selection_cancels_active_real_password_dialog(
        dialog: PreviewDialog, tmp_path: Path, monkeypatch: Any,
        password_dialog: Any) -> None:
    archive = protected_archive(tmp_path / 'protected.zip', monkeypatch)
    archive.list_contents = lambda: (archive._get_password(), [])[1]
    seen = []
    deadline = time.monotonic() + 3

    def dismiss() -> bool:
        if password._active_dialogs:
            seen.append(password._active_dialogs[0][0])
            dialog.select(None)
            return False
        assert time.monotonic() < deadline
        return True

    source = GLib.timeout_add(10, dismiss)
    try:
        dialog.select(str(archive.archive))
        dialog.idle()
        assert seen == [archive] and archive._cancelled.is_set()
        assert not password._active_dialogs
        archive.close.assert_called_once()
    finally:
        dialog.destroy()
        if not seen:
            GLib.source_remove(source)


def test_thumbnailer_creation_failure_raises(
        tmp_path: Path, monkeypatch: Any) -> None:
    source = make_image(tmp_path / 'image.png')
    thumbnailer = thumbnail_tools.Thumbnailer(
        dst_dir=str(tmp_path / 'cache'), size=(64, 64), store_on_disk=True)
    monkeypatch.setattr(thumbnailer, '_create_thumbnail_pixbuf',
                        Mock(side_effect=OSError('create')))
    with pytest.raises(OSError, match='create'):
        thumbnailer.thumbnail(source)


def test_cancel_during_archive_creation_cancels_before_listing(
        tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    thumbnailer = thumbnail_tools.Thumbnailer(store_on_disk=False, archive_support=True)
    archive = Mock()
    entered, release = threading.Event(), threading.Event()

    def create(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(4)
        return archive

    monkeypatch.setattr(thumbnail_tools.archive_tools, 'archive_mime_type', lambda path: 'zip')
    monkeypatch.setattr(thumbnail_tools.archive_tools, 'get_recursive_archive_handler', create)
    worker = threading.Thread(target=thumbnailer.thumbnail, args=('book.zip',))
    worker.start()
    try:
        assert entered.wait(3)
        thumbnailer.cancel()
    finally:
        release.set()
        worker.join(4)
    assert not worker.is_alive()
    archive.cancel.assert_called_once()
    archive.list_contents.assert_not_called()
    archive.close.assert_called_once()
    assert not list(tmp_path.iterdir())
    pump()


def test_library_chooser_restores_the_filter_it_saved(
        tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setitem(prefs, 'path of last browsed in filechooser', str(tmp_path))
    monkeypatch.setitem(prefs, 'last filter in library filechooser', 0)
    library = Gtk.Window()
    library.add_books = Mock()

    def open_dialog() -> Any:
        file_chooser_library_dialog.open_library_filechooser_dialog(library)
        return file_chooser_library_dialog._library_filechooser_dialog

    try:
        dialog = open_dialog()
        assert dialog.filechooser.get_filter().get_name() == _('All archives')
        chosen = dialog.filechooser.list_filters()[2]
        dialog.filechooser.set_filter(chosen)
        dialog.files_chosen([str(tmp_path / 'book.cbz')])
        library.add_books.assert_called_once()

        dialog = open_dialog()
        assert dialog.filechooser.get_filter().get_name() == chosen.get_name()
    finally:
        file_chooser_library_dialog.close_library_filechooser_dialog()
        library.destroy()
        pump()
