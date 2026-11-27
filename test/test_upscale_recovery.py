"""Cache recovery crosses interpreter lifetimes; no GPU/provider is needed."""

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


REPO = Path(__file__).resolve().parent.parent
PROBE = Path(__file__).with_name('_upscale_recovery.py')


def probe(root: Path, mode: str) -> Any:
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    result = subprocess.run([sys.executable, '-B', str(PROBE), mode, str(root), str(REPO)],
                            cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode != 'seed':
        return json.loads(result.stdout.splitlines()[-1])


def assert_only_legacy_partial(root: Path) -> None:
    # A different viewer may still own this legacy partial; never delete it.
    partials = list((root / 'cache').glob('*.partial'))
    assert len(partials) == 1
    assert partials[0].name.endswith('_ctx.onnx.partial')
    assert (root / 'cache/unrelated.failed').read_text() == 'engine build exit code 1\n'
    assert (root / 'cache/unrelated_ctx.onnx').read_bytes() == b'unrelated engine'


def test_stale_partial_then_fresh_process_reuses_engine(tmp_path: Path) -> None:
    probe(tmp_path, 'seed')
    assert probe(tmp_path, 'retry') == {
        'context': True, 'marker': False, 'legacy': True, 'builds': 1}
    assert probe(tmp_path, 'retry') == {
        'context': True, 'marker': False, 'legacy': True, 'builds': 1}
    assert_only_legacy_partial(tmp_path)


def test_aborted_child_cleans_own_partial_then_fresh_process_retries(tmp_path: Path) -> None:
    probe(tmp_path, 'seed')
    (tmp_path / 'block-warmup').touch()
    assert probe(tmp_path, 'abort') == {
        'context': False, 'marker': False, 'legacy': True, 'builds': 1}
    assert_only_legacy_partial(tmp_path)
    (tmp_path / 'block-warmup').unlink()
    assert probe(tmp_path, 'retry')['builds'] == 2
    assert_only_legacy_partial(tmp_path)


def test_timed_out_child_cleans_own_partial_and_remembers_failure(tmp_path: Path) -> None:
    probe(tmp_path, 'seed')
    (tmp_path / 'block-warmup').touch()
    assert probe(tmp_path, 'timeout') == {
        'context': False, 'marker': True, 'legacy': True, 'builds': 1}
    assert_only_legacy_partial(tmp_path)
    (tmp_path / 'block-warmup').unlink()
    assert probe(tmp_path, 'unavailable')['builds'] == 1


def test_failed_build_leaves_marker_in_fresh_process(tmp_path: Path) -> None:
    probe(tmp_path, 'seed')
    (tmp_path / 'fail-warmup').touch()
    result = probe(tmp_path, 'unavailable')
    assert result['marker'] and result['builds'] == 1
    assert_only_legacy_partial(tmp_path)
