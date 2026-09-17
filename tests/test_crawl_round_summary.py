# -*- coding: utf-8 -*-
"""tests/test_crawl_round_summary.py —— 抓取轮次收尾与「前日补齐」判据的回归门禁

覆盖两个**真实发生过的静默故障**（都不是崩溃报错、而是被宽 except 吞掉/一直不触发）：

  ① R1（2026-09-17 定位）：`run_round` 收尾汇总框引用 `groups` / `run_online_total`
     —— 两者只存在于 `_live_items` 的局部作用域 → **每轮必 NameError**，
     被外层 `except Exception` 吞成「本轮失败」：
       · runs.status 永远 error、message 写着 NameError；
       · 手动更新的「已完成」通知永远不发；
       · 自动上传那段（在它后面）本轮永不执行。
     而抓取结果本身是好的，所以人肉跑一轮**只看到数据正常、看不出状态错了**。
     ⇒ 这里用 demo 模式把同一条收尾路径跑一遍，断言 status=ok + 通知已写。

  ② D2（2026-09-17 新增）：`_prev_day_needs_backfill` 的三条判据必须
     「该补才补、不该补不补」（不补是为了不每天平白多打 12 次 REINS 检索）。

铁律：只用临时库，绝不碰真库、绝不碰 8765 进程（本文件不起浏览器、不联网）。
"""
from __future__ import annotations

import sqlite3

import pytest

from core import config as cfgmod


def _cfg(tmp_path, mode="demo"):
    """最小可用配置：demo 模式 + 关掉导出（不写盘）。"""
    cfg = cfgmod.load()
    cfg.setdefault("app", {})["mode"] = mode
    cfg.setdefault("download", {})["export_excel"] = False
    cfg.setdefault("download", {})["export_daily_page"] = False
    cfg.setdefault("crawl", {})["delist_enabled"] = False
    return cfg


# ---------------------------------------------------------------- ① R1
def test_demo_round_finishes_ok_and_notifies(store, tmp_path):
    """demo 一轮跑完必须 status=ok（旧代码在收尾处 NameError → status=error）。"""
    from core import crawler

    lines: list[str] = []
    stats = crawler.run_round(store, _cfg(tmp_path), trigger="manual",
                              scale=0.01, progress_cb=lines.append)

    assert stats["status"] == "ok", (
        "收尾路径抛异常了（看 errors）：%s" % stats.get("errors"))
    assert not [e for e in stats.get("errors", []) if "NameError" in e], stats["errors"]
    # 收尾汇总框必须真的打出来（说明走完了 summary 那段，而不是中途被吞）
    joined = "\n".join(lines)
    assert "全期間主轮" in joined, "收尾汇总框没打印 → 收尾那段没走完"
    # 手动触发的轮次必须写「已完成」通知（旧代码因 NameError 永远写不到这一步）
    rows = list(store.conn.execute(
        "select kind, message from notifications where kind='round_done' order by id desc limit 1"))
    assert rows, "没有 round_done 通知：收尾没走到通知那一步"
    assert "手动更新完成" in rows[0]["message"]


def test_round_summary_uses_real_group_count(store, tmp_path):
    """汇总框里的「種目组：N 组」必须是真实组数（而不是未定义变量/0）。"""
    from core import crawler

    lines: list[str] = []
    cfg = _cfg(tmp_path)
    crawler.run_round(store, cfg, trigger="manual", scale=0.01, progress_cb=lines.append)
    # demo 路径不跑主轮，组数按 0 如实报（关键：不是 NameError、不是瞎猜一个数）
    hit = [ln for ln in lines if "種目组：" in ln]
    assert hit, "汇总框缺「種目组」行"
    assert "None" not in hit[0]


# ---------------------------------------------------------------- ② D2
def _mk_store(tmp_path):
    from core.store import Store
    return Store(str(tmp_path / "d2.db"))


def _add_run(store, day, hhmm, status="ok"):
    store.start_run("interval")
    rid = store.conn.execute("select max(id) from runs").fetchone()[0]
    store.conn.execute("update runs set status=?, finished_at=? where id=?",
                       (status, "%s %s:00" % (day, hhmm), rid))
    store.conn.commit()
    return rid


def _add_prop(store, no, reg=None, chg=None):
    store.conn.execute(
        "insert into properties (property_no, reg_date_iso, chg_date_iso,"
        " first_seen_at, last_seen_at) values (?,?,?,?,?)",
        (no, reg, chg, "2026-09-17 00:00:00", "2026-09-17 00:00:00"))
    store.conn.commit()


def test_backfill_when_prev_day_empty(store, tmp_path):
    """前一日一条都没有（那天整轮没跑成）→ 必须补。"""
    from core import crawler
    s = _mk_store(tmp_path)
    _add_run(s, "2026-09-16", "21:30")
    hit = crawler._prev_day_needs_backfill(s, {"crawl": {}}, "2026-09-17")
    assert hit and hit[0] == "2026-09-16", hit
    assert "0 条" in hit[1]


def test_backfill_when_prev_day_tail_broken(store, tmp_path):
    """前一日有数据，但最后一轮 15:00 就结束了（尾巴断了）→ 必须补。"""
    from core import crawler
    s = _mk_store(tmp_path)
    _add_prop(s, "A001", reg="2026-09-16")
    _add_run(s, "2026-09-16", "15:00")
    hit = crawler._prev_day_needs_backfill(s, {"crawl": {}}, "2026-09-17")
    assert hit and hit[0] == "2026-09-16", hit
    assert "尾巴" in hit[1]


def test_no_backfill_when_prev_day_complete(store, tmp_path):
    """前一日有数据、最后一轮跑到 21:40（正常）→ **不补**（省 12 次检索）。"""
    from core import crawler
    s = _mk_store(tmp_path)
    _add_prop(s, "A001", reg="2026-09-16")
    _add_run(s, "2026-09-16", "21:40")
    assert crawler._prev_day_needs_backfill(s, {"crawl": {}}, "2026-09-17") is None


def test_no_backfill_when_already_done_today(store, tmp_path):
    """今天已经补过（watermark 记着）→ 不再重复补，即使判据仍成立。"""
    from core import crawler
    s = _mk_store(tmp_path)
    _add_run(s, "2026-09-16", "15:00")       # 判据①成立
    s.set_watermark("date_backfill", "2026-09-16")
    assert crawler._prev_day_needs_backfill(s, {"crawl": {}}, "2026-09-17") is None


def test_failed_runs_do_not_count_as_tail(store, tmp_path):
    """前一日只有**失败**轮次（status=error）→ 视为尾巴断了 → 必须补。"""
    from core import crawler
    s = _mk_store(tmp_path)
    _add_prop(s, "A001", chg="2026-09-16")
    _add_run(s, "2026-09-16", "21:50", status="error")
    hit = crawler._prev_day_needs_backfill(s, {"crawl": {}}, "2026-09-17")
    assert hit and hit[0] == "2026-09-16", hit
    assert "成功轮次" in hit[1]


def test_backfill_respects_explicit_off_switch():
    """sync_dates_backfill_prev_day=false 时 sync_today_dates 不应去查判据（显式关）。"""
    import inspect
    from core import crawler
    sig = inspect.signature(crawler.sync_today_dates)
    assert "backfill_prev" in sig.parameters, "缺少显式开关参数"
    src = inspect.getsource(crawler.sync_today_dates)
    assert "sync_dates_backfill_prev_day" in src, "没读配置开关"
