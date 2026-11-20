@echo off
rem Opens the Jako window (the desktop "Jako" icon). Starts Jako first if it isn't running.
cd /d "%~dp0"
if exist data\jako-stopped.flag del data\jako-stopped.flag
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://localhost:8765/api/health -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  call start-jako.bat --boot
  exit /b 0
)
powershell -NoProfile -Command "if (-not (Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*jako.hands*' })) { exit 1 }"
if errorlevel 1 call jako-laptop-link.bat
call :openjako
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
start "" "%BROWSER%" --app=http://localhost:8765 --user-data-dir="%CD%\data\jako-window" --autoplay-policy=no-user-gesture-required --start-maximized --no-first-run --no-default-browser-check
exit /b 0

