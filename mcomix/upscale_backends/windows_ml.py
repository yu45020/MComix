"""windows_ml.py - Vendor GPU execution providers from the Windows ML catalog.

Windows ML (Windows 11 24H2 and later) installs and updates execution
providers from GPU vendors, such as NVIDIA TensorRT for RTX, which run
models much faster than DirectML. They need the
onnxruntime-windowsml and windowsml packages; without them, or when no
tested provider is installed, callers use DirectML.

TensorRT compiles a model into an engine, which takes seconds and can crash
the process with some driver and package combinations. The engine is
therefore built once per model and provider version in a child process,
saved as an ONNX "EP context" model next to a cache of compiled kernels,
and later loaded in a fraction of a second. A build that fails is
remembered, so it is not retried until the model or provider changes.

Nothing here downloads providers, except install(), which callers only use
on the user's explicit request.
"""

import hashlib
import multiprocessing
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

#: Catalog providers that are tested with MComix's models, in order of
#: preference. Others (e.g. OpenVINO, MIGraphX) are not used yet.
PROVIDERS = ('NvTensorRTRTXExecutionProvider',)
#: User-visible names of execution providers.
LABELS = {
    'NvTensorRTRTXExecutionProvider': 'NVIDIA TensorRT-RTX',
    'DmlExecutionProvider': 'DirectML',
}
#: Seconds allowed for building an engine in the child process.
BUILD_TIMEOUT = 300

# windowsml.EpReadyState values.
_READY, _NOT_READY, _NOT_PRESENT = 0, 1, 2
_CERTIFIED = 1

#: Catalog queries take about 0.5 s each, so their results, failures
#: included, are kept until refresh(): _catalog_states() result, and
#: prepared providers' libraries.
#: _lock guards them; slow catalog calls run outside it, and a result is only
#: kept if refresh() (which bumps _generation) did not run meanwhile.
_lock = threading.Lock()
_generation = 0
_states: Optional[List[Tuple[str, int]]] = None
_libraries: Dict[str, str] = {}
#: Providers registered with onnxruntime: name -> library. The app holds one
#: session at a time, so a registration has a single owner.
_registered: Dict[str, str] = {}


class Unavailable(Exception):
    """ Raised when no usable Windows ML provider is available. """


def label(provider: str) -> str:
    """ Returns the user-visible name of execution <provider>. """
    return LABELS.get(provider, provider)


def refresh() -> None:
    """ Forgets cached catalog results, e.g. after installing a provider.
    Registered libraries stay in use by their sessions. """
    global _states, _generation
    with _lock:
        _generation += 1
        _states = None
        _libraries.clear()


def _catalog_states() -> List[Tuple[str, int]]:
    """ Returns (name, ready state) of the certified tested providers in
    the catalog, or [] without Windows ML. """
    global _states
    with _lock:
        if _states is not None:
            return _states
        generation = _generation
    states = _query_catalog()
    with _lock:
        if generation == _generation:
            _states = states
    return states


def _query_catalog() -> List[Tuple[str, int]]:
    try:
        from windowsml import EpCatalog
    except Exception:  # Not installed, or no Windows ML on this system.
        return []
    states = {}
    try:
        with EpCatalog() as catalog:
            for ep in catalog.find_all_providers():
                if ep.name in PROVIDERS and int(ep.certification) == _CERTIFIED:
                    states[ep.name] = int(ep.ready_state)
    except Exception:
        return []
    return [(name, states[name]) for name in PROVIDERS if name in states]


def installed_provider() -> Optional[str]:
    """ Returns the preferred tested provider that is installed on this
    system (it may still need to be prepared, which needs no download). """
    return next((name for name, state in _catalog_states() if state != _NOT_PRESENT), None)


def installable_provider() -> Optional[str]:
    """ Returns a tested provider that Windows can download for this
    system's hardware, if none is installed yet. """
    states = _catalog_states()
    if any(state != _NOT_PRESENT for name, state in states):
        return None
    return next((name for name, state in states), None)


def install(name: str) -> None:
    """ Downloads and installs provider <name> (may take minutes). Only
    call on the user's request. """
    try:
        _ensure_ready(name, allow_download=True)
    finally:
        refresh()


def _ensure_ready(name: str, allow_download: bool = False) -> str:
    """ Prepares provider <name> for this process; returns its library. """
    with _lock:
        library = _libraries.get(name)
        generation = _generation
    if library is not None and not allow_download:
        return library
    from windowsml import EpCatalog
    with EpCatalog() as catalog:
        ep = next((ep for ep in catalog.find_all_providers() if ep.name == name), None)
        if ep is None:
            raise Unavailable('%s is not in the Windows ML catalog' % name)
        if int(ep.ready_state) == _NOT_PRESENT and not allow_download:
            raise Unavailable('%s is not installed' % name)
        ep.ensure_ready()
        library = str(ep.library_path)
    with _lock:
        if generation == _generation:
            _libraries[name] = library
    return library


def register(name: str) -> str:
    """ Registers provider <name> with onnxruntime unless it is registered;
    returns its library path. """
    library = _ensure_ready(name)
    if name not in _registered:
        import onnxruntime as ort
        ort.register_execution_provider_library(name, library)
        _registered[name] = library
    return _registered[name]


def unregister(name: str) -> None:
    """ Unregisters provider <name>, which frees its device memory pool (the
    CUDA context itself stays until the process exits), so release its
    sessions first. """
    if name not in _registered:
        return
    import onnxruntime as ort
    try:
        ort.unregister_execution_provider_library(name)
    except Exception:
        return  # Keep it: the next register() reuses it, the next release retries.
    del _registered[name]


def _devices(name: str) -> List[Any]:
    import onnxruntime as ort
    gpu = ort.OrtHardwareDeviceType.GPU
    return [d for d in ort.get_ep_devices() if d.ep_name == name and d.device.type == gpu]


def provider_options(name: str, nhwc: bool, multiple: int, max_side: int) -> Dict[str, str]:
    """ Returns the session options of provider <name> for inputs of at
    most <max_side> pixels per side that are multiples of <multiple>. """
    if name != 'NvTensorRTRTXExecutionProvider':
        return {}

    def shape(side: int) -> str:
        return 'input:' + ('1x%dx%dx3' if nhwc else '1x3x%dx%d') % (side, side)
    # TensorRT checks shapes symbolically and cannot prove that e.g. SAFMN's
    # pool + upsample branches keep the size for arbitrary sizes, so every
    # profile shape must be a multiple of the model's padding multiple.
    max_side = -(-max_side // multiple) * multiple
    return {'nv_profile_min_shapes': shape(multiple),
            'nv_profile_opt_shapes': shape(max_side),
            'nv_profile_max_shapes': shape(max_side)}


def _cache_key(model: str, name: str, library: str, device: Any,
               options: Dict[str, str]) -> str:
    digest = hashlib.sha256()
    with open(model, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    digest.update(repr((name, library, device.vendor_id, device.device_id,
                        sorted(options.items()))).encode())
    return digest.hexdigest()[:24]


def _session_options(devices: List[Any], options: Dict[str, str],
                     runtime_cache: str, context_file: Optional[str] = None) -> Any:
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    # Fail to load rather than run unsupported nodes on the CPU.
    so.add_session_config_entry('session.disable_cpu_ep_fallback', '1')
    if context_file is not None:
        so.add_session_config_entry('ep.context_enable', '1')
        so.add_session_config_entry('ep.context_file_path', context_file)
        so.add_session_config_entry('ep.context_embed_mode', '1')
    so.add_provider_for_devices(devices, dict(options, nv_runtime_cache_path=runtime_cache))
    return so


def build_engine(model: str, name: str, options: Dict[str, str], runtime_cache: str,
                 context_file: str, warmup: Tuple[int, ...], nhwc: bool,
                 partial: str) -> None:
    """ Child process: compiles <model> for provider <name> into the EP
    context model <context_file>, and runs it once on a <warmup>-shaped
    input to fill the kernel cache. Raises on failure (exit code 1). """
    import numpy as np
    import onnxruntime as ort
    register(name)
    try:
        ort.InferenceSession(model, sess_options=_session_options(
            _devices(name), options, runtime_cache, partial))
        session = ort.InferenceSession(partial, sess_options=_session_options(
            _devices(name), options, runtime_cache))
        feed = np.zeros(warmup, dtype=np.uint8 if nhwc else np.float32)
        session.run(None, {session.get_inputs()[0].name: feed})
        os.replace(partial, context_file)
    finally:
        _remove_partial(partial)


def _remove_partial(partial: str) -> None:
    """ Removes this attempt's temporary context, including after a kill. """
    try:
        os.remove(partial)
    except OSError:
        pass  # A later build uses a new path even if cleanup fails.


def abort_builds() -> None:
    """ Kills engine builds in progress, e.g. on exit; they are retried
    later, not remembered as failed. """
    _aborting.set()


class Aborted(Unavailable):
    """ Raised when an engine build is aborted. """


_aborting = threading.Event()


def _build_in_child(model: str, name: str, options: Dict[str, str], runtime_cache: str,
                    context_file: str, warmup: Tuple[int, ...], nhwc: bool) -> Optional[int]:
    """ Runs build_engine in a spawned process; returns its exit code
    (None on timeout). A crash only ends the child. """
    if _aborting.is_set():
        raise Aborted('engine build aborted')
    # ORT refuses to overwrite a context. Use an attempt-owned path so a
    # stale file, or another viewer building the same key, cannot block us.
    partial = context_file + '.' + uuid.uuid4().hex + '.partial'
    process = multiprocessing.get_context('spawn').Process(
        target=build_engine,
        args=(model, name, options, runtime_cache, context_file, warmup, nhwc, partial),
        name='upscale-engine-build', daemon=True)
    process.start()
    deadline = time.monotonic() + BUILD_TIMEOUT
    while process.is_alive() and time.monotonic() < deadline and not _aborting.is_set():
        process.join(0.2)
    try:
        if process.is_alive():
            process.kill()
            process.join()
            if _aborting.is_set():
                raise Aborted('engine build aborted')
            return None
        # Shutdown may arrive just as the child exits, before we can kill it.
        # Do not turn that cancelled request into a persistent failure marker.
        if _aborting.is_set():
            raise Aborted('engine build aborted')
        return process.exitcode
    finally:
        _remove_partial(partial)


def create_session(model: str, nhwc: bool, multiple: int, max_side: int,
                   cache_dir: str) -> Tuple[Any, str]:
    """ Returns (session, provider name) for <model> on the preferred
    installed Windows ML provider, building and caching its engine first if
    needed. The provider stays registered: release the session, then call
    unregister(provider name). Raises
    Unavailable if there is no usable provider. """
    name = installed_provider()
    if name is None:
        raise Unavailable('no tested Windows ML execution provider is installed')
    import onnxruntime as ort
    library = register(name)
    try:
        devices = _devices(name)
        if not devices:
            raise Unavailable('%s has no GPU device' % name)
        options = provider_options(name, nhwc, multiple, max_side)
        key = _cache_key(model, name, library, devices[0].device, options)
        os.makedirs(cache_dir, exist_ok=True)
        context_file = os.path.join(cache_dir, key + '_ctx.onnx')
        failed_file = os.path.join(cache_dir, key + '.failed')
        runtime_cache = os.path.join(cache_dir, key + '_kernels')
        os.makedirs(runtime_cache, exist_ok=True)
        if os.path.exists(failed_file):
            raise Unavailable('%s failed before with this model' % label(name))
        warmup = (1, max_side, max_side, 3) if nhwc else (1, 3, max_side, max_side)
        warmup = tuple(-(-n // multiple) * multiple if n > 3 else n for n in warmup)
        for attempt in range(2):
            if not os.path.exists(context_file):
                code = _build_in_child(model, name, options, runtime_cache,
                                       context_file, warmup, nhwc)
                if code != 0 or not os.path.exists(context_file):
                    mark_failed(failed_file, 'engine build %s' % (
                        'timed out' if code is None else 'exit code %s' % code))
                    raise Unavailable('%s could not build the model' % label(name))
            try:
                session = ort.InferenceSession(context_file, sess_options=_session_options(
                    devices, options, runtime_cache))
                return session, name
            except Exception:
                # E.g. after a driver update: rebuild once.
                try:
                    os.remove(context_file)
                except FileNotFoundError:
                    pass  # Another viewer removed it first.
                except OSError:
                    break
        mark_failed(failed_file, 'cannot load the built engine')
        raise Unavailable('%s could not load the model' % label(name))
    except BaseException:
        unregister(name)
        raise


def mark_failed(failed_file: str, reason: str) -> None:
    """ Remembers a build/load failure for this engine cache entry. """
    try:
        with open(failed_file, 'w', encoding='utf-8') as f:
            f.write(reason + '\n')
    except OSError:
        pass

# vim: expandtab:sw=4:ts=4
