@echo off
chcp 65001 >nul
title STEP1 - Refresh apartment details from REINS
cd /d "%~dp0"

echo ==========================================================
echo   STEP 1 : re-fetch 729 pseudo-details + 6 missing from REINS
echo   Time window : 07:00-23:00 Japan time only
echo   Keep the 8765 black window CLOSED while this runs.
echo ==========================================================
echo.

rem --- guard: 8765 must not run (shares data\session.json) ---
netstat -ano | findstr ":8765" | findstr "LISTENING" >nul
if %errorlevel%==0 (
  echo [STOP] Port 8765 is still in use.
  echo        Close the 8765 black window ^(start_mvp.bat^) first,
  echo        then double-click this file again.
  echo.
  pause
  exit /b 1
)
echo [OK] 8765 is not running. Good.
echo.

set "PY=C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo Using python: %PY%
echo.

rem --- v1.9.88 backfill address: clear old resume point, force full re-fetch ---
if exist tools\_refresh_done_nos.txt del /F tools\_refresh_done_nos.txt
echo [1/2] Refreshing 729 pseudo-details (re-fetch to BACKFILL ADDRESS) ...
"%PY%" tools\refresh_by_no.py --file tools\_pseudo_detail_nos.txt --no-pdf --resume
echo.
echo [2/2] Refreshing 6 missing listings (re-fetch to BACKFILL ADDRESS) ...
"%PY%" tools\refresh_by_no.py --file tools\_missing_6_nos.txt --no-pdf --resume
echo.
echo ==========================================================
echo   Finished. Please screenshot the LAST 3 LINES and send
echo   the screenshot to Xiao Ba.
echo ==========================================================
pause
