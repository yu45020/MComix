"""Python ZIP codec selection, extraction and application loading regressions."""

import io
import os
from pathlib import Path
import shutil
import tempfile
from typing import Dict, List
from unittest.mock import Mock
import zipfile

from PIL import Image
import pytest

from mcomix import archive_extractor, archive_tools, constants
from mcomix.archive import password
from mcomix.archive import zip as zip_archive
from mcomix.preferences import prefs
from ._archive_fakes import pump, real_file_handler


_METHODS = [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED,
            zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA]
_CODECS = {zipfile.ZIP_DEFLATED: 'zlib', zipfile.ZIP_BZIP2: 'bz2', zipfile.ZIP_LZMA: 'lzma'}


def _write(path: Path, methods: List[int]) -> Dict[str, bytes]:
    pages = {}
    with zipfile.ZipFile(str(path), 'w') as output:
        output.writestr('pages/', b'')
        for index, method in enumerate(methods):
            codec = _CODECS.get(method)
            if codec is not None and getattr(zipfile, codec) is None:
                pytest.skip('%s codec unavailable to generate fixture' % codec)
            buffer = io.BytesIO()
            Image.new('RGB', (12 + index, 9), (index * 50, 25, 100)).save(buffer, 'PNG')
            name = 'pages/%02d-画.png' % index
            pages[os.path.normpath(name)] = buffer.getvalue()
            output.writestr(name, buffer.getvalue(), compress_type=method)
        output.writestr('empty.txt', b'', compress_type=methods[-1])
    return pages


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    # No external extractor may accidentally make a broken classification pass.
    monkeypatch.setitem(archive_tools._HANDLERS, constants.ZIP_EXTERNAL, ())


@pytest.mark.parametrize('method', [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_real_supported_methods_select_python_and_round_trip(tmp_path: Path, method: int) -> None:
    path = tmp_path / 'book.cbz'
    pages = _write(path, [method])
    assert zip_archive.is_py_supported_zipfile(str(path))
    assert archive_tools.archive_mime_type(str(path)) == constants.ZIP
    archive = archive_tools.get_archive_handler(str(path))
    assert isinstance(archive, zip_archive.ZipArchive)
    try:
        assert archive.supports_random_access()
        names = archive.list_contents()
        for name, data in pages.items():
            assert name in names and archive.read(name) == data
            assert archive.get_entry_size(name) == len(data)
            archive.extract(name, str(tmp_path / 'out'))
            assert (tmp_path / 'out' / name).read_bytes() == data
        assert archive.read('empty.txt') == b''
    finally:
        archive.close()
    path.unlink()  # Windows handles must be closed too.


def test_missing_required_codec_uses_external_fallback(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'book.cbz'
    _write(path, [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
    monkeypatch.setattr(zipfile, 'zlib', None)
    assert not zip_archive.is_py_supported_zipfile(str(path))
    assert archive_tools.archive_mime_type(str(path)) == constants.ZIP_EXTERNAL
    assert archive_tools.get_archive_handler(str(path)) is None
    external = Mock(__name__='ExternalZip')
    external.is_available.return_value = True
    monkeypatch.setitem(archive_tools._HANDLERS, constants.ZIP_EXTERNAL, (external,))
    assert archive_tools.get_archive_handler(str(path)) is external.return_value
    external.assert_called_once_with(str(path))


def test_bzip2_traditional_encryption_still_uses_python(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(__file__).parent / 'files' / 'archives' / 'Encrypted.zip.bz2'
    if zipfile.bz2 is None:
        pytest.skip('bz2 codec unavailable')
    asked = Mock(return_value='password')
    monkeypatch.setattr(password, 'ask_for_password', asked)
    archive = archive_tools.get_archive_handler(str(path))
    assert isinstance(archive, zip_archive.ZipArchive)
    try:
        names = archive.list_contents()
        assert names
        for name in names:
            assert archive.read(name)
        asked.assert_called_once_with(archive)
    finally:
        archive.close()


def _write_raw_names(path: Path, names: List[bytes]) -> None:
    """Write members whose names are these bytes, without the UTF-8 flag."""
    placeholders = [bytes([ord('A') + index]) * len(name) for index, name in enumerate(names)]
    with zipfile.ZipFile(str(path), 'w') as output:
        for index, placeholder in enumerate(placeholders):
            output.writestr(placeholder.decode('ascii'), b'page %d' % index)
    data = path.read_bytes()
    for placeholder, name in zip(placeholders, names):
        data = data.replace(placeholder, name)
    path.write_bytes(data)


def test_names_without_utf8_flag_decode_as_utf8_then_cp932_then_cp437(
        tmp_path: Path) -> None:
    path = tmp_path / 'legacy.zip'
    # 表 is 95 5C in cp932: its second byte is a backslash.
    _write_raw_names(path, ['表紙.png'.encode('cp932'), '画/01.png'.encode('utf-8'),
                           b'\x81 x.png'])
    archive = zip_archive.ZipArchive(str(path))
    try:
        names = archive.list_contents()
        assert names == ['表紙.png', os.path.join('画', '01.png'), 'ü x.png']
        assert [archive.read(name) for name in names] == [b'page 0', b'page 1', b'page 2']
    finally:
        archive.close()


@pytest.mark.parametrize('lazy', [False, True])
def test_mixed_codecs_file_handler_loads_all_pages(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lazy: bool) -> None:
    path = tmp_path / 'mixed.cbz'
    pages = _write(path, _METHODS)
    monkeypatch.setitem(prefs, 'lazy archive reading', lazy)
    handler, window = real_file_handler(monkeypatch)
    handler.thread_delete = shutil.rmtree
    image = window.imagehandler
    try:
        assert handler.open_file(str(path))
        pump(lambda: handler.file_loaded and not handler.file_loading)
        assert handler.archive_type == constants.ZIP
        assert len(image._image_files) == len(pages)
        assert handler._extractor.is_lazy() == lazy
        for page in image._image_files:
            name = handler._name_table[page]
            assert handler.read_file(page) == pages[name]
            assert handler.materialize([page]) == []
            assert Path(page).read_bytes() == pages[name]
    finally:
        handler.close_file(wait_for_release=True)
        handler.wait_for_file_release(str(path))
    assert handler._tmp_dir is None
    path.unlink()


@pytest.mark.parametrize('lazy', [False, True])
def test_nested_new_codecs_keep_recursive_storage_owned(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lazy: bool) -> None:
    inner = tmp_path / 'inner.cbz'
    pages = _write(inner, [zipfile.ZIP_BZIP2])
    outer = tmp_path / 'outer.cbz'
    with zipfile.ZipFile(str(outer), 'w') as output:
        output.writestr('inner.cbz', inner.read_bytes(), compress_type=zipfile.ZIP_LZMA)
    monkeypatch.setitem(prefs, 'lazy archive reading', lazy)
    destination = tmp_path / 'extract'
    destination.mkdir()
    extractor = archive_extractor.Extractor()
    try:
        extractor.setup(str(outer), str(destination))
        pump(lambda: extractor.get_files() is not None)
        names = [os.path.join('inner.cbz', name) for name in pages]
        assert set(names) <= set(extractor.get_files())
        assert extractor.is_lazy() == lazy
        private = Path(extractor._archive._sub_archive_dir.name)
        assert private.is_dir() and destination not in private.parents
        extractor.set_files(names)
        extractor.extract()
        pump(lambda: all(extractor.is_ready(name) for name in names))
        for name, data in zip(names, pages.values()):
            assert extractor.read(name) == data
            assert Path(extractor.materialize(name)).read_bytes() == data
    finally:
        extractor.close()
    assert not private.exists()
    outer.unlink()
    inner.unlink()
