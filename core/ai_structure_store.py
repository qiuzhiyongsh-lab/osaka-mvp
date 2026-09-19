# -*- coding: utf-8 -*-
"""AI 结构独立存储（v1.9.25）—— 与主库 jproperty.db **完全分离** 的第二个库文件。

为什么单独一个文件
------------------
勇哥要求："这个 AI 是和其他数据不直接相关的，单独用一个文件去进行相应的存储"。
所以这里不复用 Store（主库），而是独立 sqlite 文件 `data/ai_pdf_store.db`：

  - 它是**派生/解读层**：来源是 PDF 的 AI 识别结果（Excel 总表），
    与主表 properties 的抓取数据互不覆盖、互不迁移。
  - 删掉它不影响主业务；重跑识别只重写它。
  - 主库 4073 条房源里，目前只有做过 AI 识别的物件才有对应记录。

表设计
------
  ai_field_meta   字段字典（59 列的四语言名 / 分组 / 来源），一次写入、可重跑覆盖
  ai_structure    每个物件一条「AI 结构」：字段值 + 异常等级 + 雷达图 + 结论
  ai_edit_log     编辑审计：将来字段可编辑，每次改动留痕（谁改的、从什么改成什么）

约定
----
  - 所有 JSON 字段用 ensure_ascii=False 写，中文原样入库，不转义。
  - level 异常等级：ok（正常）/ warn（存疑→红字）/ danger（特别存疑→红字黄底）
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

# 独立库文件默认位置：<项目根>/data/ai_pdf_store.db
DEFAULT_DB_NAME = "ai_pdf_store.db"

SCHEMA = """
-- ① 字段字典：59 列的中/繁/英/日 四语言名 + 分组 + 数据来源
CREATE TABLE IF NOT EXISTS ai_field_meta (
  col_name   TEXT PRIMARY KEY,
  seq        INTEGER,                -- 原表列序号 1..59，用于稳定排序
  group_key  TEXT,
  group_cn   TEXT,
  zh         TEXT,
  zh_tw      TEXT,
  en         TEXT,
  ja         TEXT,
  source     TEXT,                   -- 数据库(100%) / PDF视觉补充
  note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_fm_group ON ai_field_meta(group_key);

-- ② AI 结构：每物件一行
CREATE TABLE IF NOT EXISTS ai_structure (
  property_no    TEXT PRIMARY KEY,
  structure_json TEXT NOT NULL,      -- 分组字段（值 + 异常等级 + 存疑原因）
  radar_json     TEXT,               -- 雷达图：各维度分数 + 打分理由
  conclusion     TEXT,               -- 约 200 字的 AI 结论
  anomaly_json   TEXT,               -- 异常清单（汇总，便于列表页/导出用）
  overall        INTEGER,            -- 综合分（雷达图各维度均值）
  edited         INTEGER NOT NULL DEFAULT 0,   -- 0=纯 AI 产出 / 1=人工改过
  source_file    TEXT,
  source_row     INTEGER,
  extracted_at   TEXT,               -- 数据抽取日（Excel 抽出日）
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_as_overall ON ai_structure(overall);

  -- ③ 编辑审计：将来所有字段都可编辑，每次改动留痕
CREATE TABLE IF NOT EXISTS ai_edit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  property_no TEXT NOT NULL,
  col_name    TEXT NOT NULL,
  old_value   TEXT,
  new_value   TEXT,
  editor      TEXT,
  edited_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_el_no ON ai_edit_log(property_no);

-- ④ 每晚 AI 抽取的运行日志（v1.9.26 · PRD 25 R11）
--    独立于主库 runs（那是 REINS 抓取轮次），这里只记「本地/云端抽取」的账，
--    用于统计本地命中率与「当日未完成」队列（D3）。
CREATE TABLE IF NOT EXISTS ai_run_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,
  ended_at      TEXT,
  trigger       TEXT,                 -- nightly / manual / api
  total         INTEGER DEFAULT 0,    -- 本轮计划处理数
  done          INTEGER DEFAULT 0,    -- 实际成功数
  local_hit     INTEGER DEFAULT 0,    -- L1/L2 本地成功数（0 元）
  cloud_used    INTEGER DEFAULT 0,    -- L3 云端调用数（花钱）
  skipped       INTEGER DEFAULT 0,    -- pdf_hash 未变跳过数
  failed        INTEGER DEFAULT 0,    -- 失败数
  pending       INTEGER DEFAULT 0,    -- 本轮结束仍未完成（含超上限顺延）
  note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_arl_start ON ai_run_log(started_at);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def merge_structure(old: dict, new: dict,
                    rejected: dict | None = None) -> tuple[dict, dict]:
    """把「AI 新结果」合并进「旧结构」，**保护人工改过的字段**（v1.9.26 R1）。

    返回 (合并后的 structure, {"kept":[...], "updated":[...], "carried":[...]})
      kept    —— 因 edited=True 原样保留的列名（人工值，AI 不许动）
      updated —— 用了新 AI 值的列名
      carried —— 本轮 AI 没抽出、沿用了旧值的列名（含被闸门驳回→降级的）

    rejected: {列名: 驳回原因} —— 本轮**读到了值但没过质量闸门**的列。
      这些列不能静默沿用旧值当 ok 用（实测：历史库里留着「駐車場=月額」这种
      标签碎片，因为驳回后该列无产出、旧值一直保持 ok）。按勇哥口径：
        原因含「标签」→ 字段与内容完全不相关 → **danger（红字+黄底）**
        其余（金额不合理/长度异常/OCR 存疑）→ **warn（红字）**
      人工改过的字段除外（手工最优先）。

    structure 形状：{"groups": [{"key","cn","fields":[{"col","value","level","edited","note"}]}]}
    两边结构缺失都做防御（不会抛异常）。
    """
    rejected = rejected or {}
    old_g = (old or {}).get("groups") or []
    new_g = (new or {}).get("groups") or []
    old_map: dict[str, dict] = {}
    old_group_of: dict[str, dict] = {}
    for g in old_g:
        for f in (g.get("fields") or []):
            col = f.get("col")
            if col:
                old_map[col] = f
                old_group_of[col] = g

    kept, updated, carried = [], [], []
    merged: list[dict] = []
    seen_cols: set[str] = set()

    for g in new_g:
        fields_out = []
        for f in (g.get("fields") or []):
            col = f.get("col")
            oldf = old_map.get(col) if col else None
            if oldf is not None and oldf.get("edited"):
                # ★ 人工改过：整字段原样保留（值 / level=ok / note）
                fields_out.append(dict(oldf))
                kept.append(col)
            else:
                fields_out.append(dict(f))
                if oldf is not None:
                    updated.append(col)
            if col:
                seen_cols.add(col)
        merged.append({**{k: v for k, v in g.items() if k != "fields"},
                       "fields": fields_out})

    # 旧结构里「本轮没抽出来」的字段：保留下来，**但不是所有列都该降级**
    #   - DB 源列（source 含「数据库」）—— AI 本来就没义务抽，算法 sources 不受影响，保持原样；
    #   - AI 源列——上一轮抽得到、这一轮抽不到，属于不稳定信号，降级 warn + 注明；
    #   - 人工改过的——保持原样（edited 优先级最高）。
    carry_groups: dict[str, list[dict]] = {}
    for col, oldf in old_map.items():
        if col in seen_cols:
            continue
        src = str(oldf.get("source") or "")
        if oldf.get("edited") or ("数据库" in src) or ("100%" in src):
            out_f = dict(oldf)
        elif col in rejected:
            # 本轮读到了值但没过闸门 → 旧值不可信，按勇哥口径降级并写明原因
            reason = str(rejected[col] or "未通过质量闸门")
            out_f = dict(oldf)
            out_f["level"] = "danger" if "标签" in reason else "warn"
            out_f["note"] = f"本轮读取未通过闸门：{reason}（旧值待人工核对）"
        else:
            out_f = dict(oldf)
            out_f["level"] = "warn"
            out_f["note"] = "本轮未重新提取，沿用上次值"
        gkey = (old_group_of[col].get("key") or "") if old_group_of.get(col) else ""
        carry_groups.setdefault(gkey, []).append(out_f)
        carried.append(col)

    if carry_groups:
        key_to_fields = {g.get("key"): g for g in merged}
        for gkey, fields in carry_groups.items():
            tgt = key_to_fields.get(gkey)
            if tgt is not None:
                tgt["fields"].extend(fields)
            else:
                merged.append({"key": gkey or "_legacy",
                               "cn": (old_group_of.get(fields[0].get("col"), {}) or {}).get("cn", "其他"),
                               "fields": fields})

    return {"groups": merged}, {"kept": kept, "updated": updated, "carried": carried}


class AIStructureStore:
    """独立库读写。每线程一条连接（与主库同样的并发经验）。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as con:
            con.executescript(SCHEMA)

    # ---- 连接 ----
    def _conn(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.db_path, timeout=30)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            self._local.con = con
        return con

    # ---- 字段字典 ----
    def replace_field_meta(self, rows: list[dict]):
        """整表覆盖写入字段字典（重跑导入即刷新，保证与 Excel 字段说明一致）。"""
        with self._conn() as con:
            con.execute("DELETE FROM ai_field_meta")
            con.executemany(
                "INSERT INTO ai_field_meta"
                " (col_name,seq,group_key,group_cn,zh,zh_tw,en,ja,source,note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(r["col_name"], r["seq"], r["group_key"], r["group_cn"],
                  r.get("zh", ""), r.get("zh_tw", ""), r.get("en", ""),
                  r.get("ja", ""), r.get("source", ""), r.get("note", ""))
                 for r in rows],
            )

    def field_meta(self) -> dict[str, dict]:
        with self._conn() as con:
            return {r["col_name"]: dict(r)
                    for r in con.execute("SELECT * FROM ai_field_meta")}

    def group_order(self) -> list[tuple[str, str]]:
        """分组展示顺序：(group_key, group_cn)，按组内最小列序号排。"""
        with self._conn() as con:
            rows = con.execute(
                "SELECT group_key, group_cn, MIN(seq) AS ms"
                " FROM ai_field_meta GROUP BY group_key, group_cn ORDER BY ms"
            ).fetchall()
        return [(r["group_key"], r["group_cn"]) for r in rows]

    # ---- AI 结构 ----
    def upsert(self, property_no: str, *, structure: dict, radar: dict | None,
               conclusion: str, anomalies: list, overall: int | None,
               source_file: str = "", source_row: int | None = None,
               extracted_at: str = "", preserve_edited: bool = True,
               rejected: dict | None = None):
        """写/更新一条 AI 结构。**人工改过的字段永不被 AI 覆盖**（v1.9.26 R1）。

        为什么必须逐字段合并（这条是踩过坑的）
        -----------------------------------
        旧实现直接 `structure_json=excluded.structure_json` 整体覆盖——
        那么勇哥在页面上手工校正过的今日価格 / 面積 / 管理費，只要夜里 AI 重跑一次
        就会被冲回 AI 的错值。这与勇哥 2026-09-20 拍板的
        **「将来如果手工改过这个数值，以手工改过的数值为准」** 直接冲突。

        合并口径：
          - 旧字段 `edited=True`  → **整字段原样保留**（值 / level=ok / note），只更新顺序位置；
          - 旧字段未编辑         → 用新值；
          - 新结构里没有、旧结构里有的字段 → 保留旧值（宁可不删），
            未编辑的降级为 warn 并注明「本轮未重新提取」，人工改过的保持不动。
        radar / conclusion 属**解读层**（不是字段值），照常更新；edited 标记保留。

        rejected: {列名: 驳回原因} —— 本轮读到了值但没过质量闸门（详见 merge_structure）。
        preserve_edited=False 仅在「人工确认要全量重来」时用（当前无入口）。
        """
        old_rec = self.get(property_no) if preserve_edited else None
        merged, merged_out = structure, {"kept": [], "updated": [], "carried": []}
        if old_rec is not None:
            merged, merged_out = merge_structure(old_rec["structure"], structure,
                                                 rejected)

        with self._conn() as con:
            cur = con.execute(
                "SELECT property_no, edited FROM ai_structure WHERE property_no=?",
                (property_no,))
            old = cur.fetchone()
            # edited=1 的三个来源：本行原本被人工改过 / 本轮保留了人工字段 / Python 侧标记
            edited = 1 if ((old and old["edited"]) or bool(merged_out["kept"])) else 0
            con.execute(
                "INSERT INTO ai_structure"
                " (property_no,structure_json,radar_json,conclusion,anomaly_json,"
                "  overall,edited,source_file,source_row,extracted_at,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(property_no) DO UPDATE SET"
                "  structure_json=excluded.structure_json,"
                "  radar_json=excluded.radar_json,"
                "  conclusion=excluded.conclusion,"
                "  anomaly_json=excluded.anomaly_json,"
                "  overall=excluded.overall,"
                "  edited=excluded.edited,"
                "  source_file=excluded.source_file,"
                "  source_row=excluded.source_row,"
                "  extracted_at=excluded.extracted_at,"
                "  updated_at=excluded.updated_at",
                (property_no,
                 json.dumps(merged, ensure_ascii=False),
                 json.dumps(radar or {}, ensure_ascii=False),
                 conclusion,
                 json.dumps(anomalies or [], ensure_ascii=False),
                 overall, edited,
                 source_file, source_row, extracted_at,
                 _now(), _now()),
            )
        return merged_out

    def get(self, property_no: str) -> dict | None:
        with self._conn() as con:
            r = con.execute("SELECT * FROM ai_structure WHERE property_no=?",
                            (property_no,)).fetchone()
        if not r:
            return None
        return {
            "property_no": r["property_no"],
            "structure": json.loads(r["structure_json"] or "{}"),
            "radar": json.loads(r["radar_json"] or "{}"),
            "conclusion": r["conclusion"] or "",
            "anomalies": json.loads(r["anomaly_json"] or "[]"),
            "overall": r["overall"],
            "edited": bool(r["edited"]),
            "source_file": r["source_file"],
            "source_row": r["source_row"],
            "extracted_at": r["extracted_at"],
            "updated_at": r["updated_at"],
        }

    def count(self) -> int:
        with self._conn() as con:
            return con.execute("SELECT COUNT(*) c FROM ai_structure").fetchone()["c"]

    # ---- 给定时任务的钩子（v1.9.26 · PRD 25 / D3）----
    def pdf_hash_of(self, property_no: str) -> str:
        """上次入库时记录的 PDF 指纹（没有记录返回 ''）。用于「PDF 没变就不重跑」。"""
        with self._conn() as con:
            r = con.execute("SELECT source_file FROM ai_structure WHERE property_no=?",
                            (property_no,)).fetchone()
        return (r["source_file"] or "") if r else ""

    def pending_property_nos(self, hashes: dict[str, str], limit: int = 10000) -> list[str]:
        """D3「当日未完成」队列：(候选 PDF 集合) − (hash 未变且已入库)。

        hashes —— {property_no: 当前 PDF 的 sha256 前 16 位}
        """
        out = []
        for no, h in (hashes or {}).items():
            old = self.pdf_hash_of(no)
            if old != h:          # 无记录 → ''  ≠ 当前 hash → 也算待办
                out.append(no)
            if len(out) >= limit:
                break
        return out

    def has(self, property_no: str) -> bool:
        return self.get(property_no) is not None

    # ---- 每晚抽取的运行账（R11 / D1 可追溯）----
    def start_run(self, trigger: str = "nightly", total: int = 0) -> int:
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO ai_run_log (started_at,trigger,total) VALUES (?,?,?)",
                (_now(), trigger, total))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict, note: str = ""):
        with self._conn() as con:
            con.execute(
                "UPDATE ai_run_log SET ended_at=?, done=?, local_hit=?, cloud_used=?,"
                " skipped=?, failed=?, pending=?, note=? WHERE id=?",
                (_now(), int(stats.get("done", 0)), int(stats.get("local", 0)),
                 int(stats.get("cloud", 0)), int(stats.get("skip", 0)),
                 int(stats.get("fail", 0)), int(stats.get("pending", 0)),
                 note, run_id))

    def close(self):
        """关闭本线程的连接（**脚本用完必须调**，否则 Windows 上文件被占用删不掉）。

        长驻 Web 服务不需要调用（线程活着就要复用连接）；跑批脚本/单元测试
        建临时库时，不 close 会留下 test.db 句柄，导致 cleanup 报 WinError 32。
        """
        con = getattr(self._local, "con", None)
        if con is not None:
            try:
                con.close()
            except Exception:                                  # noqa: BLE001
                pass
            self._local.con = None

    def existing_property_nos(self) -> set[str]:
        """已有 AI 结构的物件番号集合（历史补跑时用）。"""
        with self._conn() as con:
            return {r[0] for r in con.execute(
                "SELECT property_no FROM ai_structure").fetchall()}

    def recent_runs(self, limit: int = 10) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM ai_run_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # 派生状态：表本身没 status 列，别让每个调用方各写一遍判断
            if not d.get("ended_at"):
                d["status"] = "running"
            elif d.get("failed") or d.get("pending"):
                d["status"] = "partial"
            else:
                d["status"] = "ok"
            out.append(d)
        return out

    # ---- 编辑（将来所有字段都可编辑）----
    def edit_field(self, property_no: str, col_name: str, new_value: str,
                   editor: str = "") -> dict:
        """改单个字段：改 structure_json 里的值 + 记审计 + 标 edited=1。

        人工改过之后，该字段的异常等级自动降为 ok（人已经确认过了），
        但审计日志里保留原值，随时可追溯。
        """
        rec = self.get(property_no)
        if rec is None:
            return {"ok": False, "error": "该房源没有 AI 结构记录"}
        old_value, found = None, False
        for g in rec["structure"].get("groups", []):
            for f in g.get("fields", []):
                if f.get("col") == col_name:
                    old_value = f.get("value")
                    f["value"] = new_value
                    f["level"] = "ok"
                    f["edited"] = True
                    f["note"] = ""
                    found = True
                    break
            if found:
                break
        if not found:
            return {"ok": False, "error": f"字段 {col_name} 不在 AI 结构里"}
        with self._conn() as con:
            con.execute(
                "UPDATE ai_structure SET structure_json=?, edited=1, updated_at=?"
                " WHERE property_no=?",
                (json.dumps(rec["structure"], ensure_ascii=False), _now(), property_no))
            con.execute(
                "INSERT INTO ai_edit_log"
                " (property_no,col_name,old_value,new_value,editor,edited_at)"
                " VALUES (?,?,?,?,?,?)",
                (property_no, col_name, old_value, new_value, editor, _now()))
        return {"ok": True, "old_value": old_value, "new_value": new_value}

    def edit_log(self, property_no: str, limit: int = 50) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM ai_edit_log WHERE property_no=?"
                " ORDER BY id DESC LIMIT ?", (property_no, limit)).fetchall()
        return [dict(r) for r in rows]
