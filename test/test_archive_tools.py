
import io
import lzma
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

import pytest

from . import MComixTest, get_testfile_path

from mcomix import archive_tools
from mcomix import constants
from mcomix.archive import sevenzip_external

from ._archive_fakes import FakeArchive


_EXTENSION_TO_MIME_TYPES = {
    'cbz'    : constants.ZIP,
    'zip'    : constants.ZIP,
    'zip.bz2': constants.ZIP if zipfile.bz2 is not None else constants.ZIP_EXTERNAL,
    'rar'    : constants.RAR,
    'tar'    : constants.TAR,
    'tar.gz' : constants.GZIP,
    'tar.bz2': constants.BZIP2,
    'tar.xz' : constants.XZ,
    'pdf'    : constants.PDF,
    '7z'     : constants.SEVENZIP,
    'lha'    : constants.LHA,
}

_ARCHIVE_TYPE_NAMES = {
    constants.ZIP         : 'zip',
    constants.RAR         : 'rar',
    constants.TAR         : 'tar',
    constants.GZIP        : 'gzip',
    constants.BZIP2       : 'bzip2',
    constants.XZ          : 'xz',
    constants.PDF         : 'pdf',
    constants.SEVENZIP    : '7z',
    constants.LHA         : 'lha',
    constants.ZIP_EXTERNAL: 'zip (external)',
}

class ArchiveToolsTest(MComixTest):

    def test_archive_mime_type(self):

       dir = get_testfile_path('archives')
       for filename in os.listdir(dir):
           ext = '.'.join(filename.split('.')[1:])
           path = os.path.join(dir, filename)
           archive_type = archive_tools.archive_mime_type(path)
           expected_type = _EXTENSION_TO_MIME_TYPES.get(ext, '???')
           msg = (
               'archive_mime_type("%s") failed; '
               'result differs: %s [%s] instead of %s [%s]'
               % (path,
                  archive_type, _ARCHIVE_TYPE_NAMES.get(archive_type, '???'),
                  expected_type, _ARCHIVE_TYPE_NAMES.get(expected_type, '???'),
                 )
           )
           self.assertEqual(archive_type, expected_type, msg=msg)


def _tar_bytes() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as output:
        info = tarfile.TarInfo('page.jpg')
        info.size = 4
        output.addfile(info, io.BytesIO(b'page'))
    return stream.getvalue()


@pytest.mark.parametrize('name, data, expected', [
    ('book.tar.xz', lzma.compress(_tar_bytes()), constants.XZ),
    ('book.tar.lzma', lzma.compress(_tar_bytes(), format=lzma.FORMAT_ALONE), constants.XZ),
    # Like a plain .gz: not an archive.
    ('notes.xz', lzma.compress(b'notes'), None),
    ('notes.lzma', lzma.compress(b'notes', format=lzma.FORMAT_ALONE), None),
], ids=['tar.xz', 'tar.lzma', 'xz', 'lzma'])
def test_xz_is_an_archive_only_when_it_holds_a_tar(
        tmp_path: Path, name: str, data: bytes, expected: Optional[int]) -> None:
    path = tmp_path / name
    path.write_bytes(data)
    assert archive_tools.archive_mime_type(str(path)) == expected


def test_tar_formats_include_xz_without_7z() -> None:
    # The tar backend reads them: they must not depend on 7z being installed.
    assert {'xz', 'lzma'} <= set(archive_tools.get_supported_formats()['Tar'][1])


@pytest.mark.parametrize('nested, data', [
    ('extras.7z', b'7z\xbc\xaf\x27\x1c' + b'\x00' * 64),
    # A solid backend whose listing fails after its first member.
    ('broken.cbz', b'fake'),
], ids=['damaged 7z', 'failed listing'])
def test_damaged_nested_archive_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           nested: str, data: bytes) -> None:
    if nested == 'extras.7z' and not sevenzip_external.SevenZipArchive.is_available():
        pytest.skip('7z not available')
    book = tmp_path / 'book.cbz'
    with zipfile.ZipFile(book, 'w') as output:
        output.writestr('page1.jpg', b'page')
        output.writestr(nested, data)
    if nested == 'broken.cbz':
        get_handler = archive_tools.get_archive_handler
        monkeypatch.setattr(archive_tools, 'get_archive_handler', lambda path, mimetype=None:
                            get_handler(path, mimetype) if path == str(book)
                            else FakeArchive('list-error', solid=True))
    destination = tmp_path / 'extract'
    destination.mkdir()
    archive = archive_tools.get_recursive_archive_handler(str(book), str(destination))
    try:
        assert archive.list_contents() == ['page1.jpg']
        # The skipped archive takes no part in how the book is read.
        assert archive.supports_random_access()
        assert not archive.is_solid()
    finally:
        archive.close()

