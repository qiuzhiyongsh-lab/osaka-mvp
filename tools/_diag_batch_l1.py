# -*- coding: utf-8 -*-
"""回归：对一批随机 PDF 只跑 L1（文字层正则，快、不调 OCR），确认 v1.9.34 的
正则放宽没有把别的 PDF 抓坏（尤其是 会社住所/所在地/最寄駅1 不能被污染）。
"""
from pathlib import Path
import sys, glob
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config as cfgmod
from core.store import Store
from core.ai_structure_store import AIStructureStore
from core import ai_pipeline, pdf_local_extract as L1

CFG = cfgmod.load(); PATHS = cfgmod.paths(CFG)
STORE = Store(PATHS["db"])
AI_STORE = AIStructureStore(Path(PATHS["db"]).parent / "ai_pdf_store.db")
le = CFG.get("local_extract") or {}
cfg = dict(le); cfg["ai"] = CFG.get("ai") or {}; cfg["ocr_enabled"] = False  # 只看 L1

pdfs = sorted(glob.glob(str(Path(PATHS["attachments"]) / "*.pdf")))
# 跳过已深挖的；取若干随机
import random
random.seed(7)
sample = [p for p in pdfs if "300139241123" not in p][:0]
sample = random.sample([p for p in pdfs if "300139241123" not in p], 8)

WANT = ["管理体制", "駐車場", "現況", "設備・条件", "建物引渡", "共用施設",
        "注意事項", "所在地", "最寄駅1", "構造（原文）", "バルコニー", "会社住所"]

for p in sample:
    no = Path(p).stem
    try:
        res = ai_pipeline.run_one(no, PATHS, cfg, AI_STORE, force=True,
                                  allow_cloud=False, main_store=STORE)
    except Exception as e:
        print(f"[ERR] {no}: {e}")
        continue
    f = res.get("fields") or {}
    ex = L1.extract(L1.text_of(p))
    bad = []
    for c in ("会社住所", "所在地", "最寄駅1"):
        v = (f.get(c) or {}).get("value")
        if v and any(k in v for k in ("大臣", "免許", "協会", "会員", "流通")):
            bad.append(f"{c}={v!r}(疑似污染)")
    print(f"{no}: L1通过 {len(f)} 列 | 污染:{bad if bad else '无'} | "
          f"会社={ (f.get('会社住所') or {}).get('value','-')!r}"
          f" 所在={ (f.get('所在地') or {}).get('value','-')!r}"
          f" 駅={ (f.get('最寄駅1') or {}).get('value','-')!r}")
