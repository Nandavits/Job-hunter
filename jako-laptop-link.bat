@echo off
rem (Re)starts only Jako's laptop link for Iron Man. start-jako.bat does this too.
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist .venv-hands\Scripts\pythonw.exe (
  echo The laptop link is not set up yet. Run start-jako.bat first.
  pause
  exit /b 1
)
fc /b requirements-hands.txt .venv-hands\installed.txt >nul 2>&1 || (
  echo Installing new parts for the laptop link...
  .venv-hands\Scripts\python -m pip install -q --disable-pip-version-check -r requirements-hands.txt > logs\jako-hands-setup.log 2>&1 && copy /y requirements-hands.txt .venv-hands\installed.txt >nul
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*jako.hands*' } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }"
powershell -NoProfile -Command "Start-Process -FilePath '.venv-hands\Scripts\pythonw.exe' -ArgumentList '-m','jako.hands' -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardError 'logs\jako-hands.log'"
timeout /t 4 /nobreak >nul
powershell -NoProfile -Command "if (Test-Path logs\jako-hands-run.log) { Get-Content logs\jako-hands-run.log -Tail 1 } else { 'Laptop link started.' }"
timeout /t 5 >nul
