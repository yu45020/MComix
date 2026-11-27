"""Replace small configuration files only after a complete, flushed write."""

from contextlib import contextmanager, suppress
import os
import tempfile
from typing import IO, Iterator


@contextmanager
def atomic_write(path: str, mode: str = 'w') -> Iterator[IO]:
    """Yield a temporary stream, then close and atomically replace ``path``.

    The old file stays in place when writing fails. This protects against
    partial saves, rather than promising durability across a power failure.
    """
    descriptor, temporary = tempfile.mkstemp(
        prefix='.' + os.path.basename(path) + '.', suffix='.tmp',
        dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(descriptor, mode) as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise
