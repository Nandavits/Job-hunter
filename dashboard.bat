@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Job Hunter - dashboard
rem dashboard.bat         = start it (or, if it is already running, just open it in the browser)
rem dashboard.bat --boot  = quiet start when you sign in to Windows (used by the Startup shortcut)
set "BOOT="
if /i "%~1"=="--boot" set "BOOT=1"
if not exist data mkdir data
rem the background check and the desktop icon point at THIS folder: set up again if the folder moved
set "TASKWHERE="
if exist data\dashboard-autostart-v3.done set /p TASKWHERE=<data\dashboard-autostart-v3.done
if not exist data\dashboard-autostart-off.flag if /i not "%TASKWHERE%"=="%~dp0" (
  rem keep it running: a hidden check every 5 minutes starts it again if it stopped
  powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\Job Hunter dashboard.lnk')"
  schtasks /create /f /tn "Job Hunter dashboard" /sc minute /mo 5 /tr "wscript.exe \"%~dp0dashboard-hidden.vbs\"" >nul
  if not errorlevel 1 (
    >data\dashboard-autostart-v3.done echo %~dp0
    if exist data\dashboard-icon-v2.done del data\dashboard-icon-v2.done
    echo.
    echo From now on the dashboard keeps itself running in the background: it starts after you sign in
    echo and starts again within 5 minutes if it ever stops. To switch this off: dashboard-autostart-off
  )
)
rem already running: --boot has nothing to do, the icon just opens it
curl -s -o nul --max-time 2 http://127.0.0.1:8095/ && (
  if defined BOOT exit /b 0
  start "" chrome http://127.0.0.1:8095/ || start "" http://127.0.0.1:8095/
  exit /b 0
)
if not defined BOOT if exist ".venv\Scripts\python.exe" if exist "dashboard-hidden.vbs" (
  rem start it in the background - no window to close by mistake - then open it when it answers
  echo Starting the Job Hunter dashboard... this takes up to 1-2 minutes when the laptop is busy.
  echo Your browser opens by itself when it is ready. This window closes by itself.
  wscript.exe "%~dp0dashboard-hidden.vbs"
  for /l %%i in (1,1,90) do (
    timeout /t 2 /nobreak >nul
    curl -s -o nul --max-time 2 http://127.0.0.1:8095/ && (
      start "" chrome http://127.0.0.1:8095/ || start "" http://127.0.0.1:8095/
      exit /b 0
    )
  )
  echo The dashboard did not start. Is Docker Desktop running? Start it, then double-click Job Hunter again.
  pause
  exit /b 1
)
if not defined BOOT (
  echo Starting the Job Hunter dashboard... this takes up to 1-2 minutes when the laptop is busy.
  echo Your browser opens by itself when it is ready. Keep this window open.
  echo.
)
set PYTHONUTF8=1
set DATABASE_URL=postgresql+psycopg://jobhunter:jobhunter@127.0.0.1:5433/jobhunter

if defined BOOT (
  rem right after sign-in Docker Desktop needs a minute or two
  for /l %%i in (1,1,60) do (
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
if not defined BOOT pause
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
set "NEEDPIP="
fc /b requirements.txt data\packages-main.txt >nul 2>&1 || set "NEEDPIP=1"
fc /b requirements-dashboard.txt data\packages-dashboard.txt >nul 2>&1 || set "NEEDPIP=1"
if defined NEEDPIP (
  echo Installing / updating packages...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt -r requirements-dashboard.txt
  if errorlevel 1 (
    echo Installing the dashboard packages failed - see the messages above.
    if not defined BOOT pause
    exit /b 1
  )
  copy /y requirements.txt data\packages-main.txt >nul
  copy /y requirements-dashboard.txt data\packages-dashboard.txt >nul
)
echo.
echo The dashboard opens in your browser: http://127.0.0.1:8095
if not defined BOOT echo It runs only on this computer. Close this window to stop it.
if not defined BOOT goto visible
:serve
".venv\Scripts\python.exe" -m jobhunter dashboard --no-browser
rem exit code 3 = Job Hunter was updated: start the new version straight away
if errorlevel 3 if not errorlevel 4 goto serve
exit /b 0
:visible
".venv\Scripts\python.exe" -m jobhunter dashboard
if errorlevel 3 if not errorlevel 4 goto visible
pause
