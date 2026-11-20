@echo off
rem Turns Jako off until you open it again (desktop "Jako" icon, open-jako.bat or start-jako.bat).
cd /d "%~dp0"
if not exist data mkdir data
echo stopped> data\jako-stopped.flag
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*jako-window*' -or $_.CommandLine -like '*jako.hands*' } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }"
docker compose stop jako >nul 2>&1
echo Jako is off. Double-click the Jako icon on your desktop to start it again.
timeout /t 5 >nul
