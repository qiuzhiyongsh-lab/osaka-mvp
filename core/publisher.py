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
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

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

# v1.9.38：本地 AI 结构库路径（与 AIStructureStore 默认路径一致），publisher 增量读取推送。
#   core/ 的父目录即项目根（osaka-mvp / 发布包 app_local 均成立），其下 data/ai_pdf_store.db。
AI_DB_DEFAULT = Path(__file__).resolve().parents[1] / "data" / "ai_pdf_store.db"


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
    # v1.9.37-hotfix：失败也记时间戳，避免 last_error 孤立误判。
    #   （失败分支只写 last_error、不刷新 last_at → 旧 502 会停在上次成功时间戳，
    #    看起来像"当前故障"。加 last_error_at 让错误与发生时间成对出现。）
    try:
        con.execute("ALTER TABLE publish_state ADD COLUMN last_error_at TEXT")
    except Exception:
        pass
    # v1.9.38：AI 结构自动上线的水位线 + 错误标志（独立于房源主数据）
    try:
        con.execute("ALTER TABLE publish_state ADD COLUMN last_ai_at TEXT")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE publish_state ADD COLUMN last_ai_error TEXT")
    except Exception:
        pass
    con.commit()


def get_state(con: sqlite3.Connection) -> dict:
    _ensure_state(con)
    row = con.execute("SELECT * FROM publish_state WHERE id=1").fetchone()
    if row is None:
        return {"last_at": None, "last_count": 0, "last_mode": None,
                "last_endpoint": None, "last_error": None, "last_error_at": None,
                "last_ai_at": None, "last_ai_error": None}
    try:
        keys = row.keys()
        return {k: row[k] for k in keys}
    except Exception:                                        # noqa: BLE001
        return {"last_at": row[1], "last_count": row[2], "last_mode": row[3],
                "last_endpoint": row[4], "last_error": row[5],
                "last_error_at": row[6] if len(row) > 6 else None,
                "last_ai_at": row[7] if len(row) > 7 else None,
                "last_ai_error": row[8] if len(row) > 8 else None}


def set_state(con: sqlite3.Connection, *, last_at=None, last_count=None,
              last_mode=None, last_endpoint=None, last_error=None,
              last_ai_at=None, last_ai_error=None) -> None:
    _ensure_state(con)
    cur = get_state(con)
    # last_error=None 即「清空」（所有调用方：成功/无行传 None 清、失败传字符串设）；
    #   ⚠ 旧实现用 `last_error if not None else cur["last_error"]` 把 None 当「保留」，
    #   导致成功分支永远清不掉错误 → 陈旧 502 赖在 last_error 不走的真 bug，已修正。
    # 失败(resolved last_error 非 None)→记当前时间；成功/清空→置 None（与 last_error 同生命周期）
    _error_at = (datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                 ) if last_error else None
    con.execute(
        "INSERT INTO publish_state (id,last_at,last_count,last_mode,last_endpoint,last_error,last_error_at,last_ai_at,last_ai_error)"
        " VALUES (1,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
        " last_at=excluded.last_at, last_count=excluded.last_count,"
        " last_mode=excluded.last_mode, last_endpoint=excluded.last_endpoint,"
        " last_error=excluded.last_error, last_error_at=excluded.last_error_at,"
        " last_ai_at=excluded.last_ai_at, last_ai_error=excluded.last_ai_error",
        (last_at if last_at is not None else cur["last_at"],
         last_count if last_count is not None else cur["last_count"],
         last_mode if last_mode is not None else cur["last_mode"],
         last_endpoint if last_endpoint is not None else cur["last_endpoint"],
         last_error,
         _error_at,
         last_ai_at if last_ai_at is not None else cur["last_ai_at"],
         last_ai_error if last_ai_error is not None else cur["last_ai_error"]),
    )
    con.commit()


# --------------------------------------------------------------------------
# 取数据
# --------------------------------------------------------------------------
def _scope_where(cfg: dict, mode: str, watermark: str | None):
    """返回 (where_sql, args)。mode='full' 取全范围；mode='incr' 只取水位线之后的。

    v1.9.9 新增：date_from / date_to（日期段筛选，替代/补充旧的 month_scope）。
      口径同 scope_caliber：
        · download —— COALESCE(last_seen_at, first_seen_at)（本地下载日）
        · platform —— COALESCE(chg_date_iso, reg_date_iso)（平台登録/変更日）
      date_to 若只给日期(YYYY-MM-DD) 自动补到当天 23:59:59，闭区间更直观。
    """
    pub = (cfg.get("publish") or {})
    month = str(pub.get("month_scope") or "").strip()      # 例：2026-09（旧，兼容）
    caliber = str(pub.get("scope_caliber") or "download").strip()
    date_from = str(pub.get("date_from") or "").strip()
    date_to = str(pub.get("date_to") or "").strip()
    where, args = [], []
    col = ("COALESCE(chg_date_iso, reg_date_iso)"
           if caliber == "platform"
           else "COALESCE(last_seen_at, first_seen_at)")
    # 月份口径（兼容旧配置）：没填日期段时才回退到 month_scope
    if (not date_from and not date_to) and month:
        if caliber == "platform":
            where.append("(substr(chg_date_iso,1,7)=? OR substr(reg_date_iso,1,7)=?)")
            args += [month, month]
        else:
            where.append("substr(COALESCE(last_seen_at,first_seen_at),1,7)=?")
            args.append(month)
    # 日期段口径（新）：闭区间 [date_from, date_to]
    if date_from:
        where.append(col + " >= ?")
        args.append(date_from)
    if date_to:
        dt = date_to
        if len(date_to) == 10:          # YYYY-MM-DD → 当天结束
            dt = date_to + " 23:59:59"
        where.append(col + " <= ?")
        args.append(dt)
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
    # ⚠ v1.9.19：同一个密钥历史上被写成两个键名 —— 发送端读 `publish.token`，
    #   而线上 `api_ingest` 只认 `publish.ingest_token`。一旦有人只改了其中一个，
    #   就会出现「守卫放行了、令牌却不对」的**静默 401**（最难查的一类）。
    #   这里做键名兜底：谁有值用谁，两个都空才判为未配置。
    token = str(pub.get("token") or pub.get("ingest_token") or "").strip()
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
# v1.9.38：AI 结构自动上线 —— 本地增量读取 + POST 到 /api/ingest
# --------------------------------------------------------------------------
def build_ai_rows(ai_db_path, last_ai_at=None, log=None) -> tuple[list[dict], str | None]:
    """从本地 AI 结构库取增量（updated_at > 水位线）行。返回 (rows, 本次水位线)。

    updated_at 存 `YYYY-MM-DD HH:MM:SS` 文本，字典序==时间序，直接字符串比较即增量正确。
    """
    say = log or (lambda *_a, **_k: None)
    cols = ["property_no", "structure_json", "radar_json", "conclusion",
            "anomaly_json", "overall", "edited", "source_file", "source_row",
            "extracted_at", "created_at", "updated_at"]
    try:
        con = sqlite3.connect(str(ai_db_path), timeout=20)
        con.row_factory = sqlite3.Row
        if last_ai_at:
            raw = con.execute(
                "SELECT * FROM ai_structure WHERE updated_at > ? ORDER BY updated_at",
                (last_ai_at,)).fetchall()
        else:
            raw = con.execute("SELECT * FROM ai_structure ORDER BY updated_at").fetchall()
        con.close()
    except Exception as e:                                       # noqa: BLE001
        say("⚠ 读本地 AI 结构库失败（跳过本次 AI 推送）：%s" % e)
        return [], last_ai_at
    rows = [{c: r[c] for c in cols} for r in raw]
    wm = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    say("· 待上传 AI 结构 %d 行（增量基线 %s）" % (len(rows), last_ai_at or "全量首推"))
    return rows, wm


def post_ai(cfg: dict, ai_rows: list[dict], log=None) -> dict:
    """把 AI 结构行 POST 到线上 /api/ingest（ai_structure 键）。"""
    say = log or (lambda *_a, **_k: None)
    pub = (cfg.get("publish") or {})
    endpoint = str(pub.get("endpoint") or "").strip()
    token = str(pub.get("token") or pub.get("ingest_token") or "").strip()
    if not endpoint:
        return {"ok": False, "errors": ["没有配置线上地址（publish.endpoint）"]}
    url = endpoint.rstrip("/") + "/api/ingest"
    sent, upserted, errors = 0, 0, []
    total = len(ai_rows)
    for i in range(0, total, BATCH):
        chunk = ai_rows[i:i + BATCH]
        try:
            res = _post(url, token, {"ai_structure": chunk, "source": "osaka-mvp"}, timeout=120)
        except urllib.error.HTTPError as e:
            errors.append("HTTP %s: %s" % (e.code, e.read()[:200]))
            break
        except Exception as e:                                   # noqa: BLE001
            errors.append(type(e).__name__ + ": " + str(e))
            break
        if not res.get("ok"):
            errors.append(str(res.get("error") or res)[:200])
            break
        sent += len(chunk)
        upserted += int(res.get("ai_upserted") or 0)
        say("  ↑ 已推送 AI %d/%d（线上确认 %s）" % (sent, total, res.get("ai_upserted")))
    return {"ok": not errors and sent == total, "sent": sent,
            "upserted": upserted, "total": total, "errors": errors, "url": url}


def _push_ai(cfg: dict, con: sqlite3.Connection, log=None,
             force: bool = False) -> dict:
    """v1.9.38：把本地 AI 结构推到线上 /api/ingest（ai_structure 键）。

    增量水位线 last_ai_at：上次成功推的基线；None=全量首推。失败只记 last_ai_error，
    不影响房源主数据推送结果。返回 {ok,sent,upserted,total,errors}。

    v1.9.44（勇哥需求「强制同步上传」）：`force=True` → **忽略增量水位线**，
    把本地 AI 结构库**全量**重推一遍（线上 == 本地），用于"线上漏了 AI 解读、
    想一键补齐"的场景；推成功后水位线照样前移。
    """
    say = log or (lambda *_a, **_k: None)
    state = get_state(con)
    base = None if force else state.get("last_ai_at")
    if force:
        say("· 强制同步：忽略 AI 增量水位线，全量重推本地 AI 解读")
    ai_rows, ai_wm = build_ai_rows(AI_DB_DEFAULT, base, log=say)
    if not ai_rows:
        say("· 没有需要上传的 AI 结构（自 %s 无变化）" % (state.get("last_ai_at") or "无基线"))
        return {"ok": True, "sent": 0, "total": 0, "skipped": True}
    ares = post_ai(cfg, ai_rows, log=say)
    if ares.get("ok"):
        set_state(con, last_ai_at=ai_wm, last_ai_error=None)
        say("✓ AI 结构已推送 %d 条" % ares["sent"])
    else:
        set_state(con, last_ai_error="; ".join(ares.get("errors") or [])[:500])
        say("✗ AI 结构推送失败：" + ("; ".join(ares.get("errors") or [])))
    return ares


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
            log=None, force_ai: bool = False) -> dict:
    """执行一次上传。mode='incr' 增量（自动模式用）/ 'full' 全量重传（手动模式用）。

    v1.9.38：房源主数据 + AI 结构**两条腿都推**（AI 结构自动上线，不再依赖手动 sync+deploy）。
    v1.9.44：`force_ai=True` → AI 结构**忽略增量水位线、全量重推**（「强制同步上传」按钮用；
    与 mode='full' 的房源全量重传合起来 = 线上 100% 对齐本地）。
    """
    say = log or (lambda *_a, **_k: None)
    t0 = datetime.now()
    say("▶ 开始上传到线上（%s）…" % ("增量" if mode == "incr" else "全量重传"))
    rows, watermark = build_rows(con, cfg, mode, log=say)
    if not rows:
        say("· 没有需要上传的房源主数据（增量模式下很正常：这轮没有新变化）")
        set_state(con, last_error=None, last_mode=mode)
        res = {"ok": True, "sent": 0, "total": 0, "skipped": True}
    else:
        res = post_rows(cfg, rows, log=say)
        try:
            write_site_data(cfg, rows, log=say)
        except Exception as e:                                 # noqa: BLE001
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

    # v1.9.38：AI 结构自动上线（独立于房源主数据分支，增量推、失败不阻断主数据）
    ai_res = _push_ai(cfg, con, log=say, force=force_ai)
    res["elapsed_s"] = round((datetime.now() - t0).total_seconds(), 1)
    res["ai"] = ai_res
    return res


def preview_scope(cfg: dict, con: sqlite3.Connection, limit: int = 5, log=None) -> dict:
    """预览：按当前 publish 配置（月份/口径/日期段）算「会推多少行 + 样本」，不真正发送。

    v1.9.9 新增，供设置页「预览会传多少」按钮调用——先验证日期段/口径筛选是否如预期，
    避免一拍脑袋就全量推送、推完才发现范围错了。
    """
    say = log or (lambda *_a, **_k: None)
    # 与 build_rows 完全一致：直接复用 _scope_where 的返回（不加 is_active），
    # 保证「预览 count」==「实际发布 count」，用户看到的数就是真会推的数。
    inner, args = _scope_where(cfg, "full", None)
    cur = con.cursor()
    cnt = cur.execute("SELECT count(*) AS c FROM properties WHERE " + inner, args).fetchone()
    total = int(cnt[0]) if cnt else 0
    cols = ["property_no", "building_name", "address", "reg_date_iso", "chg_date_iso"]
    rows = cur.execute(
        "SELECT " + ",".join(cols) + " FROM properties WHERE " + inner +
        " ORDER BY last_seen_at DESC LIMIT ?",
        args + [int(limit)]).fetchall()
    # 不依赖 con.row_factory：用列名 zip 元组，任何连接都能跑（含测试用裸连接）
    sample = [dict(zip(cols, r)) for r in rows]
    say("· 预览：范围内共 %d 行（前 %d 条示例）" % (total, min(int(limit), total)))
    return {"count": total, "sample": sample}


# --------------------------------------------------------------------------
# v1.9.9：上传独立定时器（与抓取轮解耦）
# --------------------------------------------------------------------------
# v1.9.21：定时器上传日志共享缓冲 —— PUB_LOOP 的 _log 同时写这里，
#   让 /api/publish/status 能把「自动定时器」的日志也回传给上传面板
#   （此前 PUB_LOOP 只把日志交给 app.log() 进 server.log，上传面板永远看不到定时上传痕迹）。
PUB_LOG: list = []


def pub_log(msg):
    PUB_LOG.append(str(msg))
    if len(PUB_LOG) > 400:
        PUB_LOG[:] = PUB_LOG[-400:]


class PublishLoop:
    """独立上传线程：按 publish.interval_minutes 周期（默认 10 分钟）检查本地库，
    有新盘/变更就增量推到线上，没有就静默跳过（不碰 REINS、不刷日志刷屏）。

    与 v1.5.19 的「每轮抓完 _auto_push」互补：
      · 抓取轮尾巴的 _auto_push = 抓完立刻推（最新鲜）
      · 本循环 = 周期兜底（即便长时间不抓、或 _auto_push 失败，也能按周期补推）
    两者都走 publisher.publish(incr) 的水位线，幂等、不重复推。
    """

    def __init__(self, store, cfg: dict, log_fn=None):
        self.store = store
        self.cfg = cfg
        # v1.9.21：定时器日志同时进共享缓冲 PUB_LOG（回传面板）+ 原始 log_fn（server.log）
        base = log_fn or (lambda m: None)
        self._log = lambda m: (base(m), pub_log(m))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.next_run_at: datetime | None = None
        self.last_run_at: str = ""
        self.last_count: int = 0
        self.last_error: str = ""
        self.enabled: bool = False

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _interval_seconds(self) -> float:
        pub = (self.cfg.get("publish") or {})
        m = float(pub.get("interval_minutes") or 10)
        if m < 1:
            m = 1
        # 下限保护：低于 1 分钟 = 高频刷线上接口，夹到 1 分钟
        return m * 60.0

    def _is_enabled(self) -> bool:
        pub = (self.cfg.get("publish") or {})
        return bool(pub.get("auto_enabled")) and bool(pub.get("enabled"))

    def status(self) -> dict:
        return {
            "running": self.running,
            "enabled": self._is_enabled(),
            "interval_minutes": (self.cfg.get("publish") or {}).get("interval_minutes", 10),
            "next_run_at": self.next_run_at.strftime("%Y-%m-%d %H:%M:%S") if self.next_run_at else None,
            "last_run_at": self.last_run_at or None,
            "last_count": self.last_count,
            "last_error": self.last_error or "",
        }

    def start(self) -> str:
        if self.running:
            return "已在运行"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="publish-loop")
        self._thread.start()
        self._log("☁ 上传定时器已启动（每 %d 分钟检查一次）" % int(self._interval_seconds() // 60))
        return "已启动"

    def stop(self) -> str:
        self._stop.set()
        self.next_run_at = None
        self._log("☁ 上传定时器已停止")
        return "已停止"

    def _loop(self):
        while not self._stop.is_set():
            self.enabled = self._is_enabled()
            if not self.enabled:
                self.next_run_at = None
                time.sleep(2)
                continue
            delay = self._interval_seconds()
            self.next_run_at = datetime.now() + timedelta(seconds=delay)
            self._log("☁ 下次上传：%s" % self.next_run_at.strftime("%H:%M:%S"))
            # 分片 sleep，便于随时停止 / 配置热改实时生效
            waited = 0.0
            while waited < delay and not self._stop.is_set():
                time.sleep(min(2.0, delay - waited))
                waited += 2.0
            if self._stop.is_set():
                break
            if not self._is_enabled():
                continue
            try:
                r = publish(self.cfg, self.store.conn, mode="incr", log=self._log)
                self.last_count = int(r.get("sent") or 0)
                self.last_run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if r.get("errors"):
                    self.last_error = "; ".join(r.get("errors"))[:500]
                else:
                    self.last_error = ""
                # 区间未变 / 无新数据：publish 内部已记「跳过」，这里不重复刷
            except Exception as e:                                # noqa: BLE001
                self.last_error = "%s: %s" % (type(e).__name__, e)
                self._log("☁ 上传定时器异常：" + self.last_error)
            # v1.9.28：同一个心跳顺手做一次**账号种子兜底推送**（非强制）。
            #   平时管理员一改账号就即时推了，这里只为兜两种意外：
            #   ① 那次即时推送恰好网络失败；② 线上容器被重建，库回落成发布包里的旧种子。
            #   内容指纹与上次一致时 push_seed 内部**直接返回、零网络请求**，所以挂着不花钱。
            #   不传 force_users：保护员工在线上自设的密码不被旧种子回退。
            try:
                from core import account_sync as _accsync         # noqa: PLC0415（避免循环导入）
                _accsync.push_seed(self.cfg, log=self._log, timeout=8)
            except Exception as e:                                # noqa: BLE001
                self._log("☁ 账号种子兜底推送异常（不影响上传）：%s: %s" % (type(e).__name__, e))
        self._log("☁ 上传定时器线程已退出")
