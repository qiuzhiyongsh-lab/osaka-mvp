@echo off
REM ---- osaka-mvp hourly snapshot: create / repair (windowless, pythonw) ----
REM ---- idempotent: safe to run again and again ----
REM ---- NOTE: never use ">" inside echo text (cmd treats it as redirection) ----
echo ============================================
echo  osaka-mvp hourly snapshot  (WINDOWLESS)
echo --------------------------------------------
echo  pythonw.exe : no black window every hour
echo  log file    : .backups hot snapshot.log
echo ============================================
echo.

set PYW=C:\Users\25374\AppData\Local\Programs\Python\Python313\pythonw.exe
set SCRIPT=C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp\tools\hourly_hot_snapshot.py

if not exist "%PYW%" (
    echo [ERROR] pythonw not found: %PYW%
    pause
    exit /b 1
)
if not exist "%SCRIPT%" (
    echo [ERROR] script not found: %SCRIPT%
    pause
    exit /b 1
)

echo [1/3] remove old task (if any) ...
%SystemRoot%\System32\schtasks.exe /Delete /TN "osaka_hotsnap" /F

echo [2/3] create task with pythonw ...
%SystemRoot%\System32\schtasks.exe /Create /TN "osaka_hotsnap" /SC HOURLY /MO 1 /TR "\"%PYW%\" \"%SCRIPT%\"" /F
if errorlevel 1 (
    echo.
    echo [FAILED] could not create task. Try as Administrator.
    pause
    exit /b 1
)

echo.
echo [3/3] result:
%SystemRoot%\System32\schtasks.exe /Query /TN "osaka_hotsnap" /FO LIST
echo.
echo done. Ready / jiu xu means success.
echo.
pause
