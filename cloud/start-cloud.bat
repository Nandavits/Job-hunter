@echo off
cd /d "%~dp0"
if not exist ..\logs mkdir ..\logs
echo ============================================
echo   Job Hunter Cloud - start (first time: 5-10 minutes)
echo ============================================
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start it, wait until it says running, then double-click this again.
  echo DOCKER_NOT_RUNNING > ..\logs\cloud-start.log
  pause
  exit /b 1
)
if not exist .env (
  echo Missing .env - copy .env.example to .env and fill SECRET_KEY and POSTGRES_PASSWORD.
  echo NO_ENV > ..\logs\cloud-start.log
  pause
  exit /b 1
)
echo Building and starting...
docker compose up -d --build > ..\logs\cloud-start.log 2>&1
if errorlevel 1 (
  echo BUILD_FAILED >> ..\logs\cloud-start.log
  echo Build failed - see logs\cloud-start.log
  pause
  exit /b 1
)
echo Waiting for the website...
timeout /t 15 >nul
docker compose ps >> ..\logs\cloud-start.log 2>&1
echo Running the end-to-end check...
docker compose exec -T web python -m jhcloud.smoke > ..\logs\cloud-check.log 2>&1
type ..\logs\cloud-check.log
echo DONE >> ..\logs\cloud-start.log
echo.
echo Open http://127.0.0.1:8000 in your browser.
timeout /t 20
