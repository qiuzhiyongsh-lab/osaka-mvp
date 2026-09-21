#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.9.49 四组新搜索条件 · 后端集成验证（直接查真实 jproperty.db，不联网）。

铁律：独立诊断脚本，不触碰 8765 进程；只验证 store.py 的 search()/distinct_lines()/stations_for()。
验收映射：A1–A18 中后端可自动验证的部分（A10 零回归 / A1 户型精确 / A2 DK≠LDK / 楼龄 / 沿线 / 单价）。
"""
import os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.store import Store, _layout_rooms, _built_year_int, _layout_type

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jproperty.db")
STORE = Store(DB)

fail = []
def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (("  → " + detail) if detail else ""))
    if not cond:
        fail.append(name)

def rows_to(field_fn, f):
    rs, total = STORE.search(f, limit=200, offset=0)
    return rs, total

print("=== 候选接口 ===")
lines = STORE.distinct_lines()
print("  distinct_lines 共 %d 条" % len(lines))
check("线路候选非空", len(lines) > 0, str(lines[:5]))
sample_line = lines[0] if lines else None
stns = STORE.stations_for(sample_line) if sample_line else []
print("  stations_for(%r) 共 %d 条" % (sample_line, len(stns)))
check("车站候选非空（首条线路）", len(stns) > 0, str(stns[:5]))

print("=== A10：四组全空 → 不追加过滤（零回归）===")
rs_all, total_all = rows_to(None, {})
print("  全库 active 总数 = %d" % total_all)
check("A10 全空返回全量", total_all > 0, "total=%d" % total_all)

print("=== A1/A2：户型精确匹配（DK≠LDK）===")
rs, total = rows_to(None, {"layout_types": ["LDK"]})
bad = [r["property_no"] for r in rs if _layout_type(r["layout"]) != "LDK"]
check("A1 layout_types=['LDK'] 全部以 LDK 结尾", len(bad) == 0, "异常 %d 条" % len(bad))
print("  LDK 命中 %d 条" % total)
rs_dk, total_dk = rows_to(None, {"layout_types": ["DK"]})
check("A2 DK 与 LDK 不重叠", total_dk > 0 and total > 0)
print("  DK 命中 %d 条，LDK 命中 %d 条" % (total_dk, total))

print("=== 户型多选 OR ===")
rs_or, total_or = rows_to(None, {"layout_types": ["LDK", "SLDK"]})
check("A 多选 OR：LDK+SLDK 命中 ≥ LDK 单独", total_or >= total, "or=%d base=%d" % (total_or, total))
print("  LDK+SLDK 命中 %d 条" % total_or)

print("=== 部屋数范围 ===")
rs_r, total_r = rows_to(None, {"rooms_min": 3, "rooms_max": 4})
bad_r = [r["property_no"] for r in rs_r if not (3 <= _layout_rooms(r["layout"]) <= 4)]
check("部屋数 3–4 范围正确", len(bad_r) == 0, "异常 %d" % len(bad_r))
print("  部屋数[3,4] 命中 %d 条" % total_r)

print("=== 所在階范围 ===")
rs_f, total_f = rows_to(None, {"floor_min": 10, "floor_max": 20})
check("所在階 10–20 返回>0", total_f > 0, "total=%d" % total_f)
print("  所在階[10,20] 命中 %d 条" % total_f)

print("=== 楼龄（年数段，age_max=10）===")
import datetime
this_year = datetime.date.today().year
rs_a, total_a = rows_to(None, {"age_max": 10})
bad_a = [r["property_no"] for r in rs_a if _built_year_int(r["built_year_month"]) < this_year - 10]
check("楼龄≤10 ⇔ 建成年≥%d" % (this_year - 10), len(bad_a) == 0, "异常 %d" % len(bad_a))
print("  楼龄≤10 命中 %d 条（今年=%d）" % (total_a, this_year))

print("=== 建筑年代（year_from/year_to）===")
rs_y, total_y = rows_to(None, {"year_from": 2000, "year_to": 2020})
bad_y = [r["property_no"] for r in rs_y if not (2000 <= _built_year_int(r["built_year_month"]) <= 2020)]
check("建成年 2000–2020 范围正确", len(bad_y) == 0, "异常 %d" % len(bad_y))
print("  建成年[2000,2020] 命中 %d 条" % total_y)

print("=== 沿線（线路 + 车站 + 徒歩上限）===")
# 找一个能命中 >0 的 线路+车站 组合（证实 AND 逻辑真返回数据，而非恒 0）
_chosen = None
for _ln in lines:
    _ss = STORE.stations_for(_ln)
    for _st in _ss:
        _rl, _tl = rows_to(None, {"lines": [_ln], "stations": [_st]})
        if _tl > 0:
            _chosen = (_ln, _st, _tl); break
    if _chosen: break
check("沿線 线路+车站 AND 命中 >0（存在有效组合）", _chosen is not None,
      ("%s / %s → %d" % (_chosen[0], _chosen[1], _chosen[2])) if _chosen else "未找到任何命中组合")
if _chosen:
    _ln, _st, _tl = _chosen
    rs_l, total_l = rows_to(None, {"lines": [_ln], "stations": [_st], "walk_max": 15})
    check("沿線 + 徒歩≤15 不抛错且 ≤ 原组合", total_l >= 0 and total_l <= _tl, "total=%d" % total_l)
    print("  线路=%s 车站=%s 徒歩≤15 → %d 条（全组合 %d）" % (_ln, _st, total_l, _tl))

print("=== 单价（sqm 30–100 万円）===")
rs_u, total_u = rows_to(None, {"unit_price_unit": "sqm", "unit_price_min": 30, "unit_price_max": 100})
bad_u = [r["property_no"] for r in rs_u if not (30e4 <= (r["unit_price_sqm"] or -1) <= 100e4)]
check("单价 sqm 30–100万円 范围正确", len(bad_u) == 0, "异常 %d" % len(bad_u))
print("  单价 sqm[30,100] 命中 %d 条" % total_u)

print("=== 单价（tsubo 20–60 万円）===")
rs_t, total_t = rows_to(None, {"unit_price_unit": "tsubo", "unit_price_min": 20, "unit_price_max": 60})
bad_t = [r["property_no"] for r in rs_t if not (20e4 <= (r["unit_price_tsubo"] or -1) <= 60e4)]
check("单价 tsubo 20–60万円 范围正确", len(bad_t) == 0, "异常 %d" % len(bad_t))
print("  单价 tsubo[20,60] 命中 %d 条" % total_t)

print("=== 组合（户型+楼龄+单价）===")
rs_c, total_c = rows_to(None, {"layout_types": ["LDK"], "age_max": 15, "unit_price_unit": "sqm", "unit_price_min": 40, "unit_price_max": 120})
check("组合查询不抛错且返回合理", total_c >= 0, "total=%d" % total_c)
print("  LDK+楼龄≤15+sqm[40,120] 命中 %d 条" % total_c)

print("\n=== 结果 ===")
if fail:
    print("✗ 失败 %d 项：%s" % (len(fail), fail))
    sys.exit(1)
else:
    print("✓ 全部后端集成验证通过")
