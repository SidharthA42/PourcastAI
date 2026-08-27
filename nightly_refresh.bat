@echo off
REM ============================================================================
REM  PourCastAI - nightly CRM refresh
REM  Regenerates fresh (date-seeded) inventory + shipments, then re-seeds HubSpot.
REM  Use with n8n (Execute Command -> call this file) OR Windows Task Scheduler.
REM  Edit PROJECT if your repo lives somewhere other than D:\pourcast-ai
REM ============================================================================

set "PROJECT=D:\pourcast-ai"
set "PY=%PROJECT%\.venv\Scripts\python.exe"
set "LOG=%PROJECT%\logs\nightly_%date:~-4%%date:~4,2%%date:~7,2%.log"

if not exist "%PROJECT%\logs" mkdir "%PROJECT%\logs"

cd /d "%PROJECT%"

echo ==================================================== >> "%LOG%"
echo  Nightly refresh started %date% %time%              >> "%LOG%"
echo ==================================================== >> "%LOG%"

REM 1) Fresh data every night (date-based RNG seed, not the fixed seed 42)
set "SIMULATE_SEED=daily"
echo [1/2] Simulating fresh data...                      >> "%LOG%"
"%PY%" simulate.py                                        >> "%LOG%" 2>&1
if errorlevel 1 (
    echo  SIMULATE FAILED - aborting, HubSpot not touched. >> "%LOG%"
    exit /b 1
)

REM 2) Push the new open shipments into HubSpot (idempotent upsert)
echo [2/2] Seeding HubSpot...                            >> "%LOG%"
"%PY%" hubspot_seed.py                                    >> "%LOG%" 2>&1
if errorlevel 1 (
    echo  HUBSPOT SEED FAILED.                            >> "%LOG%"
    exit /b 1
)

echo  Nightly refresh finished OK %date% %time%          >> "%LOG%"
exit /b 0
