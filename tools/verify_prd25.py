# -*- coding: utf-8 -*-
"""PRD 25 · R1 验收脚本：AI 重跑**不得覆盖人工改过的字段**。

为什么单独立一个验收脚本
------------------------
勇哥的规则是硬规则（"手工改过以手工为准"），而覆盖是**静默发生**的——
夜里 AI 重跑一次，页面上看起来正常，值却已经回到 AI 的错值，没人会发现。
所以必须有可复跑的自动断言，而不是"我看过了没问题"。

用法::
    python tools/verify_prd25.py

退出码 0 = 全绿；1 = 有断言失败（**不许交付**）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ai_structure_store import AIStructureStore, merge_structure   # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    tag = "✅" if cond else "❌"
    print(f"  {tag} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def structure_of(values: dict) -> dict:
    """构造最小 structure：{"高亮":[{col,value,level}]}"""
    return {"groups": [{"key": "g1", "cn": "基本", "fields": [
        {"col": c, "value": v, "level": "ok", "edited": False, "note": ""}
        for c, v in values.items()]}]}


def field_of(rec: dict, col: str) -> dict | None:
    for g in rec["structure"].get("groups", []):
        for f in g.get("fields", []):
            if f.get("col") == col:
                return f
    return None


def main() -> int:
    print("=" * 68)
    print("PRD 25 · R1 验收：人工值保护（AI 重跑不得覆盖手工修改）")
    print("=" * 68)

    # 用临时库，绝不碰 data/ai_pdf_store.db
    with tempfile.TemporaryDirectory() as td:
        store = AIStructureStore(Path(td) / "test.db")

        # ---------- 场景 1：AI 先出一版 ----------
        print("\n[场景1] AI 首轮产出")
        store.upsert("300000000001", structure=structure_of({
            "専有面積（㎡）": 82.06, "管理費（円/月）": 12000, "現況": "居住中"}),
            radar={"a": 5}, conclusion="AI 结论 v1", anomalies=[], overall=70,
            source_file="hashAAA", extracted_at="2026-09-20")
        rec = store.get("300000000001")
        check("AI 首轮面积写入", field_of(rec, "専有面積（㎡）")["value"] == 82.06)

        # ---------- 场景 2：勇哥手工改错值 ----------
        print("\n[场景2] 勇哥手工把管理費改成真实值 9800")
        store.edit_field("300000000001", "管理費（円/月）", 9800, editor="yongge")
        rec = store.get("300000000001")
        check("手工值已生效", field_of(rec, "管理費（円/月）")["value"] == 9800)
        check("整体 edited=1", rec["edited"] is True)
        check("该字段 edited=True", field_of(rec, "管理費（円/月）")["edited"] is True)
        check("人工后等级降为 ok", field_of(rec, "管理費（円/月）")["level"] == "ok")

        # ---------- 场景 3：夜里 AI 重跑（PDF 更新 → hash 变了）----------
        print("\n[场景3] 夜里 AI 重跑，企图写回 12000（连同新批示的面积）")
        store.upsert("300000000001", structure=structure_of({
            "専有面積（㎡）": 99.99, "管理費（円/月）": 12000, "築年": "1998"}),
            radar={"a": 6}, conclusion="AI 结论 v2", anomalies=[], overall=75,
            source_file="hashBBB", extracted_at="2026-09-21")
        rec = store.get("300000000001")
        f = field_of(rec, "管理費（円/月）")
        check("★ 手工值未被 AI 覆盖", f["value"] == 9800, f"实际={f['value']}")
        check("★ 仍标记为人工", f["edited"] is True)
        check("AI 能更新未编辑字段", field_of(rec, "専有面積（㎡）")["value"] == 99.99)
        check("AI 能新增字段", field_of(rec, "築年") is not None)
        check("整行 edited 仍为 1", rec["edited"] is True)
        # 结论属解读层，允许更新（人工改的是字段值，不是结论）
        check("解读层结论已更新到 v2", rec["conclusion"] == "AI 结论 v2")

        # ---------- 场景 4：旧有但本轮没抽出来的字段 ----------
        print("\n[场景4] 本轮 AI 漏抽「現況」（旧值不能凭空消失）")
        store.upsert("300000000001", structure=structure_of({
            "専有面積（㎡）": 99.99, "管理費（円/月）": 12000}),
            radar=None, conclusion="AI 结论 v3", anomalies=[], overall=70,
            source_file="hashCCC", extracted_at="2026-09-21")
        rec = store.get("300000000001")
        gk = field_of(rec, "現況")
        check("旧字段被保留而非丢弃", gk is not None)
        check("未编辑的沿用值降级 warn", gk and gk["level"] == "warn", gk and gk.get("note"))
        check("已人工的面积仍然安全", field_of(rec, "管理費（円/月）")["value"] == 9800)

        # ---------- 场景 5：D3 待办队列 ----------
        print("\n[场景5] D3 待办队列（PDF 变了才重跑）")
        hashes = {"300000000001": "hashCCC", "300000000002": "hashNEW"}
        pend = store.pending_property_nos(hashes)
        check("hash 未变的已跳过", "300000000001" not in pend, f"pend={pend}")
        check("新/变更的进待办", "300000000002" in pend)

        # ---------- 场景 6：编辑审计链路 ----------
        print("\n[场景6] 编辑审计留痕")
        logs = store.edit_log("300000000001")
        check("审计日志有记录", len(logs) >= 1)
        # 注意：ai_edit_log 的 old/new 列是 TEXT 亲和性，int 落库会被转成 str
        _L = logs[0] if logs else {}
        check("记录了旧值→新值",
              str(_L.get("old_value")) == "12000" and str(_L.get("new_value")) == "9800",
              f"{_L.get('old_value')} → {_L.get('new_value')}")
        check("记录了操作人", _L.get("editor") == "yongge", str(_L.get("editor")))

        store.close()          # Windows 上不关连接 → 临时库删不掉（WinError 32）

    # ---------- 场景 7：merge_structure 纯函数 ----------
    print("\n[场景7] merge_structure 合并口径")
    old = structure_of({"a": 1, "b": 2})
    old["groups"][0]["fields"][0]["edited"] = True     # a 被人工改过
    new = structure_of({"a": 111, "b": 222, "c": 3})
    merged, info = merge_structure(old, new)
    vals = {f["col"]: f["value"] for f in merged["groups"][0]["fields"]}
    check("人工列保留旧值", vals.get("a") == 1, f"a={vals.get('a')}")
    check("未编辑列取新值", vals.get("b") == 222)
    check("新列直接进来", vals.get("c") == 3)
    check("统计 kept/updated 正确", info["kept"] == ["a"] and "b" in info["updated"],
          str(info))

    print("\n" + "=" * 68)
    if FAILS:
        print(f"❌ R1 验收未通过，{len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ R1 验收全绿：人工值在 AI 重跑后完好无损")
    return 0


if __name__ == "__main__":
    sys.exit(main())
