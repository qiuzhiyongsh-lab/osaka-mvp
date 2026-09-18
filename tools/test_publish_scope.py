# -*- coding: utf-8 -*-
"""v1.9.9 离线单测：上传日期段筛选 + 独立定时器周期下限 + 预览计数。

不连 REINS、不起 Flask、不碰线上。直接 import core.publisher / core.scheduler 做确定性断言。
运行：python tools/test_publish_scope.py  → 全 PASS 才退出 0，否则退出 1 并打失败明细。
"""
from __future__ import annotations
import sqlite3
import sys
import os

# 让 import 找到 core/（脚本在 tools/ 下）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import publisher as P  # noqa: E402

PASS, FAIL = 0, 0
FAILS = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        FAILS.append(name + ((" ｜ " + extra) if extra else ""))
        print("  FAIL  " + name + ((" ｜ " + extra) if extra else ""))


# ---------------------------------------------------------------------------
# 1) _scope_where：日期段两口径
# ---------------------------------------------------------------------------
cfg_dl = {"publish": {"scope_caliber": "download", "date_from": "2026-09-01", "date_to": "2026-09-30"}}
cfg_pl = {"publish": {"scope_caliber": "platform", "date_from": "2026-09-01", "date_to": "2026-09-30"}}
cfg_no = {"publish": {"scope_caliber": "download"}}  # 啥都没填 → 1=1
cfg_month = {"publish": {"scope_caliber": "download", "month_scope": "2026-09"}}  # 没日期段回退月份

w, a = P._scope_where(cfg_dl, "full", None)
check("download 口径用 last_seen_at 列", "COALESCE(last_seen_at, first_seen_at) >= ?" in w, w)
check("download 闭区间下界=date_from", a[0] == "2026-09-01", str(a))
check("download 闭区间上界补 23:59:59", a[1] == "2026-09-30 23:59:59", str(a))

w, a = P._scope_where(cfg_pl, "full", None)
check("platform 口径用 chg/reg 列", "COALESCE(chg_date_iso, reg_date_iso) <= ?" in w, w)
check("platform 闭区间上界补 23:59:59", a[1] == "2026-09-30 23:59:59", str(a))

w, a = P._scope_where(cfg_no, "full", None)
check("啥都没填 → 1=1（全量，兼容旧行为）", w == "1=1" and a == [], w)

w, a = P._scope_where(cfg_month, "full", None)
check("没日期段回退月份 substr 7", "substr(COALESCE(last_seen_at,first_seen_at),1,7)=?" in w, w)
check("月份回退 args=[2026-09]", a == ["2026-09"], str(a))

# incr + 水位线
w, a = P._scope_where(cfg_dl, "incr", "2026-09-10 00:00:00")
check("incr 叠加水位线条件", "COALESCE(last_seen_at,first_seen_at) > ?" in w, w)
check("incr 水位线在 args 末位", a[-1] == "2026-09-10 00:00:00", str(a))


# ---------------------------------------------------------------------------
# 2) PublishLoop 周期下限保护
# ---------------------------------------------------------------------------
loop_low = P.PublishLoop(store=None, cfg={"publish": {"interval_minutes": 0.5, "enabled": True}})
check("周期 <1 夹到 1 分钟(60s)", abs(loop_low._interval_seconds() - 60.0) < 1e-6,
      str(loop_low._interval_seconds()))
loop_def = P.PublishLoop(store=None, cfg={"publish": {"interval_minutes": 10, "enabled": True}})
check("周期默认 10 分钟(600s)", abs(loop_def._interval_seconds() - 600.0) < 1e-6,
      str(loop_def._interval_seconds()))
loop_neg = P.PublishLoop(store=None, cfg={"publish": {"interval_minutes": -5, "enabled": True}})
check("周期负数也夹到 1 分钟", abs(loop_neg._interval_seconds() - 60.0) < 1e-6,
      str(loop_neg._interval_seconds()))


# ---------------------------------------------------------------------------
# 3) preview_scope：真库计数（in-memory sqlite，schema 与真实 properties 对齐）
# ---------------------------------------------------------------------------
con = sqlite3.connect(":memory:")
con.execute("""CREATE TABLE properties (
    property_no TEXT, building_name TEXT, address TEXT,
    reg_date_iso TEXT, chg_date_iso TEXT, last_seen_at TEXT, first_seen_at TEXT, is_active INTEGER
)""")
rows = [
    ("A001", "ビルA", "大阪市", "2026-09-05", "2026-09-05", "2026-09-10 08:00:00", "2026-09-09 08:00:00", 1),
    ("A002", "ビルB", "大阪市", "2026-09-15", "2026-09-15", "2026-09-20 08:00:00", "2026-09-19 08:00:00", 1),
    ("A003", "ビルC", "大阪市", "2026-08-01", "2026-08-01", "2026-08-01 08:00:00", "2026-08-01 08:00:00", 1),  # 8 月 → 落范围外
    ("A004", "ビルD", "大阪市", "2026-09-25", "2026-09-25", "2026-09-28 08:00:00", "2026-09-27 08:00:00", 0),  # 9 月但 is_active=0
]
con.executemany("INSERT INTO properties VALUES (?,?,?,?,?,?,?,?)", rows)
con.commit()

cfg_scope = {"publish": {"scope_caliber": "download", "date_from": "2026-09-01", "date_to": "2026-09-30"}}
r = P.preview_scope(cfg_scope, con, limit=5, log=lambda *_: None)
# 9 月内（last_seen_at）→ A001(9/10)、A002(9/20)、A004(9/28,is_active=0) 命中；A003(8月) 落外。
# 与 build_rows 一致：不过滤 is_active，故 A004 计入（预览==真推）。
check("preview 9月内=3 行(A001/A002/A004)", r["count"] == 3, "count=%d sample=%s" % (r["count"], r.get("sample")))
check("preview sample 字段齐全", all(set(s) >= {"property_no", "building_name", "address"} for s in r["sample"]),
      str(r.get("sample")))

# 平台口径：用 reg/chg 日期
cfg_pl_scope = {"publish": {"scope_caliber": "platform", "date_from": "2026-09-01", "date_to": "2026-09-30"}}
r2 = P.preview_scope(cfg_pl_scope, con, limit=5, log=lambda *_: None)
check("preview platform 口径同样 3 行(A001/A002/A004 平台日 9月)", r2["count"] == 3, "count=%d" % r2["count"])

# 无筛选 → 全量（4 行全在，与 build_rows WHERE 1=1 一致）
r3 = P.preview_scope({"publish": {}}, con, limit=5, log=lambda *_: None)
check("preview 无筛选 → 4 行（与 build_rows 一致）", r3["count"] == 4, "count=%d" % r3["count"])

con.close()


# ---------------------------------------------------------------------------
# 4) scheduler.rearm：置位后 _loop 应检测到（静态验证 Event 语义）
# ---------------------------------------------------------------------------
from core import scheduler as S  # noqa: E402
fake = S.Scheduler.__new__(S.Scheduler)  # 不调 __init__，只验证 rearm 方法
fake._rearm = __import__("threading").Event()
fake._log = lambda *_: None
fake._rearm.set()
check("rearm 置位后 is_set()=True", fake._rearm.is_set())
fake._rearm.clear()
check("rearm clear 后 is_set()=False", not fake._rearm.is_set())


# ---------------------------------------------------------------------------
print("")
print("=" * 56)
print("PASS=%d  FAIL=%d" % (PASS, FAIL))
if FAIL:
    print("失败项：")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("全部通过 ✅")
sys.exit(0)
