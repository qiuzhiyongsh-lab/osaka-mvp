# -*- coding: utf-8 -*-
"""物件種目 · 两级目录（一级＝物件種別，二级＝物件種目）+ 多选筛选用的小词典。

沿用 REINS「基本条件」里那套**原始分类**（见用户截图 · 物件種目 下拉框）：

    一级：一戸建（売一戸建）  →  二级：新築戸建 / 中古戸建 / 新築テラス / 中古テラス
    一级：公寓（売マンション）→  二级：新築マンション / 中古マンション / 新築タウン / 中古タウン
    一级：土地（売土地）      →  没有新／中古之分，本身就是一个叶子

**二级一律显示 REINS 物件種目的原始名称**（用户要求「和原版一样」）——
面板里就是 新築マンション / 中古マンション / 新築タウン / 中古タウン 这 4 个，
不是笼统的「新築／中古」。リゾート 等其他種目归「其他」组兜底。

为什么用「关键词匹配」而不是「精确值匹配」：
  本机库只存 property_subtype 一列原始文字（实测 kind 一列一直是空的），
  而且 REINS 会在種目后面挂后缀，例如
      「中古マンション／オーナーチェンジ」「売地／オークション」
  精确匹配会漏掉这些，关键词匹配（LIKE %中古% AND %マンション%）则一并吃掉。

兜底：库里出现、但归不进上面任何叶子的種目（例：中古リゾート），
  自动进「其他」组，以"原文叶子"（x:原始種目文字）出现在面板里，
  保证任何一条数据都筛得出来、不会"查不到"。
"""
from __future__ import annotations

# 叶子 key → AND 组；每个 AND 组内是 OR 的 LIKE 片段（% 通配）
# 顺序＝面板里的显示顺序（与 REINS 下拉框一致：先 新築X / 中古X，再 新築Y / 中古Y）
LEAF_MATCH: dict[str, list[list[str]]] = {
    # 一戸建
    "house_new":         [["%新築%"], ["%戸建%"]],
    "house_old":         [["%中古%"], ["%戸建%"]],
    "house_terrace_new": [["%新築%"], ["%テラス%"]],
    "house_terrace_old": [["%中古%"], ["%テラス%"]],
    # 公寓（マンション）
    "apt_new":           [["%新築%"], ["%マンション%"]],
    "apt_old":           [["%中古%"], ["%マンション%"]],
    "apt_town_new":      [["%新築%"], ["%タウン%"]],
    "apt_town_old":      [["%中古%"], ["%タウン%"]],
    # 土地（无新／中古之分）
    "land":              [["%土地%", "%売地%"]],
}

MISC_PREFIX = "x:"          # 「其他」组叶子：x:原始種目文字 → 精确匹配


def _hit(subtype: str, groups: list[list[str]]) -> bool:
    """Python 版 LIKE：AND 组之间 AND、组内 OR（片段去掉 % 取子串）。"""
    s = subtype or ""
    return all(any(frag.strip("%") in s for frag in g) for g in groups)


def leaf_of(subtype: str) -> str | None:
    """把一条原始種目归入某个叶子 key；归不进任何叶子 → None（进「其他」组）。"""
    for key, groups in LEAF_MATCH.items():
        if _hit(subtype, groups):
            return key
    return None


def build_sql(keys: list[str]) -> tuple[list[str], list[str]]:
    """把选中的叶子 key 列表翻成 SQL 片段（多叶子之间 OR）。

    返回 (片段列表, 参数列表)。片段之间由调用方用 OR 连接；
    不认识的 key（老链接里的原始種目）退回精确匹配，保证向后兼容。
    """
    frags: list[str] = []
    params: list[str] = []
    for raw in keys or []:
        k = (raw or "").strip()
        if not k:
            continue
        if k.startswith(MISC_PREFIX):
            frags.append("property_subtype = ?")
            params.append(k[len(MISC_PREFIX):])
            continue
        groups = LEAF_MATCH.get(k)
        if not groups:
            frags.append("property_subtype = ?")
            params.append(k)
            continue
        ands = []
        for g in groups:
            ands.append("(" + " OR ".join("property_subtype LIKE ?" for _ in g) + ")")
            params += list(g)
        frags.append("(" + " AND ".join(ands) + ")")
    return frags, params


def tree(extra_subtypes=None) -> list[dict]:
    """给页面渲染的两级目录树。

    节点形态：
        {"key","i18n","children":[...]}   → 两级（一级本身不可直接选，选了＝全选其二级）
        {"key","i18n"}                    → 一级就是叶子（土地）
        {"key":"x:原文","text":"原文"}     → 「其他」组的原文叶子

    二级叶子的 `text` ＝ REINS 物件種目原始名称（不随界面语言变，和原版一致）。
    """
    nodes: list[dict] = [
        {"key": "house", "i18n": "cat.house", "children": [
            {"key": "house_new",         "text": "新築戸建"},
            {"key": "house_old",         "text": "中古戸建"},
            {"key": "house_terrace_new", "text": "新築テラス"},
            {"key": "house_terrace_old", "text": "中古テラス"},
        ]},
        {"key": "apt", "i18n": "cat.apt", "children": [
            {"key": "apt_new",      "text": "新築マンション"},
            {"key": "apt_old",      "text": "中古マンション"},
            {"key": "apt_town_new", "text": "新築タウン"},
            {"key": "apt_town_old", "text": "中古タウン"},
        ]},
        {"key": "land", "i18n": "cat.land"},
    ]
    extras = sorted({(s or "").strip() for s in (extra_subtypes or [])
                     if (s or "").strip() and leaf_of(s) is None})
    if extras:
        nodes.append({"key": "misc", "i18n": "cat.misc", "children": [
            {"key": MISC_PREFIX + s, "text": s} for s in extras]})
    return nodes
