"""Resume choices and explicit startup positions with real ZIPs and GTK dialogs."""

from pathlib import Path
import pickle
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock
import zipfile

import pytest
from gi.repository import GLib, Gtk

from mcomix import constants, file_handler, main, message_dialog
from mcomix.preferences import prefs
from ._app_fakes import PNG, fake_app, folder_book, loaded, pump, write_archive


class _Window(Gtk.Window):
    _open_startup_file = main.MainWindow._open_startup_file


@pytest.fixture
def books(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setitem(prefs, 'previous quit was quit and save', False)
    monkeypatch.setitem(prefs, 'auto load last file', False)
    monkeypatch.setitem(prefs, 'path to last file', '')
    monkeypatch.setitem(prefs, 'page of last file', 1)
    window = _Window()
    app = fake_app(monkeypatch, tmp_path, window, recent=True)
    app.paths = [write_archive(tmp_path / (name + '.cbz'), ['%02d.png' % i for i in range(5)])
                 for name in 'ab']
    for path in app.paths:
        app.handler.last_read_page.set_page(path, 3)
    app.tmp, app.fileinfo = tmp_path, Path(constants.FILEINFO_PICKLE_PATH)
    yield app
    app.close()
    window.destroy()


def _answer(monkeypatch: pytest.MonkeyPatch, response: Any,
            remember: bool = False, on_response: Any = None) -> list:
    dialogs = []
    initialize = message_dialog.MessageDialog.__init__

    def create(dialog: Any, *args: Any, **kwargs: Any) -> None:
        initialize(dialog, *args, **kwargs)
        dialogs.append(dialog)

        def respond() -> bool:
            dialog.remember_checkbox.set_active(remember)
            if on_response is not None:
                on_response()
            else:
                dialog.response(response)
            return False

        GLib.idle_add(respond)
    monkeypatch.setattr(message_dialog.MessageDialog, '__init__', create)
    return dialogs


@pytest.mark.parametrize('response,expected', [(Gtk.ResponseType.YES, 3),
                                               (Gtk.ResponseType.NO, 1)])
def test_unset_choice_runs_real_dialog_and_remembers_both_responses(
        books: Any, monkeypatch: pytest.MonkeyPatch, response: Any, expected: int) -> None:
    dialogs = _answer(monkeypatch, response, remember=True)
    assert books.handler.open_file(books.paths[0])
    loaded(books.handler)
    assert len(dialogs) == 1
    assert books.window.pages[-1] == expected
    assert prefs['stored dialog choices']['resume-from-last-read-page'] == int(response)
    books.window.pages.clear()
    assert books.handler.open_file(books.paths[1])
    loaded(books.handler)
    assert len(dialogs) == 1
    assert books.window.pages == [expected]


@pytest.mark.parametrize('choice,page,expected', [
    (None, 1, 1), (Gtk.ResponseType.YES, 4, 4), (Gtk.ResponseType.NO, 99, 5),
    (Gtk.ResponseType.YES, -1, 5)])
def test_explicit_archive_page_overrides_history_and_stored_choice(
        books: Any, monkeypatch: pytest.MonkeyPatch, choice: Any,
        page: int, expected: int) -> None:
    if choice is not None:
        prefs['stored dialog choices']['resume-from-last-read-page'] = int(choice)
    monkeypatch.setattr(books.handler, '_ask_goto_last_read_page',
                        Mock(side_effect=AssertionError('explicit page must not ask')))
    assert books.handler.open_file(books.paths[0], start_page=page)
    loaded(books.handler)
    assert books.window.pages == [expected]


def test_negative_page_keeps_double_page_last_spread(
        books: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(prefs, 'default double page', True)
    assert books.handler.open_file(books.paths[0], start_page=-1)
    loaded(books.handler)
    assert books.window.pages == [4]


@pytest.mark.parametrize('entry,page,expected,found', [
    ('vol2/a:b.png', 0, 3, True),     # Stored name, not the name made safe on disk.
    ('vol2\\a:b.png', 1, 3, True),    # The entry wins over the page.
    ('vol2/A:B.png', 2, 2, False),    # Exact codepoints: falls back to the page.
    ('missing.png', 0, 1, False),     # No page either: page 1, still no prompt.
])
def test_start_entry_opens_the_archive_member_with_that_stored_name(
        books: Any, monkeypatch: pytest.MonkeyPatch, entry: str, page: int,
        expected: int, found: bool) -> None:
    path = write_archive(books.tmp / 'named.cbz', ['vol1/01.png', 'vol1/02.png',
                                                   'vol2/a:b.png', 'vol2/c.png'])
    books.handler.last_read_page.set_page(path, 4)
    monkeypatch.setattr(books.handler, '_ask_goto_last_read_page',
                        Mock(side_effect=AssertionError('explicit entry must not ask')))
    assert books.handler.open_file(path, start_page=page, start_entry=entry)
    loaded(books.handler)
    assert books.window.pages == [expected]
    assert books.window.show_notice.called != found


def test_start_entry_never_matches_a_member_of_a_nested_archive(books: Any) -> None:
    inner = write_archive(books.tmp / 'inner.zip', ['01.png'])
    path = books.tmp / 'outer.cbz'
    with zipfile.ZipFile(str(path), 'w') as archive:
        archive.write(inner, 'a.zip')  # Its pages sort before z.png.
        archive.writestr('z.png', PNG)
    assert books.handler.open_file(str(path), start_page=2, start_entry='01.png')
    loaded(books.handler)
    assert books.window.pages == [2]
    books.window.show_notice.assert_called_once()


@pytest.mark.parametrize('entry,found', [('b/1.png', True), ('B/1.png', False)])
def test_start_entry_opens_the_folder_image_at_that_path(
        books: Any, entry: str, found: bool) -> None:
    root = folder_book(books.tmp / 'Root')
    assert books.handler.open_file(root, start_page=1, start_entry=entry)
    loaded(books.handler)
    shown = 'b/1.png' if found else 'a/1.png'
    assert books.images.get_real_path() == str(books.tmp / 'Root' / shown)
    assert books.window.show_notice.called != found


@pytest.mark.parametrize('mode,history', [('auto', False), ('save', False), ('save', True)])
def test_startup_restores_saved_page_independent_of_history_policy(
        books: Any, monkeypatch: pytest.MonkeyPatch, mode: str, history: bool) -> None:
    books.handler.last_read_page.set_enabled(history)
    monkeypatch.setitem(prefs, 'store recent file info', history)
    prefs['stored dialog choices']['resume-from-last-read-page'] = int(Gtk.ResponseType.NO)
    if mode == 'save':
        books.fileinfo.write_bytes(pickle.dumps([books.paths[0], 3]))
        monkeypatch.setitem(prefs, 'previous quit was quit and save', True)
    else:
        monkeypatch.setitem(prefs, 'auto load last file', True)
        monkeypatch.setitem(prefs, 'path to last file', books.paths[0])
        monkeypatch.setitem(prefs, 'page of last file', 4)
    books.window._open_startup_file(None)
    loaded(books.handler)
    assert books.window.pages == [4]
    assert not prefs['previous quit was quit and save']


def test_save_and_quit_position_wins_over_auto_load(
        books: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    books.fileinfo.write_bytes(pickle.dumps([books.paths[0], 3]))
    monkeypatch.setitem(prefs, 'previous quit was quit and save', True)
    monkeypatch.setitem(prefs, 'auto load last file', True)
    monkeypatch.setitem(prefs, 'path to last file', books.paths[1])
    monkeypatch.setitem(prefs, 'page of last file', 2)
    books.window._open_startup_file(None)
    loaded(books.handler)
    assert books.handler._current_file == books.paths[0]
    assert books.window.pages == [4]


def test_explicit_startup_path_wins_and_retains_normal_resume_prompt(
        books: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    books.fileinfo.write_bytes(pickle.dumps([books.paths[0], 3]))
    monkeypatch.setitem(prefs, 'previous quit was quit and save', True)
    monkeypatch.setitem(prefs, 'auto load last file', True)
    monkeypatch.setitem(prefs, 'path to last file', books.paths[0])
    dialogs = _answer(monkeypatch, Gtk.ResponseType.NO)
    books.window._open_startup_file(books.paths[1])
    loaded(books.handler)
    assert books.handler._current_file == books.paths[1]
    assert len(dialogs) == 1 and books.window.pages[-1] == 1
    assert not prefs['previous quit was quit and save']


@pytest.mark.parametrize('saved', [b'broken pickle', pickle.dumps(['path', True])])
def test_corrupt_saved_position_falls_back_to_auto_load(
        books: Any, monkeypatch: pytest.MonkeyPatch, saved: bytes) -> None:
    books.fileinfo.write_bytes(saved)
    monkeypatch.setitem(prefs, 'previous quit was quit and save', True)
    monkeypatch.setitem(prefs, 'auto load last file', True)
    monkeypatch.setitem(prefs, 'path to last file', books.paths[1])
    monkeypatch.setitem(prefs, 'page of last file', 2)
    books.window._open_startup_file(None)
    loaded(books.handler)
    assert books.handler._current_file == books.paths[1]
    assert books.window.pages == [2]


@pytest.mark.parametrize('saved', [False, True])
def test_missing_startup_path_leaves_window_empty(
        books: Any, monkeypatch: pytest.MonkeyPatch, saved: bool) -> None:
    missing = str(books.tmp / 'missing.cbz')
    monkeypatch.setitem(prefs, 'auto load last file', True)
    monkeypatch.setitem(prefs, 'path to last file', missing)
    if saved:
        books.fileinfo.write_bytes(pickle.dumps([missing, 3]))
        monkeypatch.setitem(prefs, 'previous quit was quit and save', True)
    books.window._open_startup_file(None)
    assert not books.handler.file_loaded
    assert books.window.pages == []
    assert not prefs['previous quit was quit and save']


def test_local_image_restore_keeps_saved_file_identity(
        books: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = books.tmp / '01.png', books.tmp / '02.png'
    first.write_bytes(PNG)
    second.write_bytes(PNG)
    books.fileinfo.write_bytes(pickle.dumps([str(second), 1]))
    monkeypatch.setitem(prefs, 'previous quit was quit and save', True)
    books.window._open_startup_file(None)
    loaded(books.handler)
    assert books.handler.archive_type is None
    assert books.window.pages == [2]
    assert books.window.imagehandler.get_real_path() == str(second)


def test_shutdown_cancels_real_resume_without_late_position_write(
        books: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup = []
    _answer(monkeypatch, Gtk.ResponseType.CANCEL, remember=True,
            on_response=lambda: books.handler.begin_shutdown(
                lambda: (cleanup.append(True), books.handler.close_file())))
    books.window._open_startup_file(books.paths[0])
    pump(lambda: bool(cleanup))
    assert not books.handler.file_loaded
    assert books.handler._resume_dialog is None
    assert 'resume-from-last-read-page' not in prefs['stored dialog choices']
    assert not books.fileinfo.exists()
    books.window.uimanager.recent.add.assert_not_called()
    assert books.handler.open_file(books.paths[1]) is False


@pytest.mark.parametrize('page', [True, 0])
def test_invalid_auto_load_position_uses_first_page(
        books: Any, monkeypatch: pytest.MonkeyPatch, page: Any) -> None:
    monkeypatch.setitem(prefs, 'auto load last file', True)
    monkeypatch.setitem(prefs, 'path to last file', books.paths[0])
    monkeypatch.setitem(prefs, 'page of last file', page)
    books.window._open_startup_file(None)
    loaded(books.handler)
    assert books.window.pages == [1]


def test_invalid_auto_load_path_leaves_window_empty(
        books: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(prefs, 'auto load last file', True)
    monkeypatch.setitem(prefs, 'path to last file', None)
    books.window._open_startup_file(None)
    assert not books.handler.file_loaded
    assert books.window.pages == []


@pytest.mark.parametrize('mode', ['save', 'auto'])
def test_folder_book_reopens_as_its_folder_at_the_image_shown(
        books: Any, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    root = folder_book(books.tmp / 'Root')
    assert books.handler.open_file(root)
    loaded(books.handler)
    books.window.set_page(2)
    if mode == 'save':
        books.handler.write_fileinfo_file()
        monkeypatch.setitem(prefs, 'previous quit was quit and save', True)
    else:
        # What _terminate_program stores for the last-file auto-load.
        monkeypatch.setitem(prefs, 'auto load last file', True)
        for name, value in zip(['path to last file', 'page of last file', 'image of last file'],
                               books.handler.get_book_position()):
            monkeypatch.setitem(prefs, name, value)
    books.handler.close_file()
    # A page added before it moves the image's page number.
    (books.tmp / 'Root' / 'a' / '0.png').write_bytes(PNG)
    books.window._open_startup_file(None)
    loaded(books.handler)
    assert books.handler._base_path == root
    assert books.images.get_real_path() == str(books.tmp / 'Root' / 'b' / '1.png')


def test_failed_position_write_keeps_the_saved_position(
        books: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert books.handler.open_file(books.paths[0], start_page=2)
    loaded(books.handler)
    saved = pickle.dumps([books.paths[1], 3])
    books.fileinfo.write_bytes(saved)

    def torn(info: Any, stream: Any, protocol: int) -> None:
        stream.write(b'\x80')
        raise OSError('disk full')

    monkeypatch.setattr(file_handler, 'pickle', SimpleNamespace(
        dump=torn, HIGHEST_PROTOCOL=pickle.HIGHEST_PROTOCOL))
    with pytest.raises(OSError):
        books.handler.write_fileinfo_file()
    assert books.fileinfo.read_bytes() == saved


def test_closing_an_archive_without_images_adds_no_book(books: Any) -> None:
    empty = write_archive(books.tmp / 'empty.cbz', ['notes.txt'])
    assert books.handler.open_file(empty)
    loaded(books.handler)
    books.handler.close_file()
    assert books.library.get_book_by_path(empty) is None


@pytest.mark.parametrize('double,page,members', [
    (True, 4, ['%02d.png' % i for i in range(5)]),
    (False, 3, ['00.png', '01.png', '02.png', '__MACOSX/._00.png'])])
def test_finished_book_does_not_ask_to_resume(
        books: Any, double: bool, page: int, members: list) -> None:
    path = write_archive(books.tmp / 'finished.cbz', members)
    books.window.displayed_double = lambda: double
    assert books.handler.open_file(path, start_page=page)
    loaded(books.handler)
    books.handler.close_file()
    assert books.handler.last_read_page.get_page(path) is None
    book = books.library.get_book_by_path(path)  # Recent keeps it, with its finished badge.
    assert book.get_last_read_page() == book.pages
