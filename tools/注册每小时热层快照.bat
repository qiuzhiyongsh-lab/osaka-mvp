@echo off
REM ---- osaka-mvp hourly hot snapshot / register to Windows Task Scheduler ----
echo ============================================
echo  osaka-mvp hourly hot snapshot - register
echo --------------------------------------------
echo  zuo yong: mei xiao shi dui employee.db + ai_pdf_store.db
echo            zuo yi ci yuan zi kuai zhao (VACUUM INTO),
echo            bu ting ji / bu ying xiang 8765
echo ============================================
echo.

set PY=C:\Users\25374\AppData\Local\Programs\Python\Python313\python.exe
set SCRIPT=C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp\tools\hourly_hot_snapshot.py

if not exist "%PY%" (
    echo [ERROR] python not found: %PY%
    pause
    exit /b 1
)
if not exist "%SCRIPT%" (
    echo [ERROR] script not found: %SCRIPT%
    pause
    exit /b 1
)

%SystemRoot%\System32\schtasks.exe /Create /TN "osaka_hotsnap" /SC HOURLY /MO 1 /TR "\"%PY%\" \"%SCRIPT%\"" /F

echo.
echo ---- jie guo fu he (result) ----
%SystemRoot%\System32\schtasks.exe /Query /TN "osaka_hotsnap" /FO LIST
echo.
echo kan dao "zhuang tai: yi zhun jiu xu" huo "Ready" ji cheng gong.
echo che xiao:  schtasks /Delete /TN "osaka_hotsnap" /F
echo.
pause
