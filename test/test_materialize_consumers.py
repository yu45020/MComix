"""Failed archive materialization must not export a partial page."""

from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, List, Tuple
from unittest.mock import Mock

import gi
import pytest
from PIL import Image

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk  # noqa: E402

from mcomix import clipboard, image_tools, openwith, process, thumbbar  # noqa: E402
from ._archive_fakes import Extractors, FakeArchive, extractor_handler, pump


@pytest.fixture
def page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    extractors = Extractors(tmp_path, monkeypatch)

    def open_page(fails: bool, lazy: bool) -> Tuple[Any, str]:
        archive = FakeArchive('extract-error' if fails else 'success', lazy=lazy,
                              members=('page.png',))
        extractor = extractors.open(archive, lazy)
        pump(lambda: extractor.get_files() is not None)
        handler = extractor_handler(extractor)
        extractor.extract()
        pump(lambda: extractor.is_ready('page.png'))
        path = os.path.join(extractor.get_directory(), 'page.png')
        if fails and not lazy:
            assert extractor.get_error('page.png') == 'bad data'
            assert Path(path).read_bytes() == b'PARTIAL'
        handler._base_path = str(tmp_path / 'book.cbz')
        window = Mock()
        window.filehandler = handler
        window.imagehandler.get_path_to_page.return_value = path
        window.imagehandler.get_pixbufs.return_value = ['pixbuf fixture']
        window.displayed_double.return_value = False
        return window, path

    yield open_page
    extractors.close()


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list:
    calls = []

    def fake_spawn(args: Any, **kwargs: Any) -> None:
        calls.append((args, os.getcwd()))

    monkeypatch.setattr(process, 'Win32Popen', fake_spawn)
    monkeypatch.setattr(process, 'popen', fake_spawn)
    return calls


@pytest.mark.parametrize('fails,lazy,page_in_workdir', [
    (True, False, False), (True, False, True), (False, True, True)])
def test_open_with_exports_only_successfully_materialized_page(
        page: Any, launched: list, lazy: bool, fails: bool, page_in_workdir: bool) -> None:
    window, path = page(fails, lazy)
    command = openwith.OpenWithCommand(
        'viewer', 'viewer' if page_in_workdir else 'viewer "%F"',
        '%D' if page_in_workdir else '', False)
    original_cwd = os.getcwd()
    command.execute(window)
    assert os.getcwd() == original_cwd
    if fails:
        assert launched == []
        window.osd.show.assert_called_once()
    else:
        expected_args = ['viewer'] if page_in_workdir else ['viewer', path]
        expected_cwd = os.path.dirname(path) if page_in_workdir else original_cwd
        assert launched == [(expected_args, expected_cwd)]
        assert Path(path).read_bytes() == b'COMPLETE'
        window.osd.show.assert_not_called()


@pytest.mark.parametrize('command,written,expected', [
    # An escaped '%' before a page variable.
    ('viewer "%%%F"', True, lambda page, base: ['%' + page]),
    # Archive path and directory, and page names.
    ('viewer %B %S %A %C', False, lambda page, base: [
        base, os.path.dirname(base), base, os.path.dirname(base)]),
    ('viewer %f %d', False, lambda page, base: [
        'page.png', os.path.basename(os.path.dirname(page))]),
    # Literal text.
    ('viewer %%F %%D', False, lambda page, base: ['%F', '%D']),
])
def test_only_commands_naming_the_page_write_it(
        page: Any, launched: list, command: str, written: bool,
        expected: Callable[[str, str], List[str]]) -> None:
    window, path = page(False, True)
    window.imagehandler.get_page_filename.return_value = 'page.png'
    openwith.OpenWithCommand('viewer', command, '', False).execute(window)
    base = window.filehandler.get_path_to_base()
    assert [args for args, cwd in launched] == [['viewer'] + expected(path, base)]
    assert Path(path).exists() == written
    window.osd.show.assert_not_called()


def _animated_gif() -> Any:
    stream = BytesIO()
    Image.new('RGB', (4, 6), 'red').save(
        stream, format='GIF', save_all=True, duration=100, loop=0,
        append_images=[Image.new('RGB', (4, 6), 'blue')])
    animation = image_tools.load_pixbuf(stream.getvalue())
    assert image_tools.is_animation(animation)
    return animation


@pytest.mark.parametrize('fails,lazy,pages', [
    (True, False, 'static'), (False, True, 'static'),
    (False, True, 'animated'), (False, True, 'animated double')])
def test_clipboard_exports_only_successfully_materialized_page(
        page: Any, lazy: bool, fails: bool, pages: str) -> None:
    window, path = page(fails, lazy)
    static = image_tools.pil_to_pixbuf(Image.new('RGB', (4, 6), 'red'))
    if pages == 'static':
        window.imagehandler.get_pixbufs.return_value = [static]
    else:
        window.imagehandler.get_pixbufs.side_effect = lambda count: [_animated_gif()] * count
        window.displayed_double.return_value = pages == 'animated double'
    window.is_manga_mode = False
    # A private selection avoids overwriting the user's clipboard.
    selection = '_MCOMIX_PAGE_COPY_TEST'
    received = Gtk.Clipboard.get(Gdk.Atom.intern(selection, False))
    received.set_text('before', -1)
    clipboard.Clipboard(window, selection).copy_page()
    if fails:
        assert received.wait_for_text() == 'before'
        window.osd.show.assert_called_once()
    else:
        assert received.wait_for_text() == path
        image = received.wait_for_image()
        assert (image.get_width(), image.get_height()) == (
            (8, 6) if pages == 'animated double' else (4, 6))
        assert image_tools.pixbuf_to_pil(image).getpixel((0, 0))[:3] == (255, 0, 0)
        assert Path(path).read_bytes() == b'COMPLETE'
        window.osd.show.assert_not_called()


@pytest.mark.parametrize('fails,lazy', [(True, False), (False, True)])
def test_drag_exports_only_successfully_materialized_page(
        page: Any, lazy: bool, fails: bool) -> None:
    window, path = page(fails, lazy)
    consumer = SimpleNamespace(_window=window, _get_selected_row=lambda: 0)
    selection = Mock()
    thumbbar.ThumbnailSidebar._drag_data_get(consumer, None, None, selection)
    if fails:
        selection.set_uris.assert_not_called()
        window.osd.show.assert_called_once()
    else:
        selection.set_uris.assert_called_once_with([Path(path).as_uri()])
        assert Path(path).read_bytes() == b'COMPLETE'
        window.osd.show.assert_not_called()
