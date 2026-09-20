# -*- coding: utf-8 -*-
"""清理 v1.9.36 测试时在 300139241123/300140713692 落库的 2 条误抓残留字段。

这些值是 RULES 未收紧前被 L1 抽到的免责长句（駐輪場=空き状況免责文 / セキュリティ=サービス料），
本轮重抽后已不再抽出（rejected 或正则不匹配），但 merge_structure 会把旧 AI 源值 carry 成 warn 显示。
发版前直接删除，让前端「缺」而非「warn 误显」。仅作用于这 2 条已知残留，不动其他数据。
"""
import sqlite3, json
from pathlib import Path

DB = Path("data/ai_pdf_store.db")
TARGET = {
    "300139241123": ["駐輪場・バイク置場"],
    "300140713692": ["セキュリティ"],
}


def main() -> int:
    con = sqlite3.connect(str(DB))
    for pno, cols in TARGET.items():
        row = con.execute("SELECT structure_json FROM ai_structure WHERE property_no=?",
                          (pno,)).fetchone()
        if not row or not row[0]:
            continue
        d = json.loads(row[0])
        cnt = 0
        for g in d.get("groups", []):
            before = len(g.get("fields", []))
            g["fields"] = [f for f in g["fields"] if f.get("col") not in cols]
            cnt += before - len(g["fields"])
        con.execute("UPDATE ai_structure SET structure_json=? WHERE property_no=?",
                    (json.dumps(d, ensure_ascii=False), pno))
        print(f"  {pno}: 删除 {cnt} 个测试残留字段 {cols}")
    con.commit()
    con.close()
    print("[OK] 清理完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
