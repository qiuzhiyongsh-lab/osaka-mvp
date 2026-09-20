# -*- coding: utf-8 -*-
"""v1.9.34 诊断：对 300139241123 跑 run_one（强制重跑），看扩字段后的效果。

重点对比勇哥点名的列是否进卡：
  管理体制 / 駐車場 / 現況 / 設備・条件 / 建物引渡 / 共用施設 / 注意事項
  / 所在地 / 最寄駅1 / 構造（原文） / バルコニー / 会社住所
"""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfgmod
from core.store import Store
from core.ai_structure_store import AIStructureStore
from core import ai_pipeline

NO = "300139241123"
CFG = cfgmod.load()
PATHS = cfgmod.paths(CFG)
STORE = Store(PATHS["db"])
AI_STORE = AIStructureStore(Path(PATHS["db"]).parent / "ai_pdf_store.db")

le = CFG.get("local_extract") or {}
cfg = dict(le)
cfg["ai"] = CFG.get("ai") or {}
cfg["key_gate"] = True

WANT = ["管理体制", "駐車場", "現況", "設備・条件", "建物引渡", "共用施設",
        "注意事項", "所在地", "最寄駅1", "構造（原文）", "バルコニー", "会社住所"]


def main():
    res = ai_pipeline.run_one(NO, PATHS, cfg, AI_STORE,
                              force=True, allow_cloud=False, main_store=STORE)
    print(f"[route] {res['route']}  ms={res['ms']}  local_hit={res.get('local_hit')}")
    fields = res.get("fields") or {}
    print(f"[fields] 过闸门 {len(fields)} 列；被驳回 {len(res.get('rejected') or [])} 列")
    print("\n=== 勇哥点名的优先列 ===")
    for c in WANT:
        f = fields.get(c)
        if f:
            print(f"  ✅ {c} = {f['value']!r}  (level={f['level']})")
        else:
            print(f"  ❌ {c} = 缺失")
    print("\n=== 全部过闸门字段 ===")
    for c, f in fields.items():
        print(f"  {c} = {f['value']!r}  (level={f['level']})")
    print("\n=== 被驳回（待核对）===")
    for c, v, r in (res.get("rejected") or []):
        print(f"  {c} = {v!r}  -> {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
