@echo off
rem Drag one or more RAW recordings onto this file. Edited videos are read from %APPDATA%\AutoEditor\training.
cd /d "%~dp0"
".venv\Scripts\python.exe" auto_editor.py --train %*
pause
