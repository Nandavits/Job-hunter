@echo off
rem Job Hunter: search for jobs that fit YOUR CV (config\profile.yaml) instead of the titles in config.yaml.
cd /d "%~dp0"
set PYTHONUTF8=1
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m jobhunter use-cv-search
echo.
call check-settings.bat
