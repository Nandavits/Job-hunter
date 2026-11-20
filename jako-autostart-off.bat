@echo off
rem Stops Jako from starting by itself when you sign in to Windows (start-jako.bat still works).
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\Jako.lnk')"
cd /d "%~dp0"
if not exist data mkdir data
echo done> data\jako-autostart.done
echo Jako will no longer start by itself. Double-click start-jako.bat whenever you want it.
pause
