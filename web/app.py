# -*- coding: utf-8 -*-
"""本地版站点（Flask）。跑在你自己的机器上，地址 http://127.0.0.1:8765

页面：
  /            仪表盘：本地数据概览 + 三种更新方式的开关 + 实时日志
  /search      查询（区/种别/价格/面积/关键词/按天）
  /p/<编号>     房源详情（含本地 PDF、变更历史）
  /runs        运行日志
  /files       本地落盘文件（Excel / 按天页 / PDF），可直接打开
"""
from __future__ import annotations

import os
import re
import sys
import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from werkzeug.exceptions import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfgmod                      # noqa: E402
from core import credentials as creds_mod              # noqa: E402
from core import version as ver                        # noqa: E402
from core.auth import Auth, friendly_error            # noqa: E402
from core.crawler import probe_query                  # noqa: E402
from core import store as store_mod                   # noqa: E402
from core.scheduler import Scheduler                  # noqa: E402
from core.store import Store                          # noqa: E402
from core.wareki import to_ad as wareki_to_ad          # noqa: E402

# 线上收数时**永不接受**的列（勇哥：PDF 不上传）
NEVER_UPLOAD_KEYS = {"pdf_path", "pdf_url", "absent_runs"}


# ---------------- 全局：配置 / 库 / 调度 / 日志 ----------------
CFG = cfgmod.load()
PATHS = cfgmod.paths(CFG)
STORE = Store(PATHS["db"])
LOGS: deque[str] = deque(maxlen=400)
TEST_RESULT: dict = {}            # 测试类操作的结构化结果，供页面轮询展示

# 日志同时落盘：data/logs/server.log。服务"莫名其妙没了"时靠它查原因。
LOG_DIR = Path(PATHS["root"]) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    LOGS.append(line)
    print(line, flush=True)
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2 * 1024 * 1024:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass                                          # 日志失败绝不影响主流程


# v1.2.5：启动即把存量"伪新增"（平台早已改价、我方才首抓）回填为 price_down/price_up，
# 使"新增/变更"口径与平台对齐。幂等（已有变更记录则跳过），仅首启有效，失败也不阻塞启动。
try:
    _n = STORE.backfill_platform_changes()
    if _n:
        log(f"[v1.2.5] 回填平台变更记录 {_n} 条（存量'伪新增'→'变更'）")
except Exception as _e:
    log(f"[v1.2.5] 回填跳过：{_e}")

# v1.5.4：启动补算「平台日期口径」reg_date_iso / chg_date_iso，并清掉可证伪的脏日期
# （pipeline 曾用下载日伪造的 8 位「登録年月日」、误存进日期字段的下拉框文案）。
# 幂等，失败也不阻塞启动 —— 不跑它的话「按平台日期筛选」会一直是空的。
try:
    _bd = STORE.backfill_platform_dates()
    log(f"[v1.5.4] 平台日期口径回填：{_bd}")
except Exception as _e:
    log(f"[v1.5.4] 平台日期回填跳过：{_e}")

# v1.4.0：启动自愈 —— 上次进程被直接关掉时 finish_run 没机会跑，runs 行会永远卡在
# running，页面上就会同时显示好几个"进行中"。启动扫一遍补完，杜绝假'进行中'。
try:
    _m = STORE.recover_runs()
    if _m:
        log(f"[v1.4.0] 启动自愈：{_m} 个卡住的轮次已标 interrupted")
except Exception as _e:
    log(f"[v1.4.0] 启动自愈跳过：{_e}")

# v1.5.2：上一轮被打断 → 按勇哥的要求"咨询一声"：从断点继续 / 重新下载整轮 / 这次不跑。
# 15 秒没点 → 走默认（restart，跟以前一样按调度跑一整轮），绝不自作主张。
try:
    _ir = STORE.last_interrupted_run()
    _st = STORE.load_crawl_state(_ir["id"]) if _ir else None
    if _ir and _st and int(_st.get("group_index") or 0) > 0:
        _did = STORE.add_decision(
            _ir["id"], "resume_after_restart",
            {"run_id": _ir["id"], "group": _st.get("group_label") or "",
             "group_index": _st.get("group_index"), "page": _st.get("page") or 1},
            [{"action": "resume", "label": "从断点继续（跳过已跑完的房型）"},
             {"action": "restart", "label": "重新下载整轮（12 个子查询全跑）"},
             {"action": "skip", "label": "这次不跑（等下次调度）"}],
            "restart", 15)
        log(f"[v1.5.2] 上一轮 run{_ir['id']} 在第 {_st.get('group_index')} 组被打断"
            f" → 已在页面发起询问（decision #{_did}）")
except Exception as _e:
    log(f"[v1.5.2] 断点询问跳过：{_e}")


SCHED = Scheduler(STORE, CFG, log_fn=log)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False

# ============================================================
# v1.5.18：对外（线上分享）模式
#   设环境变量 OSAKA_PUBLIC=1 时，**同一套代码**当作"只读展示站"跑：
#     · 采集 / 账号 / 本地文件 / 指定日期下载 四个页面不暴露
#     · 抓取、调度、发布、凭据、登录、PDF 下载 一类接口一律拒绝
#     · 首页（仪表盘，含更新开关）直接跳到查询页
#   本地跑（不设这个变量）行为完全不变 —— 这是"线上页面 == 本地页面"的前提。
# ============================================================
PUBLIC = os.environ.get("OSAKA_PUBLIC", "").strip().lower() in {"1", "true", "yes", "on"}

PUBLIC_HIDDEN_PAGES = {"/collect", "/account", "/files", "/specified"}
PUBLIC_HIDDEN_APIS = (
    "/api/run", "/api/publish", "/api/env", "/api/schedule",
    "/api/login", "/api/credentials", "/api/download",
    "/api/query/test", "/api/mode", "/api/decide", "/api/sync",
    "/api/notifications/read", "/api/status", "/api/logs",
)


@app.before_request
def _public_guard():
    """对外模式下把"只有本机才有意义"的能力收起来（PDF 也在其中，随 /download 一起关）。"""
    if not PUBLIC:
        return None
    path = request.path
    # 注："/" 不再重定向 —— v1.5.19 起对外版有自己的**只读概览页**（见 index()）。
    if path in PUBLIC_HIDDEN_PAGES or path.startswith("/download/"):
        abort(404)
    if path.startswith("/api/") and any(path.startswith(x) for x in PUBLIC_HIDDEN_APIS):
        return jsonify({"status": "error",
                        "message": "线上展示版只提供查询，不提供此功能"}), 403
    return None


@app.before_request
def _access_guard():
    """v1.8.0 N2 访问码：对外模式若设了 `public.access_code`，未带正确口令则拦截。

    - 只读保证已由 `_public_guard` 完成；这里只管「谁看得到」。
    - 口令来源：`config.public.access_code`（config.local.yaml 本机真值，不入库；
      随同步脚本上云到线上 config.yaml）。**未设 → 完全开放**（向后兼容，当前线上即此态）。
    - 校验：URL `?code=` 或 Cookie `osaka_access_code` 任一匹配即放行。
    - 页面：渲染简单输入页；`/api`（除 /api/ping 健康检查）返回 401 JSON。
    """
    if not PUBLIC:
        return None
    code = (CFG.get("public") or {}).get("access_code") or ""
    if not code:
        return None
    path = request.path
    ok = (request.args.get("code") == code) or (request.cookies.get("osaka_access_code") == code)
    if ok:
        return None
    if path.startswith("/api/"):
        if path == "/api/ping":        # 健康检查放行，便于监控/告警
            return None
        return jsonify({"status": "error", "message": "需要访问码"}), 401
    return render_template("access_code.html", path=path)


@app.after_request
def _plant_access_cookie(resp):
    """凭 `?code=` 进来的，顺手种 Cookie，之后免带参数（30 天）。"""
    code = (CFG.get("public") or {}).get("access_code") or ""
    if PUBLIC and code and request.args.get("code") == code:
        resp.set_cookie("osaka_access_code", code,
                        max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax")
    return resp


@app.context_processor
def inject_globals():
    """所有模板都能直接拿到 cfg / paths / sched / 金额格式器。"""
    return {
        "cfg": CFG,
        "paths": {k: str(v) for k, v in PATHS.items()},
        "sched": SCHED.status(),
        "test": dict(TEST_RESULT),
        "version": ver.VERSION,
        "build_at": ver.BUILD_AT,
        "public": PUBLIC,
        "wan": lambda v: f"{(int(v or 0)//10000):,}",
        "num": lambda v: f"{int(v):,}" if v not in (None, "") else "—",
        "wareki_to_ad": wareki_to_ad,
    }


def _refresh_cfg():
    """重新读配置（网页改过 config.yaml 后生效）。"""
    global CFG, PATHS
    CFG = cfgmod.load()
    PATHS = cfgmod.paths(CFG)
    SCHED.cfg = CFG
    return CFG


# ============================================================
# 心跳 + 错误兜底
#   前端每 4 秒打一次 /api/ping：连不上就说明本地服务已经退出，
#   页面顶部会亮红色横幅，而不是给用户看 "Failed to fetch"。
#   /api/* 下的任何异常一律返回 JSON，前端才解析得动。
# ============================================================
@app.get("/api/ping")
def api_ping():
    return jsonify({"ok": True, "ts": _now(), "pid": os.getpid()})


@app.post("/api/ingest")
def api_ingest():
    """v1.5.19：线上**收数**入口 —— 本地每轮抓完推一次，同事立刻能看到新房源，
    **不需要重新发布**（发布 = 容器重建 = 有 1~2 分钟空窗，能省则省）。

    安全：必须带对 `publish.ingest_token`（本机 config.yaml），否则 401。
    卫生：只收 `PROPERTY_COLUMNS` 里的列，PDF 三件套（pdf_path/pdf_url/absent_runs）
          一律丢弃 —— 勇哥明确要求 PDF 不上传。
    """
    _refresh_cfg()
    want = str(((CFG.get("publish") or {}).get("ingest_token") or "")).strip()
    got = str(request.headers.get("X-Publish-Token") or "").strip()
    if not want or got != want:
        return jsonify({"ok": False, "error": "令牌不对（401）"}), 401
    body = request.get_json(force=True, silent=True) or {}
    rows = body.get("rows")
    if not isinstance(rows, list):
        return jsonify({"ok": False, "error": "rows 必须是数组"}), 400
    clean = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("property_no"):
            continue
        clean.append({k: v for k, v in r.items()
                      if k in store_mod.PROPERTY_COLUMNS and k not in NEVER_UPLOAD_KEYS})
    try:
        n = STORE.upsert_many(clean, log=log)
    except Exception as e:                                    # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    total = STORE.conn.execute("SELECT COUNT(*) c FROM properties").fetchone()["c"]
    log(f"☁ 收到线上推送 {n} 条（库内共 {total} 条）")
    return jsonify({"ok": True, "upserted": n, "total": total})


@app.errorhandler(HTTPException)
def _handle_http_error(e: HTTPException):
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": f"{e.code} {e.name}",
                        "path": request.path}), e.code
    return e


@app.errorhandler(Exception)
def _handle_any_error(e: Exception):
    import traceback
    log(f"✗ 未处理异常 {type(e).__name__}: {e}")
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(traceback.format_exc() + "\n")
    except Exception:
        pass
    if request.path.startswith("/api/"):
        return jsonify({"status": "error",
                        "message": f"{type(e).__name__}: {e}"}), 500
    return ("<h2>服务内部错误</h2>"
            f"<p>{type(e).__name__}: {e}</p>"
            "<p>详细堆栈见 <code>data/logs/server.log</code>。</p>"), 500


# ============================================================
# 页面
# ============================================================
def _ward_counts(limit: int = 12) -> list:
    """在架房源按区分布（对外概览页用）。查不动就返回空列表，绝不连带整个页面 500。"""
    try:
        rows = STORE.conn.execute(
            "SELECT COALESCE(ward,'（未填）') w, COUNT(*) c FROM properties "
            "WHERE is_active=1 GROUP BY w ORDER BY c DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"ward": r["w"], "count": r["c"]} for r in rows]
    except Exception:                                         # noqa: BLE001
        return []


@app.get("/")
def index():
    _refresh_cfg()
    if PUBLIC:
        # v1.5.19：对外版概览 —— 与本地仪表盘同一套统计口径，但**没有**更新开关、
        # 实时日志、断点询问这些只有本机才有意义的东西，纯只读。
        return render_template(
            "overview_public.html",
            stats=STORE.stats(),
            changes=STORE.recent_changes(12),
            wards=_ward_counts(12),
            updated=STORE.stats().get("last_run") or "",
            data_updated_at=STORE.stats().get("data_updated_at") or "",
            today=datetime.now().strftime("%Y-%m-%d"),
        )
    return render_template(
        "index.html",
        cfg=CFG, paths={k: str(v) for k, v in PATHS.items()},
        stats=STORE.stats(), sched=SCHED.status(),
        coverage=STORE.online_coverage(),
        runs=STORE.recent_runs(10), changes=STORE.recent_changes(8),
        logs=list(LOGS)[-120:],
    )


@app.get("/search")
def search():
    """查询页：服务端只负责渲染外壳 + 下拉选项；实际查询走客户端 /api/query。"""
    _refresh_cfg()
    return render_template("search.html", opts=STORE.filter_options(),
                           today=datetime.now().strftime("%Y-%m-%d"))


# 详情页 / 对比页字段小词典：详情库 detail_json 实际是英文键，这里同时收录
# 英文键（主）与日文键（兼容老数据）。
# ⚠ v1.7.2 修订（勇哥 2026-09-16 反馈「后台的语言翻译成正常的」）：旧注释写的
#   "没收录的原样显示" **作废** —— has_map / unit_price_sqm / ward 就是这么以机器键
#   上了客户页面。现在未收录 = 隐藏 + 告警，并由 tools/verify_field_labels.py
#   拿真实库全量键（2638 行 × 35 键）断言覆盖率，缺一个即红。
JP2CN = {
    # 英文键（本机库实际使用的）
    "trade_type": "交易方式", "use_zone": "用途地域", "management_fee": "管理费（每月）",
    "repair_fund": "修缮积立金（每月）", "broker": "中介公司", "broker_tel": "中介电话",
    "public_status": "公开状态", "source_url": "来源链接",
    # v1.7.2 补录（此前漏收录 → 机器键直接上屏）
    "ward": "所在区", "unit_price_sqm": "㎡单价", "unit_price_tsubo": "坪单价",
    "has_photo": "照片", "has_floorplan": "户型图", "has_map": "位置图",
    # 日文键（兼容历史/外部数据）
    "管理費": "管理费（每月）", "修繕積立金": "修缮积立金（每月）", "構造": "构造",
    "用途地域": "用途地域", "現況": "现状", "引渡": "交房", "バルコニー": "阳台",
    "方角": "朝向", "総戸数": "总户数", "駐車場": "停车场", "交通": "交通",
    "セールスポイント": "卖点", "設備": "设备", "備考": "备注", "取引態様": "交易方式",
    "周辺環境": "周边环境",
    "地目": "地目", "接道状況": "接道状况", "土地権利": "土地权利", "都市計画": "城市规划",
    "建ぺい率": "建蔽率", "容積率": "容积率", "間取": "户型", "向き": "朝向",
    "築年月": "建成时间", "完成時期": "竣工时期", "引渡時期": "交付时间", "管理形態": "管理形态",
}

_CORE_KEYS = {
    "property_no", "address", "building_name", "price", "land_area", "exclusive_area",
    "building_area", "layout", "floor", "built_year_month", "line_station",
    "registration_date", "change_date", "first_seen_at", "last_seen_at", "image_count",
    "property_subtype", "kind", "ward", "previous_price", "unit_price_sqm",
    "unit_price_tsubo", "above_ground_floors", "pdf_path", "source_url",
    # 注：has_photo / has_floorplan / has_map 于 v1.7.2 从这里移出 —— 原来它们被当成
    #    "内部键"默默丢掉，详情页只剩页头三个图标；现在改为正常渲染成
    #    「照片 / 户型图 / 位置图 → 有·无」，信息更明确。
}

# v1.7.2：内部机器键 —— 抓取/存储专用，**永不显示**。
#   pdf_url/pdf_path 是文件路径（页面另有「打开本地 PDF」按钮），没有给人看的理由。
_INTERNAL_KEYS = {"pdf_url", "pdf_path", "absent_runs", "detail_json", "_ms_pdf", "__fp__"}

# v1.7.2：REINS 后台枚举 → 客户看得懂的说法。
#   取值清单来自真实库全量统计（2026-09-16 实枚举），不靠猜。
_ENUM_CN = {
    "trade_type": {
        "専任": "专任媒介", "専属": "专属专任媒介", "一般": "一般媒介（多家代理）",
        "代理": "代理（卖方代理）", "売主": "业主直售", "準専任": "准专任媒介",
        "オーナーチェンジ": "业主变更（带租约出售）", "専任媒介": "专任媒介",
    },
    "use_zone": {
        "商業": "商业地区", "一住": "一类居住地区", "二住": "二类居住地区",
        "一中": "一类中高层居住专用地区", "二中": "二类中高层居住专用地区",
        "一低": "一类低层居住专用地区", "二低": "二类低层居住专用地区",
        "近商": "邻近商业地区", "準工": "准工业地区", "工業": "工业地区",
        "工専": "工业专用地区", "準住": "准居住地区", "田園住": "田园居住地区",
        "定めなし": "无指定", "無指定": "无指定",
        "第一種低層住居専用地域": "一类低层居住专用地区",
        "第一種中高層住居専用地域": "一类中高层居住专用地区",
        "第一種住居地域": "一类居住地区", "第二種住居地域": "二类居住地区",
        "近隣商業地域": "邻近商业地区", "商業地域": "商业地区",
        "準工業地域": "准工业地区", "工業地域": "工业地区",
    },
    "public_status": {
        "公開中": "公开中", "申込あり": "已有申请", "一時停止": "暂停招募",
        "取引完了": "已成交", "売却済": "已售出",
    },
}

# v1.7.2：运行时统计"遇到了词典没收录的键"。它不该长期非空 ——
#   tools/verify_field_labels.py 会拿真实库全量键断言为 0，缺一个就红。
_MISSING_KEYS: set = set()


def _warn_missing(keys) -> None:
    if not keys:
        return
    _MISSING_KEYS.update(keys)
    try:
        print("[fieldmap] 未收录的字段键，已隐藏不显示，请补进词典：%s"
              % ", ".join(sorted(keys)))
    except Exception:
        pass


def _enum_cn(key: str, val) -> str | None:
    """REINS 枚举 → 中文。多行枚举（如「売主\\nオーナーチェンジ」）逐段翻译后拼接。"""
    m = _ENUM_CN.get(key)
    if not m:
        return None
    parts = [p.strip() for p in re.split(r"[\n/]", str(val)) if p.strip()]
    return " · ".join(m.get(p, p) for p in parts)


# v1.7.4：REINS 固定文本值（与字段名无关，见到即译）。
# 真实库实测：修繕積立金 25 条「確認中」、4 条「なし」，不译就是没翻译的机器值。
# 与 fieldmap.js 的 TEXT_CN 保持一致（两处必须同步，否则详情页/查询页显示不一致）。
_TEXT_CN = {
    "確認中": "确认中", "なし": "无", "未定": "未定", "不明": "不明",
    "有": "有", "無": "无", "－": "", "-": "",
}


def _text_cn(val) -> str | None:
    """固定文本值 → 中文；不在表里返回 None（让调用方继续走原逻辑）。"""
    return _TEXT_CN.get(str(val).strip())


def _money(v, suffix: str = " 円") -> str:
    """金额：千分位 + 单位；非数字原样返回。"""
    try:
        return f"{int(float(v)):,}{suffix}"
    except Exception:
        return str(v)


def _media_flags(row) -> dict:
    """v1.5.3：画/図/所 三个图标 + 真实照片张数（未知时 image_count 为 -1）。

    返回给前端的统一形态：
      photo  = 1/0（**有没有**照片，来自列表行图标，永远可靠）
      plan   = 1/0（有没有 間取図/図面）
      map    = 1/0（有没有 所在図/周辺地図）
      count  = 真实张数（int）；None = 还没抓到详情、未知（**不要显示成 0**）
    """
    r = row if isinstance(row, dict) else dict(row)
    try:
        cnt = int(r.get("image_count"))
    except Exception:
        cnt = -1
    return {
        "photo": 1 if (r.get("has_photo") or (cnt > 0)) else 0,
        "plan": 1 if r.get("has_floorplan") else 0,
        "map": 1 if r.get("has_map") else 0,
        "count": cnt if cnt >= 0 else None,
    }


_AGENCY_KEYS = {"broker", "broker_tel"}
def _is_agency(k):
    """v1.7.0：判断某 detail_json 原始键是否属于「中介/担当」类（一键隐藏用）。"""
    if k in _AGENCY_KEYS:
        return True
    import re as _re
    return bool(_re.search(r"取扱|担当|仲介|電話|メール|取引", k or ""))


def _detail_pairs(row) -> list[tuple[str, str, bool]]:
    """把一条房源整理成「标签 → 值」列表，给详情页的密集网格用（空的直接丢掉）。"""
    r = dict(row)

    def wan(v):
        return f"{(int(v) // 10000):,} 万円" if v not in (None, "") else ""

    def sq(v):
        return f"{v} ㎡" if v not in (None, "") else ""

    def unit(v, per="㎡"):
        """单价：小额用「円/㎡」（千分位），大额用「万円/㎡」。
        v1.7.2 修 bug：原来一律 int(v)//10000 → 5,632 円/㎡ 被显示成「0 万円/㎡」。"""
        if v in (None, "", 0):
            return ""
        try:
            n = int(float(v))
        except Exception:
            return ""
        if n <= 0:
            return ""
        return (f"{n:,} 円/{per}" if n < 10000
                else f"{n / 10000:,.1f} 万円/{per}")

    pairs = [
        ("物件番号", r.get("property_no"), False), ("種別", r.get("kind"), False),
        ("物件種目", r.get("property_subtype"), False), ("所在区", r.get("ward"), False),
        ("所在地", r.get("address"), False), ("建物名", r.get("building_name"), False),
        ("沿線・駅", r.get("line_station"), False),
        ("価格", wan(r.get("price")), False), ("前回価格", wan(r.get("previous_price")), False),
        ("㎡単価", unit(r.get("unit_price_sqm")), False),
        ("坪単価", unit(r.get("unit_price_tsubo"), "坪"), False),
        ("専有面積", sq(r.get("exclusive_area")), False), ("土地面積", sq(r.get("land_area")), False),
        ("建物面積", sq(r.get("building_area")), False),
        ("間取り", r.get("layout"), False), ("所在階", r.get("floor"), False),
        ("階建", r.get("above_ground_floors"), False), ("築年月", r.get("built_year_month"), False),
        ("画像数", (f"{r['image_count']} 枚" if isinstance(r.get("image_count"), int)
                    and r["image_count"] >= 0 else "未知（未抓详情）"), False),
        ("登録年月日", r.get("registration_date"), False), ("変更年月日", r.get("change_date"), False),
        ("初回取得", r.get("first_seen_at"), False), ("最終取得", r.get("last_seen_at"), False),
    ]
    try:
        extra = json.loads(r.get("detail_json") or "{}")
    except Exception:
        extra = {}
    missing: list[str] = []
    for k, v in extra.items():
        # ① 内部机器键 / 页头已单独渲染的核心列 —— 都不在这里显示
        if k.startswith("_") or k in _INTERNAL_KEYS or k in _CORE_KEYS or v in (None, ""):
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        v = str(v).strip()
        if v in ("", "-", "－", "None"):
            continue
        # ② 值本地化：有/无、金额、REINS 枚举中文
        if k in ("has_photo", "has_floorplan", "has_map"):
            v = "无" if v.lower() in ("0", "false", "none") else "有"
        elif k in ("repair_fund", "management_fee"):
            # 值有两种形态：纯数字（12345）→ 千分位+円；带单位文本（「30,500円」）→ 原样；
            # 固定文本（「確認中」/「なし」）→ 翻中文（v1.7.4）
            _tc = _text_cn(v)
            v = _tc if _tc is not None else _money(v)
        elif k in ("unit_price_sqm", "unit_price_tsubo"):
            v = _money(v, " 円/㎡" if k == "unit_price_sqm" else " 円/坪")
        else:
            _e = _enum_cn(k, v)
            if _e:
                v = _e
        # ③ 词典没收录 → 隐藏 + 记下来（不再是"原样显示原始键"）
        if k not in JP2CN:
            missing.append(k)
            continue
        pairs.append((JP2CN[k], v, _is_agency(k)))
    _warn_missing(missing)
    return [(a, b, c) for a, b, c in pairs if b not in (None, "")]


@app.get("/p/<property_no>")
def detail(property_no: str):
    _refresh_cfg()
    row = STORE.get_property(property_no)
    if row is None:
        return render_template("detail.html", row=None, no=property_no,
                               pairs=[], changes=[], snaps=[]), 404
    return render_template("detail.html", row=row, no=property_no,
                           pairs=_detail_pairs(row),
                           snaps=STORE.snapshots(property_no, 20),
                           changes=STORE.property_changes(property_no, 20))


# ============================================================
# AI 解读（v1.7.4 · PRD 09 需求 4：PDF → AI 提取 → 独立表 → 详情页展示）
# ============================================================
def _pdf_hash(property_no: str) -> str:
    """来源 PDF 指纹（sha256 前 16 位）。PDF 没变就不必重跑 AI。"""
    import hashlib
    pdf = PATHS["attachments"] / f"{property_no}.pdf"
    if not pdf.exists():
        return ""
    h = hashlib.sha256()
    with pdf.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


@app.get("/api/ai/<property_no>")
def api_ai_get(property_no: str):
    """详情页「AI 解读」卡片的数据源。没有结果时返回 status=none（前端显示空态引导）。"""
    row = STORE.get_ai_extraction(property_no)
    if row is None:
        return jsonify({"ok": True, "status": "none"})
    try:
        result = json.loads(row["result_json"] or "{}")
    except Exception:
        result = {}
    return jsonify({
        "ok": True,
        "status": row["status"],
        "result": result,
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "updated_at": row["updated_at"],
    })


@app.post("/api/ai/<property_no>")
def api_ai_put(property_no: str):
    """写入/覆盖某房源的 AI 解读结果（仅本机访问；供 AI 提取工具或人工提交）。

    body：{"result": {...}, "model": "...", "prompt_version": "...", "cost_ms": 123}
          或直接把 result 结构放在顶层。result 只写 ai_extractions 派生表。
    """
    if STORE.get_property(property_no) is None:
        return jsonify({"ok": False, "error": f"物件 {property_no} 不在本地库"}), 404
    data = request.get_json(force=True, silent=True) or {}
    result = data.get("result") if isinstance(data.get("result"), dict) else (
        data.get("result_json") if isinstance(data.get("result_json"), dict) else data)
    if not isinstance(result, dict) or not result:
        return jsonify({"ok": False, "error": "body 里没有可用的 result 结构"}), 400
    model = str(data.get("model") or "manual")
    STORE.upsert_ai_extraction(
        property_no, result, model=model,
        prompt_version=str(data.get("prompt_version") or ""),
        pdf_hash=_pdf_hash(property_no),
        cost_ms=data.get("cost_ms"),
    )
    log(f"[AI] 已写入 {property_no} 的 AI 解读（model={model}）")
    return jsonify({"ok": True, "property_no": property_no})


@app.post("/api/ai/<property_no>/run")
def api_ai_run(property_no: str):
    """用 config.yaml → ai.* 配置的模型，对该房源 PDF 现跑一次 AI 提取。

    没配 api_key 时返回友好 400（走「AI 代跑/手动提交」路径不受影响）。
    """
    _refresh_cfg()
    ai_cfg = CFG.get("ai") or {}
    if not (ai_cfg.get("api_key") or "").strip():
        return jsonify({"ok": False, "error":
                        "未配置 AI API Key（config.yaml → ai.api_key）。"
                        "申请火山方舟 Key 填入后即可一键重新生成。"}), 400
    if STORE.get_property(property_no) is None:
        return jsonify({"ok": False, "error": f"物件 {property_no} 不在本地库"}), 404
    from core import ai_extract
    try:
        result, meta = ai_extract.extract_property(property_no, PATHS, ai_cfg)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "本房源没有落盘 PDF，无法提取"}), 404
    except Exception as e:                       # 模型/网络错误不拖垮页面
        log(f"[AI] {property_no} 生成失败：{e}")
        return jsonify({"ok": False, "error": f"生成失败：{e}"}), 500
    STORE.upsert_ai_extraction(
        property_no, result, model=meta.get("model", ""),
        prompt_version=meta.get("prompt_version", ""),
        pdf_hash=_pdf_hash(property_no), cost_ms=meta.get("cost_ms"),
    )
    log(f"[AI] {property_no} 生成完成（{meta.get('cost_ms', 0)}ms，model={meta.get('model')}）")
    return jsonify({"ok": True, "property_no": property_no})


@app.get("/compare")
def compare():
    """房源对比页：横向比较 2–6 套房。

    选中的物件番号由查询页写入浏览器本地（localStorage），对比页读 ?nos=，
    没有参数时回退到本地记录。数据实时从本机库取，所以价格/面积永远是最新的。
    """
    _refresh_cfg()
    return render_template("compare.html")


@app.get("/runs")
def runs():
    _refresh_cfg()
    return render_template("runs.html", runs=STORE.recent_runs(80))


@app.get("/files")
def files():
    _refresh_cfg()
    def listing(sub: str, exts: tuple[str, ...]):
        d = PATHS[sub]
        if not d.exists():
            return []
        out = []
        for p in sorted(d.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in exts:
                out.append({
                    "name": p.name,
                    "dir": sub,
                    "size": f"{p.stat().st_size/1024:.1f} KB",
                    "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M"),
                })
        return out
    return render_template("files.html",
                           exports=listing("exports", (".xlsx",)),
                           daily=listing("daily", (".html",)),
                           pdfs=listing("attachments", (".pdf",)),
                           root=str(PATHS["root"]))


@app.get("/download/<dir>/<path:name>")
def download(dir: str, name: str):
    if dir not in ("exports", "daily", "attachments"):
        return "not allowed", 403
    return send_from_directory(PATHS[dir], name, as_attachment=False)


# ============================================================
# 账号管理 + 测试 / 真实下载
# ============================================================
def _session_info() -> dict:
    """登录会话文件的「新鲜度」（页面展示用）。

    只反映**最近一次登录 / 自动重登**的时间，不代表此刻一定还有效
    （REINS 的会话在服务端有有效期）。真正是否可用由抓取时的
    Auth.open_authed_page() 判定——过期会自动用本机账号重登一次。
    """
    sf = PATHS["session"]
    if not sf.exists():
        return {"exists": False, "mtime": "", "age_min": None}
    try:
        m = datetime.fromtimestamp(sf.stat().st_mtime)
        return {"exists": True, "mtime": m.strftime("%Y-%m-%d %H:%M"),
                "age_min": int((datetime.now() - m).total_seconds() // 60)}
    except Exception:
        return {"exists": True, "mtime": "", "age_min": None}


@app.get("/collect")
def collect():
    """数据抓取设置：把「模式 / 环境自检 / 账号 / 四步测试 / 更新设置」集中在一页。"""
    _refresh_cfg()
    return render_template("collect.html",
                           cfg=CFG, sched=SCHED.status(),
                           sess=_session_info(),
                           creds=creds_mod.status(PATHS["root"]),
                           test=dict(TEST_RESULT),
                           today=datetime.now().strftime("%Y-%m-%d"),
                           wards=OSAKA_WARDS,
                           stats=STORE.stats())


@app.get("/account")
def account():
    _refresh_cfg()
    return render_template("account.html",
                           creds=creds_mod.status(PATHS["root"]),
                           sess=_session_info(),
                           login_sel=CFG.get("selectors", {}).get("login", {}),
                           test=dict(TEST_RESULT))


def _is_localhost() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1")


@app.get("/api/credentials")
def api_credentials_get():
    c = creds_mod.status(PATHS["root"])
    if request.args.get("show") == "1" and c.get("saved") and _is_localhost():
        try:
            full = creds_mod.load(PATHS["root"])
            c["password"] = full["password"]
        except Exception as e:
            c["password_error"] = str(e)
    return jsonify(c)


@app.post("/api/credentials")
def api_credentials_post():
    body = request.get_json(force=True, silent=True) or {}
    mid = (body.get("member_id") or "").strip()
    pw = body.get("password") or ""
    if not mid or not pw:
        return jsonify({"status": "error", "message": "会员ID 与密码都不能为空"}), 400
    # 网站地址（登录地址）：新字段名 site_url，兼容旧字段名 site_name
    site_addr = (body.get("site_url") or body.get("site_name") or "").strip()
    res = creds_mod.save(PATHS["root"], mid, pw,
                         account_name=(body.get("account_name") or "").strip(),
                         site_name=site_addr)
    log(f"✓ 账号已加密保存（会员ID：{mid[:2]}***{mid[-2:]}）"
        + (f"，登录地址：{site_addr}" if site_addr else "，登录地址沿用配置默认值"))
    return jsonify({"status": "ok", "message": "账号已加密保存（仅存于本机）",
                    "algorithm": res.get("algorithm")})


@app.delete("/api/credentials")
def api_credentials_delete():
    creds_mod.clear(PATHS["root"])
    log("✓ 已清空已保存的账号")
    return jsonify({"status": "ok", "message": "已清空已保存的账号"})


def _bg(fn):
    threading.Thread(target=fn, daemon=True).start()


@app.post("/api/login/auto")
def api_login_auto():
    """测试登录：用本地账号自动填表登录，验证能否进会员页（不继续抓取）。"""
    cfg = _refresh_cfg()
    if (cfg.get("app", {}).get("mode") or "demo").lower() == "demo":
        return jsonify({"status": "demo",
                        "message": "当前是离线演示模式，不需要登录 REINS。\n"
                                   "要做真实登录/抓取，请先点仪表盘「切换到真实抓取」。"})
    if SCHED.status()["busy"]:
        return jsonify({"status": "busy", "message": "已有任务在跑，请稍后"}), 409

    def _do():
        TEST_RESULT["login"] = {"status": "running", "at": _now()}
        try:
            c = creds_mod.load(PATHS["root"])
            if not c or not c.get("member_id"):
                TEST_RESULT["login"] = {"status": "no_creds", "at": _now(),
                                        "message": "尚未录入账号，请到「账号管理」录入。"}
                log("✗ 测试登录：尚未录入账号")
                return
            ok = Auth(cfg, PATHS["session"]).auto_login(
                c["member_id"], c["password"], wait_seconds=45,
                log=log)        # 不传 headless → 用 config.browser.headless（默认有头；REINS 拦截无头）
            if ok:
                TEST_RESULT["login"] = {"status": "ok", "at": _now(),
                                        "message": "✓ 登录成功，会话已保存"}
                log("✓ 测试登录成功，会话已保存")
            else:
                TEST_RESULT["login"] = {"status": "fail", "at": _now(),
                                        "message": "✗ 自动登录未成功（可能需要验证码，"
                                                   "请改用「手工登录」）"}
                log("✗ 测试登录失败（可能需验证码）")
        except Exception as e:
            TEST_RESULT["login"] = {"status": "error", "at": _now(),
                                    "message": "✗ " + friendly_error(e)}
            log(f"✗ 测试登录异常：{friendly_error(e)}")

    _bg(_do)
    return jsonify({"status": "started", "message": "已开始测试登录（最多等 30 秒）"})


@app.post("/api/query/test")
def api_query_test():
    """测试查询：登录后跑 1 个最小范围查询，返回条数 + 样本（不落库）。"""
    cfg = _refresh_cfg()
    if (cfg.get("app", {}).get("mode") or "demo").lower() == "demo":
        return jsonify({"status": "demo",
                        "message": "当前是离线演示模式，无需真实查询。\n"
                                   "切到真实抓取后可测试查询。"})
    if SCHED.status()["busy"]:
        return jsonify({"status": "busy", "message": "已有任务在跑，请稍后"}), 409

    def _do():
        TEST_RESULT["query"] = {"status": "running", "at": _now()}
        try:
            res = probe_query(STORE, cfg, limit=5)
            TEST_RESULT["query"] = {"status": "ok", "at": _now(), **res}
            log(f"✓ 测试查询：{res['round']} → 结果 {res['total']}，"
                f"样本 {res['count']} 条")
        except Exception as e:
            TEST_RESULT["query"] = {"status": "error", "at": _now(),
                                    "message": f"✗ {type(e).__name__}: {e}"}
            log(f"✗ 测试查询异常：{e}")

    _bg(_do)
    return jsonify({"status": "started", "message": "已开始测试查询（登录 + 单轮最小查询）"})


@app.post("/api/download/test")
def api_download_test():
    """测试下载数据：真实下载但限小量（最多 3 条 + PDF），落库但轻量。"""
    cfg = _refresh_cfg()
    if (cfg.get("app", {}).get("mode") or "demo").lower() == "demo":
        return jsonify({"status": "demo",
                        "message": "当前是离线演示模式，无需真实下载。\n"
                                   "切到真实抓取后可测试下载。"})
    if SCHED.status()["busy"]:
        return jsonify({"status": "busy", "message": "已有任务在跑，请稍后"}), 409
    _bg(lambda: _run_download("test-download", trial=True,
                              key="download_test", label="测试下载数据"))
    return jsonify({"status": "started",
                    "message": "已开始测试下载（最轻量：1 轮 / 最多 3 条 + PDF）"})


@app.post("/api/download/real")
def api_download_real():
    """真实下载数据：按配置全量跑一轮，写入正式库。"""
    cfg = _refresh_cfg()
    if (cfg.get("app", {}).get("mode") or "demo").lower() == "demo":
        return jsonify({"status": "demo",
                        "message": "当前是离线演示模式，无法真实下载。\n"
                                   "切到真实抓取后可真实下载。"})
    if SCHED.status()["busy"]:
        return jsonify({"status": "busy", "message": "已有任务在跑，请稍后"}), 409
    _bg(lambda: _run_download("real-download", trial=False,
                              key="download_real", label="真实下载数据"))
    return jsonify({"status": "started", "message": "已开始真实下载（按配置全量跑一轮）"})


def _run_download(trigger: str, trial: bool, key: str, label: str) -> None:
    TEST_RESULT[key] = {"status": "running", "at": _now()}
    res = SCHED.trigger_manual(trigger=trigger, trial=trial)
    TEST_RESULT[key] = {"status": res.get("status", "ok"), "at": _now(),
                        "stats": {k: res.get(k) for k in
                                  ("scanned", "fetched", "new", "changed",
                                   "pdf_saved", "source")},
                        "errors": res.get("errors", [])[:3]}
    log(f"{'✓' if res.get('status')=='ok' else '✗'} {label}："
        f"落库 {res.get('fetched')} / 新盘 {res.get('new')} / 变更 {res.get('changed')}")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# API
# ============================================================
@app.get("/api/status")
def api_status():
    _refresh_cfg()
    return jsonify({"sched": SCHED.status(), "stats": STORE.stats(),
                    "busy": SCHED.status()["busy"], "test": dict(TEST_RESULT)})


@app.get("/api/phase")
def api_phase():
    """v1.5.15：实时阶段进度（前端 .phaser 面板轮询）。

    阶段标签 / 分组 / 待处理估算(need_detail/need_pdf) 来自 crawl_state；
    实时扫描/详情/PDF 计数(scanned/fetched/pdf_saved) 来自 runs 表的运行中行
    （progress_run 每 flush 都写，是现场最可靠的数字源；crawl_state 不重复记）。
    """
    _refresh_cfg()
    cs = STORE.load_crawl_state() or {}
    busy = bool(SCHED.status().get("busy"))
    # 实时计数：runs 表里 status='running' 的最近一行
    scanned = fetched = pdf_saved = 0
    try:
        _r = STORE.conn.execute(
            "SELECT scanned, fetched, pdf_saved FROM runs "
            "WHERE status='running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if _r:
            scanned = int(_r[0] or 0)
            fetched = int(_r[1] or 0)
            pdf_saved = int(_r[2] or 0)
    except Exception:                                # noqa: BLE001
        pass
    # v1.7.0：概览「种目×登录/变更」12 行矩阵（分母来自 online_stats，分子来自 properties）
    groups = []
    _rid = cs.get("run_id") if cs else None
    if _rid:
        try:
            groups = STORE.phase_groups(_rid)
        except Exception:                       # noqa: BLE001
            groups = []
    return jsonify({
        "running": busy,
        "phase": cs.get("phase"),
        "run_id": cs.get("run_id"),
        "target_date": cs.get("target_date"),
        "group_index": cs.get("group_index"),
        "group_label": cs.get("group_label"),
        "axis": cs.get("axis"),
        "page": cs.get("page"),
        "scanned": scanned,
        "fetched": fetched,
        "pdf_saved": pdf_saved,
        "need_detail": cs.get("need_detail") or 0,
        "need_pdf": cs.get("need_pdf") or 0,
        "groups": groups,
        "as_of": cs.get("updated_at"),
    })


@app.get("/api/logs")
def api_logs():
    since = int(request.args.get("since", 0) or 0)
    return jsonify({"total": len(LOGS), "lines": list(LOGS)[since:]})


MAX_COMPARE = 6          # 一次最多横向对比几套（前后端同一个上限）


def _row_payload(r) -> dict:
    """把一条房源行整理成前端要的形状：解析 detail_json、拼出 PDF 链接。"""
    d = dict(r)
    try:
        d["detail"] = json.loads(d.get("detail_json") or "{}")
    except Exception:
        d["detail"] = {}
    # v1.4.0：两阶段下载后，列表可能先落库、详情还没补。前端靠这个字段做视觉区分
    # （已下详情 vs 只有列表/待补），否则用户分不清"没下下来"和"下了但没详情"。
    d["has_detail"] = bool(d.get("detail_json"))
    # v1.7.4 R8：取引態様（売主 / 専任 / 専属 / 代理 / 一般）—— 客户点名"哪套是卖主要看得见"。
    # 值可能是多行枚举（如「売主\nオーナーチェンジ」），徽章只取**第一行**，否则标签过长撑破行。
    _tt = str((d.get("detail") or {}).get("trade_type") or "").strip()
    d["trade_type"] = _tt.splitlines()[0].strip() if _tt else ""
    # v1.5.3：画/図/所 三个图标 + 真实照片张数（count=None 表示还没抓到详情、未知）
    d["media"] = _media_flags(d)
    if d.get("pdf_path"):
        d["pdf_url"] = f"/download/attachments/{os.path.basename(d['pdf_path'])}"
    else:
        d["pdf_url"] = None
    d.pop("detail_json", None)
    return d


@app.get("/api/compare")
def api_compare():
    """对比页数据源：nos=番号1,番号2,...（最多 6 个，按传入顺序返回）。"""
    _refresh_cfg()
    raw = (request.args.get("nos") or "").strip()
    nos: list[str] = []
    for x in raw.split(","):
        x = x.strip()
        if x and x not in nos:
            nos.append(x)
    over = max(0, len(nos) - MAX_COMPARE)
    nos = nos[:MAX_COMPARE]

    got = STORE.get_many(nos)
    rows = [_row_payload(got[n]) for n in nos if n in got]
    missing = [n for n in nos if n not in got]
    return jsonify({"count": len(rows), "max": MAX_COMPARE, "over": over,
                    "missing": missing, "rows": rows})


@app.get("/api/query")
def api_query():
    """查询本地库，默认按「当天」过滤；返回房源行 + 解析后的详情 + PDF 链接。

    前端查询页点「查询」时调用；日期可选任意一天（date=YYYY-MM-DD），
    date 为空则默认当天。其余条件（关键词/区/种目/价格/面积/是否带PDF）可叠加。

    物件種目支持多选：subtype 参数可以重复出现（对象种目的两级目录叶子 key，
    如 subtype=house_new&subtype=apt_old），互相关联 OR 后一起过滤。
    区（ward）/ 取引態様（trade_type）同样支持多选：同名参数重复出现即多值 OR，
    如 ward=北区&ward=中央区、trade_type=売主&trade_type=専任（v1.7.6）。
    """
    _refresh_cfg()
    f = {
        "q": (request.args.get("q") or "").strip(),
        # v1.7.6：区支持多选（OR）—— 前端每个选中区重复传 ward 参数，后端收成列表。
        # 单值的 ward= 也兼容（退回成单元素列表）。
        "wards": [w.strip() for w in request.args.getlist("ward") if w.strip()],
        "ward": request.args.get("ward", ""),
        "kind": request.args.get("kind", ""),
        "subtype": request.args.get("subtype", ""),
        "subtypes": [s.strip() for s in request.args.getlist("subtype") if s.strip()],
        "price_min": request.args.get("price_min", ""),
        "price_max": request.args.get("price_max", ""),
        "area_min": request.args.get("area_min", ""),
        "area_max": request.args.get("area_max", ""),
        "has_pdf": request.args.get("has_pdf", ""),
        # v1.7.4 R8：取引態様（売主 / 専任 / 専属 / 代理 / 一般）—— 客户点名要能筛出「哪些是卖主」。
        # v1.7.6：支持多选（OR）—— 前端每个选中值重复传 trade_type 参数，后端收成列表。
        "trade_types": [t.strip() for t in request.args.getlist("trade_type") if t.strip()],
        "trade_type": (request.args.get("trade_type") or "").strip(),
        "date": request.args.get("date", ""),
        # v1.7.0：日期时间段（起~止）
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
        "order": request.args.get("order", ""),
        # R20：排序键 + 方向（服务端 SQL 排序；空值排最后；番号稳定次级键）。
        # 前端默认 updated/desc，与旧 order=date_desc 行为一致。
        "sort": (request.args.get("sort") or "updated").strip().lower(),
        "sort_dir": (request.args.get("sort_dir") or "desc").strip().lower(),
        # v1.5.4：日期口径 —— registration=平台登録日 / change=平台変更日 / any=两者任一。
        # 用户要求：日期按「平台新建或变更日」算，不是我方下载日。
        "date_caliber": (request.args.get("date_caliber") or "any").strip().lower(),
    }
    # v1.7.0：日期段优先 —— 起止任一带值就走范围，清掉单日期避免双重过滤
    # v1.7.4 修静默 BUG：旧代码清完 f["date"] 后，下面的「默认填今天」又把它填回来，
    #   于是 store.search() 里 date_from/date_to 与 date=今天 **同时生效**（AND），
    #   查「09-11~09-17」只剩「09-17 当天」的量 —— 今天还没抓到数据时就是 **0 条**，
    #   页面还显示「日期 2026-09-17」（单日），用户完全看不出自己选的区间被吞了。
    #   → 区间模式必须**不填**默认日期。
    _has_range = bool(f.get("date_from") or f.get("date_to"))
    if _has_range:
        f["date"] = ""
    # 默认查询当天数据。
    # v1.4.0：date=all 表示**不按日期过滤** —— 概览 KPI「本地房源总数」点进来要看全库，
    # 而不是只有今天那一屏（否则点"总数 3000"却只看到今天的几十条，很困惑）。
    if f["date"] == "all":
        f["date"] = ""
    elif not f["date"] and not _has_range:
        f["date"] = datetime.now().strftime("%Y-%m-%d")
    page = max(1, int(request.args.get("page", 1) or 1))
    limit = max(1, min(200, int(request.args.get("limit", 50) or 50)))
    rows, total = STORE.search(f, limit=limit, offset=(page - 1) * limit)

    # 每行附一个「状态标」：一眼看出这套是本轮新入库（新增）、还是库中已有但今天变了（变更）。
    # 没有变更记录 = 空（已在本机库里、本次没变化）。前端用 row.tag_new / row.tag_changed 渲染。
    #
    # 【为什么「变更」优先于「新增」·非显然】REINS 自带「変更前価格 / 変更年月日」，
    # 于是一套房可能"我方今天才第一次抓到，但平台其实早就改过价"——它同时有 new 与
    # price_down/price_up 两类记录。业务上销售关心的是"这套房相对平台有没有变过"，
    # 且用户已明确要求这种情况算「变更」而非「新增」（PRD 6.11 / 6.12），
    # 所以这里让"任何一个变更类记录"都能把标签压成 changed。
    CHANGED_TYPES = ("modified", "price_down", "price_up")
    tags = STORE.change_types_on(f["date"])
    out = []
    for r in rows:
        p = _row_payload(r)
        cts = tags.get(r["property_no"]) or set()
        if cts & set(CHANGED_TYPES):
            p["tag"] = "changed"
        elif cts:
            p["tag"] = "new"
        else:
            p["tag"] = ""
        out.append(p)
    # library：附带本机库概况（最近有数据的日期 / 今天条数 / 上轮运行状态），
    # 让查询页在"今天一条都没有"时能给出可操作的提示，而不是一片空白。
    return jsonify({"date": f["date"], "total": total, "page": page,
                    "pages": max(1, (total + limit - 1) // limit), "rows": out,
                    "library": STORE.library_info()})


@app.get("/api/library_dates")
def api_library_dates():
    """本机库里「哪些日期有数据、各多少条」——查询页选日期 / 提示用。"""
    _refresh_cfg()
    return jsonify({"dates": STORE.available_dates(120),
                    "library": STORE.library_info()})


@app.get("/api/overview")
def api_overview():
    """概览页定时刷新用：返回 KPI / 自动更新状态 / 最近变更 / 最近运行。

    v1.4.0：顺带返回 sync（同步状态 + 详情层进度）与未读通知，
    前端每几秒轮询这一个接口就够，不必再单独打 /api/sync。
    """
    _refresh_cfg()
    sync = STORE.sync_status()
    _today = datetime.now().strftime("%Y-%m-%d")
    sync["detail"] = STORE.detail_progress(_today)   # 面板默认按今天算分母（用户 09-15：分母应是当天数据）
    sync["detail_all"] = STORE.detail_progress()      # 累计（历史存量，仅供参考）
    sync["pdf"] = STORE.pdf_progress(_today)          # v1.5.13：PDF 命中率分母=有図面图标（当天）
    sync["pdf_all"] = STORE.pdf_progress()            # 累计（有図面图标的全库子集）
    return jsonify({
        "stats": STORE.stats(),
        "coverage": STORE.online_coverage(),      # 线上覆盖（v1.2.7）
        "sync": sync,                             # v1.4.0：同步状态
        "notifications": STORE.list_notifications(5, unread_only=True),
        "sched": SCHED.status(),
        "runs": [dict(r) for r in STORE.recent_runs(10)],
        "changes": [dict(c) for c in STORE.recent_changes(8)],
        "decisions": STORE.pending_decisions(3),   # v1.5.2：待你决定的询问（15 秒窗口）
        "reconcile": STORE.reconcile(),            # D5（v1.8.2）：概览对账六数
    })


@app.post("/api/decide")
def api_decide():
    """v1.5.2：回答一次「问人」（断点续传 / 重新下载 / 停止…）。

    body: {id: <decision id>, action: "resume" | "restart" | "skip" | "retry" | ...}
    点了就立刻按这个动作办；超时没点则由抓取线程自己写默认动作（不卡住）。
    """
    _refresh_cfg()
    body = request.get_json(force=True, silent=True) or {}
    did = body.get("id")
    action = (body.get("action") or "").strip()
    if not did or not action:
        return jsonify({"status": "error", "message": "缺少 id / action"}), 400
    ok = STORE.decide(int(did), action)
    # 「从断点继续 / 重新下载整轮」= 立刻开一轮；其余动作只记录，抓取线程自己读
    if ok and action in ("resume", "restart"):
        if SCHED.status()["busy"]:
            return jsonify({"status": "busy", "message": "已有任务在跑"})
        threading.Thread(target=SCHED.trigger_manual,
                         kwargs={"trigger": "manual", "resume": (action == "resume")},
                         daemon=True).start()
        return jsonify({"status": "started",
                        "message": "已从断点继续" if action == "resume" else "已重新下载整轮"})
    return jsonify({"status": "ok" if ok else "stale"})


@app.get("/api/sync")
def api_sync():
    """v1.4.0：REINS 检索页(线上) vs 本机后台 的同步状态。

    口径（与 PRD 07 一致）：
      online_total = 当天各组「結果 N 件」之和 → 列表层分母
      local_total  = 本机库落在这些组種目范围内的累计条数
      detail.with_detail / without_detail = 已抓到详情 / 只有列表（等阶段2补）
      running      = 当前正在跑的轮次及其实时进度
    """
    _refresh_cfg()
    sync = STORE.sync_status()
    _today = datetime.now().strftime("%Y-%m-%d")
    sync["detail"] = STORE.detail_progress(_today)   # 面板默认按今天算分母（用户 09-15：分母应是当天数据）
    sync["detail_all"] = STORE.detail_progress()      # 累计（历史存量，仅供参考）
    sync["pdf"] = STORE.pdf_progress(_today)          # v1.5.13：PDF 命中率分母=有図面图标（当天）
    sync["pdf_all"] = STORE.pdf_progress()            # 累计（有図面图标的全库子集）
    return jsonify(sync)


@app.get("/api/notifications")
def api_notifications():
    """v1.4.0：通知列表（默认只返回未读；unread=0 返回全部）。"""
    unread = request.args.get("unread", "1") == "1"
    return jsonify({"items": STORE.list_notifications(20, unread_only=unread),
                    "unread": STORE.unread_notifications()})


@app.post("/api/notifications/read")
def api_notifications_read():
    """v1.4.0：通知标已读（不传 ids = 全部标已读）。"""
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("ids")
    STORE.mark_notifications_read(ids if isinstance(ids, list) and ids else None)
    return jsonify({"status": "ok", "unread": STORE.unread_notifications()})


@app.get("/api/history/<property_no>")
def api_property_history(property_no: str):
    """v1.4.0：某房源的历轮快照 + 与上一轮的差异。

    差异**一律本地比对**（property_history 表），不回 REINS 查 —— 省请求、不碰风控。
    这是"本地快照"相对"回线上查"的核心价值：想算哪两轮就算哪两轮。
    """
    rows = [dict(r) for r in STORE.history_for(property_no, 30)]
    diffs: list[dict] = []
    for i in range(len(rows) - 1):
        new_r, old_r = rows[i], rows[i + 1]
        if (new_r.get("price") or 0) != (old_r.get("price") or 0):
            diffs.append({"run_id": new_r.get("run_id"), "field": "price",
                          "old": old_r.get("price"), "new": new_r.get("price"),
                          "at": new_r.get("captured_at")})
        if bool(new_r.get("has_detail")) != bool(old_r.get("has_detail")):
            diffs.append({"run_id": new_r.get("run_id"), "field": "has_detail",
                          "old": old_r.get("has_detail"), "new": new_r.get("has_detail"),
                          "at": new_r.get("captured_at")})
    return jsonify({"property_no": property_no, "count": len(rows),
                    "history": rows, "diffs": diffs})


@app.post("/api/run")
def api_run():
    """手动更新：立刻按当前时间节点跑一轮（全部条件）。
    body.trial=true → 试跑：只跑 1 个查询轮、最多 3 条详情。"""
    _refresh_cfg()
    if SCHED.status()["busy"]:
        return jsonify({"status": "busy", "message": "已有任务在跑"}), 409
    _body = request.get_json(force=True, silent=True) or {}
    trial = bool(_body.get("trial"))
    resume = bool(_body.get("resume"))     # v1.5.2：断点续传（从上次中断的房型接着跑）
    t = threading.Thread(target=SCHED.trigger_manual,
                         kwargs={"trigger": "manual", "trial": trial, "resume": resume},
                         daemon=True)
    t.start()
    return jsonify({"status": "started",
                    "message": "已开始试跑（最轻量，1 轮 / 最多 3 条）" if trial
                               else "已开始手动更新（全部数据）"})


# =====================================================================
# v1.5.6：上传到线上（把本机数据推到 osaka-house-search.app.workbuddy.link）
#   勇哥 2026-09-15 要求：设置页里要有两个选项
#     ① 自动完成 —— 每轮抓取正常收尾后自动增量推送
#     ② 手动方式 —— 点按钮全量重传
#   ⚠ 只传详情内容，不传 PDF（publisher.py 里 NEVER_UPLOAD 已排除）
# =====================================================================
_PUB = {"busy": False, "log": [], "result": None, "mode": None}


def _publish_worker(mode: str):
    _PUB.update({"busy": True, "log": [], "result": None, "mode": mode})

    def say(*a, **_k):
        _PUB["log"].append(" ".join(str(x) for x in a))
        if len(_PUB["log"]) > 300:
            _PUB["log"] = _PUB["log"][-300:]

    con = None
    try:
        cfg = cfgmod.load()
        from core.publisher import publish
        import sqlite3
        con = sqlite3.connect(str(PATHS["db"]))
        con.row_factory = sqlite3.Row
        _PUB["result"] = publish(cfg, con, mode=mode, log=say)
    except Exception as e:                                       # noqa: BLE001
        _PUB["result"] = {"ok": False, "errors": [type(e).__name__ + ": " + str(e)]}
        say("✗ " + type(e).__name__ + ": " + str(e))
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:                                        # noqa: BLE001
            pass
        _PUB["busy"] = False


@app.post("/api/publish")
def api_publish():
    """手动上传：mode=full 全量重传 / mode=incr 增量推送。"""
    if _PUB["busy"]:
        return jsonify({"status": "busy", "message": "已有上传在跑"}), 409
    _body = request.get_json(force=True, silent=True) or {}
    mode = "full" if str(_body.get("mode") or "full") == "full" else "incr"
    threading.Thread(target=_publish_worker, args=(mode,), daemon=True).start()
    return jsonify({"status": "started", "mode": mode,
                    "message": "已开始全量重传" if mode == "full" else "已开始增量推送"})


@app.get("/api/publish/status")
def api_publish_status():
    """上传进度 + 上次上传状态（publisher 的 publish_state 表）。"""
    cfg = _refresh_cfg()
    st = {}
    try:
        from core.publisher import get_state
        import sqlite3
        con = sqlite3.connect(str(PATHS["db"]))
        con.row_factory = sqlite3.Row
        st = get_state(con)
        con.close()
    except Exception as e:                                       # noqa: BLE001
        st = {"last_error": str(e)}
    pub = (cfg.get("publish") or {})
    return jsonify({
        "busy": _PUB["busy"], "mode": _PUB["mode"],
        "log": _PUB["log"][-40:], "result": _PUB["result"],
        "state": st,
        "config": {"enabled": bool(pub.get("enabled", True)),
                   "mode": pub.get("mode", "auto"),
                   "endpoint": pub.get("endpoint", ""),
                   "month_scope": pub.get("month_scope", ""),
                   "scope_caliber": pub.get("scope_caliber", "download")},
    })


@app.post("/api/publish/settings")
def api_publish_settings():
    """保存上传设置（自动 / 手动 + 线上地址 + 月份范围）。"""
    cfg = _refresh_cfg()
    body = request.get_json(force=True, silent=True) or {}
    p = cfg.setdefault("publish", {})
    if "enabled" in body:
        p["enabled"] = bool(body["enabled"])
    if "mode" in body and str(body["mode"]) in ("auto", "manual"):
        p["mode"] = str(body["mode"])
    for k in ("endpoint", "token", "month_scope", "scope_caliber"):
        if k in body:
            p[k] = str(body[k] or "").strip()
    if "scope_caliber" in p and p["scope_caliber"] not in ("download", "platform"):
        p["scope_caliber"] = "download"
    cfgmod.save(cfg)
    log("上传设置已保存：mode=%s endpoint=%s 月份=%s(%s)"
        % (p.get("mode"), p.get("endpoint"), p.get("month_scope"),
           p.get("scope_caliber")))
    return jsonify({"status": "ok", "config": {
        "enabled": p.get("enabled"), "mode": p.get("mode"),
        "endpoint": p.get("endpoint"), "month_scope": p.get("month_scope"),
        "scope_caliber": p.get("scope_caliber")}})


@app.post("/api/env")
def api_env():
    """环境自检：不连 REINS，只验本机跑真实抓取的条件是否具备。"""
    cfg = _refresh_cfg()
    mode = (cfg.get("app", {}).get("mode") or "demo").lower()
    items: list[dict] = []

    # ① Playwright 库
    try:
        import playwright
        items.append({"name": "Playwright 库", "ok": True,
                      "detail": f"已安装 {getattr(playwright, '__version__', '')}".strip()})
    except BaseException as e:                                   # noqa: BLE001
        items.append({"name": "Playwright 库", "ok": False,
                      "detail": f"未安装 → 执行：pip install playwright（{e}）"})

    # ② Edge 浏览器能否被自动驱动
    try:
        from core.auth import _sync_playwright
        with _sync_playwright() as p:
            b = p.chromium.launch(channel="msedge", headless=True)
            ver = b.version
            b.close()
        items.append({"name": "Edge 浏览器（可被自动驱动）", "ok": True,
                      "detail": f"版本 {ver}"})
    except BaseException as e:                                   # noqa: BLE001
        items.append({"name": "Edge 浏览器（可被自动驱动）", "ok": False,
                      "detail": f"{type(e).__name__}: {str(e)[:140]}"})

    # ③ 登录会话文件
    sf = PATHS["session"]
    if sf.exists():
        items.append({"name": "登录会话文件", "ok": True,
                      "detail": f"{sf.name} 已存在（{sf.stat().st_size} 字节）"})
    else:
        items.append({"name": "登录会话文件", "ok": False,
                      "detail": "未创建 —— 需点一次「重新登录」手工登录 REINS"})

    ready = all(i["ok"] for i in items)
    msg = ("✓ 本机已具备真实抓取条件" if ready else
           "✗ 还差条件，按下面逐条处理（详见每行说明）")
    return jsonify({"status": "ok", "mode": mode, "ready": ready,
                    "message": msg, "items": items})


@app.post("/api/schedule")
def api_schedule():
    """保存三种更新方式的设置。"""
    cfg = _refresh_cfg()
    body = request.get_json(force=True, silent=True) or {}
    s = cfg.setdefault("schedule", {})
    s["mode"] = "random"        # v1.2.0：统一「随机间隔·分钟制」，界面不再提供「定时」
    if "interval_hours" in body:
        try:
            s["interval_hours"] = float(body["interval_hours"])
        except Exception:
            pass
    for k in ("random_min_hours", "random_max_hours",
              "random_min_minutes", "random_max_minutes"):
        if k in body:
            try:
                s[k] = float(body[k])
            except Exception:
                pass
    # 分钟制下限保护：低于 5 分钟一律夹到 5（避免高频轰炸 REINS）
    for k in ("random_min_minutes", "random_max_minutes"):
        if k in s and s[k] is not None:
            try:
                s[k] = max(5.0, float(s[k]))
            except Exception:
                pass
    if "window" in body and isinstance(body["window"], dict):
        s["window"] = {"start": body["window"].get("start", "07:00"),
                       "end": body["window"].get("end", "22:00")}
    if "enabled" in body:
        s["enabled"] = bool(body["enabled"])
    cfgmod.save(cfg)
    SCHED.cfg = cfg
    log(f"更新设置已保存：{SCHED.describe()}")

    # enabled 变化时同步启停线程
    if s.get("enabled") and not SCHED.running:
        SCHED.start()
    elif not s.get("enabled") and SCHED.running:
        SCHED.stop()
    return jsonify({"status": "ok", "sched": SCHED.status()})


@app.post("/api/crawl/settings")
def api_crawl_settings():
    """D7（v1.8.2）：保存抓取轮次计划开关 —— 全期間主轮 / 前一天补齐。

    复用 _refresh_cfg() + cfgmod.save(cfg) 模式（与 /api/schedule 一致）。
    只动 config.crawl 下白名单键，不触碰 schedule / 凭据。
    """
    cfg = _refresh_cfg()
    body = request.get_json(force=True, silent=True) or {}
    cr = cfg.setdefault("crawl", {})
    if "main_round_enabled" in body:
        cr["main_round_enabled"] = bool(body["main_round_enabled"])
    if "sync_dates_backfill_prev_day" in body:
        cr["sync_dates_backfill_prev_day"] = bool(body["sync_dates_backfill_prev_day"])
    cfgmod.save(cfg)
    log(f"抓取计划开关已保存：全期間主轮={cr.get('main_round_enabled')} "
        f"前日补齐={cr.get('sync_dates_backfill_prev_day')}")
    return jsonify({"status": "ok", "crawl": cr})


@app.post("/api/login")
def api_login():
    """打开浏览器窗口手工登录 REINS，成功后保存会话。"""
    cfg = _refresh_cfg()
    if (cfg.get("app", {}).get("mode") or "demo").lower() == "demo":
        return jsonify({"status": "demo",
                        "message": "当前是「离线演示模式」，不需要登录 REINS。\n\n"
                                   "要做真实抓取，请点本页左侧同一行里的"
                                   "「切换到真实抓取」按钮（在「重新登录」右边），"
                                   "然后按顺序：\n"
                                   "① 环境自检 → ② 重新登录（手工输一次账号密码）"
                                   " → ③ 登录/查询自检 → ④ 试跑。"})

    def _do():
        try:
            ok = Auth(cfg, PATHS["session"]).manual_login(wait_seconds=300)
            log("✓ 登录成功，会话已保存" if ok else "✗ 未检测到登录成功，请重试")
        except Exception as e:
            log(f"✗ 登录过程出错：{friendly_error(e)}")

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"status": "started",
                    "message": "已打开浏览器窗口，请手工登录（登录成功后自动保存）"})


@app.post("/api/login-test")
def api_login_test():
    """登录与查询自检：验证"能不能正常登录、能不能查到"。"""
    cfg = _refresh_cfg()
    mode = (cfg.get("app", {}).get("mode") or "demo").lower()
    if mode == "demo":
        # 演示模式：不连 REINS，直接验证本地链路（库可读 + 有数据）
        n = STORE.stats()["total"]
        return jsonify({"status": "ok", "mode": "demo",
                        "message": f"演示模式自检通过：本地库可读，当前 {n} 条房源。"
                                   f"（真实登录请把 app.mode 改为 live）"})
    try:
        from core.auth import SessionExpired, _sync_playwright
        auth = Auth(cfg, PATHS["session"])
        with _sync_playwright() as p:
            # 有头模式：REINS 会拦截无头浏览器
            b = auth.launch(p, headless=bool(cfg.get("browser", {}).get("headless", False)))
            ctx = auth.new_context(b)
            page = ctx.new_page()
            page.goto(cfg["site"]["search_url"])
            page.wait_for_load_state("networkidle", timeout=25000)
            ok = auth.looks_logged_in(page)
            b.close()
        if ok:
            return jsonify({"status": "ok", "mode": "live",
                            "message": "✓ 登录有效，已能打开検索条件入力页"})
        return jsonify({"status": "expired", "mode": "live",
                        "message": "✗ 会话失效：请点「重新登录」手工登录一次"})
    except SessionExpired as e:
        return jsonify({"status": "expired", "message": f"✗ 会话失效：{e}"})
    except BaseException as e:                                   # noqa: BLE001
        return jsonify({"status": "error",
                        "message": "✗ " + friendly_error(e)[:300]})


@app.post("/api/mode")
def api_mode():
    """切换 demo / live 模式。"""
    cfg = _refresh_cfg()
    body = request.get_json(force=True, silent=True) or {}
    mode = body.get("mode", "demo")
    if mode not in ("demo", "live"):
        return jsonify({"status": "error", "message": "mode 只能是 demo 或 live"}), 400
    cfg.setdefault("app", {})["mode"] = mode
    cfgmod.save(cfg)
    _refresh_cfg()
    log(f"模式已切换为：{'真实抓取' if mode == 'live' else '离线演示'}")
    return jsonify({"status": "ok", "mode": mode})


OSAKA_WARDS = ["北区", "都島区", "福島区", "此花区", "中央区", "西区", "港区", "大正区",
               "天王寺区", "浪速区", "西淀川区", "淀川区", "東淀川区", "東成区", "生野区",
               "旭区", "城東区", "鶴見区", "阿倍野区", "住之江区", "住吉区", "東住吉区",
               "平野区", "西成区"]


@app.get("/specified")
def specified():
    """v1.2.0：本页已并入「数据抓取设置 → ④ 下载条件」。

    旧链接保留为跳转，保证书签 / 浏览器历史不失效；老接口保持不变。
    """
    return redirect("/collect#dlcond")


def _opts_from_body(body: dict) -> dict:
    """把前端 POST 的 JSON 收拢成 crawler 要的 opts（价格/面积原样传，单位由表单文案约定）。

    物件種目支持**分组多选**（v1.2.3）：body.groups = [{"kind":"売一戸建","subtypes":[...]}, ...]，
    一个请求就能同时要到「一户建 2 项 + 公寓 4 项」；crawler 按组各跑一次检索
    （REINS 一行槽位只能挂一个種別），最后按物件番号去重合并。
    兼容老调用：只传 kind + subtypes 时自动包成一组。
    """
    def s(k):
        return (body.get(k) or "").strip()
    raw = body.get("subtypes")
    if isinstance(raw, str):
        raw = [raw]
    subs: list[str] = []
    for x in (raw or []):
        t = str(x or "").strip()
        if t and t not in subs:
            subs.append(t)
    if not subs and s("subtype"):
        subs = [s("subtype")]
    # 日期条件可多选（v1.2.0）：当天登录＝登録年月日；当天更新＝変更年月日。
    # 两个都勾 → crawler 会各跑一次检索、按物件番号去重合并。
    raw_dt = body.get("date_types")
    if isinstance(raw_dt, str):
        raw_dt = [raw_dt]
    dts: list[str] = []
    for x in (raw_dt or []):
        v = str(x or "").strip()
        if v in ("登録年月日", "変更年月日") and v not in dts:
            dts.append(v)
    if not dts and s("date_type") in ("登録年月日", "変更年月日"):
        dts = [s("date_type")]
    if not dts:
        dts = ["登録年月日"]
    # 分组种目多选（v1.2.3）：body.groups = [{"kind":"売一戸建","subtypes":[...]}, ...]
    # 前端第 4 步的種目面板按 一户建 / 公寓 / 土地 分组勾选，这里原样收下。
    raw_g = body.get("groups")
    groups_in: list[dict] = []
    if isinstance(raw_g, list):
        for g in raw_g:
            if not isinstance(g, dict):
                continue
            k = (g.get("kind") or "").strip()
            ss: list[str] = []
            for x in (g.get("subtypes") or []):
                t = str(x or "").strip()
                if t and t not in ss:
                    ss.append(t)
            if k and ss:
                groups_in.append({"kind": k, "subtypes": ss})
    return {
        "kind": s("kind"), "subtype": (subs[0] if subs else ""), "subtypes": subs,
        "groups": groups_in,
        "ward": s("ward"),
        "price_min": body.get("price_min") or "", "price_max": body.get("price_max") or "",
        "area_min": body.get("area_min") or "", "area_max": body.get("area_max") or "",
        "line": s("line"), "station": s("station"),
        "date_type": dts[0], "date_types": dts, "date": s("date"),
        # 土地権利/借地権種類 = 所有権のみ（默认勾上，与线上口径一致；
        # 抓不到该控件时 crawler 自动退回「指定なし ＝ 全部」）
        "ownership_only": bool(body.get("ownership_only", True)),
    }


@app.post("/api/query/preview")
def api_query_preview():
    """确定（实时预览）：按手动选项连 REINS 查，返回条数 + 样本（不落库）。"""
    cfg = _refresh_cfg()
    if (cfg.get("app", {}).get("mode") or "demo").lower() == "demo":
        return jsonify({"status": "demo",
                        "message": "当前是离线演示模式，无法实时查询。\n切到真实抓取后可预览。"})
    if SCHED.status()["busy"]:
        return jsonify({"status": "busy", "message": "已有任务在跑，请稍后"}), 409
    body = request.get_json(force=True, silent=True) or {}
    opts = _opts_from_body(body)
    _bg(lambda: _run_specified_preview(opts))
    return jsonify({"status": "started", "message": "已开始实时预览查询（实时连 REINS 查）"})


@app.post("/api/download/specified")
def api_download_specified():
    """下载（正式全部下载）：在 exclusive 内连 REINS 抓 + 入库 + PDF；
    期间暂停当天下载，跑完自动恢复。"""
    cfg = _refresh_cfg()
    if (cfg.get("app", {}).get("mode") or "demo").lower() == "demo":
        return jsonify({"status": "demo",
                        "message": "当前是离线演示模式，无法真实下载。\n切到真实抓取后可下载。"})
    if SCHED.status()["busy"]:
        return jsonify({"status": "busy", "message": "已有任务在跑，请稍后"}), 409
    body = request.get_json(force=True, silent=True) or {}
    opts = _opts_from_body(body)
    if not opts["date"]:
        return jsonify({"status": "error",
                        "message": "指定日期下载必须填写「指定日期」（YYYY-MM-DD）"}), 400
    if not opts["kind"]:
        return jsonify({"status": "error",
                        "message": "指定日期下载必须选择「物件種別」（必填）"}), 400
    trial = bool(body.get("trial", False))
    _bg(lambda: _run_specified_download(opts, trial=trial))
    return jsonify({"status": "started",
                    "message": ("已开始指定日期试下载（暂停当天下载，完成后自动恢复）"
                                if trial else
                                "已开始指定日期下载（暂停当天下载，完成后自动恢复）")})


def _run_specified_preview(opts):
    TEST_RESULT["specified_preview"] = {"status": "running", "at": _now()}
    try:
        res = SCHED.trigger_specified_preview(opts)
        TEST_RESULT["specified_preview"] = {"status": res.get("status", "ok"),
                                            "at": _now(), **res}
        log(("✓" if res.get("status") == "ok" else "✗")
            + " 指定日期预览：结果 " + str(res.get("total")) + " 条，样本 "
            + str(res.get("count")) + " 条")
    except Exception as e:  # noqa: BLE001
        TEST_RESULT["specified_preview"] = {"status": "error", "at": _now(),
                                            "message": str(e)}
        log("✗ 指定日期预览异常：" + str(e))


def _run_specified_download(opts, trial: bool = False):
    TEST_RESULT["specified_download"] = {"status": "running", "at": _now()}
    try:
        res = SCHED.trigger_specified_download(opts, trial=trial)
        TEST_RESULT["specified_download"] = {
            "status": res.get("status", "ok"), "at": _now(),
            "stats": {k: res.get(k) for k in
                      ("scanned", "fetched", "new", "changed", "pdf_saved", "source")},
            "errors": res.get("errors", [])[:3]}
        log(("✓" if res.get("status") == "ok" else "✗")
            + " 指定日期下载：落库 " + str(res.get("fetched")) + " / 新盘 "
            + str(res.get("new")) + " / 变更 " + str(res.get("changed")))
    except Exception as e:  # noqa: BLE001
        TEST_RESULT["specified_download"] = {"status": "error", "at": _now(),
                                             "message": str(e)}
        log("✗ 指定日期下载异常：" + str(e))


def create_app():
    return app


# ===================================================================
# v1.9.1（Task B）REINS 物件番号検索：后端登录式
# 前端按钮调本接口 → 后端用已登录 REINS 会话代填番号+点検索+截图 → 回传图片 URL。
# 番号検索页 URL/选择器来自 config.yaml（待真机在真实 DOM 取证后填，门禁②）。
# ===================================================================
@app.route("/api/reins/bukken_search", methods=["POST"])
def api_reins_bukken_search():
    body = request.get_json(silent=True) or {}
    no = str((body.get("no") or request.args.get("no") or "")).strip()
    if not no:
        return jsonify({"ok": False, "error": "缺少番号"}), 400
    try:
        r = reins_bukken_search(CFG, log, no)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    if not r.get("ok"):
        return jsonify({"ok": False, "error": r.get("error", "番号検索失败")}), 502
    return jsonify({"ok": True, "image_url": r.get("image_url"),
                    "reins_url": r.get("reins_url", "")})


@app.route("/bukken_shot/<path:filename>")
def bukken_shot(filename):
    # 只从 bukken_shots 运行时目录读截图（send_from_directory 已防目录穿越）
    d = PATHS["root"] / "bukken_shots"
    return send_from_directory(str(d), filename, mimetype="image/png")


if __name__ == "__main__":
    log(f"本地站点启动：http://{CFG['web']['host']}:{CFG['web']['port']}")
    log(f"数据落盘目录：{PATHS['root']}")
    if CFG.get("schedule", {}).get("enabled"):
        SCHED.start()
    app.run(host=CFG["web"]["host"], port=int(CFG["web"]["port"]),
            debug=False, use_reloader=False, threaded=True)
