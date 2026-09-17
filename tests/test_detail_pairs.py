# -*- coding: utf-8 -*-
"""详情页渲染路径（app._detail_pairs）真实回归：真库全量无机器键 + 枚举翻译 + 值本地化。

铁律：只读真库副本（webapp fixture 已把 OSAKA_DB 指向副本），绝不碰真库、绝不监听端口。
"""
import json
import os
import sqlite3

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
