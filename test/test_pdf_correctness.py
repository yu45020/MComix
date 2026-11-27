"""PDF identifiers, lossless rotation and external renderer failure handling."""

import io
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import Mock

from PIL import Image
import pytest

from mcomix import image_tools
from mcomix.archive import archive_base, pdf_external


@pytest.fixture(autouse=True)
def isolated_temp(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))


@pytest.fixture
def native() -> Any:
    return pytest.importorskip('mcomix.archive.native_pdf.child')


def image_bytes(format: str = 'JPEG', mode: str = 'RGB') -> bytes:
    image = Image.new(mode, (30, 20))
    image.putdata([(x * 7 % 256, y * 11 % 256, (x + y) * 5 % 256)
                   for y in range(20) for x in range(30)])
    buffer = io.BytesIO()
    image.save(buffer, format=format, quality=95)
    image.close()
    return buffer.getvalue()


def worker_for_images(native: Any, tmp_path: Path, images: list,
                      rotations: list) -> Any:
    path = tmp_path / 'mixed.pdf'
    with native.pymupdf.open() as doc:
        for data, rotation in zip(images, rotations):
            page = doc.new_page(width=30, height=20)
            page.insert_image(page.rect, stream=data)
            page.set_rotation(rotation)
        doc.save(str(path))
    return native.MuPDFWorker(str(path))


@pytest.mark.parametrize('rotation', [90, 270])
def test_rotated_jpeg_preserves_pixels_and_gtk_orientation(native: Any,
                                                           tmp_path: Path,
                                                           rotation: int) -> None:
    original = image_bytes()
    worker = worker_for_images(native, tmp_path, [original], [rotation])
    try:
        name, = worker.iter_contents()
        assert name.endswith('.png')
        worker.extract_file(name, str(tmp_path))
        with Image.open(io.BytesIO(original)) as image:
            expected = image.rotate(-rotation, expand=True)
        with Image.open(tmp_path / name) as result:
            assert result.format == 'PNG'
            assert result.size == expected.size
            assert result.tobytes() == expected.tobytes()
            assert result.getexif().get(274) is None
        pixbuf = image_tools.load_pixbuf(str(tmp_path / name))
        assert image_tools.get_implied_rotation(pixbuf) == 0
        assert (pixbuf.get_width(), pixbuf.get_height()) == expected.size
        pixels = bytes(pixbuf.get_pixels())
        rows = [pixels[y * pixbuf.get_rowstride():
                       y * pixbuf.get_rowstride() + expected.width * 3]
                for y in range(expected.height)]
        assert b''.join(rows) == expected.tobytes()
        expected.close()
    finally:
        worker.doc.close()


def test_mixed_image_formats_keep_original_jpeg(native: Any, tmp_path: Path) -> None:
    original = image_bytes()
    worker = worker_for_images(native, tmp_path,
                               [original, image_bytes('PNG'), original], [0, 0, 90])
    try:
        names = list(worker.iter_contents())
        assert [Path(name).suffix for name in names] == ['.jpeg', '.png', '.png']
        for name in names:
            worker.extract_file(name, str(tmp_path))
            with Image.open(tmp_path / name) as image:
                assert image.format == ('JPEG' if name.endswith('.jpeg') else 'PNG')
        assert (tmp_path / names[0]).read_bytes() == original
    finally:
        worker.doc.close()


def test_real_five_digit_xref(native: Any, tmp_path: Path) -> None:
    path = tmp_path / 'large-xref.pdf'
    original = image_bytes()
    with native.pymupdf.open() as doc:
        page = doc.new_page(width=30, height=20)
        while doc.xref_length() < 10001:
            doc.update_object(doc.get_new_xref(), 'null')
        xref = page.insert_image(page.rect, stream=original)
        assert xref >= 10001
        doc.save(str(path), garbage=0)
    worker = native.MuPDFWorker(str(path))
    try:
        name, = worker.iter_contents()
        assert '_mcmxref%d.' % xref in name
        worker.extract_file(name, str(tmp_path))
        assert (tmp_path / name).read_bytes() == original
    finally:
        worker.doc.close()


def many_page_pdf(count: int) -> bytes:
    # Minimal real PDF: avoid quadratic page insertion while exercising MuPDF's
    # actual page tree and selecting distinct content on the five-digit page.
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               ('<< /Type /Pages /Count %d /Kids [%s] >>' %
                (count, ' '.join('%d 0 R' % n for n in range(3, count + 3)))).encode()]
    for index in range(count):
        content = ' /Contents %d 0 R' % (count + 3) if index == count - 1 else ''
        objects.append(('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 3 2]%s >>'
                        % content).encode())
    stream = b'1 0 0 rg 0 0 3 2 re f\n'
    objects.append(b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'endstream')
    data = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(b'%d 0 obj\n' % number + obj + b'\nendobj\n')
    start = len(data)
    data.extend(b'xref\n0 %d\n0000000000 65535 f \n' % len(offsets))
    for offset in offsets[1:]:
        data.extend(b'%010d 00000 n \n' % offset)
    data.extend(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n'
                % (len(offsets), start))
    return bytes(data)


def test_real_five_digit_page_render(native: Any, tmp_path: Path) -> None:
    path = tmp_path / 'many-pages.pdf'
    path.write_bytes(many_page_pdf(10000))
    worker = native.MuPDFWorker(str(path))
    try:
        names = worker.list_pages(0, 10001)
        assert len(names) == 10000 and names[-1] == 'page10000.png'
        worker.extract_file('page10000.png', str(tmp_path))
        with Image.open(tmp_path / 'page10000.png') as image:
            assert image.getpixel((0, 0)) == (255, 0, 0)
    finally:
        worker.doc.close()


@pytest.fixture
def external(monkeypatch: Any) -> Iterator[Any]:
    monkeypatch.setattr(pdf_external, '_mutool_exec', ['mutool'])
    yield pdf_external.PdfArchive('sample.pdf')


@pytest.mark.parametrize('version_output, available', [
    (b'', False),  # MuPDF 1.6 has no -v switch.
    (b'mutool version 1.7.0\n', False),
    (b'mutool version 1.8.0\n', True),
    (b'mutool version 1.24.2\n', True),
])
def test_external_needs_mutool_draw(monkeypatch: Any, version_output: bytes,
                                    available: bool) -> None:
    monkeypatch.setattr(pdf_external, '_pdf_possible', None)
    monkeypatch.setattr(pdf_external, '_mutool_exec', None)
    found = []

    def find_executable(candidates: Any) -> str:
        found.extend(candidates)
        return '/tools/' + candidates[0]

    proc = Mock(stderr=io.BytesIO(version_output))
    monkeypatch.setattr(pdf_external.process, 'find_executable', find_executable)
    monkeypatch.setattr(pdf_external.process, 'popen', lambda *a, **k: proc)
    assert pdf_external.PdfArchive.is_available() is available
    assert found == ['mutool']


def completed(code: int = 0, stdout: str = '', stderr: str = '') -> Any:
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.mark.parametrize('matrix,width,height', [
    ('0 0 0 0 0 0', 30, 20), ('1 0 0 1 0 0', 30, 0),
    ('nan 0 0 1 0 0', 30, 20), ('1 0 0', 30, 20),
])
def test_external_rejects_degenerate_geometry(external: Any, monkeypatch: Any,
                                              tmp_path: Path, matrix: str,
                                              width: int, height: int) -> None:
    trace = '<fill_image matrix="%s" width="%d" height="%d"/>' % (matrix, width, height)
    monkeypatch.setattr(pdf_external.subprocess, 'run', lambda *a, **k: completed(stdout=trace))
    with pytest.raises(archive_base.ArchiveError, match='Invalid geometry.*1.png'):
        external.extract('1.png', str(tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('stage', ['list', 'trace', 'render', 'spawn', 'missing', 'empty'])
def test_external_failures_are_actionable_and_preserve_existing(external: Any,
                                                                monkeypatch: Any,
                                                                tmp_path: Path,
                                                                stage: str) -> None:
    target = tmp_path / '1.png'
    target.write_bytes(b'previous successful image')

    def run(cmd: list, **kwargs: Any) -> Any:
        if stage == 'spawn':
            raise FileNotFoundError('helper missing')
        rendering = '-o' in cmd
        if rendering:
            output = Path(cmd[cmd.index('-o') + 1])
            if stage == 'missing':
                output.unlink()
            elif stage == 'render':
                output.write_bytes(b'partial')
        failed = stage in ('list', 'trace') or (stage == 'render' and rendering)
        return completed(7 if failed else 0, stderr='broken PDF')

    monkeypatch.setattr(pdf_external.subprocess, 'run', run)
    with pytest.raises(archive_base.ArchiveError) as error:
        if stage == 'list':
            list(external.iter_contents())
        else:
            external.extract('1.png', str(tmp_path))
    assert 'failed' in str(error.value) or 'produced no image' in str(error.value)
    assert target.read_bytes() == b'previous successful image'
    assert list(tmp_path.iterdir()) == [target]


def test_external_valid_rotation_and_tiny_dpi(external: Any, monkeypatch: Any,
                                              tmp_path: Path) -> None:
    def run(cmd: list, **kwargs: Any) -> Any:
        if '-o' in cmd:
            assert cmd[cmd.index('-r') + 1] == '1'
            Path(cmd[cmd.index('-o') + 1]).write_bytes(image_bytes('PNG'))
            return completed()
        return completed(stdout='<fill_image transform="0 -99999 99999 0 0 0" width="30" height="20"/>')

    monkeypatch.setattr(pdf_external.subprocess, 'run', run)
    external.extract('1.png', str(tmp_path))
    with Image.open(tmp_path / '1.png') as image:
        assert image.format == 'PNG'
    assert [path.name for path in tmp_path.iterdir()] == ['1.png']


@pytest.mark.parametrize('mode', ['P', 'I;16'])
def test_rotated_png_modes_remain_decodable(native: Any, tmp_path: Path,
                                            mode: str) -> None:
    image = Image.new(mode, (30, 20))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    image.close()
    worker = worker_for_images(native, tmp_path, [buffer.getvalue()], [90])
    try:
        name, = worker.iter_contents()
        xref = worker._get_image_xref(0)
        with Image.open(io.BytesIO(worker.doc.extract_image(xref)['image'])) as embedded:
            expected = embedded.rotate(-90, expand=True)
        worker.extract_file(name, str(tmp_path))
        with Image.open(tmp_path / name) as result:
            assert result.tobytes() == expected.tobytes()
        pixbuf = image_tools.load_pixbuf(str(tmp_path / name))
        assert (pixbuf.get_width(), pixbuf.get_height()) == expected.size
        expected.close()
    finally:
        worker.doc.close()


def test_external_rejects_escaping_destination_before_spawn(external: Any,
                                                            monkeypatch: Any,
                                                            tmp_path: Path) -> None:
    def run(*args: Any, **kwargs: Any) -> None:
        pytest.fail('invalid destination must not run helper')

    monkeypatch.setattr(pdf_external.subprocess, 'run', run)
    with pytest.raises(archive_base.ArchiveError):
        external.extract('../1.png', str(tmp_path))
