@echo off
REM ---- osaka-mvp hourly snapshot: switch to windowless mode (pythonw) ----
echo ============================================
echo  osaka-mvp: switch to WINDOWLESS mode
echo --------------------------------------------
echo  pythonw.exe instead of python.exe
echo  => mei xiao shi bu zai dan hei chuang kou
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

echo [1/2] change task command to pythonw ...
%SystemRoot%\System32\schtasks.exe /Change /TN "osaka_hotsnap" /TR "\"%PYW%\" \"%SCRIPT%\""
if errorlevel 1 (
    echo Change failed - recreate task ...
    %SystemRoot%\System32\schtasks.exe /Delete /TN "osaka_hotsnap" /F
    %SystemRoot%\System32\schtasks.exe /Create /TN "osaka_hotsnap" /SC HOURLY /MO 1 /TR "\"%PYW%\" \"%SCRIPT%\"" /F
)

echo.
echo [2/2] result:
%SystemRoot%\System32\schtasks.exe /Query /TN "osaka_hotsnap" /FO LIST
echo.
echo log file: C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp\.backups\hot\snapshot.log
echo.
pause
