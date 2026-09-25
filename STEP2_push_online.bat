@echo off
chcp 65001 >nul
title STEP2 - Push all data to the online site
cd /d "%~dp0"

echo ==========================================================
echo   STEP 2 : push ALL rows to the online site (full re-push)
echo   This is the only working channel for DATA to go online.
echo   It is NOT a re-deploy: no code is transferred.
echo ==========================================================
echo.

set "PY=C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo Using python: %PY%
echo.

"%PY%" tools\force_full_push.py
echo.
echo ==========================================================
echo   Success mark : RESULT_OK   (e.g. sent=5304  total=5304 ai=True)
echo   If you see RESULT_FAIL or an empty endpoint -> check network,
echo   then double-click this file again.
echo ==========================================================
pause
