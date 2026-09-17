# -*- coding: utf-8 -*-
"""BUG-1 历史错行清洗（v1.6，数据已全库核验）。

范围：property_subtype 含「売地」（売地 / 売地／オークション）且 detail_json 含
repair_fund 的行。这些行的 repair_fund 实为「坪単価」（土地类无 修繕積立金），
来自 LIST_FIELD_MAP(3,10) 的错列。

做法：
  ① 从 detail_json 删掉 repair_fund（清掉本地显示的假「修繕積立金」）；
  ② 若主列 unit_price_tsubo 为空，把该值填入（坪単価 落主列，线上/本地都正确显示）。

默认 dry-run（不写库）；加 --apply 才执行。
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "..", "data", "jproperty.db")
APPLY = "--apply" in sys.argv

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

rows = con.execute(
    """
    SELECT property_no, unit_price_tsubo, detail_json
    FROM properties
    WHERE property_subtype LIKE '売地%'
      AND detail_json LIKE '%"repair_fund"%'
    """
).fetchall()

print(f"待清洗 売地 行数: {len(rows)}   (apply={APPLY})")
n_done = 0
for r in rows:
    dj = json.loads(r["detail_json"] or "{}")
    rf = dj.get("repair_fund")
    if rf is None:
        continue
    try:
        val = float("".join(ch for ch in str(rf) if ch.isdigit() or ch == "."))
    except ValueError:
        val = None
    new_dj = dict(dj)
    new_dj.pop("repair_fund", None)
    cur_ut = r["unit_price_tsubo"]
    fill_ut = (cur_ut is None) or (cur_ut == 0)
    if APPLY:
        if fill_ut and val is not None:
            con.execute(
                "UPDATE properties SET unit_price_tsubo=?, detail_json=? WHERE property_no=?",
                (val, json.dumps(new_dj, ensure_ascii=False), r["property_no"]),
            )
        else:
            con.execute(
                "UPDATE properties SET detail_json=? WHERE property_no=?",
                (json.dumps(new_dj, ensure_ascii=False), r["property_no"]),
            )
    n_done += 1
    print(f"  no={r['property_no']}  repair_fund={rf!r}  -> unit_price_tsubo "
          f"{'填 '+str(val) if fill_ut else '保留原值'}")

if APPLY:
    con.commit()
    print(f"\n已应用：{n_done} 行清洗完成。")
else:
    print(f"\n[DRY-RUN] 未写入。确认无误后加 --apply 执行。")
con.close()
