# -*- coding: utf-8 -*-
"""值本地化测试：有/无、円/㎡（小值千分位，修 bug）、円/坪、金额千分位、枚举中文。"""
import verify_field_labels as v
import app as app_mod

JS = v.load_js()


def test_bool_no():
    assert v.js_field_value("has_floorplan", "0") == "无"
    assert v.js_field_value("has_map", "false") == "无"
    assert v.js_field_value("has_photo", "None") == "无"


def test_bool_yes():
    assert v.js_field_value("has_photo", "1") == "有"
    assert v.js_field_value("has_map", "1") == "有"


def test_unit_price_small_bugfix():
    # v1.7.2 bug 修复：5632 不能显示成「0 万円/㎡」
    assert v.js_field_value("unit_price_sqm", "5632") == "5,632 円/㎡"


def test_unit_price_large_uses_manen():
    # v1.7.3 口径统一（勇哥定：房子都是万円为单位）：>=1万 走万円，与详情页一致
    assert v.js_field_value("unit_price_sqm", "120000") == "12.0 万円/㎡"
    assert v.js_field_value("unit_price_sqm", "3130000") == "313.0 万円/㎡"


def test_unit_price_tsubo():
    assert v.js_field_value("unit_price_tsubo", "80000") == "8.0 万円/坪"
    # 小值仍走 円，避免「0.6 万円/㎡」这种没信息量的写法
    assert v.js_field_value("unit_price_sqm", "5632") == "5,632 円/㎡"


def test_money_thousands():
    assert v.js_field_value("management_fee", "12000") == "12,000 円"
    assert v.js_field_value("repair_fund", "8000") == "8,000 円"


def test_blank_value_hidden():
    assert v.js_field_value("has_photo", "-") == ""
    assert v.js_field_value("has_map", "") == ""
    assert v.js_field_value("has_floorplan", "－") == ""


def test_is_machine_label():
    assert v.is_machine_label("has_map") is True
    assert v.is_machine_label("unit_price_sqm") is True
    assert v.is_machine_label("所在区") is False
    assert v.is_machine_label("用途地域") is False


def test_enum_cn_python():
    # app._enum_cn：多行枚举逐段翻译拼接
    assert app_mod._enum_cn("trade_type", "専任") == "专任媒介"
    assert app_mod._enum_cn("use_zone", "近商") == "邻近商业地区"
    assert app_mod._enum_cn("public_status", "公開中") == "公开中"
    assert app_mod._enum_cn("trade_type", "売主\nオーナーチェンジ") == "业主直售 · 业主变更（带租约出售）"


def test_internal_keys_never_in_jp2cn_render():
    # 内部机器键即使进了 detail_json，也不该被 JP2CN 翻出标签
    for k in app_mod._INTERNAL_KEYS:
        assert k not in app_mod.JP2CN, "内部键 %s 进了 JP2CN，会泄露" % k
