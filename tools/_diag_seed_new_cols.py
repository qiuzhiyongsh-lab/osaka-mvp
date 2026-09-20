# -*- coding: utf-8 -*-
"""v1.9.36 加列：把 11 个新字段幂等 INSERT 进 ai_field_meta（seq 61-71）。

与 tools/import_ai_pdf_excel.py 的 _EXTRA_META 保持一致，保证将来 Excel re-import
（replace_field_meta 整表覆盖）不会洗掉这些列。

用法：python tools/_diag_seed_new_cols.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ai_pdf_store.db"

# (col_name, seq, group_key, group_cn, zh, zh_tw, en, ja, source, note)
NEW_COLS = [
    ("ペット（飼育可）", 61, "status", "现状与交付", "能否饲养宠物", "可否飼養寵物",
     "Pet Allowed", "ペット（飼育可）", "PDF视觉补充", "可/不可/相談"),
    ("権利形態", 62, "building", "建筑与年代", "权利形态", "權利形態",
     "Title Type", "権利形態", "PDF视觉补充", "所有権/借地権"),
    ("前面道路", 63, "building", "建筑与年代", "临路情况", "臨路情況",
     "Road Frontage", "前面道路", "PDF视觉补充", "幅員/公道/私道"),
    ("セキュリティ", 64, "building", "建筑与年代", "安防", "安防",
     "Security", "セキュリティ", "PDF视觉补充", "オートロック/防犯"),
    ("駐輪場・バイク置場", 65, "building", "建筑与年代", "自行车/摩托车位", "自行車/機車位",
     "Bike/Moto Parking", "駐輪場・バイク置場", "PDF视觉补充", "有/無/月額"),
    ("エレベーター", 66, "building", "建筑与年代", "电梯", "電梯",
     "Elevator", "エレベーター", "PDF视觉补充", "有/無/基"),
    ("建蔽率", 67, "building", "建筑与年代", "建筑覆盖率", "建蔽率",
     "Building Coverage", "建蔽率", "PDF视觉补充", "%"),
    ("容積率", 68, "building", "建筑与年代", "容积率", "容積率",
     "Floor Area Ratio", "容積率", "PDF视觉补充", "%"),
    ("地目", 69, "building", "建筑与年代", "地目", "地目",
     "Land Category", "地目", "PDF视觉补充", "宅地/田/畑"),
    ("バルコニー方向", 70, "layout", "户型与面积", "阳台朝向", "陽台朝向",
     "Balcony Direction", "バルコニー方向", "PDF视觉补充", "南/北/東/西向"),
    ("リフォーム履歴", 71, "comment", "描述与备注", "翻新履历", "翻新履歷",
     "Renovation History", "リフォーム履歴", "PDF视觉补充", "有/無/年"),
]


def main() -> int:
    if not DB.exists():
        print(f"[FAIL] 找不到库：{DB}")
        return 1
    con = sqlite3.connect(str(DB))
    before = con.execute("SELECT COUNT(*) FROM ai_field_meta").fetchone()[0]
    inserted, skipped = [], []
    for row in NEW_COLS:
        cn = row[0]
        exists = con.execute("SELECT 1 FROM ai_field_meta WHERE col_name=?", (cn,)).fetchone()
        if exists:
            skipped.append(cn)
            continue
        con.execute(
            "INSERT INTO ai_field_meta"
            " (col_name,seq,group_key,group_cn,zh,zh_tw,en,ja,source,note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        inserted.append(cn)
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM ai_field_meta").fetchone()[0]
    con.close()
    print(f"[OK] 之前 {before} 列 → 现在 {after} 列")
    print(f"      新增 {len(inserted)} 列：{inserted}")
    if skipped:
        print(f"      已存在跳过 {len(skipped)} 列：{skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
