@echo off
rem Restarts Jako after an update, without opening a second window. The open Jako window keeps working.
cd /d "%~dp0"
echo Restarting Jako...
docker compose restart jako > logs\jako-restart.log 2>&1
call jako-laptop-link.bat
