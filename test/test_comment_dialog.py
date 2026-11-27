"""The Comments dialog shows each comment of the open book once."""

from pathlib import Path

import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk  # noqa: E402

from mcomix import comment_dialog  # noqa: E402
from ._app_fakes import fake_app, loaded, pump, write_archive  # noqa: E402


def test_open_dialog_shows_each_comment_of_an_opened_archive_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = write_archive(tmp_path / 'book.cbz', ['page.png', 'a.txt', 'b.txt'])
    app = fake_app(monkeypatch, tmp_path, window=Gtk.Window())
    dialog = comment_dialog._CommentsDialog(app.window)
    try:
        assert app.handler.open_file(archive)
        loaded(app.handler, archive)
        pump()
        notebook = dialog._notebook
        tabs = [notebook.get_tab_label_text(notebook.get_nth_page(index))
                for index in range(notebook.get_n_pages())]
        assert sorted(tabs) == ['a.txt', 'b.txt']
    finally:
        dialog.destroy()
        app.close()
        app.window.destroy()
