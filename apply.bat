@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Job Hunter - apply
set PYTHONUTF8=1
set DATABASE_URL=postgresql+psycopg://jobhunter:jobhunter@127.0.0.1:5433/jobhunter

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start it, wait until it says "running", then double-click apply again.
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
rem keep packages up to date after Job Hunter updates (fast when nothing changed)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt >nul 2>&1
".venv\Scripts\python.exe" -m jobhunter check-key
:menu
echo.
echo ================= Your jobs =================
".venv\Scripts\python.exe" -m jobhunter list
echo =============================================
echo.
set "JOBID="
set /p JOBID=Type the id of the job to apply to (or just press Enter to quit): 
if not defined JOBID exit /b 0
echo.
echo For LinkedIn / StepStone / XING / Indeed jobs, open the job, click Apply, and copy the
echo company's application page link. For company career pages, just press Enter.
set "LINK="
set /p LINK=Application link (or press Enter): 
if defined LINK (
  ".venv\Scripts\python.exe" -m jobhunter fill !JOBID! --url "!LINK!"
) else (
  ".venv\Scripts\python.exe" -m jobhunter fill !JOBID!
)
goto menu
