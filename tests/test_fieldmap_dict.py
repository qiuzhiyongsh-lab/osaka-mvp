# -*- coding: utf-8 -*-
"""字段词典覆盖测试（查询页/详情页/对比页共用的 fieldmap.js + i18n.js + app.JP2CN）。

关 director 红项：「tests/ 具体用例」—— 验证此前直接上屏的机器键都已收口，
且三处渲染共用同一份词典。
"""
import verify_field_labels as v
import app as app_mod

JS = v.load_js()
I18N = v.load_i18n()

# 勇哥 2026-09-16 反馈：这批机器键此前原样上屏
LEAKED = ["ward", "unit_price_sqm", "unit_price_tsubo", "has_photo", "has_floorplan", "has_map"]


def test_field_cn_covers_leaked_keys():
    for k in LEAKED:
        assert JS["cn"].get(k), "FIELD_CN 漏收录机器键 %s → 会原样上屏" % k


def test_field_key_maps_to_i18n():
    for k in LEAKED:
        i18n_key = JS["key"].get(k, "")
        assert i18n_key.startswith("xf."), "FIELD_KEY[%s] 应指向 xf.* i18n 键，实际=%r" % (k, i18n_key)


def test_i18n_four_language_complete():
    missing = []
    for k, i18n_key in JS["key"].items():
        for lang in ("zh", "zh-TW", "en", "ja"):
            if i18n_key not in I18N[lang]:
                missing.append("%s→%s@%s" % (k, i18n_key, lang))
    assert not missing, "i18n 四语言缺词条：%s" % ", ".join(missing)


def test_field_skip_internal_keys():
    internal = {"pdf_url", "pdf_path", "absent_runs", "_ms_pdf", "__fp__", "detail_json"}
    for k in internal:
        assert JS["skip"].get(k) == "1", "内部键 %s 未进 FIELD_SKIP → 可能上屏" % k
        assert v.js_field_label(k, JS) == "", "内部键 %s 仍会渲染标签" % k


def test_enum_cn_fixture_values():
    en = JS["enum"]
    assert en["trade_type"].get("専任") == "专任媒介"
    assert en["use_zone"].get("近商") == "邻近商业地区"
    assert en["public_status"].get("公開中") == "公开中"


def test_field_label_unknown_returns_empty_not_raw():
    """v1.7.2 规则反转：未收录键 → 返回空串（不显示），绝不再原样上屏。"""
    assert v.js_field_label("totally_unknown_machine_key", JS) == ""


def test_app_jp2cn_consistent_with_fieldmap():
    """app.JP2CN 也应收口同一批键（详情页走 Python 路径）。"""
    for k in LEAKED:
        assert k in app_mod.JP2CN, "app.JP2CN 漏收录机器键 %s" % k
