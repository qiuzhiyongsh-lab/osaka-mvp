# -*- coding: utf-8 -*-
"""v1.6 双库快照 / 受控回写工具（PRD 14 §6，方案 A）。

设计（双版本并存，主库冻结）：
  主库（冻结）：data/jproperty.db        —— 8765 稳定版专用；只有本工具的 promote 能写它
  工作库（试验）：data/jproperty_v16.db  —— 8766 试验版专用；v1.6 开发写这里，脏数据隔离

命令：
  python tools/db_guard.py snapshot   # 主库→工作库（首次建立快照；已存在则报错，--force 覆盖）
  python tools/db_guard.py verify     # 对比两库表/行数，输出漂移报告
  python tools/db_guard.py promote    # 工作库→主库（受控回写；自动先备主库，需 --yes 确认）
  python tools/db_guard.py rollback   # 主库→工作库（丢弃工作库的全部试验改动，需 --yes 确认）

所有写操作前都会对「目标库」做一次备份（.backups/db_guard/），可随时恢复。
运维经理（OPS）负责执行 snapshot/promote/rollback 并登记；详见 docs/标准/05。
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "data" / "jproperty.db"
WORK = ROOT / "data" / "jproperty_v16.db"
BACKUP_DIR = ROOT / ".backups" / "db_guard"


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup(src: Path, label: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f"{label}_{_ts()}_{src.name}"
    shutil.copy2(src, dst)
    return dst


def _counts(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out: dict[str, int] = {}
        for t in tables:
            try:
                out[t] = con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
            except Exception:
                out[t] = -1
        return out
    finally:
        con.close()


def cmd_snapshot(args) -> int:
    if not MAIN.exists():
        print(f"[!] 主库不存在：{MAIN}（先让 8765 跑过至少一轮）")
        return 2
    if WORK.exists() and not args.force:
        print(f"[!] 工作库已存在：{WORK}\n    要重建快照请加 --force（会覆盖试验数据）。")
        return 2
    if WORK.exists():
        b = _backup(WORK, "work_before_snapshot")
        print(f"· 已备份旧工作库：{b.name}")
    shutil.copy2(MAIN, WORK)
    kb = WORK.stat().st_size // 1024
    print(f"[✓] 已建立工作库快照：{MAIN.name} → {WORK.name}（{kb} KB）")
    return 0


def cmd_verify(args) -> int:
    if not MAIN.exists():
        print("[!] 主库不存在"); return 2
    if not WORK.exists():
        print("[!] 工作库不存在，先跑 snapshot"); return 2
    m, w = _counts(MAIN), _counts(WORK)
    print(f"主库 {MAIN.name} / 工作库 {WORK.name} 行数对比：")
    allt = sorted(set(m) | set(w))
    maxlen = max((len(t) for t in allt), default=10)
    drift = False
    for t in allt:
        mc, wc = m.get(t, "—"), w.get(t, "—")
        flag = "" if mc == wc else "  ⚠ 不一致"
        if mc != wc:
            drift = True
        print(f"  {t:<{maxlen}}  主={mc:<8} 工={wc:<8}{flag}")
    if drift:
        print("[!] 两库存在漂移（试验版改动尚未回写，或主库有新抓取）")
    else:
        print("[✓] 两库一致")
    return 0


def cmd_promote(args) -> int:
    if not WORK.exists():
        print("[!] 工作库不存在，无法回写"); return 2
    if not args.yes:
        print("[!] 受控回写会覆盖主库（冻结库）。确认请加 --yes。")
        return 2
    b = _backup(MAIN, "main_before_promote")
    print(f"· 已备份主库：{b.name}")
    shutil.copy2(WORK, MAIN)
    print(f"[✓] 已受控回写：{WORK.name} → {MAIN.name}")
    return 0


def cmd_rollback(args) -> int:
    if not MAIN.exists():
        print("[!] 主库不存在"); return 2
    if not args.yes:
        print("[!] 回滚会丢弃工作库的全部试验改动。确认请加 --yes。")
        return 2
    b = _backup(WORK, "work_before_rollback")
    print(f"· 已备份工作库：{b.name}")
    shutil.copy2(MAIN, WORK)
    print(f"[✓] 已回滚工作库：{MAIN.name} → {WORK.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="v1.6 双库快照 / 受控回写工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot").add_argument("--force", action="store_true")
    sub.add_parser("verify")
    p = sub.add_parser("promote"); p.add_argument("--yes", action="store_true")
    r = sub.add_parser("rollback"); r.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    return {
        "snapshot": cmd_snapshot,
        "verify": cmd_verify,
        "promote": cmd_promote,
        "rollback": cmd_rollback,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
