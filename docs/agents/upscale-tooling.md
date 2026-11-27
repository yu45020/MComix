# Upscaling model tooling

The development scripts in `tools/` that export, benchmark and verify upscaling models. None of
them ships with the app; the bundled model is in `mcomix/upscale_models/`. The exporters and
models need torch, onnx, onnxscript, onnxconverter-common and spandrel, and `bench_upscale.py`
needs psutil, which `.venv` lacks: run them in the training environment. `export_onnx.py`,
`bench_upscale.py` and `verify_spanf_color.py` also need an ONNX Runtime with the DirectML
provider (the viewer's, or `onnxruntime-directml`), and `--runtime auto` needs the viewer's
Windows ML packages.

`tools/export_onnx.py` exports a trained checkpoint (any architecture its Spandrel loader
supports, and SPAN-F-YC training or fused checkpoints) to the viewer's ONNX format;
`mcomix/upscale_models/README.md` gives the commands for the bundled model and for fine-tuned
SPAN (`--precision fp32 --opset 18`). It detects SPAN-F-YC checkpoints, recomputes their fused
convolutions and exports a deployment-only copy. Their activation (`tanh`, matching
`super_resolution/src/models/spanf.py`, or upstream `silu`) is not in the checkpoint:
`--spanf-activation`, default `tanh`. RGB SPAN-F is no longer bundled or accepted. The trained
default is `tools/weights/SPANF_YC_2x.pth`.

`tools/spanf.py` implements the checkpoint-compatible SPAN-F training model and Conv3XC fusion;
`tools/spanf_yc.py` ports `super_resolution/src/models/spanf_yc.py` (BT.601 luma head + softmax
kernel-resampled chroma) onto its blocks in a shape-free, DirectML-only-safe ONNX form.

`tools/bench_upscale.py` times ONNX models through the viewer's adapter:

```
python tools/bench_upscale.py --onnx mcomix/upscale_models/SPANF_YC_2x.fp16.onnx
```

`tools/export_spanf_color.py` exports random-weight FP16 SPAN-F x2 benchmarks: `--variant
baseline` (the default, a Tanh control) or `--variant yc` (SPAN-F-YC). It saves reference
outputs beside the model, which `tools/verify_spanf_color.py` checks on the GPU providers:

```
python tools/export_spanf_color.py --variant yc
python tools/verify_spanf_color.py --onnx tools/weights/SPANF_yc_random_x2.fp16.onnx --runtime auto --engine-cache .tmp/engine-cache --output .tmp/spanf-yc-verify.json
```
