@echo off
rem (Re)starts only Jako's laptop link for Iron Man. start-jako.bat does this too.
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist .venv-hands\Scripts\pythonw.exe (
  echo The laptop link is not set up yet. Run start-jako.bat first.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*jako.hands*' } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }"
powershell -NoProfile -Command "Start-Process -FilePath '.venv-hands\Scripts\pythonw.exe' -ArgumentList '-m','jako.hands' -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardError 'logs\jako-hands.log'"
timeout /t 4 /nobreak >nul
powershell -NoProfile -Command "try { $t = (Select-String -Path .env -Pattern '^JAKO_HANDS_TOKEN=(.+)$').Matches[0].Groups[1].Value.Trim(); $r = Invoke-RestMethod -Uri http://127.0.0.1:8766/status -Headers @{'X-Jako-Token'=$t} -TimeoutSec 5; 'Laptop link is running on ' + $r.computer + '. Iron Man is ready.' } catch { 'Laptop link did not start - see logs\jako-hands.log' }"
timeout /t 6 >nul
