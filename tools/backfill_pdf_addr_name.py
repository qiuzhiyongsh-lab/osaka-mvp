# -*- coding: utf-8 -*-
"""把「地址（所在地）/ 物业名称（物件名）」从 **PDF 文字层**补进 ai_structure（v1.9.74）。

背景（勇哥 2026-09-24 反馈「五处谷歌地图按钮都没有」）
-----------------------------------------------------
详情页「PDF 文件生成出来的内容」区的前两条 = 所在地 / 物件名（ai_field_meta 里 seq 4/5，属 basic 组），
但这两项**历史上从来没被本地规则抽到过**（规则失配），所以 1487 条结构里大部分只有别的字段。

本脚本只做**外科手术式回填**（不重跑整条管线，避免把 Excel 导入的几十个字段
误标成「本轮未重新提取」而降级）：
  · 只碰 `所在地` / `物件名` 两个 col；
  · 目标字段**缺失** → 新增（放进 basic 组，置于组首）；
  · 目标字段**有值但不过闸门**（= 明显不是地址/楼名的脏值，如「大阪府知事第60085号」）→ 用 PDF 值替换；
  · 目标字段**已有合法值** → 一律不动（不做无谓改写）；
  · `edited=True`（人工改过）→ 永不触碰（v1.9.26 R1 铁律）；
  · 其它字段、radar/conclusion/anomaly 全部原样保留。

用法：
  python tools/backfill_pdf_addr_name.py            # 预演（默认，不写库）
  python tools/backfill_pdf_addr_name.py --apply    # 落库
  python tools/backfill_pdf_addr_name.py --apply --limit 50
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import pdf_local_extract as L          # noqa: E402

DB_AI = ROOT / "data" / "ai_pdf_store.db"
DB_MAIN = ROOT / "data" / "jproperty.db"
ATT = ROOT / "data" / "attachments"
TARGETS = ("所在地", "物件名")
NOTE = "v1.9.74 PDF 文字层重抽"
SOURCE = "PDF文字层"


def _meta(con) -> dict[str, dict]:
    return {r["col_name"]: dict(r) for r in con.execute("SELECT * FROM ai_field_meta")}


def _mk_field(col: str, value: str, meta: dict) -> dict:
    m = meta.get(col) or {}
    return {
        "col": col,
        "label": {"zh": m.get("zh") or col, "zh_tw": m.get("zh_tw") or col,
                  "en": m.get("en") or col, "ja": m.get("ja") or col},
        "value": value, "level": "ok", "note": NOTE,
        "source": SOURCE, "edited": False,
    }


def _basic_group(meta: dict) -> dict:
    m = meta.get("所在地") or {}
    return {"key": m.get("group_key") or "basic",
            "label": {"zh": m.get("group_cn") or "基本信息", "zh_tw": m.get("group_cn") or "基本信息",
                      "en": "basic", "ja": m.get("group_cn") or "基本情報"},
            "fields": []}


def plan_one(groups: list, vals: dict, meta: dict) -> tuple[list, list, list]:
    """返回 (新 groups, 新增说明, 覆盖说明)。纯函数，便于预演。"""
    added, replaced = [], []
    for col in TARGETS:
        v = (vals or {}).get(col)
        if not v:
            continue
        gate = L.VALIDATORS.get(col)
        if gate and not gate(v):
            continue
        holder = None
        for g in groups:
            for f in g.get("fields", []):
                if f.get("col") == col:
                    holder = f
                    break
            if holder:
                break
        if holder is None:
            tgt = None
            for g in groups:
                if (g.get("key") or "") == (meta.get(col) or {}).get("group_key", "basic"):
                    tgt = g
                    break
            if tgt is None:
                tgt = _basic_group(meta)
                groups.insert(0, tgt)          # basic 组放最前 → 与「第一、第二依次排序」一致
            tgt.setdefault("fields", []).insert(0, _mk_field(col, v, meta))
            added.append((col, v))
        else:
            if holder.get("edited"):
                continue                       # 人工值不动
            old = str(holder.get("value") or "")
            if old and (gate is None or gate(old)):
                continue                       # 已有合法值 → 不折腾
            holder["value"] = v
            holder["level"] = "ok"
            holder["note"] = NOTE
            holder["source"] = SOURCE
            replaced.append((col, old, v))
    return groups, added, replaced


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只预演）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    con = sqlite3.connect(str(DB_AI))
    con.row_factory = sqlite3.Row
    meta = _meta(con)
    rows = con.execute(
        "SELECT property_no, structure_json, edited FROM ai_structure"
        " WHERE structure_json IS NOT NULL AND structure_json != ''").fetchall()
    main = sqlite3.connect(str(DB_MAIN))
    have_pdf = {r[0] for r in main.execute(
        "SELECT property_no FROM properties WHERE pdf_path IS NOT NULL AND trim(pdf_path)!=''")}
    main.close()

    n = 0
    stat = {"no_pdf_file": 0, "scan": 0, "no_value": 0, "added": 0, "replaced": 0, "unchanged": 0}
    ex_add, ex_rep = [], []
    updates = []
    for r in rows:
        if args.limit and n >= args.limit:
            break
        pno = r["property_no"]
        if pno not in have_pdf:
            stat["no_pdf_file"] += 1
            continue
        pdf = ATT / f"{pno}.pdf"
        if not pdf.exists():
            stat["no_pdf_file"] += 1
            continue
        try:
            text = L.text_of(pdf)
        except Exception:                       # noqa: BLE001
            stat["scan"] += 1
            continue
        if not text or len(text) < 40:
            stat["scan"] += 1
            continue
        vals = L.extract(text)
        if not any(vals.get(c) for c in TARGETS):
            stat["no_value"] += 1
            continue
        try:
            struct = json.loads(r["structure_json"])
        except Exception:                       # noqa: BLE001
            continue
        groups = struct.get("groups") or []
        groups, added, replaced = plan_one(groups, vals, meta)
        n += 1
        if added:
            stat["added"] += 1
            if len(ex_add) < 8:
                ex_add.append((pno, added))
        if replaced:
            stat["replaced"] += 1
            if len(ex_rep) < 8:
                ex_rep.append((pno, replaced))
        if not added and not replaced:
            stat["unchanged"] += 1
            continue
        struct["groups"] = groups
        updates.append((json.dumps(struct, ensure_ascii=False), pno))

    print(f"扫描结构 {len(rows)} 条；有 PDF 且能抽到值的 {n} 条")
    print(f"  无 PDF 文件 {stat['no_pdf_file']} | 无文字层(扫描件,需OCR) {stat['scan']}"
          f" | 两字段都没抽到 {stat['no_value']}")
    print(f"  新增字段 {stat['added']} 条房源 | 覆盖脏值 {stat['replaced']} 条房源"
          f" | 已有合法值不动 {stat['unchanged']} 条房源")
    print("\n新增样本:")
    for pno, a in ex_add:
        print(f"   {pno}: " + "; ".join(f"{c}={v!r}" for c, v in a))
    print("覆盖脏值样本:")
    for pno, a in ex_rep:
        print(f"   {pno}: " + "; ".join(f"{c}: {o!r} → {v!r}" for c, o, v in a))

    if not args.apply:
        print(f"\n[预演] 未写库。加 --apply 落库（待写 {len(updates)} 行）。")
        con.close()
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with con:
        for sj, pno in updates:
            con.execute("UPDATE ai_structure SET structure_json=?, updated_at=?"
                        " WHERE property_no=?", (sj, now, pno))
    print(f"\n[落库] 已更新 {len(updates)} 行。")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
