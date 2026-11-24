@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Job Hunter - dashboard
set PYTHONUTF8=1
set DATABASE_URL=postgresql+psycopg://jobhunter:jobhunter@127.0.0.1:5433/jobhunter

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start it, wait until it says "running", then double-click dashboard again.
  pause
  exit /b 1
)
echo Starting the job database...
docker compose up -d db >nul 2>&1
for /l %%i in (1,1,20) do (
  docker compose exec -T db pg_isready -U jobhunter >nul 2>&1 && goto dbready
  timeout /t 2 /nobreak >nul
)
echo The database did not start. Run start-jobhunter once, then try again.
pause
exit /b 1
:dbready

if exist ".venv\Scripts\python.exe" goto ready
echo.
echo First-time setup: installing Python packages and a browser (3-5 minutes)...
set "PYEXE="
where py >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3"
if not defined PYEXE if exist "C:\Python311\python.exe" set "PYEXE=C:\Python311\python.exe"
if not defined PYEXE set "PYEXE=python"
!PYEXE! -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo Could not create a Python environment. Is Python installed? Get it from python.org, then try again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Installing packages failed - see the messages above.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m playwright install chromium
echo Setup finished.

:ready
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt -r requirements-dashboard.txt
if errorlevel 1 (
  echo Installing the dashboard packages failed - see the messages above.
  pause
  exit /b 1
)
echo.
echo The dashboard opens in your browser: http://127.0.0.1:8095
echo It runs only on this computer. Close this window to stop it.
".venv\Scripts\python.exe" -m jobhunter dashboard
pause
