"""Startup failure paths must remain usable without an interactive console."""
from io import StringIO
from typing import Any
from unittest.mock import Mock

import pytest

from mcomix import run


class _Console(StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize('stream_name', ['stdin', 'stdout'])
def test_noninteractive_error_exits_without_prompt(
        monkeypatch: pytest.MonkeyPatch, stream_name: str) -> None:
    monkeypatch.setattr(run.sys, 'platform', 'win32')
    monkeypatch.setattr(run.sys, 'stdin', _Console())
    monkeypatch.setattr(run.sys, 'stdout', _Console())
    monkeypatch.setattr(run.sys, stream_name, None)
    prompt = Mock(side_effect=AssertionError('unexpected prompt'))
    monkeypatch.setattr('builtins.input', prompt)
    with pytest.raises(SystemExit) as result:
        run.wait_and_exit()
    assert result.value.code == 1
    prompt.assert_not_called()


def test_interactive_error_keeps_prompt_and_failure_status(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run.sys, 'platform', 'win32')
    monkeypatch.setattr(run.sys, 'stdin', _Console())
    monkeypatch.setattr(run.sys, 'stdout', _Console())
    prompt = Mock(side_effect=EOFError())
    monkeypatch.setattr('builtins.input', prompt)
    with pytest.raises(SystemExit) as result:
        run.wait_and_exit()
    assert result.value.code == 1
    prompt.assert_called_once_with('Press ENTER to continue...')


def test_missing_gtk_typelib_prints_the_version_message(
        monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    import gi

    def missing(namespace: str, version: str) -> None:
        # What gi.require_version raises when the typelib isn't installed.
        raise ValueError('Namespace %s not available for version %s' % (namespace, version))

    monkeypatch.setattr(gi, 'require_version', missing)
    exited = Mock(side_effect=SystemExit(1))
    monkeypatch.setattr(run, 'wait_and_exit', exited)
    with pytest.raises(SystemExit):
        run.setup_dependencies()
    exited.assert_called_once_with()
    assert 'required versions of GTK+ 3.0 and PyGObject' in caplog.text


def test_failed_console_detection_keeps_failure_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run.sys, 'platform', 'win32')
    monkeypatch.setattr(run.sys, 'stdin', _Console())
    monkeypatch.setattr(run.sys, 'stdout', _Console())
    monkeypatch.setattr(run.sys.stdin, 'isatty', Mock(side_effect=OSError('console lost')))
    prompt = Mock(side_effect=AssertionError('unexpected prompt'))
    monkeypatch.setattr('builtins.input', prompt)
    with pytest.raises(SystemExit) as result:
        run.wait_and_exit()
    assert result.value.code == 1
    prompt.assert_not_called()
