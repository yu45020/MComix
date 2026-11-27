"""Export seeded random SPAN-F (baseline) or SPAN-F-YC for FP16 ONNX benchmarks.

Legacy exporter at opset 18 -> FP16 -> uint8-NHWC I/O. Defaults match current
super_resolution SPAN-F: Tanh, 32 features, x2, nearest-initialized
conv_near. This is a runtime experiment, not a checkpoint/quality evaluation.

Examples (in an environment with torch, onnx and onnxconverter-common):
    python tools/export_spanf_color.py
    python tools/export_spanf_color.py --variant yc
"""

import argparse
from collections import Counter
import hashlib
import json
import pathlib
import platform

import numpy as np
import onnx
import onnxruntime as ort
import torch
from onnxconverter_common import float16

from export_onnx import add_uint8_io
from spanf import ACTIVATIONS, SPANF
from spanf_yc import SPANFYC

MODELS = {'baseline': SPANF, 'yc': SPANFYC}


def reference_cases() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(0)
    gray = rng.integers(0, 256, (1, 47, 61, 1), dtype=np.uint8).repeat(3, axis=3)
    near = gray.copy()
    near[:, :, :, 0] = np.minimum(near[:, :, :, 0].astype(np.int16) + 1, 255)
    mixed = rng.integers(0, 256, (1, 97, 129, 1), dtype=np.uint8).repeat(3, axis=3)
    mixed[:, 2:10, 2:10] = [255, 0, 0]
    return {
        'color': rng.integers(0, 256, (1, 47, 61, 3), dtype=np.uint8),
        'gray': gray,
        'odd_gray': rng.integers(0, 256, (1, 17, 23, 1), dtype=np.uint8).repeat(3, axis=3),
        'black': np.zeros((1, 1, 1, 3), dtype=np.uint8),
        'white': np.full((1, 1, 1, 3), 255, dtype=np.uint8),
        'near_gray': near,
        'mixed': mixed,
    }


def prepare_pages(directory: pathlib.Path) -> None:
    from bench_upscale import mandelbrot
    directory.mkdir(parents=True, exist_ok=True)
    for width, height in ((800, 1200), (1400, 2000)):
        image = mandelbrot(width, height)
        image.save(directory / f'color_{width}x{height}.png')
        image.convert('L').convert('RGB').save(directory / f'gray_{width}x{height}.png')
    print(f'Benchmark pages: {directory}', flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--variant', choices=MODELS, default='baseline')
    ap.add_argument('--output', type=pathlib.Path)
    ap.add_argument('--activation', choices=ACTIVATIONS, default='tanh')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--prepare-pages', type=pathlib.Path)
    args = ap.parse_args()
    output = args.output or pathlib.Path(
        f'tools/weights/SPANF_{args.variant}_random_x2.fp16.onnx')
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    model = MODELS[args.variant](deploy=True, activation=args.activation).eval()
    state_hash = hashlib.sha256()
    for key, value in model.state_dict().items():
        state_hash.update(key.encode())
        state_hash.update(value.numpy().tobytes())
    params = sum(p.numel() for p in model.parameters())
    temporary = output.with_suffix('.export.onnx')
    torch.onnx.export(model, (torch.rand(1, 3, 64, 96),), str(temporary),
                      dynamo=False, opset_version=18,
                      input_names=['input'], output_names=['output'],
                      dynamic_axes={'input': {0: 'n', 2: 'h', 3: 'w'},
                                    'output': {0: 'n', 2: 'out_h', 3: 'out_w'}})
    graph = float16.convert_float_to_float16(onnx.load(str(temporary)), keep_io_types=True)
    graph = add_uint8_io(graph)
    onnx.helper.set_model_props(graph, {
        'mcomix.scale': '2', 'mcomix.pad_multiple': '1',
        'mcomix.name': f'SPAN-F {args.variant} random x2 (benchmark only)',
        'mcomix.io': 'uint8-nhwc', 'mcomix.runtimes': 'windowsml,directml',
        'spanf.activation': args.activation,
        'benchmark.seed': str(args.seed), 'benchmark.weights': 'random',
        'benchmark.variant': args.variant, 'benchmark.nearest_init': 'true',
        'benchmark.architecture': 'channels=32,blocks=5,scale=2',
        'benchmark.state_sha256': state_hash.hexdigest(),
    })
    onnx.checker.check_model(graph, full_check=True)
    onnx.save(graph, str(output))
    temporary.unlink()
    print(f'{args.variant}: {params} parameters, {output.stat().st_size / 1024:.1f} KiB', flush=True)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    session = ort.InferenceSession(str(output), options, providers=['CPUExecutionProvider'])
    references, checks = {}, []
    with torch.inference_mode():
        for name, pixels in reference_cases().items():
            x = torch.from_numpy(pixels.transpose(0, 3, 1, 2).copy()).float() / 255
            prediction = model(x)
            expected = np.floor(prediction.clamp(0, 1).numpy().transpose(0, 2, 3, 1) * 255 + 0.5).astype(np.uint8)
            actual = session.run(None, {'input': pixels})[0]
            assert actual.shape == expected.shape, name
            diff = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
            assert diff.max() <= 2, (name, diff.max())
            is_gray = np.array_equal(pixels[..., 0], pixels[..., 1]) and np.array_equal(pixels[..., 1], pixels[..., 2])
            spread = int(np.ptp(actual.astype(np.int16), axis=-1).max())
            if is_gray and args.variant != 'baseline':
                assert spread == 0, (name, spread)
            references[name + '__input'] = pixels
            references[name + '__expected'] = expected
            checks.append({'case': name, 'max_diff': int(diff.max()), 'gray_input': is_gray,
                           'output_max_channel_spread': spread})
            print(f'  {name}: max diff {diff.max()}, output channel spread {spread}', flush=True)
        # A mixed batch must make its decision separately for each sample.
        batch = np.concatenate((reference_cases()['gray'], reference_cases()['color']))
        x = torch.from_numpy(batch.transpose(0, 3, 1, 2).copy()).float() / 255
        expected = np.floor(model(x).clamp(0, 1).numpy().transpose(0, 2, 3, 1) * 255 + 0.5).astype(np.uint8)
        actual = session.run(None, {'input': batch})[0]
        assert np.abs(actual.astype(np.int16) - expected.astype(np.int16)).max() <= 2
        if args.variant != 'baseline':
            assert np.ptp(actual[0].astype(np.int16), axis=-1).max() == 0
    np.savez_compressed(output.with_suffix('.reference.npz'), **references)
    summary = {
        'variant': args.variant, 'activation': args.activation, 'seed': args.seed,
        'parameters': params, 'state_sha256': state_hash.hexdigest(),
        'onnx_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'python': platform.python_version(), 'torch': torch.__version__,
        'onnx': onnx.__version__, 'onnxruntime': ort.__version__,
        'op_counts': dict(Counter(n.op_type for n in graph.graph.node)), 'cpu_checks': checks,
    }
    output.with_suffix('.export.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    if args.prepare_pages:
        prepare_pages(args.prepare_pages)


if __name__ == '__main__':
    main()
