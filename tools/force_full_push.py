# -*- coding: utf-8 -*-
"""force_full_push：离线「强制同步上传」——把本地全部房源 (+AI) 重推覆盖线上。

为什么需要：UI 的「强制同步上传」按钮走 /api/publish，需要浏览器登录态；
本工具**离线直调 core.publisher.publish**，绕开登录态，供无人值守/运维场景用。

背景（2026-09-24 实证）：传输的增量腿只认 `COALESCE(last_seen,first_seen) > 水位线`，
对「已存在行的字段更新」（日期同步写 reg/chg_date_iso、复活写 is_active 等）不重推
→ 线上永久旧值（本地 735 vs 线上 561，差 174）。全量重传是唯一对齐手段。

用法：
  python tools/force_full_push.py            # 房源 full + AI 全量（= UI「强制同步上传」）
  python tools/force_full_push.py --incr     # 只增量（调试用）
  python tools/force_full_push.py --no-ai    # 不推 AI
"""
from __future__ import annotations

import io
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core import config as cfgmod          # noqa: E402
from core.publisher import publish         # noqa: E402


def main() -> int:
    mode = "incr" if "--incr" in sys.argv else "full"
    force_ai = "--no-ai" not in sys.argv

    cfg = cfgmod.load()
    db = Path(str(cfgmod.paths(cfg)["db"]))
    if not db.exists():
        print("✗ 找不到本地库：%s" % db)
        return 2
    print("→ 目标库=%s" % db)
    print("→ endpoint=%s" % ((cfg.get("publish") or {}).get("endpoint") or "(未配置)"))
    print("→ mode=%s  force_ai=%s" % (mode, force_ai))

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    def say(*a, **_k):
        print(" ".join(str(x) for x in a), flush=True)

    try:
        res = publish(cfg, con, mode=mode, log=say, force_ai=force_ai)
    finally:
        con.close()

    ok = bool(res.get("ok"))
    print("----")
    print("ok=%s  sent=%s  total=%s  ai=%s" % (
        ok, res.get("sent"), res.get("total"),
        (res.get("ai") or {}).get("ok")))
    if not ok:
        print("errors=%s" % (res.get("errors") or res.get("error")))
    print("RESULT_OK" if ok else "RESULT_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
