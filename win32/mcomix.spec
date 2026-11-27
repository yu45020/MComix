# -*- mode: python ; coding: utf-8 -*-


import glob
import os

def list_files(basedir, *patterns):
    """ Locates all files in <basedir> that match one of <patterns>. """

    all_files = []
    for dirpath, _, _ in os.walk(basedir):

        for pattern in patterns:
            cur_pattern = os.path.join(dirpath, pattern)
            all_files.extend([os.path.normpath(path)
                              for path in glob.glob(cur_pattern)])

    return all_files


added_files = [(os.path.join('..', path),
                os.path.split(path)[0])
               for path in list_files('mcomix/messages', '*.mo')]
added_files.extend([(os.path.join('..', path),
                     os.path.split(path)[0])
                    for path in list_files('mcomix/images', '*.png', '*.svg')])
added_files.extend([(os.path.join('..', path),
                     os.path.split(path)[0])
                    for path in list_files('mcomix/upscale_models', '*.onnx', '*.md')])

# windowsml loads its DLL with ctypes, which PyInstaller cannot see.
# Check its license terms before distributing (see win32/README.md).
try:
    from PyInstaller.utils.hooks import collect_dynamic_libs
    added_binaries = (collect_dynamic_libs('windowsml')
                      + collect_dynamic_libs('onnxruntime'))
except Exception:
    added_binaries = []

attach_console = True
if 'PYINSTALLER_CONSOLE' in os.environ:
    attach_console = int(os.environ['PYINSTALLER_CONSOLE']) != 0

a = Analysis(['../mcomixstarter.py'],
             pathex=[],
             binaries=added_binaries,
             datas=added_files,
             hiddenimports=[],
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             excludes=['torch', 'spandrel', 'onnx', 'onnxscript'],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             noarchive=False)

pyz = PYZ(a.pure)

exe = EXE(pyz,
          a.scripts,
          [],
          exclude_binaries=True,
          name='MComix',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          console=attach_console,
          disable_windowed_traceback=False,
          target_arch=None,
          version=os.path.join(SPECPATH, 'version_file.txt'),
          codesign_identity=None,
          entitlements_file=None , icon='../mcomix/images/mcomix.ico')

coll = COLLECT(exe,
               a.binaries,
               a.datas,
               strip=False,
               upx=True,
               upx_exclude=[],
               name='MComix')
