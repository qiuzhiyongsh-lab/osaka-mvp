# -*- coding: utf-8 -*-
"""AI 结论引擎（v1.9.25）—— 雷达图打分 + 约 200 字结论。

设计取向（PM + UI 视角）
------------------------
勇哥的客户是**中文投资者**，看大阪二手房最关心的是：
「这个价格划不划算、好不好租、好不好脱手」。所以六个维度这样定调：

  性价比 Value    25%  ← 权重最高：同区横向比单价，投资者第一问
  地段交通 Location 20%  ← 徒步分钟数是大阪二手房的硬通货
  收益性 Yield    15%  ← 表面回报率，有真租金用真值、没租金给估算
  建物品質 Quality 15%  ← 築年 + 構造 + 総戸数
  居住快適 Comfort  15%  ← 面积 / 户型 / 阳台 / 设备
  流动性 Liquidity 10%  ← 价格带是否好脱手 + 委托形态 + 公开状态

每条分数都带 **reason（打分理由）**，因为：
  - 分数不解释 = 不可信，用户会怀疑"凭什么打 80"；
  - 将来字段可编辑，改完要能重算，理由让人看得懂变化从哪来。

打分口径全部规则化（不随机、不调用模型），保证：
  ① 可复现  ② 可解释  ③ 批量重算零成本
"""
from __future__ import annotations

import re
from typing import Any

# ---------------- 维度定义（顺序 = 雷达图顺时针顺序）----------------
DIMS = [
    ("value",     "性价比",   "性價比",  "Value",       "コスパ",   25),
    ("location",  "地段交通", "地段交通", "Location",    "立地交通",  20),
    ("yield",     "收益性",   "收益性",   "Yield",       "収益性",   15),
    ("quality",   "建物品質", "建物品質", "Build Qual.", "建物品質",  15),
    ("comfort",   "居住舒适", "居住舒適", "Comfort",     "快適性",   15),
    ("liquidity", "流动性",   "流動性",   "Liquidity",   "流動性",   10),
]
DIM_LABEL = {k: {"zh": zh, "zh_tw": tw, "en": en, "ja": ja}
             for k, zh, tw, en, ja, _ in DIMS}
DIM_WEIGHT = {k: w for k, _, _, _, _, w in DIMS}

# 大阪市中心区（立地加分）
CORE_WARDS = {"中央区": 5, "北区": 5, "西区": 4, "福島区": 3,
              "天王寺区": 3, "浪速区": 3, "都島区": 2, "城東区": 1}

# 構造评分（抗震/品质：SRC/RC 最好，铁骨次之，木造最低）
STRUCT_BONUS = [
    ("鉄骨鉄筋コンクリート", 8), ("鉄筋コンクリート", 6),
    ("鉄骨", 0), ("木造", -6), ("軽量鉄骨", -4), ("その他", -2),
]


# ============================================================
# 解析小工具：把 OCR 出来的日文原文读成可计算的数字
# ============================================================
def _num(v: Any) -> float | None:
    """把 Excel 单元格的值转成 float；'1,700' / '9,800' / '16.65' 都能认。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def walk_minutes(text: Any) -> int | None:
    """『大阪メトロ四つ橋線　花園町 徒歩　1分』→ 1。取最小值（多条线路时按最快那条）。"""
    if not text:
        return None
    ms = [int(x) for x in re.findall(r"徒歩\s*(\d+)\s*分", str(text))]
    return min(ms) if ms else None


def _station_name(text: Any) -> str:
    if not text:
        return ""
    m = re.search(r"[\s　]([^\s　]+?)\s*徒歩", str(text))
    return m.group(1).strip() if m else ""


def _rent_yen(row: dict) -> float | None:
    """从現況詳細里抓月租金：『月額賃貸43,000円』→ 43000。"""
    s = str(row.get("現況詳細") or "")
    m = re.search(r"([\d,，]+)\s*円", s)
    if m:
        return _num(m.group(1))
    return None


def _is_rented(row: dict) -> bool:
    s = str(row.get("現況") or "")
    return "賃貸中" in s or "賃貸" in s


def _struct_key(text: str) -> str:
    """从任意长度的文本里捞出**已知结构关键词**（长按先匹配，避免 SRC 被认成 RC）。"""
    for kw, _b in STRUCT_BONUS:
        if kw == "その他":
            continue
        if kw in text:
            return kw
    return ""


def _struct_label(row: dict) -> str:
    """構造 → 展示用结构名（如『鉄筋コンクリート造』）。

    ⚠ 为什么不能直接拼字段值：
      - 原值常已带'造'（鉄骨造），直接拼会出'鉄骨造造'；
      - 拆分字段为空时会回退到『構造（原文）』，而原文往往是一整段 OCR 杂片
        （实测有『木造合金メッキ鋼板ぶき3階建■築年月：…』这种），直接拼进结论
        会让文案变成乱码墙。
      所以：短且干净 → 直接用；否则只捞已知结构关键词，捞不到就宁可不写。
    """
    s = str(row.get("構造") or "").strip()
    raw = str(row.get("構造（原文）") or "").strip()
    for cand in (s, raw):
        if not cand:
            continue
        if len(cand) <= 12:                       # 短 = 干净字段
            return cand if cand.endswith("造") else cand + "造"
    kw = _struct_key(raw) or _struct_key(s)       # 长 = 杂片，只捞关键词
    return (kw + "造") if kw and not kw.endswith("造") else kw


# 枚举型字段的合法取值（用于把 OCR 黏连杂片挡在结论之外）
KNOWN_STATUS = ("空室", "賃貸中", "居住中", "民泊", "空家", "事業用", "更地", "未完成")
KNOWN_MGMT = ("日勤", "巡回", "常駐", "常驻", "全部委託", "一部委託", "委託", "自主管理")
# 明显的跨字段黏连标记（原文里出现这些，说明这一格被 OCR 串了别的字段）
_JUNK_MARK = ("■", "※", "図面", "引渡", "築年", "間取", "施工", "価格", "備考")


def _enum_clean(text: Any, keywords: tuple[str, ...]) -> str:
    """枚举字段清洗：认得出就返回可展示的值，认不出返回 ''（宁缺勿脏）。

    实测被黏连污染的真值：現況『/ 居住中 ■引渡/ 相談 ※図面と現況』、『築年月』；
    管理体制『■ 施工会社』、『間取明細』。这些直接拼进结论会变成乱码墙。
    """
    s = str(text or "").strip()
    if not s:
        return ""
    if len(s) <= 12 and not any(j in s for j in _JUNK_MARK):
        return s                      # 干净值，原样用
    for k in keywords:                # 含杂片 → 只捞合法枚举词
        if k in s:
            return k
    return ""


# 大阪中古住宅单价的**合理值域**（円/㎡）。超出即视为 OCR 脏数据，不参与打分。
UNIT_PRICE_MIN = 80_000
UNIT_PRICE_MAX = 2_500_000


def unit_price(row: dict, ctx: dict | None = None) -> tuple[float | None, str]:
    """取可信的单价（円/㎡），顺带说明来源。

    为什么要有这道闸：OCR 会把单价识别错（实测有一条被读成 1.2 万/㎡，
    真实应为 61.5 万/㎡），若直接拿去打分，"性价比"会被打到 95 分、把综合分
    虚高到 89 —— **脏数据污染结论**。所以三级取数：

      ① 表内单价 且 与「总价÷面积」勾稽得上（偏差≤5%）且在合理值域 → 直接用
      ② 否则用「总价÷面积」重算                          → 记为"重算"
      ③ 都没有 → None，性价比按中性 60 分给，绝不瞎打
    """
    ctx = ctx or {}
    raw = _num(row.get("単価（円/㎡）"))
    price = _num(row.get("価格（円）")) or (_num(row.get("価格（税込・万円）") or 0) * 10000)
    area = _num(row.get("専有面積（㎡）"))
    calc = (price / area) if (price and area) else None

    def _ok(v):
        return v is not None and UNIT_PRICE_MIN <= v <= UNIT_PRICE_MAX

    if _ok(raw) and calc:
        dev = abs(raw - calc) / calc * 100
        if dev <= 5:
            return raw, ""
        return calc, f"表内单价{raw:,.0f}与总价/面积推算值{calc:,.0f}偏差{dev:.0f}%，已按总价÷面积重算"
    if _ok(raw):
        return raw, ""
    if _ok(calc):
        return calc, (f"表内单价异常已弃用，按总价÷面积重算" if raw else "按总价÷面积推算")
    return None, "单价数据不可信，性价比按中性值处理"


# ============================================================
# 六个维度的打分
# ============================================================
def _score_location(row: dict) -> tuple[int, str]:
    w = walk_minutes(row.get("最寄駅1"))
    ward = str(row.get("区") or "").strip()
    st = _station_name(row.get("最寄駅1"))
    if w is None:
        base, why = 60, "未识别到徒步分钟数"
    elif w <= 3:
        base, why = 95, f"{st}徒步{w}分，几乎站内可达"
    elif w <= 5:
        base, why = 88, f"{st}徒步{w}分，通勤舒适"
    elif w <= 8:
        base, why = 80, f"{st}徒步{w}分，属便利圈"
    elif w <= 12:
        base, why = 70, f"{st}徒步{w}分，尚可接受"
    elif w <= 15:
        base, why = 60, f"{st}徒步{w}分，略偏远"
    else:
        base, why = 45, f"{st}徒步{w}分，对出租与自住都偏弱"
    b = CORE_WARDS.get(ward, 0)
    if b:
        why += f"；{ward}属市中心区（+{b}）"
    return max(0, min(100, base + b)), why


def _score_value(row: dict, ctx: dict) -> tuple[int, str]:
    """性价比：单价在全量样本里的百分位。越便宜分越高。

    ⚠ 单价必须过 unit_price() 的值域闸，否则脏数据会把分打飞。
    """
    up, note = unit_price(row, ctx)
    med = ctx.get("unit_price_median")
    if up is None or not med:
        return 60, note or "单价数据不可信，按中性给分"
    ratio = up / med
    if ratio <= 0.70:
        s, w = 95, f"单价{up/10000:.1f}万/㎡，仅为样本中位数{med/10000:.1f}万的{int(ratio*100)}%，明显偏低"
    elif ratio <= 0.85:
        s, w = 86, f"单价{up/10000:.1f}万/㎡，低于中位数约{int((1-ratio)*100)}%，有价格优势"
    elif ratio <= 1.00:
        s, w = 75, f"单价{up/10000:.1f}万/㎡，略低于样本中位数，属合理区间"
    elif ratio <= 1.15:
        s, w = 64, f"单价{up/10000:.1f}万/㎡，略高于中位数{int((ratio-1)*100)}%，溢价尚可控"
    elif ratio <= 1.35:
        s, w = 52, f"单价{up/10000:.1f}万/㎡，高于中位数{int((ratio-1)*100)}%，需靠其他卖点支撑"
    else:
        s, w = 40, f"单价{up/10000:.1f}万/㎡，为样本中位数的{ratio:.1f}倍，价格明显偏贵"
    if note:
        w += f"（{note}）"
    return s, w


def _score_quality(row: dict) -> tuple[int, str]:
    y = _num(row.get("築年（西暦）"))
    parts = []
    if y is None:
        s = 60
        parts.append("筑年未识别")
    else:
        y = int(y)
        age = 2026 - y
        if age <= 6:
            s = 95
        elif age <= 11:
            s = 88
        elif age <= 16:
            s = 80
        elif age <= 21:
            s = 72
        elif age <= 26:
            s = 65
        elif age <= 36:
            s = 55
        elif age <= 41:
            s = 45
        else:
            s = 35
        parts.append(f"{y}年筑（房龄约{age}年）")
    st = str(row.get("構造") or row.get("構造（原文）") or "")
    for kw, b in STRUCT_BONUS:
        if kw in st:
            s += b
            if b:
                parts.append(f"{kw}造（{'+' if b>0 else ''}{b}）")
            break
    units = _num(row.get("総戸数（戸）"))
    if units and units >= 80:
        s += 3
        parts.append(f"总{int(units)}户规模社区（+3）")
    elif units and units <= 20:
        s -= 2
        parts.append(f"仅{int(units)}户小型物业（-2）")
    return max(0, min(100, s)), "；".join(parts)


def _score_yield(row: dict) -> tuple[int, str]:
    price = _num(row.get("価格（円）")) or (_num(row.get("価格（税込・万円）") or 0) * 10000)
    rent = _rent_yen(row)
    area = _num(row.get("専有面積（㎡）"))
    if price and rent:
        gy = rent * 12 / price * 100
        src = f"按現況月租{int(rent):,}円实算，表面回报{gy:.2f}%"
    else:
        # 没租金时用面积口径估算（小户型回报率天然更高，这是大阪市场的经验规律）
        w = walk_minutes(row.get("最寄駅1")) or 10
        if area is None:
            gy = 5.0
        elif area < 25:
            gy = 6.5
        elif area < 35:
            gy = 6.0
        elif area < 50:
            gy = 5.3
        elif area < 70:
            gy = 4.8
        else:
            gy = 4.2
        gy += 0.4 if w <= 5 else (-0.3 if w >= 13 else 0)
        src = f"未披露租金，按面积{area or '未知'}㎡与徒步{w}分估算约{gy:.2f}%"
    if gy >= 7:
        s = 95
    elif gy >= 6:
        s = 88
    elif gy >= 5:
        s = 78
    elif gy >= 4.5:
        s = 70
    elif gy >= 4:
        s = 62
    elif gy >= 3.5:
        s = 52
    else:
        s = 40
    if _is_rented(row):
        s += 3
        src += "；现况租赁中，带租约可立即产生现金流（+3）"
    return max(0, min(100, s)), src


def _score_comfort(row: dict) -> tuple[int, str]:
    a = _num(row.get("専有面積（㎡）"))
    if a is None:
        s, why = 60, "面积未识别"
    else:
        if a >= 70:
            s, why = 95, f"{a}㎡大空间，家庭自住也够用"
        elif a >= 60:
            s, why = 88, f"{a}㎡，2LDK以上舒展"
        elif a >= 50:
            s, why = 80, f"{a}㎡，格局方正度好"
        elif a >= 40:
            s, why = 72, f"{a}㎡，1LDK～2LDK主力区间"
        elif a >= 30:
            s, why = 62, f"{a}㎡，偏单身/投资向"
        elif a >= 25:
            s, why = 52, f"{a}㎡紧凑，以投资出租为主"
        else:
            s, why = 42, f"{a}㎡极小户型，自住舒适度有限"
    madori = str(row.get("間取り") or "")
    if "ＬＤＫ" in madori or "LDK" in madori:
        s += 4
        why += f"；{madori}带LDK起居空间（+4）"
    balcony = str(row.get("バルコニー") or "")
    if "有" in balcony or "㎡" in balcony:
        s += 3
        why += "；带阳台（+3）"
    floor = _num(row.get("所在階"))
    if floor and floor >= 8:
        s += 3
        why += f"；{int(floor)}层高层，采光视野好（+3）"
    zoning = str(row.get("用途地域") or "")
    if "住" in zoning:
        s += 2
        why += "；住宅用途地域，居住氛围安静（+2）"
    equip = str(row.get("設備・条件") or "")
    if "オートロック" in equip:
        s += 2
        why += "；带自动门禁（+2）"
    return max(0, min(100, s)), why


def _score_liquidity(row: dict) -> tuple[int, str]:
    """流动性：好不好脱手。大阪二手房最好卖的是 1000～5000 万日元这个带。"""
    man = _num(row.get("価格（税込・万円）"))
    if man is None:
        s, why = 60, "价格未识别"
    elif man < 800:
        s, why = 72, f"{man:.0f}万日元低价带，首付门槛低、成交快"
    elif man <= 3000:
        s, why = 90, f"{man:.0f}万日元落在最活跃的成交价格带，最易脱手"
    elif man <= 5000:
        s, why = 80, f"{man:.0f}万日元，需求仍在但买家群收窄"
    elif man <= 8000:
        s, why = 65, f"{man:.0f}万日元偏高，成交周期会拉长"
    else:
        s, why = 48, f"{man:.0f}万日元属高价物件，买家相对有限"
    tf = str(row.get("取引形態") or "")
    if "専属専任" in tf:
        s += 6
        why += "；専属専任委托，中介推广力度大（+6）"
    elif "専任" in tf:
        s += 4
        why += "；専任委托（+4）"
    elif "一般" in tf:
        s -= 2
        why += "；一般媒介，多家竞争但推广分散（-2）"
    if "公開中" in str(row.get("公開状況") or ""):
        s += 2
        why += "；平台公开中（+2）"
    return max(0, min(100, s)), why


_SCORERS = {
    "location":  _score_location,
    "value":     _score_value,
    "yield":     _score_yield,
    "quality":   _score_quality,
    "comfort":   _score_comfort,
    "liquidity": _score_liquidity,
}


def build_radar(row: dict, ctx: dict | None = None) -> dict:
    """算出六维分数 + 综合分。ctx 里放全量样本的统计量（如单价中位数）。"""
    ctx = ctx or {}
    dims, overall = [], 0.0
    for key, zh, tw, en, ja, weight in DIMS:
        fn = _SCORERS[key]
        score, reason = (fn(row, ctx) if key == "value" else fn(row))
        dims.append({"key": key, "label": DIM_LABEL[key],
                     "score": int(score), "weight": weight, "reason": reason})
        overall += int(score) * weight / 100.0
    return {"dims": dims, "overall": round(overall), "max": 100}


# ============================================================
# 约 200 字的 AI 结论
# ============================================================
def _grade(overall: int) -> str:
    if overall >= 85:
        return "综合表现突出"
    if overall >= 78:
        return "整体条件优秀"
    if overall >= 70:
        return "综合表现良好"
    if overall >= 62:
        return "条件中等偏上"
    if overall >= 54:
        return "中规中矩"
    return "需谨慎评估"


def _fit(overall: int, dims: list[dict], rented: bool) -> str:
    """给一句「适合谁」的定位——投资人最想知道的就是这个。"""
    v = next((d["score"] for d in dims if d["key"] == "value"), 60)
    y = next((d["score"] for d in dims if d["key"] == "yield"), 60)
    c = next((d["score"] for d in dims if d["key"] == "comfort"), 60)
    if rented or y >= 78:
        return "整体更适合以收租为目的、追求稳定现金流的投资者"
    if v >= 78:
        return "价格具备优势，适合兼顾自住与保值的买家"
    if c >= 80:
        return "居住属性突出，适合自住或长期持有型买家"
    if overall >= 70:
        return "综合均衡，投资与自住两种用途都能兼顾"
    return "建议在价格与持有成本上再做一轮谈判，适合有明确改造或运营计划的买家"


def build_conclusion(row: dict, radar: dict, anomaly_count: int = 0,
                     ctx: dict | None = None) -> str:
    """拼一段约 200 字的中文结论。

    叙述顺序按买家真实阅读习惯：**地段 → 房子 → 钱 → 能不能生钱 → 值不值 → 风险**。
    每一句都必须有真实字段支撑，绝不写"配套成熟、交通便利"这种放之四海皆准的空话。
    """
    ctx = ctx or {}
    name = str(row.get("物件名") or "").strip()
    ward = str(row.get("区") or "").strip()
    st = _station_name(row.get("最寄駅1"))
    w = walk_minutes(row.get("最寄駅1"))
    year = _num(row.get("築年（西暦）"))
    madori = str(row.get("間取り") or "").strip()
    area = _num(row.get("専有面積（㎡）"))
    man = _num(row.get("価格（税込・万円）"))
    unit, _unote = unit_price(row, ctx)
    fee = _num(row.get("管理費・積立金等合計（円/月）")) or _num(row.get("管理費（円/月）"))
    struct = _struct_label(row)
    status = _enum_clean(row.get("現況"), KNOWN_STATUS)
    units = _num(row.get("総戸数（戸）"))
    mgr = _enum_clean(row.get("管理体制"), KNOWN_MGMT)
    park = str(row.get("駐車場") or "").strip()
    balcony = str(row.get("バルコニー") or "").strip()
    floor = _num(row.get("所在階"))
    total_floor = _num(row.get("地上階数"))
    overall = radar.get("overall", 0)
    dims = radar.get("dims", [])
    top = sorted(dims, key=lambda d: -d["score"])[:2]
    weak = sorted(dims, key=lambda d: d["score"])[0]

    seg = []
    # ① 地段交通
    if st and w is not None:
        seg.append(f"本物件位于{ward or '大阪市'}，{st}站徒步{w}分钟")
    else:
        seg.append(f"本物件位于{ward or '大阪市'}")
    if name:
        seg.append(f"，属「{name}」")
    seg.append("。")

    # ② 房子本体
    body = []
    if year:
        body.append(f"{int(year)}年建成（房龄约{int(2026 - year)}年）")
    if struct:
        body.append(f"{struct}")
    if area:
        body.append(f"专有面积{area:g}㎡")
    if madori:
        body.append(f"{madori}格局")
    if floor and total_floor:
        body.append(f"位于{int(floor)}层/共{int(total_floor)}层")
    elif floor:
        body.append(f"位于{int(floor)}层")
    if body:
        seg.append("、".join(body) + "。")

    # ③ 规模与管理
    mg = []
    if units:
        mg.append(f"全栋{int(units)}户")
    if mgr:
        mg.append(f"管理为{mgr}")
    if balcony and ("有" in balcony or "㎡" in balcony):
        mg.append("带阳台")
    if park and "有" in park:
        mg.append("可停车")
    if mg:
        seg.append("，".join(mg) + "。")

    # ④ 钱（价格 / 单价 / 持有成本）
    money = []
    if man:
        money.append(f"挂牌价{man:g}万日元")
    if unit:
        money.append(f"单价约{unit/10000:.1f}万/㎡")
    if fee:
        money.append(f"每月管理类支出约{int(fee):,}日元")
    if money:
        seg.append("，".join(money) + "。")

    # ⑤ 能否生钱
    if _is_rented(row):
        rent = _rent_yen(row)
        if rent and man:
            gy = rent * 12 / (man * 10000) * 100
            seg.append(f"现况租赁中，月租约{int(rent):,}日元，表面回报约{gy:.2f}%，买入即可产生现金流。")
        else:
            seg.append("现况租赁中，带租约交付，接手即有租金。")
    elif "空室" in status:
        seg.append("现况空室，可立即看房自用，也便于重新招租设定租金。")
    elif status:
        seg.append(f"现况为{status}。")

    # ⑥ 综合判断 + 长短板 + 适合谁
    seg.append(f"综合评分{overall}分，{_grade(overall)}")
    if top:
        seg.append(f"，强项在{'与'.join(d['label']['zh'] for d in top)}")
    if weak and weak["score"] < 65:
        seg.append(f"，{weak['label']['zh']}相对偏弱（{weak['score']}分）")
    seg.append("。")
    seg.append(_fit(overall, dims, _is_rented(row)) + "。")

    # ⑦ 风险提示
    if anomaly_count:
        seg.append(f"本条有{anomaly_count}处AI识别值存疑（页面以红色标注，特别存疑为红字黄底），成交前请对照原始图纸人工复核。")

    return "".join(seg)
