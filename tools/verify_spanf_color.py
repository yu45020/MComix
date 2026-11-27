"""Verify FP16 SPAN-F exports on actual GPU providers, before timing.

Reads references saved by export_spanf_color.py; torch stays out of this
process. Uses RGB inputs/outputs directly, before any app grayscale conversion.
Checks odd/tiny shapes, gray/near-gray/color and mixed pages, and whole against
tiled output. CPU fallback is disabled by the viewer adapter, and --runtime
auto must select TensorRT-RTX.
"""

import argparse
import json
import pathlib
import sys

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcomix.upscale_backends.onnx_sr import OnnxSrAdapter  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--onnx', type=pathlib.Path, required=True)
    ap.add_argument('--runtime', choices=('auto', 'directml'), required=True)
    ap.add_argument('--engine-cache', required=True)
    ap.add_argument('--output', type=pathlib.Path, required=True)
    args = ap.parse_args()
    metadata = json.loads(args.onnx.with_suffix('.export.json').read_text(encoding='utf-8'))
    references = np.load(args.onnx.with_suffix('.reference.npz'))
    adapter = OnnxSrAdapter(str(args.onnx), runtime=args.runtime, cache_dir=args.engine_cache,
                            tile_size=768, tile_overlap=32)
    adapter.load()
    try:
        if args.runtime == 'auto':
            assert adapter.can_fall_back, f'Windows ML fell back to {adapter.provider_label}'
        else:
            assert adapter.provider_label == 'DirectML', adapter.provider_label
        print(f'{metadata["variant"]}: {adapter.provider_label}', flush=True)
        checks = []
        for key in references.files:
            if not key.endswith('__input'):
                continue
            name = key.removesuffix('__input')
            pixels = references[key]
            actual = adapter._run(pixels[0])[None]
            expected = references[name + '__expected']
            assert actual.shape == expected.shape, name
            diff = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
            assert diff.max() <= 3, (name, diff.max())
            is_gray = np.array_equal(pixels[..., 0], pixels[..., 1]) and np.array_equal(pixels[..., 1], pixels[..., 2])
            spread = int(np.ptp(actual.astype(np.int16), axis=-1).max())
            if is_gray and metadata['variant'] != 'baseline':
                assert spread == 0, (name, spread)
            checks.append({'case': name, 'max_diff': int(diff.max()),
                           'gray_input': is_gray, 'output_max_channel_spread': spread})
            print(f'  {name}: max diff {diff.max()}, output channel spread {spread}', flush=True)
        mixed = Image.fromarray(references['mixed__input'][0])
        full = np.asarray(adapter.upscale(mixed))
        adapter.tile_size, adapter.tile_overlap = 32, 16
        tiled = np.asarray(adapter.upscale(mixed))
        difference = np.abs(full.astype(np.int16) - tiled.astype(np.int16))
        tile_diff = int(difference.max())
        assert tile_diff <= 2, tile_diff
        print(f'  mixed full/tiled: max diff {tile_diff}, unequal values {(difference != 0).mean():.3%}', flush=True)
        result = {'variant': metadata['variant'], 'provider': adapter.provider_label,
                  'checks': checks, 'mixed_full_tiled_max_diff': tile_diff,
                  'mixed_full_tiled_unequal_fraction': float((difference != 0).mean())}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    finally:
        adapter.close()


if __name__ == '__main__':
    main()
