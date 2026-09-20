# -*- coding: utf-8 -*-
"""清理批量重抽中落库的 2 个明显脏值（宁缺勿滥）。"""
import sqlite3, json
from pathlib import Path

AI = Path(__file__).resolve().parent.parent / "data" / "ai_pdf_store.db"
ai = sqlite3.connect(str(AI))
ai.execute("PRAGMA busy_timeout=15000")


def _rm(g):
    ch = False
    for grp in g["groups"]:
        before = len(grp["fields"])
        grp["fields"] = [f for f in grp["fields"]
                         if not (f.get("col") == "駐輪場・バイク置場"
                                 and "空状態はご確認ください" in (f.get("value") or ""))]
        if len(grp["fields"]) != before:
            ch = True
    return ch


def _strip(g):
    ch = False
    for grp in g["groups"]:
        for f in grp["fields"]:
            if f.get("col") == "ペット（飼育可）" and (f.get("value") or "").startswith("】"):
                f["value"] = f["value"][1:]
                ch = True
    return ch


def edit(pno, fn):
    row = ai.execute("SELECT structure_json FROM ai_structure WHERE property_no=?", (pno,)).fetchone()
    if not row or not row[0]:
        return False
    g = json.loads(row[0])
    changed = fn(g)
    if changed:
        ai.execute("UPDATE ai_structure SET structure_json=? WHERE property_no=?",
                   (json.dumps(g, ensure_ascii=False), pno))
    return changed


c1 = edit("300134464383", _rm)
c2 = edit("100140802801", _strip)
ai.commit()
print("300134464383 removed bad 駐輪場:", c1)
print("100140802801 stripped ペット bracket:", c2)
