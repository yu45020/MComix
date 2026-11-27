"""Disk thumbnail identity, native-size reuse and damaged-cache recovery."""

import io
import os
import struct
import zlib
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image, PngImagePlugin

from mcomix import constants, image_tools, thumbnail_tools
from mcomix.archive.zip import ZipArchive


@pytest.fixture
def cached(tmp_path_factory, monkeypatch):
    tmp_path = tmp_path_factory.mktemp('thumbnail-cache')
    # Legacy unittest cases leave tempfile.tempdir pointing to removed storage.
    monkeypatch.setattr(thumbnail_tools.tempfile, 'tempdir', str(tmp_path))
    source = tmp_path / 'cover.png'
    Image.new('RGB', (100, 150), 'red').save(source)
    thumbnailer = thumbnail_tools.Thumbnailer(
        dst_dir=str(tmp_path / 'thumbs'), store_on_disk=True,
        size=(500, 500), archive_support=True)
    return thumbnailer, str(source)


def write_cache(thumbnailer, source, size=(100, 150), changes=None,
                remove=None):
    path = Path(thumbnailer._path_to_thumbpath(source))
    path.parent.mkdir(exist_ok=True)
    metadata = thumbnailer._get_text_data(source)
    metadata = {key[6:]: value
                for key, value in metadata.items()}
    metadata.update(changes or {})
    if remove is not None:
        metadata.pop(remove)
    text = PngImagePlugin.PngInfo()
    for key, value in metadata.items():
        text.add_text(key, value)
    Image.new('RGB', size, 'blue').save(path, format='PNG', pnginfo=text)
    return path


def reuses_cache(thumbnailer, source):
    """Return whether thumbnail() answered from the disk cache."""
    with mock.patch.object(thumbnailer, '_create_thumbnail_pixbuf',
                           wraps=thumbnailer._create_thumbnail_pixbuf) as create:
        assert thumbnailer.thumbnail(source) is not None
    return not create.called


def assert_reused(thumbnailer, source):
    first = thumbnailer.thumbnail(source)
    with mock.patch.object(thumbnailer, '_create_thumbnail_pixbuf',
                           side_effect=AssertionError('cache missed')):
        second = thumbnailer.thumbnail(source)
    assert (second.get_width(), second.get_height()) == (
        first.get_width(), first.get_height())
    assert image_tools.pixbuf_to_pil(second).tobytes() == image_tools.pixbuf_to_pil(first).tobytes()


@pytest.mark.parametrize('source_size,target', [
    ((100, 150), (500, 500)), ((1, 151), (500, 500)), ((100, 150), (100, 500)),
    ((100, 150), (70, 180)), ((600, 900), (120, 300)),
])
def test_actual_decoder_outputs_are_reused(cached, source_size, target):
    thumbnailer, source = cached
    Image.new('RGB', source_size, 'red').save(source)
    thumbnailer.width, thumbnailer.height = target
    assert_reused(thumbnailer, source)


def test_missing_metadata_regenerates(cached):
    thumbnailer, source = cached
    write_cache(thumbnailer, source, remove='Thumb::MTime')
    result = thumbnailer.thumbnail(source)
    assert bytes(result.get_pixels())[:3] == bytes((255, 0, 0))
    assert reuses_cache(thumbnailer, source)


@pytest.mark.parametrize('changes', [
    {'Thumb::URI': 'file:///another-book.png'}, {'Thumb::MTime': 'nan'},
    {'Thumb::Size': '-1'},
])
def test_invalid_metadata_regenerates(cached, changes):
    thumbnailer, source = cached
    write_cache(thumbnailer, source, changes=changes)
    result = thumbnailer.thumbnail(source)
    assert bytes(result.get_pixels())[:3] == bytes((255, 0, 0))
    assert reuses_cache(thumbnailer, source)


def test_different_requested_size_is_rejected(cached):
    thumbnailer, source = cached
    write_cache(thumbnailer, source, changes={'MComix::Thumb::Size': '128x128'})
    assert not reuses_cache(thumbnailer, source)
    assert reuses_cache(thumbnailer, source)
    other = thumbnail_tools.Thumbnailer(
        dst_dir=thumbnailer.dst_dir, store_on_disk=True,
        size=(500, 499), archive_support=True)
    assert not reuses_cache(other, source)


def png_chunks(data):
    """Yield (kind, payload, crc bytes) of each chunk after the signature."""
    offset = 8
    while offset < len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        end = offset + 8 + length
        yield data[offset + 4:offset + 8], data[offset + 8:end], data[end:end + 4]
        offset = end + 4


def replace_idat(path, payload, keep_crc=False):
    """Swap the single IDAT payload, with a matching CRC unless keep_crc."""
    data = path.read_bytes()
    parts = [data[:8]]
    for kind, old, crc in png_chunks(data):
        if kind == b'IDAT':
            old = payload
            if not keep_crc:
                crc = struct.pack('>I', zlib.crc32(kind + payload))
        parts.append(struct.pack('>I', len(old)) + kind + old + crc)
    path.write_bytes(b''.join(parts))


@pytest.mark.parametrize('damage', ['zlib-checksum', 'idat-crc', 'random'])
def test_damaged_png_regenerates(cached, damage):
    thumbnailer, source = cached
    path = write_cache(thumbnailer, source)
    if damage == 'zlib-checksum':
        data = bytearray(path.read_bytes())
        data[-17] ^= 255
        path.write_bytes(data)
    elif damage == 'idat-crc':
        # Pixels that decode, under the CRC of the original IDAT.
        other = io.BytesIO()
        Image.new('RGB', (100, 150), 'green').save(other, format='PNG')
        payload = next(payload for kind, payload, crc in png_chunks(other.getvalue())
                       if kind == b'IDAT')
        replace_idat(path, payload, keep_crc=True)
    else:
        path.write_bytes(b'not an image')
    assert not reuses_cache(thumbnailer, source)
    assert reuses_cache(thumbnailer, source)


def test_crc_valid_undecodable_idat_regenerates_once(cached):
    thumbnailer, source = cached
    path = write_cache(thumbnailer, source)
    replace_idat(path, b'invalid compressed image data')
    with Image.open(path) as image:
        image.verify()  # Header/CRC checks do not establish decodability.
    assert not reuses_cache(thumbnailer, source)
    result = thumbnailer.thumbnail(source)
    assert bytes(result.get_pixels())[:3] == bytes((255, 0, 0))
    assert reuses_cache(thumbnailer, source)


@pytest.mark.parametrize('outcome', ['valid', 'bad-size', 'bad-crc'])
def test_cache_file_is_released_on_every_validation_exit(cached, outcome):
    thumbnailer, source = cached
    path = write_cache(thumbnailer, source,
                       changes={'MComix::Thumb::Size': '99x150'} if outcome == 'bad-size' else None)
    if outcome == 'bad-crc':
        data = bytearray(path.read_bytes())
        data[-17] ^= 255
        path.write_bytes(data)
    assert reuses_cache(thumbnailer, source) == (outcome == 'valid')
    # Windows refuses replacement when a reader retains its file handle.
    renamed = path.with_name('moved.png')
    path.rename(renamed)
    renamed.rename(path)


def test_offline_cover_is_available_but_wrong_identity_still_rejected(cached):
    thumbnailer, source = cached
    path = write_cache(thumbnailer, source, changes={'Thumb::URI': 'file:///another-book.png'})
    wrong_identity = path.read_bytes()
    write_cache(thumbnailer, source)
    os.unlink(source)
    assert reuses_cache(thumbnailer, source)
    path.write_bytes(wrong_identity)
    # Without its source the book has no thumbnail; the other book's is never shown.
    with mock.patch.object(thumbnailer, '_create_thumbnail_pixbuf', return_value=(None, None)):
        assert thumbnailer.thumbnail(source) is None


def test_zip_cover_decoded_once_across_fresh_thumbnailers(cached):
    thumbnailer, source = cached
    archive = str(Path(source).with_suffix('.cbz'))
    with zipfile.ZipFile(archive, 'w') as output:
        output.write(source, 'cover.png')
    reads = []
    original = ZipArchive.read

    def read(self, name):
        reads.append(name)
        return original(self, name)

    with mock.patch.object(ZipArchive, 'read', read):
        for _ in range(4):
            fresh = thumbnail_tools.Thumbnailer(
                dst_dir=thumbnailer.dst_dir, store_on_disk=True,
                size=(500, 500), archive_support=True)
            image = fresh.thumbnail(archive)
            assert (image.get_width(), image.get_height()) == (100, 150)
    assert reads == ['cover.png']


@pytest.mark.parametrize('format', ['JPEG', 'GIF', 'TIFF'])
def test_other_source_formats_and_exif_keep_existing_geometry(cached, format):
    thumbnailer, source = cached
    source = str(Path(source).with_suffix('.' + format.lower()))
    image = Image.new('RGB', (100, 150), 'red')
    if format in ('JPEG', 'TIFF'):
        exif = image.getexif()
        exif[274] = 6
        image.save(source, format=format, exif=exif)
    else:
        image.save(source, format=format)
    assert_reused(thumbnailer, source)


@pytest.mark.skipif(os.name != 'nt', reason='Windows exclusive file ownership')
def test_exclusively_locked_cache_does_not_block_thumbnail_generation(cached):
    import ctypes
    from ctypes import wintypes

    thumbnailer, source = cached
    path = write_cache(thumbnailer, source)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        assert not reuses_cache(thumbnailer, source)
    finally:
        assert kernel.CloseHandle(handle)
    assert reuses_cache(thumbnailer, source)


def test_huge_corrupt_cached_ihdr_regenerates_without_allocating(cached):
    thumbnailer, source = cached
    path = write_cache(thumbnailer, source)
    data = path.read_bytes()
    payload = struct.pack('>II', 100000, 100000) + data[24:29]
    path.write_bytes(data[:16] + payload + struct.pack('>I', zlib.crc32(b'IHDR' + payload)) + data[33:])
    with pytest.raises(Image.DecompressionBombError):
        Image.open(path)
    assert not reuses_cache(thumbnailer, source)
    assert reuses_cache(thumbnailer, source)


@pytest.mark.parametrize('archive', [False, True])
def test_pillow_jpeg_outputs_are_reused(
        cached, monkeypatch, archive):
    thumbnailer, source = cached
    source = str(Path(source).with_suffix('.jpg'))
    Image.new('RGB', (100, 150), 'red').save(source)
    if archive:
        path = str(Path(source).with_suffix('.cbz'))
        with zipfile.ZipFile(path, 'w') as output:
            output.write(source, 'cover.jpg')
        source = path
    original = image_tools.get_image_info

    def pillow_only(source):
        format, dimensions, providers = original(source)
        return format, dimensions, (constants.IMAGEIO_PIL,)

    monkeypatch.setattr(image_tools, 'get_image_info', pillow_only)
    assert_reused(thumbnailer, source)
