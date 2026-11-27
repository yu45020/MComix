# coding: utf-8

import io
import os
import subprocess
import tempfile
import zlib
from pathlib import Path
from unittest import mock

import pytest

from . import MComixTest

from mcomix import process
from mcomix.archive import archive_base
from mcomix.archive import sevenzip_external


def _completed(returncode, stdout=b'', stderr=b''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class ExternalReadTest(MComixTest):
    """ Error handling of archives read with an external executable,
    with the executable mocked. """

    def setUp(self):
        super(ExternalReadTest, self).setUp()
        self.dest_dir = tempfile.mkdtemp(prefix='extract.')

    def _archive(self, executable='7z'):
        cls = sevenzip_external.SevenZipArchive
        patcher = mock.patch.object(cls, '_get_executable', lambda self: executable)
        patcher.start()
        self.addCleanup(patcher.stop)
        return cls('test.archive')

    def _run(self, result):
        return mock.patch.object(subprocess, 'run', return_value=result)

    def test_read(self):
        archive = self._archive()
        with self._run(_completed(0, b'CONTENT')):
            self.assertEqual(archive.read('page.jpg'), b'CONTENT')
        # A valid empty entry.
        with self._run(_completed(0, b'')):
            self.assertEqual(archive.read('page.jpg'), b'')
        # Warnings: the data is still usable.
        with self._run(_completed(1, b'CONTENT', b'warning')):
            self.assertEqual(archive.read('page.jpg'), b'CONTENT')

    def test_read_failure(self):
        archive = self._archive()
        for stdout in (b'', b'PARTIAL CONTENT'):
            with self._run(_completed(2, stdout, b'CRC Failed')):
                self.assertRaises(archive_base.ArchiveError,
                                  archive.read, 'page.jpg')

    def test_no_executable(self):
        archive = self._archive(None)
        self.assertRaises(archive_base.ArchiveError, archive.read, 'page.jpg')
        self.assertRaises(archive_base.ArchiveError, archive.extract,
                          'page.jpg', self.dest_dir)

    def test_extract_streams_to_file(self):
        archive = self._archive()
        path = os.path.join(self.dest_dir, 'page.jpg')

        def run(args, stdout=None, **kwargs):
            # The output goes straight to the file, not through memory.
            self.assertNotEqual(stdout, subprocess.PIPE)
            stdout.write(b'CONTENT')
            return _completed(0, None)

        with mock.patch.object(subprocess, 'run', run):
            archive.extract('page.jpg', self.dest_dir)
        with open(path, 'rb') as fp:
            self.assertEqual(fp.read(), b'CONTENT')

    def test_extract_failure(self):
        archive = self._archive()

        def run(args, stdout=None, **kwargs):
            stdout.write(b'PARTIAL')
            return _completed(2, None, b'CRC Failed')

        with mock.patch.object(subprocess, 'run', run):
            self.assertRaises(archive_base.ArchiveError, archive.extract,
                              'page.jpg', self.dest_dir)
        # No partial file is left.
        self.assertEqual(os.listdir(self.dest_dir), [])

    def test_solid_extraction_stopping_early(self):
        archive = self._archive()
        archive._contents = [('1.jpg', 4, None), ('2.jpg', 4, None), ('3.jpg', 4, None)]
        archive.unicode_mapping.update((name, name) for name, size, crc in archive._contents)
        proc = mock.Mock(stdout=io.BytesIO(b'AAAABB'))
        proc.wait.return_value = 2

        with mock.patch.object(process, 'popen', return_value=proc):
            pages = archive.iter_extract(['1.jpg', '2.jpg', '3.jpg'], self.dest_dir)
            self.assertEqual(next(pages), '1.jpg')
            self.assertRaisesRegex(archive_base.ArchiveError, 'exit status 2', next, pages)
        # The truncated page is never written.
        self.assertEqual(os.listdir(self.dest_dir), ['1.jpg'])


_PAGES = [('1.jpg', b'AAAA'), ('2.jpg', b'BBBB'), ('3.jpg', b'CCCC')]


def _listing(archive_type: str, encrypted: bool) -> str:
    # Real 7z order for RAR listings: Encrypted before CRC.
    entry = 'Path = %s\nSize = 4\n' + ('Encrypted = +\n' if encrypted else '') + \
        'CRC = %08X\n'
    header = 'Type = %s\nSolid = +\n----------\n' % archive_type
    return header + ''.join(entry % (name, zlib.crc32(data)) for name, data in _PAGES)


@pytest.mark.parametrize('archive_type, encrypted, checked', [
    ('7z', False, True), ('Rar', False, True),
    # LHA lists CRC-16 in 7z's CRC field, and RAR5 a password-dependent
    # checksum for encrypted members: neither is compared.
    ('Lzh', False, False), ('Rar5', True, False),
])
def test_solid_extraction_rejects_a_damaged_full_length_page(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive_type: str, encrypted: bool,
        checked: bool) -> None:
    archive = sevenzip_external.SevenZipArchive('book.archive')
    monkeypatch.setattr(archive, '_get_executable', lambda: 'tool')
    monkeypatch.setattr(archive, '_get_password', lambda: setattr(archive, '_password', 'pw'))
    monkeypatch.setattr(subprocess, 'run', mock.Mock(
        return_value=subprocess.CompletedProcess([], 0, _listing(archive_type, encrypted))))
    names = archive.list_contents()
    # 7z writes a member's full size even when its data fails to decode.
    proc = mock.Mock(stdout=io.BytesIO(b'AAAAXXXXCCCC'))
    proc.wait.return_value = 2
    monkeypatch.setattr(process, 'popen', mock.Mock(return_value=proc))

    pages = archive.iter_extract(names, str(tmp_path))
    if checked:
        assert next(pages) == '1.jpg'
        with pytest.raises(archive_base.ArchiveError, match='CRC mismatch'):
            next(pages)
        assert os.listdir(tmp_path) == ['1.jpg']
    else:
        assert list(pages) == names
