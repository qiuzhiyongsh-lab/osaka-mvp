# -*- coding: utf-8 -*-
"""员工业务数据回流内核（线上 → 本地）—— PRD v2.0.1 §14 步骤 2。

============================================================================
★ 方向：单向。线上是权威源，本地只拉不推。
============================================================================
β 拓扑（勇哥 2026-09-23 拍板）：员工主 workspace = **线上站**，本地 8765 是
**离线只读副本**。所以这里**只有 pull，没有 push**：

  · 不做反向推送 —— 本地写回线上会引发 §4.5 的双主冲突（后写胜出、删除复活），
    那正是「账号同步四反模式」的血案源头。**单向 = 从根上消掉这类冲突**。
  · 本地写入（离线场景）不保证被保留：全量对账时以线上为准覆盖。
    （PRD §4.2：本地 → 线上仅在离线恢复时发生，彼时由人工走既有通道。）

============================================================================
★ 🚨 跨库自增 id 陷阱（本模块最关键的设计决策）
============================================================================
线上/本地是**两个独立 SQLite 库**，各自的 `INTEGER PRIMARY KEY AUTOINCREMENT`
**不共享 id 空间**：线上 favorites id=5 与本地 id=5 完全可能是两回事。
更致命的是 `property_tags.tag_id` **引用** `tags.id` —— 若按自增 id 对齐，
标签会挂到完全不相干的房源上。

→ **解决：本地表完全采用线上行的 id（整行 INSERT OR REPLACE），不做任何 id 重映射。**
   这样本地 id 空间 == 线上 id 空间，引用关系天然正确，同步逻辑也最简单。
   代价：本地不能独立新增（会撞 id）—— 但 β 下本地本就不写员工数据，代价为零。

============================================================================
★ 两种同步模式
============================================================================
· **增量**（常规）：`GET /api/emp/export?since=<水位线>` → 按 id INSERT OR REPLACE。
· **全量对账**（首拉 / 超过 _FULL_INTERVAL_H / 手动 force_full）：
  灌入线上全集 + **按 id 快照差量删除**。
  为什么需要它：线上删标签是**物理 DELETE**（不是软删），增量同步收不到"删除事件"，
  本地会永久残留孤儿标签 → 靠周期性全量对账兜底清理。

  🔴 **绝不清空表**（v1.9.70 反向挑战修掉）：本地 8765 同样挂着 /api/emp 写接口，
  员工若在本地写入，而本模块**单向只拉不推** → 一旦清表，本地独有行既回不到线上、
  本地也没了，**永久丢失且无告警**。
  → 删除判据用**上一轮的 id 快照**（存在 state.synced_ids）：
    只删「上轮同步过、本轮线上已没有」的行；本地独有行（从未同步过）原样保留。

水位线落 `data/employee_sync_state.json`（不建表：只有一个游标，JSON 足够，
也避免再往 employee.db 里塞非 PRD 定义的表）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

LOG_PREFIX = "[员工同步]"
_FLOCK = threading.Lock()

TABLES = ("favorites", "tags", "property_tags", "customers",
          "customer_properties", "customer_owner_log")

_FULL_INTERVAL_H = 24          # 全量对账最小间隔（小时）
_TIMEOUT = 30                  # HTTP 超时（秒）
_RETRIES = 2

# 🔴 v1.9.70 血案修复：增量查询必须带**回溯窗口**，不能用裸水位线。
#   起因（验证时实测）：水位线 01:35:44，而线上在 01:10 写入的行（时间戳早于水位线）
#   → `WHERE ts > since` 查不到 → **增量永久漏拉**，只能等 24h 全量对账才补上。
#   真实诱因不止一种：①NTP 时钟漂移（两端时钟不一致）②写入提交延迟（事务未落定就被拉）
#   ③批量导入/后台改库用了历史时间戳 ④线上 _now() 早于本地时钟。
#   → 每轮增量把 since 往前回退 5 分钟，配合 INSERT OR REPLACE 幂等覆盖：
#     多拉的一点数据无害，漏拉则要等一整天。
#   ⚠ 水位线本身**不回退**（next_since 仍用线上返回值），否则会累积漂移。
_LOOKBACK_SEC = 300


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def state_path() -> Path:
    return _root() / "data" / "employee_sync_state.json"


def is_public() -> bool:
    return str(os.environ.get("OSAKA_PUBLIC") or "").strip() == "1"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _shift(ts: str | None, seconds: int) -> str | None:
    """把 'YYYY-MM-DD HH:MM:SS' 平移 seconds 秒（回溯窗口用）。失败则原样返回。"""
    if not ts:
        return None
    try:
        return (datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:                                              # noqa: BLE001
        return ts


def load_state() -> dict:
    p = state_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                              # noqa: BLE001
        return {}


def _save_state(st: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.part")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def reset_state() -> dict:
    """清空水位线（下次强制全量对账）。"""
    st = {"last_since": None, "last_sync_at": None, "last_full_at": None,
          "last_result": None, "synced_ids": {}}
    _save_state(st)
    return st


def _conf(cfg: dict | None = None) -> tuple[str, str]:
    """返回 (endpoint, token)。缺任一即视为未配置。"""
    if cfg is None:
        from core import config
        cfg = config.load()
    pub = cfg.get("publish") or {}
    ep = str(pub.get("endpoint") or "").strip().rstrip("/")
    tok = str(pub.get("ingest_token") or pub.get("token") or "").strip()
    return ep, tok


def _pull(endpoint: str, token: str, since: str | None, log) -> dict:
    """拉取线上增量。since 为空 = 全量。"""
    url = endpoint + "/api/emp/export"
    if since:
        url += "?since=" + urllib.parse.quote(since)
    last_err = None
    for i in range(_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "osaka-employee-sync",
                              "X-Publish-Token": token})
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:                                     # noqa: BLE001
            last_err = e
            if i < _RETRIES:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError("拉取失败（%d 次重试后）：%s: %s"
                       % (_RETRIES + 1, type(last_err).__name__, last_err))


def _upsert_rows(cur: sqlite3.Cursor, table: str, rows: list[dict]) -> int:
    """按线上 id 整行写入（**不重映射 id**，见文件头说明）。"""
    if not rows:
        return 0
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    sql = "INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (
        table, ",".join(cols), ",".join("?" * len(cols)))
    n = 0
    for r in rows:
        cur.execute(sql, [r.get(c) for c in cols])
        n += 1
    return n


def sync_once(force_full: bool = False, log=None, cfg: dict | None = None) -> dict:
    """执行一次回流。返回结果摘要（不抛异常，便于调度器吞掉）。

    返回 key：ok / skipped / reason / mode / pulled / applied / since / next_since
    """
    log = log or (lambda m: None)
    if not _FLOCK.acquire(blocking=False):
        return {"ok": False, "skipped": True, "reason": "上一轮尚未结束（并发跳过）"}
    try:
        # 线上是权威源，绝不能自己拉自己（也避免无谓的外网请求）
        if is_public():
            return {"ok": False, "skipped": True, "reason": "线上模式（权威源）不回流"}

        ep, tok = _conf(cfg)
        if not ep or not tok:
            return {"ok": False, "skipped": True,
                    "reason": "未配置 publish.endpoint / ingest_token"}

        from core import employee_schema as es
        es.ensure_employee_tables()                    # 幂等，防本地库缺失

        st = load_state()
        since = None if force_full else (st.get("last_since") or None)
        # 首拉 / 距上次全量超过阈值 → 走全量对账
        need_full = force_full or not st.get("last_full_at") or not since
        if not need_full:
            try:
                last_full = datetime.strptime(st["last_full_at"], "%Y-%m-%d %H:%M:%S")
                need_full = (datetime.now() - last_full).total_seconds() >= _FULL_INTERVAL_H * 3600
            except Exception:                                      # noqa: BLE001
                need_full = True

        # 增量查询带回溯窗口（见 _LOOKBACK_SEC 注释）；水位线本身不回退。
        pull_since = None if need_full else _shift(since, -_LOOKBACK_SEC)
        data = _pull(ep, tok, pull_since, log)
        if not data.get("ok"):
            return {"ok": False, "skipped": False, "reason": "线上返回异常：%s" % str(data)[:200]}

        tables = data.get("tables") or {}

        # ---- 删除计划：只删「上轮同步过、本轮线上已没有」的行 ----
        # 判据必须是**上一轮的 id 快照**，不能是"线上当前没有"（否则本地独有行会被误删）。
        prev_ids = {t: {int(x) for x in (st.get("synced_ids") or {}).get(t, []) if x is not None}
                    for t in TABLES}
        del_plan: dict[str, list[int]] = {}
        new_synced: dict[str, list[int]] = {}
        for t in TABLES:
            cur_ids = {r.get("id") for r in (tables.get(t) or []) if r.get("id") is not None}
            if need_full:
                del_plan[t] = sorted(prev_ids[t] - cur_ids)   # 上轮有、本轮没了 = 线上删了
                new_synced[t] = sorted(cur_ids)
            else:
                del_plan[t] = []                              # 增量不做物理删（靠全量兜底）
                new_synced[t] = sorted(prev_ids[t] | cur_ids)

        con = sqlite3.connect(str(es.db_path()), timeout=30)
        try:
            cur = con.cursor()
            applied = {}
            dropped = {}
            if need_full:
                # 🔴 v1.9.70 反向挑战修掉：全量对账**绝不 `DELETE FROM` 清空表**。
                #   风险（QA 质疑后确认成立）：本地 8765 **同样挂着 /api/emp 写接口**
                #   （v1.9.69 双侧都挂载），员工若在本地写入数据，而本模块是**单向
                #   只拉不推** —— 一旦清表，本地独有行既不会回到线上、本地也没了，
                #   **永久丢失且无告警**。
                #   → 改为**差量删除**：只删"线上已不存在"的 id（清理线上物理删的孤儿），
                #     本地独有行原样保留；线上某表为空时**一律不删**（保守，宁可留孤儿）。
                cur.execute("BEGIN")
                for t in TABLES:
                    to_del = sorted(del_plan.get(t) or ())
                    if to_del:
                        cur.execute("DELETE FROM %s WHERE id IN (%s)"
                                    % (t, ",".join("?" * len(to_del))), to_del)
                        dropped[t] = cur.execute("SELECT changes()").fetchone()[0]
                    else:
                        dropped[t] = 0
                for t in TABLES:
                    applied[t] = _upsert_rows(cur, t, tables.get(t) or [])
                con.commit()
                mode = "full"
            else:
                cur.execute("BEGIN")
                for t in TABLES:
                    applied[t] = _upsert_rows(cur, t, tables.get(t) or [])
                con.commit()
                mode = "inc"
        except Exception:                                          # noqa: BLE001
            con.rollback()
            raise
        finally:
            con.close()

        next_since = data.get("next_since") or _now()
        new_st = {
            "last_since": next_since,
            "last_sync_at": _now(),
            "last_full_at": _now() if need_full else st.get("last_full_at"),
            "synced_ids": new_synced,
            "last_result": {"mode": mode, "applied": applied, "dropped": dropped,
                            "pulled": data.get("counts") or {}},
        }
        _save_state(new_st)
        total = sum(applied.values())
        log("%s %s 完成：%d 行（%s）%s" % (
            LOG_PREFIX, "全量对账" if need_full else "增量", total, applied,
            ("清理孤儿 %s" % dropped) if need_full else ""))
        return {"ok": True, "skipped": False, "mode": mode, "pulled": data.get("counts") or {},
                "applied": applied, "dropped": dropped, "since": since, "next_since": next_since}
    except Exception as e:                                          # noqa: BLE001
        log("%s 失败：%s: %s" % (LOG_PREFIX, type(e).__name__, e))
        return {"ok": False, "skipped": False, "reason": "%s: %s" % (type(e).__name__, e)}
    finally:
        _FLOCK.release()


def status() -> dict:
    """回流状态（供设置页/QA 查看）。"""
    st = load_state()
    ep, tok = _conf()
    return {
        "configured": bool(ep and tok),
        "endpoint": ep,
        "public": is_public(),
        "state": st,
    }
