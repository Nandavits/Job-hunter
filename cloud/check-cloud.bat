@echo off
cd /d "%~dp0"
docker compose exec -T web python -m jhcloud.smoke > ..\logs\cloud-check.log 2>&1
docker compose logs --tail 60 worker scheduler > ..\logs\cloud-worker.log 2>&1
type ..\logs\cloud-check.log
timeout /t 15
