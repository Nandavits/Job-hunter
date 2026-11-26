@echo off
rem Claude reads every open job's ad against your CV now: jobs where you miss a required skill are hidden.
cd /d "%~dp0"
set PYTHONUTF8=1
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo Checking every open job against your CV... (about 2 seconds per job)
"%PY%" -m jobhunter ai-check
pause
