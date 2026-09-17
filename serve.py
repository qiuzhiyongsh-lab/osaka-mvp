# -*- coding: utf-8 -*-
"""本地服务启动器（比直接跑 run.py 更耐操）。

为什么需要它
------------
网页上点按钮报 "Failed to fetch"，绝大多数情况不是代码错，而是
**本地服务已经退出了**（关掉了黑窗口、进程被结束、或启动时崩在没注意到的地方）。
这个启动器专门解决这类"莫名其妙就没了"：

  ① 把服务日志和完整堆栈写进  data/logs/server.log
  ② 捕获主线程 + 后台线程的未处理异常并落盘（不再静默死掉）
  ③ 端口被占用时给人话提示，而不是甩一堆 traceback
  ④ 服务异常退出后自动重启（最多 5 次，间隔 3 秒）

用法
----
    python serve.py              # 启动（默认带自动重启）
    python serve.py --no-restart # 崩了就退出，不重启
    python serve.py --port 8766  # 换端口

停止：在本窗口按 Ctrl+C，或直接关掉窗口。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import config as cfgmod                                  # noqa: E402

LOG_FILE = ROOT / "data" / "logs" / "server.log"
MAX_RESTART = 5
RESTART_DELAY = 3


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_log(text: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except Exception:
        pass


def say(msg: str) -> None:
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    write_log(line)


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def _free_port_via_powershell(port: int) -> bool:
    """用 PowerShell 精确结束占用端口的进程（中文系统下比 netstat 可靠）。

    返回是否有进程被清理。即便 PowerShell 不可用也安全（返回 False，靠 serve.py
    启动失败后的提示兜底）。
    """
    try:
        import subprocess
        ps = (
            "$pids=(Get-NetTCPConnection -LocalPort %d "
            "-ErrorAction SilentlyContinue).OwningProcess;"
            "if($pids){$pids|ForEach-Object{Stop-Process -Id $_ -Force "
            "-ErrorAction SilentlyContinue};'killed'}else{'none'}"
        ) % port
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=20)
        return "killed" in (r.stdout + r.stderr)
    except Exception:
        return False


def install_excepthooks() -> None:
    """让任何未处理异常都留下痕迹，而不是让进程悄悄消失。"""
    def _hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        say(f"‼ 未处理异常，服务将退出：{exc_type.__name__}: {exc}")
        write_log(text)
    sys.excepthook = _hook

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        text = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback))
        say(f"‼ 后台线程异常（{args.thread.name if args.thread else '?'}）："
            f"{args.exc_type.__name__}: {args.exc_value}")
        write_log(text)
    threading.excepthook = _thread_hook


def serve_once(host: str, port: int, extra_args: list[str]) -> None:
    from web.app import app, CFG, PATHS, SCHED

    CFG["web"]["host"] = host
    CFG["web"]["port"] = port

    say("=" * 62)
    say("  大阪房源查询系统 · 本地版  —— 服务已启动")
    say(f"  打开地址： http://{host}:{port}")
    say(f"  数据目录： {PATHS['root']}")
    say(f"  服务日志： {LOG_FILE}")
    say("  停止服务： 在本窗口按 Ctrl+C，或直接关掉本窗口")
    say("=" * 62)

    if CFG.get("schedule", {}).get("enabled") and not SCHED.running:
        SCHED.start()
        say(f"· 自动更新已启用：{SCHED.describe()}")

    # 模板热重载：debug=False 时 Flask 默认缓存编译后的模板，导致改模板不生效。
    # 显式开启后，每次请求都会按文件 mtime 重新编译，无需重启进程即可看到模板改动。
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True

    app.run(host=host, port=port, debug=False,
            use_reloader=False, threaded=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-restart", action="store_true", help="崩溃后不自动重启")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    cfg = cfgmod.load()
    host = args.host or cfg.get("web", {}).get("host", "127.0.0.1")
    port = args.port or int(cfg.get("web", {}).get("port", 8765))

    install_excepthooks()

    if _port_in_use(host, port):
        say(f"· 端口 {port} 被占用，尝试自动清理占用进程…")
        _free_port_via_powershell(port)
        time.sleep(1.5)
        if _port_in_use(host, port):
            print()
            print("=" * 62)
            print(f"  ⚠ 端口 {port} 仍被占用，且无法自动清理。")
            print()
            print(f"  先试试直接打开： http://{host}:{port}")
            print("  如果打不开，说明占用端口的是别的程序，可以换个端口启动：")
            print(f"      python serve.py --port {port + 1}")
            print("=" * 62)
            print()
            return 2
        say(f"· 已清理端口 {port}，继续启动。")

    attempts = 0
    while True:
        try:
            serve_once(host, port, [])
            say("· 服务正常退出。")
            return 0
        except KeyboardInterrupt:
            say("· 收到停止信号（Ctrl+C），服务已关闭。")
            return 0
        except SystemExit as e:
            say(f"· 服务退出（code={e.code}）。")
            return 0 if not e.code else int(e.code)
        except OSError as e:
            say(f"✗ 启动失败（系统错误）：{e}")
            return 1
        except BaseException as e:                                # noqa: BLE001
            say(f"✗ 服务崩溃：{type(e).__name__}: {e}")
            write_log(traceback.format_exc())
            if args.no_restart or attempts >= MAX_RESTART:
                say("· 已达重启上限或已禁用重启，进程结束。请查看上面的日志。")
                return 1
            attempts += 1
            say(f"· {RESTART_DELAY} 秒后自动重启（第 {attempts}/{MAX_RESTART} 次）…")
            time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
