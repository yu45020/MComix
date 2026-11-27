"""Delete removes the shown image and stays in its folder book."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock

from gi.repository import Gtk

from mcomix import main
from mcomix.preferences import prefs
from ._app_fakes import PNG, fake_app


class _ConfirmingDialog:
    def __init__(self, *args: Any) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        return Mock()

    def run(self) -> Gtk.ResponseType:
        return Gtk.ResponseType.OK


def test_delete_in_double_page_at_second_last_page_stays_in_the_book(
        tmp_path: Path, monkeypatch: Any) -> None:
    book = tmp_path / 'book'
    book.mkdir()
    for name in ['1.png', '2.png', '3.png', '4.png']:
        (book / name).write_bytes(PNG)
    # A sibling folder the double step would open.
    (tmp_path / 'next').mkdir()
    (tmp_path / 'next' / '1.png').write_bytes(PNG)
    app = fake_app(monkeypatch, tmp_path)
    monkeypatch.setitem(prefs, 'default double page', True)
    monkeypatch.setitem(prefs, 'double step in double page mode', True)
    monkeypatch.setattr(main.message_dialog, 'MessageDialog', _ConfirmingDialog)
    window = app.window
    record_page = window.set_page
    window.set_page = lambda page, at_bottom=False: record_page(page)
    window.imagehandler.get_virtual_double_page = lambda page=None: False
    window.next_book, window.previous_book = Mock(), Mock()
    window.flip_page = lambda *args, **kwargs: main.MainWindow.flip_page(window, *args, **kwargs)
    try:
        assert app.handler.open_file(str(book / '3.png'))
        assert window.imagehandler.get_current_page() == 3

        main.MainWindow.delete(window)
        window.next_book.assert_not_called()
        assert sorted(path.name for path in book.iterdir()) == ['1.png', '2.png', '4.png']
        assert app.handler.file_loaded
        assert window.imagehandler.get_real_path() == str(book / '4.png')
    finally:
        app.close()
