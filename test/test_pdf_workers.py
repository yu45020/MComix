"""Native PDF child ownership, reuse, concurrency and failure regression tests."""

import gc
import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterator

from PIL import Image
import pytest

manager = pytest.importorskip('mcomix.archive.native_pdf.manager')
MuPDFArchive = pytest.importorskip('mcomix.archive.native_pdf.parent').MuPDFArchive


def counted_serve(connection: Any, filename: str) -> None:
    """Instrument real spawned workers without adding production diagnostics."""
    from mcomix.archive.native_pdf import child, manager as child_manager
    original_open = child.pymupdf.open
    original_close = child.MuPDFWorker.close
    original_extract = child.MuPDFWorker.extract_file
    original_listing = child.MuPDFWorker.iter_contents

    def event(name: str) -> None:
        with open(filename + '.events', 'a', encoding='utf8') as stream:
            stream.write(json.dumps([name, os.getpid(), threading.get_ident()]) + '\n')

    def doc_open(*args: Any, **kwargs: Any) -> Any:
        event('open')
        return original_open(*args, **kwargs)

    def doc_close(worker: Any) -> None:
        event('close')
        original_close(worker)

    def extract(worker: Any, *args: Any) -> None:
        event('extract')
        gate = Path(filename + '.gate')
        if gate.exists():
            Path(filename + '.entered').touch()
            deadline = time.monotonic() + 10
            while gate.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
        original_extract(worker, *args)

    def listing(worker: Any) -> Iterator[str]:
        for name in original_listing(worker):
            event('listed')
            yield name

    child.pymupdf.open = doc_open
    child.MuPDFWorker.close = doc_close
    child.MuPDFWorker.extract_file = extract
    child.MuPDFWorker.iter_contents = listing
    child_manager._serve(connection, filename)


def failed_listing_serve(connection: Any, filename: str) -> None:
    """Fail after one listed member in the real spawned worker."""
    from mcomix.archive.native_pdf import child, manager as child_manager

    def listing(worker: Any) -> Iterator[str]:
        yield 'page0001.png'
        raise RuntimeError('listing interrupted')

    child.MuPDFWorker.iter_contents = listing
    child_manager._serve(connection, filename)


def failed_serve(connection: Any, filename: str) -> None:
    """Simulate a child import/startup failure before the ready handshake."""
    connection.close()
    raise RuntimeError('startup failed')


@pytest.fixture(autouse=True)
def isolated_temp(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    from mcomix.archive.native_pdf.child import pymupdf
    path = tmp_path / 'book.pdf'
    with pymupdf.open() as document:
        for index in range(3):
            page = document.new_page(width=12, height=10)
            page.draw_rect(page.rect, color=(1, 0, 0), fill=(index / 3, 0, 0))
        document.save(str(path))
    return path


@pytest.fixture
def archive(pdf: Path, monkeypatch: Any) -> Iterator[Any]:
    monkeypatch.setattr(manager, '_serve', counted_serve)
    archive = MuPDFArchive(str(pdf))
    yield archive
    archive.close()


def events(pdf: Path) -> list:
    path = Path(str(pdf) + '.events')
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def count(pdf: Path, name: str) -> int:
    return [e[0] for e in events(pdf)].count(name)


def blank_pdf(path: Path, pages: int) -> Path:
    from mcomix.archive.native_pdf.child import pymupdf
    with pymupdf.open() as document:
        for _ in range(pages):
            document.new_page(width=3, height=2)
        document.save(str(path))
    return path


def test_constructor_does_no_pdf_work(pdf: Path, monkeypatch: Any) -> None:
    from mcomix.archive.native_pdf import parent

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail('construction must not create a PDF wrangler or spawn')

    monkeypatch.setattr(parent, 'MuPDFProcessWrangler', forbidden)
    MuPDFArchive(str(pdf)).close()


def test_spawned_document_is_reused_and_closed(archive: Any, pdf: Path,
                                               tmp_path: Path) -> None:
    assert archive.mgr._process is None
    assert len(list(archive.mgr.iter_contents())) == 3
    process = archive.mgr._process
    assert process.name == 'mcomix-pdf'
    assert process.pid != os.getpid()
    names = archive.list_contents()
    assert names == ['page0001.png', 'page0002.png', 'page0003.png']
    assert archive.list_contents() == names
    assert list(archive.iter_extract(names, str(tmp_path))) == names
    for name in names:
        with Image.open(tmp_path / name) as image:
            assert image.size[0] > 0
        assert archive.read(name).startswith(b'\x89PNG')
    assert archive.mgr._process is process
    assert count(pdf, 'open') == 1
    archive.close()
    assert not process.is_alive()
    assert count(pdf, 'close') == 1
    # A real Windows replacement verifies no document file handle survives.
    moved = pdf.with_suffix('.moved')
    pdf.rename(moved)
    moved.rename(pdf)
    archive.close()
    assert archive.list_contents() == names
    reopened = archive.mgr._process
    assert reopened.pid != process.pid
    archive.close()
    assert not reopened.is_alive()
    assert count(pdf, 'open') == 2


def test_concurrent_extraction_uses_one_child_thread(archive: Any, pdf: Path,
                                                     tmp_path: Path) -> None:
    names = archive.list_contents()
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(archive.read, names * 4))
    assert all(data.startswith(b'\x89PNG') for data in results)
    observed = events(pdf)
    assert count(pdf, 'open') == 1
    assert count(pdf, 'extract') == 12
    assert len({(e[1], e[2]) for e in observed}) == 1


def test_remote_error_preserves_worker_and_batch_progress(archive: Any,
                                                          pdf: Path,
                                                          tmp_path: Path) -> None:
    names = archive.list_contents()
    iterator = archive.iter_extract([names[0], 'bad.png', names[2]], str(tmp_path))
    assert next(iterator) == names[0]
    with pytest.raises(RuntimeError, match='Invalid PDF page name'):
        next(iterator)
    assert archive.read(names[2]).startswith(b'\x89PNG')
    assert count(pdf, 'open') == 1


def test_child_crash_is_cleaned(pdf: Path) -> None:
    wrangler = manager.MuPDFProcessWrangler(str(pdf))
    assert len(list(wrangler.iter_contents())) == 3
    process = wrangler._process
    process.terminate()
    process.join(5)
    assert not process.is_alive()
    with pytest.raises(RuntimeError, match='stopped unexpectedly'):
        list(wrangler.iter_contents())
    assert wrangler._process is None
    wrangler.close()


def test_abandoned_wrangler_finalizer_joins_its_child(pdf: Path) -> None:
    wrangler = manager.MuPDFProcessWrangler(str(pdf))
    assert len(list(wrangler.iter_contents())) == 3
    process = wrangler._process
    del wrangler
    gc.collect()
    process.join(5)
    assert not process.is_alive()


def test_close_waits_for_inflight_request(archive: Any, pdf: Path) -> None:
    name = archive.list_contents()[0]
    gate = Path(str(pdf) + '.gate')
    entered = Path(str(pdf) + '.entered')
    gate.touch()
    process = archive.mgr._process
    with ThreadPoolExecutor(max_workers=2) as executor:
        reading = executor.submit(archive.read, name)
        deadline = time.monotonic() + 8
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered.exists()
        closing = executor.submit(archive.close)
        try:
            time.sleep(0.05)
            assert not closing.done()
            assert process.is_alive()
        finally:
            gate.unlink()
        assert reading.result(timeout=8).startswith(b'\x89PNG')
        closing.result(timeout=8)
    assert not process.is_alive()


def test_cancel_stops_a_hung_child(archive: Any, pdf: Path) -> None:
    name = archive.list_contents()[0]
    gate = Path(str(pdf) + '.gate')
    entered = Path(str(pdf) + '.entered')
    # The child stays in the gated call, as on a PDF that MuPDF loops on.
    gate.touch()
    process = archive.mgr._process
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            reading = executor.submit(archive.read, name)
            deadline = time.monotonic() + 8
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert entered.exists()
            archive.cancel()
            with pytest.raises(RuntimeError, match='stopped unexpectedly'):
                reading.result(timeout=5)
        assert not process.is_alive()
        with pytest.raises(RuntimeError, match='closed'):
            archive.read(name)
        archive.close()
    finally:
        gate.unlink()


def test_close_fences_suspended_extract_iterator(archive: Any, tmp_path: Path) -> None:
    names = archive.list_contents()
    iterator = archive.iter_extract(names, str(tmp_path))
    assert next(iterator) == names[0]
    archive.close()
    with pytest.raises(RuntimeError, match='closed'):
        next(iterator)
    assert not (tmp_path / names[1]).exists()


def test_startup_failure_releases_child(pdf: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(manager, '_serve', failed_serve)
    wrangler = manager.MuPDFProcessWrangler(str(pdf))
    with pytest.raises(RuntimeError, match='failed to start'):
        list(wrangler.iter_contents())
    assert wrangler._process is None
    assert wrangler._connection is None
    wrangler.close()


def test_close_of_crashed_child_is_idempotent(pdf: Path) -> None:
    wrangler = manager.MuPDFProcessWrangler(str(pdf))
    assert len(list(wrangler.iter_contents())) == 3
    process = wrangler._process
    process.terminate()
    process.join(5)
    assert not process.is_alive()
    wrangler.close()
    wrangler.close()
    assert wrangler._process is None


def test_mixed_pdf_identifiers_do_not_change_on_repeated_listing(tmp_path: Path) -> None:
    import io
    from mcomix.archive.native_pdf.child import pymupdf
    path = tmp_path / 'mixed.pdf'
    image = Image.new('RGB', (12, 10), 'red')
    stream = io.BytesIO()
    image.save(stream, format='JPEG')
    image.close()
    with pymupdf.open() as document:
        page = document.new_page(width=12, height=10)
        page.insert_image(page.rect, stream=stream.getvalue())
        page = document.new_page(width=12, height=10)
        page.insert_text((1, 5), 'text', fontsize=2)
        page = document.new_page(width=12, height=10)
        page.insert_image(page.rect, stream=stream.getvalue())
        document.save(str(path))
    archive = MuPDFArchive(str(path))
    try:
        names = archive.list_contents()
        assert names[0].endswith('.jpeg') and '_mcmxref' in names[0]
        assert names[1:] == ['page0002.png', 'page0003.png']
        assert archive.list_contents() == names
        for name in reversed(names):
            assert archive.read(name)
        assert archive.list_contents() == names
    finally:
        archive.close()


def test_failed_document_can_be_retried_after_repair(pdf: Path) -> None:
    valid = pdf.read_bytes()
    pdf.write_bytes(b'%PDF-corrupt')
    archive = MuPDFArchive(str(pdf))
    try:
        with pytest.raises(RuntimeError, match='PDF worker failed'):
            archive.list_contents()
        assert archive.mgr._process is None
        pdf.write_bytes(valid)
        assert len(archive.list_contents()) == 3
    finally:
        archive.close()


def test_listing_batches_bound_partial_work_and_remain_stable(tmp_path: Path,
                                                              monkeypatch: Any) -> None:
    path = blank_pdf(tmp_path / 'partial.pdf', 260)
    monkeypatch.setattr(manager, '_serve', counted_serve)
    archive = MuPDFArchive(str(path))
    try:
        iterator = archive.iter_contents()
        assert next(iterator) == 'page0001.png'
        assert count(path, 'listed') == 128
        repeated = archive.iter_contents()
        assert next(repeated) == 'page0001.png'
        assert count(path, 'listed') == 128
        repeated.close()
        iterator.close()
        process = archive.mgr._process
    finally:
        archive.close()
    assert not process.is_alive()
    assert count(path, 'listed') == 128
    assert count(path, 'close') == 1
    archive = MuPDFArchive(str(path))
    try:
        names = archive.list_contents()
        assert len(names) == 260
        assert names[-1] == 'page0260.png'
        assert archive.list_contents() == names
    finally:
        archive.close()


def test_repeated_listing_error_never_publishes_partial_index(pdf: Path,
                                                              monkeypatch: Any) -> None:
    monkeypatch.setattr(manager, '_serve', failed_listing_serve)
    archive = MuPDFArchive(str(pdf))
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match='listing interrupted'):
                archive.list_contents()
    finally:
        archive.close()
