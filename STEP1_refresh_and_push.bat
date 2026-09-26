@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  大阪房源：补 729 条伪详情地址 + 强制全量推线上
REM  适用：REINS 维护窗口结束后（日本 07:00 后 = 本机 06:00 后）
REM  流程：① 手动关 8765 → ② 补抓 → ③ 手动重启 8765 → ④ 推线上
REM  注意：8765 的开关由你手动做（AI 不 taskkill）；脚本只跑两条 python
REM  python 用 managed venv（已装 playwright + requests）
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo   大阪房源  补 729 条伪详情地址 + 推送线上
echo   请在 REINS 维护结束后运行：日本 07:00 后 = 本机 06:00 后
echo ============================================================
echo.

echo 【步骤 1/4】请先【手动关闭本地 8765】
echo   （用 start_mvp 的关闭方式，不要 taskkill）
echo   关闭后按任意键继续补抓...
pause >nul

echo.
echo 【步骤 2/4】开始补抓 729 条（会弹 Edge 窗口）
echo   若 REINS 会话失效，窗口会让你手工登录，登完自动继续
echo   别动鼠标，等它跑完（约数十分钟）
echo   已成功番号记到 _refresh_done_nos.txt，中断后加 --resume 续跑
echo.
"C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe" tools\refresh_by_no.py --file tools\_pseudo_detail_nos.txt --no-pdf --resume
echo.
echo 补抓结束，看上方 成功/跳过/失败 计数。
echo.

echo 【步骤 3/4】请【手动重启 8765】（双击 start_mvp.bat）
echo   让 v1.9.88 全角地址解析生效，并重新载入补好的数据
echo   重启后按任意键继续推送线上...
pause >nul

echo.
echo 【步骤 4/4】强制全量推线上（离线直推，不需 8765 在线）
echo.
"C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe" tools\force_full_push.py
echo.
echo ============================================================
echo   全部完成！线上已含 729 条地址更新
echo   去线上站查一条之前空地址的房源验证即可
echo ============================================================
echo.
pause
