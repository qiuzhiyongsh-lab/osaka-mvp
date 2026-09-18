# -*- coding: utf-8 -*-
"""上传到线上（v1.5.6 新增）—— 把本机抓到的房源推到线上「大阪房源查询系统」。

【为什么需要它】本机（osaka-mvp）是抓取端，线上那个 WorkBuddy 应用
（wbapp_L8M4tyzslRfAA0IWoy29Iz）是给人看的展示端。勇哥要求：
  ① 设置页要有上传选项：**自动完成**（每轮抓完自动增量推送）/ **手动方式**（点按钮全量重传）；
  ② **不要 PDF**，只把详情内容（detail_json）传过去；
  ③ 现有的本地 9 月数据全部推上去。

【两条腿走路 · 为什么不只做一条】
  · 腿 1（在线）：POST /api/ingest —— 批量 upsert 到线上库。快、实时、不重启。
  · 腿 2（兜底）：data/upload/site_data.json —— 线上应用启动时幂等导入。
    因为线上应用是「代码包」形态发布的，重新发布时容器里的库可能被重置；
    把数据随包带上去，才能保证「重新发布也不丢」。
  所以上传时**两条腿都走**：先 POST，再写一份 json 备着。

【口径】scope 默认按「下载日落在某个月」（COALESCE(last_seen_at, first_seen_at)），
因为勇哥说的是"本地的 9 月份数据"。想改成按平台日期，把 config.publish.scope_caliber
设为 platform 即可（用 reg_date_iso/chg_date_iso）。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime

# 本地列 → 列映射（v1.6 同源透传）。
# 旧版曾把本地列重命名为旧线上列名（building_name→title、address→address_raw…），
# 但 v1.5.18 同源后线上跑本地同一份代码（web/ 模板读本地列名、store 按本地
# PROPERTY_COLUMNS 落库），重命名会导致这些列被 upsert 丢弃 → 线上名称/地址空白（BUG-2）。
# 故现在**直接透传本地列名**（build_rows 用 src 作输出键；dst 保留仅供阅读、不再使用）。
FIELD_MAP = [
    ("property_no", "property_no"),
    ("building_name", "title"),
    ("property_subtype", "category"),
    ("kind", "kind"),
    ("ward", "ward"),
    ("address", "address_raw"),
    ("line_station", "station"),
    ("price", "price"),
    ("previous_price", "previous_price"),
    ("land_area", "land_area"),
    ("exclusive_area", "exclusive_area"),
    ("building_area", "building_area"),
    ("unit_price_sqm", "unit_price_sqm"),
    ("unit_price_tsubo", "unit_price_tsubo"),
    ("built_year_month", "built_year_month"),
    ("layout", "layout"),
    ("floor", "floor_info"),
    ("above_ground_floors", "above_ground_floors"),
    ("image_count", "image_count"),
    ("source_url", "source_url"),
    ("registration_date", "registration_date"),
    ("change_date", "change_date"),
    ("reg_date_iso", "reg_date_iso"),
    ("chg_date_iso", "chg_date_iso"),
    ("first_seen_at", "first_seen_at"),
    ("last_seen_at", "last_seen_at"),
    ("last_changed_at", "last_changed_at"),
    ("is_active", "is_active"),
    ("has_photo", "has_photo"),
    ("has_floorplan", "has_floorplan"),
    ("has_map", "has_map"),
    ("detail_json", "detail_json"),
]

# 明确**不传**的列：PDF（勇哥：不需要 PDF）、以及本机专用的下架计数
NEVER_UPLOAD = {"pdf_path", "pdf_url", "absent_runs"}

BATCH = 200


# --------------------------------------------------------------------------
# 状态记录（增量上传的水位线）
# --------------------------------------------------------------------------
def _ensure_state(con: sqlite3.Connection) -> None:
    # ⚠ SQL 里**绝不能**写 -- 行内注释：它会把后面的内容（含右括号）一起注释掉，
    #   报 "incomplete input"。说明一律写在 Python 注释里。
    #   last_at = 上次上传成功的时间（增量上传的水位线）
    con.execute(
        "CREATE TABLE IF NOT EXISTS publish_state ("
        "  id INTEGER PRIMARY KEY CHECK (id=1),"
        "  last_at TEXT,"
        "  last_count INTEGER DEFAULT 0,"
        "  last_mode TEXT,"
        "  last_endpoint TEXT,"
        "  last_error TEXT"
        ")"
    )
    con.commit()


def get_state(con: sqlite3.Connection) -> dict:
    _ensure_state(con)
    row = con.execute("SELECT * FROM publish_state WHERE id=1").fetchone()
    if row is None:
        return {"last_at": None, "last_count": 0, "last_mode": None,
                "last_endpoint": None, "last_error": None}
    try:
        keys = row.keys()
        return {k: row[k] for k in keys}
    except Exception:                                        # noqa: BLE001
        return {"last_at": row[1], "last_count": row[2], "last_mode": row[3],
                "last_endpoint": row[4], "last_error": row[5]}


def set_state(con: sqlite3.Connection, *, last_at=None, last_count=None,
              last_mode=None, last_endpoint=None, last_error=None) -> None:
    _ensure_state(con)
    cur = get_state(con)
    con.execute(
        "INSERT INTO publish_state (id,last_at,last_count,last_mode,last_endpoint,last_error)"
        " VALUES (1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
        " last_at=excluded.last_at, last_count=excluded.last_count,"
        " last_mode=excluded.last_mode, last_endpoint=excluded.last_endpoint,"
        " last_error=excluded.last_error",
        (last_at if last_at is not None else cur["last_at"],
         last_count if last_count is not None else cur["last_count"],
         last_mode if last_mode is not None else cur["last_mode"],
         last_endpoint if last_endpoint is not None else cur["last_endpoint"],
         last_error if last_error is not None else cur["last_error"]),
    )
    con.commit()


# --------------------------------------------------------------------------
# 取数据
# --------------------------------------------------------------------------
def _scope_where(cfg: dict, mode: str, watermark: str | None):
    """返回 (where_sql, args)。mode='full' 取全范围；mode='incr' 只取水位线之后的。"""
    pub = (cfg.get("publish") or {})
    month = str(pub.get("month_scope") or "").strip()      # 例：2026-09
    caliber = str(pub.get("scope_caliber") or "download").strip()
    where, args = [], []
    if month:
        if caliber == "platform":
            where.append("(substr(chg_date_iso,1,7)=? OR substr(reg_date_iso,1,7)=?)")
            args += [month, month]
        else:
            where.append("substr(COALESCE(last_seen_at,first_seen_at),1,7)=?")
            args.append(month)
    if mode == "incr" and watermark:
        # 水位线之后「又被看到过」的行 → 增量
        where.append("COALESCE(last_seen_at,first_seen_at) > ?")
        args.append(watermark)
    return (" AND ".join(where) if where else "1=1"), args


def build_rows(con: sqlite3.Connection, cfg: dict, mode: str = "full",
               log=None) -> tuple[list[dict], str | None]:
    """从本地库取出要上传的行（已映射成线上列名）。返回 (rows, 本次水位线)。"""
    say = log or (lambda *_a, **_k: None)
    state = get_state(con)
    where, args = _scope_where(cfg, mode, state.get("last_at"))
    cols = [c for c, _ in FIELD_MAP]
    sql = ("SELECT " + ",".join(cols) + " FROM properties WHERE " + where +
           " ORDER BY COALESCE(last_seen_at,first_seen_at)")
    con.row_factory = sqlite3.Row
    raw = list(con.execute(sql, args))
    rows: list[dict] = []
    for r in raw:
        out = {}
        # v1.6 BUG-2 修复：同源后线上跑本地同一份代码（web/ 模板读本地列名
        # building_name/address/...），线上 upsert 也按本地 PROPERTY_COLUMNS 落库。
        # 因此**直接透传本地列名**（src），不再重命名为旧线上列名（title/address_raw/...），
        # 否则这些旧名列被 upsert 丢弃、本地列恒为空 → 线上名称/地址空白。
        for src in cols:
            try:
                out[src] = r[src]
            except Exception:                                # noqa: BLE001
                out[src] = None
        out["source_site"] = "reins"
        for _k in NEVER_UPLOAD:
            out.pop(_k, None)
        rows.append(out)
    watermark = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    say("· 待上传 %d 行（口径：%s月份=%s / 模式=%s）"
        % (len(rows), (cfg.get("publish") or {}).get("scope_caliber", "download"),
           (cfg.get("publish") or {}).get("month_scope") or "全部", mode))
    return rows, watermark


# --------------------------------------------------------------------------
# 腿 1：POST 到线上
# --------------------------------------------------------------------------
def _post(endpoint: str, token: str, payload: dict, timeout: int = 60) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Publish-Token": token or ""})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        txt = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(txt)
    except Exception:                                        # noqa: BLE001
        return {"ok": False, "raw": txt[:400]}


def post_rows(cfg: dict, rows: list[dict], log=None) -> dict:
    say = log or (lambda *_a, **_k: None)
    pub = (cfg.get("publish") or {})
    endpoint = str(pub.get("endpoint") or "").strip()
    token = str(pub.get("token") or "").strip()
    if not endpoint:
        # ⚠ 必须用 "errors" 键（与正常返回一致），否则 publish() 读 res.get("errors")
        #   （复数）会得到 []，把真实原因吞成「未知原因」。这是 2026-09-18 推送静默失败的根因。
        return {"ok": False, "errors": ["没有配置线上地址（publish.endpoint）"]}
    url = endpoint.rstrip("/") + "/api/ingest"
    sent, upserted, errors = 0, 0, []
    total = len(rows)
    for i in range(0, total, BATCH):
        chunk = rows[i:i + BATCH]
        try:
            res = _post(url, token, {"rows": chunk, "source": "osaka-mvp"}, timeout=120)
        except urllib.error.HTTPError as e:
            errors.append("HTTP %s: %s" % (e.code, e.read()[:200]))
            break
        except Exception as e:                                # noqa: BLE001
            errors.append(type(e).__name__ + ": " + str(e))
            break
        if not res.get("ok"):
            errors.append(str(res.get("error") or res)[:200])
            break
        sent += len(chunk)
        upserted += int(res.get("upserted") or 0)
        say("  ↑ 已推送 %d/%d（线上确认 %s）" % (sent, total, res.get("upserted")))
    return {"ok": not errors and sent == total, "sent": sent,
            "upserted": upserted, "total": total, "errors": errors, "url": url}


# --------------------------------------------------------------------------
# 腿 2：写一份 json 兜底（线上重新发布时启动导入）
# --------------------------------------------------------------------------
def write_site_data(cfg: dict, rows: list[dict], log=None) -> str:
    say = log or (lambda *_a, **_k: None)
    root = str((cfg.get("app") or {}).get("output_root") or "./data")
    outdir = os.path.join(root, "upload")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "site_data.json")
    tmp = path + ".part"
    data = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "osaka-mvp", "count": len(rows), "rows": rows}
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)          # 原子改名：磁盘上永远要么没有、要么完整
    say("· 兜底数据包已生成：%s（%.2f MB）"
        % (path, os.path.getsize(path) / 1048576.0))
    return path


# --------------------------------------------------------------------------
# 对外总入口
# --------------------------------------------------------------------------
def publish(cfg: dict, con: sqlite3.Connection, mode: str = "incr",
            log=None) -> dict:
    """执行一次上传。mode='incr' 增量（自动模式用）/ 'full' 全量重传（手动模式用）。"""
    say = log or (lambda *_a, **_k: None)
    t0 = datetime.now()
    say("▶ 开始上传到线上（%s）…" % ("增量" if mode == "incr" else "全量重传"))
    rows, watermark = build_rows(con, cfg, mode, log=say)
    if not rows:
        say("· 没有需要上传的数据（增量模式下很正常：这轮没有新变化）")
        set_state(con, last_error=None, last_mode=mode)
        return {"ok": True, "sent": 0, "total": 0, "skipped": True}

    res = post_rows(cfg, rows, log=say)
    try:
        write_site_data(cfg, rows, log=say)
    except Exception as e:                                     # noqa: BLE001
        say("⚠ 兜底数据包生成失败（不影响在线推送）：" + str(e))

    if res.get("ok"):
        set_state(con, last_at=watermark, last_count=res["sent"],
                  last_mode=mode,
                  last_endpoint=str((cfg.get("publish") or {}).get("endpoint") or ""),
                  last_error=None)
        say("✓ 上传完成：%d 行 / 用时 %.1fs" % (res["sent"], (datetime.now() - t0).total_seconds()))
    else:
        # 兜底：任何来源若用单数 "error" 键，也要能读到，绝不吞成「未知原因」
        _errs = res.get("errors") or (res.get("error") and [str(res.get("error"))]) or []
        set_state(con, last_error="; ".join(_errs)[:500], last_mode=mode)
        say("✗ 上传失败：" + ("; ".join(_errs) or "未知原因"))
    res["elapsed_s"] = round((datetime.now() - t0).total_seconds(), 1)
    return res
