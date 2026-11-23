@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Job Hunter - check settings
rem Drag a profile.yaml or config.yaml onto this file to check it, or double-click to check your own.
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m jobhunter check-settings %*
echo.
pause
