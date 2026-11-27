"""Vendor registration and refresh snapshots, without GPU native calls."""

from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from typing import Any, Callable, Tuple
from unittest.mock import Mock
import weakref

import numpy as np

import pytest

from mcomix.upscale_backends import ModelFailure, onnx_sr, windows_ml as wml


TRT = wml.PROVIDERS[0]
#: The real child launcher; the vendor fixture replaces it.
build_in_child = wml._build_in_child


def start(call: Callable[[], Any]) -> Tuple[threading.Thread, list]:
    results = []

    def run() -> None:
        try:
            results.append(call())
        except BaseException as error:
            results.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results


def finish(job: Tuple[threading.Thread, list]) -> Any:
    thread, results = job
    thread.join(5)
    assert not thread.is_alive(), 'provider operation did not finish'
    assert len(results) == 1
    result = results.pop()
    if isinstance(result, BaseException):
        raise result
    return result


@pytest.fixture
def vendor(monkeypatch: Any, tmp_path: Path) -> Any:
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(wml, '_registered', {})
    monkeypatch.setattr(wml, '_libraries', {})
    monkeypatch.setattr(wml, '_states', None)
    monkeypatch.setattr(wml, '_generation', 0)
    monkeypatch.setattr(wml, '_aborting', threading.Event())
    state = SimpleNamespace(
        ep=SimpleNamespace(name=TRT, ready_state=0, certification=1,
                           library_path='vendor-v1.dll'),
        registered=set(), calls=[], sessions=weakref.WeakSet(),
        builds=0, premature=0, fail='', metadata={'mcomix.scale': '2'},
        prepare=lambda: None, unregister=lambda: None)

    def ensure_ready() -> None:
        state.prepare()
        state.ep.ready_state = 0
    state.ep.ensure_ready = ensure_ready

    class Catalog:
        def __enter__(self) -> 'Catalog':
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def find_all_providers(self) -> list:
            return [state.ep]

    class Options:
        def __init__(self) -> None:
            self.provider = False
            self.config = {}

        def add_session_config_entry(self, key: str, value: str) -> None:
            self.config[key] = value

        def add_provider_for_devices(self, devices: list, options: dict) -> None:
            self.provider = True
            assert devices and options

    class Session:
        def __init__(self, path: str, sess_options: Any = None,
                     providers: Any = None, **kwargs: Any) -> None:
            self.provider = TRT if sess_options and sess_options.provider else providers[0]
            if self.provider == TRT and state.fail == 'load':
                state.sessions.add(self)
                raise RuntimeError('engine load failed')
            self.path = path
            self.metadata = dict(state.metadata)
            state.sessions.add(self)

        def get_modelmeta(self) -> Any:
            return SimpleNamespace(custom_metadata_map=self.metadata)

        def get_inputs(self) -> list:
            if self.provider == TRT and state.fail == 'input':
                raise ValueError('input inspection failed')
            if self.provider == TRT and state.fail == 'cycle':
                cause = RuntimeError('native cause')
                error = ValueError('input inspection failed')
                cause.__cause__ = error
                raise error from cause
            return [SimpleNamespace(name='input')]

        def get_providers(self) -> list:
            return [self.provider]

        def run(self, outputs: Any, feed: Any) -> None:
            raise RuntimeError('device removed')

    def register(name: str, library: str) -> None:
        assert name not in state.registered, 'duplicate native registration'
        state.registered.add(name)
        state.calls.append(('register', name, library))

    def unregister(name: str) -> None:
        # Unregistering under a live session is the bug; windows_ml would
        # swallow an exception here, so the fixture checks it at teardown.
        if [s for s in state.sessions if s.provider == TRT]:
            state.premature += 1
        state.unregister()
        if state.fail == 'unregister':
            raise RuntimeError('provider still busy')
        state.registered.remove(name)
        state.calls.append(('unregister', name))

    def devices() -> list:
        gpu = SimpleNamespace(type='GPU', vendor_id=1, device_id=2)
        return [SimpleNamespace(ep_name=name, device=gpu) for name in state.registered]

    ort = SimpleNamespace(
        __version__='fake', OrtHardwareDeviceType=SimpleNamespace(GPU='GPU'),
        SessionOptions=Options, InferenceSession=Session,
        get_available_providers=lambda: ['DmlExecutionProvider', 'CPUExecutionProvider'],
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0), get_ep_devices=devices,
        register_execution_provider_library=register,
        unregister_execution_provider_library=unregister)
    monkeypatch.setitem(sys.modules, 'onnxruntime', ort)
    monkeypatch.setitem(sys.modules, 'windowsml', SimpleNamespace(EpCatalog=Catalog))
    model = tmp_path / 'model.onnx'
    model.write_bytes(b'model')
    state.model, state.cache = str(model), str(tmp_path / 'cache')

    def build(model: str, name: str, options: dict, runtime: str,
              context: str, warmup: tuple, nhwc: bool) -> Any:
        state.builds += 1
        if state.fail == 'abort':
            raise wml.Aborted('cancelled')
        if state.fail == 'build-error':
            raise OSError('cannot spawn')
        if state.fail == 'build':
            return 1
        Path(context).write_bytes(b'engine')
        return 0
    monkeypatch.setattr(wml, '_build_in_child', build)
    yield state
    assert not state.premature, 'provider unregistered while a session was alive'


def adapter(vendor: Any) -> onnx_sr.OnnxSrAdapter:
    return onnx_sr.OnnxSrAdapter(vendor.model, runtime='auto', cache_dir=vendor.cache)


def create(vendor: Any, cache: str = '') -> Tuple[Any, str]:
    return wml.create_session(vendor.model, False, 1, 16, cache or vendor.cache)


@pytest.mark.parametrize('failure', ['build', 'build-error', 'load'])
def test_failed_vendor_falls_back_without_registration(vendor: Any, failure: str) -> None:
    vendor.fail = failure
    value = adapter(vendor)
    value.load()
    assert value.provider_label == 'DirectML'
    assert not vendor.registered and not wml._registered
    assert [call[0] for call in vendor.calls] == ['register', 'unregister']
    value.close()
    value.close()
    assert len(vendor.calls) == 2


@pytest.mark.parametrize('failure', ['abort', 'input'])
def test_aborted_or_invalid_session_releases_before_raising(vendor: Any, failure: str) -> None:
    vendor.fail = failure
    value = adapter(vendor)
    error = wml.Aborted if failure == 'abort' else ValueError
    with pytest.raises(error):
        value.load()
    assert value._session is None and value._provider == ''
    assert not vendor.registered and not wml._registered
    assert not list(Path(vendor.cache).glob('*.failed'))


def test_successful_adapter_reload_close_and_run_failure(vendor: Any) -> None:
    value = adapter(vendor)
    value.load()
    assert value.can_fall_back and vendor.registered == {TRT}
    try:
        value._run(np.zeros((16, 16, 3), dtype='uint8'))
    except ModelFailure as error:
        assert 'device removed' in str(error)
        value.close()  # The failure traceback is still alive at release.
    else:
        pytest.fail('expected a device failure')
    assert not list(Path(vendor.cache).glob('*.failed'))
    value.close()
    value.load()
    value.load()  # Reload must release its previous session first.
    value.close()
    assert vendor.builds == 1
    assert [call[0] for call in vendor.calls] == ['register', 'unregister'] * 3


def test_failed_unregister_keeps_known_registration_for_retry(vendor: Any) -> None:
    session, owner = create(vendor)
    session = None
    vendor.fail = 'unregister'
    wml.unregister(owner)
    assert vendor.registered == {TRT} and TRT in wml._registered
    vendor.fail = ''
    session, owner = create(vendor)
    assert len(vendor.calls) == 1  # Reused, not registered twice.
    session = None
    wml.unregister(owner)
    assert not vendor.registered and not wml._registered and session is None


def test_refresh_discards_slow_catalog_result(vendor: Any, monkeypatch: Any) -> None:
    entered, release = threading.Event(), threading.Event()
    calls = []

    def query() -> list:
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            return [(TRT, 2)]
        return [(TRT, 0)]
    monkeypatch.setattr(wml, '_query_catalog', query)
    job = start(wml.installed_provider)
    try:
        assert entered.wait(5)
        wml.refresh()
        assert wml.installed_provider() == TRT
    finally:
        release.set()
    finish(job)
    assert wml.installed_provider() == TRT and len(calls) == 2


def test_refresh_during_prepare_does_not_publish_stale_library(vendor: Any) -> None:
    entered, release = threading.Event(), threading.Event()

    def prepare() -> None:
        entered.set()
        assert release.wait(5)
    vendor.prepare = prepare
    job = start(lambda: wml.register(TRT))
    try:
        assert entered.wait(5)
        wml.refresh()
        assert wml.installed_provider() == TRT  # No lock held across slow I/O.
    finally:
        release.set()
    assert finish(job) == 'vendor-v1.dll'
    wml.unregister(TRT)
    vendor.prepare = lambda: None
    vendor.ep.library_path = 'vendor-v2.dll'
    assert wml.register(TRT) == 'vendor-v2.dll'


def test_cyclic_load_error_releases_session_and_keeps_its_chain(vendor: Any) -> None:
    vendor.fail = 'cycle'
    with pytest.raises(ValueError) as raised:
        finish(start(adapter(vendor).load))
    assert raised.value.__cause__.__cause__ is raised.value
    assert not vendor.registered


def test_shutdown_racing_with_failed_child_exit_is_an_abort(
        vendor: Any, monkeypatch: Any) -> None:
    process = Mock(exitcode=1)
    process.is_alive.return_value = True

    def finish_and_abort(timeout: float) -> None:
        process.is_alive.return_value = False
        wml.abort_builds()

    process.join.side_effect = finish_and_abort
    monkeypatch.setattr(wml, '_build_in_child', build_in_child)
    monkeypatch.setattr(wml.multiprocessing, 'get_context',
                        Mock(return_value=Mock(Process=Mock(return_value=process))))
    with pytest.raises(wml.Aborted):
        create(vendor)
    assert not list(Path(vendor.cache).glob('*.failed'))


def test_shutdown_before_build_does_not_spawn(vendor: Any, monkeypatch: Any) -> None:
    wml.abort_builds()
    context = Mock()
    monkeypatch.setattr(wml, '_build_in_child', build_in_child)
    monkeypatch.setattr(wml.multiprocessing, 'get_context', context)
    with pytest.raises(wml.Aborted):
        create(vendor)
    context.assert_not_called()
    assert not list(Path(vendor.cache).glob('*.failed'))
