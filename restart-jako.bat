@echo off
rem Restarts Jako after an update: the Jako server, Iron Man's laptop link and the Jako window.
cd /d "%~dp0"
if not exist logs mkdir logs
echo Restarting Jako...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*jako-window*' } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }"
docker compose restart jako > logs\jako-restart.log 2>&1
call jako-laptop-link.bat
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://localhost:8765/api/health -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" && goto up
  timeout /t 2 /nobreak >nul
)
:up
call :openjako
exit /b 0

:openjako
if defined BOOT exit /b 0
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
start "" "%BROWSER%" --app=http://localhost:8765 --user-data-dir="%CD%\data\jako-window" --autoplay-policy=no-user-gesture-required --start-maximized --no-first-run --no-default-browser-check
exit /b 0
