@echo off
rem Double-click to open the editor, or drag a recording / .aeproj onto this file.
cd /d "%~dp0"
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
".venv\Scripts\python.exe" editor.py %*
if errorlevel 1 pause
