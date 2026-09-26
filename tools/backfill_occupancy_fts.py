#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.9.94 回填脚本：

  ① 给 occupancy_status 为空的存量房源补算（落库时已由 derive_occupancy_status 派生，
     但老库新增列前已入库的行需要补一次，否则"房产状态"面板筛不出历史房源）。
  ② 重建 FTS5 全文索引（properties_fts）。

  幂等、可重复跑；只写派生列与索引，不碰任何主数据。
  默认 dry-run（只统计、不写库）；加 --apply 才真正改写。可用 --db 指定库路径
  （默认 data/jproperty.db）。

用法：
  python tools/backfill_occupancy_fts.py            # 只看会改多少
  python tools/backfill_occupancy_fts.py --apply    # 真正回填 + 重建索引
  python tools/backfill_occupancy_fts.py --db /path/to/copy.db --apply   # 在副本上跑（验证用）
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.store import Store, derive_occupancy_status, _safe_json  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="v1.9.94 回填 occupancy_status + 重建 FTS5")
    ap.add_argument("--db", default=None, help="目标库路径（默认 data/jproperty.db）")
    ap.add_argument("--apply", action="store_true", help="真正改写；不加只统计")
    args = ap.parse_args()

    db = args.db or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jproperty.db")
    if not os.path.exists(db):
        print(f"[错误] 库不存在：{db}")
        sys.exit(2)

    s = Store(db)

    # ① 统计 / 补 occupancy_status
    rows = s.conn.execute(
        "SELECT property_no, detail_json FROM properties "
        "WHERE occupancy_status IS NULL OR occupancy_status = ''"
    ).fetchall()
    print(f"[occupancy_status] 待补算 {len(rows)} 条（总库 {s.conn.execute('SELECT count(*) FROM properties').fetchone()[0]} 条）")
    if args.apply:
        for r in rows:
            val = derive_occupancy_status(_safe_json(r["detail_json"]))
            s.conn.execute(
                "UPDATE properties SET occupancy_status = ? WHERE property_no = ?",
                (val, r["property_no"]))
        s.conn.commit()
        print(f"[occupancy_status] 已补算并提交 {len(rows)} 条")

    # ② FTS5 索引
    try:
        c = s.conn
        cnt_fts = c.execute("SELECT count(*) FROM properties_fts").fetchone()[0]
        cnt_prop = c.execute("SELECT count(*) FROM properties").fetchone()[0]
        if cnt_prop > 0 and cnt_fts != cnt_prop:
            if args.apply:
                c.execute("INSERT INTO properties_fts(properties_fts) VALUES('rebuild')")
                c.commit()
                print(f"[FTS5] 重建索引完成（properties={cnt_prop} → fts 已对齐）")
            else:
                print(f"[FTS5] 计数不一致（properties={cnt_prop} fts={cnt_fts}），--apply 将重建")
        else:
            print(f"[FTS5] 索引已一致（properties={cnt_prop} fts={cnt_fts}），无需重建")
        print(f"[FTS5] 就绪状态 fts_ready={s.fts_ready()}")
    except Exception as e:  # noqa: BLE001
        print(f"[FTS5] 重建失败（不影响查询，已降级 LIKE）：{e}")

    if not args.apply:
        print("\n（dry-run：未做任何改动。加 --apply 才真正回填 + 重建索引）")


if __name__ == "__main__":
    main()
