@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Job Hunter - dashboard
rem dashboard.bat         = start it (or, if it is already running, just open it in the browser)
rem dashboard.bat --boot  = quiet start when you sign in to Windows (used by the Startup shortcut)
set "BOOT="
if /i "%~1"=="--boot" set "BOOT=1"
if not defined BOOT (
  rem already running (it starts at sign-in): just open it, no waiting
  curl -s -o nul --max-time 2 http://127.0.0.1:8095/ && (start "" http://127.0.0.1:8095/ & exit /b 0)
  echo Starting the Job Hunter dashboard... this takes up to 1-2 minutes when the laptop is busy.
  echo Your browser opens by itself when it is ready. Keep this window open.
  echo.
)
set PYTHONUTF8=1
set DATABASE_URL=postgresql+psycopg://jobhunter:jobhunter@127.0.0.1:5433/jobhunter

if defined BOOT (
  rem right after sign-in Docker Desktop needs a minute or two
  for /l %%i in (1,1,90) do (
    docker info >nul 2>&1 && goto dockerup
    timeout /t 4 /nobreak >nul
  )
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start it, wait until it says "running", then double-click dashboard again.
  if not defined BOOT pause
  exit /b 1
)
:dockerup
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
if not exist data mkdir data
if not exist data\dashboard-icon-v2.done (
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Job Hunter.lnk'); $s.TargetPath='%~f0'; $s.WorkingDirectory='%CD%'; $s.WindowStyle=1; $s.IconLocation='%SystemRoot%\System32\shell32.dll,22'; $s.Description='Open the Job Hunter dashboard'; $s.Save()"
  echo done> data\dashboard-icon-v2.done
)
if not exist data\dashboard-autostart.done (
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Job Hunter dashboard.lnk'); $s.TargetPath='%~f0'; $s.Arguments='--boot'; $s.WorkingDirectory='%CD%'; $s.WindowStyle=7; $s.Description='Start the Job Hunter dashboard'; $s.Save()"
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Job Hunter.lnk'); $s.TargetPath='%~f0'; $s.WorkingDirectory='%CD%'; $s.WindowStyle=1; $s.IconLocation='%SystemRoot%\System32\shell32.dll,22'; $s.Description='Open the Job Hunter dashboard'; $s.Save()"
  echo done> data\dashboard-autostart.done
  echo.
  echo From now on the dashboard starts by itself when you sign in to Windows,
  echo and the "Job Hunter" icon on your desktop opens it. To stop the automatic start: dashboard-autostart-off
)
set "NEEDPIP="
if not exist data\dashboard-packages.done set "NEEDPIP=1"
if not defined NEEDPIP for %%F in (requirements.txt requirements-dashboard.txt) do (
  for /f %%R in ('powershell -NoProfile -Command "if ((Get-Item '%%F').LastWriteTime -gt (Get-Item 'data\dashboard-packages.done').LastWriteTime) {'new'}"') do set "NEEDPIP=1"
)
if defined NEEDPIP (
  echo Installing / updating packages...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt -r requirements-dashboard.txt
  if errorlevel 1 (
    echo Installing the dashboard packages failed - see the messages above.
    pause
    exit /b 1
  )
  echo done> data\dashboard-packages.done
)
echo.
echo The dashboard opens in your browser: http://127.0.0.1:8095
echo It runs only on this computer. Close this window to stop it.
if defined BOOT (
  ".venv\Scripts\python.exe" -m jobhunter dashboard --no-browser
) else (
  ".venv\Scripts\python.exe" -m jobhunter dashboard
)
if not defined BOOT pause
