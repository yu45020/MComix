"""7z listings use an explicit strict encoding without losing member identity."""
import os
import subprocess
import sys
from unittest.mock import Mock

import pytest

from mcomix import process
from mcomix.archive import archive_base, sevenzip_external


_ENCRYPTED_HEADER = 'error: can not open encrypted archive. wrong password?\n'


def _listing(names: list) -> str:
    return 'Solid = +\n----------\n' + ''.join(
        'Path = %s\nSize = %d\n' % (name, index + 1)
        for index, name in enumerate(names))


def _archive_writing(output: bytes,
                     monkeypatch: pytest.MonkeyPatch) -> archive_base.BaseArchive:
    """Return a 7z archive whose listing runs a real child writing output bytes."""
    archive = sevenzip_external.SevenZipArchive('book.archive')
    args = [sys.executable, '-c', 'import sys;sys.stdout.buffer.write(%r)' % output]
    monkeypatch.setattr(archive, '_get_executable', lambda: sys.executable)
    monkeypatch.setattr(archive, '_get_list_arguments', lambda: args)
    return archive


def _archive_running(run: Mock, monkeypatch: pytest.MonkeyPatch) -> archive_base.BaseArchive:
    archive = sevenzip_external.SevenZipArchive('book.archive')
    monkeypatch.setattr(archive, '_get_executable', lambda: 'helper')
    monkeypatch.setattr(subprocess, 'run', run)
    return archive


def test_real_utf8_subprocess_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ['café/雪.png', 'a?b.png', 'a_b.png']
    archive = _archive_writing(_listing(names).encode('utf-8'), monkeypatch)
    for _ in range(2):
        public = archive.list_contents()
        assert [archive._original_filename(name) for name in public] == names
        assert len({os.path.normcase(name) for name in public}) == len(names)
        assert [archive.get_entry_size(name) for name in public] == [1, 2, 3]
        assert len(archive._contents) == len(names)
    assert archive.is_solid()


def test_invalid_utf8_is_actionable_and_never_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even an invalid byte after an otherwise valid member must reject the whole
    # listing, rather than yielding a usable-looking, replacement-named page.
    output = _listing(['valid.png']).encode('utf-8') + b'\xff'
    archive = _archive_writing(output, monkeypatch)
    with pytest.raises(archive_base.ArchiveError, match='non-UTF-8.*Update') as raised:
        archive.list_contents()
    assert isinstance(raised.value.__cause__, UnicodeDecodeError)
    assert archive.unicode_mapping == {}


def test_listing_process_flags_and_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=subprocess.CompletedProcess([], 0, _listing(['page.png'])))
    archive = _archive_running(run, monkeypatch)
    monkeypatch.setattr(process, '_get_creationflags', lambda: 1234)
    archive.list_contents()
    kwargs = run.call_args.kwargs
    assert kwargs['creationflags'] == 1234
    assert kwargs['stdin'] is process.NULL
    assert '-sccUTF-8' in run.call_args.args[0]


def test_sevenzip_encrypted_header_retry_preserves_utf8_member_identity(
        monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], 2, _ENCRYPTED_HEADER),
        subprocess.CompletedProcess([], 0, _listing(['雪.png']))])
    archive = _archive_running(run, monkeypatch)
    password = Mock(side_effect=lambda: setattr(archive, '_password', 'secret'))
    monkeypatch.setattr(archive, '_get_password', password)
    names = archive.list_contents()
    assert [archive._original_filename(name) for name in names] == ['雪.png']
    assert run.call_count == 2 and password.call_count == 1
    assert '-psecret' in run.call_args.args[0]
    assert [archive.get_entry_size(name) for name in names] == [1]


@pytest.mark.parametrize('status', [1, 2])
def test_listing_status_never_publishes_failed_members(
        status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=subprocess.CompletedProcess([], status, _listing(['page.png'])))
    archive = _archive_running(run, monkeypatch)
    if status == 1:
        assert archive.list_contents()
    else:
        with pytest.raises(archive_base.ArchiveError, match='exit status %d' % status):
            archive.list_contents()
        assert not archive.unicode_mapping


def test_wrong_encrypted_header_password_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=subprocess.CompletedProcess([], 2, _ENCRYPTED_HEADER))
    archive = _archive_running(run, monkeypatch)
    monkeypatch.setattr(archive, '_get_password', lambda: setattr(archive, '_password', 'wrong'))
    with pytest.raises(archive_base.ArchiveError, match='exit status 2'):
        archive.list_contents()
    assert run.call_count == 2
    assert not archive.unicode_mapping
