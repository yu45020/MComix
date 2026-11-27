"""Atomic configuration saves keep the last complete state on failure."""

import datetime
import json
import os
from pathlib import Path
import pickle
import stat
from types import SimpleNamespace
from typing import Any

import pytest

from mcomix import atomic_file, constants, preferences


class _BrokenStream:
    def __init__(self, stream: Any, operation: str) -> None:
        self.stream = stream
        self.operation = operation

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)

    def __enter__(self) -> '_BrokenStream':
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def write(self, value: Any) -> int:
        if self.operation == 'write':
            self.stream.write(value[:max(1, len(value) // 2)])
            self.stream.flush()
            raise OSError('injected write failure')
        return self.stream.write(value)

    def close(self) -> None:
        self.stream.close()
        if self.operation == 'close':
            raise OSError('injected close failure')


BOOKMARK = ('日本語', 'café.cbz', 2, 10, None, datetime.datetime(2026, 1, 1))


@pytest.fixture
def saved(tmp_path: Path, monkeypatch: Any) -> Any:
    for name, filename in [('PREFERENCE_PATH', 'preferences.conf'),
                           ('BOOKMARK_PICKLE_PATH', 'bookmarks.pickle'),
                           ('KEYBINDINGS_CONF_PATH', 'keys.conf')]:
        monkeypatch.setattr(constants, name, str(tmp_path / filename))
    monkeypatch.setattr(preferences, 'prefs', {
        'language': '日本語 café', 'smart scroll percentage': 0.5,
        'number of pixels to scroll per mouse wheel event': 50})
    monkeypatch.setattr(preferences, '_file_readable', True)
    from mcomix import bookmark_backend, bookmark_menu_item, keybindings
    from mcomix import message_dialog
    errors = []
    monkeypatch.setattr(message_dialog.MessageDialog, 'run',
                        lambda dialog: errors.append(dialog) or dialog.destroy())
    store = type(bookmark_backend.BookmarksStore)()
    manager = object.__new__(keybindings._KeybindingManager)
    manager._action_to_bindings = {'next_page': [(32, 0)]}
    pages = iter(range(1, 100))

    def bookmark() -> Any:
        return bookmark_menu_item._Bookmark(
            None, None, *BOOKMARK[:2], next(pages), *BOOKMARK[3:])

    return SimpleNamespace(store=store, manager=manager, directory=tmp_path,
                           bookmark=bookmark, errors=errors)


def _save(saved: Any, writer: str) -> Path:
    if writer == 'preferences':
        preferences.write_preferences_file()
        return Path(constants.PREFERENCE_PATH)
    if writer == 'bookmarks':
        saved.store.add_bookmark(saved.bookmark())
        return Path(constants.BOOKMARK_PICKLE_PATH)
    saved.manager.save()
    return Path(constants.KEYBINDINGS_CONF_PATH)


@pytest.mark.parametrize('writer', ['preferences', 'bookmarks', 'keybindings'])
@pytest.mark.parametrize('failure', ['write', 'close', 'replace'])
def test_failed_real_save_preserves_previous_and_cleans_temp(
        saved: Any, monkeypatch: Any, writer: str, failure: str) -> None:
    path = _save(saved, writer)
    original = path.read_bytes()
    descriptors = []
    fdopen = os.fdopen

    def wrap(fd: int, mode: str) -> Any:
        stream = fdopen(fd, mode)
        descriptors.append(stream)
        return _BrokenStream(stream, failure)

    def fail(*args: Any) -> None:
        raise OSError('injected ' + failure + ' failure')

    monkeypatch.setattr(atomic_file.os, 'fdopen', wrap)
    if failure == 'replace':
        monkeypatch.setattr(atomic_file.os, 'replace', fail)
    if writer == 'bookmarks':
        # A bookmark action reports the failure instead of raising.
        _save(saved, writer)
        assert len(saved.errors) == 1
    else:
        with pytest.raises(OSError, match='injected ' + failure):
            _save(saved, writer)
    assert path.read_bytes() == original
    assert list(saved.directory.iterdir()) == [path]
    assert all(stream.closed for stream in descriptors)
    if writer == 'preferences':
        preferences.prefs['language'] = 'changed'
        preferences.read_preferences_file()
        assert preferences.prefs['language'] == '日本語 café'
        assert not Path(str(path) + '.broken').exists()
    elif writer == 'bookmarks':
        expected = [BOOKMARK[:2] + (1,) + BOOKMARK[3:]]
        assert [bookmark.pack() for bookmark in saved.store.get_bookmarks()] == expected
        with path.open('rb') as stream:
            assert pickle.load(stream) == constants.VERSION
            assert pickle.load(stream) == expected
    else:
        with path.open() as stream:
            assert json.load(stream) == {'next_page': ['space']}


@pytest.mark.parametrize('content', [b'{"language": ', b'5'])
def test_corrupt_preferences_set_aside_with_defaults(
        saved: Any, content: bytes) -> None:
    path = Path(constants.PREFERENCE_PATH)
    path.write_bytes(content)
    preferences.read_preferences_file()
    assert preferences.prefs['language'] == '日本語 café'
    assert not path.exists()
    assert Path(str(path) + '.broken').read_bytes() == content
    preferences.write_preferences_file()
    assert json.loads(path.read_text())['language'] == '日本語 café'


@pytest.mark.parametrize('failing', ['open', 'replace'])
def test_unreadable_preferences_kept_and_not_written_at_quit(
        saved: Any, monkeypatch: Any, failing: str) -> None:
    path = Path(constants.PREFERENCE_PATH)
    content = b'{"language": "kept"}' if failing == 'open' else b'{broken'
    path.write_bytes(content)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise PermissionError('locked')

    with monkeypatch.context() as patch:
        if failing == 'open':
            patch.setattr(preferences, 'open', fail, raising=False)
        else:
            patch.setattr(preferences.os, 'replace', fail)
        preferences.read_preferences_file()
    assert preferences.prefs['language'] == '日本語 café'
    preferences.write_preferences_file()
    assert path.read_bytes() == content
    assert list(saved.directory.iterdir()) == [path]


@pytest.mark.parametrize('key, value, loaded', [
    ('smart scroll percentage', -0.5, 0.5),
    ('smart scroll percentage', '0.3', 0.5),
    ('smart scroll percentage', 0.25, 0.25),
    ('number of pixels to scroll per mouse wheel event', -1, 50),
])
def test_out_of_range_scroll_values_load_as_default(
        saved: Any, key: str, value: Any, loaded: Any) -> None:
    Path(constants.PREFERENCE_PATH).write_text(json.dumps({key: value}))
    preferences.read_preferences_file()
    assert preferences.prefs[key] == loaded


def test_same_directory_closed_before_replace(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / 'café.conf'
    path.write_text('old')
    replace = os.replace
    observed = []

    def publish(source: str, destination: str) -> None:
        assert Path(source).parent == path.parent
        assert path.read_text() == 'old'
        # Windows refuses this rename if the writer still owns its handle.
        alternate = str(source) + '.closed'
        os.rename(source, alternate)
        os.rename(alternate, source)
        observed.append(Path(source).read_text())
        replace(source, destination)

    monkeypatch.setattr(atomic_file.os, 'replace', publish)
    with atomic_file.atomic_write(str(path)) as stream:
        stream.write('complete')
        assert path.read_text() == 'old'
    assert observed == ['complete']
    assert path.read_text() == 'complete'


def test_windows_readonly_target_preserved(tmp_path: Path) -> None:
    if os.name != 'nt':
        pytest.skip('Windows read-only replacement semantics')
    path = tmp_path / 'config'
    path.write_text('old')
    path.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            with atomic_file.atomic_write(str(path)) as stream:
                stream.write('new')
        assert path.read_text() == 'old'
        assert list(tmp_path.iterdir()) == [path]
    finally:
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
