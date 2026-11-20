@echo off
rem Makes Jako your live, animated desktop. Uses Lively Wallpaper (free, open source: github.com/rocksdanister/lively).
rem To stop it: right-click the Lively icon near the clock > Close wallpapers, or uninstall Lively.
cd /d "%~dp0"
call :findlively
if not defined LIVELY (
  echo Installing Lively Wallpaper - if Windows asks for permission, click Yes...
  winget install --id rocksdanister.LivelyWallpaper -e --silent --accept-package-agreements --accept-source-agreements
  call :findlively
)
if not defined LIVELY (
  echo Lively Wallpaper could not be installed automatically.
  echo Get it free from the Microsoft Store ^(search "Lively Wallpaper"^), then run this file again.
  pause
  exit /b 1
)
set "LIB=%LOCALAPPDATA%\Lively Wallpaper\Library\wallpapers\jako-desktop"
if not exist "%LIB%" mkdir "%LIB%"
xcopy /y /q "jako\desktop\*" "%LIB%\" >nul
echo Starting Lively...
start "" "%LIVELY%"
timeout /t 15 /nobreak >nul
"%LIVELY%" setwp --file "%LIB%"
echo.
echo Done: Jako is now your live desktop. Minimise your windows to see it.
echo It shows the still Jako picture until Jako is running, then switches to the live view by itself.
timeout /t 8
exit /b 0

:findlively
set "LIVELY="
for %%P in ("%ProgramFiles%\Lively Wallpaper\Lively.exe" "%LOCALAPPDATA%\Programs\Lively Wallpaper\Lively.exe" "%ProgramFiles(x86)%\Lively Wallpaper\Lively.exe") do if exist "%%~P" set "LIVELY=%%~P"
exit /b 0
