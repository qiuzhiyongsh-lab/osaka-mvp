# -*- coding: utf-8 -*-
"""手动触发「今日日期同步」（v1.9.86 根治价格漂移的落地入口）。

背景：sync_today_dates 是自包含函数（内部开浏览器搜当天列表 + 回写 price/subtype/address），
      但当前 main_round_enabled=false（U3 拍板保持关），主轮不调度它，也没有 web 接口触发。
      本脚本直接调用它，作为路径②（不依赖主轮开关）的手动触发入口。

根治机制：v1.9.86 写库段对已存在行也合并列表行当前 _base（含 price），仅字段真变才落库 +
          前推 last_seen_at → REINS 改价后本地不再冻结（5WHY 根因修复）。

用法（勇哥本机；cwd=osaka-mvp；代理 127.0.0.1:7890 + Edge 开着；REINS 时段 07:00–23:00 JST 内）：
  python tools/run_date_sync.py            # 默认今天
  python tools/run_date_sync.py --today 2026-09-25

注意：
  · 会开独立 Edge 复用 data/session.json，可能与 8765 抢会话（正常，会自动重登）。
  · sync_today_dates 搜「全市当天」，量大时跑一阵（只回写日期+价格，比全量主轮轻）。
  · 刷完 last_seen 前推 → 下一轮增量推送按水位线把这些房源重推到线上。
"""
from __future__ import annotations
import sys
import time
import argparse
from core import config as cfgmod
from core import store as storemod
from core.crawler import sync_today_dates


def main() -> None:
    ap = argparse.ArgumentParser(description="手动触发今日日期同步（根治价格漂移）")
    ap.add_argument("--today", help="指定日期 YYYY-MM-DD（默认今天）")
    args = ap.parse_args()

    cfg = cfgmod.load()
    paths = cfgmod.paths(cfg)
    db_path = paths["db"]
    print("[run_date_sync] 目标库:", db_path)

    # 护栏：避免误写空壳库
    try:
        import sqlite3
        _c = sqlite3.connect(str(db_path))
        _n = _c.execute("select count(*) from properties").fetchone()[0]
        _c.close()
        if _n == 0:
            print("✗ properties 表为空（疑似空壳库），中止。检查 OSAKA_DB / cwd 是否和 8765 一致。")
            return
        print("[run_date_sync] 现有房源 %d 条，开始日期同步" % _n)
    except Exception as e:
        print("✗ 无法打开目标库：%s: %s" % (type(e).__name__, e))
        return

    store = storemod.Store(db_path)
    log = lambda *a, **k: print(*a)
    run_id = "manual_date_sync_%d" % int(time.time())
    try:
        res = sync_today_dates(store, cfg, log, today=args.today, run_id=run_id)
    except Exception as e:  # noqa: BLE001
        print("✗ 日期同步失败：%s: %s" % (type(e).__name__, e))
        return

    print("=== 日期同步完成 ===")
    print("收集 %d / 更新 %d / 新建 %d" % (res.get("collected", 0),
                                           res.get("updated", 0), res.get("new", 0)))
    print("本地[今 登録或変更]=%d (reg=%d / chg=%d)"
          % (res.get("union", 0), res.get("reg", 0), res.get("chg", 0)))
    print("提示：更新行已前推 last_seen；下一轮增量推送按水位线把这些房源重推到线上。")


if __name__ == "__main__":
    main()
