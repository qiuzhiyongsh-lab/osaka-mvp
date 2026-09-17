#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/verify_value_sanity.py —— 「数值合理性」回归门禁（v1.7.3 新建）

背景（勇哥 2026-09-16 质问）：
    物件 100140455511 显示「修繕積立金（每月）10,346,000 円」—— 荒谬。
    核实：该值 == unit_price_tsubo(10,346,000)，即列表页 (3,10) 这一格被抓成了
    坪単価列却存进 repair_fund。全库 1060 条 repair_fund 中：
      781 条(73.7%) 与 unit_price_tsubo 完全相等 → 列错位铁证
      960 条(90.6%) >= 100万/月 → 物理上不可能

为什么以前的测试没拦住（教训）：
    v1.7.2 建的 22 个用例只覆盖「字段词典 / 机器键不上屏」，**完全没有数值合理性
    与爬列错位的断言**；而 BUG-1 修复只按「売地」一种種目打补丁、还写错注释断言
    マンション 类正常。→ 同类错误换个種目就复发，无人拦截。

本脚本补齐这一层：对金额/单价类字段做**值域 + 交叉校验**。

分级：
    红 = **新出现**的脏数据（不在 baseline 里）→ 阻断（退出码 1）
    黄 = baseline 已记录的历史脏数据 → 不阻断，打印「待清洗 N 条」

这样：① 立刻具备防新增能力；② 历史脏数据有清单、不掩盖；③ 清洗后清空 baseline 即全绿。

用法：
    python tools/verify_value_sanity.py                 # 扫默认库
    python tools/verify_value_sanity.py --db X.db       # 指定库
    python tools/verify_value_sanity.py --update-baseline   # 把当前脏数据固化成 baseline
退出码：0 = 无新增脏数据；1 = 有新增（红）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "value_sanity_baseline.json"

# —— 合理区间（依据：真实库实测 + 业务常识，改这里要同步改注释里的依据）——
# 修繕積立金 / 管理费都是「月额」。全库 management_fee（抓对的那列）实测：
#   最小 2,544 / 中位 9,250 / 最大 91,000 円。故 100万/月 定为「不可能」红线。
FUND_IMPOSSIBLE = 1_000_000   # 月额 >= 100万 → 必定不是月额费用
FUND_ABNORMAL = 200_000       # 月额 >= 20万 → 异常（留 2 倍余量，仍判脏）

RED: list[str] = []
YEL: list[str] = []


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def scan(db: Path) -> dict[str, dict]:
    """返回 { "物件番号:字段": {"value":…, "reason":…} } 形式的脏数据字典。"""
    tmp = Path(tempfile.mkdtemp(prefix="vsanity_")) / "jproperty.db"
    shutil.copy2(db, tmp)
    con = sqlite3.connect(str(tmp))
    con.row_factory = sqlite3.Row
    dirty: dict[str, dict] = {}

    rows = con.execute(
        "SELECT property_no, unit_price_tsubo, detail_json FROM properties "
        "WHERE detail_json IS NOT NULL AND detail_json<>''"
    ).fetchall()

    for r in rows:
        no = r["property_no"]
        try:
            d = json.loads(r["detail_json"])
        except Exception:
            continue

        rf = _num(d.get("repair_fund"))
        mf = _num(d.get("management_fee"))
        tsubo = _num(r["unit_price_tsubo"])

        # ① 修繕積立金 == 坪単価 → 列错位铁证（最硬的一条）
        if rf and tsubo and abs(rf - tsubo) < 1:
            dirty["%s:repair_fund" % no] = {
                "value": rf, "reason": "==unit_price_tsubo(%s) 列错位" % tsubo}
        # ② 月额费用 >= 100万 → 物理不可能
        elif rf is not None and rf >= FUND_IMPOSSIBLE:
            dirty["%s:repair_fund" % no] = {"value": rf, "reason": "月额>=100万 不可能"}
        elif mf is not None and mf >= FUND_IMPOSSIBLE:
            dirty["%s:management_fee" % no] = {"value": mf, "reason": "月额>=100万 不可能"}
        # ③ 月额费用 >= 20万 → 异常（保守，留足余量）
        elif rf is not None and rf >= FUND_ABNORMAL:
            dirty["%s:repair_fund" % no] = {"value": rf, "reason": "月额>=20万 异常"}
        elif mf is not None and mf >= FUND_ABNORMAL:
            dirty["%s:management_fee" % no] = {"value": mf, "reason": "月额>=20万 异常"}

    con.close()
    return dirty


def load_baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    try:
        return set(json.loads(BASELINE.read_text(encoding="utf-8")).get("dirty", []))
    except Exception:
        return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args()

    db = Path(args.db) if args.db else ROOT / "data" / "jproperty.db"
    if not db.exists():
        print("找不到数据库：%s" % db)
        return 1

    dirty = scan(db)

    if args.update_baseline:
        BASELINE.write_text(
            json.dumps({"dirty": sorted(dirty)}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print("已固化 baseline：%d 条 → %s" % (len(dirty), BASELINE))
        return 0

    base = load_baseline()
    new_dirty = {k: v for k, v in dirty.items() if k not in base}
    known = {k: v for k, v in dirty.items() if k in base}

    print("=" * 70)
    print("数值合理性门禁（库副本：%s）" % db.name)
    print("=" * 70)
    print("检出脏数据：%d 条（历史已记录 %d 条 / 新增 %d 条）"
          % (len(dirty), len(known), len(new_dirty)))

    if new_dirty:
        print("\n[红] 新增脏数据 %d 条（不在 baseline，必须处理）：" % len(new_dirty))
        for k, v in list(new_dirty.items())[:20]:
            print("   %-28s %s  ← %s" % (k, v["value"], v["reason"]))
        if len(new_dirty) > 20:
            print("   … 还有 %d 条" % (len(new_dirty) - 20))
        RED.append("新增脏数据 %d 条" % len(new_dirty))

    if known:
        print("\n[黄] 历史脏数据 %d 条（已在 baseline，待清洗）：" % len(known))
        for k, v in list(known.items())[:5]:
            print("   %-28s %s  ← %s" % (k, v["value"], v["reason"]))
        if len(known) > 5:
            print("   … 还有 %d 条（清洗后清空 baseline 即全绿）" % (len(known) - 5))
        YEL.append("历史脏数据 %d 条待清洗" % len(known))

    print("\n" + "=" * 70)
    if RED:
        print("结果：红 %d 项 —— 不通过" % len(RED))
        return 1
    print("结果：无新增脏数据（黄 %d 项 = 历史待清洗）—— VALUE_SANITY_OK" % len(YEL))
    return 0


if __name__ == "__main__":
    sys.exit(main())
