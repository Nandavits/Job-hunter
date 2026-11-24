@echo off
rem Stops the Job Hunter dashboard from starting by itself when you sign in (dashboard.bat still works).
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\Job Hunter dashboard.lnk')"
cd /d "%~dp0"
if not exist data mkdir data
echo done> data\dashboard-autostart.done
echo The dashboard will no longer start by itself. Use the "Job Hunter" icon on your desktop instead.
pause
