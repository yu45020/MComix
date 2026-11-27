# Bundled upscaling models

The model files are data loaded at runtime by ONNX Runtime; they are not part
of MComix's GPL-licensed source code and have their own licenses, given with
each model below. They were converted to ONNX by
`tools/export_onnx.py` (opset 18, uint8 input/output); no retraining.

A model can come as precision variants side by side (`x.fp16.onnx`,
`x.fp32.onnx`). Whichever one is chosen in Preferences, MComix uses the
variant whose `mcomix.runtimes` metadata lists the GPU runtime in use
(`windowsml` for TensorRT-RTX, `directml`); the export script measures and
records this. Only variants that a runtime uses are bundled: the model
below uses fp16 on both runtimes, so its fp32 file is not shipped.

## Default: SPAN-F-YC x2

`SPANF_YC_2x.fp16.onnx` (used by both runtimes).

- Network: project SPAN-F-YC, defined in
  `super_resolution/src/models/spanf_yc.py`: a SPAN-F trunk with 32 channels,
  five blocks and Tanh activation, a BT.601 luma head, and softmax-weighted
  5x5 chroma resampling. Equal RGB input channels produce equal output channels.
- Backbone: NTIRE 2025 Efficient SR (team XiaomiMM),
  https://github.com/Amazingren/NTIRE2025_ESR/blob/main/models/team24_SPANF.py
  (MIT License, Copyright (c) 2025 BinRen).
- Weights: `SPANF_YC_2x.pth`, trained for this fork.
- Source checkpoint SHA-256:
  `4c91c8748e60417d53ed98180fe61a4eafd609554b42101ba2f0ea2745d8fea5`
- License: MIT, below.

### License of SPANF_YC_2x.fp16.onnx

MIT License

Copyright (c) 2026 yu45020

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The exporter infers scale, feature width and chroma kernel size, strictly loads
weights, and recomputes fused convolutions from the training branches.
`--spanf-activation` defaults to `tanh`, matching the supplied training model.
The former RGB SPAN-F model and its checkpoint export are no longer supported.

## Using replacement weights

Choose an exported `.onnx` file in Preferences > Display > Image quality.
Keep custom models in a user-owned folder so application updates do not
replace them. Raw `.pth` checkpoints must first be converted with a matching
exporter; `tools/export_onnx.py` handles the architectures supported by its
Spandrel loader and also detects training or fused SPAN-F-YC checkpoints. SPAN-F-YC
training branches are fused from their learned weights before export; saved
`eval_conv` caches are recomputed. For example:

```
python tools/export_onnx.py tools/weights/SPANF_YC_2x.pth --name "SPAN-F-YC x2"
```

This writes `.fp32.onnx` and `.fp16.onnx` beside the checkpoint and prints
which files to use.
See `win32/README.md` in the source tree for packaging details.

## Regenerating trained models

For checkpoints fine-tuned with `super_resolution/src/models/span.py`, use:

```
python tools/export_onnx.py tools/weights/2x-span.pth --name "SPAN x2" --precision fp32 --opset 18
```

This infers the scale and feature width, recomputes every Conv3XC from its
training branches, and writes `2x-span.fp32.onnx` beside the checkpoint. It uses
the legacy exporter, with dynamic batch and image dimensions and uint8 NHWC RGB
I/O. Use `--io float32` for float32 NCHW I/O in [0, 1].

Input normalization is inside the graph: `(x - mean) * img_range`, with
defaults `mean=(0.4488, 0.4371, 0.4040)` and `img_range=255`. The output is used
directly, without reversing that normalization, matching the training model.
If training used different settings, pass `--img-range VALUE --rgb-mean R G B`;
these values cannot be recovered from the state dictionary.

FP32 is deliberate: the supplied `2x-span.pth` lost substantial accuracy in
FP16 on the structured export check (about 24.5 dB agreement with PyTorch).
Without `--precision fp32` the export also creates FP16 for evaluation. Check
its printed accuracy before using it. An FP32-only export marks FP32 for both
runtimes and disables runtime selection of any older FP16 sibling.
TensorRT-RTX is more sensitive to reduced precision than DirectML. For this
checkpoint, use DirectML for verified FP32 agreement; mixed-precision exports
were investigated and not adopted.

Copy the files the export prints under `ship:`:

```
python tools/export_onnx.py tools/weights/SPANF_YC_2x.pth --name "SPAN-F-YC x2"
copy tools\weights\SPANF_YC_2x.fp16.onnx mcomix\upscale_models\
```
