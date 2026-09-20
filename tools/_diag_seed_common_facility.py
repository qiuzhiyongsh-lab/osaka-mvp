# -*- coding: utf-8 -*-
"""一次性：把 共用施設（会所）列补进 ai_pdf_store.db 的 ai_field_meta。

可重复跑（幂等）：已存在则跳过。与 import_ai_pdf_excel.py 的 _EXTRA_META 保持一致，
保证将来 re-import 也不会把它洗掉。
"""
from pathlib import Path
import sqlite3

DB = Path(__file__).resolve().parent.parent / "data" / "ai_pdf_store.db"
ROW = ("共用施設", 60, "building", "建筑与年代", "共用设施", "共用設施",
       "Common Facilities", "共用施設", "PDF视觉补充", "会所/集会所/健身房/泳池等")


def main():
    if not DB.exists():
        print(f"[FAIL] 找不到 {DB}")
        return 1
    con = sqlite3.connect(str(DB))
    try:
        cur = con.execute("SELECT col_name FROM ai_field_meta WHERE col_name=?", (ROW[0],))
        if cur.fetchone():
            print(f"[SKIP] {ROW[0]} 已存在")
        else:
            con.execute(
                "INSERT INTO ai_field_meta"
                " (col_name,seq,group_key,group_cn,zh,zh_tw,en,ja,source,note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)", ROW)
            con.commit()
            print(f"[OK] 已插入 {ROW[0]}（group={ROW[2]}）")
        n = con.execute("SELECT COUNT(*) FROM ai_field_meta").fetchone()[0]
        print(f"[INFO] ai_field_meta 现有 {n} 列")
        for r in con.execute("SELECT col_name,group_key,zh FROM ai_field_meta WHERE col_name=? OR group_key='building' ORDER BY seq"):
            print("   ", r)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
