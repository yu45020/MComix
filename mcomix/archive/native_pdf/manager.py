"""A serialized, spawned PDF worker with one document per archive."""

import multiprocessing as mp
import threading
from multiprocessing.util import Finalize
from typing import Any, Generator, Iterable

from .child import MuPDFWorker


def _serve(connection: Any, filename: str) -> None:
    """Own the document and run all MuPDF operations on one child thread."""
    worker = None
    try:
        try:
            worker = MuPDFWorker(filename)
            connection.send((True, None))
        except Exception as error:
            connection.send((False, str(error)))
            return
        while True:
            command, args = connection.recv()
            try:
                if command == 'close':
                    worker.close()
                    worker = None
                    connection.send((True, None))
                    return
                if command == 'list_pages':
                    result = worker.list_pages(*args)
                elif command == 'extract_file':
                    worker.extract_file(*args)
                    result = None
                else:
                    raise ValueError('Unknown PDF worker command: %s' % command)
                connection.send((True, result))
            except Exception as error:
                # MuPDF exception classes are not consistently picklable across
                # supported versions. Keep errors usable without losing the pipe.
                connection.send((False, str(error)))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        if worker is not None:
            worker.close()
        connection.close()


def _terminate(process: Any, connection: Any) -> None:
    """Release an abandoned child without retaining its archive owner."""
    connection.close()
    if process.is_alive():
        process.terminate()
    process.join()


class MuPDFProcessWrangler:
    """Share one serialized child/document between an archive's callers.

    A manager server dispatches calls on multiple threads, which is unsafe for
    a shared MuPDF document. This worker instead handles the pipe on its main
    thread. Constructing the wrangler does not start a process or open a PDF.
    """

    def __init__(self, filename: str) -> None:
        self._filename = filename
        self._lock = threading.RLock()
        self._process = None
        self._connection = None
        self._finalizer = None
        self._closed = False

    def _start(self) -> None:
        """Start and initialize a child while holding the request lock."""
        context = mp.get_context('spawn')
        connection, child_connection = context.Pipe()
        process = context.Process(target=_serve,
                                  args=(child_connection, self._filename),
                                  name='mcomix-pdf')
        try:
            process.start()
        except BaseException:
            connection.close()
            raise
        finally:
            child_connection.close()
        self._process = process
        self._connection = connection
        self._finalizer = Finalize(self, _terminate,
                                   args=(process, connection), exitpriority=10)
        try:
            self._receive()
        except (EOFError, OSError) as error:
            self._dispose()
            raise RuntimeError('PDF worker failed to start') from error
        except BaseException:
            self._dispose()
            raise

    def _receive(self) -> Any:
        """Receive one result, including failures from opening the document."""
        success, result = self._connection.recv()
        if not success:
            raise RuntimeError('PDF worker failed: %s' % result)
        return result

    def _dispose(self) -> None:
        """Finish the owned process and remove its finalizer."""
        if self._finalizer is not None:
            self._finalizer()
        self._finalizer = None
        self._connection = None
        self._process = None

    def _request(self, command: str, *args: Any) -> Any:
        """Serialize complete requests; never share MuPDF objects with GTK."""
        with self._lock:
            if self._closed:
                raise RuntimeError('PDF worker is closed')
            if self._process is None:
                self._start()
            try:
                self._connection.send((command, args))
                return self._receive()
            except (EOFError, OSError) as error:
                self._dispose()
                raise RuntimeError('PDF worker stopped unexpectedly') from error

    def iter_contents(self) -> Generator[str, None, None]:
        """List pages with one request, leaving no remote generator behind."""
        offset = 0
        while True:
            pages = self._request('list_pages', offset, 128)
            yield from pages
            if len(pages) < 128:
                return
            offset += len(pages)

    def extract_pages(self, page_list: Iterable[str],
                      destination_dir: str) -> Generator[str, None, None]:
        """Publish each successful page while reusing the open document."""
        for filename in page_list:
            self._request('extract_file', filename, destination_dir)
            yield filename

    def terminate(self) -> None:
        """Fail pending and later requests by ending the child, without the
        request lock a blocked request holds."""
        self._closed = True
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()

    def close(self) -> None:
        """Wait for active requests, close the document, and join its child."""
        with self._lock:
            if self._closed:
                # Join a child that terminate() ended while idle.
                self._dispose()
                return
            self._closed = True
            try:
                if self._process is not None:
                    self._connection.send(('close', ()))
                    self._receive()
                    self._process.join()
            except (EOFError, OSError):
                # A crashed child has already released its document handles.
                pass
            finally:
                self._dispose()
