# -*- coding: utf-8 -*-
"""L1 · PDF **文字层** → 规则提取（本地、零成本、不联网）。

这层是整个 PRD 25 的省钱主力：实测这批 REINS 单页里约 **65% 是带文字层的 PDF**，
直接读内部文字对象 + 正则就能取值，**一个 token 都不用烧**。

规则来源（不是拍脑袋写的，是 2026-09-20 凌晨在 190 份真实 PDF 上跑出来验证过的）：
  - 正则表 RULES        ← tmp/pdf_extract/fill_ai_cols.py v3（命中率实测 117/117 份）
  - 模糊认定 FUZZY      ← tmp/pdf_extract/apply_rules_v2.py（勇哥拍板的"模糊认定"口径）

勇哥的取值口径（2026-09-20 拍板，代码里必须体现）：
  1. AI 读取归 AI 读取，**不影响** DB（properties）原有数据算法 —— 本模块只读 PDF、只输出 dict。
  2. 模糊认定来的值必须标 `warn`（页面红字）；**将来应用取值时默认不取**。
  3. 将来手工改过的值以手工为准 —— 落库由 ai_structure_store.upsert() 保护，本模块不参与覆盖决策。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SQM_PER_TSUBO = 3.30578

# ---------------------------------------------------------------- 取值正则表
# 说明：值都在「标签之后」；OCR/排版会把冒号吃掉，所以统一写成 [：:\s]* 宽容匹配。
RULES: dict[str, list[str]] = {
    "物件コメント": [r"(?:物件コメント|コメント|セールスポイント|物件説明)[：:\s]*(.{10,120}?)(?=【|■|●|\n|$)"],
    "構造（原文）": [r"構造[・･]?[階数]*[：:\s]*((?:鉄筋|鉄骨|木造|ＲＣ|RC|ＳＲＣ|SRC|軽量鉄骨|コンクリート)[^\n]{0,40})",
                 r"建物構造[：:\s]*([^\n]{2,40})"],
    "総戸数（戸）": [r"(?:総戸数|一棟の総戸数)[：:\s]*([\d,]+)\s*戸"],
    "専有面積（坪）": [r"専有面積[^\n]{0,20}?([\d.,]+)\s*坪"],
    "バルコニー": [r"バルコニー[面積]*[：:\s]*([\d.,]+\s*㎡|有|無|なし)"],
    "管理組合": [r"管理組合[：:\s]*(有|無|なし|未設立|設立済)"],
    "管理体制": [r"(?:管理体制|管理形態|管理員)[：:\s]*([^\n]{1,14})"],
    "積立金（円/月）": [r"(?:修繕積立金|積立金)[：:\s]*[^\d]{0,6}([\d,]{3,7})\s*円"],
    "その他（円/月）": [r"その他[費用]*[：:\s]*[^\d]{0,6}([\d,]{3,7})\s*円"],
    "管理費・積立金等合計（円/月）": [r"(?:費用合計|合計)[：:\s]*[^\d]{0,6}([\d,]{3,7})\s*円"],
    "駐車場": [r"駐車場[：:\s]*([^\n]{1,30})"],
    "現況": [r"現況[：:\s]*([^\n]{1,20})"],
    "現況詳細": [r"現況[：:\s]*(?:賃貸中|居住中|空室|空家)[\(（]([^\)\n]{1,24})[\)）]"],
    "契約形態": [r"契約形態[：:\s]*([^\n]{1,24})"],
    "設備・条件": [r"設備[・･]?[条件]*[：:\s]*([^\n]{2,60})"],
    "建物引渡": [r"(?:引渡可能時期|引渡時期|建物引渡)[：:\s]*([^\n]{1,20})"],
    "報酬形態": [r"報酬[：:\s]*([^\n]{1,16})"],
    "手数料": [r"(?:仲介手数料|手数料)[：:\s]*([^\n]{1,16})"],
    "広告": [r"広告[：:\s]*(可|不可)"],
    "担当者連絡先": [r"担当者?[：:\s]*([^\n]{2,30})"],
    "会社住所": [r"(〒\s*\d{3}-?\d{4}[^\n]{2,40})"],
    # FAX 正则踩过坑：写成 Ｆ?A?X? 会退化成匹配空串（每个字符都可选），
    # 把 58 条正常电话误判成 FAX。这里必须是「显式 FAX 标签 + 电话号码」。
    "FAX": [r"(?:ＦＡＸ|FAX|Fax|ｆａｘ)\s*[：:：.\s]*\s*(0\d{1,4}[-\s]?\d{2,4}[-\s]?\d{3,4})"],
    "E-MAIL": [r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"],
    "注意事項": [r"((?:本書と現況[^\n]{0,40}|図面と現状[^\n]{0,40}|現況優先[^\n]{0,20}))"],
}


# ---------------------------------------------------------------- 文本读取
def text_of(pdf_path: str | Path) -> str:
    """读 PDF 文字层全文并做轻清洗（横向空白折叠）。扫描件返回 ''（交给 L2 OCR）。"""
    try:
        import pymupdf                       # noqa: PLC0415
    except ImportError as e:                 # pragma: no cover
        raise RuntimeError("缺依赖 pymupdf：请先执行 pip install pymupdf") from e
    doc = pymupdf.open(str(pdf_path))
    try:
        parts = [page.get_text("text") or "" for page in doc]
    finally:
        doc.close()
    return re.sub(r"[ \t]+", " ", "\n".join(parts)).strip()


# ---------------------------------------------------------------- 模糊认定（勇哥拍板口径）
def _fix_area(raw: str) -> tuple[float, float] | None:
    """'82.06mi' → (82.06㎡, 24.82坪)；'7585mi' → (75.85, 22.94)（OCR 丢了小数点）。"""
    m = re.search(r"(\d{2,4})(?:[.,](\d{1,2}))?\s*(?:mi|mf|mｌ|m1|m2|㎡|m²|n2|ＭＦ|ＭＩ)",
                  raw, re.I)
    if not m:
        return None
    intpart, dec = m.group(1), m.group(2)
    if dec:
        sqm = float(f"{intpart}.{dec}")
    elif len(intpart) >= 3:                      # 缺小数点：末两位是小数
        sqm = float(intpart[:-2] + "." + intpart[-2:])
    else:
        return None
    if not (15 <= sqm <= 200):                   # 合理性闸门：日本住宅専有面積区间
        return None
    return round(sqm, 2), round(sqm / SQM_PER_TSUBO, 2)


def _fix_genkyo(raw: str) -> str | None:
    for kw, std in (("居住中", "居住中"), ("選住中", "居住中"), ("選住", "居住中"),
                    ("空家", "空家"), ("空き家", "空家"), ("賃貸中", "賃貸中")):
        if kw in raw:
            return std
    return None


def _fix_hikiwatashi(raw: str) -> str | None:
    if raw.strip().startswith("相談"):
        return "相談"
    if "即時" in raw or "即可" in raw:            # OCR 把 即時 认成 即可
        return "即時"
    return None


def _fix_koukoku(raw: str) -> str | None:
    if "要承諾" in raw:
        return "不可（要承諾）"
    if "自社HPのみ" in raw:
        return "不可（自社HPのみ）"
    return None


def _fix_juusho(raw: str) -> str | None:
    m = re.search(r"(大阪市[^\s|]{4,32})", raw)
    if not m:
        return None
    a = m.group(1)
    a = re.sub(r"(?<=\d)[二二](?=\d)", "-", a)   # OCR 把 - 认成「二/一」
    a = re.sub(r"(?<=\d)一(?=\d)", "-", a)
    a = re.sub(r"[画|」』 ]+$", "", a)
    if re.search(r"\d$", a) and len(a) >= 8:
        return a
    return None


def _fix_email(raw: str) -> str | None:
    m = re.search(r"[\w.+-]+\s*@\s*[\w.\sJ]{4,40}", raw)
    if not m:
        return None
    e = re.sub(r"\s+", "", m.group(0))
    e = (e.replace("coJjp", "co.jp").replace("coJp", "co.jp")
           .replace(".Jp", ".jp").replace(".jP", ".jp"))
    e = re.sub(r"[^A-Za-z0-9@.+-]", "", e)
    if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", e):
        return e
    return None


#: 模糊认定器：质量闸门驳回后，用「人的判断」再捞一次（结果一律标 warn 红字）
FUZZY: dict[str, Any] = {
    "専有面積（㎡）": lambda r: (_fix_area(r) or (None, None))[0],
    "専有面積（坪）": lambda r: (_fix_area(r) or (None, None))[1],
    "現況": _fix_genkyo,
    "建物引渡": _fix_hikiwatashi,
    "広告": _fix_koukoku,
    "会社住所": _fix_juusho,
    "E-MAIL": _fix_email,
}

#: 特殊 busy：OCR 出来是这个值时「像又不像」，人工也难定，单独标出给勇哥裁决
SPECIAL_VALUES: dict[str, set[str]] = {
    "管理体制": {"P 有", "管理臣"},
    "駐車場": {"駐輪場、バイク置き場"},
}


# ---------------------------------------------------------------- 主入口
def extract(text: str) -> dict[str, str]:
    """规则提取：返回 {列名: 原始值}（**未校验**，校验交给 core.ai_quality）。"""
    out: dict[str, str] = {}
    if not text or len(text) < 40:          # 太薄 = 扫描件，走 OCR 更有效
        return out
    for col, pats in RULES.items():
        for p in pats:
            m = re.search(p, text)
            if m:
                v = m.group(1).strip(" 　:：・")
                v = re.sub(r"\s+", " ", v)
                if v and len(v) <= 120:
                    out[col] = v
                    break
    return out


def is_special(col: str, raw: str) -> bool:
    """命中「特殊 busy」清单 → 页面单独标出供勇哥定夺（不自动录入）。"""
    return str(raw).strip() in SPECIAL_VALUES.get(col, set())


if __name__ == "__main__":                                   # 单份自测
    import sys
    if len(sys.argv) > 1:
        t = text_of(sys.argv[1])
        print(f"[L1] 文字层 {len(t)} 字符")
        for k, v in extract(t).items():
            print(f"  {k} = {v!r}")
