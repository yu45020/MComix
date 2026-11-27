"""Regression coverage for legacy unittest temporary-storage ownership."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

import pytest

from . import MComixTest
from mcomix import constants
from mcomix.preferences import prefs
from mcomix.thumbnail_tools import Thumbnailer


@pytest.mark.skipif(sys.platform != 'win32', reason='the paths are under %APPDATA% on Windows')
def test_app_storage_is_under_the_temporary_folder() -> None:
    temp_root = Path(tempfile.gettempdir()).resolve()
    for path in (constants.CONFIG_DIR, constants.DATA_DIR, constants.THUMBNAIL_PATH,
                 Thumbnailer().dst_dir):
        assert Path(path).resolve().is_relative_to(temp_root)


@pytest.mark.parametrize('outcome,environment_present', [
    ('test-failure', True), ('setup-failure', False)])
def test_storage_restored_on_all_exits(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str,
        environment_present: bool) -> None:
    for name in MComixTest._storage_environment:
        if environment_present:
            monkeypatch.setenv(name, str(tmp_path))
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    original_cwd = os.getcwd()
    original_env = {name: os.environ.get(name)
                    for name in MComixTest._storage_environment}
    original_prefs = dict(prefs)
    owned = []

    class StorageCase(MComixTest):
        def setUp(self) -> None:
            super().setUp()
            owned.append(Path(self.tmp_dir))
            os.chdir(self.tmp_dir)
            prefs['test-only-storage-marker'] = 'temporary'
            if outcome == 'setup-failure':
                raise RuntimeError('expected setup failure')

        def runTest(self) -> None:
            if outcome == 'test-failure':
                self.fail('expected test failure')

    result = unittest.TestResult()
    StorageCase().run(result)
    assert len(result.errors) == (outcome == 'setup-failure')
    assert len(result.failures) == (outcome == 'test-failure')
    assert os.getcwd() == original_cwd
    assert {name: os.environ.get(name) for name in original_env} == original_env
    assert tempfile.tempdir == str(tmp_path)
    assert dict(prefs) == original_prefs
    assert owned and not owned[0].exists()
    assert owned[0].parent == tmp_path
    with tempfile.TemporaryDirectory() as directory:
        assert Path(directory).is_dir()


def test_storage_restored_if_base_setup_fails(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    original_env = {name: os.environ.get(name)
                    for name in MComixTest._storage_environment}
    original_prefs = dict(prefs)
    original_mkdir = os.mkdir
    owned = []

    def fail_home(path: str, *args, **kwargs) -> None:
        if Path(path).name == 'home':
            owned.append(Path(path).parent)
            raise OSError('expected mkdir failure')
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, 'mkdir', fail_home)

    class StorageCase(MComixTest):
        def runTest(self) -> None:
            pass

    case = StorageCase()
    result = unittest.TestResult()
    case.run(result)
    assert len(result.errors) == 1
    assert {name: os.environ.get(name) for name in original_env} == original_env
    assert tempfile.tempdir == str(tmp_path)
    assert dict(prefs) == original_prefs
    assert owned and not owned[0].exists()


def test_process_discovery_owns_its_temporary_tree(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_process import ProcessTest
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    before = set(tmp_path.iterdir())
    original_cwd = os.getcwd()
    original_path = os.environ.get('PATH')
    result = unittest.TestResult()
    ProcessTest('test_find_executable').run(result)
    assert not result.errors and not result.failures
    assert set(tmp_path.iterdir()) == before
    assert os.getcwd() == original_cwd
    assert os.environ.get('PATH') == original_path
    assert tempfile.tempdir == str(tmp_path)
