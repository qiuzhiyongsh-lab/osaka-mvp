# -*- coding: utf-8 -*-
"""v1.9.35 字段覆盖实证：对 3 个真实 PDF 跑 run_one（L1+L2 OCR），输出：
 1) 命中字段数 / 优先列(12)命中 / 全字段对照表
 2) 对「PDF 常见但 AI 表可能无列」的关键词做原文扫描，证明缺口（ペット/借地/道路/セキュリティ...）
同 DB 不抢端口，短事务 + busy_timeout。
"""
import sys, time, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfgmod
from core.store import Store
from core.ai_structure_store import AIStructureStore
from core import ai_pipeline
from core import pdf_local_extract as ple

CFG = cfgmod.load()
PATHS = cfgmod.paths(CFG)
STORE = Store(PATHS["db"])
AI_STORE = AIStructureStore(Path(PATHS["db"]).parent / "ai_pdf_store.db")
try:
    AI_STORE._conn.execute("PRAGMA busy_timeout=15000")
except Exception:
    pass

# 勇哥点名 + 投资房源高频、但现有 60 列可能缺的字段
GAP_KEYWORDS = {
    "ペット（飼育可/許容飼育動物）": ["ペット", "飼育", "犬", "猫", "小動物", "爬虫類"],
    "借地権": ["借地権", "借地期間", "借地期限", "借地"],
    "権利（所有権/借地）": ["所有権", "地上権"],
    "前面道路（幅員/接道）": ["前面道路", "道路幅員", "接道", "公道", "私道"],
    "セキュリティ（オートロック等）": ["セキュリティ", "オートロック", "防犯", "モニター付"],
    "管理形態（全部委託/自主管理）": ["管理形態", "全部委託", "一部委託", "自主管理"],
    "引渡時期": ["引渡時期", "引渡し時期"],
    "登記（済/未）": ["登記", "未登記", "済登記"],
    "建蔽率/容積率": ["建蔽率", "容積率", "建ぺい率"],
    "地目": ["地目"],
    "ガス/給排水/エレベーター": ["ガス", "給排水", "エレベーター", "都市ガス", "プロパン"],
    "駐輪場/バイク置場": ["駐輪場", "バイク置場", "ミニバイク"],
    "敷地権/バルコニー方向": ["敷地権", "バルコニー方向", "南向"],
    "リフォーム/リノベ": ["リフォーム", "リノベーション", "改装"],
}
IMPORTANT = ["管理体制","駐車場","現況","設備・条件","建物引渡","共用施設","注意事項",
             "所在地","最寄駅1","構造（原文）","バルコニー","会社住所"]

def raw_scan_text(property_no):
    """用 L1 文字层抽取拿到原文；足以证明文字层是否含关键词。"""
    try:
        pdf = PATHS["attachments"] / f"{property_no}.pdf"
        text = ple.text_of(pdf) if hasattr(ple, "text_of") else ""
    except Exception:
        text = ""
    return text

def run_one_persist(property_no):
    if STORE.get_property(property_no) is None:
        print(f"[SKIP] {property_no} 不在主库"); return
    le = CFG.get("local_extract") or {}
    cfg = dict(le); cfg["ai"] = CFG.get("ai") or {}; cfg["prefer_local"]=True; cfg["key_gate"]=True
    t0=time.time()
    res = ai_pipeline.run_one(property_no, PATHS, cfg, AI_STORE, force=True, allow_cloud=False, main_store=STORE)
    ms=int((time.time()-t0)*1000)
    fields = res.get("fields") or {}
    local_hit = bool(res.get("local_hit"))
    rej = res.get("rejected") or []
    if local_hit:
        ai_pipeline.save_fields(AI_STORE, property_no, res)
    # 优先列命中
    imp_hit = [k for k in IMPORTANT if k in fields]
    print(f"\n{'='*70}\n[{property_no}] {STORE.get_property(property_no)['building_name']}")
    print(f"local_hit={local_hit} 字段数={len(fields)} rejected={len(rej)} ({ms}ms)")
    print(f"优先列命中 {len(imp_hit)}/12: {imp_hit}")
    # 全字段表
    print("--- 已抽取字段对照（列 | 值）---")
    for k in sorted(fields):
        v = str(fields[k])
        print(f"  {k}: {v[:48]}")
    # 缺口关键词扫描（文字层）
    text = raw_scan_text(property_no)
    print("--- 缺口字段：PDF 原文是否含该信息（证明能否补）---")
    for label, kws in GAP_KEYWORDS.items():
        hit = [kw for kw in kws if kw in text]
        print(f"  [{'含' if hit else '不含'}] {label}" + (f" <- {hit}" if hit else ""))
    return dict(fields=fields, local_hit=local_hit)

if __name__ == "__main__":
    targets = sys.argv[1:] or ["300139241123","300140713692","300139229454"]
    for p in targets:
        try:
            run_one_persist(p)
        except Exception as e:
            import traceback; traceback.print_exc()
