# -*- coding: utf-8 -*-
"""路径②/① 刷完后的验收：核对已知 5 条差异房源现状（只读本地库，不改写）。

只读 data/jproperty.db 输出 price/last_seen/chg/subtype/address，并对照刷前基线判是否已修。
（另 5 条漂移番号在对比截图 #50 截断处丢失，未纳入本核对；请对照 server.log 或 REINS 页面。）

用法（勇哥本机；cwd=osaka-mvp）：
  python tools/check_drift_status.py
"""
from __future__ import annotations
import sqlite3
from datetime import datetime
from core import config as cfgmod
from core import store as storemod

# 刷前基线（DB 证据 / REINS 贴图，单位：円）：
#   缺失 -> 本地 ABSENT；残缺 -> 字段全 NULL；漂移 -> 旧价格
BASELINE = {
    "300140865451": ("缺失", None),
    "300139562919": ("残缺", None),
    "300137242506": ("残缺", None),
    "300139730214": ("漂移", 34800000),   # REINS 5980万
    "300138617167": ("漂移", 49800000),   # REINS 更高
}


def main() -> None:
    cfg = cfgmod.load()
    paths = cfgmod.paths(cfg)
    db_path = paths["db"]
    try:
        _c = sqlite3.connect(str(db_path))
        _n = _c.execute("select count(*) from properties").fetchone()[0]
        _c.close()
        if _n == 0:
            print("✗ properties 表为空（疑似空壳库），中止。检查 OSAKA_DB / cwd。")
            return
    except Exception as e:
        print("✗ 无法打开目标库：%s: %s" % (type(e).__name__, e))
        return

    store = storemod.Store(db_path)
    today = datetime.now().strftime("%Y-%m-%d")
    print("今日 %s | 本地库 %s\n" % (today, db_path))

    for no, (kind, base_price) in BASELINE.items():
        p = store.get_property(no)
        if p is None:
            status = "✓ 已补（原本缺失）" if kind == "缺失" else "✗ 仍缺"
            cur = "ABSENT"
        else:
            price = p.get("price")
            sub = p.get("property_subtype")
            addr = p.get("address")
            ls = p.get("last_seen_at") or ""
            chg = p.get("chg_date_iso") or ""
            if kind == "残缺":
                ok = bool(sub and price and addr)
                status = "✓ 已回填" if ok else "✗ 仍残缺"
            elif kind == "漂移":
                changed = (price != base_price)
                recent = ls.startswith(today)
                if changed and recent:
                    status = "✓ 已归位"
                elif changed:
                    status = "⚠ 价变但 last_seen 旧（未重推）"
                else:
                    status = "✗ 价格未变"
            else:
                status = "存在"
            cur = "price=%s万 sub=%s chg=%s last=%s" % (
                (price // 10000) if price else price, sub, chg, ls[:10])
        print("[%s] %s  %s" % (status, no, cur))

    print("\n注：另 5 条漂移番号在对比截图 #50 截断处丢失，未纳入本核对；")
    print("    请对照 server.log「日期同步完成：更新 N」或 REINS 页面确认其余 7 条漂移是否归位。")


if __name__ == "__main__":
    main()
