# Installing MComix

This fork has no package in winget, Chocolatey, Scoop, Flathub or a Linux
distribution. The `mcomix` packages there install
[upstream MComix](https://sourceforge.net/projects/mcomix/), a different
program (see [Relationship to upstream](../../README.md#relationship-to-upstream)).

## Windows: portable build

Download `mcomix-win64-<version>.zip` from the
[Releases page](https://github.com/yu45020/mcomix/releases), extract it
anywhere and run `MComix.exe`. Keep the whole folder together. No installation
or administrator access is needed. To open files and folders from the
right-click menu, see
[Add MComix to the right-click menu](../../README.md#add-mcomix-to-the-right-click-menu).

Preferences, the library and GPU caches are stored in `%APPDATA%\MComix`.
Deleting the program folder leaves them in place; delete that folder too to
remove everything.

Neural upscaling needs a DirectX 12 GPU. It never runs on the CPU.

## Windows: from source

MComix runs from a [conda-forge](https://conda-forge.org/) environment that
provides Python, GTK 3 and the compiled packages. Install
[Miniforge](https://github.com/conda-forge/miniforge) (it includes `mamba`),
then in PowerShell:

```powershell
git clone https://github.com/yu45020/mcomix.git
cd mcomix
mamba env create -p ./.venv -f environment.yml
.\start.bat
```

`start.bat [file]` starts MComix from the source tree with `.venv`. To update
the environment later, run `mamba env update -p ./.venv -f environment.yml`.
[environment.yml](../../environment.yml) lists the follow-up commands for GTK's
icon loaders and settings schemas, if a warning or the file chooser asks for
them.

## Linux and other systems: from source

This fork is developed and tested on Windows. It should also run from source
on Linux, but that isn't tested, and neural upscaling is Windows-only.

You need GTK 3 and PyGObject from your system packages: see the PyGObject
[Getting Started guide](https://pygobject.readthedocs.io/en/latest/getting_started.html),
and install the GTK **3** packages it mentions, not GTK 4 (for example
`gir1.2-gtk-3.0` on Debian and Ubuntu, `gtk3` on Arch Linux). Then create a
virtual environment that can see the system PyGObject, and install MComix into
it:

```bash
git clone https://github.com/yu45020/mcomix.git
cd mcomix
python3 -m venv --system-site-packages ../mcomix-venv
../mcomix-venv/bin/python -m pip install '.[fileformats]'
../mcomix-venv/bin/mcomix
```

`--system-site-packages` lets the environment use the PyGObject built by your
distribution; without it pip tries to build PyGObject from source. The
`fileformats` extra adds PyMuPDF (PDF) and chardet (comment file encodings).

The `share/` folder holds the desktop entry, MIME type, icons and man page,
laid out as they go under `/usr/local/share/`; copy them by hand for desktop
integration. Settings are stored in `~/.config/mcomix` and
`~/.local/share/mcomix`.

## Optional tools

| To open | You need |
| --- | --- |
| ZIP/CBZ, TAR/CBT and plain images | nothing else |
| 7z/CB7, RAR/CBR, LHA, and ZIPs Python can't read (such as AES-encrypted ones) | the `7z` executable from [7-Zip](https://www.7-zip.org/) (or 7-Zip-Zstandard), on `PATH` or next to `MComix.exe` |
| PDF | the [PyMuPDF](https://pypi.org/project/PyMuPDF/) package, or `mutool` 1.8 or later from [MuPDF](https://mupdf.com/) |
| AVIF pages | Pillow from PyPI (its wheels include AVIF; conda-forge's Pillow doesn't) |
| Neural upscaling (Windows) | the `upscale` extra in [pyproject.toml](../../pyproject.toml) and a DirectX 12 GPU |

`environment.yml` installs PyMuPDF, Pillow from PyPI and the `upscale` extra,
so a source setup on Windows only lacks 7-Zip. The portable build bundles the
Python packages; it includes `7z` and `mutool` only when its builder added
them.
