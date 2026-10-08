# -*- coding: utf-8 -*-
"""详情页渲染路径（app._detail_pairs）真实回归：真库全量无机器键 + 枚举翻译 + 值本地化。

铁律：只读真库副本（webapp fixture 已把 OSAKA_DB 指向副本），绝不碰真库、绝不监听端口。
"""
import json
import os
import sqlite3
from pathlib import Path

import app as app_mod
from verify_field_labels import is_machine_label


def _all_detail_rows():
    con = sqlite3.connect(os.environ["OSAKA_DB"])
    con.row_factory = sqlite3.Row
    return con.execute(
        "SELECT * FROM properties WHERE detail_json IS NOT NULL AND detail_json<>''"
    ).fetchall()


def test_detail_pairs_no_machine_key_on_real_db(webapp):
    app_mod._MISSING_KEYS.clear()
    bad = []
    for r in _all_detail_rows():
        for label, _value, _ag in app_mod._detail_pairs(r):
            if is_machine_label(str(label)):
                bad.append((r["property_no"], label))
    assert not bad, "详情页机器键上屏：%s" % bad[:5]


def test_detail_pairs_missing_keys_empty(webapp):
    app_mod._MISSING_KEYS.clear()
    for r in _all_detail_rows():
        app_mod._detail_pairs(r)
    assert not app_mod._MISSING_KEYS, "词典未收录键：%s" % sorted(app_mod._MISSING_KEYS)


def test_detail_pairs_synthetic_value_localization(webapp):
    """合成一行，验证有/无、円/㎡(小值)、円/坪、枚举逐段翻译都正确。"""
    row = {
        "property_no": "X1", "kind": "中古マンション", "property_subtype": "売マンション",
        "ward": "北区", "address": "a", "building_name": "b", "line_station": "c",
        "price": "36800000", "previous_price": "", "unit_price_sqm": "5632",
        "unit_price_tsubo": "80000", "exclusive_area": "70", "land_area": "",
        "building_area": "", "layout": "3LDK", "floor": "5", "above_ground_floors": "10",
        "built_year_month": "202001", "image_count": 3,
        "registration_date": "2026-09-01", "change_date": "", "first_seen_at": "2026-09-16",
        "last_seen_at": "2026-09-16",
        "detail_json": json.dumps({
            "has_floorplan": "0", "has_map": "1", "trade_type": "専任",
            "use_zone": "近商", "public_status": "公開中", "management_fee": "12000",
        }, ensure_ascii=False),
    }
    pairs = dict((lab, val) for lab, val, _ag in app_mod._detail_pairs(row))
    assert pairs.get("户型图") == "无"
    assert pairs.get("位置图") == "有"
    assert pairs.get("㎡単価") == "5,632 円/㎡"          # bug 修复回归（小值走 円/㎡）
    # 注：详情页 Python 路径对 ≥10000 走「万円/坪」；查询页 JS 路径对同值走「円/坪」
    #     两路径展示口径不一致（待统一，见交付说明），此处按详情页真实行为断言。
    assert pairs.get("坪単価") == "8.0 万円/坪"
    assert pairs.get("交易方式") == "专任媒介"
    assert pairs.get("用途地域") == "邻近商业地区"
    assert pairs.get("公开状态") == "公开中"
    assert pairs.get("管理费（每月）") == "12,000 円"
    for lab in pairs:
        assert not is_machine_label(lab), "机器键上屏：%s" % lab


# ============================================================
# v1.9.124 防回归门禁：中介公司 + 电话 在全部渲染链不得再次丢失
# （勇哥 2026-10-08 反馈「原有信息后续又没了」已是第 2~3 次，根因 = 三链独立实现 +
#  手动发版滞后 + 静态资源改了未抬 ?v= 缓存破坏 → 任一链重构漏字段即复发）
# ============================================================
_BROKER_NO = "300140894939"
_BROKER_CO = "（株）ＥＳ＆Ｃｏｍｐａｎｙ"
_BROKER_TEL = "0666849636"


def _real_row(no):
    con = sqlite3.connect(os.environ["OSAKA_DB"])
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM properties WHERE property_no=?", (no,)).fetchone()
    con.close()
    return row


def test_row_payload_broker_real_db(webapp):
    """真库房源 300140894939：_row_payload 必须把中介公司/电话从 detail_json 透传（员工卡片同源出口）。"""
    row = _real_row(_BROKER_NO)
    assert row, "房源 %s 不在真库" % _BROKER_NO
    p = app_mod._row_payload(row)
    assert p.get("broker") == _BROKER_CO, "broker 透传丢失: %r" % p.get("broker")
    assert p.get("broker_tel") == _BROKER_TEL, "broker_tel 透传丢失: %r" % p.get("broker_tel")


def test_detail_pairs_broker_real_db(webapp):
    """真库房源 300140894939：_detail_pairs 必含『中介公司』行，值含公司名 + ☎ 电话，且标 agency。"""
    row = _real_row(_BROKER_NO)
    assert row, "房源 %s 不在真库" % _BROKER_NO
    pairs = app_mod._detail_pairs(row)
    agency_co = [(lab, val) for lab, val, ag in pairs if lab == "中介公司" and ag]
    assert agency_co, "详情页缺『中介公司』agency 行"
    val = agency_co[0][1]
    assert _BROKER_CO in val, "『中介公司』缺公司名: %r" % val
    assert ("☎ " + _BROKER_TEL) in val, "『中介公司』缺电话: %r" % val


def test_detail_pairs_broker_synthetic(webapp):
    """合成行：有 broker → 合并『中介公司』单行；有公司无电话 → 只公司；broker 空 → 不出现该行。"""
    base = {"property_no": "X1", "kind": "中古マンション", "detail_json": json.dumps({}, ensure_ascii=False)}

    r1 = dict(base); r1["detail_json"] = json.dumps({"broker": "（株）テスト", "broker_tel": "0123456789"}, ensure_ascii=False)
    p1 = dict((lab, val) for lab, val, _ag in app_mod._detail_pairs(r1))
    assert p1.get("中介公司") == "（株）テスト ☎ 0123456789"

    r2 = dict(base); r2["detail_json"] = json.dumps({"broker": "（株）テスト", "broker_tel": ""}, ensure_ascii=False)
    p2 = dict((lab, val) for lab, val, _ag in app_mod._detail_pairs(r2))
    assert p2.get("中介公司") == "（株）テスト"

    r3 = dict(base); r3["detail_json"] = json.dumps({"broker": "", "broker_tel": ""}, ensure_ascii=False)
    p3 = dict((lab, val) for lab, val, _ag in app_mod._detail_pairs(r3))
    assert "中介公司" not in p3


def test_three_chain_broker_invariant(webapp):
    """三链一致性门禁：查询页母本(search.html)、员工卡片(prop_card.js)、详情页(app.py _detail_pairs)
    都必须保留 broker 渲染，防止任一链被重构时漏掉 → 信息再次丢失（复发根因）。"""
    root = Path(__file__).resolve().parent.parent
    search_html = (root / "web" / "templates" / "search.html").read_text(encoding="utf-8")
    prop_card = (root / "web" / "static" / "prop_card.js").read_text(encoding="utf-8")
    app_py = (root / "web" / "app.py").read_text(encoding="utf-8")
    assert 'class="broker"' in prop_card and "data-agency" in prop_card, "prop_card.js 漏 broker 渲染"
    assert "broker" in search_html and "data-agency" in search_html, "search.html 母本漏 broker 渲染"
    assert "_broker" in app_py and "中介公司" in app_py, "app.py _detail_pairs 漏 broker 合并"
