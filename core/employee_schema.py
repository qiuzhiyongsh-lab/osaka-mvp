# -*- coding: utf-8 -*-
"""员工业务库（收藏 / 标签 / 客户）建表与幂等迁移 —— PRD v2.0.1 §3 / §14 步骤 1。

为什么单独一个库（employee.db），不塞进主库 jproperty.db：
  · 主库是「房源数据」，线上由 site_data.json 按 stamp 触发重灌（serve_public）；
    员工业务数据若混在主库，一旦重灌/整表重建就会被冲掉。
  · 独立库 → 与房源同步链路彻底解耦，只受数据盘持久化保护。

铁律（R-4）：线上 data 盘持久化、发版不重置 →
  · 建表一律 CREATE TABLE IF NOT EXISTS
  · 加列一律先 PRAGMA table_info 判断，缺了才 ALTER TABLE ADD COLUMN
  · 禁止 DROP / 重建

本模块**只读配置、不写业务数据**；被 web/app.py 在模块导入时调用一次（幂等）。
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


def _root() -> Path:
    """工程根目录（core/ 的上一级）。"""
    return Path(__file__).resolve().parent.parent


def db_path() -> Path:
    """员工业务库路径。可用环境变量 OSAKA_EMPLOYEE_DB 覆盖（测试用）。"""
    env = (os.environ.get("OSAKA_EMPLOYEE_DB") or "").strip()
    if env:
        return Path(env).resolve()
    return _root() / "data" / "employee.db"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---- 表定义：T1–T6（PRD v2.0.1 §3.2–§3.6）----
# 每条 = (表名, CREATE 语句)。UNIQUE 写在表内，索引单独建。
TABLES: list[tuple[str, str]] = [
    ("favorites", """
        CREATE TABLE IF NOT EXISTS favorites (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT    NOT NULL,
            property_no    TEXT    NOT NULL,
            created_at     TEXT    NOT NULL,
            UNIQUE(owner_username, property_no)
        )
    """),
    ("tags", """
        CREATE TABLE IF NOT EXISTS tags (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT    NOT NULL,
            name           TEXT    NOT NULL,
            color          TEXT    NULL,
            created_at     TEXT    NOT NULL,
            UNIQUE(owner_username, name)
        )
    """),
    ("property_tags", """
        CREATE TABLE IF NOT EXISTS property_tags (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT    NOT NULL,
            property_no    TEXT    NOT NULL,
            tag_id         INTEGER NOT NULL,
            created_at     TEXT    NOT NULL,
            UNIQUE(owner_username, property_no, tag_id)
        )
    """),
    # W3 已定：仅 created_at（登记日期），无 owner_since / 有效期窗口
    ("customers", """
        CREATE TABLE IF NOT EXISTS customers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT    NOT NULL,
            name           TEXT    NOT NULL,
            phone          TEXT    NULL,
            phone_norm     TEXT    NULL,
            note           TEXT    NULL,
            created_at     TEXT    NOT NULL,
            updated_at     TEXT    NOT NULL
        )
    """),
    ("customer_properties", """
        CREATE TABLE IF NOT EXISTS customer_properties (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id  INTEGER NOT NULL,
            property_no  TEXT    NOT NULL,
            intent       INTEGER NULL,
            created_at   TEXT    NOT NULL,
            created_by   TEXT    NOT NULL,
            UNIQUE(customer_id, property_no)
        )
    """),
    ("customer_owner_log", """
        CREATE TABLE IF NOT EXISTS customer_owner_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            from_owner  TEXT    NULL,
            to_owner    TEXT    NOT NULL,
            reason      TEXT    NULL,
            changed_by  TEXT    NOT NULL,
            changed_at  TEXT    NOT NULL
        )
    """),
]

INDEXES: list[tuple[str, str]] = [
    ("idx_fav_owner_time", "CREATE INDEX IF NOT EXISTS idx_fav_owner_time ON favorites(owner_username, created_at DESC)"),
    ("idx_cust_owner_time", "CREATE INDEX IF NOT EXISTS idx_cust_owner_time ON customers(owner_username, created_at DESC)"),
    ("idx_cust_phone_norm", "CREATE INDEX IF NOT EXISTS idx_cust_phone_norm ON customers(phone_norm)"),
]

# ---- 列增补迁移：{表名: [(列名, 类型)]} ----
# 老库缺列时补齐；已存在则跳过（幂等，绝不 DROP）。
#
# v1.9.69（步骤0 接口隔离）补两类列，都是**同步与软删**的刚需：
#   ① `updated_at`：增量同步（/api/emp/export?since=）需要统一水位线。
#      T1/T2/T3/T5 建表时只有 created_at，改标签名/改客户备注这类更新无法被增量捕获
#      → 统一补 updated_at，写入时与 created_at 同值，更新时刷新。
#      ⚠ 不加 NOT NULL/DEFAULT：SQLite 的 ALTER ADD COLUMN 带 NOT NULL 必须有 DEFAULT，
#        历史行会拿到默认值导致水位线错乱；这里让旧行保持 NULL，查询用
#        `COALESCE(updated_at, created_at)` 兜底（见 employee_api._ts_of）。
#   ② `deleted` / `deleted_at`（仅 customers）：R-5 缓解措施——客户删除必须**软删**
#      （CRM 场景"删不掉/删错"都致命），删除只打标记，保留对账轨迹。
COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "favorites": [("updated_at", "TEXT")],
    "tags": [("updated_at", "TEXT")],
    "property_tags": [("updated_at", "TEXT")],
    "customer_properties": [("intent", "INTEGER"), ("created_by", "TEXT"), ("updated_at", "TEXT")],
    "customers": [("note", "TEXT"), ("phone_norm", "TEXT"), ("updated_at", "TEXT"),
                  ("deleted", "INTEGER NOT NULL DEFAULT 0"), ("deleted_at", "TEXT")],
}


def _ensure_columns(cur: sqlite3.Cursor, table: str) -> int:
    """补齐缺失列，返回新增列数。"""
    wanted = COLUMN_MIGRATIONS.get(table) or []
    if not wanted:
        return 0
    cur.execute("PRAGMA table_info(%s)" % table)
    have = {r[1] for r in cur.fetchall()}
    added = 0
    for col, decl in wanted:
        if col not in have:
            cur.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
            added += 1
    return added


def ensure_employee_tables(path: str | os.PathLike | None = None) -> dict:
    """建库建表（幂等）。返回执行摘要，便于启动日志与自检。

    幂等保证：重复调用不会报错、不会重建、不会丢数据。
    """
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=20)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        created = []
        for name, ddl in TABLES:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
            existed = cur.fetchone() is not None
            cur.execute(ddl)
            if not existed:
                created.append(name)
        for _, ddl in INDEXES:
            cur.execute(ddl)
        migrated = {}
        for table in COLUMN_MIGRATIONS:
            n = _ensure_columns(cur, table)
            if n:
                migrated[table] = n
        con.commit()
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                    "AND name IN ('favorites','tags','property_tags','customers',"
                    "'customer_properties','customer_owner_log')")
        return {
            "ok": True,
            "db": str(p),
            "tables_found": cur.fetchone()[0],
            "tables_created": created,
            "columns_added": migrated,
        }
    finally:
        con.close()


if __name__ == "__main__":
    import json
    print(json.dumps(ensure_employee_tables(), ensure_ascii=False, indent=2))
