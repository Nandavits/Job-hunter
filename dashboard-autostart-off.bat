@echo off
rem Stops the Job Hunter dashboard from starting by itself (the desktop icon still works).
cd /d "%~dp0"
schtasks /delete /f /tn "Job Hunter dashboard" >nul 2>&1
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\Job Hunter dashboard.lnk')"
if not exist data mkdir data
echo off> data\dashboard-autostart-off.flag
echo The dashboard will no longer start by itself. Use the "Job Hunter" icon on your desktop instead.
echo To switch it back on: delete data\dashboard-autostart-off.flag, then double-click Job Hunter.
pause
