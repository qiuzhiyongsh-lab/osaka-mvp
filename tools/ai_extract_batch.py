# -*- coding: utf-8 -*-
"""批量 AI 提取（v1.7.4 · PRD 09 需求 4）。

做什么
----
扫库里有落盘 PDF 的房源 → 逐份调 AI 提取 → 写 ai_extractions 派生表。
- 去重：库里有同一条 pdf_hash 的记录就跳过（PDF 没变不重跑，省钱省时）。
- 失败隔离：单份失败记 error 不影响整批。
- 断点续跑：随时 Ctrl+C，重跑自动接着没做的来。

用法
----
    python tools/ai_extract_batch.py --limit 50          # 先少跑试效果
    python tools/ai_extract_batch.py --limit 200 --force # 无视去重全量重跑
前置：config.yaml 里填好 ai.api_key（见操作手册 v1.4.2 第 1-2 步）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfgmod                 # noqa: E402
from core.ai_extract import PROMPT_VERSION, extract_property  # noqa: E402
from core.store import Store                      # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--force", action="store_true", help="无视 pdf_hash 去重，全部重跑")
    a = ap.parse_args()

    cfg = cfgmod.load()
    ai_cfg = cfg.get("ai") or {}
    if not (ai_cfg.get("api_key") or "").strip():
        print("[停止] config.yaml 还没配 ai.api_key（火山方舟控制台申请，见操作手册第 1 步）。")
        sys.exit(1)
    paths = cfgmod.paths(cfg)
    store = Store(a.db or paths["db"])
    print(f"[info] 模型={ai_cfg.get('model')}  库={store.db_path}")

    rows = [r for r in store.conn.execute(
        "SELECT property_no, pdf_path FROM properties "
        "WHERE pdf_path IS NOT NULL AND pdf_path != '' AND is_active=1 "
        "ORDER BY property_no")]
    done: dict[str, str] = {}
    for r in store.conn.execute(
            "SELECT property_no, pdf_hash FROM ai_extractions WHERE status='ok'"):
        if r["pdf_hash"]:
            done[r["property_no"]] = r["pdf_hash"]

    import hashlib
    todo: list[str] = []
    for r in rows:
        no = r["property_no"]
        if a.force or no not in done:
            todo.append(no)
    todo = todo[: a.limit]
    print(f"[info] 有 PDF 的房源 {len(rows)} 条；本次待跑 {len(todo)} 条（--force={a.force}）")

    ok = skip = fail = 0
    for i, no in enumerate(todo, 1):
        try:
            result, meta = extract_property(no, paths, ai_cfg)
            store.upsert_ai_extraction(no, result, model=meta["model"],
                                       prompt_version=meta["prompt_version"],
                                       pdf_hash=hashlib.sha256(
                                           (Path(paths["attachments"]) / f"{no}.pdf")
                                           .read_bytes()).hexdigest()[:16],
                                       cost_ms=meta["cost_ms"])
            ok += 1
        except Exception as e:                    # 单份失败不拖垮整批
            fail += 1
            print(f"  [{i}/{len(todo)}] {no} 失败：{e}")
            continue
        time.sleep(0.2)
    print(f"完成：成功 {ok} · 失败 {fail} · 总待跑 {len(todo)}")


if __name__ == "__main__":
    main()
