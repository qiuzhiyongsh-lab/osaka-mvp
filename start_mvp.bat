@echo off
chcp 65001 >nul
title 大阪房源查询系统 - 本地版  【关掉本窗口 = 停止服务】
cd /d "%~dp0"

echo ============================================
echo   大阪房源查询系统 - 本地版 MVP
echo ============================================
echo.

set "PY="
if exist "C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY for %%I in (python.exe) do if not defined PY set "PY=%%~$PATH:I"
if not defined PY goto NOPY

echo 使用 Python：%PY%
echo 服务地址：http://127.0.0.1:8765
echo 服务日志：data\logs\server.log
echo.
echo 【重要】这个黑窗口不要关。
echo         关掉窗口 = 服务停止 = 网页上点按钮会报 "请求失败"。
echo         要停止服务时按 Ctrl+C，或直接关掉本窗口。
echo.

"%PY%" serve.py
set "RC=%ERRORLEVEL%"

echo.
echo 服务已退出（退出码 %RC%）。
if not "%RC%"=="0" echo 异常退出，请查看 data\logs\server.log
echo.
pause
exit /b %RC%

:NOPY
echo [错误] 没有找到 Python。
echo.
echo 请先安装 Python 3，或在命令行里确认 python 可用。
echo.
pause
exit /b 1
