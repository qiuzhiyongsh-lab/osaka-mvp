# -*- coding: utf-8 -*-
"""把桌面《大阪房源PDF数据总表.xlsx》的 AI 识别结果灌进**独立库** data/ai_pdf_store.db。

用法::
    python tools/import_ai_pdf_excel.py                      # 用默认路径（桌面）
    python tools/import_ai_pdf_excel.py "D:/xxx/总表.xlsx"    # 指定文件

做了什么
--------
  ① 读「字段说明」→ 建 59 列的四语言字段字典（一次覆盖，重跑即刷新）
  ② 读「物件数据」193 行 → 逐条落 AI 结构（按 group 组织，空值不落字段）
  ③ 读「OCR待核对」+「AI vs DB差异核对」→ 给字段打异常等级：
       danger = 特别存疑（OCR 判定"建议留空"，值基本不可信）→ 红字黄底
       warn   = 存疑（"可人工查证后补录"，或数值勾稽对不上） → 红字
  ④ 再做一轮**自校验勾稽**（价格/单价/面积/坪/月度费用），抓出表里没列的错
  ⑤ 调 core.ai_score 算六维雷达图 + 约 200 字结论

⚠ 只写独立库，绝不碰主库 jproperty.db；主表人工数据不会被覆盖。
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.ai_structure_store import AIStructureStore   # noqa: E402
from core import ai_score                              # noqa: E402

DEFAULT_XLSX = Path(r"C:\Users\25374\Desktop\大阪房源PDF数据总表.xlsx")
DB_PATH = ROOT / "data" / "ai_pdf_store.db"

# ---------------- 59 列的展示分组（顺序即页面自上而下顺序）----------------
GROUPS: list[tuple[str, str, list[str]]] = [
    ("basic", "基本信息", [
        "物件番号（ファイル名）", "物件種目", "物件種別補足", "物件名",
        "所在地", "区", "公開状況"]),
    ("price", "价格信息", [
        "価格（税込・万円）", "価格（円）", "単価（円/㎡）", "単価（円/坪）"]),
    ("transit", "交通", ["最寄駅1"]),
    ("layout", "户型与面积", [
        "間取り", "専有面積（㎡）", "専有面積（坪）", "土地面積（㎡）",
        "建物面積（㎡）", "バルコニー", "所在階", "地上階数"]),
    ("building", "建筑与年代", [
        "築年月（原文）", "築年（西暦）", "構造（原文）", "構造",
        "総戸数（戸）", "用途地域"]),
    ("cost", "管理与费用", [
        "管理費（円/月）", "積立金（円/月）", "その他（円/月）",
        "管理費・積立金等合計（円/月）", "管理組合", "管理体制"]),
    ("status", "现状与交付", [
        "現況", "現況詳細", "契約形態", "建物引渡", "駐車場", "設備・条件"]),
    ("trade", "交易条件", ["取引形態", "報酬形態", "手数料", "広告"]),
    ("agency", "中介信息", [
        "問い合わせ先会社", "TEL", "FAX", "E-MAIL", "担当者連絡先", "会社住所"]),
    ("meta", "日期与来源", [
        "登録日", "変更日", "初回確認日", "最終確認日", "抽出日",
        "写真有無", "間取図有無", "地図有無", "ソースPDF"]),
    ("comment", "描述与备注", ["物件コメント", "注意事項"]),
]
# 反查：列 → (group_key, group_cn, 组内序号)
_COL2GRP: dict[str, tuple[str, str, int]] = {}
for _gk, _gcn, _cols in GROUPS:
    for _i, _c in enumerate(_cols):
        _COL2GRP[_c] = (_gk, _gcn, _i)

TSUBO_PER_SQM = 0.3025          # 1㎡ = 0.3025坪
SQM_PER_TSUBO = 1 / 0.3025      # 3.3058


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _clean(v):
    """单元格 → 展示字符串。None/空串 → ''（有则显示，无则不显）。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).replace("\xa0", " ").strip()


# ============================================================
# ① 字段字典
# ============================================================
def load_field_meta(ws) -> list[dict]:
    """字段说明表：第 1 行 banner、第 2 行表头、第 3 行起 59 列。"""
    out = []
    for r in range(3, ws.max_row + 1):
        col = ws.cell(row=r, column=2).value
        if not col:
            continue
        gk, gcn, gi = _COL2GRP.get(col, ("other", "其他", 99))
        out.append({
            "col_name": col,
            "seq": _num(ws.cell(row=r, column=1).value) or (r - 2),
            "group_key": gk,
            "group_cn": gcn,
            "zh": ws.cell(row=r, column=3).value or "",
            "zh_tw": ws.cell(row=r, column=4).value or "",
            "en": ws.cell(row=r, column=5).value or "",
            "ja": ws.cell(row=r, column=6).value or "",
            "source": ws.cell(row=r, column=8).value or "",
            "note": ws.cell(row=r, column=7).value or "",
        })
    return out


# ============================================================
# ② 异常清单
# ============================================================
def load_ocr_issues(ws) -> dict[tuple[str, str], dict]:
    """OCR待核对 → {(物件番号, 字段): {reason, advice, level}}"""
    m = {}
    for r in range(2, ws.max_row + 1):
        no = _clean(ws.cell(row=r, column=1).value)
        fld = _clean(ws.cell(row=r, column=2).value)
        if not no or not fld:
            continue
        advice = _clean(ws.cell(row=r, column=5).value)
        # 「建议留空」= 值基本是乱码，不可信 → danger（红字黄底）
        # 「可人工查证后补录」= 可能是真的但要人核 → warn（红字）
        level = "danger" if "留空" in advice else "warn"
        m[(no, fld)] = {
            "reason": _clean(ws.cell(row=r, column=4).value),
            "advice": advice,
            "level": level,
        }
    return m


def load_diff_issues(ws) -> dict[str, dict]:
    """AI vs DB差异核对 → {物件番号: {field, reason, level:warn}}"""
    m = {}
    for r in range(2, ws.max_row + 1):
        no = _clean(ws.cell(row=r, column=2).value)
        if not no:
            continue
        m[no] = {
            "field": _clean(ws.cell(row=r, column=3).value),
            "base": _clean(ws.cell(row=r, column=4).value),
            "read": _clean(ws.cell(row=r, column=5).value),
            "reason": _clean(ws.cell(row=r, column=6).value),
        }
    return m


def _pct(a, b):
    """两数相对偏差（%），基准为 0 时返回 None。"""
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b) * 100


def self_check(row: dict) -> list[dict]:
    """数值勾稽自校验：抓出 Excel 异常表没列、但数学上对不上的错。"""
    out = []
    man = _num(row.get("価格（税込・万円）"))
    yen = _num(row.get("価格（円）"))
    if man and yen and _pct(yen, man * 10000) and _pct(yen, man * 10000) > 1:
        out.append({"field": "価格（円）", "level": "warn",
                    "reason": f"与万日元口径不符：{man:g}万×10000={man*10000:,.0f}，实际{yen:,.0f}"})
    up = _num(row.get("単価（円/㎡）"))
    area = _num(row.get("専有面積（㎡）"))
    if up and area and yen:
        d = _pct(up * area, yen)
        if d and d > 5:
            out.append({"field": "単価（円/㎡）", "level": "warn",
                        "reason": f"单价×面积={up*area:,.0f}円，与总价{yen:,.0f}円偏差{d:.0f}%"})
    sqm = _num(row.get("専有面積（㎡）"))
    tsubo = _num(row.get("専有面積（坪）"))
    if sqm and tsubo:
        d = _pct(tsubo, sqm * TSUBO_PER_SQM)
        if d and d > 3:
            out.append({"field": "専有面積（坪）", "level": "warn",
                        "reason": f"与{sqm:g}㎡换算值{sqm*TSUBO_PER_SQM:.2f}坪偏差{d:.0f}%"})
    mg = _num(row.get("管理費（円/月）"))
    tk = _num(row.get("積立金（円/月）"))
    ot = _num(row.get("その他（円/月）"))
    tot = _num(row.get("管理費・積立金等合計（円/月）"))
    if tot and mg is not None:
        s = (mg or 0) + (tk or 0) + (ot or 0)
        if tot > 1000 and s > 0:          # 合计明显为个位数的（明显 OCR 错）单独判
            d = _pct(s, tot)
            if d and d > 1:
                out.append({"field": "管理費・積立金等合計（円/月）", "level": "warn",
                            "reason": f"分项合计{s:,.0f}≠合计栏{tot:,.0f}（偏差{d:.0f}%）"})
        elif tot < 100 and s > 100:
            out.append({"field": "管理費・積立金等合計（円/月）", "level": "danger",
                        "reason": f"合计栏仅{tot:g}，与分项{int(s):,}円量级严重不符，疑OCR漏位"})

    # 枚举型字段：值里混了别的字段残片就标存疑（页面红色提示人工核）
    for col, kws in (("現況", ai_score.KNOWN_STATUS), ("管理体制", ai_score.KNOWN_MGMT)):
        raw = _clean(row.get(col))
        if not raw:
            continue
        cleaned = ai_score._enum_clean(raw, kws)
        if not cleaned:
            out.append({"field": col, "level": "warn",
                        "reason": f"「{raw}」不是合法的{col}取值，疑OCR跨字段黏连"})
        elif cleaned != raw:
            out.append({"field": col, "level": "warn",
                        "reason": f"原值含其他字段残片，有效部分为「{cleaned}」，请对照原件确认"})
    return out


# ============================================================
# 主流程
# ============================================================
def main(xlsx: Path = DEFAULT_XLSX):
    if not xlsx.exists():
        print(f"[FAIL] 找不到文件：{xlsx}")
        return 1
    print(f"[1/6] 读取 {xlsx.name}")
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws_data = wb["物件数据"]
    ws_meta = wb["字段说明"]
    ws_ocr = wb["OCR待核对"]
    ws_diff = wb["AI vs DB差异核对"]

    # 表头
    hdr = [_clean(ws_data.cell(row=1, column=c).value)
           for c in range(1, ws_data.max_column + 1)]
    ncol = len(hdr)

    print("[2/6] 建字段字典 + 载入异常清单")
    store = AIStructureStore(DB_PATH)
    meta = load_field_meta(ws_meta)
    # v1.9.34（PRD-25 扩字段）：「共用施設（会所）」不在原 Excel 字段说明 59 列里，
    #   这里保证无论 Excel 是否含该列，独立库字段字典都带它（避免 re-import 被 replace_field_meta 洗掉）。
    _EXTRA_META = [{
        "col_name": "共用施設", "seq": 60, "group_key": "building",
        "group_cn": "建筑与年代", "zh": "共用设施", "zh_tw": "共用設施",
        "en": "Common Facilities", "ja": "共用施設",
        "source": "PDF视觉补充", "note": "会所/集会所/健身房/泳池等",
    }]
    _have = {m["col_name"] for m in meta}
    for m in _EXTRA_META:
        if m["col_name"] not in _have:
            meta.append(m)
    store.replace_field_meta(meta)
    ocr = load_ocr_issues(ws_ocr)
    diff = load_diff_issues(ws_diff)
    print(f"      字段 {len(meta)} 列 ｜ OCR存疑 {len(ocr)} 条 ｜ 差异核对 {len(diff)} 条")

    # 全量统计（性价比打分要用单价中位数做基准）
    rows = []
    for r in range(2, ws_data.max_row + 1):
        d = {hdr[i]: ws_data.cell(row=r, column=i + 1).value for i in range(ncol)}
        no = _clean(d.get("物件番号（ファイル名）"))
        if not no:
            continue
        d["__row__"] = r
        rows.append(d)
    ups = [x for x in (_num(r.get("単価（円/㎡）")) for r in rows) if x]
    ups.sort()
    median = ups[len(ups) // 2] if ups else 0
    ctx = {"unit_price_median": median}
    print(f"[3/6] 物件 {len(rows)} 条 ｜ 单价中位数 {median:,.0f} 円/㎡（性价比基准）")

    print("[4/6] 逐条构建 AI 结构 + 打分")
    n_danger = n_warn = 0
    for d in rows:
        no = _clean(d["物件番号（ファイル名）"])
        r = d["__row__"]
        # --- 异常：OCR 清单 + 差异核对 + 自校验 ---
        bad: dict[str, dict] = {}
        for fld, info in ocr.items():
            if fld[0] == no:
                bad.setdefault(fld[1], {"level": info["level"],
                                        "reason": info["reason"] or info["advice"]})
        dv = diff.get(no)
        if dv:
            f = dv["field"] or "管理費・積立金等合計（円/月）"
            cur = bad.get(f)
            if not cur or cur["level"] != "danger":
                bad[f] = {"level": "warn", "reason": dv["reason"] or dv["read"]}
        for it in self_check(d):
            cur = bad.get(it["field"])
            if not cur or (cur["level"] == "warn" and it["level"] == "danger"):
                bad[it["field"]] = {"level": it["level"], "reason": it["reason"]}

        # --- 按组组织字段（有则显示，无则不显）---
        groups = []
        for gk, gcn, cols in GROUPS:
            fields = []
            for c in cols:
                raw = d.get(c)
                val = _clean(raw)
                if val == "":
                    continue          # 无则不显
                fm = next((m for m in meta if m["col_name"] == c), {})
                b = bad.get(c)
                fields.append({
                    "col": c,
                    "label": {"zh": fm.get("zh") or c, "zh_tw": fm.get("zh_tw") or c,
                              "en": fm.get("en") or c, "ja": fm.get("ja") or c},
                    "value": val,
                    "level": b["level"] if b else "ok",
                    "note": b["reason"] if b else "",
                    "source": fm.get("source", ""),
                    "edited": False,
                })
            if fields:
                groups.append({"key": gk, "label": {"zh": gcn, "zh_tw": gcn,
                                                    "en": gk, "ja": gcn},
                               "fields": fields})

        anomalies = [{"field": k, "level": v["level"], "reason": v["reason"]}
                     for k, v in bad.items()]
        n_danger += sum(1 for a in anomalies if a["level"] == "danger")
        n_warn += sum(1 for a in anomalies if a["level"] == "warn")

        radar = ai_score.build_radar(d, ctx)
        concl = ai_score.build_conclusion(d, radar, len(anomalies), ctx)
        store.upsert(no, structure={"groups": groups}, radar=radar,
                     conclusion=concl, anomalies=anomalies,
                     overall=radar["overall"],
                     source_file=xlsx.name, source_row=r,
                     extracted_at=_clean(d.get("抽出日")))

    print(f"      异常标注：danger {n_danger} 处（红字黄底）｜ warn {n_warn} 处（红字）")
    print(f"[5/6] 独立库就绪：{DB_PATH} 共 {store.count()} 条")

    # ---- 自检：抽 3 条打印，确认不是空壳 ----
    print("[6/6] 抽验")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    for rr in con.execute("SELECT property_no,overall,length(conclusion) lc,"
                          " length(structure_json) ls FROM ai_structure"
                          " ORDER BY overall DESC LIMIT 3"):
        print(f"      {rr['property_no']}  综合{rr['overall']}分  "
              f"结论{rr['lc']}字  结构{rr['ls']}字节")
    con.close()
    print("[OK] 导入完成")
    return 0


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    sys.exit(main(p))
