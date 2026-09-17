#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/clean_misaligned_repair_fund.py —— 清洗「修繕積立金列错位」历史脏数据

背景：列表页 (3,10) 这一格实际是**坪単価**，却被存进 repair_fund。
      全库 1060 条 repair_fund **无一条有效**（最小值 419,000 円/月，物理不可能），
      其中 781 条与 unit_price_tsubo 完全相等（列错位铁证）。
      正确值早已保在 unit_price_tsubo 主列，删掉 repair_fund **不丢任何信息**。

安全：
  · 默认 **--dry-run**（只报不写）；必须显式 --apply 才动库。
  · --apply 前自动备份真库到 data/jproperty.db.bak_<时间戳>。
  · 只改 detail_json 里的 repair_fund 键；若该物件 unit_price_tsubo 为空，
    先把值迁进 unit_price_tsubo 再删（信息不丢）。

用法：
    python tools/clean_misaligned_repair_fund.py            # dry-run
    python tools/clean_misaligned_repair_fund.py --apply    # 真清洗（先备份）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jproperty.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写库（默认只 dry-run）")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print("找不到数据库：%s" % db)
        return 1

    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import verify_value_sanity as vs

    dirty = vs.scan(db)
    if not dirty:
        print("没有脏数据，无需清洗。")
        return 0

    nos = sorted({k.split(":")[0] for k in dirty})
    print("=" * 66)
    print("待清洗：%d 条记录 / %d 个字段（dry-run=%s）" % (len(nos), len(dirty), not args.apply))
    print("=" * 66)
    for k in list(dirty)[:10]:
        print("   %-28s %s" % (k, dirty[k]["reason"]))
    if len(dirty) > 10:
        print("   … 还有 %d 条" % (len(dirty) - 10))

    if not args.apply:
        print("\n[dry-run] 未改动任何数据。确认无误后加 --apply 执行（会自动备份）。")
        return 0

    # 备份
    bak = db.with_suffix(db.suffix + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(db, bak)
    print("\n已备份：%s" % bak)

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    moved = cleared = 0
    for no in nos:
        r = con.execute(
            "SELECT unit_price_tsubo, detail_json FROM properties WHERE property_no=?", (no,)
        ).fetchone()
        if not r:
            continue
        try:
            d = json.loads(r["detail_json"] or "{}")
        except Exception:
            continue
        rf = d.get("repair_fund")
        if rf is None:
            continue
        tsubo = r["unit_price_tsubo"]
        if tsubo in (None, ""):
            # 坪単価主列为空 → 先迁过去，保证信息不丢
            con.execute("UPDATE properties SET unit_price_tsubo=? WHERE property_no=?", (rf, no))
            moved += 1
        d.pop("repair_fund", None)
        con.execute("UPDATE properties SET detail_json=? WHERE property_no=?",
                    (json.dumps(d, ensure_ascii=False), no))
        cleared += 1
    con.commit()
    con.close()
    print("清洗完成：清掉 repair_fund %d 条；其中 %d 条的值迁入 unit_price_tsubo。" % (cleared, moved))
    print("下一步：跑 tools/verify_value_sanity.py --update-baseline 把 baseline 清空 → 全绿。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
