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

v2.0.x（2026-09-26 勇哥拍板）新增：
  · `tags.category`：'fav'=收藏类（作用于房源）/ 'cust'=客户类（作用于客户），
    两类**完全独立**分栏管理。存量行 category 为 NULL → 应用层统一 COALESCE 为 'fav'
    （勇哥 Q3 拍板：存量标签全部归收藏类）。
    ⚠ 唯一约束仍为表内的 UNIQUE(owner_username, name) —— 即**标签名全局唯一**（跨类也唯一）。
    这是刻意为之：SQLite 的 ALTER TABLE 改不了 UNIQUE，而"禁止 DROP / 重建"是铁律；
    且名称全局唯一可避免"同名两处、删错一个"的混淆。代价＝两类不能有同名标签。
  · `customer_tags`：客户 ⇄ 客户类标签（与 property_tags 平行，作用于客户）。

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
    # v2.0.x：category 进表 + 唯一约束按 (owner, category, name)（两类可同名）
    ("tags", """
        CREATE TABLE IF NOT EXISTS tags (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT    NOT NULL,
            name           TEXT    NOT NULL,
            color          TEXT    NULL,
            category       TEXT    NOT NULL DEFAULT 'fav',
            created_at     TEXT    NOT NULL,
            UNIQUE(owner_username, category, name)
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
    # v2.0.x · 客户 ⇄ 客户类标签（勇哥：标签管理分「收藏类 / 客户类」，完全独立）
    ("customer_tags", """
        CREATE TABLE IF NOT EXISTS customer_tags (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT    NOT NULL,
            customer_id    INTEGER NOT NULL,
            tag_id         INTEGER NOT NULL,
            created_at     TEXT    NOT NULL,
            updated_at     TEXT,
            UNIQUE(owner_username, customer_id, tag_id)
        )
    """),
    # F7（2026-09-27 定稿）：全功能操作日志（收藏/标签/客户关联）—— 谁/何时/做了什么/对象
    #   纯 append-only；弱依赖（写入失败仅记日志，不阻断主业务）。
    ("emp_action_log", """
        CREATE TABLE IF NOT EXISTS emp_action_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_username TEXT    NOT NULL,
            action       TEXT    NOT NULL,
            target_type  TEXT    NOT NULL,
            target_id    TEXT,
            detail_json  TEXT,
            created_at   TEXT    NOT NULL
        )
    """),
]

INDEXES: list[tuple[str, str]] = [
    ("idx_fav_owner_time", "CREATE INDEX IF NOT EXISTS idx_fav_owner_time ON favorites(owner_username, created_at DESC)"),
    ("idx_cust_owner_time", "CREATE INDEX IF NOT EXISTS idx_cust_owner_time ON customers(owner_username, created_at DESC)"),
    ("idx_cust_phone_norm", "CREATE INDEX IF NOT EXISTS idx_cust_phone_norm ON customers(phone_norm)"),
    # v2.0.x：标签分栏 + 下钻（按标签查关联对象）的查询热点
    ("idx_tags_owner_cat", "CREATE INDEX IF NOT EXISTS idx_tags_owner_cat ON tags(owner_username, category)"),
    ("idx_ptag_tag", "CREATE INDEX IF NOT EXISTS idx_ptag_tag ON property_tags(tag_id)"),
    ("idx_ctag_tag", "CREATE INDEX IF NOT EXISTS idx_ctag_tag ON customer_tags(tag_id)"),
    ("idx_emp_action_log_who", "CREATE INDEX IF NOT EXISTS idx_emp_action_log_who ON emp_action_log(emp_username, created_at)"),
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
#
# v2.0.x：`tags.category`（'fav' / 'cust'）。存量为 NULL → 应用层 COALESCE(category,'fav')
#   （勇哥拍板：现有标签都是房源标签，全部归收藏类）。
COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "favorites": [("updated_at", "TEXT")],
    "tags": [("updated_at", "TEXT"), ("category", "TEXT")],
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


def _migrate_tags_category_unique(cur: sqlite3.Cursor) -> bool:
    """把 tags 的唯一约束从 (owner,name) 升级为 (owner,category,name)。

    勇哥 2026-09-26 明确要求「用建新表迁移的方式做」：
      ① 建 `tags_v2`（新约束）
      ② 搬数据 —— **保留 id**（否则 property_tags / customer_tags 的 tag_id 引用会全部错位）
      ③ 旧表 `RENAME TO tags_legacy`（**不 DROP**，数据留底，可人工回滚）
      ④ `tags_v2` `RENAME TO tags`
    幂等：已是新结构 → 返回 False；`tags_legacy` 已存在（迁移过一半）→ 返回 False 交人工判断。
    """
    row = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tags'").fetchone()
    if not row:
        return False
    if "UNIQUE(owner_username, category, name)" in " ".join((row[0] or "").split()):
        return False          # 已是新结构（新库按 TABLES 建表就是新结构）
    if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tags_legacy'").fetchone():
        return False          # 已迁移过一次，不再动
    # 索引会随表改名一起迁到 tags_legacy；先删掉，稍后由 INDEXES 在新表上重建
    cur.execute("DROP INDEX IF EXISTS idx_tags_owner_cat")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tags_v2 (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT    NOT NULL,
            name           TEXT    NOT NULL,
            color          TEXT    NULL,
            category       TEXT    NOT NULL DEFAULT 'fav',
            created_at     TEXT    NOT NULL,
            updated_at     TEXT,
            UNIQUE(owner_username, category, name)
        )
    """)
    cur.execute(
        "INSERT OR IGNORE INTO tags_v2"
        "(id, owner_username, name, color, category, created_at, updated_at)"
        " SELECT id, owner_username, name, color, COALESCE(category,'fav'),"
        "        created_at, updated_at FROM tags")
    cur.execute("ALTER TABLE tags RENAME TO tags_legacy")
    cur.execute("ALTER TABLE tags_v2 RENAME TO tags")
    return True


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
        # ⚠ 顺序铁律：**先补列、再建索引**。
        #    v2.0.x 的 idx_tags_owner_cat 引用的是迁移新增的 tags.category，
        #    若先建索引 → 旧库上直接 "no such column: category"（实测踩到）。
        migrated = {}
        for table in COLUMN_MIGRATIONS:
            n = _ensure_columns(cur, table)
            if n:
                migrated[table] = n
        # v2.0.x：解除「标签名跨类唯一」限制（勇哥 2026-09-26 确认要做）——
        # 旧 UNIQUE(owner,name) → 新 UNIQUE(owner,category,name)；建新表迁移、旧表留底。
        rebuilt = _migrate_tags_category_unique(cur)
        for _, ddl in INDEXES:
            cur.execute(ddl)
        con.commit()
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                    "AND name IN ('favorites','tags','property_tags','customers',"
                    "'customer_properties','customer_owner_log','customer_tags')")
        return {
            "ok": True,
            "db": str(p),
            "tables_found": cur.fetchone()[0],
            "tables_created": created,
            "columns_added": migrated,
            "tags_unique_rebuilt": rebuilt,
        }
    finally:
        con.close()


if __name__ == "__main__":
    import json
    print(json.dumps(ensure_employee_tables(), ensure_ascii=False, indent=2))
