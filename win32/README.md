# Building a portable Windows package

Run `build_portable.bat` from any directory. It uses the conda-forge
Python/GTK environment in `.venv` and builds the GUI application once in
`dist/MComix`. Run `dist\MComix\MComix.exe`. Keep the whole folder together.
No ZIP is created by default. Use `build_portable.bat --zip` to also create
`dist/mcomix-win64-<version>.zip`. Existing ZIP files are left untouched.
Python and the application dependencies are bundled; users do not need
conda, MSYS2 or an installation step.

## Prerequisites

- Set up `.venv` with the command at the top of `environment.yml`.
- Install PyInstaller 6 in that environment:
  `mamba install -p ./.venv --override-channels -c conda-forge pyinstaller`.
- Install the pinned `upscale` dependencies from `pyproject.toml` in the
  same environment (numpy from conda-forge; the two Windows ML packages
  from pip). The batch script checks these dependencies before building.

The build uses a clean PATH to avoid loading GTK libraries from MSYS2.
Existing files in `dist/MComix` are replaced on each build.
Optional archive tools and their licenses are copied from the sibling
`mcomix-other` directory by `build_pyinstaller.py` when present.

You can empty or delete `build/` when no build or smoke test is running.
It contains generated PyInstaller files, logs, temporary smoke-test copies,
and a disposable conda package download cache. The next build recreates
what it needs. The application only needs `dist/MComix/` to run.

## Models and replacement checkpoints

The portable application includes this loose file under
`MComix\_internal\mcomix\upscale_models\`:

- `SPANF_YC_2x.fp16.onnx`: trained SPAN-F-YC x2, the default.

The model's license and attributions are included beside the model and under
`MComix\licenses\upscale-models\README.md`.
See `mcomix/upscale_models/README.md` for sources and export instructions.

Users select a model in **Preferences > Display > Image quality**. They can
keep replacement ONNX files anywhere in their user folder, so selecting a
custom model needs no administrator access and survives replacing the application folder.
The **Default** button returns to the bundled trained SPAN-F-YC model. Users can also
replace the bundled ONNX file while MComix is closed; extracting a fresh
release over the folder can overwrite those replacements.

This is a portable application distribution. Preferences, library data, and
GPU caches still use the normal `%APPDATA%\MComix` directory.

The viewer loads **ONNX**, not raw `.pth` checkpoints. Convert a supported
checkpoint using `tools/export_onnx.py` in a separate development environment
with its documented export dependencies, then select the exported ONNX.
Keep together the precision variants printed under `ship:` by the exporter.
Models need MComix metadata and compatible dynamic input/output shapes;
renaming a checkpoint to `.onnx` does not convert it. `tools/export_onnx.py`
imports trained SPAN-F-YC checkpoints and recomputes their fused deployment
convolutions. Its `--spanf-activation` defaults to `tanh`, matching
the bundled trained model; use the activation used during training.

Before distributing builds, resolve the `windowsml` redistribution terms:
its package metadata states a Microsoft copyright with no license text.

## Releasing a build

Releases are the portable ZIP only; there is no installer or package manager
build. Before publishing a ZIP:

- record the source commit and the SHA-256 of the ZIP;
- run the built `MComix.exe` in a clean user account (a build log doesn't show
  that it starts);
- check the third-party and model notices under `licenses/`, and resolve the
  `windowsml` redistribution terms;
- back up `%APPDATA%\MComix` before testing an upgrade over an existing setup.
