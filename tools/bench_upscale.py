"""Speed test for ONNX models through the viewer's adapter, on pages smaller than 2K.

Usage:
    python tools/bench_upscale.py [images, folders or .zip/.cbz ...] --onnx model.onnx
        [--tile 768 --overlap 32]

Without inputs, synthetic 800x1200 and 1400x2000 pages (Mandelbrot images)
are used. With --tile, the PSNR of tiled vs full-frame output is also reported.
"""

from __future__ import annotations

import argparse
import io
import pathlib
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import numpy as np
from PIL import Image, ImageDraw

MAX_SIDE = 2048
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.avif'}


def load_pages(paths: list[str], limit: int, max_side: int) -> list[tuple[str, Image.Image]]:
    sources = []  # (name, path or bytes)
    for p in map(pathlib.Path, paths):
        if p.is_dir():
            files = sorted(f for f in p.rglob('*') if f.suffix.lower() in IMAGE_EXTS)
            sources += [(f.name, f) for f in files]
        elif p.suffix.lower() in ('.zip', '.cbz'):
            with zipfile.ZipFile(p) as z:
                sources += [(n, io.BytesIO(z.read(n))) for n in sorted(z.namelist())
                            if pathlib.Path(n).suffix.lower() in IMAGE_EXTS]
        else:
            sources.append((p.name, p))
    pages = []
    for name, src in sources:
        try:
            pages.append((name, Image.open(src)))
        except Exception as e:
            print(f'skip {name}: {e}')
    if not paths:
        pages = [(f'synthetic {w}x{h}', mandelbrot(w, h)) for w, h in ((800, 1200), (1400, 2000))]
    pages = [(n, im.convert('RGB')) for n, im in pages if max(im.size) < max_side]
    return pages[:limit]


def mandelbrot(width: int, height: int) -> Image.Image:
    """Structured test image (noise is out of distribution for SR models):
    a Mandelbrot set with comic-like line art on top."""
    im = Image.effect_mandelbrot((width, height), (-2.0, -1.2, 0.8, 1.2), 64)
    im = Image.merge('RGB', (im, im.rotate(180), im.transpose(Image.FLIP_LEFT_RIGHT)))
    draw = ImageDraw.Draw(im)
    rng = np.random.default_rng(0)
    for _ in range(width * height // 2000):
        x, y = rng.integers(0, width), rng.integers(0, height)
        color = tuple(int(c) for c in rng.integers(0, 256, 3))
        if rng.random() < 0.7:
            dx, dy = rng.integers(-80, 81, 2)
            draw.line((x, y, x + dx, y + dy), fill=color, width=int(rng.integers(1, 4)))
        else:
            r = int(rng.integers(3, 30))
            draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=2)
    return im


def psnr(a: Image.Image, b: Image.Image) -> float:
    mse = np.mean((np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)) ** 2)
    return float(10 * np.log10(255 ** 2 / max(float(mse), 1e-12)))


def timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def gpu_dedicated_mib() -> float:
    """This process's dedicated GPU memory (Windows performance counter)."""
    query = (f"(Get-Counter '\\GPU Process Memory(pid_{os.getpid()}_*)\\Dedicated Usage')"
             ".CounterSamples | Measure-Object CookedValue -Sum | % Sum")
    powershell = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32',
                              'WindowsPowerShell', 'v1.0', 'powershell.exe')
    # A caller may inherit PowerShell 7's bundled module path. Windows
    # PowerShell cannot load those Diagnostics type files; use its own
    # modules rather than silently reporting zero GPU memory.
    env = os.environ.copy()
    env['PSModulePath'] = os.path.join(os.path.dirname(powershell), 'Modules')
    out = subprocess.run([powershell, '-NoProfile', '-Command', query],
                         capture_output=True, text=True, env=env).stdout.strip()
    return float(out or 0) / 2**20


class TimedSession(object):
    """Wraps an InferenceSession and adds up the time spent in run()."""

    def __init__(self, session) -> None:
        self.session = session
        self.ms = 0.0

    def run(self, *args, **kwargs):
        t = time.perf_counter()
        try:
            return self.session.run(*args, **kwargs)
        finally:
            self.ms += (time.perf_counter() - t) * 1000

    def __getattr__(self, name: str):
        return getattr(self.session, name)


def bench_onnx(paths: list[str], pages: list[tuple[str, Image.Image]], reps: int,
               tiling: dict[str, int], psnr_check: bool = True,
               runtime: str = 'directml', engine_cache: str = '') -> None:
    """Times mcomix's ONNX adapter (pad, run, crop, PIL conversion) and reports
    memory. "Model ms" is the time spent in InferenceSession.run (including
    host <-> device copies); the rest of "warm ms" is CPU-side conversion.
    Peak memory is per process: benchmark one model per run to compare."""
    import psutil
    proc = psutil.Process()

    def ram() -> str:
        info = proc.memory_info()
        return f'RAM {info.rss / 2**20:.0f} MiB (peak {info.peak_wset / 2**20:.0f})'

    print(f'start: {ram()}, VRAM {gpu_dedicated_mib():.0f} MiB')
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    t = time.perf_counter()
    import onnxruntime
    print(f'import onnxruntime {onnxruntime.__version__}: '
          f'{(time.perf_counter() - t) * 1000:.0f} ms')
    from mcomix.upscale_backends.onnx_sr import OnnxSrAdapter
    for path in paths:
        # The app's code path: Windows ML providers with 'auto', see
        # mcomix/upscale_backends/windows_ml.py (the first run builds the
        # engine in a child process; later runs load it from engine_cache).
        adapter = OnnxSrAdapter(path, runtime=runtime, cache_dir=engine_cache, **tiling)
        t = time.perf_counter()
        adapter.load()
        adapter._session = timed_session = TimedSession(adapter._session)
        fast = adapter.can_fall_back
        print(f'\n{pathlib.Path(path).name} ({adapter.provider_label}, '
              f'{getattr(adapter, "io", "float32-nchw")}): '
              f'session {(time.perf_counter() - t) * 1000:.0f} ms; '
              f'{ram()}, VRAM {gpu_dedicated_mib():.0f} MiB')
        if tiling['tile_size']:
            print(f'  tiles of {tiling["tile_size"]} px + {tiling["tile_overlap"]} px overlap')
        print(f'  {"page":<40} {"in":>11} {"first ms":>9} {"warm ms":>8} {"model ms":>9}'
              f' {"MP/s":>6} {"peak VRAM MiB":>14}')
        for page_name, im in pages:
            vram = [0.0]
            done = threading.Event()

            def sample() -> None:
                while not done.is_set():
                    vram[0] = max(vram[0], gpu_dedicated_mib())
            sampler = threading.Thread(target=sample)
            sampler.start()
            first = timed(lambda: adapter.upscale(im))
            runs = []
            for _ in range(reps):
                timed_session.ms = 0.0
                runs.append((timed(lambda: adapter.upscale(im)), timed_session.ms))
            warm = statistics.median(total for total, _ in runs)
            model = statistics.median(model for _, model in runs)
            done.set()
            sampler.join()
            size = f'{im.width}x{im.height}'
            print(f'  {page_name[-40:]:<40} {size:>11} {first:>9.0f} {warm:>8.0f} {model:>9.0f}'
                  f' {im.width * im.height / 1e6 / warm * 1000:>6.2f} {vram[0]:>14.0f}')
        print(f'  after pages: {ram()}, VRAM {gpu_dedicated_mib():.0f} MiB')
        if fast and psnr_check:
            # Same tiling on DirectML, as the reference for accuracy.
            results = [adapter.upscale(im) for _, im in pages]
            reference = OnnxSrAdapter(path, **tiling)
            reference.load()
            for (page_name, im), result in zip(pages, results):
                expected = reference.upscale(im)
                diff = np.abs(np.asarray(result, dtype=int) - np.asarray(expected, dtype=int))
                print(f'  {page_name[-40:]:<40} {adapter.provider_label} vs DirectML: PSNR '
                      f'{psnr(result, expected):.1f} dB, max diff {diff.max()}, '
                      f'{(diff > 2).mean() * 100:.3f}% of values differ by > 2')
            reference.close()
        if tiling['tile_size'] and psnr_check and not fast:
            # After the memory figures: full frames need much more VRAM.
            # (Windows ML engines only accept inputs up to a tile.)
            tiled = [adapter.upscale(im) for _, im in pages]
            adapter.tile_size = 0
            for (page_name, im), result in zip(pages, tiled):
                full = adapter.upscale(im)
                diff = np.abs(np.asarray(result, dtype=int) - np.asarray(full, dtype=int))
                print(f'  {page_name[-40:]:<40} tiled vs full frame: PSNR '
                      f'{psnr(result, full):.1f} dB, max diff {diff.max()}, '
                      f'{(diff > 2).mean() * 100:.3f}% of values differ by > 2')
        adapter.close()
        print(f'  after close: {ram()}, VRAM {gpu_dedicated_mib():.0f} MiB')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='*')
    ap.add_argument('--pages', type=int, default=5, help='max pages to test')
    ap.add_argument('--max-side', type=int, default=MAX_SIDE,
                    help='only test pages smaller than this')
    ap.add_argument('--reps', type=int, default=5, help='warm runs per page')
    ap.add_argument('--onnx', action='append', required=True, metavar='PATH',
                    help='ONNX model to benchmark (repeatable)')
    ap.add_argument('--tile', type=int, default=0, metavar='SIZE',
                    help='tile size in input pixels (default: full frame)')
    ap.add_argument('--overlap', type=int, default=32, help='tile overlap per side')
    ap.add_argument('--no-psnr', action='store_true',
                    help='skip the full-frame reference run for tiles')
    ap.add_argument('--runtime', choices=('directml', 'auto'), default='directml',
                    help='auto = the fastest installed Windows ML provider (e.g. '
                         'TensorRT-RTX), as the viewer does; needs onnxruntime-windowsml '
                         'and windowsml')
    ap.add_argument('--engine-cache', metavar='DIR',
                    default=os.path.join(tempfile.gettempdir(), 'mcomix-engine-cache'),
                    help='where Windows ML engines are cached')
    args = ap.parse_args()

    pages = load_pages(args.inputs, args.pages, args.max_side)
    if not pages:
        raise SystemExit(f'No pages with max side < {args.max_side}')
    bench_onnx(args.onnx, pages, args.reps,
               {'tile_size': args.tile, 'tile_overlap': args.overlap}, not args.no_psnr,
               args.runtime, args.engine_cache)


if __name__ == '__main__':
    main()
