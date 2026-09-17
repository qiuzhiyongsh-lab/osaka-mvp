# -*- coding: utf-8 -*-
"""数值合理性 + 爬列错位 回归测试（v1.7.3 新建，补 v1.7.2 遗漏的那一层）

背景：勇哥 2026-09-16 质疑「修繕積立金 10,346,000 円 肯定不对，测试是怎么做的？」
核实：(3,10) 这一格被当成修繕積立金，实际是坪単価；全库 1060 条 repair_fund
**无一条有效**（最小值 419,000，月额不可能）。v1.7.2 的 22 个用例只测了
「字段词典/机器键不上屏」，没测数值合理性，所以一条都没拦住 —— 本文件补齐。
"""
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

import verify_value_sanity as vs
from core.crawler import _postprocess_list_rec

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = r"C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe"


def _mkdb(tmp_path, rows) -> Path:
    """造一个最小 properties 表，只含门禁需要的三列。"""
    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE properties (property_no TEXT, unit_price_tsubo TEXT, detail_json TEXT)")
    con.executemany("INSERT INTO properties VALUES (?,?,?)", rows)
    con.commit()
    con.close()
    return db


# ------------------------------------------------ 抓取侧：列错位必须被拦下
def test_crawler_drops_misaligned_repair_fund():
    """勇哥报的那条：repair_fund 与坪単価完全相等 → 判定为坪単価列，丢弃。"""
    out = _postprocess_list_rec({
        "property_subtype": "新築マンション",
        "repair_fund": "10,346,000",
        "unit_price_tsubo": "10,346,000",
    })
    assert out.get("repair_fund") is None, "错位值不应写进 repair_fund"
    assert out.get("unit_price_tsubo") == 10_346_000, "坪単価必须保住"


def test_crawler_keeps_normal_repair_fund():
    """正常的月额修繕積立金必须保留（不能一刀切全删）。"""
    out = _postprocess_list_rec({
        "property_subtype": "中古マンション",
        "repair_fund": "9,250",
        "unit_price_tsubo": "1,135,000",
    })
    assert out.get("repair_fund") == 9_250


def test_crawler_moves_to_tsubo_when_empty():
    """坪単価主列为空时，错位值应迁过去而不是丢掉。"""
    out = _postprocess_list_rec({
        "property_subtype": "中古マンション",
        "repair_fund": "2,470,000",
    })
    assert out.get("repair_fund") is None
    assert out.get("unit_price_tsubo") == 2_470_000


def test_crawler_fix_is_subtype_agnostic():
    """教训：v1.6 补丁只按「売地」打，换个種目就复发。本用例锁死「与種目无关」。"""
    for sub in ("売地", "新築マンション", "中古マンション", "中古戸建", "新築戸建"):
        out = _postprocess_list_rec({
            "property_subtype": sub,
            "repair_fund": "5,000,000",
            "unit_price_tsubo": "5,000,000",
        })
        assert out.get("repair_fund") is None, "%s 的错位值没被拦下" % sub


# ------------------------------------------------ 门禁侧：能检出脏数据
def test_gate_detects_column_misalignment(tmp_path):
    db = _mkdb(tmp_path, [
        ("A1", "10346000", json.dumps({"repair_fund": 10_346_000})),
        ("A2", "", json.dumps({"repair_fund": 9_250})),
        ("A3", "", json.dumps({"management_fee": 9_250})),
    ])
    dirty = vs.scan(db)
    assert "A1:repair_fund" in dirty, "列错位未被检出"
    assert "A2:repair_fund" not in dirty, "正常值被误判"
    assert "A3:management_fee" not in dirty, "正常值被误判"


def test_gate_detects_impossible_monthly_fee(tmp_path):
    db = _mkdb(tmp_path, [
        ("B1", "", json.dumps({"management_fee": 31_236_000})),
    ])
    dirty = vs.scan(db)
    assert "B1:management_fee" in dirty


# ------------------------------------------------ 全库：不允许出现「新增」脏数据
def test_no_new_dirty_beyond_baseline(real_db_copy):
    """baseline 里是历史待清洗清单；只要冒出 baseline 之外的新脏数据就红。"""
    if not Path(VENV_PY).exists():
        pytest.skip("venv 不存在：%s" % VENV_PY)
    r = subprocess.run(
        [str(VENV_PY), str(ROOT / "tools" / "verify_value_sanity.py"), "--db", str(real_db_copy)],
        capture_output=True, text=True,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
    assert r.returncode == 0, "出现了 baseline 之外的新增脏数据"
