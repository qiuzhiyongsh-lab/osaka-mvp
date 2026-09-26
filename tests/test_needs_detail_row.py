# -*- coding: utf-8 -*-
"""_needs_detail 回归测试：sqlite3.Row 入参绝不能触发 .get() AttributeError。

背景（v1.9.94 P0 修复，见 docs/REINS数据完整性核对_2026-09-26.md §六）：
    store.get_property() 返回 sqlite3.Row，而 _needs_detail 规则③/④ 历史上误用
    existing.get("pdate_checked") —— sqlite3.Row 没有 .get() 方法（只有下标 []），
    抛 AttributeError，使『今日日期同步』（自动增量）从 2026-09-25 12:30 起每轮中断，
    当日新上架房源漏抓。修复在 _needs_detail 入参顶部把 existing 归一化为 dict。

铁律：
  · 纯函数单测，不碰真库、不碰 8765、不 import app / web。
  · 用**真实 sqlite3.Row**（而非手工 Mock）喂入，确保回归点真实覆盖。
  · pdate_checked 一律用 INTEGER（0/1），贴合真实库列语义（"0"/"1" 字符串会令
    `not existing.get("pdate_checked")` 语义反转，详见 _needs_detail 伪详情分支）。

覆盖：
  1. 核心回归：真实 sqlite3.Row 入参三分支（有地址 / 伪详情 / 已核验伪详情）不抛 AttributeError。
  2. 归一化不破坏 dict 旧路径：dict 入参与 Row 入参结果一致。
  3. 规则① None -> True；规则② 空 detail_json -> True；规则③ 缺平台日期未核验 -> True。
"""
import json
import sqlite3

import core.crawler as crawler


def _make_row(values: dict) -> sqlite3.Row:
    """造一个真实 sqlite3.Row（模拟 store.get_property 返回值）。

    列类型按插入值的 Python 类型走，故 pdate_checked 传 int 则取回也是 int，
    贴合真实库 INTEGER 列语义。
    """
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    cols = ", ".join('"%s"' % k for k in values)
    placeholders = ", ".join("?" for _ in values)
    con.execute("CREATE TABLE p(%s)" % cols)
    con.execute(
        "INSERT INTO p(%s) VALUES(%s)"
        % (", ".join('"%s"' % k for k in values), placeholders),
        tuple(values.values()),
    )
    return con.execute("SELECT * FROM p").fetchone()


def _row(address, pdate_checked=0, reg="2026-09-26", chg=""):
    """造一行 Row：address 非空=有详情；空=伪详情。pdate_checked 用 int。"""
    dj = json.dumps({"address": address}) if address else json.dumps({"price": "1000"})
    return _make_row({
        "property_no": "X",
        "detail_json": dj,
        "pdate_checked": pdate_checked,
        "reg_date_iso": reg,
        "chg_date_iso": chg,
    })


# ---------------------------------------------------------------------------
# 1. 核心回归：真实 sqlite3.Row 入参，绝对不能抛 AttributeError
# ---------------------------------------------------------------------------
def test_row_input_never_raises_attributeerror():
    # 有地址（完整详情）-> False（不重抓）
    assert crawler._needs_detail(_row("大阪市北区", pdate_checked=0)) is False
    # 伪详情（缺 address）未核验 -> True（规则④补抓一次）
    assert crawler._needs_detail(_row("", pdate_checked=0)) is True
    # 伪详情已核验 -> False（护栏：不无限重抓）
    assert crawler._needs_detail(_row("", pdate_checked=1)) is False


# ---------------------------------------------------------------------------
# 2. 归一化不破坏 dict 旧路径：dict 入参与 Row 入参结果一致
#    （确保修复只是兼容层，不改变既有行为语义）
# ---------------------------------------------------------------------------
def test_dict_input_matches_row_input():
    dj_full = json.dumps({"address": "a"})
    dj_pseudo = json.dumps({"price": "1000"})
    d_full = {"detail_json": dj_full, "pdate_checked": 0,
              "reg_date_iso": "2026-09-26", "chg_date_iso": ""}
    d_pseudo = {"detail_json": dj_pseudo, "pdate_checked": 0,
                "reg_date_iso": "2026-09-26", "chg_date_iso": ""}
    d_pseudo_checked = {"detail_json": dj_pseudo, "pdate_checked": 1,
                        "reg_date_iso": "", "chg_date_iso": ""}
    assert crawler._needs_detail(d_full) == crawler._needs_detail(_row("a", pdate_checked=0))
    assert crawler._needs_detail(d_pseudo) == crawler._needs_detail(_row("", pdate_checked=0))
    assert crawler._needs_detail(d_pseudo_checked) == crawler._needs_detail(_row("", pdate_checked=1))


# ---------------------------------------------------------------------------
# 3. 各规则分支（用 dict，类型明确，断言语义）
# ---------------------------------------------------------------------------
def test_rule1_none_returns_true():
    """规则①：库里没有这套房 -> 抓。"""
    assert crawler._needs_detail(None) is True


def test_rule2_empty_detail_json_returns_true():
    """规则②：detail_json 空串 / None -> 抓。"""
    assert crawler._needs_detail({"detail_json": ""}) is True
    assert crawler._needs_detail({"detail_json": None}) is True


def test_rule3_missing_platform_date_triggers_refetch():
    """规则③：有详情、有地址、但缺平台日期且未核验 -> 补抓一次；已核验则放过。"""
    no_date = {"detail_json": json.dumps({"address": "a"}),
               "pdate_checked": 0, "reg_date_iso": "", "chg_date_iso": ""}
    assert crawler._needs_detail(no_date) is True
    no_date_checked = dict(no_date, pdate_checked=1)
    assert crawler._needs_detail(no_date_checked) is False


def test_rule4_pseudo_detail_triggers_refetch_once():
    """规则④（本次修复的关键场景）：伪详情（缺 address）未核验 -> True；已核验 -> False。"""
    pseudo = {"detail_json": json.dumps({"price": "1000"}),
              "pdate_checked": 0, "reg_date_iso": "", "chg_date_iso": ""}
    assert crawler._needs_detail(pseudo) is True
    pseudo_checked = dict(pseudo, pdate_checked=1)
    assert crawler._needs_detail(pseudo_checked) is False
