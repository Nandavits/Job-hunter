@echo off
rem Starts Jako. Double-click it once; after that Windows starts Jako by itself when you sign in.
rem start-jako.bat --boot  = quiet start at sign-in (used by the Startup shortcut)
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist data mkdir data
set "BOOT="
if /i "%~1"=="--boot" set "BOOT=1"
if exist data\jako-stopped.flag del data\jako-stopped.flag

rem --- 1. Secret key for the laptop link (created once, stays in .env) ---
findstr /b "JAKO_HANDS_TOKEN=" .env >nul 2>&1
if errorlevel 1 powershell -NoProfile -Command "Add-Content -Path .env -Value ([Environment]::NewLine + 'JAKO_HANDS_TOKEN=' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))"

rem --- 2. First run only: start with Windows, and the Jako wallpaper ---
if not defined BOOT if not exist data\jako-autostart.done (
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Jako.lnk'); $s.TargetPath='%~f0'; $s.Arguments='--boot'; $s.WorkingDirectory='%CD%'; $s.WindowStyle=7; $s.Description='Start Jako'; $s.Save()"
  echo done> data\jako-autostart.done
  echo Jako will now start by itself when you sign in to Windows. To stop that, run jako-autostart-off.bat.
)
if not exist data\jako-shortcut.done (
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Jako.lnk'); $s.TargetPath='%CD%\open-jako.bat'; $s.WorkingDirectory='%CD%'; $s.WindowStyle=7; $s.IconLocation='%SystemRoot%\System32\shell32.dll,13'; $s.Description='Open Jako'; $s.Save()"
  echo done> data\jako-shortcut.done
)
if not defined BOOT if not exist data\jako-wallpaper.done (
  powershell -NoProfile -ExecutionPolicy Bypass -File jako\set-wallpaper.ps1 "%CD%\jako\static\wallpaper.jpg"
  echo done> data\jako-wallpaper.done
)

rem --- 3. Jako's hands: lets Iron Man use this laptop (runs on Windows, not in Docker) ---
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python is not installed, so Iron Man cannot use the laptop yet.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this file again.
  goto docker
)
if not exist .venv-hands\Scripts\pythonw.exe (
  echo Setting up the laptop link ^(first time only^)...
  %PY% -m venv .venv-hands
)
fc /b requirements-hands.txt .venv-hands\installed.txt >nul 2>&1 || (
  .venv-hands\Scripts\python -m pip install -q --disable-pip-version-check -r requirements-hands.txt > logs\jako-hands-setup.log 2>&1 && copy /y requirements-hands.txt .venv-hands\installed.txt >nul
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*jako.hands*' } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }"
powershell -NoProfile -Command "Start-Process -FilePath '.venv-hands\Scripts\pythonw.exe' -ArgumentList '-m','jako.hands' -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardError 'logs\jako-hands.log'"

:docker
rem --- 4. Jako and the team (Docker). At sign-in Docker needs a minute, so wait for it. ---
docker info >nul 2>&1
if not errorlevel 1 goto dockerok
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
echo Waiting for Docker Desktop...
for /l %%i in (1,1,60) do (
  timeout /t 5 /nobreak >nul
  docker info >nul 2>&1 && goto dockerok
)
echo Docker Desktop did not start. Start it, wait until it says "running", then double-click this file again.
if not defined BOOT pause
exit /b 1

:dockerok
echo Starting Jako and the team (the first time takes 5-10 minutes)...
docker compose up -d --build jako > logs\jako-setup.log 2>&1
if errorlevel 1 (
  echo Starting Jako failed - see logs\jako-setup.log
  if not defined BOOT pause
  exit /b 1
)
rem wait until Jako answers, then open its window
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://localhost:8765/api/health -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" && goto jakoup
  timeout /t 2 /nobreak >nul
)
:jakoup
call :openjako
call :livedesktop
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

:livedesktop
rem Live desktop: start Lively Wallpaper (if installed) and show the Jako live desktop.
set "LIVELY="
for %%P in ("%ProgramFiles%\Lively Wallpaper\Lively.exe" "%LOCALAPPDATA%\Programs\Lively Wallpaper\Lively.exe" "%ProgramFiles(x86)%\Lively Wallpaper\Lively.exe") do if exist "%%~P" set "LIVELY=%%~P"
if not defined LIVELY exit /b 0
set "LIB=%LOCALAPPDATA%\Lively Wallpaper\Library\wallpapers\jako-desktop"
if not exist "%LIB%\index.html" (
  if not exist "%LIB%" mkdir "%LIB%"
  xcopy /y /q "jako\desktop\*" "%LIB%\" >nul
)
tasklist /fi "imagename eq Lively.exe" | find /i "Lively.exe" >nul || (start "" "%LIVELY%" & timeout /t 15 /nobreak >nul)
"%LIVELY%" setwp --file "%LIB%"
exit /b 0
