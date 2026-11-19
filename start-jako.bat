@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start it, wait until it says "running", then double-click this file again.
  pause
  exit /b 1
)
echo Starting Jako (the first time takes 5-10 minutes)...
docker compose up -d --build jako > logs\jako-setup.log 2>&1
if errorlevel 1 (
  echo Starting Jako failed - see logs\jako-setup.log
  pause
  exit /b 1
)
timeout /t 5 /nobreak >nul
start "" "http://localhost:8765"
echo Jako is running. The page opens in your browser: click the circle once, then say "Hey Jako ...".
echo Jako keeps running in the background while Docker Desktop is running.
pause
