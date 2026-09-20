# -*- coding: utf-8 -*-
"""复刻 api_ai_run 的本地优先落库逻辑，对指定房源强制重跑并持久化。

用于把 v1.9.35 改进后的抽取结果真正写进 ai_pdf_store.db（之前 _diag_extract 只打印未落库）。
同 DB 不抢端口（与 8765 共享库），写操作为短事务，超时 15s 自动退避。
"""
import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfgmod
from core.store import Store
from core.ai_structure_store import AIStructureStore
from core import ai_pipeline

CFG = cfgmod.load()
PATHS = cfgmod.paths(CFG)
STORE = Store(PATHS["db"])
AI_STORE = AIStructureStore(Path(PATHS["db"]).parent / "ai_pdf_store.db")
# 给 sqlite 写留超时，避免与 8765 抢锁直接失败
try:
    AI_STORE._conn.execute("PRAGMA busy_timeout=15000")
except Exception:
    pass


def run_one_persist(property_no: str):
    if STORE.get_property(property_no) is None:
        print(f"[SKIP] {property_no} 不在主库"); return
    pdf = PATHS["attachments"] / f"{property_no}.pdf"
    if not pdf.exists():
        print(f"[SKIP] {property_no} 无落盘 PDF（{pdf}）"); return
    le = CFG.get("local_extract") or {}
    cfg = dict(le)
    cfg["ai"] = CFG.get("ai") or {}
    cfg["prefer_local"] = True
    cfg["key_gate"] = True
    t0 = time.time()
    res = ai_pipeline.run_one(property_no, PATHS, cfg, AI_STORE,
                              force=True, allow_cloud=False, main_store=STORE)
    ms = int((time.time() - t0) * 1000)
    fields = res.get("fields") or {}
    local_hit = bool(res.get("local_hit"))
    rejected = res.get("rejected") or []
    print(f"[{property_no}] local_hit={local_hit} 字段数={len(fields)} "
          f"rejected={len(rejected)} ({ms}ms)")
    if local_hit:
        ai_pipeline.save_fields(AI_STORE, property_no, res)
        print(f"  -> 已持久化 {len(fields)} 字段")
    else:
        print(f"  -> 未落库（local_hit=False）")


if __name__ == "__main__":
    targets = sys.argv[1:] or ["300139241123"]
    for p in targets:
        run_one_persist(p)
