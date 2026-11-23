@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
echo ============================================
echo   Job Hunter - setup and first job search
echo ============================================
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start Docker Desktop, wait until it says "running", then double-click this file again.
  echo DOCKER_NOT_RUNNING > logs\setup.log
  pause
  exit /b 1
)
echo [1/3] Building and starting containers (first time takes 5-10 minutes)...
docker compose up -d --build db jobhunter > logs\setup.log 2>&1
if errorlevel 1 (
  echo Build failed - see logs\setup.log
  echo BUILD_FAILED >> logs\setup.log
  pause
  exit /b 1
)
rem restart the scheduler so changed settings (e.g. every_minutes) take effect
docker compose restart jobhunter >nul 2>&1
echo [2/3] Checking company job boards...
docker compose run --rm jobhunter python -m jobhunter check-sources > logs\sources.txt 2>&1
echo Running the job search...
docker compose run --rm jobhunter python -m jobhunter run --no-digest > logs\first-run.log 2>&1
echo [3/3] Jobs found:
docker compose run --rm jobhunter python -m jobhunter list > logs\jobs.txt 2>&1
type logs\jobs.txt
echo.
echo DONE >> logs\setup.log
echo Finished. Job Hunter now checks for new jobs every 30 minutes while Docker Desktop is running.
pause
