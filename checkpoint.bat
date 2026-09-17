@echo off
chcp 65001 >nul
title 存档检查点（AI 改代码前先跑） - 大阪房源查询系统
cd /d "%~dp0"

echo ============================================
echo   存档检查点 = 备份房源库 + git 提交当前改动
echo ============================================
echo.

set "PY=C://Users//25374//.workbuddy//binaries//python//envs//default//Scripts//python.exe"
if exist "%PY%" goto RUN
set "PY=python"
:RUN
"%PY%" checkpoint.py %*

echo.
echo 常用命令：
echo   看所有检查点   checkpoint.bat --list
echo   看未存档改动   checkpoint.bat --diff
echo   回到上一个     checkpoint.bat --restore HEAD~1
echo   存档并写说明   checkpoint.bat "这次改了什么"
echo.
pause
