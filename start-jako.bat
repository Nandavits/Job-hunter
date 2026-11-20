@echo off
cd /d "%~dp0"
if not exist logs mkdir logs

rem --- 1. Secret key for the laptop link (created once, stays in .env) ---
findstr /b "JAKO_HANDS_TOKEN=" .env >nul 2>&1
if errorlevel 1 powershell -NoProfile -Command "Add-Content -Path .env -Value ([Environment]::NewLine + 'JAKO_HANDS_TOKEN=' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))"

rem --- 2. Jako's hands: lets Iron Man use this laptop (runs on Windows, not in Docker) ---
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python is not installed, so Iron Man cannot use the laptop yet.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this file again.
  echo Jako and the rest of the team still start now.
  goto docker
)
if not exist .venv-hands\Scripts\pythonw.exe (
  echo Setting up the laptop link ^(first time only^)...
  %PY% -m venv .venv-hands
)
.venv-hands\Scripts\python -m pip install -q --disable-pip-version-check -r requirements-hands.txt > logs\jako-hands-setup.log 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*jako.hands*' } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }"
powershell -NoProfile -Command "Start-Process -FilePath '.venv-hands\Scripts\pythonw.exe' -ArgumentList '-m','jako.hands' -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardError 'logs\jako-hands.log'"

:docker
rem --- 3. Jako and the team (Docker) ---
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start it, wait until it says "running", then double-click this file again.
  pause
  exit /b 1
)
echo Starting Jako and the team (the first time takes 5-10 minutes)...
docker compose up -d --build jako > logs\jako-setup.log 2>&1
if errorlevel 1 (
  echo Starting Jako failed - see logs\jako-setup.log
  pause
  exit /b 1
)
timeout /t 5 /nobreak >nul
call :openjako
echo.
echo Jako is running in its own window. Say "Hello Jako, wake up" or clap twice.
echo The very first time, allow the microphone once. After that Jako starts listening by itself.
echo Iron Man can use this laptop while this computer is on. Changes always wait for your "yes".
pause
exit /b 0

:openjako
rem Own window, own profile: Jako may speak without a click, and the microphone choice is remembered.
set "BROWSER="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "BROWSER=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER (
  start "" "http://localhost:8765"
  exit /b 0
)
start "" "%BROWSER%" --app=http://localhost:8765 --user-data-dir="%CD%\data\jako-window" --autoplay-policy=no-user-gesture-required --window-size=1200,860 --no-first-run --no-default-browser-check
exit /b 0
