# -*- coding: utf-8 -*-
"""backfill_gaps：扫描「欠账」并把可自动补的入队（v1.9.81 批次1 · 任务4）。

两类欠账
--------
① **PDF 欠账**：`has_floorplan=1`（平台标明有図面）但本地 `pdf_path` 为空
   —— 实证 2026-09-25 共 **1146 条**。这些是"该有却没有"，属真欠账。
   处理：入 `pdf_recrawl_queue`（交 tools/recrawl_pdf.py 消费，需 REINS 登录态）。

② **详情欠账**：`detail_json` 为空 —— 实证 2026-09-25 共 **336 条**
   （在架 33 + 已下架 303）。勇哥 09-25 定：**一起补**。
   处理：只**报告**（详情补抓由阶段B 走番号検索完成）；已下架的 303 条
   大概率检索 0 件 → 会被 v1.9.81 G-3「连续 2 次 0 件 → 终局跳过」快速收敛，
   不会每轮白跑，这正是 G-3 与本项目题的协同点。

用法
----
  python tools/backfill_gaps.py                 # 只扫描报告（默认，不写库）
  python tools/backfill_gaps.py --enqueue-pdf   # 把 PDF 欠账入补抓队列
  python tools/backfill_gaps.py --limit 200     # 限制入队条数（默认全量）
"""
from __future__ import annotations

import io
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core import config as cfgmod   # noqa: E402


def _open(cfg: dict):
    db = Path(str(cfgmod.paths(cfg)["db"]))
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


def main() -> int:
    cfg = cfgmod.load()
    con = _open(cfg)
    q = lambda s: con.execute(s).fetchone()[0]   # noqa: E731

    print("=== 欠账扫描 ===")
    total = q("SELECT COUNT(*) FROM properties")
    pdf_gap = q("SELECT COUNT(*) FROM properties"
                " WHERE COALESCE(has_floorplan,0)=1"
                "   AND (pdf_path IS NULL OR pdf_path='')")
    pdf_gap_act = q("SELECT COUNT(*) FROM properties"
                    " WHERE COALESCE(has_floorplan,0)=1"
                    "   AND (pdf_path IS NULL OR pdf_path='') AND is_active=1")
    det_gap = q("SELECT COUNT(*) FROM properties"
                " WHERE detail_json IS NULL OR detail_json='' OR detail_json='{}'")
    det_gap_act = q("SELECT COUNT(*) FROM properties"
                    " WHERE (detail_json IS NULL OR detail_json='' OR detail_json='{}')"
                    "   AND is_active=1")
    pending = q("SELECT COUNT(*) FROM pdf_recrawl_queue WHERE status='pending'")
    print(f"  房源总行            = {total}")
    print(f"  ① PDF 欠账(应下未下) = {pdf_gap}（在架 {pdf_gap_act} / 已下架 {pdf_gap - pdf_gap_act}）")
    print(f"  ② 详情欠账(detail空) = {det_gap}（在架 {det_gap_act} / 已下架 {det_gap - det_gap_act}）")
    print(f"  补抓队列现有 pending = {pending}")

    if "--enqueue-pdf" in sys.argv:
        limit = 0
        for i, a in enumerate(sys.argv):
            if a == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        rows = con.execute(
            "SELECT property_no FROM properties"
            " WHERE COALESCE(has_floorplan,0)=1 AND (pdf_path IS NULL OR pdf_path='')"
            " ORDER BY COALESCE(last_seen_at,first_seen_at) DESC").fetchall()
        nos = [r[0] for r in rows]
        if limit:
            nos = nos[:limit]
        n = 0
        for no in nos:
            have = con.execute(
                "SELECT 1 FROM pdf_recrawl_queue WHERE property_no=? AND status='pending'",
                (no,)).fetchone()
            if have:
                continue
            con.execute(
                "INSERT INTO pdf_recrawl_queue (property_no,status,priority,reason,created_at)"
                " VALUES (?,'pending',2,'pdf_missing_backfill',datetime('now','localtime'))",
                (no,))
            n += 1
        con.commit()
        pend2 = con.execute(
            "SELECT COUNT(*) FROM pdf_recrawl_queue WHERE status='pending'").fetchone()[0]
        print(f"\n  ✓ 已入补抓队列 {n} 条（现有 pending = {pend2}）")
        print("  → 补抓执行：python tools/recrawl_pdf.py（需 REINS 登录态 + 工作时段）")

    if "--list-detail" in sys.argv:
        rows = con.execute(
            "SELECT property_no, is_active, COALESCE(last_seen_at,first_seen_at) ls"
            " FROM properties"
            " WHERE detail_json IS NULL OR detail_json='' OR detail_json='{}'"
            " ORDER BY is_active DESC, ls DESC LIMIT 50").fetchall()
        print("\n=== 详情欠账样本（前 50）===")
        for r in rows:
            print(f"  {r[0]}  active={r[1]}  last={r[2]}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
