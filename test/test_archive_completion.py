"""Terminal archive outcomes with real workers, waiters and GTK dispatch."""

import os
from pathlib import Path
import threading
import time
from typing import Any, Iterator, List
from unittest.mock import Mock

import pytest

from mcomix import archive_extractor, archive_tools
from ._archive_fakes import Extractors, FakeArchive, extractor_handler, pump
from ._shells import file_handler_shell


@pytest.fixture
def opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    extractors = Extractors(tmp_path, monkeypatch)
    yield extractors.open
    extractors.close()


def _listed(extractor: archive_extractor.Extractor) -> List[str]:
    pump(lambda: extractor.get_files() is not None)
    return extractor.get_files()


@pytest.mark.parametrize('solid,mode', [
    (True, 'omit'), (True, 'early-error'), (True, 'missing'), (False, 'extract-error')])
def test_failed_member_finishes_waiter_and_rejects_partial(
        opened: Any, mode: str, solid: bool) -> None:
    extractor = opened(FakeArchive(mode, solid))
    names = _listed(extractor)
    handler = extractor_handler(extractor)
    path = os.path.join(extractor.get_directory(), 'last.png')
    completed = []
    extractor.file_extracted += lambda obj, name: completed.append(name)
    waiter = threading.Thread(target=handler._wait_on_file, args=(path,), daemon=True)
    waiter.start()
    extractor.extract()
    waiter.join(3)
    assert not waiter.is_alive(), 'FileHandler waiter was stranded'
    pump(lambda: len(completed) == len(names))
    assert sorted(completed) == sorted(names)
    assert all(extractor.is_ready(name) for name in names)
    assert extractor.get_files() == []
    assert extractor.get_error('last.png') is not None
    assert extractor.get_entry_size('last.png') is None
    # Existing output must not disguise failure for eager decoding/open-with.
    Path(path).write_bytes(b'EXISTING PARTIAL')
    for operation in (extractor.read, extractor.materialize):
        with pytest.raises(archive_extractor.ArchiveException):
            operation('last.png')
    with pytest.raises(archive_extractor.ArchiveException):
        handler.get_image_source(path)
    assert handler.materialize([path]) == [path]
    with pytest.raises(archive_extractor.ArchiveException):
        handler.read_file(path)


@pytest.mark.parametrize('solid', [True, False])
def test_zero_byte_member_is_successful(opened: Any, solid: bool) -> None:
    extractor = opened(FakeArchive(solid=solid))
    _listed(extractor)
    extractor.extract()
    pump(lambda: extractor.is_ready('empty.txt'))
    assert extractor.get_error('empty.txt') is None
    assert extractor.read('empty.txt') == b''
    assert extractor.get_entry_size('empty.txt') == 0


def test_duplicate_and_unrequested_yields_do_not_double_publish(opened: Any) -> None:
    extractor = opened(FakeArchive('duplicate'))
    _listed(extractor)
    completed = []
    extractor.file_extracted += lambda obj, name: completed.append(name)
    extractor.extract()
    pump(lambda: len(completed) == 3)
    assert sorted(completed) == ['empty.txt', 'first.png', 'last.png']
    assert not extractor.is_ready('unexpected.png')


@pytest.mark.parametrize('mode', ['list-error', 'password-error'])
def test_listing_failure_discards_partial_index_and_clears_loading(
        opened: Any, mode: str) -> None:
    extractor = opened(FakeArchive(mode))
    completed = []
    extractor.contents_listed += lambda obj, files: completed.append(files)
    handler = file_handler_shell()
    handler.file_loading = True
    handler._file_operation_active = False
    handler._pending_file_operation = None
    handler._shutting_down = False
    handler._shutdown_ready = None
    handler._tmp_dir = extractor.get_directory()
    handler._extractor = extractor
    handler._archive_opened = Mock()
    handler._window = Mock()
    extractor.contents_listed += handler._listed_contents
    pump(lambda: len(completed) == 1)
    assert completed == [[]]
    assert extractor.get_files() == []
    assert extractor.get_listing_error()
    assert not handler.file_loading
    handler._archive_opened.assert_called_once_with([])
    handler._window.statusbar.set_message.assert_called_once()
    handler._window.osd.show.assert_called_once()


def test_empty_selection_and_repeated_lifecycle(
        opened: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive = FakeArchive()
    extractor = opened(archive)
    _listed(extractor)
    extractor.set_files([])
    extractor.extract()
    extractor.set_files(['first.png'])
    extractor.extract()
    pump(lambda: extractor.is_ready('first.png'))
    assert archive.batches == [['first.png']]
    extractor.stop()
    extractor.stop()
    extractor.close()
    extractor.close()
    assert archive.closed == 1
    replacement = FakeArchive()
    monkeypatch.setattr(archive_tools, 'get_recursive_archive_handler',
                        lambda *args, **kwargs: replacement)
    extractor.setup('replacement', str(tmp_path))
    _listed(extractor)
    extractor.extract()
    pump(lambda: extractor.is_ready('last.png'))
    assert extractor.read('last.png') == b'COMPLETE'
    extractor.close()
    assert replacement.closed == 1


@pytest.mark.parametrize('during_batch', [False, True])
def test_stop_wakes_waiter_and_drops_old_callbacks(opened: Any, during_batch: bool) -> None:
    archive = FakeArchive('batch-block' if during_batch else 'success')
    extractor = opened(archive)
    names = _listed(extractor)
    handler = extractor_handler(extractor)
    completed = []
    extractor.file_extracted += lambda obj, name: completed.append(name)
    waiter = threading.Thread(target=handler._wait_on_file, daemon=True,
                              args=(os.path.join(extractor.get_directory(), 'last.png'),))
    waiter.start()
    if during_batch:
        extractor.extract()
        assert archive.entered.wait(3)
        archive.release.set()
    extractor.stop()
    waiter.join(3)
    pump(lambda: True)
    assert not waiter.is_alive()
    assert all(extractor.is_ready(name) for name in names)
    if not during_batch:
        assert all(extractor.get_error(name) for name in names)
    assert completed == []


def test_reused_extractor_discards_queued_listing_callback(
        opened: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    extractor = opened(FakeArchive('list-error'))
    # Deliberately avoid iterating GLib, leaving the old completion queued.
    deadline = time.monotonic() + 3
    while extractor.get_files() is None:
        assert time.monotonic() < deadline
        time.sleep(0.002)
    completed = []
    extractor.contents_listed += lambda obj, files: completed.append(files)
    monkeypatch.setattr(archive_tools, 'get_recursive_archive_handler',
                        lambda *args, **kwargs: FakeArchive())
    extractor.setup('new archive', str(tmp_path))
    pump(lambda: len(completed) == 1)
    assert completed == [['first.png', 'empty.txt', 'last.png']]
    assert extractor.get_listing_error() is None


def test_stop_during_listing_publishes_terminal_empty_index(opened: Any) -> None:
    archive = FakeArchive('list-block')
    extractor = opened(archive)
    assert archive.entered.wait(3)

    def release_listing() -> None:
        # Release only after stop has raised the existing worker stop flag.
        deadline = time.monotonic() + 3
        while not extractor._list_thread.must_stop():
            assert time.monotonic() < deadline
            time.sleep(0.002)
        archive.release.set()

    release = threading.Thread(target=release_listing)
    release.start()
    extractor.stop()
    release.join(3)
    assert extractor.get_files() == []
    assert extractor.get_listing_error() is not None


def test_reordering_inflight_batch_keeps_completed_members_terminal(opened: Any) -> None:
    archive = FakeArchive('batch-block')
    extractor = opened(archive)
    names = _listed(extractor)
    extractor.extract()
    assert archive.entered.wait(3)
    extractor.set_files(list(reversed(names)))
    archive.release.set()
    pump(lambda: all(extractor.is_ready(name) for name in names))
    assert all(extractor.get_error(name) is None for name in names)
    assert archive.calls == len(names)
