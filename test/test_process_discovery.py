"""Executable discovery policy; all helper fixtures are inert files."""

import importlib
import os
from pathlib import Path
import stat
import sys
from typing import Callable, Iterator, List, Tuple

import pytest

from mcomix import process


WINDOWS = sys.platform == 'win32'


def _helper(directory: Path, name: str = 'helper') -> str:
    directory.mkdir(parents=True, exist_ok=True)
    suffix = '.exe' if WINDOWS else ''
    filename = directory / (name + suffix)
    filename.write_bytes(b'inert fixture, never execute')
    filename.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return str(filename)


@pytest.fixture
def isolated_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cwd = tmp_path / 'cwd'
    cwd.mkdir()
    monkeypatch.chdir(str(cwd))
    monkeypatch.setenv('PATH', '')
    if WINDOWS:
        monkeypatch.setattr(process, '_exe_dir', str(tmp_path / 'application'))
    return tmp_path


def test_bare_name_ignores_cwd_and_explicit_workdir(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = isolated_search
    _helper(root / 'cwd')
    _helper(root / 'custom-workdir')
    expected = _helper(root / 'path')
    monkeypatch.setenv('PATH', str(root / 'path'))
    assert process.find_executable(('helper',)) == expected
    assert process.find_executable(
        ('helper',), workdir=str(root / 'custom-workdir')) == expected


@pytest.mark.parametrize('entry', ['', '.', 'relative'])
def test_relative_path_entries_do_not_restore_cwd_lookup(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch,
        entry: str) -> None:
    root = isolated_search
    _helper(root / 'cwd')
    _helper(root / 'cwd' / 'relative')
    monkeypatch.setenv('PATH', entry)
    assert process.find_executable(('helper',)) is None
    expected = _helper(root / 'path')
    monkeypatch.setenv('PATH', os.pathsep.join((entry, str(root / 'path'))))
    assert process.find_executable(('helper',)) == expected


def _record_probes(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Make every file probe fail and record it, so a search may name any directory
    (a drive root, a UNC share) without touching it."""
    probes: List[str] = []

    def isfile(path: str) -> bool:
        probes.append(os.path.normcase(path))
        return False

    monkeypatch.setattr(os.path, 'isfile', isfile)
    return probes


@pytest.mark.skipif(not WINDOWS, reason='Windows path qualification')
def test_drive_relative_path_entry_is_not_searched(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helpers = isolated_search / 'cwd' / 'helpers'
    expected = _helper(helpers)
    monkeypatch.setenv('PATH', os.path.splitdrive(str(helpers))[0] + 'helpers')
    assert process.find_executable(('helper',)) is None
    monkeypatch.setenv('PATH', str(helpers))
    assert process.find_executable(('helper',)) == expected


@pytest.mark.skipif(not WINDOWS, reason='Windows path qualification')
@pytest.mark.parametrize('entry, searched', [
    (r'\helpers', False), ('/helpers', False), (r'\\server\share\helpers', True)])
def test_only_drive_or_unc_qualified_root_paths_are_searched(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch,
        entry: str, searched: bool) -> None:
    monkeypatch.setenv('PATH', entry)
    probes = _record_probes(monkeypatch)
    assert process.find_executable(('helper',)) is None
    bundled = os.path.normcase(str(isolated_search / 'application' / 'helper.exe'))
    unc = os.path.normcase(r'\\server\share\helpers\helper.exe')
    assert probes == ([bundled, unc] if searched else [bundled])


@pytest.mark.skipif(not WINDOWS, reason='Bundled Windows helpers')
def test_bundled_helper_precedes_path_then_falls_back(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = isolated_search
    _helper(root / 'cwd')
    bundled = _helper(root / 'application')
    expected = _helper(root / 'path')
    monkeypatch.setenv('PATH', str(root / 'path'))
    assert process.find_executable(('helper',)) == bundled
    Path(bundled).unlink()
    assert process.find_executable(('helper',)) == expected


def test_missing_path_and_empty_candidates(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _helper(isolated_search / 'cwd')
    if WINDOWS:
        _helper(isolated_search / 'cwd', '')
    monkeypatch.delenv('PATH', raising=False)
    assert process.find_executable(('', 'helper')) is None
    assert process.find_executable(()) is None


@pytest.mark.skipif(not WINDOWS, reason='Windows extension and drive handling')
def test_windows_case_extension_and_absolute_search_directories(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _helper(isolated_search / 'path', 'Helper')
    monkeypatch.setenv('PATH', str(isolated_search / 'path'))
    result = process.find_executable(('HELPER.EXE',))
    assert result is not None
    assert os.path.samefile(result, expected)
    assert process.find_executable((expected[:-4],)) == expected


@pytest.fixture
def import_process() -> Iterator[Callable[..., None]]:
    """Reimport process under patched sys attributes, because it reads the bundled helpers'
    directory at import; the real one is restored afterwards."""
    with pytest.MonkeyPatch.context() as patch:
        def reimport(**sys_attributes: object) -> None:
            for name, value in sys_attributes.items():
                patch.setattr(sys, name, value, raising=False)
            importlib.reload(process)

        yield reimport
    importlib.reload(process)


@pytest.mark.skipif(not WINDOWS, reason='Bundled Windows helpers')
@pytest.mark.parametrize('argv0', ['./spoofed.py', r'C:\untrusted\MComix.exe'])
def test_source_bundled_helpers_do_not_follow_argv_or_cwd(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch,
        import_process: Callable[..., None], argv0: str) -> None:
    import_process(argv=[argv0], frozen=False)
    probes = _record_probes(monkeypatch)
    assert process.find_executable(('helper',)) is None
    source_root = os.path.dirname(os.path.dirname(os.path.realpath(process.__file__)))
    assert probes == [os.path.normcase(os.path.join(source_root, 'helper.exe'))]


@pytest.mark.skipif(not WINDOWS, reason='Bundled Windows helpers')
def test_frozen_bundled_helpers_follow_runtime_executable(
        isolated_search: Path, import_process: Callable[..., None]) -> None:
    runtime = _helper(isolated_search / 'frozen', 'MComix')
    _helper(isolated_search / 'cwd')
    bundled = _helper(isolated_search / 'frozen')
    import_process(argv=['spoofed.py'], frozen=True, executable=runtime)
    assert process.find_executable(('helper',)) == bundled


@pytest.mark.skipif(not WINDOWS, reason='Bundled Windows helpers')
def test_relative_frozen_executable_does_not_restore_cwd_lookup(
        isolated_search: Path, import_process: Callable[..., None]) -> None:
    _helper(isolated_search / 'cwd')
    import_process(frozen=True, executable='MComix.exe')
    assert process.find_executable(('helper',)) is None


def _record_spawns(monkeypatch: pytest.MonkeyPatch) -> List[Tuple[List[str], str]]:
    """Replace subprocess.Popen; record each command and its executable.
    Any other argument (handles, creation flags) fails the call."""
    calls = []

    class FakePopen:
        pid = 0

        def __init__(self, args: List[str], *, executable: str) -> None:
            calls.append((args, executable))

    monkeypatch.setattr(process.subprocess, 'Popen', FakePopen)
    return calls


@pytest.mark.skipif(not WINDOWS, reason='Windows process boundary')
def test_windows_spawn_never_falls_back_to_os_executable_search(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _helper(isolated_search / 'cwd')
    calls = _record_spawns(monkeypatch)
    with pytest.raises(FileNotFoundError) as exc:
        process.Win32Popen(['helper'])
    assert exc.value.filename == 'helper'
    assert calls == []


@pytest.mark.skipif(not WINDOWS, reason='Windows explicit process boundary')
@pytest.mark.parametrize('suffix, relative', [('.com', True), ('.scr', False), ('.EXE', True)])
def test_windows_spawn_preserves_explicit_executable_name(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch,
        suffix: str, relative: bool) -> None:
    executable = Path(_helper(isolated_search / 'cwd' / 'explicit directory'))
    if suffix != '.EXE':
        executable = executable.rename(executable.with_suffix(suffix))
    supplied = str(executable.with_suffix(suffix))
    if relative:
        supplied = os.path.relpath(supplied)
    calls = _record_spawns(monkeypatch)
    assert process.Win32Popen([supplied, 'argument']) == 0
    assert len(calls) == 1
    args, application = calls[0]
    assert args == [supplied, 'argument']
    assert os.path.isabs(application)
    assert os.path.samefile(application, executable)


def _script(directory: Path, filename: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(b'inert fixture, never execute')
    return str(path)


@pytest.mark.skipif(not WINDOWS, reason='Windows PATHEXT lookup')
@pytest.mark.parametrize('filename, requested', [
    ('tool.bat', 'tool.bat'), ('tool.cmd', 'TOOL.CMD'), ('tool.com', 'tool.com')])
def test_windows_bare_pathext_names_search_path_but_not_cwd(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch,
        filename: str, requested: str) -> None:
    root = isolated_search
    _script(root / 'cwd', filename)
    monkeypatch.setenv('PATHEXT', '.COM;.EXE;.BAT;.CMD')
    assert process.find_executable((requested,)) is None
    expected = _script(root / 'path', filename)
    monkeypatch.setenv('PATH', str(root / 'path'))
    result = process.find_executable((requested,))
    assert result is not None
    assert os.path.samefile(result, expected)


@pytest.mark.skipif(not WINDOWS, reason='Windows PATHEXT lookup')
def test_windows_pathext_policy_keeps_exe_suffix_for_other_names(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = isolated_search
    monkeypatch.setenv('PATH', str(root / 'path'))
    monkeypatch.setenv('PATHEXT', '.COM;.EXE;.BAT;.CMD')
    # Extensionless helpers keep the .exe-only rule, as before.
    _script(root / 'path', 'helper.bat')
    assert process.find_executable(('helper',)) is None
    # A dotted name whose suffix is not executable still means name.exe.
    versioned = _script(root / 'path', 'tool.v2.exe')
    assert process.find_executable(('tool.v2',)) == versioned
    # An extension removed from PATHEXT is no longer accepted as-is.
    _script(root / 'path', 'script.cmd')
    monkeypatch.setenv('PATHEXT', '.EXE;.BAT')
    assert process.find_executable(('script.cmd',)) is None
    # Without PATHEXT, the formats CreateProcessW can start are used.
    monkeypatch.delenv('PATHEXT')
    assert process.find_executable(('script.cmd',)) is not None


@pytest.mark.skipif(not WINDOWS, reason='Windows process boundary')
def test_windows_spawn_resolves_bare_batch_and_com_names_from_path(
        isolated_search: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = isolated_search
    batch = _script(root / 'path', 'open-with.bat')
    command = _script(root / 'path', 'open-with.com')
    _script(root / 'cwd', 'cwd-only.bat')
    monkeypatch.setenv('PATH', str(root / 'path'))
    monkeypatch.setenv('PATHEXT', '.COM;.EXE;.BAT;.CMD')
    calls = _record_spawns(monkeypatch)
    assert process.Win32Popen(['open-with.bat', 'page.png']) == 0
    assert process.Win32Popen(['open-with.com']) == 0
    assert [(args, os.path.normcase(exe)) for args, exe in calls] == [
        (['open-with.bat', 'page.png'], os.path.normcase(batch)),
        (['open-with.com'], os.path.normcase(command))]
    with pytest.raises(FileNotFoundError):
        process.Win32Popen(['cwd-only.bat'])
    assert len(calls) == 2
