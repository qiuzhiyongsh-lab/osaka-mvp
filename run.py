# -*- coding: utf-8 -*-
"""大阪房产软件 · 本地版 MVP —— 一个入口搞定所有事。

用法（在本目录下执行）：
    python run.py            # 启动本地网页（推荐，之后所有操作都在网页上点）
    python run.py login      # 手工登录 REINS（只需一次，保存会话）
    python run.py run        # 不开网页，直接跑一轮抓取
    python run.py status     # 看当前配置与本地数据概况
    python run.py reset      # 清空本地数据（会二次确认）

前提：Python 3.10+ ；pip install -r requirements.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import config as cfgmod           # noqa: E402
from core.store import Store                # noqa: E402


def cmd_serve():
    import web.app as webapp
    webapp.app.run(host=webapp.CFG["web"]["host"],
                   port=int(webapp.CFG["web"]["port"]),
                   debug=False, use_reloader=False, threaded=True)


def cmd_login():
    from core.auth import Auth, PlaywrightMissing
    cfg = cfgmod.load()
    paths = cfgmod.paths(cfg)
    try:
        ok = Auth(cfg, paths["session"]).manual_login(wait_seconds=300, interactive=True)
    except PlaywrightMissing as e:
        print(e)
        return
    print("✓ 登录成功，会话已保存" if ok else "✗ 未确认登录成功")


def cmd_run(scale: float = 0.25):
    from core.crawler import run_round
    cfg = cfgmod.load()
    paths = cfgmod.paths(cfg)
    store = Store(paths["db"])
    res = run_round(store, cfg, trigger="manual", progress_cb=print, scale=scale)
    print("\n结果：", {k: res[k] for k in ("mode", "scanned", "fetched", "new", "changed", "status")})
    if res["errors"]:
        print("错误：", res["errors"][:5])


def cmd_status():
    cfg = cfgmod.load()
    paths = cfgmod.paths(cfg)
    store = Store(paths["db"])
    s = store.stats()
    print(f"模式          : {cfg['app']['mode']}")
    print(f"落盘根目录    : {paths['root']}")
    print(f"数据库        : {paths['db']}")
    print(f"会话文件      : {'已存在' if paths['session'].exists() else '未创建'}")
    print(f"房源总数      : {s['total']}   今日新盘: {s['today_new']}   今日降价: {s['today_down']}")
    print(f"已落盘 PDF    : {s['with_pdf']}")
    sc = cfg.get("schedule", {})
    print(f"调度          : enabled={sc.get('enabled')} mode={sc.get('mode')} "
          f"interval={sc.get('interval_hours')}h random={sc.get('random_min_hours')}-"
          f"{sc.get('random_max_hours')}h window={sc.get('window')}")


def cmd_reset():
    cfg = cfgmod.load()
    paths = cfgmod.paths(cfg)
    print(f"⚠ 将删除本地数据：{paths['db']} 与 {paths['root']} 下的 attachments/exports/daily")
    if input("确认请输入 DELETE：").strip() != "DELETE":
        print("已取消")
        return
    for p in [paths["db"]] + list(paths["attachments"].glob("*")) + \
             list(paths["exports"].glob("*")) + list(paths["daily"].glob("*")):
        try:
            Path(p).unlink()
        except Exception:
            pass
    print("✓ 已清空")


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "serve").lower()
    if cmd in ("serve", "web", "start"):
        cmd_serve()
    elif cmd == "login":
        cmd_login()
    elif cmd in ("run", "once"):
        cmd_run()
    elif cmd == "status":
        cmd_status()
    elif cmd == "reset":
        cmd_reset()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
