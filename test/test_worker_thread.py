"""Worker queue sample and deprecation-free threading APIs."""

from pathlib import Path
import threading
from typing import Any, Callable, Iterator
import warnings
import zipfile

import pytest

from mcomix import archive_packer
from mcomix.worker_thread import WorkerThread


@pytest.fixture
def workers() -> Iterator[Any]:
    """Release all owned gates before bounded shutdown, even on assertions."""
    owned = []
    gates = []

    def create(process: Callable[[Any], None], **kwargs: Any) -> WorkerThread:
        worker = WorkerThread(process, **kwargs)
        owned.append(worker)
        return worker

    def gate() -> threading.Event:
        event = threading.Event()
        gates.append(event)
        return event

    yield create, gate
    for event in gates:
        event.set()
    for worker in owned:
        worker.request_stop()
        for thread in worker._threads:
            thread.join(5)
            assert not thread.is_alive(), 'owned worker did not stop'
        worker.stop()


def test_stop_waits_for_active_order_flushes_queue_and_allows_reuse(
        workers: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    create, gate = workers
    entered, release, requested, stopped, reused = (gate() for _ in range(5))
    seen = []

    def process(value: str) -> None:
        seen.append(value)
        if value == 'active':
            entered.set()
            assert release.wait(5)
            assert worker.must_stop()
        if value == 'reused':
            reused.set()

    worker = create(process, unique_orders=True)
    original_request = worker.request_stop

    def request_stop() -> None:
        original_request()
        requested.set()

    monkeypatch.setattr(worker, 'request_stop', request_stop)
    worker.append_order('active')
    assert entered.wait(5)
    worker.append_order('discarded')

    def stop() -> None:
        worker.stop()
        stopped.set()

    stopper = threading.Thread(target=stop)
    stopper.start()
    try:
        assert requested.wait(5)
        assert not stopped.is_set()
        assert worker._threads[0].is_alive()
    finally:
        release.set()
        stopper.join(5)
    assert not stopper.is_alive()
    assert stopped.is_set()
    assert not worker.must_stop()
    worker.append_order('reused')
    assert reused.wait(5)
    worker.stop()
    worker.stop()
    assert seen == ['active', 'reused']


def test_worker_public_queue_apis_are_deprecation_warning_free(workers: Any) -> None:
    create, gate = workers
    done = gate()
    worker = create(lambda value: done.set())
    with warnings.catch_warnings():
        warnings.simplefilter('error', DeprecationWarning)
        with worker:
            worker.append_order(1)
            worker.extend_orders([2])
            assert all(not thread.daemon for thread in worker._threads)
        assert done.wait(5)
        worker.request_stop()
        worker.stop()


def test_packer_uses_non_daemon_thread_and_writes_real_zip(
        tmp_path: Path) -> None:
    image = tmp_path / 'page.png'
    comment = tmp_path / 'notes.txt'
    image.write_bytes(b'page payload')
    comment.write_bytes(b'comment payload')
    destination = tmp_path / 'book.cbz'
    packer = archive_packer.Packer([str(image)], [str(comment)],
                                   str(destination), 'book')
    with warnings.catch_warnings():
        warnings.simplefilter('error', DeprecationWarning)
        packer.pack()
    packer._pack_thread.join(5)
    assert not packer._pack_thread.is_alive()
    assert not packer._pack_thread.daemon
    assert packer.wait()
    with zipfile.ZipFile(str(destination)) as archive:
        assert archive.namelist() == ['1 - book.png', 'notes.txt']
        assert archive.read('1 - book.png') == b'page payload'
        assert archive.read('notes.txt') == b'comment payload'


def test_directory_deletion_uses_non_daemon_thread_without_deprecations(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Import GTK dependencies before the scoped warning check.
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('GdkPixbuf', '2.0')
    from mcomix import file_handler
    created = []
    original_thread = threading.Thread

    def thread(**kwargs: Any) -> threading.Thread:
        result = original_thread(**kwargs)
        created.append(result)
        return result

    directory = tmp_path / 'owned-storage'
    directory.mkdir()
    (directory / 'page.png').write_bytes(b'page')
    monkeypatch.setattr(file_handler.threading, 'Thread', thread)
    with warnings.catch_warnings():
        warnings.simplefilter('error', DeprecationWarning)
        file_handler.FileHandler.thread_delete(None, str(directory))
    assert len(created) == 1
    created[0].join(5)
    assert not created[0].is_alive()
    assert not created[0].daemon
    assert not directory.exists()
