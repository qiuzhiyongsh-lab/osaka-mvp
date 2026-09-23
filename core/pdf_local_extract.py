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
    "構造（原文）": [r"構造[等・･階数]*[：:\s]*\n?\s*((?:鉄筋|鉄骨|木造|ＲＣ|RC|ＳＲＣ|SRC|軽量鉄骨|コンクリート)[^\n]{0,40})",
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
    # 会社住所：〒 与地址常不在同一行，允许跨换行（仍锚定到都道府県/市，避免吞太多）
    "会社住所": [r"(〒\s*\d{3}-?\d{4}[\s\S]{0,40}?(?:大阪府|大阪市|兵庫|神戸|東京都|京都府)[^\n]{0,30})"],
    # FAX 正则踩过坑：写成 Ｆ?A?X? 会退化成匹配空串（每个字符都可选），
    # 把 58 条正常电话误判成 FAX。这里必须是「显式 FAX 标签 + 电话号码」。
    "FAX": [r"(?:ＦＡＸ|FAX|Fax|ｆａｘ)\s*[：:：.\s]*\s*(0\d{1,4}[-\s]?\d{2,4}[-\s]?\d{3,4})"],
    "E-MAIL": [r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"],
    "注意事項": [r"((?:本書と現況[^\n]{0,40}|図面と現状[^\n]{0,40}|現況優先[^\n]{0,20}))"],
    # v1.9.34（PRD-25 扩字段）：勇哥点名要的「具体地址+区」「更详细的地铁站」「会所」
    # v1.9.74（勇哥 2026-09-24 反馈「五处地图按钮都没有」）：实测 300 份样本后放宽锚点——
    #   ① 允许多一个「〒534-0024」前缀（原规则遇邮编直接失配）；
    #   ② 允许标签同行带前缀（「●物件所在地　大阪市…」）。
    #   命中 18.0%→21.5%，精度 73.5%→78.0%（闸门 _valid_addr 见下）。
    "所在地": [r"所在[地等]?[：:\s]*\n?\s*(?:〒\s*\d{3}-?\d{4}\s*)?"
               r"((?:大阪府|大阪市|兵庫県|兵庫|神戸市|東京都|京都府)[^\n]{4,40})",
               r"所在[地等]?[^\n]{0,10}?((?:大阪府|大阪市)[^\n]{4,40})"],
    # v1.9.72（勇哥 2026-09-24）：「物业名称」=物件名/建物名，**必须从 PDF 抓取**。
    # v1.9.74 实测修正：原来的「物件名:/建物名:」两式只命中 9.5%，且**精度仅 50%**——
    #   这批概要書常见「标签与值被版式拉平、不相邻」，于是正则吃到下一行的**别的标签**
    #   （販売価格 / 物件種別 / 所在階）。改为：① 标签变体（含「建物名称」这种带「称」的）
    #   ② 兜底「楼名 + 下一行 xxx号室」；两道都过 _valid_name 闸门。
    #   → 命中 13~14.5%，精度 50%→84.6%。
    "物件名": [r"(?:物件名称|物件名|建物名称|建物名|マンション名)[：:\s]*\n?\s*([^\n（）()]{2,40})",
               r"([^\n]{3,40})\n\s*\d{2,4}\s*号室"],
    # 最寄駅1：① 交通 必须在行首（避开「国土交通大臣」误命中）② 兜底抓「铁路公司+駅+徒歩/バス」
    "最寄駅1": [r"(?:^|\n)[^\S\n]*交通[：:\s]*([^\n]{4,50})",
               r"([^\n]{0,24}?(?:ＪＲ|ＪＲ東|地下鉄|大阪メトロ|阪急|阪神|京阪|近鉄|南海|泉北|西日本|線)[^\n]{0,18}?駅[^\n]{0,10}?(?:徒歩|バス)[^\n]{0,6})"],
    "共用施設": [r"(?:共用施設|共有施設)[：:\s]*([^\n]{1,30})"],
    # v1.9.36（勇哥 Release 清单：加列）— PDF 有 / 投资有用 / 原 60 列缺的字段
    "ペット（飼育可）": [r"ペット[（(]?[飼育可不可]*[）)]?[：:\s]*([^\n]{1,20})",
                     r"(?:愛玩動物|ペットの?[飼育])[：:\s]*([^\n]{1,20})"],
    "権利形態": [r"(?:権利形態|権利)[：:\s]*([^\n]{1,16})"],
    "前面道路": [r"(?:前面道路|接道|道路[（(][^）)]*[）)])[：:\s]*([^\n]{1,30})"],
    "セキュリティ": [r"(?:セキュリティ|防犯設備?|防犯)[：:\s]*([^\n]*(?:オートロック|有|無|なし|あり|防犯)[^\n]{0,16})"],
    "駐輪場・バイク置場": [r"(?:駐輪場|バイク置場)[：:\s]*([^\n]*(?:有|無|なし|空|月額|円|設置|可能|不要)[^\n]{0,20})"],
    "エレベーター": [r"エレベーター[：:\s]*([^\n]{1,10})"],
    "建蔽率": [r"建[ぺペ]?い率[：:\s]*([\d]{1,3}\s*%)"],
    "容積率": [r"容積率[：:\s]*([\d]{1,4}\s*%)"],
    "地目": [r"地目[：:\s]*([^\n]{1,10})"],
    "バルコニー方向": [r"(?:バルコニー方向|バルコニー方角|方位|バルコニー[（(][^）)]*[）)])[：:\s]*([^\n]{1,8})"],
    "リフォーム履歴": [r"(?:リフォーム|改装|内装リフォーム)[：:\s]*([^\n]{1,24})"],
}


# ---------------------------------------------------------------- 文本读取
#: v1.9.74 血训：这批 REINS 概要書的文字层里混着 **康熙部首**（U+2F00-U+2FDF）
#: 和 CJK 部首补充（U+2E80-U+2EFF）字符 —— 看上去和汉字一样，码位却不是汉字。
#: 实测 `300140844388.pdf` 里写的是「⼤阪市淀川区東三国6丁⽬22-13」（⼤=U+2F24、⽬=U+2F6C），
#: 于是所有以正常汉字书写的规则（大阪市 / 用途地域 / 専有面積 / 管理費 …）**全部失配**。
#: 归一化只动这两个部首区（NFKC 还原成对应汉字），其余字符（㎡、全角括号…）一律不碰
#: → 对既有规则零副作用（实测 250 份：字段总产出 +0.6%，但个别房源由「全空」变「可抽」）。
_RAD_LO, _RAD_HI = 0x2E80, 0x2FDF


def normalize_radicals(text: str) -> str:
    """把文字层里的「部首形汉字」还原成正常汉字（逐字符、最小侵入）。"""
    if not text:
        return text
    import unicodedata                       # noqa: PLC0415
    out: list[str] = []
    for ch in text:
        if _RAD_LO <= ord(ch) <= _RAD_HI:
            nf = unicodedata.normalize("NFKC", ch)
            out.append(nf if nf != ch else ch)
        else:
            out.append(ch)
    return "".join(out)


def text_of(pdf_path: str | Path) -> str:
    """读 PDF 文字层全文并做轻清洗（横向空白折叠 + 部首归一）。扫描件返回 ''（交给 L2 OCR）。"""
    try:
        import pymupdf                       # noqa: PLC0415
    except ImportError as e:                 # pragma: no cover
        raise RuntimeError("缺依赖 pymupdf：请先执行 pip install pymupdf") from e
    doc = pymupdf.open(str(pdf_path))
    try:
        parts = [page.get_text("text") or "" for page in doc]
    finally:
        doc.close()
    return normalize_radicals(re.sub(r"[ \t]+", " ", "\n".join(parts)).strip())


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


# ---------------------------------------------------------------- 取值闸门（v1.9.74）
#: REINS 概要書的字段名清单（用来挡掉「正则吃到了下一行的标签」这类错值）。
_LABELS: frozenset[str] = frozenset("""
物件番号 物件種目 物件種別 物件種別補足 物件名 物件名称 建物名 建物名称 所在地 物件所在地 区 公開状況
価格 販売価格 単価 最寄駅 間取り 専有面積 土地面積 建物面積 バルコニー 所在階 地上階数 築年月 築年
構造 総戸数 用途地域 管理費 積立金 その他 管理組合 管理体制 現況 現況詳細 契約形態 建物引渡 駐車場
設備 設備・条件 取引形態 報酬形態 手数料 広告 担当者 担当者連絡先 会社住所 登録日 変更日 初回確認日
最終確認日 写真有無 間取図有無 地図有無 物件コメント 注意事項 共用施設 権利形態 前面道路 セキュリティ
エレベーター 建蔽率 容積率 地目 名称 交通 沿線 賃料 敷金 礼金 管理費等 修繕積立金 引渡 権利 土地権利
価 格 販売 万円 円 専有面積（㎡） バルコニー方向 リフォーム履歴
""".split())

_RE_ADDR_NUM = r"[\d０-９一二三四五六七八九十]"
_RE_ADDR_UNIT = r"丁目|番|号|[\d０-９][\-－]|[\d０-９]－"
_RE_ADDR_SENTENCE = r"です|ます|でした|の物件|掲載|おすすめ|位置する|利便性|備え|周辺|アクセス|から徒歩|駅より"
_RE_NAME_SENTENCE = r"です|ます|掲載|おります|となります|ください|について|に関して|記載|案内日時"


def _norm_gate(v: str) -> str:
    import unicodedata                       # noqa: PLC0415
    return unicodedata.normalize("NFKC", v or "").replace(" ", "").replace("　", "")


def valid_addr(v: str) -> bool:
    """地址闸门：必须含「区」、含门牌数字/丁目/番/号，且不是散文句/金额。"""
    s = _norm_gate(v).strip("：:・●○■□")
    if "区" not in s or not re.search(_RE_ADDR_NUM, s):
        return False
    if not re.search(_RE_ADDR_UNIT, s):
        return False
    if re.search(_RE_ADDR_SENTENCE, s) or "㎡" in s or "万円" in s:
        return False
    return 5 <= len(s) <= 40


def valid_name(v: str) -> bool:
    """物业名称闸门：挡掉「别的字段名」「散文句」「纯数字」「带号室」等明显错值。"""
    s = _norm_gate(v).strip("：:・●○■□")
    if not s or s in _LABELS or s in ("-", "－", "なし"):
        return False
    if not (3 <= len(s) <= 40):
        return False
    if re.search(_RE_NAME_SENTENCE, s) or re.search(r"万円|円/|㎡|%|徒歩", s):
        return False
    if re.fullmatch(r"[\d\-\s/.,]+", s):
        return False
    if re.search(r"号室|\d{3,}号$", s):
        return False
    if "区" in s and re.search(r"丁目|番地|号室", s):      # 那是地址不是楼名
        return False
    return True


#: 逐列取值闸门：命中后还要过闸门才算数（没有登记的列 = 保持原「首个匹配即取」行为）
VALIDATORS: dict[str, Any] = {"所在地": valid_addr, "物件名": valid_name}


# ---------------------------------------------------------------- 主入口
def extract(text: str) -> dict[str, str]:
    """规则提取：返回 {列名: 原始值}（**未校验**，校验交给 core.ai_quality）。

    v1.9.74：新增逐列「取值闸门」（VALIDATORS）——同一正则的多次命中里，
    取第一个过闸门的；全不过则视为没抽到（宁缺勿错，配合页面「空值不显示」）。
    """
    out: dict[str, str] = {}
    if not text or len(text) < 40:          # 太薄 = 扫描件，走 OCR 更有效
        return out
    text = normalize_radicals(text)         # 双保险：调用方直接传原始文字层也能吃到归一
    for col, pats in RULES.items():
        gate = VALIDATORS.get(col)
        got = ""
        for p in pats:
            for m in re.finditer(p, text):
                v = m.group(1).strip(" 　:：・")
                v = re.sub(r"\s+", " ", v)
                if not v or len(v) > 120:
                    continue
                if gate is not None and not gate(v):
                    continue
                got = v
                break
            if got:
                break
        if got:
            out[col] = got
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
