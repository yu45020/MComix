#!/usr/bin/env python3
import argparse
import os
import pathlib
import re
import shutil
import string
import subprocess
import sys
import zipfile

# Patch import path to be able to import the mcomix module
sys.path.append(str(pathlib.Path(__file__).parent.parent))

from mcomix import constants

""" Wrapper for pyinstaller, to compensate some shortcomings of the build process.

This file should be run from MComix' root directory, in order to avoid
having to play around with relative path names.

    Run build_portable.bat from Windows Command Prompt or PowerShell.
    It uses the conda-forge environment in .venv; see CLAUDE.md and
    win32/README.md for setup. No MSYS2 or installer compiler is needed.
    Builds one GUI application in dist/MComix; --zip also creates a ZIP.

"""


def clear_distdir(distdir: str) -> None:
    """ Removes files from <distdir>. """
    if not os.path.isdir(distdir):
        return

    files = [os.path.join(distdir, file)
             for file in os.listdir(distdir)]

    print('Cleaning %s...' % distdir)
    for file in files:
        if os.path.isfile(file):
            os.unlink(file)
        elif os.path.isdir(file):
            shutil.rmtree(file)


def prepare_version_file() -> None:
    """ Fills out version information for embedding in the final executable. """
    print("Creating version file with current MComix version")
    with open('win32/version_file.template') as fp:
        tmpl = string.Template(fp.read())

    version_match = re.search(r"^(\d+)\.(\d+).(\d+)", constants.VERSION)
    if not version_match:
        print('Could not determine current version', file=sys.stderr)
        sys.exit(1)

    version_file_contents = tmpl.substitute(major=version_match.group(1),
                                            minor=version_match.group(2),
                                            patch=version_match.group(3))
    with open('win32/version_file.txt', 'w') as out:
        out.write(version_file_contents)


def run_pyinstaller(attach_console: bool) -> int:
    """ Runs PyInstaller using the build environment's Python. """
    print('Executing pyinstaller...')
    args = [sys.executable, '-m', 'PyInstaller', 'win32/mcomix.spec', '--noconfirm', '--clean']

    # The spec file takes this environment setting to determine if a console should be attached
    environ = os.environ.copy()
    environ["PYINSTALLER_CONSOLE"] = "1" if attach_console else "0"

    proc_result = subprocess.run(args, env=environ)

    return proc_result.returncode


def win32_newline(source: str, dest: str) -> None:
    """ Converts Unix newlines to Windows newlines. """
    from_fp = open(source, "r", encoding='utf-8')

    dest_dir = os.path.split(dest)[0]
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    to_fp = open(dest, "w", encoding='utf-8')

    for line in from_fp:
        to_fp.write(line.rstrip())
        to_fp.write("\n")

    from_fp.close()
    to_fp.close()


def copy_other_files() -> None:
    """ Copy other relevant files into dist directory. """
    print("Copying misc files into dist directory...")
    win32_newline('ChangeLog.md', 'dist/MComix/ChangeLog.md')
    win32_newline('README.md', 'dist/MComix/README.md')
    win32_newline('COPYING', 'dist/MComix/licenses/mcomix/COPYING.txt')
    win32_newline('mcomix/upscale_models/README.md',
                  'dist/MComix/licenses/upscale-models/README.md')

    if os.path.isdir('../mcomix-other/7z'):
        shutil.copy('../mcomix-other/7z/7z.exe', 'dist/MComix/7z.exe')
        shutil.copy('../mcomix-other/7z/7z.dll', 'dist/MComix/7z.dll')
        win32_newline('../mcomix-other/7z/License.txt', 'dist/MComix/licenses/7z/License.txt')

    if os.path.isdir('../mcomix-other/mutool'):
        shutil.copy('../mcomix-other/mutool/mutool.exe', 'dist/MComix/mutool.exe')
        win32_newline('../mcomix-other/mutool/COPYING.txt', 'dist/MComix/licenses/mupdf/COPYING.txt')

    # Conda-forge stores shared licenses under the environment prefix.
    for relative in ('Library/share/licenses', 'share/licenses'):
        source = pathlib.Path(sys.prefix) / relative
        if source.is_dir():
            shutil.copytree(source, 'dist/MComix/licenses/conda-forge',
                            dirs_exist_ok=True)



def create_release_archive() -> None:
    """ Packs the contents of the release directory. """
    print("Creating ZIP archive...")
    with zipfile.ZipFile(f'dist/mcomix-win64-{constants.VERSION}.zip', 'w', compression=zipfile.ZIP_DEFLATED) as zip:
        basedir = pathlib.Path('dist/MComix/')
        for dirpath, dirnames, filenames in os.walk('dist/MComix/'):
            currentdir = pathlib.Path(dirpath)
            for filename in filenames:
                full_file_path = currentdir / filename
                zip.write(full_file_path, pathlib.Path('MComix') / full_file_path.relative_to(basedir))


def main() -> None:
    parser = argparse.ArgumentParser(description='Build the portable Windows application.')
    parser.add_argument('--zip', action='store_true',
                        help='also create a ZIP of the portable application')
    args = parser.parse_args()
    prepare_version_file()
    try:
        clear_distdir('dist/MComix/')
        if run_pyinstaller(attach_console=False) != 0:
            sys.exit(1)
        copy_other_files()
        if args.zip:
            create_release_archive()
    finally:
        os.unlink('win32/version_file.txt')


if __name__ == '__main__':
    main()

# vim: expandtab:sw=4:ts=4
