@echo off
rem Run MComix from source using the conda env in .venv.
rem Isolate from MSYS2: its GI_TYPELIB_PATH and DLLs on PATH crash/break PyGObject.
setlocal
set "VENV=%~dp0.venv"
set "GI_TYPELIB_PATH="
set "PATH=%VENV%;%VENV%\Library\bin;%VENV%\Scripts;%SystemRoot%\system32;%SystemRoot%"
"%VENV%\python.exe" "%~dp0mcomixstarter.py" %*
