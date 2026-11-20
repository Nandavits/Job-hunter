@echo off
rem Opens the Jako window (the desktop "Jako" icon). Starts Jako first if it isn't running.
cd /d "%~dp0"
if exist data\jako-stopped.flag del data\jako-stopped.flag
curl -s -m 2 -o nul http://localhost:8765/api/health
if errorlevel 1 (
  call start-jako.bat --boot
  exit /b 0
)
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

