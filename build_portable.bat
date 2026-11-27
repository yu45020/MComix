@echo off
rem Build one portable GUI application; pass --zip to also create an archive.
setlocal
pushd "%~dp0" || exit /b 1
set "VENV=%~dp0.venv"
if not exist "%VENV%\python.exe" (
    echo ERROR: Set up the conda environment in .venv first. See environment.yml.
    goto :failed
)
rem Isolate GTK from other Python/GTK installations, just as start.bat does.
set "GI_TYPELIB_PATH="
set "PATH=%VENV%;%VENV%\Library\bin;%VENV%\Scripts;%SystemRoot%\system32;%SystemRoot%"
"%VENV%\python.exe" -c "import PyInstaller, gi, cairo, PIL, numpy, onnxruntime, windowsml"
if errorlevel 1 (
    echo ERROR: Build dependencies are missing. See win32\README.md.
    echo Install PyInstaller with: mamba install -p ./.venv --override-channels -c conda-forge pyinstaller
    goto :failed
)
"%VENV%\python.exe" win32\build_pyinstaller.py %*
if errorlevel 1 goto :failed
echo Portable application ready: dist\MComix\MComix.exe
popd
exit /b 0

:failed
popd
exit /b 1
