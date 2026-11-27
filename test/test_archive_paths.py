"""Archive member identity and extraction containment regressions."""

import io
import os
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, List, Tuple
from unittest import mock

import pytest

from mcomix.archive import archive_base, archive_recursive
from mcomix.archive import sevenzip_external, tar, zip as zip_archive
from mcomix import image_tools

from . import get_testfile_path
from ._archive_fakes import Extractors, FakeArchive, pump


MEMBERS = [
    ('../escape.jpg', b'traversal'),
    (r'..\back.jpg', b'backslash traversal'),
    ('/absolute.jpg', b'absolute'),
    ('C:/drive.jpg', b'drive'),
    ('C:relative.jpg', b'drive relative'),
    (r'\\server\share\unc.jpg', b'UNC'),
    ('page.jpg:stream.jpg', b'ADS'),
    ('a:b.jpg', b'colon'),
    ('a_b.jpg', b'underscore'),
    ('a_b (1).jpg', b'existing suffix'),
    ('.cover.jpg', b'hidden'),
    ('cover.jpg', b'visible'),
    ('...dots.jpg', b'dots'),
    ('folder/.page.jpg', b'nested hidden'),
    ('folder/../other.jpg', b'nested traversal'),
    ('a:b/one.jpg', b'colon directory'),
    ('a_b/one.jpg', b'underscore directory'),
    ('same.jpg', b'lowercase'),
    ('SAME.JPG', b'uppercase'),
    ('space.jpg ', b'trailing space'),
    ('space.jpg', b'plain space'),
    ('dot.jpg.', b'trailing dot'),
    ('dot.jpg', b'plain dot'),
    ('NUL.jpg', b'device'),
    ('_NUL.jpg', b'plain device'),
    ('parent.jpg', b'file with directory name'),
    ('parent.jpg/child.jpg', b'directory with file name'),
]


_NORMAL_MEMBERS = sorted(os.path.join('images', name) for name in (
    '01-JPG-Indexed.jpg', '02-JPG-RGB.jpg', '03-PNG-RGB.png', '04-PNG-Indexed.png',
    'Comment.txt'))


# Each fixture holds an "images" directory entry: listed first in the zip and
# tar, last in the RAR and 7z. 7z marks it with "Folder = +" alone in the tar,
# with "Attributes = D" alone in the 7z and with both in the zip.
@pytest.mark.skipif(not sevenzip_external.SevenZipArchive.is_available(),
                    reason='7z not available')
@pytest.mark.parametrize('fixture', [
    '01-ZIP-Normal.zip', '02-TAR-Normal.tar', '03-RAR-Normal.rar', '04-7Z-Normal.7z'])
def test_directory_entries_are_not_members(fixture: str) -> None:
    archive = sevenzip_external.SevenZipArchive(get_testfile_path('archives', fixture))
    try:
        assert sorted(archive.list_contents()) == _NORMAL_MEMBERS
    finally:
        archive.close()


def _assert_contained(root: Path, name: str) -> str:
    path = archive_base.BaseArchive._destination_path(str(root), name)
    assert os.path.commonpath((str(root.resolve()), path)) == str(root.resolve())
    return path


@pytest.mark.parametrize('format', ['zip', 'tar'])
def test_real_archive_names_remain_distinct_and_readable(tmp_path: Path, format: str) -> None:
    path = tmp_path / ('names.' + format)
    if format == 'zip':
        with zipfile.ZipFile(path, 'w') as output:
            output.writestr('folder/', b'')
            for original, data in MEMBERS:
                output.writestr(original, data)
        archive = zip_archive.ZipArchive(str(path))
        # ZipInfo converts Windows separators when writing the fixture.
        originals = archive.zip.namelist()[1:]
    else:
        with tarfile.open(path, 'w') as output:
            directory = tarfile.TarInfo('folder')
            directory.type = tarfile.DIRTYPE
            output.addfile(directory)
            for original, data in MEMBERS:
                info = tarfile.TarInfo(original)
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))
        archive = tar.TarArchive(str(path))
        originals = [name for name, _ in MEMBERS]
    try:
        names = archive.list_contents()
        assert names == archive.list_contents()
        assert len({os.path.normcase(n) for n in names}) == len(names)
        # A directory entry is never a member (no page, no reserved name).
        files = names
        assert len(files) == len(MEMBERS)
        assert '.cover.jpg' in files
        assert '...dots.jpg' in files
        assert os.path.join('folder', '.page.jpg') in files
        destination = tmp_path / 'extract'
        for name, original, (_, data) in reversed(list(zip(files, originals, MEMBERS))):
            assert archive._original_filename(name) == original
            assert archive.read(name) == data
            assert archive.get_entry_size(name) == len(data)
            archive.extract(name, str(destination))
            with open(_assert_contained(destination, name), 'rb') as output:
                assert output.read() == data
        assert list(archive.iter_extract(files, str(destination))) == files
        assert not (tmp_path / 'escape.jpg').exists()
        assert not (tmp_path / 'back.jpg').exists()
    finally:
        archive.close()


@pytest.mark.parametrize('name', [
    '../escape.jpg', r'..\escape.jpg', '/absolute.jpg', r'\rooted.jpg',
    'C:/absolute.jpg', 'C:relative.jpg', r'\\server\share\page.jpg',
    r'\\?\C:\page.jpg', 'page.jpg:stream', 'folder/../../escape.jpg',
    'bad\x00name.jpg', '', '.',
])
def test_destination_rejects_raw_unsafe_paths(tmp_path: Path, name: str) -> None:
    with pytest.raises(archive_base.ArchiveError):
        archive_base.BaseArchive._destination_path(str(tmp_path), name)


def _link_directory(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if os.name != 'nt':
            pytest.skip('Directory symlinks unavailable: %s' % error)
        # Junction creation needs no symbolic-link privilege on Windows.
        import _winapi
        try:
            _winapi.CreateJunction(str(target), str(path))
        except OSError as junction_error:
            pytest.skip('Directory links unavailable: %s' % junction_error)


@pytest.mark.parametrize('linked_parent', [False, True])
def test_materialize_rejection_preserves_outside_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked_parent: bool) -> None:
    extractors = Extractors(tmp_path, monkeypatch)
    archive = FakeArchive(lazy=True)
    extractor = extractors.open(archive, lazy=True)
    pump(lambda: extractor.get_files() is not None)
    assert extractor.is_lazy()
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'victim.jpg'
    sentinel.write_bytes(b'KEEP THIS FILE')
    if linked_parent:
        _link_directory(Path(extractor.get_directory(), 'link'), outside)
        name = 'link/victim.jpg'
    else:
        name = '../outside/victim.jpg'
    try:
        for method in (extractor.materialize, extractor.read, extractor.get_entry_size):
            with pytest.raises(archive_base.ArchiveError):
                method(name)
            assert sentinel.read_bytes() == b'KEEP THIS FILE'
        assert archive.calls == 0
    finally:
        extractors.close()


@pytest.mark.skipif(os.name != 'nt', reason='Win32 filesystem aliases')
@pytest.mark.parametrize('name', [
    'NUL.jpg', 'CON', 'CON .jpg', 'COM1.png', 'LPT9', 'COM\u00b9.jpg',
    'folder/AUX.jpg', 'trailing.jpg ', 'trailing.jpg.',
])
def test_destination_rejects_raw_windows_aliases(tmp_path: Path, name: str) -> None:
    with pytest.raises(archive_base.ArchiveError):
        archive_base.BaseArchive._destination_path(str(tmp_path), name)


@pytest.mark.parametrize('extension', ['.jpg', '.png'])
def test_extension_only_collisions_remain_pages(extension: str) -> None:
    archive = archive_base.NonUnicodeArchive('test.zip')
    originals = [extension, '../' + extension, './' + extension]
    names = [archive._unicode_filename(n) for n in originals]
    assert len(set(names)) == len(originals)
    assert all(image_tools.is_image_file(n) for n in names)
    assert [archive._original_filename(n) for n in names] == originals


@pytest.mark.parametrize('char', ['x', '\U0001f600'])
def test_long_collisions_keep_valid_components(tmp_path: Path, char: str) -> None:
    units = len(char.encode('utf-16-le')) // 2 if os.name == 'nt' else len(os.fsencode(char))
    prefix = char * (250 // units)
    originals = [prefix + ':.jpg', prefix + '_.jpg',
                 './' + prefix + '_.jpg', '../' + prefix + '_.jpg']
    members = [(name, bytes([index])) for index, name in enumerate(originals)]
    path = tmp_path / 'long.zip'
    with zipfile.ZipFile(path, 'w') as output:
        for name, data in members:
            output.writestr(name, data)
    archive = zip_archive.ZipArchive(str(path))
    try:
        names = archive.list_contents()
        assert names == archive.list_contents()
        assert len({os.path.normcase(n) for n in names}) == len(members)
        assert all(image_tools.is_image_file(n) for n in names)
        destination = tmp_path / 'extract'
        for name, (original, data) in zip(names, members):
            assert archive._original_filename(name) == original
            assert archive.read(name) == data
            archive.extract(name, str(destination))
            assert (destination / name).read_bytes() == data
    finally:
        archive.close()


@pytest.mark.skipif(os.name != 'nt', reason='Win32 filesystem aliases')
@pytest.mark.parametrize('original', ['CONxx.' + 'a' * 251, 'COM123.' + 'a' * 250])
def test_shortening_cannot_create_a_device_alias(tmp_path: Path, original: str) -> None:
    archive = archive_base.NonUnicodeArchive('test.zip')
    name = archive._unicode_filename(original)
    _assert_contained(tmp_path, name)
    assert len(name.encode('utf-16-le')) // 2 <= 255
    assert archive._original_filename(name) == original


def _sevenzip_listing(members: List[Tuple[str, bytes]]) -> str:
    return 'Solid = +\n----------\n' + ''.join(
        'Path = %s\nSize = %d\n\n' % (name, len(data))
        for name, data in members)


def test_sevenzip_mapping_batch_and_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Earlier unittest fixtures may leave tempfile pointing at a removed path.
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    members = MEMBERS
    archive = sevenzip_external.SevenZipArchive('test.7z')
    listing = subprocess.CompletedProcess([], 0, _sevenzip_listing(members))
    with mock.patch.object(archive, '_get_executable', return_value='7z'), \
            mock.patch.object(subprocess, 'run', return_value=listing):
        names = archive.list_contents()
        assert archive.list_contents() == names
    assert len(archive._contents) == len(members)
    assert len({os.path.normcase(n) for n in names}) == len(names)
    assert [archive.get_entry_size(n) for n in names] == [len(d) for _, d in members]

    def run(args: List[Any], stdout: Any = None, **kwargs: Any) -> subprocess.CompletedProcess:
        args = [os.fsdecode(a) for a in args]
        list_path = next(a[3:] for a in args if a.startswith('-i@'))
        with open(list_path, 'rb') as selected:
            original = selected.read().decode('utf-8').rstrip('\r\n')
        data = dict(members)[original]
        if stdout != subprocess.PIPE:
            stdout.write(data)
            data = None
        return subprocess.CompletedProcess(args, 0, data, b'')

    destination = tmp_path / 'extract'
    with mock.patch.object(archive, '_get_executable', return_value='7z'), \
            mock.patch.object(subprocess, 'run', side_effect=run):
        for name, (_, data) in zip(names, members):
            assert archive.read(name) == data
            archive.extract(name, str(destination))
            with open(_assert_contained(destination, name), 'rb') as output:
                assert output.read() == data

    proc = mock.Mock(stdout=io.BytesIO(b''.join(d for _, d in members)))
    with mock.patch.object(archive, '_get_executable', return_value='7z'), \
            mock.patch('mcomix.archive.sevenzip_external.process.popen', return_value=proc):
        assert list(archive.iter_extract(names, str(destination))) == names
    for name, (_, data) in zip(names, members):
        with open(_assert_contained(destination, name), 'rb') as output:
            assert output.read() == data
    assert not (tmp_path / 'escape.jpg').exists()


def test_recursive_nested_archive_checks_before_backend_extract(tmp_path: Path) -> None:
    archive = archive_base.BaseArchive('test.zip')
    archive.iter_contents = lambda: iter(['../escape.zip'])
    archive.extract = mock.Mock()
    wrapper = archive_recursive.RecursiveArchive(archive, str(tmp_path / 'extract'))
    with pytest.raises(archive_base.ArchiveError):
        wrapper.list_contents()
    archive.extract.assert_not_called()


def test_nested_archive_names_and_members_cannot_escape(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as output:
        output.writestr('../page.jpg', b'first')
        output.writestr('page.jpg', b'second')
    outer = tmp_path / 'outer.zip'
    with zipfile.ZipFile(outer, 'w') as output:
        output.writestr('../inner.zip', inner.getvalue())
    destination = tmp_path / 'extract'
    archive = archive_recursive.RecursiveArchive(zip_archive.ZipArchive(str(outer)), str(destination))
    try:
        names = archive.list_contents()
        assert len(set(names)) == 2
        for name, data in zip(names, (b'first', b'second')):
            assert archive.read(name) == data
            archive.extract(name, str(destination))
            with open(_assert_contained(destination, name), 'rb') as output:
                assert output.read() == data
        assert not (tmp_path / 'inner.zip').exists()
        assert not (destination / 'page.jpg').exists()
    finally:
        archive.close()


@pytest.mark.parametrize('batch,linked_root', [(False, False), (True, True)])
def test_nested_storage_does_not_collide_with_public_members(
        tmp_path: Path, batch: bool, linked_root: bool) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as output:
        output.writestr('page.jpg', b'nested')
    path = tmp_path / 'outer.zip'
    with zipfile.ZipFile(path, 'w') as output:
        output.writestr('inner.zip', inner.getvalue())
        output.writestr('sub-archives/0001.zip/page.jpg', b'outer')
    destination = tmp_path / 'extract'
    storage_parent = tmp_path
    if linked_root:
        storage_parent = tmp_path / 'target'
        storage_parent.mkdir()
        target = storage_parent / 'extract'
        target.mkdir()
        _link_directory(destination, target)
    archive = archive_recursive.RecursiveArchive(zip_archive.ZipArchive(str(path)), str(destination))
    storage = None
    try:
        names = archive.list_contents()
        expected = {os.path.join('inner.zip', 'page.jpg'): b'nested',
                    os.path.join('sub-archives', '0001.zip', 'page.jpg'): b'outer'}
        assert set(names) == set(expected)
        storage = Path(archive._sub_archive_dir.name)
        assert storage.parent == storage_parent
        assert storage != destination
        if batch:
            assert set(archive.iter_extract(names, str(destination))) == set(names)
        for name in names:
            assert archive.read(name) == expected[name]
            if not batch:
                archive.extract(name, str(destination))
            assert (destination / name).read_bytes() == expected[name]
        assert storage.exists()
    finally:
        archive.close()
    assert storage is not None and not storage.exists()
    archive.close()  # Repeated close must not attempt to remove another root.
