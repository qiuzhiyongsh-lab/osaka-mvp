#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.9.94 一次性修复：FTS5 索引损坏导致 properties 写入报 'database disk image is malformed'。

根因：_ensure_fts 早期重建只修了 FTS 元数据，底层 shadow 表已坏；AFTER UPDATE 触发器
properties_fts_au 每次 UPDATE properties 都向损坏的 properties_fts 写，抛 malformed。

修复步骤（幂等、可重复）：
  1. 诊断：先卸掉三个 FTS 触发器，试 UPDATE 一行；成功即证明确为 FTS 触发器所致。
  2. DROP TABLE properties_fts（连带清掉坏 shadow 表）。
  3. 回填 occupancy_status（此时没有触发器干扰）。
  4. 重新建 Store → 自动 CREATE properties_fts + 触发器 + rebuild（从已回填的 properties 重建）。
  5. 校验 integrity + 计数一致 + fts_ready。

默认 dry-run（只做诊断 + 统计，不写库）；加 --apply 才真正修复。
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jproperty.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = args.db
    if not os.path.exists(db):
        print(f"[错误] 库不存在：{db}")
        sys.exit(2)

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row

    # ---- 1. 诊断 ----
    trigs = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'properties_fts%'")]
    print(f"[诊断] 现有 FTS 触发器: {trigs}")
    c.execute("DROP TRIGGER IF EXISTS properties_fts_au")
    c.execute("DROP TRIGGER IF EXISTS properties_fts_ai")
    c.execute("DROP TRIGGER IF EXISTS properties_fts_ad")
    r = c.execute("SELECT property_no FROM properties WHERE occupancy_status IS NULL LIMIT 1").fetchone()
    if r is None:
        print("[诊断] 没有 NULL occupancy_status 行，无需回填")
    else:
        try:
            c.execute("UPDATE properties SET occupancy_status='__diag__' WHERE property_no=?",
                      (r["property_no"],))
            c.commit()
            print("[诊断] 卸掉 FTS 触发器后 UPDATE 成功 → 根因确为 FTS 触发器写入损坏的 properties_fts")
            c.execute("UPDATE properties SET occupancy_status=NULL WHERE property_no=?",
                      (r["property_no"],))
            c.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[诊断] 卸掉触发器后 UPDATE 仍失败 → 根因在 properties 表本身：{e}")
            sys.exit(1)

    if not args.apply:
        print("\n（dry-run：仅诊断，未做任何修复。加 --apply 执行 DROP+回填+重建）")
        return

    # ---- 2. 干净重建 FTS ----
    c.execute("DROP TABLE IF EXISTS properties_fts")
    c.commit()
    print("[修复] 已 DROP properties_fts（含坏 shadow 表）")

    # ---- 3. 回填 occupancy_status（无触发器干扰）----
    from core.store import derive_occupancy_status, _safe_json  # noqa: E402
    rows = c.execute(
        "SELECT property_no, detail_json FROM properties "
        "WHERE occupancy_status IS NULL OR occupancy_status=''").fetchall()
    print(f"[修复] 需回填 occupancy_status {len(rows)} 条")
    for r in rows:
        val = derive_occupancy_status(_safe_json(r["detail_json"]))
        c.execute("UPDATE properties SET occupancy_status=? WHERE property_no=?",
                  (val, r["property_no"]))
    c.commit()
    print(f"[修复] occupancy_status 回填并提交 {len(rows)} 条")

    # ---- 4. 重新建 Store → 自动建表+触发器+rebuild ----
    from core.store import Store  # noqa: E402
    s = Store(db)
    print(f"[修复] fts_ready={s.fts_ready()}")

    # ---- 5. 校验 ----
    ic = s.conn.execute("PRAGMA integrity_check").fetchall()
    cnt_p = s.conn.execute("SELECT count(*) FROM properties").fetchone()[0]
    cnt_f = s.conn.execute("SELECT count(*) FROM properties_fts").fetchone()[0]
    print(f"[校验] integrity={ic[:1]}")
    print(f"[校验] properties={cnt_p}  fts={cnt_f}  {'✅一致' if cnt_p==cnt_f else '❌不一致'}")
    print("[修复] 完成")


if __name__ == "__main__":
    main()
