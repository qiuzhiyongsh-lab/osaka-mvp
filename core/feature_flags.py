# -*- coding: utf-8 -*-
"""Feature Flag 渐进式交付基础设施（v1.9.101）。

两层来源：
  1) DEFAULTS：代码里写死的默认值（新增 flag 时在这里登记 name → (默认开关, 说明)）。
  2) feature_flags 表：运行时覆盖（DB 优先）。运维/管理员用 /api/admin/feature_flags 改写。

读取：get_flag(conn, name) → 先看 DB 覆盖，没有回落 DEFAULTS。
写入：set_flag(conn, name, enabled, note) → upsert 到 DB（未知 flag 拒绝，强制先登记）。
列表：list_flags(conn) → 全量 [{name, enabled, default, description, note, updated_at}]。

设计约束（对齐 osaka 铁律）：
  - 极简、零依赖（只依赖 sqlite3 / 标准库），不引入任何外部包。
  - 读有 5s 进程内缓存，避免每个页面请求都打 DB；set 时立即清对应缓存。
  - 失败安全：DB 不可用时 get_flag 回落默认值，绝不抛错阻断业务。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from typing import Any

# flag 名 → (默认开关, 说明)
DEFAULTS: dict[str, tuple[bool, str]] = {
    "detail_show_reins_search": (
        True,
        "详情页「REINS 物件番号検索」按钮（默认开；关掉=灰度隐藏该营销入口，用于验证 "
        "Feature Flag 运行时开关本身，零业务回归）",
    ),
}

_CACHE: dict[str, tuple[float, bool]] = {}      # name -> (ts, enabled)
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 5.0                                # 秒

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_flags (
  name       TEXT PRIMARY KEY,
  enabled    INTEGER NOT NULL,
  note       TEXT,
  updated_at TEXT NOT NULL
);
"""


def ensure_feature_flags(conn) -> None:
    """幂等建表 + 播种默认值（只插 DB 还没有的，绝不覆盖已有运行值）。"""
    conn.executescript(_SCHEMA)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for name, (default, _desc) in DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO feature_flags(name, enabled, note, updated_at) VALUES(?,?,?,?)",
            (name, 1 if default else 0, "default seed", now),
        )
    conn.commit()


def get_flag(conn, name: str) -> bool:
    """读某个 flag 当前值：DB 覆盖优先，否则回落 DEFAULTS。失败安全。"""
    default = DEFAULTS.get(name, (False, ""))[0]
    with _CACHE_LOCK:
        c = _CACHE.get(name)
        if c and (time.time() - c[0] < _CACHE_TTL):
            return c[1]
    try:
        row = conn.execute("SELECT enabled FROM feature_flags WHERE name=?", (name,)).fetchone()
        val = bool(row["enabled"]) if row else default
    except Exception:                            # noqa: BLE001
        val = default
    with _CACHE_LOCK:
        _CACHE[name] = (time.time(), val)
    return val


def set_flag(conn, name: str, enabled: bool, note: str = "") -> dict[str, Any]:
    """改写某个 flag 的运行值（upsert）。未知 flag 直接拒绝，强制先到 DEFAULTS 登记。"""
    if name not in DEFAULTS:
        raise KeyError(f"未知 flag：{name}（请先在 feature_flags.DEFAULTS 登记）")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO feature_flags(name, enabled, note, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled, "
        "note=excluded.note, updated_at=excluded.updated_at",
        (name, 1 if enabled else 0, note or "", now),
    )
    conn.commit()
    with _CACHE_LOCK:
        _CACHE[name] = (time.time(), bool(enabled))
    return {
        "name": name, "enabled": bool(enabled), "default": DEFAULTS[name][0],
        "note": note, "updated_at": now,
    }


def list_flags(conn) -> list[dict[str, Any]]:
    """全量列出 flag（含默认/说明/运行覆盖），供管理端点与文档使用。"""
    try:
        rows = conn.execute(
            "SELECT name, enabled, note, updated_at FROM feature_flags"
        ).fetchall()
    except Exception:                            # noqa: BLE001
        rows = []
    have = {r["name"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for name, (default, desc) in DEFAULTS.items():
        r = have.get(name)
        out.append({
            "name": name,
            "enabled": bool(r["enabled"]) if r else default,
            "default": default,
            "description": desc,
            "note": (r["note"] if r else "") or "",
            "updated_at": (r["updated_at"] if r else ""),
        })
    return out
