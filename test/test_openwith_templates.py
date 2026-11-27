"""Template environment expansion must never rewrite substituted file paths."""
import ntpath
import os
import posixpath
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gi.repository import Gtk

from mcomix import callback, openwith, process
from mcomix.preferences import prefs
from ._app_fakes import pump


@pytest.fixture
def window(tmp_path: Path) -> SimpleNamespace:
    page = str(tmp_path / '$LITERAL' / '%LITERAL%' / 'page $LITERAL %LITERAL%.png')
    archive = str(tmp_path / '$LITERAL' / '%LITERAL%' / 'book $LITERAL %LITERAL%.cbz')
    return SimpleNamespace(
        filehandler=SimpleNamespace(
            file_loaded=True, archive_type=1,
            get_base_filename=lambda: os.path.basename(archive),
            get_path_to_base=lambda: archive, materialize=Mock(return_value=[])),
        imagehandler=SimpleNamespace(
            get_path_to_page=lambda: page,
            get_page_filename=lambda: os.path.basename(page),
            get_current_page=lambda: 1),
        osd=SimpleNamespace(show=Mock()))


@pytest.mark.parametrize('expandvars', [ntpath.expandvars, posixpath.expandvars])
def test_substituted_paths_stay_literal(window: SimpleNamespace,
                                        monkeypatch: pytest.MonkeyPatch,
                                        expandvars) -> None:
    monkeypatch.setenv('LITERAL', 'CHANGED')
    monkeypatch.setenv('TEMPLATE', 'expanded template')
    monkeypatch.setattr(openwith.os.path, 'expandvars', expandvars)
    command = openwith.OpenWithCommand('test', 'tool "$TEMPLATE:%F"', '', False)
    expected = command._expand_variable('F', window,
                                        openwith.IMAGE_FILE_CONTEXT | openwith.ARCHIVE_CONTEXT)
    assert command.parse(window) == ['tool', 'expanded template:' + expected]
    assert 'CHANGED' not in expected


@pytest.mark.parametrize('archive', [False, True])
def test_each_variable_expands_for_its_context(window: SimpleNamespace, archive: bool) -> None:
    page = window.imagehandler.get_path_to_page()
    book = window.filehandler.get_path_to_base()
    folder = os.path.dirname(page)
    expected = {
        '/': os.sep, 'f': os.path.basename(page), 'd': os.path.basename(folder),
        'F': os.path.normpath(page), 'D': os.path.normpath(folder),
    }
    archive_values = {
        'a': os.path.basename(book), 'c': os.path.basename(os.path.dirname(book)),
        'A': book, 'C': os.path.dirname(book),
    }
    if archive:
        expected.update(archive_values)
        expected.update(b=archive_values['a'], s=archive_values['c'],
                        B=archive_values['A'], S=archive_values['C'])
    else:
        window.filehandler.archive_type = None
        expected.update(b=expected['d'], B=expected['D'],
                        s=os.path.basename(os.path.dirname(folder)),
                        S=os.path.dirname(folder))
    for identifier, value in expected.items():
        command = openwith.OpenWithCommand('test', '%' + identifier, '', False)
        assert command.parse(window) == [value], identifier
    rejected = 'x' if archive else 'acACx'
    for identifier in rejected:
        command = openwith.OpenWithCommand('test', '%' + identifier, '', False)
        with pytest.raises(openwith.OpenWithException):
            command.parse(window)


def test_file_variables_are_rejected_without_a_page(window: SimpleNamespace) -> None:
    window.imagehandler.get_current_page = lambda: 0
    for identifier in 'fdbsFDBS':
        command = openwith.OpenWithCommand('test', '%' + identifier, '', False)
        with pytest.raises(openwith.OpenWithException, match='only be used for files'):
            command.parse(window)
    command = openwith.OpenWithCommand('test', '%A', '', False)
    assert command.parse(window) == [window.filehandler.get_path_to_base()]


@pytest.mark.parametrize('archive', [False, True])
def test_execute_keeps_literal_workdir_and_restores_cwd(
        window: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        archive: bool) -> None:
    monkeypatch.setenv('LITERAL', 'CHANGED')
    directory = Path(window.imagehandler.get_path_to_page()).parent
    directory.mkdir(parents=True)
    window.filehandler.archive_type = 1 if archive else None
    launched = []

    def launch(args: list, **kwargs) -> None:
        launched.append((args, os.getcwd()))

    monkeypatch.setattr(process, 'popen', launch)
    monkeypatch.setattr(process, 'Win32Popen', launch)
    previous = os.getcwd()
    command = openwith.OpenWithCommand('test', 'tool "%F"', '%D', False)
    command.execute(window)
    assert os.getcwd() == previous
    assert launched == [(['tool', window.imagehandler.get_path_to_page()], str(directory))]
    window.osd.show.assert_not_called()
    assert window.filehandler.materialize.call_count == int(archive)


def test_substituted_quotes_are_not_reparsed(window: SimpleNamespace,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LITERAL', 'CHANGED')
    window.imagehandler.get_page_filename = lambda: 'a "quoted" $LITERAL.png'
    command = openwith.OpenWithCommand('test', 'tool prefix%fsuffix', '', False)
    assert command.parse(window) == ['tool', 'prefixa "quoted" $LITERAL.pngsuffix']


class EditorWindow(Gtk.Window):
    @callback.Callback
    def page_changed(self) -> None:
        pass


class EditorFiles:
    @callback.Callback
    def file_opened(self) -> None:
        pass

    @callback.Callback
    def file_closed(self) -> None:
        pass


def test_edit_followed_by_unchanged_cell_still_prompts_to_save(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(prefs, 'openwith commands', [('Viewer', 'viewer %F', '', False)])
    prompt = Mock()
    prompt.run.return_value = Gtk.ResponseType.NO
    monkeypatch.setattr(openwith.message_dialog, 'MessageDialog', lambda *args: prompt)
    window = EditorWindow()
    window.filehandler = EditorFiles()
    editor = openwith.OpenWithEditor(window, openwith.OpenWithManager())
    try:
        for column, text in ((0, 'Edited'), (1, 'viewer %F')):
            [renderer] = editor._command_tree.get_column(column).get_cells()
            renderer.emit('edited', '0', text)
            pump()
        editor._response(editor, Gtk.ResponseType.DELETE_EVENT)
        prompt.run.assert_called_once()
    finally:
        editor.destroy()
        window.destroy()
