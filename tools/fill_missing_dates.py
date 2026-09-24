# -*- coding: utf-8 -*-
"""fill_missing_dates：补齐「老进程未写 reg/chg ISO」的历史缺口（v1.9.81 · U6）。

背景
----
老版本抓取进程不写 `reg_date_iso` / `chg_date_iso`（平台登録日 / 変更日），
导致这些房源在「按日期筛选」里永远捞不到（线上 date=all 与 total 曾差 ~1192 条）。
勇哥 09-25 批注：**做，只这一次**。

本工具**只做本地补齐**（读 detail_json 里已抓到的 registration_date / change_date），
**不碰 REINS**，因此可在维护时段运行。detail_json 也没有的行 → 报告为「需先补详情」，
交由批次1 任务4（欠账补抓 / 阶段B 补详情）处理。

用法
----
  python tools/fill_missing_dates.py            # dry-run（只报不写）
  python tools/fill_missing_dates.py --apply    # 真写库（--apply 前会自动备份）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jproperty.db"


def _iso(v) -> str | None:
    """'20260912' / '2026/09/12' / '2026-09-12' → '2026-09-12'；认不出返回 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("-", "—", "None"):
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        y, m, d = digits[0:4], digits[4:6], digits[6:8]
        if 1900 <= int(y) <= 2100 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{m}-{d}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run）")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print("✗ 找不到库：%s" % db)
        return 2

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT property_no, detail_json, reg_date_iso, chg_date_iso FROM properties"
        " WHERE reg_date_iso IS NULL OR chg_date_iso IS NULL").fetchall()

    print("=== U6 缺口补齐（老进程未写 reg/chg ISO）===")
    print("  待检查行（reg 或 chg 至少一个为空）= %d" % len(rows))

    plan: list[tuple] = []
    need_detail = 0
    for r in rows:
        dj = r["detail_json"]
        d = {}
        if dj:
            try:
                d = json.loads(dj) or {}
            except Exception:
                d = {}
        new_reg = r["reg_date_iso"] or _iso(d.get("registration_date"))
        new_chg = r["chg_date_iso"] or _iso(d.get("change_date"))
        if new_reg or new_chg:
            plan.append((r["property_no"], new_reg, new_chg))
        else:
            need_detail += 1

    only_reg = sum(1 for p in plan if p[1] and not p[2])
    only_chg = sum(1 for p in plan if p[2] and not p[1])
    both = sum(1 for p in plan if p[1] and p[2])
    print("  可本地补齐            = %d（reg+chg 都有 %d / 仅 reg %d / 仅 chg %d）"
          % (len(plan), both, only_reg, only_chg))
    print("  detail_json 也没有 → 需先补详情 = %d" % need_detail)

    if plan[:5]:
        print("  样本：")
        for no, rg, cg in plan[:5]:
            print("    %s → reg=%s chg=%s" % (no, rg, cg))

    if not args.apply:
        print("\n  （dry-run：未写库。确认无误后加 --apply）")
        con.close()
        return 0

    # --apply 前自动备份（铁律）
    bak = db.with_suffix(db.suffix + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(db, bak)
    print("\n  已备份 → %s" % bak)

    n = 0
    for no, rg, cg in plan:
        sets, vals = [], []
        if rg:
            sets.append("reg_date_iso=?")
            vals.append(rg)
        if cg:
            sets.append("chg_date_iso=?")
            vals.append(cg)
        vals.append(no)
        con.execute("UPDATE properties SET %s WHERE property_no=?" % ",".join(sets), vals)
        n += 1
    con.commit()

    left = con.execute(
        "SELECT COUNT(*) FROM properties"
        " WHERE reg_date_iso IS NULL AND chg_date_iso IS NULL").fetchone()[0]
    print("  ✓ 已补齐 %d 条" % n)
    print("  剩余「reg/chg 双空」（需先补详情）= %d" % left)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
