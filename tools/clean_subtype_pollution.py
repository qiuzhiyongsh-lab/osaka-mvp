# -*- coding: utf-8 -*-
"""一次性清洗「種目污染」(v1.9.87 · 修复①存量)。

【问题】REINS 列表网格 (1,5) 把「物件種目」与「取引状況(オーナーチェンジ/オークション)」
用全角斜线 ／ 拼在一格，导致 property_subtype 被写成
「中古マンション\n／\nオーナーチェンジ」→ 查询页 IN(...) 过滤失效、抓取 6 组選択误判。
全库共 228 件（226 中古マンション+オーナーチェンジ、1 売地+オークション、1 中古マンション+オークション）。

【本工具做的事】
  1. 对每条污染行：把 ／ 前段（再按 \n 取首行）作为干净 property_subtype 写回列；
  2. 把 ／ 后段（取引状況）写入 detail_json.trade_status，保留业务含义，不复用 trade_type
     （trade_type 是网格(2,2)「取引態様」，且不在 PROPERTY_COLUMNS、列表模式会被 upsert 丢弃）。
  3. 不改动其它字段、不动 last_seen（避免误触增量水位线）；需要重推线上时由后续 force push 处理。

【运行】
  cd osaka-mvp
  python tools/clean_subtype_pollution.py            # 默认 dry-run，只打印将要改什么
  python tools/clean_subtype_pollution.py --apply    # 真正落库（需勇哥授权）

【铁律】动 DB 前务必先 checkpoint（checkpoint.py）。本工具不自动 checkpoint，
调用方应先打点。落库后建议跑一次「强制同步上传」把 228 件重推线上。
"""
from __future__ import annotations
import sys
import os
import json
import argparse
import sqlite3

# 允许从 tools/ 直接运行：把项目根加入 sys.path 以 import core
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _resolve_db() -> str:
    try:
        from core import config as cfgmod
        cfg = cfgmod.load()
        return str(cfgmod.paths(cfg)["db"])
    except Exception:
        return os.path.join(_ROOT, "data", "jproperty.db")


def _split_subtype(raw: str):
    """返回 (clean_subtype, trade_status)。"""
    raw = (raw or "").strip()
    seg = raw.split("／")[0].split("\n")[0].strip()
    rest = ""
    if "／" in raw:
        rest = raw.split("／", 1)[1].replace("\n", " ").strip()
    return seg, rest


def main() -> None:
    ap = argparse.ArgumentParser(description="清洗 property_subtype 種目污染（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正落库（默认只预览）")
    args = ap.parse_args()

    db = _resolve_db()
    print("[clean] 目标库:", db)
    if not os.path.exists(db):
        print("✗ 库不存在")
        return

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT property_no, property_subtype, detail_json FROM properties "
        "WHERE property_subtype LIKE '%／%' OR property_subtype LIKE '%\n%'"
    ).fetchall()
    print("[clean] 命中污染行: %d" % len(rows))
    if not rows:
        con.close()
        return

    changed = 0
    for r in rows:
        no = r["property_no"]
        clean, ts = _split_subtype(r["property_subtype"])
        if clean == (r["property_subtype"] or ""):
            continue
        changed += 1
        # 组装新 detail_json（保留已有键，仅补/改 trade_status）
        dj = {}
        if r["detail_json"]:
            try:
                dj = json.loads(r["detail_json"])
            except Exception:
                dj = {}
        if ts:
            dj["trade_status"] = ts
        new_dj = json.dumps(dj, ensure_ascii=False, default=str) if dj else (r["detail_json"] or "")
        if args.apply:
            con.execute(
                "UPDATE properties SET property_subtype=?, detail_json=? WHERE property_no=?",
                (clean, new_dj, no),
            )
        if changed <= 12 or args.apply is False:
            print("  %s%s -> subtype=%r trade_status=%r" % (
                "" if args.apply else "[dry] ", no, clean, ts))
    if args.apply:
        con.commit()
        print("[clean] 已落库更新 %d 行。建议随后跑一次「强制同步上传」把这批重推线上。" % changed)
    else:
        print("[clean] dry-run：将更新 %d 行。加 --apply 真正落库（先 checkpoint）。" % changed)
    con.close()


if __name__ == "__main__":
    main()
