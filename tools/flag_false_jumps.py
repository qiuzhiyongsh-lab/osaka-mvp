# -*- coding: utf-8 -*-
"""v1.9.77 F7：对 changes 表「价格跳变」行打 flag（保留原行，不删）。

用法：
  python tools/flag_false_jumps.py                       # 默认区间 2026-09-15 ~ 2026-09-16
  python tools/flag_false_jumps.py 2026-09-15 2026-09-16 # 自定义起止（含全天）
  python tools/flag_false_jumps.py --dry                # 仅统计不写入

背景（PRD osaka_mvp_pdf_mismatch_prd.md §F7）：run74(2026-09-15) 列表重排导致串号，
      价格字段错配 → changes 表出现大批 ±40% 价格跳变，实为脏数据。
      标记 flag='false_jump_candidate' 便于前端/对账区分「真实跳变」与「疑似脏数据」，
      原行保留、不删，待勇哥裁决。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfgmod
from core import store as storemod


def main(argv: list) -> None:
    cfg = cfgmod.load()
    store = storemod.Store(cfgmod.paths(cfg)["db"])
    default_start, default_end = "2026-09-15 00:00:00", "2026-09-16 23:59:59"
    dry = "--dry" in argv
    start, end = default_start, default_end
    # 位置参数（起/止）覆盖默认区间
    pos = [a for a in argv if not a.startswith("--")]
    if len(pos) >= 2:
        start, end = pos[0], pos[1]
    n = store.flag_price_jumps(start, end, flag="false_jump_candidate", dry_run=dry)
    if dry:
        print("[DRY] 区间 %s ~ %s 将标记 %d 行（未写入）" % (start, end, n))
    else:
        print("已标记 %d 行 price 跳变为 false_jump_candidate（区间 %s ~ %s）"
              % (n, start, end))


if __name__ == "__main__":
    main(sys.argv[1:])
