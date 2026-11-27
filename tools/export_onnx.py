"""Export a super-resolution checkpoint to ONNX for MComix (dev-only).

Usage:
    python tools/export_onnx.py tools/weights/SPANF_YC_2x.pth [--name NAME]

Writes <checkpoint>.fp32.onnx and <checkpoint>.fp16.onnx next to the input.
By default ('mcomix.io' metadata 'uint8-nhwc') the graphs take and return
uint8 NHWC RGB, and convert to and from the network's float NCHW in [0, 1]
on the device; the conversion stays in fp32 in the fp16 graph. With
--io float32, they take and return float32 NCHW RGB in [0, 1]
('float32-nchw', the contract of models without 'mcomix.io'). Batch, height
and width are dynamic; height and width must be multiples of the
'mcomix.pad_multiple' metadata value (the viewer pads and crops).

'mcomix.runtimes' metadata tells the viewer which file to use per runtime:
the fp16 graph for Windows ML providers (TensorRT-RTX), and for DirectML the
faster variant that runs fully there (measured here). Ship the files it
prints under "ship:" together (one file when fp16 suits both runtimes); the
viewer picks the sibling meant for its runtime.

SPAN automatically uses the legacy exporter (the default exporter crashes on
it); export fine-tuned SPAN with --precision fp32 --opset 18 (see
mcomix/upscale_models/README.md). Needs torch, spandrel, onnx, onnxscript,
onnxconverter-common and an onnxruntime with the DirectML provider.
SPAN-F-YC training and deployment checkpoints are detected locally; training
branches are fused and the legacy exporter is selected automatically. Their
activation is not stored in the checkpoint: --spanf-activation tanh (default,
the current super_resolution/src/models/spanf_yc.py) or silu.
"""

import argparse
import copy
import pathlib
import statistics
import time
from typing import List, Optional

import numpy as np
import onnx
import onnxruntime as ort
import torch
import torch.nn.functional as F
from onnxconverter_common import float16
from PIL import Image
from spandrel import ModelLoader

from spanf import ACTIVATIONS
from spanf_yc import is_spanf_yc, load_spanf_yc

SAFMN_PAD_MULTIPLE = 8  # SAFM pools by up to 2**3


def safm_forward_exportable(self, x):
    """SAFM.forward with fixed-ratio pooling instead of adaptive pooling.

    Identical to the original when height and width are multiples of
    2**(n_levels - 1), and exportable with dynamic sizes. """
    xc = x.chunk(self.n_levels, dim=1)
    out = []
    for i in range(self.n_levels):
        if i > 0:
            s = F.max_pool2d(xc[i], 2**i)
            s = self.mfr[i](s)
            s = F.interpolate(s, scale_factor=float(2**i), mode='nearest')
        else:
            s = self.mfr[i](xc[i])
        out.append(s)
    return self.act(self.aggr(torch.cat(out, dim=1))) * x


def add_uint8_io(model: onnx.ModelProto) -> onnx.ModelProto:
    """Returns <model> (float32 NCHW RGB in [0, 1] in and out) wrapped to take
    and return uint8 NHWC RGB. Output values are rounded as the viewer did on
    the CPU: floor(clip(y, 0, 1) * 255 + 0.5). Only Cast nodes touch uint8
    tensors: TensorRT supports uint8 nowhere else (e.g. not in Transpose). """
    model = copy.deepcopy(model)
    graph = model.graph
    (old_in,), (old_out,) = graph.input, graph.output
    core_in, core_out = 'mcomix_input_nchw', 'mcomix_output_nchw'
    renames = {old_in.name: core_in, old_out.name: core_out}
    for node in graph.node:
        node.input[:] = [renames.get(n, n) for n in node.input]
        node.output[:] = [renames.get(n, n) for n in node.output]

    def nhwc_info(info: onnx.ValueInfoProto, name: str) -> onnx.ValueInfoProto:
        n, _, h, w = (d.dim_param or d.dim_value for d in info.type.tensor_type.shape.dim)
        return onnx.helper.make_tensor_value_info(name, onnx.TensorProto.UINT8, [n, h, w, 3])

    make = onnx.helper.make_node
    graph.initializer.extend(
        onnx.numpy_helper.from_array(np.array(v, np.float32), 'mcomix_' + k)
        for k, v in (('0', 0), ('1', 1), ('255', 255), ('half', 0.5)))
    pre = [make('Cast', ['input'], ['mcomix_input_f'], to=onnx.TensorProto.FLOAT),
           make('Transpose', ['mcomix_input_f'], ['mcomix_input_nchw_f'], perm=[0, 3, 1, 2]),
           make('Div', ['mcomix_input_nchw_f', 'mcomix_255'], [core_in])]
    post = [make('Clip', [core_out, 'mcomix_0', 'mcomix_1'], ['mcomix_clipped']),
            make('Mul', ['mcomix_clipped', 'mcomix_255'], ['mcomix_scaled']),
            make('Add', ['mcomix_scaled', 'mcomix_half'], ['mcomix_offset']),
            make('Floor', ['mcomix_offset'], ['mcomix_rounded']),
            make('Transpose', ['mcomix_rounded'], ['mcomix_rounded_nhwc'], perm=[0, 2, 3, 1]),
            make('Cast', ['mcomix_rounded_nhwc'], ['output'], to=onnx.TensorProto.UINT8)]
    nodes = pre + list(graph.node) + post
    del graph.node[:]
    graph.node.extend(nodes)
    new_in, new_out = nhwc_info(old_in, 'input'), nhwc_info(old_out, 'output')
    del graph.input[:]
    del graph.output[:]
    graph.input.append(new_in)
    graph.output.append(new_out)
    return model


def dummy_input(sess: ort.InferenceSession, height: int, width: int) -> np.ndarray:
    """Returns a zero input of <height> x <width> for either I/O contract."""
    if sess.get_inputs()[0].type == 'tensor(uint8)':
        return np.zeros((1, height, width, 3), np.uint8)
    return np.zeros((1, 3, height, width), np.float32)


def float_runner(sess: ort.InferenceSession):
    """Returns a function running <sess> on float32 NCHW in [0, 1] (multiples
    of 1/255 for uint8 graphs) and returning float32 NCHW."""
    if sess.get_inputs()[0].type != 'tensor(uint8)':
        return lambda a: sess.run(None, {'input': a})[0]

    def run(a: np.ndarray) -> np.ndarray:
        x = np.ascontiguousarray(np.rint(a * 255).astype(np.uint8).transpose(0, 2, 3, 1))
        return sess.run(None, {'input': x})[0].transpose(0, 3, 1, 2).astype(np.float32) / 255
    return run


def pad_run_crop(run, x: np.ndarray, scale: int, multiple: int) -> np.ndarray:
    """Reflect-pad NCHW <x> to <multiple>, run, and crop (as the viewer does)."""
    h, w = x.shape[2:]
    ph, pw = -h % multiple, -w % multiple
    xp = np.pad(x, ((0, 0), (0, 0), (0, ph), (0, pw)), mode='reflect')
    return run(xp)[:, :, :h * scale, :w * scale]


def directml_ms(path: pathlib.Path) -> Optional[float]:
    """Returns the median time of a 512x512 tile with the graph on DirectML,
    or None if it does not run fully there, as the viewer requires: no node
    may fall back to the CPU (e.g. Gelu in fp32 from opset 20, or any node
    from opset 22), and DirectML must not fail."""
    options = ort.SessionOptions()
    options.log_severity_level = 3
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry('session.disable_cpu_ep_fallback', '1')
    try:
        sess = ort.InferenceSession(str(path), options, providers=['DmlExecutionProvider'])
        feed = {'input': dummy_input(sess, 512, 512)}
        sess.run(None, feed)
        times = []
        for _ in range(5):
            start = time.perf_counter()
            sess.run(None, feed)
            times.append((time.perf_counter() - start) * 1000)
        return statistics.median(times)
    except Exception:
        return None


def set_runtimes(path: pathlib.Path, runtimes: List[str]) -> None:
    """Records in <path>'s 'mcomix.runtimes' metadata which runtimes should
    use it: 'windowsml', 'directml' (comma-separated; empty for none). The
    viewer picks the sibling variant (.fp16/.fp32.onnx) meant for its runtime."""
    m = onnx.load(str(path))
    props = {p.key: p.value for p in m.metadata_props}
    props['mcomix.runtimes'] = ','.join(runtimes)
    del m.metadata_props[:]
    onnx.helper.set_model_props(m, props)
    onnx.save(m, str(path))


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('checkpoint', type=pathlib.Path)
    ap.add_argument('--name', help='display name stored in the model')
    ap.add_argument('--io', choices=('uint8', 'float32'), default='uint8',
                    help='graph input/output: uint8 NHWC (default) or float32 NCHW in [0, 1]')
    ap.add_argument('--precision', choices=('both', 'fp32'), default='both',
                    help='both variants, or FP32 only for precision-sensitive weights (default: %(default)s)')
    ap.add_argument('--opset', type=int,
                    help='ONNX opset (default: 18, or 17 for SPAN)')
    ap.add_argument('--img-range', type=float,
                    help='SPAN input normalization range (training default: 255)')
    ap.add_argument('--rgb-mean', type=float, nargs=3, metavar=('R', 'G', 'B'),
                    help='SPAN input normalization mean (training default: 0.4488 0.4371 0.4040)')
    ap.add_argument('--spanf-activation', choices=ACTIVATIONS, default='tanh',
                    help='SPAN-F-YC activation used in training: tanh (Tanh, tanh attention) '
                    'or silu (SiLU, sigmoid - 0.5 attention) (default: %(default)s)')
    args = ap.parse_args(argv)

    loader = ModelLoader()
    state = loader.load_state_dict_from_file(str(args.checkpoint))
    if is_spanf_yc(state):
        # SPAN-F training branches are absent from Spandrel's deployment
        # architecture. Recompute their fused weights before discarding them.
        model = load_spanf_yc(state, args.spanf_activation).to_deploy()
        print(f'SPAN-F-YC activation: {args.spanf_activation}')
        scale, multiple, architecture = model.scale, 1, 'SPAN-F-YC'
        if args.opset is None:
            args.opset = 18
    elif 'conv_near.weight' in state and 'block_5.c3_r.eval_conv.weight' in state:
        ap.error('SPAN-F checkpoints are no longer supported; use SPAN-F-YC')
    else:
        desc = loader.load_from_state_dict(state)
        model = desc.model.eval()
        scale = desc.scale
        multiple = max(desc.size_requirements.multiple_of, 1)
        architecture = desc.architecture.id
    if args.img_range is not None or args.rgb_mean is not None:
        if architecture != 'SPAN':
            ap.error('--img-range and --rgb-mean are supported only for SPAN')
        if not model.is_norm:
            ap.error('this SPAN checkpoint disables normalization (no_norm)')
        if args.img_range is not None:
            if not np.isfinite(args.img_range) or args.img_range <= 0:
                ap.error('--img-range must be finite and positive')
            model.img_range = args.img_range
        if args.rgb_mean is not None:
            if not np.isfinite(args.rgb_mean).all():
                ap.error('--rgb-mean values must be finite')
            model.mean = torch.tensor(args.rgb_mean).view(1, 3, 1, 1)
    if architecture == 'SPAN':
        print(f'SPAN normalization: enabled={model.is_norm}, '
              f'img_range={model.img_range}, rgb_mean={model.mean.flatten().tolist()}')
    print(f'{architecture} x{scale}, '
          f'{sum(p.numel() for p in model.parameters()) / 1e6:.2f}M params')

    # Re-parameterized convs (e.g. SPAN's Conv3XC) fuse their weights on
    # every eval forward, which cannot be traced: fuse once, then use the
    # fused conv directly (same result).
    for module in model.modules():
        if hasattr(module, 'update_params') and hasattr(module, 'eval_conv'):
            module.update_params()
            module.forward = module.eval_conv

    torch.manual_seed(0)
    x = torch.rand(1, 3, 64, 96)
    safm_type = None
    if architecture == 'SAFMN':
        multiple = max(multiple, SAFMN_PAD_MULTIPLE)
        safm_type = type(model.feats[0].safm)
        original_forward = safm_type.forward
        with torch.inference_mode():
            ref = model(x)
            safm_type.forward = safm_forward_exportable
            patched = model(x)
        print(f'patched vs original (64x96): max diff {(ref - patched).abs().max():.2e}')

    base = args.checkpoint.with_suffix('')
    fp32_path = base.with_name(base.name + '.fp32.onnx')
    fp16_path = base.with_name(base.name + '.fp16.onnx')
    if architecture in ('SPAN', 'SPAN-F-YC'):
        # The default exporter crashes on SPAN; SPAN-F-YC also uses the
        # legacy one. The output is <scale> times larger: its dimensions need
        # their own names, or runtimes plan buffers of the input's size for it.
        torch.onnx.export(model, (x,), str(fp32_path), dynamo=False,
                          opset_version=args.opset or 17,
                          input_names=['input'], output_names=['output'],
                          dynamic_axes={'input': {0: 'n', 2: 'h', 3: 'w'},
                                        'output': {0: 'n', 2: 'out_h', 3: 'out_w'}})
    else:
        dim = torch.export.Dim.AUTO
        # Opset 18 (the exporter's lowest) runs fully on DirectML and on
        # TensorRT-RTX. DirectML lacks fp32 Gelu (opset 20+) and the
        # opset-22 versions of common ops, and newer opsets gave no speed-up.
        torch.onnx.export(model, (x,), str(fp32_path), dynamo=True,
                          opset_version=args.opset or 18,
                          input_names=['input'], output_names=['output'],
                          dynamic_shapes={'x': {0: dim, 2: dim, 3: dim}})

    meta = {'mcomix.scale': str(scale),
            'mcomix.pad_multiple': str(multiple),
            'mcomix.name': args.name or base.name,
            'mcomix.io': 'uint8-nhwc' if args.io == 'uint8' else 'float32-nchw'}
    if architecture == 'SPAN':
        meta.update({'span.normalization': str(model.is_norm).lower(),
                     'span.img_range': str(model.img_range),
                     'span.rgb_mean': ','.join(str(v) for v in model.mean.flatten().tolist())})
    if architecture == 'SPAN-F-YC':
        meta['spanf.activation'] = args.spanf_activation

    def save(m: onnx.ModelProto, path: pathlib.Path) -> None:
        if args.io == 'uint8':
            m = add_uint8_io(m)
        onnx.helper.set_model_props(m, {**{p.key: p.value for p in m.metadata_props},
                                        **meta})
        onnx.checker.check_model(m)
        onnx.save(m, str(path))  # Weights are embedded.

    m32 = onnx.load(str(fp32_path), load_external_data=True)
    pathlib.Path(str(fp32_path) + '.data').unlink(missing_ok=True)
    save(m32, fp32_path)
    # The fp16 graph keeps float32 I/O, so the uint8 conversion stays fp32.
    paths = [fp32_path]
    if args.precision == 'both':
        m16 = float16.convert_float_to_float16(copy.deepcopy(m32), keep_io_types=True)
        save(m16, fp16_path)
        paths.append(fp16_path)
    elif fp16_path.exists():
        # An older sibling must not override this FP32-only export in the viewer.
        set_runtimes(fp16_path, [])
    for path in paths:
        print(f'wrote {path} ({path.stat().st_size // 1024} KiB)')

    # Which variant each runtime should use ('mcomix.runtimes' metadata, read
    # by the viewer): Windows ML providers (TensorRT-RTX) run fp16 fastest;
    # on DirectML, the faster variant that runs there at all (fp16 for SAFMN
    # and SPAN as of 2026-10; fp32 if a model's fp16 graph fails there).
    timings = {path: directml_ms(path) for path in paths}
    for path, ms in timings.items():
        result = f'{ms:.1f} ms per 512x512 tile' if ms else 'does not run fully'
        print(f'{path.name} on DirectML: {result}')
    usable = {path: ms for path, ms in timings.items() if ms is not None}
    if not usable:
        raise SystemExit('error: no variant runs fully on DirectML (e.g. --opset above 18); '
                         'the viewer needs one as its fallback')
    for_directml = min(usable, key=lambda path: usable[path])
    for_windowsml = fp16_path if args.precision == 'both' else fp32_path
    for path in paths:
        runtimes = []
        if path == for_windowsml:
            runtimes.append('windowsml')
        if path == for_directml:
            runtimes.append('directml')
        set_runtimes(path, runtimes)
    print(f'runtimes: DirectML uses {for_directml.name}, Windows ML uses {for_windowsml.name}')
    # A variant no runtime uses is only a reference: don't ship it.
    ship = sorted({for_windowsml.name, for_directml.name})
    print(f'ship: {", ".join(ship)} (copy to mcomix/upscale_models/ for a bundled model)')

    # Structured test image (noise is out of distribution for SR models).
    im = Image.effect_mandelbrot((403, 571), (-2.0, -1.2, 0.8, 1.2), 64)
    im = Image.merge('RGB', (im, im.rotate(180), im.transpose(Image.FLIP_LEFT_RIGHT)))
    xs = np.asarray(im, dtype=np.float32).transpose(2, 0, 1)[None] / 255
    xs8 = np.ascontiguousarray(xs[:, :, :568, :400])

    def psnr(a: np.ndarray, b: np.ndarray) -> str:
        mse = np.mean((np.clip(a, 0, 1) - np.clip(b, 0, 1)) ** 2)
        return f'{10 * np.log10(1 / max(float(mse), 1e-12)):.1f} dB'

    with torch.inference_mode():
        ref8 = model(torch.from_numpy(xs8)).numpy()  # patched == original here
        if safm_type is not None:
            safm_type.forward = original_forward
        ref = model(torch.from_numpy(xs)).numpy()
    checks = [(fp32_path, 'CPUExecutionProvider')]
    if args.precision == 'both':
        checks.append((fp16_path, 'DmlExecutionProvider' if timings[fp16_path]
                       else 'CPUExecutionProvider'))
    for path, provider in checks:
        sess = ort.InferenceSession(str(path), providers=[provider])
        run = float_runner(sess)
        levels = np.abs(run(xs8) - np.clip(ref8, 0, 1)).max() * 255
        print(f'{path.name} on {provider}: export check (400x568) '
              f'max diff {levels:.2f} levels, PSNR {psnr(run(xs8), ref8)}; '
              f'padding check (403x571) PSNR {psnr(pad_run_crop(run, xs, scale, multiple), ref)}')


if __name__ == '__main__':
    main()
