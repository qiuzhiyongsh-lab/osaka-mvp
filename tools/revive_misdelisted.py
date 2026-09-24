# -*- coding: utf-8 -*-
"""R2（PRD 31）：一次性批量复活「疑似误标下架」房源。

背景：主轮关闭后复活机制成死代码（store.py:1588 安全阀 + crawler 空 seen），
导致 is_active=0 中"近期仍被看到/变动"的房源永不被救回。
本脚本把这批脏数据一次性复活（is_active=1, absent_runs=0）。

安全设计：
  · 默认 --dry：只统计 + 导出候选清单 CSV，**不改库**。
  · --apply：先备份 data/jproperty.db，再执行，可回滚。
  · 阈值可配（--chg-since / --seen-since），默认近 4 天（>= 2026-09-20）。
  · 候选 = is_active=0 AND (chg_date_iso >= ? OR last_seen_at >= ?)

用法：
  python tools/revive_misdelisted.py                       # dry-run，看清单
  python tools/revive_misdelisted.py --apply               # 执行（会先备份）
  python tools/revive_misdelisted.py --apply --chg-since 2026-09-15 --seen-since 2026-09-15
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jproperty.db"
BACKUP_DIR = ROOT / ".backups" / "revive"
DEFAULT_CHG = "2026-09-20"
DEFAULT_SEEN = "2026-09-20"


def main() -> None:
    ap = argparse.ArgumentParser(description="R2 批量复活疑似误标下架房源")
    ap.add_argument("--apply", action="store_true",
                    help="执行复活（默认 dry-run 只统计+导出清单）")
    ap.add_argument("--chg-since", default=DEFAULT_CHG,
                    help="chg_date_iso >= 该日期的标死房源纳入候选")
    ap.add_argument("--seen-since", default=DEFAULT_SEEN,
                    help="last_seen_at >= 该日期的标死房源纳入候选")
    ap.add_argument("--db", default=str(DB), help="房源库路径（默认 data/jproperty.db）")
    args = ap.parse_args()

    dbp = Path(args.db)
    if not dbp.exists():
        print("[错误] 房源库不存在：%s" % dbp)
        sys.exit(2)

    con = sqlite3.connect(str(dbp))
    con.row_factory = sqlite3.Row
    try:
        # 确保 absent_runs 列存在（与 store.py 幂等一致）
        try:
            con.execute(
                "ALTER TABLE properties ADD COLUMN absent_runs INTEGER NOT NULL DEFAULT 0")
            con.commit()
        except Exception:
            pass
        total_active0 = con.execute(
            "SELECT COUNT(*) FROM properties WHERE is_active=0").fetchone()[0]
        q = ("SELECT property_no, property_subtype, chg_date_iso, last_seen_at, absent_runs "
             "FROM properties WHERE is_active=0 AND (chg_date_iso >= ? OR last_seen_at >= ?) "
             "ORDER BY last_seen_at DESC, chg_date_iso DESC")
        rows = con.execute(q, (args.chg_since, args.seen_since)).fetchall()
    finally:
        con.close()

    print("=" * 60)
    print("R2 批量复活候选统计")
    print("  房源库          : %s" % dbp)
    print("  阈值            : chg>=%s 或 last_seen>=%s" % (args.chg_since, args.seen_since))
    print("  is_active=0 总计 : %d" % total_active0)
    print("  候选(待复活)    : %d" % len(rows))
    print("=" * 60)

    if not rows:
        print("无可复活候选。")
        return

    # 导出候选清单 CSV（dry/apply 都导，便于人工复核）
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = ROOT / "data" / ("revive_candidates_%s.csv" % ts)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["property_no", "subtype", "chg_date_iso",
                    "last_seen_at", "absent_runs"])
        for r in rows:
            w.writerow([r["property_no"], r["property_subtype"],
                        r["chg_date_iso"], r["last_seen_at"], r["absent_runs"]])
    print("候选清单已导出 → %s" % csv_path)

    if not args.apply:
        print("\n[DRY-RUN] 未改动数据库。请过目清单后加 --apply 执行。")
        print("回滚方式：--apply 前会自动备份；或 checkpoint.py --restore <改前检查点>")
        return

    # ---- apply：先备份 ----
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bk = BACKUP_DIR / ("jproperty_%s.db" % ts)
    shutil.copy2(dbp, bk)
    print("1) 已备份房源库 → %s" % bk)

    con = sqlite3.connect(str(dbp))
    try:
        nos = [r["property_no"] for r in rows]
        ph = ", ".join("?" for _ in nos)
        cur = con.execute(
            "UPDATE properties SET is_active=1, absent_runs=0 "
            "WHERE property_no IN (%s)" % ph, nos)
        con.commit()
        n = cur.rowcount
    finally:
        con.close()
    print("2) 已复活 %d 条（is_active=1, absent_runs=0）" % n)
    print("3) 回滚：shutil.copy2 %s %s 覆盖即可，或 checkpoint.py --restore <改前检查点>" % (bk, dbp))


if __name__ == "__main__":
    main()
