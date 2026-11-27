"""Fresh-process Windows ML cache probe, with fake vendor operations only.

Executed by test_upscale_recovery.py. Top-level vendor setup is repeated by
spawned build children, while the application control flow remains real.
"""

import json
from pathlib import Path
import sys
import threading
import time
import types
from typing import Any, Dict, List, Optional

MODE, ROOT, REPO = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(REPO))
TRT = 'NvTensorRTRTXExecutionProvider'
REGISTERED = set()


class EpCatalog:
    def __enter__(self) -> 'EpCatalog':
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def find_all_providers(self) -> List[types.SimpleNamespace]:
        return [types.SimpleNamespace(name=TRT, ready_state=0, certification=1,
                                      library_path='fake-vendor.dll',
                                      ensure_ready=lambda: None)]


class SessionOptions:
    def __init__(self) -> None:
        self.config = {}

    def add_session_config_entry(self, key: str, value: str) -> None:
        self.config[key] = value

    def add_provider_for_devices(self, devices: List[Any], options: Dict[str, str]) -> None:
        pass


class InferenceSession:
    def __init__(self, path: str, sess_options: Optional[SessionOptions] = None,
                 **kwargs: Any) -> None:
        self.path = Path(path)
        config = sess_options.config if sess_options is not None else {}
        context = config.get('ep.context_file_path')
        if context is not None:
            with (ROOT / 'builds').open('a') as f:
                f.write(context + '\n')
            # The real ORT guard refuses to overwrite a context destination.
            with open(context, 'xb') as f:
                f.write(b'engine')

    def get_modelmeta(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(custom_metadata_map={
            'mcomix.scale': '2', 'mcomix.pad_multiple': '8',
            'mcomix.io': 'uint8-nhwc'})

    def get_inputs(self) -> List[types.SimpleNamespace]:
        return [types.SimpleNamespace(name='input')]

    def run(self, outputs: Any, feed: Dict[str, Any]) -> List[Any]:
        if self.path.suffix == '.partial' and (ROOT / 'fail-warmup').exists():
            raise RuntimeError('deterministic engine warmup failure')
        if self.path.suffix == '.partial' and (ROOT / 'block-warmup').exists():
            (ROOT / 'warming').touch()
            while True:
                time.sleep(0.05)
        return [feed['input'].repeat(2, axis=1).repeat(2, axis=2)]


GPU = types.SimpleNamespace(type='GPU', vendor_id=1, device_id=2)
sys.modules['windowsml'] = types.SimpleNamespace(EpCatalog=EpCatalog)
sys.modules['onnxruntime'] = types.SimpleNamespace(
    SessionOptions=SessionOptions, InferenceSession=InferenceSession,
    OrtHardwareDeviceType=types.SimpleNamespace(GPU='GPU'),
    get_ep_devices=lambda: [types.SimpleNamespace(ep_name=n, device=GPU) for n in REGISTERED],
    register_execution_provider_library=lambda name, library: REGISTERED.add(name),
    unregister_execution_provider_library=REGISTERED.remove)

# Install vendor doubles before importing the application adapter.
from mcomix.upscale_backends import windows_ml  # noqa: E402

assert Path(windows_ml.__file__).resolve() == REPO / 'mcomix/upscale_backends/windows_ml.py'


def main() -> None:
    model, cache = ROOT / 'model.onnx', ROOT / 'cache'
    model.write_bytes(b'model')
    cache.mkdir(exist_ok=True)
    options = windows_ml.provider_options(TRT, True, 8, 16)
    key = windows_ml._cache_key(str(model), TRT, 'fake-vendor.dll', GPU, options)
    context = cache / (key + '_ctx.onnx')
    marker = cache / (key + '.failed')
    legacy = Path(str(context) + '.partial')
    if MODE == 'seed':
        legacy.write_bytes(b'abandoned or concurrently owned legacy context')
        (cache / 'unrelated.failed').write_text('engine build exit code 1\n')
        (cache / 'unrelated_ctx.onnx').write_bytes(b'unrelated engine')
        return

    if MODE == 'timeout':
        # The build timeout passes on a fake clock once the child is warming,
        # however slowly it starts.
        windows_ml.time = types.SimpleNamespace(monotonic=lambda: time.monotonic() + (
            windows_ml.BUILD_TIMEOUT if (ROOT / 'warming').exists() else 0))
        try:
            windows_ml.create_session(str(model), True, 8, 16, str(cache))
        except windows_ml.Unavailable:
            assert (ROOT / 'warming').exists(), 'child never reached warmup'
            assert marker.read_text() == 'engine build timed out\n'
        else:
            raise AssertionError('expected a timed-out build')
    elif MODE == 'abort':
        def abort_when_warming() -> None:
            deadline = time.monotonic() + 15
            while not (ROOT / 'warming').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            windows_ml.abort_builds()
        aborter = threading.Thread(target=abort_when_warming)
        aborter.start()
        try:
            windows_ml.create_session(str(model), True, 8, 16, str(cache))
        except windows_ml.Aborted:
            assert (ROOT / 'warming').exists(), 'child never reached warmup'
            assert not marker.exists()
        else:
            raise AssertionError('expected an aborted build')
        finally:
            aborter.join()
    else:
        try:
            session, provider = windows_ml.create_session(str(model), True, 8, 16, str(cache))
        except windows_ml.Unavailable as error:
            assert MODE == 'unavailable', str(error)
            assert marker.exists()
        else:
            assert MODE == 'retry', 'expected a remembered failure'
            assert provider == TRT and session.path == context
            assert not marker.exists()
    print(json.dumps({'context': context.exists(), 'marker': marker.exists(),
                      'legacy': legacy.exists(), 'builds': len(
                          (ROOT / 'builds').read_text().splitlines())}))


if __name__ == '__main__':
    main()
