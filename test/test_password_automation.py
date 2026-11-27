"""Known encrypted fixtures, scripted GTK entry and prompt isolation."""

from pathlib import Path
import subprocess
import sys
from typing import Any
from unittest import mock

from gi.repository import GLib, Gtk
import pytest

from . import get_testfile_path
from ._passwords import PasswordAnswers
from mcomix.archive import password
from mcomix.archive.zip import ZipArchive


def test_wrong_answer_then_explicit_retry(password_answers: PasswordAnswers) -> None:
    path = get_testfile_path('archives', 'Encrypted.zip')
    password_answers.add(path, 'wrong', 'password')
    for correct in (False, True):
        archive = ZipArchive(path)
        try:
            names = archive.list_contents()
            if correct:
                assert archive.read(names[0])
            else:
                with pytest.raises(RuntimeError):
                    archive.read(names[0])
        finally:
            archive.close()
    assert password_answers.calls == [path, path]


@pytest.mark.parametrize('answer', ['password', None])
def test_real_password_dialog_is_automatically_filled(
        password_dialog: Any, answer: Any,
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('socket.socketpair', mock.Mock(
        side_effect=AssertionError('GTK tests must not open signal sockets')))
    path = get_testfile_path('archives', 'Encrypted.zip')
    responded = []

    def respond() -> bool:
        if not password._active_dialogs:
            return True
        dialog = password._active_dialogs[0][1]
        entry = dialog.get_focus()
        assert isinstance(entry, Gtk.Entry)
        if answer is not None:
            entry.set_text(answer)
        responded.append(True)
        dialog.response(Gtk.ResponseType.OK if answer else Gtk.ResponseType.CANCEL)
        return False

    source = GLib.timeout_add(5, respond)
    archive = ZipArchive(path)
    try:
        names = archive.list_contents()
        if answer:
            assert archive.read(names[0])
        else:
            with pytest.raises(RuntimeError):
                archive.read(names[0])
        assert responded == [True]
    finally:
        if not responded:
            GLib.source_remove(source)
        archive.close()


@pytest.mark.parametrize('bypass', ['alias', 'reload'])
def test_unexpected_password_fails_without_human_input(
        tmp_path: Path, bypass: str) -> None:
    # Run the failure intentionally in a child so the outer test checks the
    # fixture's fail-fast contract, including an alias/reload of the real UI.
    probe = tmp_path / 'test_unexpected.py'
    probe.write_text('''
import importlib
from test.conftest import _REAL_PASSWORD_PROMPT
from mcomix.archive import archive_base, password

def test_unexpected():
    archive = archive_base.BaseArchive('unknown-protected.zip')
    archive._is_encrypted = True
    mode = %r
    if mode == 'alias':
        _REAL_PASSWORD_PROMPT(archive)
    else:
        importlib.reload(password).ask_for_password(archive)
''' % bypass, encoding='utf-8')
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'pytest', '-p', 'test.conftest',
         '-p', 'no:cacheprovider', '--rootdir=' + str(tmp_path),
         '--basetemp=' + str(tmp_path / 'child'),
         str(probe), '-q'], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding='utf-8', errors='replace', timeout=20)
    assert result.returncode == 1, result.stdout
    assert 'Unexpected archive password prompt' in result.stdout
    assert '1 passed' in result.stdout and '1 error' in result.stdout
