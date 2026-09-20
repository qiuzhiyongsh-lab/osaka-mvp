# -*- coding: utf-8 -*-
"""本地存储：一个 SQLite 文件 + 落盘目录。所有数据都留在你自己机器上。

五张表：
  properties  房源主档（一条房源一行，重复抓到就更新）
  snapshots   快照（每抓一次追加一行，用于识别降价/变更，永不覆盖）
  changes     变更流水（新盘 / 降价 / 变更，带幅度与时间）
  runs        运行日志（每轮成功失败、抓到多少条）
  watermark   水位线（记住上轮抓到的"最新一条"，用于增量）
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from typing import Any, Iterable

from . import catalog
from . import wareki as wareki_mod

# 主档字段（也是导出的 18+ 列）
PROPERTY_COLUMNS = [
    "property_no", "building_name", "property_subtype", "kind", "ward",
    "address", "line_station", "price", "previous_price",
    "land_area", "exclusive_area", "building_area",
    "unit_price_sqm", "unit_price_tsubo",
    "built_year_month", "layout", "floor", "above_ground_floors",
    "image_count", "pdf_path", "pdf_url", "source_url",
    # v1.5.3：REINS 列表行右侧三个图标（画/図/所）—— 1=有、0=没有。
    # 【为什么必须有这三列】原来看「有没有照片」只能等详情页，列表阶段的房源
    # image_count 只能是 0 → 界面上全显示「画像 0 枚」，把"还没抓详情"误报成"没有照片"。
    # 这三个标记在**列表页就能读到**（div.p-icon-type-ga / -zu / -sho），永远准确。
    "has_photo", "has_floorplan", "has_map",
    "registration_date", "change_date",
    # v1.5.4：**平台日期口径**（与"我方下载日"彻底分开）。
    # reg_date_iso / chg_date_iso 只存「确认来自 REINS 平台」且能解到日的 ISO 日期
    # （YYYY-MM-DD）；解不出来的一律 NULL（＝未知，绝不猜、绝不用下载日冒充）。
    "reg_date_iso", "chg_date_iso",
    "first_seen_at", "last_seen_at", "last_changed_at", "is_active", "detail_json",
    "detail_href",   # v1.9.1：列表行详情直链；供「解耦阶段B」后台补详情复用（免重搜、绝不猜 URL）
]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# R20：建筑年份（築年月）在库里是和暦文本（如「令和4年3月」），排序需转成可比较的整数。
# 返回 年*100 + 月（月份缺失按 0）；无法解析返回 None（→ NULLS LAST）。
def _built_sort_key(s):
    if not s:
        return None
    s = str(s)
    m = re.search(r"(令和|平成|昭和)\s*(\d+)\s*年(?:\s*(\d+)\s*月)?", s)
    if m:
        base = {"令和": 2018, "平成": 1988, "昭和": 1925}[m.group(1)]
        y = base + int(m.group(2))
        return y * 100 + (int(m.group(3)) if m.group(3) else 0)
    m2 = re.search(r"(\d{4})\s*年(?:\s*(\d{1,2})\s*月)?", s)
    if m2:
        return int(m2.group(1)) * 100 + (int(m2.group(2)) if m2.group(2) else 0)
    m3 = re.match(r"(\d{4})", s)
    if m3:
        return int(m3.group(1)) * 100
    return None


# R20：9 个排序键 → SQL 表达式（服务端 SQL 排序；空值排最后；番号作稳定次级键）。
_SORT_COLS = {
    "updated":    "COALESCE(last_seen_at, first_seen_at)",
    "price":      "price",
    "area":       "COALESCE(exclusive_area, land_area, building_area)",
    "unit_sqm":   "unit_price_sqm",
    "unit_tsubo": "unit_price_tsubo",
    "drop_amt":   "(price - previous_price)",
    "drop_pct":   "CASE WHEN previous_price IS NOT NULL AND previous_price > 0 "
                  "THEN (previous_price - price) * 1.0 / previous_price ELSE NULL END",
    "drop_date":  "(SELECT MAX(detected_at) FROM changes WHERE property_no = properties.property_no "
                  "AND change_type = 'price_down')",
    "built":      "built_sort_key(built_year_month)",
    "bukken":     "property_no",
}




def _coerce_int(v):
    """把价格/面积等字段统一转成 int（兼容 None / 布尔 / 带逗号空格的字符串）。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace(" ", "")
        return int(s) if s.lstrip("-").isdigit() else None
    return None


# ---- v1.5.4：平台日期口径（用户拍板：「日期＝平台新建/变更日，不是下载日」）----
_ERA = re.compile(r"(令和|平成|昭和|大正|明治)\s*\d+\s*年")
_NUM8 = re.compile(r"^\d{8}$")          # 20260914 —— pipeline 伪造的「下载日」
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_platform_date(v) -> bool:
    """这个值是不是**真的来自 REINS 平台**的日期？

    【为什么必须显式校验·血泪】`wareki.to_ad()` 对"没有和暦的字符串"是**原样返回**的，
    于是两种脏数据会一路穿过去：
      · `20260914` —— pipeline 曾用 `today_iso` 伪造「登録年月日」（本机实测 1019/1103 行），
        它其实是**下载日**，拿它当平台日期正是用户报的 bug 本身；
      · `指定なし(全期間)\\n３日以内\\n…当日` —— 日期下拉框的整段选项文案被误存进字段
        （本机实测 4 行）。
    所以这里先定性，再交给 to_ad 转换：只有"和暦"或"已是 YYYY-MM-DD"才算平台日期。
    """
    if v is None:
        return False
    s = str(v).strip()
    if not s:
        return False
    if _NUM8.match(s):          # 纯 8 位数字 = 我方伪造的下载日，不是平台日期
        return False
    if _ERA.search(s):          # 和暦（令和/平成/昭和…）
        return True
    return bool(_ISO_DAY.match(s[:10]))


def _is_junk_date(v) -> bool:
    """**可证伪**的脏日期（用于清库，宁可不清也不误清）。

    只认两种当场能证明不是日期的东西：
      · 纯 8 位数字 `20260914` —— pipeline 伪造的下载日；
      · 日期下拉框整段选项文案（含「指定なし」「以内」「当日」等）。
    其它看不懂的格式（如 `2026/09/14`）一律**不清**——只是不参与筛选，留着给人看。
    """
    if v is None:
        return False
    s = str(v).strip()
    if not s:
        return False
    if _NUM8.match(s):
        return True
    return ("指定なし" in s) or ("以内" in s and "当日" in s)


def _has_text(v) -> bool:
    """值里有没有**实际文本**（None / 空串 / 纯空白 都算"没有"）。"""
    return bool(str(v).strip()) if v is not None else False


def _to_iso_day(v) -> str | None:
    """平台日期 → 'YYYY-MM-DD'；不是平台日期 / 解不到"日" → None（绝不猜）。"""
    if not _is_platform_date(v):
        return None
    iso = wareki_mod.to_ad(str(v).strip(), fallback="")
    return iso[:10] if _ISO_DAY.match(iso[:10]) else None


def _backfill_event_day(row) -> str:
    """存量回填时给变更记录挑一个「事件日」（写进 changes.detected_at）。

    【为什么不能直接用 now()·非显然】回填本身是"今天"做的，但被回填的对象是**历史**改价。
    若写 now()，重启当天概览会把几十条陈年平台改价算成"今日变更 / 今日降价"，
    用户看到的当日数字立刻失真（本机实测 79 条会被灌进来）。

    优先级：
      1) 该房源在我方的 `first_seen_at` —— 我们第一次认识它、也是它出现在「按天页」的那天。
         这样"按天列表里有没有这套房"和"当天新增/变更统计"永远自洽。
      2) REINS「変更年月日」转公元年（first_seen_at 缺失时兜底，语义上最接近事件日）。
      3) 都没有 → 现在。
    """
    fs = str(row["first_seen_at"] or "").strip()
    if fs:
        return fs
    iso = wareki_mod.to_ad(row["change_date"], fallback="")
    if len(iso) == 10:
        return iso + " 00:00:00"
    if len(iso) == 7:
        return iso + "-01 00:00:00"
    if len(iso) == 4:
        return iso + "-01-01 00:00:00"
    return now()


SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
  property_no        TEXT PRIMARY KEY,
  building_name      TEXT,
  property_subtype   TEXT,
  kind               TEXT,
  ward               TEXT,
  address            TEXT,
  line_station       TEXT,
  price              INTEGER,
  previous_price     INTEGER,
  land_area          REAL,
  exclusive_area     REAL,
  building_area      REAL,
  unit_price_sqm     INTEGER,
  unit_price_tsubo   INTEGER,
  built_year_month   TEXT,
  layout             TEXT,
  floor              TEXT,
  above_ground_floors TEXT,
  image_count        INTEGER DEFAULT 0,   -- ⚠ v1.5.3 起：-1 = 未知（还没抓到详情），>=0 = 真实张数
  has_photo          INTEGER DEFAULT 0,   -- v1.5.3：列表行「画」图标（有照片）
  has_floorplan      INTEGER DEFAULT 0,   -- v1.5.3：列表行「図」图标（有間取図/図面）
  has_map            INTEGER DEFAULT 0,   -- v1.5.3：列表行「所」图标（有所在図/周辺地図）
  pdf_path           TEXT,
  source_url         TEXT,
  registration_date  TEXT,
  change_date        TEXT,
  first_seen_at      TEXT,
  last_seen_at       TEXT,
  last_changed_at    TEXT,
  is_active          INTEGER DEFAULT 1,
  detail_json        TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_first_seen ON properties(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_prop_ward       ON properties(ward);
CREATE INDEX IF NOT EXISTS idx_prop_subtype    ON properties(property_subtype);
CREATE INDEX IF NOT EXISTS idx_prop_price      ON properties(price);

CREATE TABLE IF NOT EXISTS snapshots (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  property_no  TEXT NOT NULL,
  price        INTEGER,
  status       TEXT,
  content_hash TEXT,
  captured_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_no ON snapshots(property_no, id DESC);

CREATE TABLE IF NOT EXISTS changes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  property_no  TEXT NOT NULL,
  change_type  TEXT NOT NULL,      -- new / price_down / price_up / modified
  field        TEXT,
  old_value    TEXT,
  new_value    TEXT,
  detected_at  TEXT NOT NULL,
  run_id       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chg_time ON changes(detected_at);
CREATE INDEX IF NOT EXISTS idx_chg_no   ON changes(property_no);

CREATE TABLE IF NOT EXISTS runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger      TEXT,               -- manual / interval / random / startup
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  scanned      INTEGER DEFAULT 0,
  fetched      INTEGER DEFAULT 0,
  new_count    INTEGER DEFAULT 0,
  change_count INTEGER DEFAULT 0,
  online_total INTEGER DEFAULT 0,   -- v1.4.0：列表层分母（REINS 检索页该组总数）
  pdf_saved    INTEGER DEFAULT 0,   -- v1.4.0：PDF 层分子（已落盘 PDF 数）
  status       TEXT,               -- running / ok / error / interrupted
  message      TEXT
);

CREATE TABLE IF NOT EXISTS watermark (
  key          TEXT PRIMARY KEY,
  last_no      TEXT,
  captured_at  TEXT
);

-- 线上覆盖（v1.2.7）：每次检索后把 REINS 报的「結果 N 件」按组落库，
-- 用来回答业务最关心的问题：「我到底下全了没有」。
-- 建表用 IF NOT EXISTS ⇒ 免迁移，老库直接用。
CREATE TABLE IF NOT EXISTS online_stats (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  captured_at  TEXT NOT NULL,
  run_id       INTEGER,
  kind         TEXT,                 -- house / apt / land
  label        TEXT,                 -- 组名，如「売一戸建 × 新築戸建·中古戸建」
  subtypes     TEXT,                 -- 逗号分隔的 REINS 種目原值，如「新築戸建,中古戸建」
  axis         TEXT,                 -- 登録年月日 / 変更年月日（v1.7.0：矩阵按轴分报数）
  online_total INTEGER               -- 线上该组总数
);
CREATE INDEX IF NOT EXISTS idx_online_time ON online_stats(captured_at);

-- 每轮全量快照（v1.4.0）：一轮抓前对全量房源存一份，差异靠本地比对（不回线上查，不碰风控）。
-- 用于「变更差异」功能：取某房源历次快照，对比上一次找差异。
CREATE TABLE IF NOT EXISTS property_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        INTEGER,
  property_no   TEXT NOT NULL,
  price         INTEGER,
  previous_price INTEGER,
  change_date   TEXT,
  has_detail    INTEGER DEFAULT 0,   -- 1 = 该轮已抓到详情(detail_json 非空)
  pdf_path      TEXT,
  captured_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ph_run ON property_history(run_id);
CREATE INDEX IF NOT EXISTS idx_ph_no  ON property_history(property_no, id DESC);

-- 通知（v1.4.0）：手动轮完成等事件写这里，前端轮询展示。
CREATE TABLE IF NOT EXISTS notifications (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT,
  message     TEXT,
  created_at  TEXT NOT NULL,
  is_read     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ntf_time ON notifications(created_at);

-- AI 解读（v1.7.4，PRD 09 需求 4）：把房源 PDF 经 AI 提取出的结构化内容存这里。
-- 按 PRD 08 分层约定这是**派生数据**：独立表、绝不写回 properties 主表；
-- 换模型重跑只覆盖本表，永远不会覆盖人工改过的主数据。
-- 每个物件只保留最新一条（UNIQUE property_no，重跑即覆盖，历史由外部备份兜底）。
CREATE TABLE IF NOT EXISTS ai_extractions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  property_no    TEXT NOT NULL UNIQUE,
  pdf_hash       TEXT,                -- 来源 PDF 指纹（sha256 前 16 位），用于「PDF 变了才重跑」
  model          TEXT,                -- 用的哪个模型（如 workbuddy-agent-vision-v1 / doubao-seed-1.6）
  prompt_version TEXT,                -- 提示词版本，可追溯
  status         TEXT NOT NULL DEFAULT 'ok',   -- ok / failed
  result_json    TEXT,                -- {"title":..., "highlights":[...], "fields":[{label,value}], "notes":...}
  cost_ms        INTEGER,
  error          TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_no ON ai_extractions(property_no);
"""


class Store:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        # v1.5.6：**每个线程一条独立连接**（线程本地存储），从根上避免并发撞车。
        #   以前是全局一条连接 + check_same_thread=False，抓取线程与网页线程同时用会
        #   抛 InterfaceError，接着 fetchone() 返回 None → TypeError（今天一天报了 11 次）。
        #   ⚠ 先试过「加线程锁」，实测并发 1600 次仍报错 74 次 ——
        #   因为锁只包得住 execute()、包不住后面的 fetchone()。改成每线程一条连接才彻底干净。
        self._tls = threading.local()
        _c = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        _c.row_factory = sqlite3.Row
        _c.executescript(SCHEMA)
        _c.commit()
        self._tls.conn = _c
        self.conn.executescript(SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    @property
    def conn(self):
        """本线程的数据库连接（**每条线程一条，互不干扰**）。

        SQLite 的连接不是线程安全的。以前靠 check_same_thread=False 硬共享一条，
        抓取线程和网页线程就会互相打断（InterfaceError / fetchone() 返回 None）。
        这里改成线程本地连接：同一线程内复用，不同线程各用各的，互不影响。
        """
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            # R20：每个线程本地连接都要注册排序用 UDF（wareki→公元月序），
            # 否则多线程 Web 下只有首个线程连上有、其余连接报 no such function。
            self._register_udfs(conn)
            self._tls.conn = conn
        return conn

    @staticmethod
    def _register_udfs(conn):
        """在一条连接上注册服务端排序用的 UDF（线程本地连接各注册一次）。"""
        try:
            conn.create_function("built_sort_key", 1, _built_sort_key)
        except Exception:
            pass

    # ---------------- 轻量迁移（老库补列，幂等） ----------------
    # 原则：只加列、不改名、不删列 —— 老库直接可用，不需要手工迁移。
    _ADD_COLUMNS = {
        "properties": {
            "pdf_url": "TEXT",          # v1.3.0：PDF 直链，用于"只补 PDF 不重抓详情"
            # v1.5.3：REINS 列表行三个图标（画/図/所）→ 1=有、0=没有。
            # 初次给老库补列时顺带把无意义的 image_count=0 改成 -1（未知），
            # 否则界面会把"还没抓详情"误显示成"没有照片"。
            "has_photo": "INTEGER DEFAULT 0",
            "has_floorplan": "INTEGER DEFAULT 0",
            "has_map": "INTEGER DEFAULT 0",
            # v1.5.4：平台日期口径（只存确认来自平台、能解到日的 ISO 日期）
            "reg_date_iso": "TEXT",
            "chg_date_iso": "TEXT",
            # v1.5.6：「平台日期已核验」标记。允许缺平台日期的老房源补抓**一次**详情，
            #   抓完置 1，以后不再重复抓（避免每轮都白抓一遍）。
            "pdate_checked": "INTEGER DEFAULT 0",
            # v1.9.1：列表行详情直链（绝对 URL）。sync_today_dates 落库，
            #   供「解耦阶段B」独立补详情复用，避免为拿直链重搜 REINS。
            "detail_href": "TEXT",
        },
        "runs": {
            "online_total": "INTEGER DEFAULT 0",  # v1.4.0：列表层分母
            "pdf_saved": "INTEGER DEFAULT 0",     # v1.4.0：PDF 层分子
        },
    }

    def _ensure_columns(self) -> None:
        for table, cols in self._ADD_COLUMNS.items():
            try:
                have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            except Exception:
                continue
            for name, decl in cols.items():
                if name not in have:
                    try:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                        if name == "has_photo":
                            # 一次性迁移（只在刚补出 has_photo 列时触发）：
                            # 老库的 image_count 全是 0，但 0 在当时并不代表"没有照片"，
                            # 而是"旧代码用 img 计数、REINS 页面里根本没有 <img>，永远 0"。
                            # 统一改成 -1 = 未知，界面上不再显示"画像 0 枚"。
                            self.conn.execute(
                                "UPDATE properties SET image_count=-1 "
                                "WHERE COALESCE(image_count,0)=0")
                    except Exception:
                        pass

    def set_pdf(self, property_no: str, pdf_path: str) -> None:
        """只更新 PDF 落盘路径（v1.3.0：后台 worker 下完 PDF 后由主线程回写）。

        单独一个方法的原因：PDF 是**后台**下载的，主档可能早就 upsert 过了，
        不能为了记一个路径把整行重写一遍（会覆盖掉更新的字段）。
        """
        if not property_no or not pdf_path:
            return
        self.conn.execute("UPDATE properties SET pdf_path=? WHERE property_no=?",
                          (str(pdf_path), property_no))
        self.conn.commit()

    # ---------------- 普通读写 ----------------
    def get_property(self, property_no: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM properties WHERE property_no=?", (property_no,)
        ).fetchone()

    def get_many(self, nos: list[str]) -> dict:
        """按物件番号批量取（给「房源对比」用）。返回 {番号: row}；查不到的键不存在。"""
        nos = [n for n in (nos or []) if n]
        if not nos:
            return {}
        ph = ",".join("?" * len(nos))
        rows = self.conn.execute(
            f"SELECT * FROM properties WHERE property_no IN ({ph})", nos
        ).fetchall()
        return {r["property_no"]: r for r in rows}

    def upsert_property(self, rec: dict) -> None:
        rec = {k: v for k, v in rec.items() if k in PROPERTY_COLUMNS}
        if "property_no" not in rec:
            raise ValueError("upsert_property 需要 property_no")
        existing = self.get_property(rec["property_no"])
        # v1.9.42（勇哥反馈）：有落盘 PDF ⇒ has_floorplan 恒为 1（PDF 就是図面 PDF）。
        #   v1.8.3 起 PDF 下载已移除「列表図标」门禁（crawler L1681）→ 会出现
        #   「有 PDF 但列表没有図标 → has_floorplan=0」→ 查询页「図」图标灰掉。
        #   这里在落库处兜底，且**防回归**：库里已有该房源 PDF 时绝不把它打回 0。
        if (rec.get("pdf_path") or (existing and dict(existing).get("pdf_path"))) \
                and not rec.get("has_floorplan"):
            rec["has_floorplan"] = 1
        # v1.5.4：只要 rec 里带了平台日期原文，就同步算出 ISO 列（便于按平台日期筛选）。
        # 原文不是平台日期（伪造下载日 / 下拉框文案）→ 对应 ISO 列写 NULL，绝不拿脏值凑数。
        if "registration_date" in rec:
            rec["reg_date_iso"] = _to_iso_day(rec["registration_date"])
        if "change_date" in rec:
            rec["chg_date_iso"] = _to_iso_day(rec["change_date"])
        if existing is None:
            rec.setdefault("first_seen_at", now())
            rec.setdefault("last_seen_at", now())
            rec.setdefault("is_active", 1)
            cols = list(rec.keys())
            ph = ",".join("?" * len(cols))
            self.conn.execute(
                f"INSERT INTO properties ({','.join(cols)}) VALUES ({ph})",
                [rec[c] for c in cols],
            )
        else:
            rec.setdefault("last_seen_at", now())
            # ⚠ first_seen_at 是"我方第一次见到这套房"的时间，只在 INSERT 时定下来。
            # 旧代码把它也并进 UPDATE 的 SET 里（且 pipeline 每轮都 setdefault 成 now()），
            # 于是每抓一次就被改写成"最后一次下载时间"—— 本机实测 1103/1103 行
            # first_seen_at 全等于 last_seen_at，"按日期查"自然一天都查不到历史。
            # 这里从 SET 里排除，作为最后一道防线（pipeline 侧也已不再传它）。
            cols = [c for c in rec if c != "property_no" and c != "first_seen_at"]
            sets = ",".join(f"{c}=?" for c in cols)
            vals = [rec[c] for c in cols]
            self.conn.execute(
                f"UPDATE properties SET {sets} WHERE property_no=?", vals + [rec["property_no"]]
            )
        self.conn.commit()

    def upsert_many(self, rows, log=None) -> int:
        """批量写入房源（**单事务**，只在最后 commit 一次）。

        给「线上发布包」用：云端容器每次重建都是空库，启动时要把 `site_data.json`
        里的上千条房源灌回去。逐条 `upsert_property` 每条都 commit 一次（每次落盘），
        实测会让服务 60 秒内起不来 → 被部署平台判定"启动失败"。
        这里改成：先一次查出已有番号，再按列集合分组 executemany，最后一次提交。

        语义与 `upsert_property` 完全一致（含 first_seen_at 只在 INSERT 时定、平台日期
        ISO 换算），只是不逐条落盘。
        """
        if log is None:
            log = lambda s: None                                  # noqa: E731
        if not rows:
            return 0
        have = {r[0] for r in self.conn.execute("SELECT property_no FROM properties")}
        inserts: dict[tuple, list] = {}          # 列元组 -> [值元组, ...]
        updates: dict[tuple, list] = {}
        now_str = now()
        skipped = 0
        for raw in rows:
            if not isinstance(raw, dict) or not raw.get("property_no"):
                skipped += 1
                continue
            rec = {k: v for k, v in raw.items() if k in PROPERTY_COLUMNS}
            # v1.9.42：与 upsert_property 同一不变式（有 PDF ⇒ 図面=1）。
            if rec.get("pdf_path") and not rec.get("has_floorplan"):
                rec["has_floorplan"] = 1
            if "registration_date" in rec:
                rec["reg_date_iso"] = _to_iso_day(rec["registration_date"])
            if "change_date" in rec:
                rec["chg_date_iso"] = _to_iso_day(rec["change_date"])
            rec.setdefault("last_seen_at", now_str)
            if rec["property_no"] in have:
                cols = tuple(sorted(c for c in rec
                                    if c not in ("property_no", "first_seen_at")))
                updates.setdefault(cols, []).append(
                    tuple(rec[c] for c in cols) + (rec["property_no"],))
            else:
                rec.setdefault("first_seen_at", now_str)
                rec.setdefault("is_active", 1)
                cols = tuple(sorted(rec))
                inserts.setdefault(cols, []).append(tuple(rec[c] for c in cols))
        try:
            for cols, batch in inserts.items():
                ph = ",".join("?" * len(cols))
                self.conn.executemany(
                    f"INSERT OR REPLACE INTO properties ({','.join(cols)}) VALUES ({ph})",
                    batch)
            for cols, batch in updates.items():
                sets = ",".join(f"{c}=?" for c in cols)
                self.conn.executemany(
                    f"UPDATE properties SET {sets} WHERE property_no=?", batch)
            self.conn.commit()
        except Exception as e:                                    # noqa: BLE001
            self.conn.rollback()
            log(f"✗ 批量写入失败（已回滚）：{e}")
            raise
        n = sum(len(b) for b in inserts.values()) + sum(len(b) for b in updates.values())
        log(f"批量写入 {n} 条（新增 {sum(len(b) for b in inserts.values())} / "
            f"更新 {sum(len(b) for b in updates.values())}，跳过 {skipped}）")
        return n

    # ---------------- 快照 / 变更 ----------------
    def add_snapshot(self, property_no: str, price, status: str, content_hash: str) -> None:
        self.conn.execute(
            "INSERT INTO snapshots (property_no,price,status,content_hash,captured_at)"
            " VALUES (?,?,?,?,?)",
            (property_no, price, status, content_hash, now()),
        )
        self.conn.commit()

    def last_snapshot(self, property_no: str):
        return self.conn.execute(
            "SELECT * FROM snapshots WHERE property_no=? ORDER BY id DESC LIMIT 1", (property_no,)
        ).fetchone()

    def snapshots(self, property_no: str, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM snapshots WHERE property_no=? ORDER BY id DESC LIMIT ?",
            (property_no, limit),
        ))

    def add_change(self, property_no, change_type, old_value=None, new_value=None,
                   field=None, run_id=None, detected_at: str | None = None) -> None:
        """写一条变更记录。

        `detected_at` 默认 = 现在（正常抓取：这条信息是我们"今天"拿到的）。
        只有**存量回填**（`backfill_platform_changes`）才显式传入更早的事件日——
        否则 79 条平台历史改价会全部被算进"今日变更"，把概览数字灌成假的。
        """
        self.conn.execute(
            "INSERT INTO changes (property_no,change_type,field,old_value,new_value,"
            "detected_at,run_id) VALUES (?,?,?,?,?,?,?)",
            (property_no, change_type, field,
             None if old_value is None else str(old_value),
             None if new_value is None else str(new_value), detected_at or now(), run_id),
        )
        self.conn.commit()

    def property_changes(self, property_no: str, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM changes WHERE property_no=? ORDER BY id DESC LIMIT ?",
            (property_no, limit),
        ))

    # ---------------- AI 解读（v1.7.4） ----------------
    def get_ai_extraction(self, property_no: str):
        """取某房源最新的 AI 解读记录（没有则 None）。"""
        return self.conn.execute(
            "SELECT * FROM ai_extractions WHERE property_no=?", (property_no,)
        ).fetchone()

    def upsert_ai_extraction(self, property_no: str, result: dict, *,
                             model: str = "", prompt_version: str = "",
                             pdf_hash: str = "", cost_ms: int | None = None) -> None:
        """写入/覆盖某房源的 AI 解读（每个物件只留最新一条）。

        result 必须是可 JSON 序列化的 dict（title/highlights/fields/notes）。
        只写 ai_extractions 派生表，**绝不碰 properties 主表**。
        """
        payload = json.dumps(result, ensure_ascii=False)
        self.conn.execute(
            """
            INSERT INTO ai_extractions
              (property_no, pdf_hash, model, prompt_version, status,
               result_json, cost_ms, created_at, updated_at)
            VALUES (?,?,?,?, 'ok', ?,?,?,?)
            ON CONFLICT(property_no) DO UPDATE SET
              pdf_hash=excluded.pdf_hash,
              model=excluded.model,
              prompt_version=excluded.prompt_version,
              status='ok',
              result_json=excluded.result_json,
              cost_ms=excluded.cost_ms,
              updated_at=excluded.updated_at
            """,
            (property_no, pdf_hash or None, model or None,
             prompt_version or None, payload, cost_ms, now(), now()),
        )
        self.conn.commit()

    def change_types_on(self, date: str) -> dict:
        """某一天各房源的变更类型：{物件番号: {"new"|"price_down"|...}}。

        给「房源查询」列表行打「新增 / 变更」标用——一眼看出这套是当天新入库的、
        还是库中已有但今天发生了变化。空日期直接返回空字典。
        """
        if not date:
            return {}
        out: dict = {}
        for r in self.conn.execute(
            "SELECT property_no, change_type FROM changes WHERE substr(detected_at,1,10)=?",
            (date,),
        ):
            out.setdefault(r["property_no"], set()).add(r["change_type"])
        return out

    def recent_changes(self, limit: int = 100, change_type: str | None = None):
        if change_type:
            return list(self.conn.execute(
                "SELECT * FROM changes WHERE change_type=? ORDER BY id DESC LIMIT ?",
                (change_type, limit),
            ))
        return list(self.conn.execute(
            "SELECT * FROM changes ORDER BY id DESC LIMIT ?", (limit,)))

    def backfill_platform_changes(self) -> int:
        """v1.2.5：把库中已有、被错标'新增'但平台其实改过价（有 previous_price）的房源，
        补写 price_down / price_up 变更记录，使'新增/变更'口径与平台对齐。幂等（已有则不重复写）。

        背景：differ.classify 在首抓时无条件写 new，没看平台的 previous_price / change_date，
        导致'平台早已改价、我方才首抓'的房源被误判'新增'。本函数把存量纠正过来。
        启动即跑一次即可（app.py 在 STORE 初始化后调用），失败也不阻塞服务。
        """
        def _to_int(v):
            if isinstance(v, bool):
                return None
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                s = v.strip().replace(",", "").replace(" ", "")
                return int(s) if s.lstrip("-").isdigit() else None
            return None

        n = 0
        for r in self.conn.execute(
            "SELECT property_no, price, previous_price, change_date, first_seen_at "
            "FROM properties "
            "WHERE previous_price IS NOT NULL AND previous_price != '' AND is_active=1"
        ).fetchall():
            price, prev = _to_int(r["price"]), _to_int(r["previous_price"])
            if price is None or prev is None or prev == price:
                continue
            # 幂等：已有 price_down / price_up 则跳过（避免重复回填）
            if self.conn.execute(
                "SELECT 1 FROM changes WHERE property_no=? AND change_type IN ('price_down','price_up')",
                (r["property_no"],),
            ).fetchone():
                continue
            ctype = "price_down" if prev > price else "price_up"
            self.add_change(r["property_no"], ctype, old_value=prev,
                            new_value=price, field="price",
                            detected_at=_backfill_event_day(r))
            n += 1
        if n:
            self.conn.commit()
        return n

    # ---------------- 运行日志 ----------------
    def start_run(self, trigger: str = "manual") -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (trigger,started_at,status) VALUES (?,?,'running')",
            (trigger, now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, scanned=0, fetched=0, new_count=0,
                   change_count=0, status="ok", message="",
                   online_total=None, pdf_saved=None) -> None:
        sets = ["finished_at=?", "scanned=?", "fetched=?", "new_count=?",
                "change_count=?", "status=?", "message=?"]
        args = [now(), int(scanned or 0), int(fetched or 0),
                int(new_count or 0), int(change_count or 0), status, message]
        if online_total is not None:
            sets.append("online_total=?"); args.append(int(online_total))
        if pdf_saved is not None:
            sets.append("pdf_saved=?"); args.append(int(pdf_saved))
        args.append(run_id)
        self.conn.execute(f"UPDATE runs SET {','.join(sets)} WHERE id=?", args)
        self.conn.commit()

    def recent_runs(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)))

    def current_run(self):
        return self.conn.execute(
            "SELECT * FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()

    def recover_runs(self) -> int:
        """启动自愈（v1.4.0）：把「卡在 running 且没 finished_at」的历史轮次标 interrupted。

        进程被 kill 时 finish_run 没机会跑，runs 行永远 running → 页面显示多个'进行中'。
        服务启动扫一遍补完，避免假'进行中'。返回补完的条数。
        """
        cur = self.conn.execute(
            "UPDATE runs SET status='interrupted', finished_at=COALESCE(finished_at, ?) "
            "WHERE (status='running' OR status IS NULL) AND finished_at IS NULL",
            (now(),))
        self.conn.commit()
        return cur.rowcount

    # ---------------- 水位线 ----------------
    def get_watermark(self, key: str):
        return self.conn.execute("SELECT * FROM watermark WHERE key=?", (key,)).fetchone()

    def set_watermark(self, key: str, last_no: str) -> None:
        self.conn.execute(
            "INSERT INTO watermark (key,last_no,captured_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET last_no=excluded.last_no,"
            "captured_at=excluded.captured_at",
            (key, last_no, now()),
        )
        self.conn.commit()

    # ---------------- 查询 ----------------
    def search(self, f: dict, limit: int = 20, offset: int = 0):
        where, args = ["1=1"], []

        def eq(col, key):
            if f.get(key):
                where.append(f"{col} = ?"); args.append(f[key])

        q = (f.get("q") or "").strip()
        if q:
            # 关键词：模糊匹配（含即命中）。空格分隔多个词时，词之间 AND（都要命中），
            # 词内字段之间 OR（任一字段含该词即可）。覆盖：
            # 地址 / 楼名 / 物件番号 / 駅・沿線 / 間取り / 区 / 種別 / 種目 / 築年月 / 详情全部字段。
            text_cols = ["address", "building_name", "property_no", "line_station",
                         "layout", "ward", "kind", "property_subtype",
                         "built_year_month", "detail_json"]
            for term in q.split():
                ors = " OR ".join(f"COALESCE({c},'') LIKE ?" for c in text_cols)
                where.append(f"({ors})")
                args += [f"%{term}%"] * len(text_cols)
        # v1.7.6：区 支持多选（OR）—— 前端每个选中区重复传 ward 参数，后端收成列表走 IN。
        # 单值的 f["ward"] 也兼容（退回成单元素列表）。
        wards = [str(x).strip() for x in (f.get("wards") or []) if str(x).strip()]
        if not wards and f.get("ward"):
            wards = [str(f["ward"]).strip()]
        if wards:
            where.append("ward IN (" + ",".join("?" * len(wards)) + ")")
            args += wards
        # 物件種目：支持多选（两级目录的叶子 key 列表，互相 OR）。
        # 单个老式原始種目字符串也照样能过（catalog 里会退回精确匹配）。
        subs = [str(s) for s in (f.get("subtypes") or []) if str(s).strip()]
        if not subs and f.get("subtype"):
            subs = [str(f["subtype"])]
        if subs:
            frags, sparams = catalog.build_sql(subs)
            if frags:
                where.append("(" + " OR ".join(frags) + ")")
                args += sparams
        if f.get("kind"):
            where.append("kind = ?"); args.append(f["kind"])
        if f.get("price_min") not in (None, ""):
            where.append("price >= ?"); args.append(int(f["price_min"]))
        if f.get("price_max") not in (None, ""):
            where.append("price <= ?"); args.append(int(f["price_max"]))
        if f.get("area_min") not in (None, ""):
            where.append("COALESCE(exclusive_area, land_area, building_area) >= ?")
            args.append(float(f["area_min"]))
        if f.get("area_max") not in (None, ""):
            where.append("COALESCE(exclusive_area, land_area, building_area) <= ?")
            args.append(float(f["area_max"]))
        if f.get("has_pdf"):
            where.append("pdf_path IS NOT NULL AND pdf_path <> ''")
        # v1.7.0：日期时间段（起~止）—— 与单日期互斥，优先走范围
        # v1.7.4 修两处：① 补 download 档（旧版悄悄退化成 any）② any 改为正确的区间语义
        if f.get("date_from") or f.get("date_to"):
            _cal = (f.get("date_caliber") or "any").lower()
            _df, _dt = f.get("date_from") or "", f.get("date_to") or ""
            if _cal == "registration":
                if _df: where.append("reg_date_iso >= ?"); args.append(_df)
                if _dt: where.append("reg_date_iso <= ?"); args.append(_dt)
            elif _cal == "change":
                if _df: where.append("chg_date_iso >= ?"); args.append(_df)
                if _dt: where.append("chg_date_iso <= ?"); args.append(_dt)
            elif _cal == "download":
                # v1.7.4 补：旧版 range 分支只有 registration/change/any 三档，
                # 选「下载日」时整段被 else 吞掉 → 退化成 any，筛出来的根本不是下载日。
                _c = "substr(COALESCE(last_seen_at, first_seen_at), 1, 10)"
                if _df: where.append(f"{_c} >= ?"); args.append(_df)
                if _dt: where.append(f"{_c} <= ?"); args.append(_dt)
            else:
                # any 的语义 = 「**任一日**落在区间内」（PRD 11 §3.5 不变量 2），即
                #     (reg BETWEEN 起 AND 止) OR (chg BETWEEN 起 AND 止)
                # 旧写法 `(reg>=起 OR chg>=起) AND (reg<=止 OR chg<=止)` 是**跨字段 AND**：
                # 一套房「登録日在区间之后 + 変更日在区间之前」时两个条件各由不同字段满足，
                # 于是**两个日期都不在区间内**却仍被捞进来 → 假阳性。
                # 注：reg/chg 为 NULL 时比较得 NULL（非 True），恰好等价于"该字段无日期"，
                # 与「任一日命中」语义一致，无需额外 COALESCE。
                _rp, _cp = [], []
                if _df:
                    _rp.append("reg_date_iso >= ?")
                    _cp.append("chg_date_iso >= ?")
                if _dt:
                    _rp.append("reg_date_iso <= ?")
                    _cp.append("chg_date_iso <= ?")
                if _rp or _cp:
                    where.append(
                        "((" + " AND ".join(_rp or ["1=1"]) + ") OR ("
                        + " AND ".join(_cp or ["1=1"]) + "))"
                    )
                    # ⚠ 参数必须**按占位符出现顺序**追加：reg 分支全部在前、chg 分支全部在后。
                    # （初版按 [起,起,止,止] 追加，与「占位符= reg起,reg止,chg起,chg止」错位，
                    #   实际筛成了 reg∈[起,起] OR chg∈[止,止] → 869 条只剩 344 条。）
                    if _df: args.append(_df)
                    if _dt: args.append(_dt)
                    if _df: args.append(_df)
                    if _dt: args.append(_dt)
        # v1.7.4 R8：取引態様（売主 / 専任 / 専属 / 代理 / 一般）—— 客户点名要能筛出「哪些是卖主」。
        # 该字段**不在主列**，存在 detail_json 里；且值是多行枚举（如「売主\nオーナーチェンジ」），
        # 故用 LIKE 包含匹配（精确等值会把 103 条「売主+オーナーチェンジ」漏掉）。
        # v1.7.6：取引態様 支持多选（OR）—— 字段在 detail_json，多行枚举，
        # 每个选中值用 LIKE 包含匹配，多值之间 OR（如「売主+オーナーチェンジ」也能命中「売主」）。
        ttypes = [str(x).strip() for x in (f.get("trade_types") or []) if str(x).strip()]
        if not ttypes and f.get("trade_type"):
            ttypes = [str(f["trade_type"]).strip()]
        if ttypes:
            _tt_frags = []
            for _tt in ttypes:
                _tt_frags.append("json_extract(detail_json, '$.trade_type') LIKE ?")
                args.append("%" + _tt + "%")
            where.append("(" + " OR ".join(_tt_frags) + ")")
        if f.get("date"):
            # v1.5.4：日期口径 = **REINS 平台的新建/变更日**，不是我方下载日。
            # 【为什么必须换·用户原话】"我这里面的日期应该是实际在平台中新建或者是变更的日期，
            # 不是实际下载的日期"。旧版按 first_seen_at（且它还被每轮覆盖成最后下载时间）筛，
            # 于是"按 09-13 查"永远只剩当天抓过的那点量，历史一查就空 —— 正是用户报的 bug。
            # caliber: registration=登録年月日 / change=変更年月日 / any=两者任一命中。
            cal = (f.get("date_caliber") or "any").lower()
            if cal == "registration":
                where.append("reg_date_iso = ?"); args.append(f["date"])
            elif cal == "change":
                where.append("chg_date_iso = ?"); args.append(f["date"])
            elif cal == "download":
                # v1.5.5：**下载日**口径——"这个日期我本地下到了哪些房源"。
                # 【为什么必须有这一档】KPI/库概况那行「今天 N 条」是抓取覆盖口径
                # （last_seen_at=今天），而查询页默认按**平台日期**筛。于是一轮刚开始时
                # 会出现「今天 16 条」却「该日期/条件下没有房源」——两条完全不同的口径，
                # 用户看着就像 bug。给他一个能直接对上「今天下载到的」的选项。
                where.append("substr(COALESCE(last_seen_at,first_seen_at),1,10) = ?")
                args.append(f["date"])
            else:
                where.append("(reg_date_iso = ? OR chg_date_iso = ?)")
                args += [f["date"], f["date"]]
        if not f.get("include_inactive"):
            where.append("is_active = 1")

        w = " AND ".join(where)
        total = self.conn.execute(f"SELECT COUNT(*) FROM properties WHERE {w}", args).fetchone()[0]
        # R20：服务端 SQL 排序（9 个键 + 正/倒序）。
        #   · 空值一律排最后（NULLS LAST），避免「无价格的房」跑到正序最前这种怪相；
        #   · 番号（property_no）作稳定次级键，保证同值不跳行、分页/刷新结果可复现；
        #   · drop_* 类字段可能为 NULL（无前价 / 无降价记录）→ 自然排最后，符合直觉。
        #   · 排序 UDF(built_sort_key) 已在每个线程本地连接的工厂里注册（见 conn 属性），
        #     不用在此重复注册，避免多线程下「只有首线程有、其余报 no such function」。
        sort = (f.get("sort") or "updated").strip().lower()
        sdir = (f.get("sort_dir") or "").strip().lower()
        # ⚠ v1.8.6 修「排序方向点了完全没反应」的真根因：
        #   旧代码是 `if f.get("order") == "date_desc": sort, sdir = "updated", "desc"`，
        #   **无条件**把排序键和方向硬覆盖掉。而前端 buildParams 每次都带 order=date_desc
        #   → 用户在界面上选的任何 sort / sort_dir 都被后端强制回 updated/desc，
        #   表现就是「改排序、改方向都没反应」（前后端各有一半责任）。
        #   修法：order 只作为**兜底**——调用方**没有显式给 sort** 时才生效。
        if f.get("order") == "date_desc" and not (f.get("sort") or "").strip():
            sort, sdir = "updated", "desc"
        if sort not in _SORT_COLS:
            sort = "updated"
        if sdir not in ("asc", "desc"):
            sdir = "desc"
        col = _SORT_COLS[sort]
        nulls = f"CASE WHEN ({col}) IS NULL THEN 1 ELSE 0 END"
        order_clause = f"ORDER BY {nulls}, {col} {'DESC' if sdir == 'desc' else 'ASC'}, property_no ASC"
        rows = list(self.conn.execute(
            f"SELECT * FROM properties WHERE {w} {order_clause} LIMIT ? OFFSET ?", args + [limit, offset]))
        return rows, int(total)

    def filter_options(self) -> dict:
        def col(name):
            return [r[0] for r in self.conn.execute(
                f"SELECT DISTINCT {name} FROM properties WHERE {name} IS NOT NULL "
                f"AND {name}<>'' ORDER BY {name}")]
        subtypes = col("property_subtype")
        return {"wards": col("ward"), "subtypes": subtypes, "kinds": col("kind"),
                # 两级目录（一级＝一戸建／公寓／土地，二级＝新築／中古）；
                # 库里归不进目录的種目自动进「其他」组，保证筛得出来。
                "tree": catalog.tree(subtypes)}

    def stats(self) -> dict:
        """概览卡片用的统计（全部按**本地库**算）。

        【当日口径·非显然】"当天"的一套房只归一类，避免同一套房同时算进「新增」和「变更」：
          · 当天有记录、但**没有任何**变更类记录（modified/price_down/price_up）→ 算「新增」
          · 当天有**任何**变更类记录（哪怕它同时也是当天首次入库）→ 算「变更」
        于是 **新增 + 变更 = 当天有变化的房源套数**（today_total），三个数字天然自洽，
        正好回答用户要的"新增多少 / 变更多少 / 合计多少"。

        【为什么按 property_no 去重·非显然】一套房当天可能同时有 modified 与 price_down
        两条流水；按行数统计会重复计数。卡片问的是"几套房"，所以一律 COUNT(DISTINCT)。
        """
        c = self.conn
        today = datetime.now().strftime("%Y-%m-%d")
        CHANGED = "('modified','price_down','price_up')"
        today_total = c.execute(
            "SELECT COUNT(DISTINCT property_no) FROM changes "
            "WHERE substr(detected_at,1,10)=?", (today,)).fetchone()[0]
        today_changed = c.execute(
            "SELECT COUNT(DISTINCT property_no) FROM changes "
            "WHERE substr(detected_at,1,10)=? AND change_type IN " + CHANGED,
            (today,)).fetchone()[0]
        # 今日降价：以 REINS 平台自身标注的「変更年月日」(properties.change_date) 为准，
        # 转公元年后 == 今天，且挂牌价 < 变更前价(previous_price)。
        # 【为什么不用 changes.price_down + detected_at=today·非显然】
        #   price_down 流水是"我方首次抓到该房源"时写的，detected_at 实际≈该房源 first_seen_at。
        #   于是"今天才抓到、但平台上周就降过价"的房源会被算成"今日降价"，数字虚高。
        #   改用平台盖戳的変更年月日，只有"平台今天确凿降价"的房源才算今日降价，
        #   与用户直觉一致，也和按天分布(09-11=61/09-12=31/09-13=40)对得上。
        today_down = 0
        for r in c.execute(
            "SELECT property_no, price, previous_price, change_date FROM properties "
            "WHERE previous_price IS NOT NULL AND previous_price<>'' "
            "AND price IS NOT NULL AND is_active=1"
        ).fetchall():
            p = _coerce_int(r["price"]); prev = _coerce_int(r["previous_price"])
            if p is None or prev is None or not (prev > p):
                continue
            iso = wareki_mod.to_ad(r["change_date"] or "", fallback="")
            if iso[:10] == today:
                today_down += 1
        return {
            "total": c.execute("SELECT COUNT(*) FROM properties").fetchone()[0],
            "today_new": max(today_total - today_changed, 0),
            "today_changed": today_changed,
            "today_total": today_total,
            "today_down": today_down,
            "with_pdf": c.execute(
                "SELECT COUNT(*) FROM properties WHERE pdf_path IS NOT NULL AND pdf_path<>''"
            ).fetchone()[0],
            "last_run": (lambda r: r[0] if r else None)(c.execute(
                "SELECT finished_at FROM runs WHERE status='ok' "
                "ORDER BY id DESC LIMIT 1").fetchone()),
            # v1.8.0 N1 数据新鲜度：库内最新一套房的 last_seen_at（抓取/覆盖口径）。
            # 前端据此显示「数据更新于 X 分钟前」，超 6 小时变黄。
            "data_updated_at": (lambda r: r[0] if r else None)(c.execute(
                "SELECT MAX(last_seen_at) FROM properties "
                "WHERE last_seen_at IS NOT NULL").fetchone()),
        }

    # ---------------- 线上覆盖（v1.2.7，回答「我下全了没有」） ----------------
    def add_online_stat(self, run_id, kind, label, subtypes, online_total,
                         axis: str | None = None) -> None:
        """记录一次「检索后线上总数」。subtypes 可传 list 或逗号分隔字符串。

        v1.7.0：axis = 登録年月日 / 変更年月日（按轴分报数，撑起概览 12 行矩阵）。
        """
        if online_total in (None, ""):
            return
        subs = subtypes if isinstance(subtypes, str) else ",".join(subtypes or [])
        self.conn.execute(
            "INSERT INTO online_stats (captured_at,run_id,kind,label,subtypes,axis,online_total)"
            " VALUES (?,?,?,?,?,?,?)",
            (now(), run_id, kind, label, subs, axis or None, int(online_total)))
        self.conn.commit()

    # ---------------- v1.5.4 平台日期回填 ----------------
    def backfill_platform_dates(self) -> dict:
        """把历史行的「平台日期」口径补算出来，并清掉**可证伪**的脏日期。

        幂等，可反复跑。做三件事：
          1) 逐行把 registration_date / change_date 换算成 reg_date_iso / chg_date_iso；
          2) 清掉被 pipeline 用「下载日」伪造的 8 位数字 registration_date
             （本机实测 1019/1103 行）——它不是平台日期，留着会让详情页显示假的登録日；
          3) 清掉误存进日期字段的「日期下拉框整段选项文案」（本机 4 行）。
        只清能证伪的；和暦 / YYYY-MM-DD 一律保留。
        """
        n_reg_iso = n_chg_iso = n_clear_reg = n_clear_chg = 0
        for r in self.conn.execute(
                "SELECT property_no, registration_date, change_date "
                "FROM properties").fetchall():
            no, reg, chg = r["property_no"], r["registration_date"], r["change_date"]
            reg_iso, chg_iso = _to_iso_day(reg), _to_iso_day(chg)
            # ⚠ v1.5.14 血泪（2026-09-15 17:00，勇哥报"今天只有 65 条，不对"）：
            #   **只在"有原文"时才回算该列**。
            #   日期检索同步（crawler.sync_today_dates）是把「登録年月日=今天」的检索结果
            #   *直接*写进 reg_date_iso 的——这类行**本来就没有详情页原文**。
            #   老逻辑无脑一行 UPDATE 把两列都覆写（无原文 → _to_iso_day(None) → NULL）⇒
            #   每次 8765 启动跑本回填，就把当天几百条平台日期**全洗成 NULL**。
            #   实测：14:47 同步写了 866 条，15:2x 重启 8765 后查询页「日期=今天」又只剩 65 条。
            #   没有原文 ≠ 日期不成立：它是"平台检索条件直接给的权威结论"，绝不能被清掉。
            sets, vals = [], []
            if _has_text(reg):
                sets.append("reg_date_iso=?")
                vals.append(reg_iso)
                n_reg_iso += 1 if reg_iso else 0
            if _has_text(chg):
                sets.append("chg_date_iso=?")
                vals.append(chg_iso)
                n_chg_iso += 1 if chg_iso else 0
            if sets:
                self.conn.execute(
                    "UPDATE properties SET " + ", ".join(sets) + " WHERE property_no=?",
                    vals + [no])
            if _is_junk_date(reg):
                self.conn.execute(
                    "UPDATE properties SET registration_date=NULL WHERE property_no=?", (no,))
                n_clear_reg += 1
            if _is_junk_date(chg):
                self.conn.execute(
                    "UPDATE properties SET change_date=NULL WHERE property_no=?", (no,))
                n_clear_chg += 1
        self.conn.commit()
        return {"reg_iso": n_reg_iso, "chg_iso": n_chg_iso,
                "cleared_registration": n_clear_reg, "cleared_change": n_clear_chg}

    # ---------------- v1.5.0 下架/成交检测 ----------------
    def mark_delisted(self, seen_nos: set, run_id: int, consecutive: int = 2,
                      scope_subtypes=None, complete: bool = True) -> dict:
        """本轮子查询结果并集里消失的房源 → 标 is_active=0（已售/下架）。

        连续 `consecutive` 轮不在结果里才标（避免单次漏抓/REINS 抖动误标）。

        v1.5.4 三处修正 —— 用户报的"昨天数据全没了"根因就在这三处：
          ① **作用域豁免**：只判定"本轮确实抓过的種目"。旧版无脑遍历全部 is_active=1，
             于是配置 update_groups 里没配的種目（如 売土地）永远进不了 seen 并集，
             两轮之后被判下架 —— 纯属结构性误杀（本机 604 行就是这么没的）。
          ② **复活机制**：曾标下架、本轮又出现的房源立刻恢复 is_active=1、absent_runs=0。
             旧版只扫 is_active=1，一旦标死永不复查，平台重新上架也救不回来。
          ③ **只在完整轮次判定**：本轮没覆盖全量種目（试跑 / 指定日期下载 / 中途中断）时
             不做下架判定，只做复活 —— 宁可漏判，绝不误杀。

        返回 {"delisted": n, "revived": n}（旧版返回 int，调用方已同步改）。
        列 `absent_runs` 按需补（ALTER 幂等；已存在则忽略）。
        """
        try:
            self.conn.execute(
                "ALTER TABLE properties ADD COLUMN absent_runs INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        except Exception:
            pass
        seen = set(seen_nos or set())
        # 安全阀：本轮**一条都没抓到** → 几乎可以肯定是 REINS/网络/会话出问题了，
        # 而不是大阪市上千套房源一夜之间全没了。此时绝不做下架判定
        # （否则连续两轮空手而归就能把整库标死，这正是 604 行事故的放大路径）。
        if not seen:
            return {"delisted": 0, "revived": 0}

        # ③ 复活：无论本轮是否完整，只要这套房确实又出现了，就把它救回来。
        revived = 0
        for r in self.conn.execute(
                "SELECT property_no FROM properties WHERE is_active=0").fetchall():
            if r["property_no"] in seen:
                self.conn.execute(
                    "UPDATE properties SET is_active=1, absent_runs=0 WHERE property_no=?",
                    (r["property_no"],))
                revived += 1
        self.conn.commit()
        if not complete:
            return {"delisted": 0, "revived": revived}

        scope = set(scope_subtypes) if scope_subtypes else None
        delisted = 0
        for r in self.conn.execute(
                "SELECT property_no, property_subtype, absent_runs "
                "FROM properties WHERE is_active=1").fetchall():
            no, sub, cur = r["property_no"], r["property_subtype"], r["absent_runs"] or 0
            if no in seen:
                if cur != 0:
                    self.conn.execute(
                        "UPDATE properties SET absent_runs=0 WHERE property_no=?", (no,))
                continue
            # ① 本轮根本没抓这类種目 → 没有"它消失了"的证据，豁免（连 absent_runs 都不动）
            if scope is not None and sub not in scope:
                continue
            nxt = cur + 1
            if nxt >= consecutive:
                self.conn.execute(
                    "UPDATE properties SET is_active=0, absent_runs=? WHERE property_no=?",
                    (nxt, no))
                delisted += 1
            else:
                self.conn.execute(
                    "UPDATE properties SET absent_runs=? WHERE property_no=?", (nxt, no))
        self.conn.commit()
        return {"delisted": delisted, "revived": revived}

    # ---------------- v1.5.2 · 断点记录 + 人工决策窗口 ----------------
    def _ensure_v152_tables(self) -> None:
        """两张新表：`crawl_state`（断点）与 `pending_decisions`（15 秒询问窗口）。

        都走 CREATE TABLE IF NOT EXISTS，老库免迁移、幂等。
        """
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS crawl_state (
                run_id       INTEGER PRIMARY KEY,
                phase        TEXT,
                group_index  INTEGER DEFAULT 0,
                group_label  TEXT,
                axis         TEXT,
                page         INTEGER DEFAULT 1,
                row_index    INTEGER DEFAULT 0,
                property_no  TEXT,
                scanned      INTEGER DEFAULT 0,
                fetched      INTEGER DEFAULT 0,
                need_detail  INTEGER DEFAULT 0,
                need_pdf     INTEGER DEFAULT 0,
                updated_at   TEXT
            )""")
        # v1.5.15：老库补列（幂等；列已存在时 ALTER 抛错被吞）
        for _col in ("need_detail", "need_pdf"):
            try:
                self.conn.execute(
                    "ALTER TABLE crawl_state ADD COLUMN %s INTEGER DEFAULT 0" % _col)
            except Exception:
                pass
        # v1.7.0：补列（幂等）
        try:
            self.conn.execute("ALTER TABLE crawl_state ADD COLUMN target_date TEXT")
        except Exception:
            pass
        try:
            self.conn.execute("ALTER TABLE online_stats ADD COLUMN axis TEXT")
        except Exception:
            pass
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_decisions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id         INTEGER,
                kind           TEXT,
                params         TEXT,
                options        TEXT,
                default_action TEXT,
                timeout_s      INTEGER DEFAULT 15,
                created_at     TEXT,
                action         TEXT,
                decided_at     TEXT,
                by_whom        TEXT
            )""")
        self.conn.commit()

    def save_crawl_state(self, run_id: int, **kw) -> None:
        """记录断点（每房型/每页/每条详情都可调，upsert 同一 run 的单行）。

        【为什么只记到"房型+页"】更细的"第几条"没必要——v1.4 起就是**边抓边落库**
        （flush_every=1），已抓过的房源下轮自动跳过，中断最多丢当前这一条。
        """
        self._ensure_v152_tables()
        cols = ("phase", "group_index", "group_label", "axis",
                "page", "row_index", "property_no", "scanned", "fetched",
                "need_detail", "need_pdf", "target_date")
        sets, vals = [], []
        for k in cols:
            if k in kw:
                sets.append("%s=?" % k)
                vals.append(kw[k])
        if not sets:
            return
        # ⚠ 不能写 `INSERT(run_id,updated_at) ... ON CONFLICT DO UPDATE SET ...`：
        #   DO UPDATE **只在冲突时**执行，首次插入就只会写 run_id/updated_at，
        #   其余列全落 DEFAULT（phase/group_label 会莫名其妙是空）。故显式分两步。
        exists = self.conn.execute(
            "SELECT 1 FROM crawl_state WHERE run_id=?", (run_id,)).fetchone()
        if exists:
            self.conn.execute(
                "UPDATE crawl_state SET %s, updated_at=? WHERE run_id=?"
                % ",".join(sets), vals + [now(), run_id])
        else:
            self.conn.execute(
                "INSERT INTO crawl_state (run_id, %s, updated_at) VALUES (%s)"
                % (",".join(c for c in cols if c in kw), ",".join(["?"] * (len(vals) + 2))),
                [run_id] + vals + [now()])
        self.conn.commit()

    def load_crawl_state(self, run_id: int | None = None) -> dict | None:
        """取断点：给了 run_id 就取那轮，否则取最近更新的一轮。"""
        self._ensure_v152_tables()
        if run_id:
            r = self.conn.execute(
                "SELECT * FROM crawl_state WHERE run_id=?", (run_id,)).fetchone()
        else:
            r = self.conn.execute(
                "SELECT * FROM crawl_state ORDER BY updated_at DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def last_interrupted_run(self) -> dict | None:
        """服务启动自检：上一轮是不是被中断了？（进程被关 / 崩溃）

        v1.5.7 修：**只有「最新一轮」真是中断/在跑，才值得问人**。
        以前是「库里存在任何一条 interrupted 就一直问」——实际踩到的情况：
        run61 被打断 → 之后 run62 / run63 都已经跑完 → 可每次重启仍拿 run61
        弹「从断点继续吗」（今天连着弹了 #2 #3 #4 三次），纯噪音。
        """
        r = self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            return None
        d = dict(r)
        return d if (d.get("status") or "") in ("running", "interrupted") else None

    def add_decision(self, run_id, kind: str, params: dict | None = None,
                     options: list | None = None, default_action: str = "",
                     timeout_s: int = 15) -> int:
        """发起一次「问人」：写一行待决，返回 decision id。"""
        self._ensure_v152_tables()
        cur = self.conn.execute(
            "INSERT INTO pending_decisions (run_id,kind,params,options,"
            "default_action,timeout_s,created_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, kind, json.dumps(params or {}, ensure_ascii=False),
             json.dumps(options or [], ensure_ascii=False),
             default_action, int(timeout_s), now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def decide(self, decision_id: int, action: str, by_whom: str = "user") -> bool:
        """人点了某个按钮 → 落定。已被超时/别人处理过则返回 False（幂等、不覆盖）。"""
        self._ensure_v152_tables()
        cur = self.conn.execute(
            "UPDATE pending_decisions SET action=?, decided_at=?, by_whom=? "
            "WHERE id=? AND action IS NULL",
            (action, now(), by_whom, decision_id))
        self.conn.commit()
        return cur.rowcount > 0

    def decision_result(self, decision_id: int) -> str | None:
        r = self.conn.execute(
            "SELECT action FROM pending_decisions WHERE id=?", (decision_id,)).fetchone()
        return r["action"] if r and r["action"] else None

    def pending_decisions(self, limit: int = 5) -> list[dict]:
        """前端轮询：还没被处理、且没超时的询问。"""
        self._ensure_v152_tables()
        out = []
        for r in self.conn.execute(
                "SELECT * FROM pending_decisions WHERE action IS NULL"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall():
            d = dict(r)
            try:
                d["params"] = json.loads(d.get("params") or "{}")
            except Exception:
                d["params"] = {}
            try:
                d["options"] = json.loads(d.get("options") or "[]")
            except Exception:
                d["options"] = []
            out.append(d)
        return out

    def online_coverage(self, date: str | None = None) -> dict:
        """线上 vs 本机 —— 回答"当前在架的房源，本机抓到了多少"。

        口径（与 docs/PRD/01「R8」一致，v1.4.1 修正）：
        · online      = 当天**每个组最近一次**的「結果 N 件」合计（同组多次只取最新）
        · local_live  = 本机库里**当天仍被看到(last_seen_at=当天)**且種目落在这几组范围内的条数
                        —— 这才是"当前还在架上、且本机已同步"的房源，才是覆盖率的分子
        · local(累计) = 本机库里落在这几组種目范围内的**全部**条数（含已成交/下架、仍留库的历史）
        · pct         = min(local_live, online) / online（强制 ≤100%，表示"当前在架的覆盖率"；
                        超过 100% 是时差/口径假象，已裁掉，真实溢出见 over）
        · pct_raw     = local_live / online（不裁，仅供核对）
        · over        = local_live - online（>0 表示本机在架比线上快照多几条，非真实覆盖溢出）

        >100% 的旧版缺陷已修：旧版用 local(累计) 作分子，会把历次成交下架的存量也算进来，
        导致 200%+ 的荒谬"覆盖"。累计存量仍保留为独立指标「累计入库」(local)。

        返回 {} = 还没有线上数据（老库或没跑过新代码），页面应如实显示"—"而不是 0。
        """
        d = date or datetime.now().strftime("%Y-%m-%d")
        rows = list(self.conn.execute(
            "SELECT o.* FROM online_stats o JOIN ("
            "  SELECT kind,label,MAX(id) mid FROM online_stats"
            "  WHERE substr(captured_at,1,10)=? GROUP BY kind,label) t ON o.id=t.mid"
            " ORDER BY o.kind, o.label", (d,)))
        if not rows:
            return {}
        online = sum(int(r["online_total"] or 0) for r in rows)
        subs = [s for r in rows for s in (r["subtypes"] or "").split(",") if s]
        local = 0
        local_live = 0
        if subs:
            ph = ",".join("?" * len(subs))
            local = self.conn.execute(
                f"SELECT COUNT(*) FROM properties WHERE property_subtype IN ({ph})",
                subs).fetchone()[0]
            local_live = self.conn.execute(
                f"SELECT COUNT(*) FROM properties WHERE property_subtype IN ({ph})"
                f" AND substr(last_seen_at,1,10)=?", subs + [d]).fetchone()[0]
        return {
            "online": online, "local": local, "local_live": local_live,
            "pct": round(min(local_live, online) * 100.0 / online, 1) if online else None,
            "pct_raw": round(local_live * 100.0 / online, 1) if online else None,
            "over": (local_live - online) if (online and local_live > online) else 0,
            "groups": [{"label": r["label"], "online": r["online_total"],
                        "at": r["captured_at"], "subtypes": r["subtypes"]} for r in rows],
            "as_of": max(r["captured_at"] for r in rows),
        }

    # REINS 单次检索的**可浏览上限**：config.max_pages=10 页 × 50 条 = 500（平台硬上限）。
    # 意义：超过 500 的房源在检索结果里**翻不到**（平台只给 10 页），所以那部分缺口
    # 是"平台截断"，我方无责；只有"可翻范围内的缺口"才算我方漏采。
    # 这是 ⑤「缺口归因」能分成 truncated / missed 两类的依据，别再笼统报一个 gap。
    PLATFORM_BROWSE_CAP = 500

    def reconcile(self) -> dict:
        """D5（v1.8.2）概览对账 + **⑤ 三段式（v1.8.5）** —— 把「平台报告总数 / 本机已抓 / 本轮新增变更 / 待补详情」
        归一成一张卡要的六个数，前端直接渲染，不必再拼四个接口。

        口径（与现有方法一致，不新增口径）：
        · online_total = 当天 online_stats 各组 online_total 之和；无线上数据则回退最近一轮 runs.online_total
        · local_live   = online_coverage 的 local_live（当天被看到、種目落在这几组的 is_active 条数）
        · local_total  = online_coverage 的 local（这几组種目全库累计条数）
        · new_count / change_count = 最近一轮 runs 的 new_count / change_count
        · pending_detail = 当天 is_active 且无 detail_json 的套数（detail_progress 的 without_detail）
        · gap = online_total - local_live（>0 = 我方漏采，<0 = 时差/口径假象）
        """
        cov = self.online_coverage() or {}
        runs = self.recent_runs(1)
        today = datetime.now().strftime("%Y-%m-%d")
        dp = self.detail_progress(today)
        dp_all = self.detail_progress()
        run = dict(runs[0]) if runs else {}
        online_total = int(cov.get("online") or run.get("online_total") or 0)
        local_live = int(cov.get("local_live") or 0)
        local_total = int(cov.get("local") or 0)

        # ---- ⑤ 三段式：逐组拆成「平台报告 / 我方扫到 / 缺口」，并把缺口**归因** ----
        # 归因规则（关键，别再笼统说"漏采"）：
        #   平台一次检索最多翻 500 条（config.max_pages=10 × 50），超出部分**物理上翻不到**
        #   → unavoidable = max(0, online - 500)，这部分算「平台截断」，我方无责；
        #   剩下在可翻范围内仍没抓到的 → avoidable = max(0, min(online,500) - local_live)，
        #   这才是真「我方漏采」，需要报警/补抓。
        cap = self.PLATFORM_BROWSE_CAP
        g_rows = []
        for g in (cov.get("groups") or []):
            subs = [s for s in (g.get("subtypes") or "").split(",") if s]
            g_online = int(g.get("online") or 0)
            g_local = 0
            if subs:
                ph = ",".join("?" * len(subs))
                g_local = self.conn.execute(
                    f"SELECT COUNT(*) FROM properties WHERE property_subtype IN ({ph})"
                    f" AND substr(last_seen_at,1,10)=?", subs + [today]).fetchone()[0]
            unavoidable = max(0, g_online - cap)
            avoidable = max(0, min(g_online, cap) - g_local)
            g_gap = max(0, g_online - g_local)
            if g_gap <= 0:
                cause = "ok"            # 无缺口
            elif avoidable > 0:
                cause = "missed"        # 可翻范围内也没抓全 = 真漏采
            else:
                cause = "truncated"     # 缺口全部来自平台 500 截断
            g_rows.append({
                "label": g.get("label"), "at": g.get("at"), "subtypes": subs,
                "online": g_online, "local_live": g_local, "gap": g_gap,
                "unavoidable": unavoidable, "avoidable": avoidable, "cause": cause,
            })
        missed_total = sum(r["avoidable"] for r in g_rows)
        truncated_total = sum(r["unavoidable"] for r in g_rows)

        # 下钻用：最近 5 轮（点开能看到每轮什么时候跑的、抓了多少）
        runs5 = [dict(r) for r in self.recent_runs(5)]

        return {
            "online_total": online_total,
            "local_live": local_live,
            "local_total": local_total,
            "new_count": int(run.get("new_count") or 0),
            "change_count": int(run.get("change_count") or 0),
            "pending_detail": int((dp or {}).get("without_detail") or 0),
            "pending_detail_all": int((dp_all or {}).get("without_detail") or 0),
            "gap": (online_total - local_live) if online_total else 0,
            "as_of": cov.get("as_of"),
            # runs 表的列名是 started_at（没有 captured_at），旧写法恒为 None，这里补回退
            "latest_run_at": run.get("captured_at") or run.get("started_at"),
            # ---- ⑤ 三段式（前端卡片按这三段渲染，每段可点开下钻）----
            "segments": {
                "platform": {
                    "total": online_total, "as_of": cov.get("as_of"), "cap": cap,
                    "rows": [{"label": r["label"], "value": r["online"], "at": r["at"]}
                             for r in g_rows],
                },
                "local": {
                    "live": local_live, "total": local_total,
                    "pending_detail": int((dp or {}).get("without_detail") or 0),
                    "pending_detail_all": int((dp_all or {}).get("without_detail") or 0),
                    "rows": [{"label": r["label"], "value": r["local_live"],
                              "subtypes": r["subtypes"]} for r in g_rows],
                },
                "gap": {
                    "total": (online_total - local_live) if online_total else 0,
                    "missed": missed_total,       # 我方漏采（可翻范围内没抓全）
                    "truncated": truncated_total, # 平台 500 截断（翻不到，非我方责任）
                    "cap": cap,
                    "rows": g_rows,
                },
            },
            "runs": [{"id": r.get("id"), "at": r.get("started_at"),
                      "trigger": r.get("trigger"), "status": r.get("status"),
                      "online_total": r.get("online_total"), "fetched": r.get("fetched"),
                      "new_count": r.get("new_count"), "change_count": r.get("change_count"),
                      "pdf_saved": r.get("pdf_saved"),
                      "message": r.get("message")} for r in runs5],
        }

    def export_rows(self, date: str | None = None) -> Iterable[sqlite3.Row]:
        if date:
            return list(self.conn.execute(
                "SELECT * FROM properties WHERE substr(COALESCE(first_seen_at,last_seen_at),1,10)=?"
                " ORDER BY property_no", (date,)))
        return list(self.conn.execute("SELECT * FROM properties ORDER BY property_no"))

    # ---------------- 运行中轮次的实时进度 ----------------
    def progress_run(self, run_id: int, scanned=0, fetched=0,
                     new_count=0, change_count=0, online_total=None,
                     pdf_saved=None) -> None:
        """把「正在跑」的这一轮的当前进度写进 runs 表（**不动 status / finished_at**）。

        为什么需要：一轮真实抓取可能跑几十分钟甚至更久（模拟人工节奏），
        如果只在整轮结束时才写库，页面和本地库在中途全是空的，
        用户会误判成"下载成功了但一条数据都没有"。边抓边落库 + 这里同步进度，
        页面上就能看到"已落库 N 条"在涨。
        """
        sets = ["scanned=?", "fetched=?", "new_count=?", "change_count=?"]
        args = [int(scanned or 0), int(fetched or 0),
                int(new_count or 0), int(change_count or 0)]
        if online_total is not None:
            sets.append("online_total=?"); args.append(int(online_total))
        if pdf_saved is not None:
            sets.append("pdf_saved=?"); args.append(int(pdf_saved))
        args.append(run_id)
        self.conn.execute(
            f"UPDATE runs SET {','.join(sets)} "
            f"WHERE id=? AND (status='running' OR status IS NULL)",
            args)
        self.conn.commit()

    # ---------------- 库概况（给「今天没数据」的友好提示用） ----------------
    def library_info(self) -> dict:
        """总条数 / 最近一次有数据的日期 / 今天有多少条 / 最近一轮运行的状态与进度。

        查询页拿它判断：是不是"今天确实一条都没有"，好给出可操作的提示，
        而不是甩一个空白列表让用户猜。**正在跑的轮次**也会带上实时进度，
        页面据此提示"抓取进行中，已落库 N 条"，避免被误读成下载失败。
        """
        c = self.conn
        today = datetime.now().strftime("%Y-%m-%d")
        last = c.execute(
            "SELECT trigger,started_at,finished_at,status,message,"
            "scanned,fetched,new_count,change_count "
            "FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        running = c.execute(
            "SELECT id,trigger,started_at,scanned,fetched,new_count,change_count "
            "FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total": c.execute("SELECT COUNT(*) FROM properties").fetchone()[0],
            "latest_date": c.execute(
                "SELECT MAX(substr(COALESCE(last_seen_at,first_seen_at),1,10))"
                " FROM properties").fetchone()[0],
            "today": today,
            # v1.5.5：**必须用 last_seen_at（下载/覆盖口径）**，不能用 first_seen_at。
            #   v1.5.4 修好「first_seen_at 被每轮覆盖」之后，这里的语义被悄悄改了：
            #   老行为下 first_seen_at 每轮都刷成 now()，所以「今天 N 条」= 今天抓到过的量；
            #   修好之后 first_seen_at 只在首次入库时定，于是这个数变成「今天**新入库**的量」，
            #   一套老房源今天改了价（算进「今日更新·变更」）却不被算进来 ——
            #   用户看到的「今天 16 条 vs 今日更新 14 条」这种对不上的时间差就是这么来的。
            #   本标签的用途是判断"今天到底有没有抓到数据"，必须是覆盖口径。
            "today_count": c.execute(
                "SELECT COUNT(*) FROM properties WHERE "
                "substr(COALESCE(last_seen_at,first_seen_at),1,10)=?", (today,)).fetchone()[0],
            "last_run": dict(last) if last else None,
            "running": dict(running) if running else None,
            # v1.8.0 N1 数据新鲜度：库内最新 last_seen_at（抓取/覆盖口径）。
            "data_updated_at": (lambda r: r[0] if r else None)(c.execute(
                "SELECT MAX(last_seen_at) FROM properties "
                "WHERE last_seen_at IS NOT NULL").fetchone()),
        }

    def available_dates(self, limit: int = 90) -> list[dict]:
        """本机库里「有数据的日期 → 条数」倒序列表，供查询页选日期 / 做提示。"""
        rows = self.conn.execute(
            "SELECT substr(COALESCE(first_seen_at,last_seen_at),1,10) d, COUNT(*) n "
            "FROM properties WHERE COALESCE(first_seen_at,last_seen_at) IS NOT NULL "
            "GROUP BY d ORDER BY d DESC LIMIT ?", (limit,)).fetchall()
        return [{"date": r["d"], "count": r["n"]} for r in rows if r["d"]]

    # ---------------- 每轮快照（v1.4.0，本地变更差异用） ----------------
    def snapshot_run(self, run_id: int) -> int:
        """把当前全量房源状态存一份快照，绑定到 run_id。返回快照条数。

        用途：事后在本地算「任意两轮差异」，不用回线上查（省请求、不碰风控）。
        """
        n = self.conn.execute(
            "INSERT INTO property_history (run_id,property_no,price,previous_price,"
            "change_date,has_detail,pdf_path,captured_at) "
            "SELECT ?,property_no,price,previous_price,change_date,"
            "CASE WHEN detail_json IS NOT NULL AND detail_json<>'' THEN 1 ELSE 0 END,"
            "pdf_path,? FROM properties",
            (run_id, now())).rowcount
        self.conn.commit()
        return n

    def prev_run_snapshot(self, run_id: int) -> dict:
        """取「比 run_id 更早的最近一轮」的快照，返回 {property_no: row}。没有则返回空。"""
        row = self.conn.execute(
            "SELECT MAX(run_id) m FROM property_history WHERE run_id < ?", (run_id,)
        ).fetchone()
        if not row or row["m"] is None:
            return {}
        return {r["property_no"]: r for r in self.conn.execute(
            "SELECT * FROM property_history WHERE run_id=?", (row["m"],))}

    def history_for(self, property_no: str, limit: int = 20) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM property_history WHERE property_no=? ORDER BY id DESC LIMIT ?",
            (property_no, limit)))

    # ---------------- 通知（v1.4.0） ----------------
    def add_notification(self, kind: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO notifications (kind,message,created_at) VALUES (?,?,?)",
            (kind, message, now()))
        self.conn.commit()

    def list_notifications(self, limit: int = 20, unread_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM notifications"
        if unread_only:
            sql += " WHERE is_read=0"
        sql += " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self.conn.execute(sql, (limit,)).fetchall()]

    def mark_notifications_read(self, ids=None) -> None:
        if ids:
            ph = ",".join("?" * len(ids))
            self.conn.execute(f"UPDATE notifications SET is_read=1 WHERE id IN ({ph})", list(ids))
        else:
            self.conn.execute("UPDATE notifications SET is_read=1")
        self.conn.commit()

    def unread_notifications(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE is_read=0").fetchone()[0]

    # ---------------- 同步状态（v1.4.0） ----------------
    def sync_status(self) -> dict:
        """REINS 检索页(线上) vs 本机后台 的同步情况（尤其当天）。

        线上 = online_stats 当天每组最近一次 online_total 之和；
        本地 = 本机库里落在这些组種目范围内的累计条数；
        另带当前运行轮的 online_total / scanned / fetched / pdf_saved 实时进度。
        """
        cov = self.online_coverage()
        running = self.conn.execute(
            "SELECT id,online_total,scanned,fetched,pdf_saved,status "
            "FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "online_total": cov.get("online") if cov else None,
            "local_total": cov.get("local") if cov else None,        # 累计入库（历史存量）
            "local_live": cov.get("local_live") if cov else None,    # 当前在架（本机已同步）
            "pct": cov.get("pct") if cov else None,                  # = local_live / online（≤100%）
            "groups": cov.get("groups") if cov else [],
            "as_of": cov.get("as_of") if cov else None,
            "running": dict(running) if running else None,
        }

    def detail_progress(self, date: str | None = None) -> dict:
        """v1.4.0：详情层进度 —— 全库里多少套**已抓到详情**、多少套还只有列表。

        两阶段下载后，列表会先齐、详情慢慢补，所以"已下/未下详情"是用户最想知道的
        第二个进度条（第一个是线上 vs 本地的同步进度）。
        """
        if date:
            where = "is_active=1 AND (reg_date_iso=? OR chg_date_iso=?)"
            params: tuple = (date, date)
            scope = "date:" + date
        else:
            where = "is_active=1"
            params = ()
            scope = "all"
        row = self.conn.execute(
            "SELECT SUM(CASE WHEN detail_json IS NOT NULL AND detail_json<>'' "
            f"THEN 1 ELSE 0 END), COUNT(*) FROM properties WHERE {where}",
            params).fetchone()
        have = int(row[0] or 0)
        total = int(row[1] or 0)
        return {"with_detail": have, "without_detail": total - have, "total": total,
                "pct": round(have * 100.0 / total, 1) if total else None,
                "scope": scope, "date": date}

    def phase_groups(self, run_id: int) -> list[dict]:
        """v1.7.0：概览面板「种目 × 登录/变更」矩阵数据源。

        分母（平台报告数 online）来自 online_stats（按 run_id 去重取每组每轴最新一次）；
        分子（fetched 已下详情 / pdf_saved 已下 PDF）从 properties 按「目标日期 + 轴 + 種目」实算；
        need_detail = max(0, online - fetched)（平台报 N 条、我方已下详情 fetched 条）。

        依赖 run 开始时 crawler 写入 crawl_state.target_date（无则回退 1=1，仅旧库/历史轮）。
        返回按 label → 登録轴在前 排序，前端直接渲染 12 行。
        """
        cs = self.load_crawl_state(run_id)
        target = (cs or {}).get("target_date") if cs else None
        rows = self.conn.execute(
            "SELECT label, subtypes, axis, online_total FROM online_stats "
            "WHERE id IN (SELECT MAX(id) FROM online_stats WHERE run_id=? "
            "GROUP BY label, subtypes, axis)", (run_id,)).fetchall()
        out = []
        for r in rows:
            label = r["label"] or "?"
            subs = [s for s in (r["subtypes"] or "").split(",") if s]
            axis = r["axis"] or ""
            online = int(r["online_total"] or 0)
            if axis == "登録年月日":
                dcol = "reg_date_iso"
            elif axis == "変更年月日":
                dcol = "chg_date_iso"
            else:
                dcol = None
            ph = ",".join("?" * len(subs)) if subs else ""
            sub_clause = (" AND property_subtype IN (%s)" % ph) if subs else ""
            if dcol and target:
                wc = "is_active=1 AND %s=?%s" % (dcol, sub_clause)
                params: tuple = (target,) + tuple(subs)
            elif (not dcol) and target:
                wc = "(reg_date_iso=? OR chg_date_iso=?)%s" % sub_clause
                params = (target, target) + tuple(subs)
            else:
                wc = "1=1%s" % sub_clause
                params = tuple(subs)
            fetched = self.conn.execute(
                "SELECT COUNT(*) FROM properties WHERE %s "
                "AND detail_json IS NOT NULL AND detail_json<>''" % wc, params).fetchone()[0]
            pdf = self.conn.execute(
                "SELECT COUNT(*) FROM properties WHERE %s "
                "AND pdf_path IS NOT NULL AND pdf_path<>''" % wc, params).fetchone()[0]
            out.append({
                "label": label,
                "axis": axis or "—",
                "online": online,
                "fetched": int(fetched),
                "need_detail": max(0, online - int(fetched)),
                "pdf_saved": int(pdf),
            })
        out.sort(key=lambda x: (x["label"], 0 if x["axis"] == "登録年月日" else 1))
        return out

    def pdf_progress(self, date: str | None = None) -> dict:
        """v1.5.13：PDF（図面）层进度 —— 分母只算「列表行有「図」图标」的房源。

        没有図面图标的房源，平台本来就没有図面，硬去详情页点「図面参照」只会失败、
        产生误导日志（"PDF 未取到…本套可能本来就没有図面"）。所以 PDF 命中率的分母
        应当是「有図面图标」的子集，而非全库 —— 否则分母里混进一堆"注定没有"的，
        命中率被无意义地拉低、看着像 bug。
        """
        if date:
            where = "is_active=1 AND has_floorplan=1 AND (reg_date_iso=? OR chg_date_iso=?)"
            params: tuple = (date, date)
            scope = "date:" + date
        else:
            where = "is_active=1 AND has_floorplan=1"
            params = ()
            scope = "has_floorplan"
        row = self.conn.execute(
            "SELECT SUM(CASE WHEN pdf_path IS NOT NULL AND pdf_path<>'' "
            f"THEN 1 ELSE 0 END), COUNT(*) FROM properties WHERE {where}",
            params).fetchone()
        have = int(row[0] or 0)
        total = int(row[1] or 0)
        return {"with_pdf": have, "without_pdf": total - have, "total_floorplan": total,
                "pct": round(have * 100.0 / total, 1) if total else None,
                "scope": scope, "date": date}

    def dump(self, obj) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str)

    def close(self):
        self.conn.close()
