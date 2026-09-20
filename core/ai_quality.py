# -*- coding: utf-8 -*-
"""抽取结果的**质量闸门**（宁缺勿滥）。

勇哥的原则（2026-09-20）：OCR/AI 取不准的值，**一律不写入**，改列进「待核对」给人裁决。
所以本模块是录库前的最后一道关口 —— 没通过的值不会进 ai_structure。

双源互证（这是质量的关键，不是单看格式漂不漂亮）
------------------------------------------------
  - 専有面積（坪）必须与主库已有的 ㎡ 满足  坪 = ㎡ ÷ 3.3058（±3%）
  - 月度费用要与 DB 的管理費勾稽，且合計不得小于任一分項
  - FAX 不得与 DB 的 TEL 相同（防把电话当传真 —— 这个规则修过一次，
    旧正则写成 `Ｆ?A?X?` 每个字符都可选，退化成匹配空串，误判 58 条）

check() 统一返回三元组 **(ok, reason, normalized)**：
  normalized 为 None 表示「原值即可」，否则用归一化后的值入库。
"""
from __future__ import annotations

import re
from typing import Any

TSUBO = 3.3058                 # 1 坪 = 3.3058 ㎡

STRUCT_OK = ["鉄骨鉄筋コンクリート造", "鉄筋鉄骨コンクリート造", "鉄筋コンクリート造",
             "鉄骨コンクリート造", "軽量鉄骨造", "重量鉄骨造", "鉄骨造", "木造",
             "コンクリート造", "ブロック造", "ＲＣ造", "RC造", "ＳＲＣ造", "SRC造",
             "Ｓ造", "S造", "Ｗ造", "W造"]
STATUS_OK = ["空室", "空家", "空き家", "賃貸中", "居住中", "所有者居住中", "民泊運営中",
             "民泊運営", "未完成", "建築中", "更地", "上屋有", "事業用"]
MGMT_OK = ["全部委託", "一部委託", "自主管理", "日勤", "巡回", "常駐", "常勤", "管理人",
           "無", "なし"]
YESNO_OK = ["有", "無", "なし"]
AD_OK = ["可", "不可"]
DELIV_OK = ["相談", "即時", "即日", "要相談", "未定", "可能"]

TEL_RE = re.compile(r"^0\d{1,4}[-\s]?\d{2,4}[-\s]?\d{3,4}$")
NOISE_IN_VALUE = ["@間取", "@所", "@用", "@総", "@管理", "@建築", "@賃料", "@を",
                  "のため", "による", "が異なる場合", "万円", "http", "全都道府県",
                  "国土交通大臣"]

# 手数料必须像钱/费率，否则就是串了标签（实测抓到过「別れ」「担当者」这种噪声）
_FEE_WORDS = ("円", "万", "%", "％", "無料", "要", "別途", "半額", "無", "有", "込")

# 标签**片段**：单独出现绝不可能是有效值（实测真抓到过 駐車場=月額 / 報酬形態=形態 /
# 担当者連絡先=分かれ —— 都是 OCR 把相邻标签或别字段的值切给了本字段）。
# 注意：不能收「有/無/可/不可/空/済/相談/即時」—— 那些是**合法值**。
_FRAGMENTS = {
    "月額", "年額", "日額", "月 額", "形態", "形 態", "態様", "区分", "区 分",
    "番号", "面積", "面 積", "所在", "交通", "備考", "備 考", "引渡", "現況",
    "間取", "間取り", "価格", "賃料", "構造", "階数", "戸数", "名称", "氏名",
    "担当", "業者", "別れ", "分かれ", "住所", "電話", "連絡先", "会社", "物件",
    "その他", "合計", "内訳", "詳細", "情報", "事項", "条件", "設備", "周辺",
}


def _label_set() -> set[str]:
    """所有已知标签名（取值标签 + 边界邻居标签）。

    用途：抽取器有时会**把下一个标签本身当成值**（「担当者=取引業態」「報酬形態=担当者」）。
    这类值格式上完全合法（长度够、无噪声），只有靠「它是不是标签名」才能识别。
    """
    try:
        from .ocr_local import TARGET, BOUNDARY            # noqa: PLC0415
        names = set(BOUNDARY)
        for ls in TARGET.values():
            names |= set(ls)
        return names
    except Exception:                                      # noqa: BLE001
        return set()


def _f(x) -> float | None:
    """转 float。**必须先剥千分位逗号** —— 这批资料里金额几乎都写成「5,000」，
    直接 float('5,000') 会抛异常 → 闸门把正常值全判成「金额不合理」（已实测复现）。"""
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:                                      # noqa: BLE001
        return None


def _digits_only(s: str) -> str:
    """只留数字，用于电话/传真号码比对（0665351234 == 06-6535-1234）。"""
    return re.sub(r"\D", "", str(s or ""))


def check(col: str, v: Any, ctx: dict | None = None) -> tuple[bool, str, Any]:
    """校验单个 (列名, 值)。ctx 为 DB 侧已知值 {"area_sqm","mgmt_fee","tel"}。"""
    ctx = ctx or {}
    if v in (None, ""):
        return False, "空值", None
    s = str(v).strip()

    # ---------- 通用噪声 ----------
    if any(n in s for n in NOISE_IN_VALUE[:9]):
        return False, "疑似跨标签黏连", None
    if len(s) > 120:
        return False, "值过长", None

    # ---------- 取到「标签本身」（担当者=取引業態 / 報酬形態=担当者）----------
    # 这类值长度、字符都合法，只有与标签字典比对才能识别出来。
    if s in _label_set():
        return False, "取到标签本身（非该字段的值）", None

    # ---------- 取到「标签片段」（駐車場=月額 / 報酬形態=形態 / 担当者=分かれ）----------
    if s.strip(" 　") in _FRAGMENTS:
        return False, "取到标签片段（非该字段的值）", None

    # ---------- 需交叉验证的数值 ----------
    if col == "専有面積（坪）":
        t, a = _f(s), _f(ctx.get("area_sqm"))
        if t is None or a is None:
            return False, "缺㎡无法校验", None
        expect = a / TSUBO
        if t <= 0:
            return False, "非正数", None
        ok = abs(t - expect) / expect <= 0.03
        return ok, (f"坪㎡换算偏差>3%（实得{s} 期望{expect:.2f}）"), None

    if col in ("積立金（円/月）", "その他（円/月）", "管理費・積立金等合計（円/月）"):
        n = _f(s)
        if n is None or n <= 0 or n > 300000:
            return False, "金额不合理", None
        return True, "", int(n)

    if col == "総戸数（戸）":
        n = _f(s)
        if n is None or not (1 <= n <= 5000):
            return False, "户数不合理", None
        return True, "", int(n)

    # ---------- 枚举白名单 ----------
    if col == "構造（原文）":
        return (any(h in s for h in STRUCT_OK)), "未含已知结构词", None
    if col == "構造":
        return (s in STRUCT_OK), "非标准结构名", None
    if col == "現況":
        return (any(k in s for k in STATUS_OK) and len(s) <= 16), "非现况枚举", None
    if col == "管理体制":
        return (any(k in s for k in MGMT_OK) and len(s) <= 16), "非管理体制枚举", None
    if col == "管理組合":
        return (any(k in s for k in YESNO_OK) and len(s) <= 6), "非有/无", None
    if col == "建物引渡":
        return (any(k in s for k in DELIV_OK) and len(s) <= 10), "非交付枚举", None

    # ---------- 联系方式 ----------
    if col == "FAX":
        s2 = s.replace(" ", "")
        m = re.search(r"0\d{1,4}[-\s]?\d{2,4}[-\s]?\d{3,4}", s2)
        if not m:
            return False, "非电话格式", None
        s2 = m.group(0)
        # 去号线后比对：同一号码「06-6535-1234」和「0665351234」必须判为同一个
        tel_d = _digits_only(ctx.get("tel"))
        if tel_d and _digits_only(s2) == tel_d:
            return False, "与DB电话相同（疑为电话而非传真）", None
        return True, "", s2

    if col == "E-MAIL":
        m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", s.replace(" ", ""))
        if not m:
            return False, "非邮箱格式", None
        mail = m.group(0)
        if re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", mail):
            return True, "", mail
        return False, "邮箱本体可疑", None

    if col == "会社住所":
        if any(n in s for n in ("@", "http", "TEL", "FAX")):
            return False, "含联系方式噪声", None
        m = re.search(r"(?:〒?\s*\d{3}-?\d{4}|[^\s]{2,8}(?:都|道|府|県))[^\n]{0,36}", s)
        if m and re.search(r"(市|区|町|村|丁目|番地|号|\d{1,2}-\d{1,2})", s) and len(s) <= 42:
            return True, "", m.group(0).strip()
        return False, "非日本地址格式", None

    # ---------- 文案型 ----------
    if col == "広告":
        if "有効期限" in s or "期限" in s:
            return False, "实为'広告有効期限'标签残片", None
        if "不可" in s:
            return True, "", "不可"
        if re.search(r"^可", s):
            return True, "", "可"
        return False, "非可/不可", None
    if col == "駐車場":
        if len(s) > 32:
            return False, "过长", None
        if any(k in s for k in ("空無", "空有", "無", "なし", "有", "円", "月額", "可能", "不可")):
            return True, "", s
        return False, "非駐車場表述", None
    if col == "バルコニー":
        if any(k in s for k in YESNO_OK) and len(s) <= 6:
            return True, "", s
        m = re.search(r"([\d]{1,3}\.?\d{0,2})\s*m", s)
        if m and len(s) <= 26:
            return True, "", (m.group(1) + "㎡")
        return False, "非面积或有無", None
    if col == "担当者連絡先":
        if "@" in s or "http" in s:
            return False, "含邮箱/网址", None
        if re.search(r"\d{5,}", s):
            return False, "含长数字", None
        return (2 <= len(s) <= 24), "长度异常（担当者姓名应 2–24 字）", None
    if col == "設備・条件":
        return (len(s) >= 4), "过短", None
    if col == "物件コメント":
        return (len(s) >= 25 and any(k in s for k in ("。", "、"))
                and not any(n in s for n in NOISE_IN_VALUE)), "非完整文案", None
    if col == "注意事項":
        return (any(k in s for k in ("優先", "現況", "図面", "差異", "相違"))
                and len(s) >= 6), "非免责声明", None
    if col == "報酬形態":
        return (len(s) <= 14 and "。" not in s), "形态异常", None
    if col == "手数料":
        return (len(s) <= 16 and any(w in s for w in _FEE_WORDS)), "非金额/费率表述", None
    if col == "契約形態":
        return (len(s) <= 20), "过长", None

    return False, "无校验规则", None
