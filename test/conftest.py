"""Shared pytest fixtures: no human password prompts, and the optional ONNX exporter."""

import importlib
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

import gi
import pytest
from gi import _ossighelper

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk  # noqa: E402

from mcomix.archive import password  # noqa: E402
from ._passwords import PasswordAnswers  # noqa: E402


_REAL_PASSWORD_PROMPT = password.ask_for_password

# The third-party modules tools/export_onnx.py imports at module level.
_EXPORTER_IMPORTS = ('numpy', 'onnx', 'onnxruntime', 'torch', 'onnxconverter_common', 'PIL',
                     'spandrel')


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """tools/export_onnx.py; the test skips when an optional package it needs is absent."""
    for name in _EXPORTER_IMPORTS:
        pytest.importorskip(name)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    return importlib.import_module('export_onnx')


@pytest.fixture(autouse=True)
def gtk_signal_wakeup(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep real GTK loops, without SIGINT's sandbox-blocked loopback socket.
    monkeypatch.setattr(_ossighelper, 'wakeup_on_signal', nullcontext)


@pytest.fixture(autouse=True)
def password_answers(monkeypatch: pytest.MonkeyPatch) -> Iterator[PasswordAnswers]:
    answers = PasswordAnswers()
    monkeypatch.setattr(password, 'ask_for_password', answers)
    original_run = Gtk.Dialog.run

    def guarded_run(dialog: Gtk.Dialog, *args: Any, **kwargs: Any) -> int:
        if any(item[1] is dialog for item in password._active_dialogs) and \
                not answers.real_dialogs:
            # Catch aliases or a reload bypassing the module-level stub too.
            answers.unexpected.append('an unplanned real password dialog')
            return Gtk.ResponseType.CANCEL
        return original_run(dialog, *args, **kwargs)

    monkeypatch.setattr(Gtk.Dialog, 'run', guarded_run)
    yield answers
    answers.check()


@pytest.fixture
def password_dialog(password_answers: PasswordAnswers,
                    monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Opt into the real UI, with a watchdog around scripted GTK responses."""
    assert not password._active_dialogs
    password_answers.real_dialogs = True
    monkeypatch.setattr(password, 'ask_for_password', _REAL_PASSWORD_PROMPT)
    expired = []

    def timeout() -> bool:
        expired.append(True)
        password_answers.unexpected.append('scripted password dialog timed out')
        for owner, dialog in password._active_dialogs[:]:
            dialog.response(Gtk.ResponseType.CANCEL)
        return False

    source = GLib.timeout_add(5000, timeout)
    try:
        yield
    finally:
        if not expired:
            GLib.source_remove(source)
        password_answers.real_dialogs = False
        assert not password._active_dialogs
