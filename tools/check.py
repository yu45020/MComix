'''Run the project checks. Usage, from any shell, with the conda environment's Python:
`<prefix>/python.exe tools/check.py [--quick]`, or
`<prefix>/python.exe tools/check.py --tests <pytest args...>` for focused tests.

Without arguments it runs lint and the whole test suite; `--quick` runs lint only. The checks
use the Python that runs this script, so a worktree without its own `.venv` is checked with the
primary checkout's. Full output goes to `.tmp/check/<mode>.log`.
'''

import argparse
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, TextIO

ROOT = Path(__file__).resolve().parents[1]
# The suite takes about a minute; a GTK loop that never exits would otherwise hang the check.
PYTEST_LIMIT = 300

PYTHON = [sys.executable, '-m']
# Pyflakes codes only: the style codes fail on most files inherited from upstream, and
# reformatting those would make rebasing onto upstream harder.
FLAKE8 = PYTHON + ['flake8', '--select', 'F', '--exclude', 'mcomix/_vendor', 'mcomix', 'test']
PYTEST = PYTHON + ['pytest', '-q']


def clean_env() -> Dict[str, str]:
    # MSYS2's GI_TYPELIB_PATH and PATH entries make PyGObject load the wrong typelibs and
    # DLLs (AGENTS.md, Environment), so the checks see only the conda prefix, Windows and the
    # archive tools' folders, which hold no MSYS2 DLLs and let the real-tool tests run.
    prefix = Path(sys.executable).parent
    system_root = os.environ['SystemRoot']
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    env.pop('GI_TYPELIB_PATH', None)
    tools = [str(path) for path in (Path(r'C:\Program Files\7-Zip-Zstandard'),
                                    Path(r'C:\Program Files\7-Zip')) if path.is_dir()]
    env['PATH'] = os.pathsep.join([
        str(prefix), str(prefix / 'Library' / 'bin'), str(prefix / 'Scripts'),
        os.path.join(system_root, 'system32'), system_root, *tools])
    return env


def pytest_basetemp(mode: str) -> Path:
    # Outside the repo (tests never write there) and per checkout: checkouts sharing
    # %TEMP%\pytest-of-<user> deleted each other's folders mid-run. pytest wipes it at start,
    # which is safe because runs in one checkout are serialized (docs/agents/validation.md).
    digest = hashlib.sha1(str(ROOT).lower().encode('utf-8')).hexdigest()[:8]
    return Path(tempfile.gettempdir()) / f'mcomix-check-{digest}' / mode


def report(message: str, log: TextIO) -> None:
    print(message, flush=True)
    print(message, file=log, flush=True)


def run(name: str, cmd: List[str], log: TextIO, limit: Optional[float] = None) -> bool:
    with subprocess.Popen(
            cmd, cwd=ROOT, env=clean_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace') as process:
        assert process.stdout is not None
        timed_out = threading.Event()

        def kill_tree() -> None:
            timed_out.set()
            # Killing only pytest would leave its spawned children holding the pipe.
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)],
                           capture_output=True)

        # A timer, because the read loop below blocks while a hung child prints nothing.
        timer = threading.Timer(limit, kill_tree) if limit is not None else None
        if timer is not None:
            timer.start()
        try:
            for line in process.stdout:
                log.write(line)
            exit_code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            if timer is not None:
                timer.cancel()
        if timed_out.is_set():
            print(f'TIMEOUT: {name} after {limit} s', file=log, flush=True)
            return False
        return exit_code == 0


def check(phases: List[str], pytest_args: List[str], log: TextIO) -> int:
    failed = []
    for index, name in enumerate(phases):
        print(f'\n=== {name} ===', file=log, flush=True)
        start = log.tell()
        try:
            if name == 'flake8':
                passed = run(name, FLAKE8, log)
            else:
                passed = run(name, PYTEST + pytest_args, log, PYTEST_LIMIT)
        except KeyboardInterrupt:
            report(f'\nINTERRUPTED: {name}; validation is incomplete.', log)
            if phases[index + 1:]:
                report('NOT RUN: ' + ', '.join(phases[index + 1:]), log)
            return 130
        log.seek(start)
        tail = list(deque(log, maxlen=60))
        log.seek(0, os.SEEK_END)
        if passed:
            # pytest's summary line, which is the evidence of what ran.
            summary = next((line.strip() for line in reversed(tail) if line.strip()), '')
            report(f'PASS: {name}' + (f' ({summary})' if summary else ''), log)
        else:
            failed.append(name)
            report(f'FAIL: {name}', log)
            print('Failure output (last 60 lines of this phase):', flush=True)
            print(''.join(tail), end='', flush=True)
    report('\n' + ('FAILED: ' + ', '.join(failed) if failed else 'All checks passed.'), log)
    return 1 if failed else 0


def main() -> int:
    # Piped stdout defaults to the ANSI code page on Windows, which garbles non-ASCII test
    # names in failure excerpts.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Run lint only')
    parser.add_argument(
        '--tests', nargs=argparse.REMAINDER,
        help='Run only pytest with the arguments that follow; must come last')
    args = parser.parse_args()
    if args.tests is not None:
        if args.quick:
            parser.error("--tests can't be combined with --quick")
        if not args.tests:
            parser.error('--tests needs at least one pytest argument')
        mode, phases, pytest_args = 'tests', ['pytest'], args.tests
    elif args.quick:
        mode, phases, pytest_args = 'quick', ['flake8'], []
    else:
        mode, phases, pytest_args = 'full', ['flake8', 'pytest'], ['test']
    path = ROOT / '.tmp' / 'check' / f'{mode}.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w+', encoding='utf-8') as log:
        report(f'Check log: {path}', log)
        if 'pytest' in phases:
            basetemp = pytest_basetemp(mode)
            # pytest creates only the last component of --basetemp.
            basetemp.parent.mkdir(parents=True, exist_ok=True)
            print(f'pytest basetemp: {basetemp}', file=log, flush=True)
            pytest_args = ['--basetemp', str(basetemp)] + pytest_args
        return check(phases, pytest_args, log)


if __name__ == '__main__':
    sys.exit(main())
