# -*- coding: utf-8 -*-
"""PRD-37 M1-B：给存量 PDF 一次性建 sha256 基线（回填 properties.pdf_sha256）。

设计要点（对应 PRD-37 §6.1 + D15 例外项）：
  · **只读 PDF**，绝不移动/重命名/删除任何文件；也**不重下载**任何 PDF。
  · **可断点续跑**：已有 pdf_sha256 的行默认跳过；中断后重跑只补剩下的。
  · **可后台跑**：6094 份 × 几 MB，纯本地读盘，几分钟完成；支持 --limit 小批试跑。
  · 指纹用**完整 sha256**（与 core/pdf_cloud.py::_sha256_of 同口径），
    便于将来与 pdf_upload_audit 表对齐。
  · 建基线**不是修历史数据**：它只是给"以后判断 PDF 变没变"立一个起点（D15）。

用法：
    python tools/pdf_sha256_baseline.py            # 正式跑（增量，跳过已填）
    python tools/pdf_sha256_baseline.py --dry-run  # 只统计不写库
    python tools/pdf_sha256_baseline.py --limit 50 # 只处理前 50 份（试跑）
    python tools/pdf_sha256_baseline.py --force    # 已填的也重算
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "jproperty.db"
ATTACH = ROOT / "data" / "attachments"

CHUNK = 1 << 20  # 1MB 分块


def sha256_of_file(p: Path) -> str:
    """完整 sha256（与 pdf_cloud._sha256_of 同口径，分块读，内存恒定）。"""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--force", action="store_true", help="已填 pdf_sha256 的也重算")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 份（0=不限）")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--attach", default=str(ATTACH))
    args = ap.parse_args()

    db, attach = Path(args.db), Path(args.attach)
    if not db.exists():
        print("✗ 找不到库：%s" % db)
        return 2
    if not attach.exists():
        print("✗ 找不到附件目录：%s" % attach)
        return 2

    pdfs = sorted(attach.glob("*.pdf"))
    total = len(pdfs)
    if args.limit:
        pdfs = pdfs[: args.limit]
    print("① 附件目录 %s：共 %d 份 PDF，本次处理 %d 份"
          % (attach, total, len(pdfs)))

    con = sqlite3.connect(str(db), timeout=30.0)
    con.row_factory = sqlite3.Row
    # 8765 可能在同时写库（抓取/同步）→ 给足等锁时间，避免 "database is locked" 直接崩
    con.execute("PRAGMA busy_timeout=30000")

    # 已有指纹的行（用于跳过）
    have = set()
    if not args.force:
        try:
            for r in con.execute(
                "SELECT property_no FROM properties "
                "WHERE pdf_sha256 IS NOT NULL AND pdf_sha256 <> ''"
            ):
                have.add(r["property_no"])
            print("   库中已有指纹的行：%d（默认跳过，--force 可重算）" % len(have))
        except sqlite3.OperationalError as e:
            print("   ⚠ 读 pdf_sha256 失败（%s）—— 可能库还没跑迁移，请先重启 8765" % e)
            return 2

    t0 = time.perf_counter()
    done = skipped = orphan = failed = 0
    pending: list[tuple[str, str]] = []  # (sha256, property_no)

    for i, p in enumerate(pdfs, 1):
        no = p.stem
        if not args.force and no in have:
            skipped += 1
            continue
        try:
            if p.stat().st_size <= 0:
                failed += 1
                continue
            pending.append((sha256_of_file(p), no))
            done += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if failed <= 3:
                print("   ⚠ 读失败 %s：%s: %s" % (p.name, type(e).__name__, e))
        if i % 500 == 0:
            el = time.perf_counter() - t0
            print("   … %d/%d（已算 %d，用时 %.1fs）" % (i, len(pdfs), done, el))

    # 批量写库（只更新库里真实存在的房源号）
    written = 0
    if pending and not args.dry_run:
        cur = con.cursor()
        for sha, no in pending:
            cur.execute("UPDATE properties SET pdf_sha256=? WHERE property_no=?", (sha, no))
            if cur.rowcount == 0:
                orphan += 1
            else:
                written += 1
        con.commit()
    elif pending:
        # dry-run：只统计哪些房号在库里存在
        for _sha, no in pending:
            r = con.execute(
                "SELECT 1 FROM properties WHERE property_no=?", (no,)
            ).fetchone()
            if r:
                written += 1
            else:
                orphan += 1

    el = time.perf_counter() - t0
    print()
    print("② 结果（用时 %.1fs）：" % el)
    print("   · 本次算出指纹：%d 份" % done)
    print("   · 已回填/将回填：%d 行" % written)
    print("   · 库中无此房号（幽灵 PDF，未写库）：%d 份" % orphan)
    print("   · 跳过（已有指纹）：%d 份" % skipped)
    print("   · 读失败：%d 份" % failed)
    if args.dry_run:
        print("   ⚠ --dry-run：未写库")

    # 覆盖率
    try:
        tot = con.execute("SELECT COUNT(*) c FROM properties").fetchone()["c"]
        got = con.execute(
            "SELECT COUNT(*) c FROM properties WHERE pdf_sha256 IS NOT NULL AND pdf_sha256 <> ''"
        ).fetchone()["c"]
        print("   · 库内 PDF 指纹覆盖率：%d / %d（%.1f%%）"
              % (got, tot, (got * 100.0 / tot) if tot else 0.0))
    except sqlite3.OperationalError:
        pass

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
