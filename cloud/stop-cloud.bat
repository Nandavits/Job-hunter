@echo off
cd /d "%~dp0"
docker compose down
echo Stopped. Your data is kept; start-cloud starts it again.
timeout /t 5
