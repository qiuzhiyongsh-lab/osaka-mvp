# -*- coding: utf-8 -*-
"""本地服务启动器（比直接跑 run.py 更耐操）。

为什么需要它
------------
网页上点按钮报 "Failed to fetch"，绝大多数情况不是代码错，而是
**本地服务已经退出了**（关掉了黑窗口、进程被结束、或启动时崩在没注意到的地方）。
这个启动器专门解决这类"莫名其妙就没了"：

  ① 把服务日志和完整堆栈写进  data/logs/server.log
  ② 捕获主线程 + 后台线程的未处理异常并落盘（不再静默死掉）
  ③ 端口被占用时提示「已有实例在跑」并直接退出，绝不互杀抢端口
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


def _run_server_dual_stack(app, host: str, port: int) -> None:
    """启动 Flask 服务：IPv4 主监听 + 尽力补 IPv6 回环监听（双栈）。

    为什么双栈：本机 `localhost` 通常解析成 `::1, 127.0.0.1`（IPv6 优先）。
    旧实现只 `app.run(host="127.0.0.1")` 绑 IPv4，导致用 `localhost` 访问时
    Edge 先连 ::1 被拒、回退 IPv4 不如 Chrome 稳 → "无法访问"；Chrome 用
    Happy Eyeballs 回退 127.0.0.1 正常。补 [::1] 监听后，`localhost` 在
    Edge/Chrome 都通。IPv6 绑定失败（极少数环境）不影响 IPv4 主监听。
    """
    from werkzeug.serving import make_server

    ipv4 = make_server(host or "127.0.0.1", port, app, threaded=True)
    ipv4.daemon_threads = True

    ipv6 = None
    try:
        ipv6 = make_server("::1", port, app, threaded=True)
        ipv6.daemon_threads = True
    except Exception as e:                                # noqa: BLE001
        say(f"· IPv6 回环监听跳过（[::1]:{port} 绑定失败，不影响 IPv4）："
            f"{type(e).__name__}: {e}")

    if ipv6 is not None:
        threading.Thread(target=ipv6.serve_forever, daemon=True,
                         name="flask-ipv6").start()
        say(f"· 双栈监听：127.0.0.1:{port} + [::1]:{port}"
            f"（localhost 在 Edge/Chrome 均可用）")

    try:
        ipv4.serve_forever()
    finally:
        ipv4.shutdown()
        ipv4.server_close()
        if ipv6 is not None:
            try:
                ipv6.shutdown()
                ipv6.server_close()
            except Exception:
                pass


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

    # v1.9.28：启动后立刻做一次**账号双向同步**（推最新种子上云 + 把员工在线上自设的密码回流）。
    #   为什么放启动时：这是"线上和本地一定一致"的最强保证 —— 即便上次改账号时网络断了，
    #   或线上容器被重建回落成旧种子，重启本地服务就自动对齐一次。
    #   放后台线程：不拖慢启动，也不影响"服务已启动"的提示；失败只记日志。
    def _boot_account_sync() -> None:
        try:
            time.sleep(3)
            from core import account_sync as accsync
            res = accsync.sync_now(CFG, log=say)
            say("· 账号同步（启动）：%s" % ("已对齐" if res.get("ok") else "未完成（见上）"))
        except Exception as e:                                # noqa: BLE001
            say(f"· 账号同步（启动）跳过：{type(e).__name__}: {e}")

    threading.Thread(target=_boot_account_sync, daemon=True,
                     name="account-sync-boot").start()

    # v1.9.64（2026-09-23 日志复核发现的 P0）：本地常驻线程必须由**启动器**拉起。
    #   原因：start_mvp.bat 走本文件（serve.py → import web.app），web/app.py 的
    #   `if __name__ == "__main__"` 块在这条启动路径下**根本不执行**——
    #   v1.9.61 的「PDF 自动重签」线程因此从未运行过（07:21 启动日志无
    #   「自动重签线程已启动」行实锤），v1.9.63 的「AI 接力」线程也会同样落空。
    #   两个 start 函数都带幂等守卫（_RESIGN_LOOP_STARTED / _AI_RELAY_STARTED），
    #   与 __main__ 直跑路径谁先到谁生效，绝不双跑。
    import web.app as _W
    if not _W.PUBLIC:
        _W._start_pdf_resign_loop()
        _W._start_ai_relay_loop()
        # v1.9.65：下载轮新 PDF 自动上云（取到数据 → PDF 同时上云 → 回推线上）
        _W._enable_pdf_cloud_auto()

    # 模板热重载：debug=False 时 Flask 默认缓存编译后的模板，导致改模板不生效。
    # 显式开启后，每次请求都会按文件 mtime 重新编译，无需重启进程即可看到模板改动。
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True

    _run_server_dual_stack(app, host, port)


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
        # v1.9.31：端口被占用 → 提示「已有实例在跑」并直接退出，绝不再 kill 互杀。
        #   以前这段会 PowerShell 杀掉占用 8765 的进程再启动；一旦系统里存在多个
        #   启动入口（运行面板 / 计划任务 / 手动），它们就会轮流把对方打死，
        #   表现为「没操作却自己断、要按继续」。
        #   现在改为：检测到已有实例就友好提示并退出，把端口留给存活的实例。
        print()
        print("=" * 62)
        print(f"  ⚠ 端口 {port} 已被占用。")
        print()
        print("  这说明本机已经有一个本地服务实例在运行（多半就是上一次启动的）。")
        print("  为避免两个实例抢端口互相打死，本次启动【不重复拉起、也不杀旧实例】。")
        print()
        print(f"  请直接使用现有实例： http://{host}:{port}")
        print("  如果你确实想重启（例如改了代码），请先手动结束旧实例，再重新运行：")
        print(f"      · 结束占用进程：")
        print(f"          Stop-Process -Id (Get-NetTCPConnection \\")
        print(f"                            -LocalPort {port}).OwningProcess -Force")
        print(f"      · 重新启动：     python serve.py")
        print("=" * 62)
        print()
        return 0

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
