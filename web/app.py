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
import hashlib
import re
import sys
import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.exceptions import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfgmod                      # noqa: E402
from core import credentials as creds_mod              # noqa: E402
from core import version as ver                        # noqa: E402
from core.auth import Auth, friendly_error            # noqa: E402
from core.crawler import probe_query, reins_bukken_search   # noqa: E402
from core import store as store_mod                   # noqa: E402
from core.scheduler import Scheduler                  # noqa: E402
from core.publisher import PublishLoop                 # noqa: E402
from core.store import Store                          # noqa: E402
from core.wareki import to_ad as wareki_to_ad          # noqa: E402
from core import accounts as acc_mod                    # noqa: E402  (PRD-19 账户权限)
from core import account_sync as acc_sync                # noqa: E402  (v1.9.28 账号双向同步)
from core.ai_structure_store import AIStructureStore     # noqa: E402  (v1.9.25 AI 解读独立库)

# 线上收数时**永不接受**的列（勇哥：PDF 不上传）
NEVER_UPLOAD_KEYS = {"pdf_path", "pdf_url", "absent_runs"}


# ---------------- 全局：配置 / 库 / 调度 / 日志 ----------------
CFG = cfgmod.load()
PATHS = cfgmod.paths(CFG)
STORE = Store(PATHS["db"])
# v1.9.25：AI 解读走**独立库文件** data/ai_pdf_store.db（勇哥要求与主数据分离）。
# 它是 PDF 的 AI 识别解读层，与主库互不覆盖；缺文件也不影响主业务（缺即无 AI 解读）。
# ⚠ PATHS["root"] 本身就是 data/ 目录（不是项目根），再拼 "data" 会变成 data/data/。
#    所以这里以主库 PATHS["db"] 的父目录为准，保证与主库同处 data/ 下。
AI_STORE = AIStructureStore(Path(PATHS["db"]).parent / "ai_pdf_store.db")

# 日志（必须在 AI_SCHED 初始化之前定义，供其 init 失败时记录）。
# 日志同时落盘：data/logs/server.log。服务"莫名其妙没了"时靠它查原因。
LOG_DIR = Path(PATHS["root"]) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"
LOGS: deque[str] = deque(maxlen=400)

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

# v1.9.26 / PRD 25：每晚 23:00 起的本地 AI 抽取调度（独立线程，与主抓取调度互不干扰）。
# 它只读本地 PDF、不碰 REINS 平台，所以夜里跑零冲突零风控。
try:
    from core.ai_scheduler import AIScheduler
    AI_SCHED = AIScheduler(CFG, PATHS, AI_STORE, log_fn=log)
except Exception as _e:                                     # noqa: BLE001
    AI_SCHED = None
    log(f"[AI定时] 初始化失败，AI 抽取定时不可用：{_e}")
acc_mod.init(PATHS["db"])   # PRD-19：账户/审计表建表（幂等，复用主库）
LOGS: deque[str] = deque(maxlen=400)
TEST_RESULT: dict = {}            # 测试类操作的结构化结果，供页面轮询展示
AI_GEN_JOB: dict = {             # 手动「正式生成」进度（设置页轮询展示）
    "running": False, "trigger": "", "total": 0, "done": 0,
    "local": 0, "cloud": 0, "skip": 0, "fail": 0, "pending": 0,
    "current": "", "started_at": "", "finished_at": "", "error": "",
}

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
PUB_LOOP = PublishLoop(STORE, CFG, log_fn=log)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False
# v1.9.1：静态资源（app.js / fieldmap.js / i18n.js / style.css）每次都要校验新鲜度。
#   【为什么】改了前端 JS 但若 core/version.py 没抬版本 → base.html 的 ?v= 不变
#   → 浏览器一直用旧缓存（本轮正是踩了这个：v1.9.1 改了 fieldmap.js/i18n.js，
#   但 VERSION 漏抬，导致勇哥浏览器仍跑旧 JS）。max-age=0 + 强制校验后，
#   即使 ?v 忘了抬，浏览器也会向服务端要最新版本，从根上杜绝这类"改了没生效"。
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# ============================================================
# v1.9.13 账户与权限（PRD-19）：会话密钥 + 总开关
#   · secret_key：优先用 config.local.yaml 的 app.session_secret（gitignored，稳定不随重启变）；
#     缺失时用「db 路径哈希」作本机稳定回退（重启不丢登录态，但不跨机器）。
#   · AUTH_ENABLED：账户拦截总开关，默认 False —— 关闭时行为与旧版完全一致（不强制登录）。
#     翻 True 即开启「对比页/查询页等受账户保护」，配合双链接方案（PRD-19 §14）的 A 正式站使用。
# ============================================================
app.secret_key = (CFG.get("app") or {}).get("session_secret") \
    or hashlib.sha256(str(PATHS["db"]).encode("utf-8")).hexdigest()
AUTH_ENABLED = bool((CFG.get("accounts") or {}).get("auth_enabled"))

# ============================================================
# PRD-19 §17：账户种子导入 + 防锁死兜底
#   ⚠ 必须放**模块级** —— 线上站（OSAKA_PUBLIC=1）经 main.py → serve_public.py 启动，
#     不会执行文件末尾的 __main__ 块（bootstrap_admin 那条也就不会跑）。
#   流程（勇哥 2026-09-19 决策：账户由线下建立 + 同步上云）：
#     ① 读 data/accounts.seed.json（只含 pbkdf2 哈希）→ UPSERT 进 accounts 表
#     ② 鉴权已开但账户表仍无可用账户 → **自动降级开放态**，绝不把自己锁在门外
# ============================================================
try:
    _seed_stat = acc_mod.import_seed_file(str(PATHS["root"] / "accounts.seed.json"),
                                          actor="system(seed)")
except Exception as _e:                      # 种子缺失/损坏绝不能让站点起不来
    _seed_stat = None
    print(f"[PRD-19] 账户种子导入失败（已忽略，站点继续启动）：{_e}", flush=True)
if _seed_stat:
    print(f"[PRD-19] 账户种子：新建 {_seed_stat['inserted']} / 更新 {_seed_stat['updated']} "
          f"（改码 {_seed_stat['pwd_reset']} · 保留已改密码 {_seed_stat['pwd_kept']}）", flush=True)
if AUTH_ENABLED and not acc_mod.has_any_account():
    print("[PRD-19] ⚠ 鉴权已开启但账户表无可用账户（种子未随同步上云？）"
          " → 自动降级为开放态，避免全员锁死", flush=True)
    AUTH_ENABLED = False

# ============================================================
# v1.5.18：对外（线上分享）模式
#   设环境变量 OSAKA_PUBLIC=1 时，**同一套代码**当作"只读展示站"跑：
#     · 采集 / 账号 / 本地文件 / 指定日期下载 四个页面不暴露
#     · 抓取、调度、发布、凭据、登录、PDF 下载 一类接口一律拒绝
#     · 首页（仪表盘，含更新开关）直接跳到查询页
#   本地跑（不设这个变量）行为完全不变 —— 这是"线上页面 == 本地页面"的前提。
# ============================================================
PUBLIC = os.environ.get("OSAKA_PUBLIC", "").strip().lower() in {"1", "true", "yes", "on"}

# ⚠ 注意：`/account` 是 **REINS 账号管理页**（录入 REINS 会员ID/密码/登录网址），
#    与「员工管理」完全是两回事；员工管理后台走 **`/staff`**，别混用。
PUBLIC_HIDDEN_PAGES = {"/collect", "/account", "/files", "/specified", "/staff"}
PUBLIC_HIDDEN_APIS = (
    "/api/run", "/api/publish", "/api/env", "/api/schedule",
    "/api/login", "/api/credentials", "/api/download",
    "/api/query/test", "/api/mode", "/api/decide", "/api/sync",
    "/api/notifications/read", "/api/status", "/api/logs",
    # v1.9.20：番号検索要开本机 Playwright + 已登录 REINS 会话，线上只读站两样都没有
    #   → 以前线上点按钮必然 500，前端只能退化成"剪贴板+开窗"（Edge 下被拦）。
    #   现在明确 403，前端按 OSAKA_PUBLIC 直接走"复制番号 + 打开 REINS 検索页"。
    "/api/reins",
    # v1.9.40：AI 读取时间（设置页）的两个**写**接口 —— 线上展示版是只读站，
    #   ① /api/ai/settings 会改线上配置并启停调度线程；
    #   ② /api/ai/generate 会拉起后台抽取线程（线上根本没有 PDF 附件目录）。
    #   两者都属"只有本机才有意义"的能力，线上明确 403（设置页 /collect 本来就 404 隐藏）。
    #   注：/api/ai/schedule/status、/api/ai/generate/status 是 GET 只读查询，不在此列。
    "/api/ai/settings", "/api/ai/generate",
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
    """v1.9.13 访问控制（PRD-19）。

    两层逻辑，由 AUTH_ENABLED 切换：
    · AUTH_ENABLED=False（默认）：维持旧 N2 访问码行为（仅 PUBLIC 模式生效；非 PUBLIC 完全开放）。
    · AUTH_ENABLED=True：账户登录为主。
        - 未登录 + 非放行路由 → 页面跳 /login、/api 返 401；
        - 已登录账户（session.user_id）→ 放行；
        - 应急码（public.access_code）：仍作管理员后门，命中即放行（PRD-19 §4.3 N2 改造）。
    放行白名单：/login、/api/auth/login、/api/ping、/static/*、访问码页。
    """
    path = request.path
    if path in ("/login", "/api/auth/login", "/api/ping") or path.startswith("/static/"):
        return None
    # ⚠ v1.9.19 🔴 修复（2026-09-19 实测事故）：/api/ingest 是**机器对机器**通道
    #   —— 本机每轮抓完把数据 POST 到线上。它自带 `X-Publish-Token` 校验（见 api_ingest），
    #   上传器没有、也不该有浏览器账户会话。v1.9.14 强制登录上线后本守卫把它一起拦了，
    #   线上收到 `{"message":"未登录"}` 401，**数据自 06:35 起静默停更**（本地 NEW 数据上不去）。
    #   豁免安全性：没带对令牌时 api_ingest 自己就返回 401「令牌不对」，等于多一层锁；
    #   且本豁免**不放开页面**——页面仍照旧跳 /login。
    if path == "/api/ingest":
        return None
    # v1.9.28：账号同步同样是**机器对机器**通道（本地 → 线上推种子 / 线上 → 本地回流），
    #   凭 X-Publish-Token 校验（见 api_accounts_seed_sync / api_accounts_state）。
    #   豁免理由与 /api/ingest 完全一致：上传器没有、也不该有浏览器账户会话；
    #   没带对令牌时接口自己就 401，等于多一层锁；且这里**不放开任何页面**。
    if path in ("/api/accounts/seed-sync", "/api/accounts/state"):
        return None

    code = (CFG.get("public") or {}).get("access_code") or ""
    emergency_ok = bool(code) and (
        request.args.get("code") == code or request.cookies.get("osaka_access_code") == code
    )

    if not AUTH_ENABLED:
        # 旧行为：N2 访问码（仅 PUBLIC 生效）
        if not PUBLIC:
            return None
        if not code:
            return None
        if emergency_ok:
            return None
        if path.startswith("/api/"):
            return jsonify({"status": "error", "message": "需要访问码"}), 401
        return render_template("access_code.html", path=path)

    # ===== AUTH_ENABLED：账户登录为主，应急码作后门 =====
    if emergency_ok:
        session["emergency"] = True
        return None
    if session.get("user_id"):
        return None
    # 未登录 → 拦截
    if path.startswith("/api/"):
        return jsonify({"status": "error", "message": "未登录"}), 401
    return redirect(url_for("login_page", next=path))


@app.after_request
def _plant_access_cookie(resp):
    """凭 `?code=` 进来的，顺手种 Cookie，之后免带参数（30 天）。"""
    code = (CFG.get("public") or {}).get("access_code") or ""
    if PUBLIC and code and request.args.get("code") == code:
        resp.set_cookie("osaka_access_code", code,
                        max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax")
    return resp


def _current_user():
    """返回当前登录账户信息（dict）或 None。供模板与接口共用（PRD-19）。"""
    if session.get("emergency"):
        return {"username": "(应急码)", "role": "admin", "emergency": True,
                "display_name": "应急访问", "cleared": 1}
    uid = session.get("user_id")
    if not uid:
        return None
    row = acc_mod.get_account(uid)
    if not row:
        return None
    return {"username": row["username"], "role": row["role"],
            "display_name": row["display_name"] or row["username"],
            "cleared": row["cleared"], "emergency": False}


@app.context_processor
def inject_globals():
    """所有模板都能直接拿到 cfg / paths / sched / 金额格式器 + 登录态（PRD-19）。"""
    return {
        "cfg": CFG,
        "paths": {k: str(v) for k, v in PATHS.items()},
        "sched": SCHED.status(),
        "test": dict(TEST_RESULT),
        "version": ver.VERSION,
        "build_at": ver.BUILD_AT,
        "public": PUBLIC,
        "auth_enabled": AUTH_ENABLED,
        "me": _current_user(),
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
    PUB_LOOP.cfg = CFG
    # v1.9.43 🔴 真 bug 修复：AI_SCHED 也要跟着换 cfg！
    #   漏了它的后果（勇哥 2026-09-21 真机踩到）：设置页保存「单轮上限/并发」后，
    #   AI_SCHED.status() 仍按**启动时**的旧 cfg 报数 → 前端回显把表单刷回旧值
    #   （落盘明明成功、页面却显示旧值 → 用户以为"没保存下来"，再设一次就写错）。
    if AI_SCHED is not None:
        AI_SCHED.cfg = CFG
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
        rows = []          # v1.9.38：允许「纯 AI 推送」（只带 ai_structure、不带 rows）
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
    log(f"☁ 收到线上推送 {n} 条房源主数据（库内共 {total} 条）")
    # v1.9.38：AI 结构自动上线 —— 本地 publisher 把 ai_structure 增量推来，整行覆盖写入线上库
    #   （线上是只读展示端，收本地最终权威结果直接覆盖最稳；不走本地「保护人工改值」合并）
    ai_n = 0
    ai_rows = body.get("ai_structure")
    if isinstance(ai_rows, list):
        try:
            for r in ai_rows:
                if not isinstance(r, dict) or not r.get("property_no"):
                    continue
                AI_STORE.upsert_raw(r)
                ai_n += 1
        except Exception as e:                                # noqa: BLE001
            return jsonify({"ok": False, "error": f"AI写入失败 {type(e).__name__}: {e}"}), 500
        if ai_n:
            log(f"☁ 收到线上 AI 结构推送 {ai_n} 条")
    return jsonify({"ok": True, "upserted": n, "total": total, "ai_upserted": ai_n})


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


def _normalize_kind(subtype: str) -> str:
    """把 properties.property_subtype（如「中古マンション」「中古戸建」「売地」）归一为
    REINS 大类（売マンション / 売一戸建 / 売土地），用于和 config.ai_read_scope 比对。

    主库 properties.kind 列为空，種目实际存在 property_subtype；ai_read_scope 存的是大类，
    所以过滤前必须归一。未命中任何大类返回空串（空串不在 ai_read_scope 里 → 被拦截）。
    """
    if not subtype:
        return ""
    s = (subtype or "").replace("\n", "").replace("／", "").replace(" ", "")
    if "マンション" in s:
        return "売マンション"
    if "戸建" in s or "タウン" in s:
        return "売一戸建"
    if "売地" in s or "土地" in s:
        return "売土地"
    return ""


@app.post("/api/ai/<property_no>/run")
def api_ai_run(property_no: str):
    """对该房源 PDF 现跑一次提取。**本地优先**（v1.9.26 / PRD 25 · D1）。

    勇哥口径：能用本地（读文本 / OCR）解决的就绝不调云端；只有本地跑不出来，
    并且用户**明确点同意**，才把 PDF 发给第三方模型。所以这里分三种结局：
      - 本地成功        → 直接落库返回 route=local，**零云端调用**
      - 本地不完整      → 返回 status=incomplete，由前端弹窗问「要不要花钱走云端」
                          （未拿到 allow_cloud=true 前绝不发请求）
      - 本地成功但被强制云端（allow_cloud 且 force_cloud）→ 才调模型

    body: {"force": bool 忽略 pdf_hash 重跑, "allow_cloud": bool 同意走云端,
           "force_cloud": bool 本地也能出结果但仍要云端}
    """
    _refresh_cfg()
    # v1.9.40：线上只读站不做 AI 抽取（线上没有 PDF 附件，点了必然失败）。
    #   详情页按钮已按 has_pdf 置灰（CUR_PDF=false 时禁用），这里是 API 层兜底。
    if PUBLIC:
        return jsonify({"ok": False,
                        "error": "线上展示版只提供查询，不提供 AI 生成（请在本机使用）"}), 403
    if STORE.get_property(property_no) is None:
        return jsonify({"ok": False, "error": f"物件 {property_no} 不在本地库"}), 404

    # v1.9.31 / PRD-25：AI 读取范围过滤（放在 PDF 检查之前，无 PDF 也能拦截）。
    # 空列表=全部種目都允许；非空则只放行勾选的種目（勇哥：手动选择哪些数据走 AI 读取）。
    # 注意：主库 properties 的種目存在 property_subtype（如「中古マンション」），kind 列为空；
    #       ai_read_scope 存 REINS 大类（売マンション/売一戸建/売土地），过滤时用 _normalize_kind 归一后比对。
    rk = CFG.get("ai_read_scope") or []
    if rk:
        prop = STORE.get_property(property_no)
        subtype = dict(prop).get("property_subtype", "") if prop else ""
        nk = _normalize_kind(subtype)
        if nk not in rk:
            return jsonify({"ok": False, "error":
                            f"種目「{subtype or '未知'}」未启用 AI 读取，请在设置页勾选该種目"}), 400

    pdf = PATHS["attachments"] / f"{property_no}.pdf"
    if not pdf.exists():
        return jsonify({"ok": False, "error":
                        "本房源没有落盘 PDF，无法提取（先用列表页把 PDF 下载下来）"}), 404

    data = request.get_json(force=True, silent=True) or {}
    force = bool(data.get("force"))
    allow_cloud = bool(data.get("allow_cloud"))
    force_cloud = bool(data.get("force_cloud"))

    le = CFG.get("local_extract") or {}
    if not le.get("enabled", True):
        return jsonify({"ok": False, "error":
                        "本地抽取已在设置里关闭（local_extract.enabled=false）"}), 400

    # ---------- ① 本地优先：文字层 → OCR（0 元）----------
    from core import ai_pipeline
    cfg = dict(le)
    cfg["ai"] = CFG.get("ai") or {}
    cfg["prefer_local"] = True
    # v1.9.33（P0-1）：详情页手动抽取启用「关键字段缺失即强制 OCR」；
    #   调度/批量路径不传此开关，只按 min_fields 阈值，避免全量 OCR 拖垮夜间窗口。
    cfg["key_gate"] = True
    try:
        # allow_cloud=False 是硬要求（D1）：本地没跑满也**绝不**在路由里私自烧钱，
        # 必须先返回 incomplete 让前端问过用户。传 AI_STORE 让 pdf_hash 跳过生效。
        res = ai_pipeline.run_one(property_no, PATHS, cfg, AI_STORE,
                                  force=force, allow_cloud=False,
                                  main_store=STORE)
    except Exception as e:                                  # noqa: BLE001
        log(f"[AI] {property_no} 本地抽取异常：{e}")
        return jsonify({"ok": False, "error": f"本地抽取失败：{e}"}), 500

    if res.get("skipped"):
        return jsonify({"ok": True, "status": "skipped", "property_no": property_no,
                        "reason": res.get("reason", "PDF 未变化，无需重跑"),
                        "route": "local"})

    local_ok = bool(res.get("local_hit"))
    if local_ok and not force_cloud:
        ai_pipeline.save_fields(AI_STORE, property_no, res)
        card = ai_pipeline.to_extraction_result(res)
        STORE.upsert_ai_extraction(
            property_no, card, model="local",
            prompt_version="local-v1", pdf_hash=res.get("pdf_hash", ""),
            cost_ms=res.get("ms"),
        )
        log(f"[AI] {property_no} 本地抽取完成（{res.get('ms')}ms，"
            f"{len(res.get('fields') or {})} 字段，云端 0 次）")
        return jsonify({"ok": True, "status": "ok", "property_no": property_no,
                        "route": "local", "cloud_used": 0,
                        "fields": len(res.get("fields") or {}),
                        "ms": res.get("ms")})

    # ---------- ② 本地不完整：先问，不静默花钱 ----------
    if not allow_cloud:
        return jsonify({
            "ok": True, "status": "incomplete", "property_no": property_no,
            "route": "local",
            "fields": len(res.get("fields") or {}),
            "rejected": [{"col": c, "raw": v, "reason": r}
                         for c, v, r in (res.get("rejected") or [])[:12]],
            "message": "本地只抽出部分字段。是否调用云端模型补全？（会产生费用）",
        })

    # ---------- ③ 用户同意后才调云端 ----------
    ai_cfg = CFG.get("ai") or {}
    if not (ai_cfg.get("api_key") or "").strip():
        return jsonify({"ok": False, "error":
                        "已同意走云端，但还没配 API Key（config.yaml → ai.api_key）。"
                        "申请火山方舟 Key 填入后即可使用。"}), 400
    from core import ai_extract
    try:
        result, meta = ai_extract.extract_property(property_no, PATHS, ai_cfg)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "本房源没有落盘 PDF，无法提取"}), 404
    except Exception as e:                       # 模型/网络错误不拖垮页面
        log(f"[AI] {property_no} 云端生成失败：{e}")
        return jsonify({"ok": False, "error": f"生成失败：{e}"}), 500
    STORE.upsert_ai_extraction(
        property_no, result, model=meta.get("model", ""),
        prompt_version=meta.get("prompt_version", ""),
        pdf_hash=_pdf_hash(property_no), cost_ms=meta.get("cost_ms"),
    )
    log(f"[AI] {property_no} 云端生成完成（{meta.get('cost_ms', 0)}ms，"
        f"model={meta.get('model')}）")
    return jsonify({"ok": True, "status": "ok", "property_no": property_no,
                    "route": "cloud", "cloud_used": 1})


# ============================================================
# AI 解读（v1.9.25）：PDF 的 AI 识别结果，存**独立库** data/ai_pdf_store.db
# 与上面的 /api/ai/*（PDF 单次动态提取）是两条不同的数据流：
#   /api/ai/*            = 现场调模型跑一次，结果存主库 ai_extractions
#   /api/ai-interpret/*  = 《大阪房源PDF数据总表》批量识别的成品，存独立库
# 详情页「AI 解读」卡片消费后者。
# ============================================================
@app.get("/api/ai-interpret/<property_no>")
def api_ai_structure_get(property_no: str):
    """返回某房源的 AI 解读：雷达图 + 约200字结论 + 分组字段（含异常等级）。"""
    rec = AI_STORE.get(property_no)
    if rec is None:
        return jsonify({"ok": True, "status": "none"})
    return jsonify({"ok": True, "status": "ok", **rec})


@app.post("/api/ai-interpret/<property_no>/edit")
def api_ai_structure_edit(property_no: str):
    """编辑 AI 解读里的单个字段（勇哥要求：所有数据将来都可编辑）。

    body: {"col": "専有面積（㎡）", "value": "63.06"}
    改完该字段异常等级归 ok、整条标 edited=1，并在独立库留审计日志（可追溯原值）。
    """
    data = request.get_json(force=True, silent=True) or {}
    col = str(data.get("col") or "").strip()
    if not col:
        return jsonify({"ok": False, "error": "缺少字段名 col"}), 400
    value = data.get("value")
    value = "" if value is None else str(value)
    actor = ""
    try:
        # 修正：原代码用 getattr(request,"user",None) 取操作人，Flask 无此属性
        # → editor 永远为空，卡死「员工蓝字+留痕」。改用 _current_user()（PRD-25）。
        cu = _current_user()
        actor = (cu or {}).get("username", "") or ""
    except Exception:
        pass
    res = AI_STORE.edit_field(property_no, col, value, editor=actor)
    if not res.get("ok"):
        return jsonify(res), 404
    log(f"[AI解读] {property_no} 字段 {col} 被改为「{value}」（{actor or '本机'}）")
    return jsonify(res)


@app.get("/api/ai-interpret/<property_no>/edits")
def api_ai_structure_edits(property_no: str):
    """该房源 AI 解读的编辑历史（谁在什么时候把什么改成了什么）。"""
    return jsonify({"ok": True, "items": AI_STORE.edit_log(property_no)})


@app.post("/api/ai/settings")
def api_ai_settings():
    """保存 AI 设置：① AI 读取范围（read_kinds → 顶层 ai_read_scope）
    ② 自动生成调度（schedule_ai：启用/时段/并发/强制重跑/历史补跑）。

    注意：绝不能写回 cfg["ai"]["read_kinds"] —— ai 块（含真 api_key）在 config.local.yaml，
    cfgmod.save 会把整个 ai 块当私密键剔除（core/config.py VULN-01），read_kinds 永远写不回。
    故改存顶层 ai_read_scope / schedule_ai：不在 local.yaml、也不匹配 SECRET_* 模式，
    可正常持久化且不泄密。
    """
    cfg = _refresh_cfg()
    body = request.get_json(force=True, silent=True) or {}
    if "read_kinds" in body:
        cfg["ai_read_scope"] = [str(x) for x in (body.get("read_kinds") or [])]
    if "schedule_ai" in body:
        sa = dict(cfg.get("schedule_ai") or {})
        incoming = body.get("schedule_ai") or {}
        # 只收白名单键，避免前端误写 timezone/backfill 等比对键被覆盖
        for k in ("enabled", "window", "max_per_run", "workers",
                  "force_rerun", "backfill_all", "backfill_batch"):
            if k in incoming:
                v = incoming[k]
                if k in ("max_per_run", "workers", "backfill_batch"):
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        v = sa.get(k)
                elif k in ("enabled", "force_rerun", "backfill_all"):
                    v = bool(v)
                sa[k] = v
        cfg["schedule_ai"] = sa
    cfgmod.save(cfg)
    # 按 enabled 启停 AIScheduler（不碰私密 ai 块；PRD-25 R6）
    # v1.9.40：线上只读站**绝不**拉起调度线程（PUBLIC 下 SCHED/PUB_LOOP 本就不启动）；
    #   配置照存（便于与本地一致），但不启停线程。PUBLIC_HIDDEN_APIS 已先挡一道，这里是双保险。
    if "schedule_ai" in body and AI_SCHED is not None and not PUBLIC:
        if (cfg.get("schedule_ai") or {}).get("enabled"):
            AI_SCHED.rearm()
            AI_SCHED.start()
        else:
            AI_SCHED.stop()
    log(f"[AI设置] ai_read_scope={cfg.get('ai_read_scope')} "
        f"schedule_ai.enabled={(cfg.get('schedule_ai') or {}).get('enabled')}")
    return jsonify({"status": "ok",
                    "ai_read_scope": cfg.get("ai_read_scope"),
                    "schedule_ai": cfg.get("schedule_ai")})


def _ai_generate_candidates(date: str | None, only_today: bool, force: bool) -> list[str]:
    """按「PDF 下载时间 + 種目范围 + 跳过已生成」圈定待生成番号（手动「正式生成」用）。

    - 日期口径（q-0）：以 PDF 落盘 mtime 作为「下载时间」；only_today=只取今天，
      date=只取指定日期，二者皆否=不限日期（处理全部匹配 PDF）。
    - 種目范围：复用 AI 读取范围的 ai_read_scope（空=全部種目）。
    - 跳过已生成（q-1）：未勾强制重跑时，AI_STORE 已有记录的房源直接跳过。
    - 防爆炸：超过单轮上限只取前 N，其余交给自动通道/再次点击。
    """
    att = PATHS["attachments"]
    rk = CFG.get("ai_read_scope") or []
    target = None
    if only_today:
        target = datetime.now().date()
    elif date:
        try:
            target = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            target = None
    cands: list[str] = []
    for p in att.glob("*.pdf"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except Exception:                                       # noqa: BLE001
            continue
        if target and mtime.date() != target:
            continue
        no = p.stem
        if rk:
            prop = STORE.get_property(no)
            subtype = dict(prop).get("property_subtype", "") if prop else ""
            if _normalize_kind(subtype) not in rk:
                continue
        if not force and AI_STORE.get(no) is not None:
            continue
        cands.append(no)
    cap = int((CFG.get("schedule_ai") or {}).get("max_per_run", 300))
    return cands[:cap]


def _ai_generate_worker(candidates: list[str], force: bool) -> None:
    """后台线程：逐份跑 AI 抽取，实时更新 AI_GEN_JOB 进度。"""
    import importlib
    ai_pipeline = importlib.import_module("core.ai_pipeline")
    le = CFG.get("local_extract") or {}
    cfg = dict(le)
    cfg["ai"] = CFG.get("ai") or {}
    cfg["prefer_local"] = True
    cfg["fallback_model"] = bool(cfg.get("fallback_model")) and bool(cfg.get("prefer_local", True))
    cloud_budget = int(le.get("fallback_max_per_run", 50))
    done = 0
    for no in candidates:
        AI_GEN_JOB["current"] = no
        try:
            allow_cloud = AI_GEN_JOB["cloud"] < cloud_budget
            r = ai_pipeline.run_one(no, PATHS, cfg, AI_STORE,
                                    force=force, allow_cloud=allow_cloud,
                                    main_store=STORE)
            if r.get("ok") and not r.get("skipped"):
                ai_pipeline.save_fields(AI_STORE, no, r)
            if r.get("ok"):
                if r.get("skipped"):
                    AI_GEN_JOB["skip"] += 1
                elif r.get("cloud_used"):
                    AI_GEN_JOB["cloud"] += 1
                else:
                    AI_GEN_JOB["local"] += 1
            else:
                AI_GEN_JOB["fail"] += 1
                log(f"[AI生成] {no} 失败：{r.get('error')}")
        except Exception as e:                                  # noqa: BLE001
            AI_GEN_JOB["fail"] += 1
            log(f"[AI生成] {no} 异常：{type(e).__name__}: {e}")
        done += 1
        AI_GEN_JOB["done"] = done
    AI_GEN_JOB["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    AI_GEN_JOB["running"] = False
    log(f"[AI生成] 完成：{AI_GEN_JOB['done']}/{AI_GEN_JOB['total']} "
        f"本地{AI_GEN_JOB['local']} 云端{AI_GEN_JOB['cloud']} "
        f"跳过{AI_GEN_JOB['skip']} 失败{AI_GEN_JOB['fail']}")


@app.get("/api/ai/schedule/status")
def api_ai_schedule_status():
    """返回 AI 自动生成调度状态（供设置页自动生成卡片回显）。"""
    if AI_SCHED is None:
        return jsonify({"ok": False, "error": "AI 定时未初始化"}), 500
    return jsonify({"ok": True, **AI_SCHED.status()})


@app.post("/api/ai/generate")
def api_ai_generate():
    """手动「正式生成」：按设置页圈定的范围批量跑 AI 抽取，后台执行 + 进度轮询。

    body: {"date": "YYYY-MM-DD"|null, "only_today": bool, "force": bool}
    默认只补缺失（有 PDF 但无 AI 解读的房源），勾 force 才整范围重跑。
    """
    _refresh_cfg()
    if AI_GEN_JOB["running"]:
        return jsonify({"ok": False, "error": "已有正式生成在跑，请稍后",
                        "job": AI_GEN_JOB}), 409
    body = request.get_json(force=True, silent=True) or {}
    date = body.get("date") or None
    only_today = bool(body.get("only_today"))
    force = bool(body.get("force"))
    cands = _ai_generate_candidates(date, only_today, force)
    if not cands:
        return jsonify({"ok": True,
                        "message": "没有需要生成的房源（范围内无匹配 PDF，或均已生成）",
                        "count": 0, "job": AI_GEN_JOB})
    AI_GEN_JOB.update({
        "running": True, "trigger": "manual", "total": len(cands), "done": 0,
        "local": 0, "cloud": 0, "skip": 0, "fail": 0, "pending": 0,
        "current": "", "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "", "error": "",
    })
    threading.Thread(target=_ai_generate_worker, args=(cands, force),
                     daemon=True, name="ai-generate").start()
    log(f"[AI生成] 启动：范围={len(cands)} 份（only_today={only_today} "
        f"date={date} force={force}）")
    return jsonify({"ok": True, "count": len(cands), "job": AI_GEN_JOB})


@app.get("/api/ai/generate/status")
def api_ai_generate_status():
    """轮询手动「正式生成」进度。"""
    return jsonify({"ok": True, "job": AI_GEN_JOB})


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


# ---- v1.9.30：PDF 图纸列表「收缩成一页」+ 按房源基本信息关键词查找 ----
# 【勇哥 2026-09-20 需求】/files 页把 3229 个 PDF 全量平铺成一张超长表 —— 翻不动、
#   也找不到想要的那一套。改法两件事：
#     ① 默认**只列最近 PDF_FILES_LIMIT 个**（一屏内看完，页面不再被撑长）；
#     ② 新增一个不限条件的关键词框，多的用它能翻遍**全部** PDF。
#   搜索口径 = 「房源查询」页的关键词口径（地址 / 楼名 / 物件番号 / 駅・沿線 / 間取り /
#   区 / 種目 / 築年月 / 详情），空格分隔多词＝都要命中、词内字段 OR —— 与查询页一字不差，
#   用户不必学两套规则（"关键词是房子查询里的基本包含的内容"）。
PDF_FILES_LIMIT = 50

# ⚠ 必须与 core/store.py::Store.search() 的关键词字段保持同源：
#   多一个少一个，用户在两个页面搜同样的词就会得到不同结果，等于给他挖坑。
PDF_SEARCH_COLS = ("address", "building_name", "property_no", "line_station",
                   "layout", "ward", "kind", "property_subtype",
                   "built_year_month", "detail_json")


def _like_escape(s: str) -> str:
    """转义 LIKE 通配符（用户输入 % / _ 时按字面匹配，别变成通配）。"""
    return (s or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _wan(v) -> str:
    """円 → 「9,800 万円」（与 web/static/app.js::wan() 同口径，两处显示才一致）。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f"{round(n / 10000):,} 万円"


def _pdf_meta(p: Path, sub: str = "attachments") -> dict:
    """一个 PDF 的轻量元信息（只 stat，不读库 —— 3229 个也要秒回）。"""
    st = p.stat()
    return {
        "name": p.name, "dir": sub, "no": p.stem,
        "size": f"{st.st_size / 1024:.1f} KB",
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
        "_mtime": st.st_mtime,
        "house": "", "in_db": False,
    }


def _attach_house_info(items: list) -> None:
    """就地给 PDF 行附上「这套图纸是哪套房」的基本信息（楼名 / 地址 / 間取り / 価格）。

    文件名 = 物件番号，所以直接按番号回查主库。两条注意：
      · 只 SELECT 需要的 7 列（**不能** SELECT *）——detail_json 很大，
        3229 行全取会把内存和响应时间都拖垮；
      · 分批 400 个占位符，避开 SQLite 的变量数上限。
    库内查不到的（历史遗留 PDF / 未入库）**留空**，页面显示「库中无此房源资料」，
    绝不编造。
    """
    nos = [it["no"] for it in items if it.get("no")]
    if not nos:
        return
    rows = {}
    cols = "property_no,building_name,address,ward,layout,property_subtype,price"
    for i in range(0, len(nos), 400):
        chunk = nos[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for r in STORE.conn.execute(
            f"SELECT {cols} FROM properties WHERE property_no IN ({ph})", chunk
        ).fetchall():
            rows[r["property_no"]] = r
    for it in items:
        r = rows.get(it.get("no"))
        it["in_db"] = bool(r)
        if not r:
            continue
        seg = []
        bname = (r["building_name"] or "").strip()
        addr = (r["address"] or "").strip()
        if bname:
            seg.append(bname)
        if addr:
            seg.append(addr)
        # 間取り 缺失时退回物件種目（土地没有間取り），价格有则带上
        kindish = (r["layout"] or "").strip() or (r["property_subtype"] or "").strip()
        tail = " ".join(x for x in (kindish.replace("\n", " "), _wan(r["price"])) if x)
        if tail:
            seg.append(tail)
        it["house"] = " ｜ ".join(seg)


def _pdf_all() -> list:
    """data/attachments 下全部 PDF，按修改时间倒序（最新在前）。"""
    d = PATHS["attachments"]
    if not d.exists():
        return []
    out = [_pdf_meta(p) for p in d.glob("*.pdf") if p.is_file()]
    out.sort(key=lambda x: x["_mtime"], reverse=True)
    return out


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

    # v1.9.30：PDF 只渲染最近 N 个（先切片再回查房源 —— 否则 3229 行全查库等于白干）
    pdfs_all = _pdf_all()
    pdfs_show = pdfs_all[:PDF_FILES_LIMIT]
    _attach_house_info(pdfs_show)
    return render_template("files.html",
                           exports=listing("exports", (".xlsx",)),
                           daily=listing("daily", (".html",)),
                           pdfs=pdfs_show,
                           pdf_total=len(pdfs_all),
                           pdf_limit=PDF_FILES_LIMIT,
                           root=str(PATHS["root"]))


@app.get("/api/files/search")
def api_files_search():
    """【本地文件页 · PDF 关键词检索】按「房源基本信息」在全量 PDF 里找图纸。

    为什么需要它：/files 页现在默认只列最近 50 个（页面不再被 3229 行撑爆），
    要找别的就得靠这个框。关键词口径与 /api/query **完全一致**：
      · 空格分隔多词 = 词与词 AND（都要命中）
      · 词内字段 OR（任一字段含该词即可）
      · 字段 = 地址 / 楼名 / 物件番号 / 駅・沿線 / 間取り / 区 / 種目 / 築年月 / 详情
    另外附赠一条：**文件名直接命中**也算 —— 这样连库里没有的遗留 PDF（约 300 个）
    也能靠番号翻出来，不留死角。

    q 为空 → 退回「最近 N 个」，与页面首屏完全一致（「清除」按钮就靠这个复位）。
    """
    _refresh_cfg()
    q = (request.args.get("q") or "").strip()
    limit = max(1, min(500, int(request.args.get("limit", PDF_FILES_LIMIT) or PDF_FILES_LIMIT)))

    pdfs_all = _pdf_all()
    by_no = {it["no"]: it for it in pdfs_all}

    if not q:
        items = pdfs_all[:limit]
        _attach_house_info(items)
        for it in items:
            it.pop("_mtime", None)
        return jsonify({"ok": True, "q": "", "mode": "recent",
                        "matched": len(pdfs_all), "shown": len(items), "items": items})

    terms = q.split()

    # ① 库内检索：拿到命中的物件番号
    where, args = [], []
    for t in terms:
        ors = " OR ".join(f"COALESCE({c},'') LIKE ? ESCAPE '\\'" for c in PDF_SEARCH_COLS)
        where.append("(" + ors + ")")
        args += [f"%{_like_escape(t)}%"] * len(PDF_SEARCH_COLS)
    sql = "SELECT property_no FROM properties WHERE " + " AND ".join(where)
    hit_nos = [r[0] for r in STORE.conn.execute(sql, args).fetchall()]

    # ② 文件名直接命中（含库外遗留 PDF）
    hit_set = set(hit_nos)
    lows = [t.lower() for t in terms]
    for stem in by_no:
        if stem in hit_set:
            continue
        if all(t in stem.lower() for t in lows):
            hit_nos.append(stem)

    items = [by_no[n] for n in hit_nos if n in by_no]
    matched = len(items)
    items.sort(key=lambda x: x["_mtime"], reverse=True)
    items = items[:limit]
    _attach_house_info(items)
    for it in items:
        it.pop("_mtime", None)
    return jsonify({"ok": True, "q": q, "mode": "search",
                    "matched": matched, "shown": len(items), "items": items})


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
                           ai_sched=(AI_SCHED.status() if AI_SCHED else {}),
                           ai_read_scope=(CFG.get("ai_read_scope") or []),
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


def _publish_worker(mode: str, force_ai: bool = False):
    _PUB.update({"busy": True, "log": [], "result": None,
                 "mode": mode, "force_ai": bool(force_ai)})

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
        _PUB["result"] = publish(cfg, con, mode=mode, log=say,
                                 force_ai=bool(_PUB.get("force_ai")))
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
    # v1.9.44「强制同步上传」（勇哥）：force_ai=True → 房源全量重传 + AI **忽略增量水位线**全量重推
    #   = 一键把线上补成 100% 与本地一致（不再受 last_ai_at 水位线限制）。
    force_ai = bool(_body.get("force_ai"))
    threading.Thread(target=_publish_worker, args=(mode, force_ai),
                     daemon=True).start()
    _msg = ("已开始强制同步上传（房源全量 + AI 全量重推）" if force_ai
            else ("已开始全量重传" if mode == "full" else "已开始增量推送"))
    return jsonify({"status": "started", "mode": mode,
                    "force_ai": force_ai, "message": _msg})


@app.route("/api/publish/status", methods=["GET", "POST"])
def api_publish_status():
    """上传进度 + 上次上传状态（publisher 的 publish_state 表）。

    v1.9.21：同时接受 GET/POST —— 前端 loadPublishStatus 用 postJSON（POST）调，
    旧版只挂 @app.get → 405 → 前端静默 catch → 上传面板永不刷新（状态恒「—」、
    按钮恒「暂停」、日志空白）。这是面板「死掉」的统一根因。
    """
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
    # v1.9.21：合并「自动定时器日志(PUB_LOG) + 手动上传日志(_PUB['log'])」，面板才看得到全貌
    try:
        from core.publisher import PUB_LOG as _PL
        _combined = (_PL[-220:] + _PUB["log"][-220:])
    except Exception:                                            # noqa: BLE001
        _combined = _PUB["log"][-220:]
    return jsonify({
        "busy": _PUB["busy"], "mode": _PUB["mode"],
        "log": _combined, "result": _PUB["result"],
        "state": st,
        "loop": PUB_LOOP.status(),
        "config": {"enabled": bool(pub.get("enabled", True)),
                   "mode": pub.get("mode", "auto"),
                   "auto_enabled": bool(pub.get("auto_enabled", False)),
                   "interval_minutes": pub.get("interval_minutes", 10),
                   "endpoint": pub.get("endpoint", ""),
                   "month_scope": pub.get("month_scope", ""),
                   "date_from": pub.get("date_from", ""),
                   "date_to": pub.get("date_to", ""),
                   "scope_caliber": pub.get("scope_caliber", "download")},
    })


@app.post("/api/publish/settings")
def api_publish_settings():
    """保存上传设置（自动开关 / 周期 / 日期段 / 线上地址 / 口径）。"""
    cfg = _refresh_cfg()
    body = request.get_json(force=True, silent=True) or {}
    p = cfg.setdefault("publish", {})
    if "enabled" in body:
        p["enabled"] = bool(body["enabled"])
    if "mode" in body and str(body["mode"]) in ("auto", "manual"):
        p["mode"] = str(body["mode"])
    for k in ("endpoint", "token", "month_scope", "scope_caliber",
              "date_from", "date_to"):
        if k in body:
            p[k] = str(body[k] or "").strip()
    if "scope_caliber" in p and p["scope_caliber"] not in ("download", "platform"):
        p["scope_caliber"] = "download"
    # v1.9.9：自动上传总开关 + 周期
    if "auto_enabled" in body:
        p["auto_enabled"] = bool(body["auto_enabled"])
    if "interval_minutes" in body:
        try:
            p["interval_minutes"] = max(1, int(float(body["interval_minutes"])))
        except Exception:
            pass
    cfgmod.save(cfg)
    PUB_LOOP.cfg = cfg
    # 自动开关变化 → 启停独立上传定时器（对外/线上模式不跑）
    if p.get("auto_enabled") and not PUBLIC and not PUB_LOOP.running:
        PUB_LOOP.start()
    elif not p.get("auto_enabled") and PUB_LOOP.running:
        PUB_LOOP.stop()
    log("上传设置已保存：auto=%s 周期=%s分 日期段=%s~%s 口径=%s"
        % (p.get("auto_enabled"), p.get("interval_minutes"),
           p.get("date_from") or "不限", p.get("date_to") or "不限",
           p.get("scope_caliber")))
    return jsonify({"status": "ok", "config": {
        "enabled": p.get("enabled"), "mode": p.get("mode"),
        "auto_enabled": p.get("auto_enabled"),
        "interval_minutes": p.get("interval_minutes"),
        "endpoint": p.get("endpoint"), "month_scope": p.get("month_scope"),
        "date_from": p.get("date_from"), "date_to": p.get("date_to"),
        "scope_caliber": p.get("scope_caliber")}})


@app.post("/api/publish/toggle")
def api_publish_toggle():
    """启动 / 暂停独立上传定时器（不删配置，只是停线程）。"""
    cfg = _refresh_cfg()
    p = cfg.setdefault("publish", {})
    enable = bool((request.get_json(force=True, silent=True) or {}).get("enabled", True))
    p["auto_enabled"] = enable
    cfgmod.save(cfg)
    PUB_LOOP.cfg = cfg
    if enable:
        if PUBLIC:
            return jsonify({"status": "blocked",
                            "message": "线上展示版不运行上传定时器"}), 403
        if not PUB_LOOP.running:
            PUB_LOOP.start()
        msg = "上传定时器已启动"
    else:
        if PUB_LOOP.running:
            PUB_LOOP.stop()
        msg = "上传定时器已暂停"
    return jsonify({"status": "ok", "enabled": enable, "running": PUB_LOOP.running,
                    "message": msg})


@app.post("/api/publish/preview")
def api_publish_preview():
    """预览：按当前配置算「会推多少行 + 前 5 条样本」，不真正发送（v1.9.9）。"""
    cfg = _refresh_cfg()
    limit = int((request.get_json(force=True, silent=True) or {}).get("limit", 5))
    try:
        from core.publisher import preview_scope
        import sqlite3
        con = sqlite3.connect(str(PATHS["db"]))
        con.row_factory = sqlite3.Row
        r = preview_scope(cfg, con, limit=limit, log=log)
        con.close()
        return jsonify({"status": "ok", "count": r["count"], "sample": r["sample"],
                        "config": {"date_from": (cfg.get("publish") or {}).get("date_from", ""),
                                   "date_to": (cfg.get("publish") or {}).get("date_to", ""),
                                   "scope_caliber": (cfg.get("publish") or {}).get("scope_caliber", "download"),
                                   "month_scope": (cfg.get("publish") or {}).get("month_scope", "")}})
    except Exception as e:                                       # noqa: BLE001
        return jsonify({"status": "error", "message": str(e)}), 500


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
    SCHED.rearm()          # v1.9.9：配置热改后立即重算下一轮时间
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
    nos = body.get("nos") if "nos" in body else body.get("no")
    if nos is None:
        nos = request.args.get("nos") or request.args.get("no")
    if isinstance(nos, str):                       # 允许 "a,b" / "a，b" 这种串
        nos = nos.replace("，", ",").replace(" ", ",").split(",")
    nos = [str(x).strip() for x in (nos or []) if str(x).strip()]
    if not nos:
        return jsonify({"ok": False, "error": "缺少番号"}), 400
    try:
        r = reins_bukken_search(CFG, log, nos)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    if not r.get("ok"):
        # v1.9.20：业务性失败（REINS 0 件 / 回读不一致 / 选择器不匹配）一律回 **200 + ok:false**，
        #   并把 image_url 一起带上 —— 前端要能看到那张真实的结果页截图。
        #   旧版回 502 有两个坑：① 网关可能把 502 包成 HTML，前端 r.json() 直接炸；
        #   ② 前端拿不到截图，用户只看到一句干巴巴的失败。
        return jsonify({"ok": False, "error": r.get("error", "番号検索失败"),
                        "image_url": r.get("image_url"), "zero": bool(r.get("zero")),
                        "reins_url": r.get("reins_url", ""), "filled": r.get("filled", 0)})
    return jsonify({"ok": True, "image_url": r.get("image_url"),
                    "reins_url": r.get("reins_url", ""), "filled": r.get("filled", 0)})


@app.route("/bukken_shot/<path:filename>")
def bukken_shot(filename):
    # 只从 bukken_shots 运行时目录读截图（send_from_directory 已防目录穿越）
    d = PATHS["root"] / "bukken_shots"
    return send_from_directory(str(d), filename, mimetype="image/png")


@app.route("/staff")
def staff_page():
    """员工管理后台（PRD-19 v2.0 · **仅线下本地 8765 可见**）。

    ⚠ 路径是 `/staff` 而不是 `/account`：后者是 **REINS 账号管理页**
      （录入 REINS 会员ID / 密码 / 登录网址），两者不能混用。
    线上 PUBLIC 模式：`/staff` 在 PUBLIC_HIDDEN_PAGES 中 → 自动 404，
      即线上站不提供任何建号能力（符合"线上只认本地同步来的账户"这条铁律）。
    未登录 → 跳登录页（带 next）；已登录但非管理员 → 403。
    """
    me = _current_user()
    if not me:
        return redirect(url_for("login_page", next="/staff"))
    if me["role"] != acc_mod.ROLE_ADMIN:
        return "需要管理员权限", 403
    return render_template("staff.html", me=me, sync=acc_sync.status(CFG))


# ============================================================
# v1.9.13 账户与权限路由（PRD-19）
# ============================================================
def _require_admin():
    """返回 (me, err_resp)。me 为当前管理员账户 dict；非管理员 err_resp 为 403 JSON。"""
    me = _current_user()
    if not me or me["role"] != acc_mod.ROLE_ADMIN:
        return None, jsonify({"ok": False, "error": "需要管理员权限"}, ), 403
    return me, None


# ---- v1.9.27 导出 → v1.9.28 直接上云：账号变更后**自动**同步到线上 ----
# 【背景·勇哥 2026-09-20 连续两次踩坑】/staff 重置只改本地主库，
#   而线上账号的唯一来源是 accounts.seed.json。结果：重置完 → 忘了导出/发布 →
#   线上仍是旧哈希 → 员工手持新码报「密码错误」。两次都栽在"忘了上云"这一步。
# 【v1.9.27】把"导出"从**手工动作**变成**流程副作用**：任何一次账户写操作
#   （新建 / 重置随机码 / 改角色 / 禁用 / 首次设密 / 改密）成功后自动导出最新种子到
#   ① 本地 data/accounts.seed.json ② 发布工程 app_local/data/accounts.seed.json。
# 【v1.9.28】再进一步：导出后**立刻 POST 到线上**（走已有的 X-Publish-Token 机器通道），
#   于是"改完即刻生效"，不再需要重新发布。单向往返变成双向：
#     本地 → 线上（本次，force_users 定向覆盖被改动的账号）
#     线上 → 本地（core/account_sync.pull_and_adopt，员工自设的密码回流 /staff）
#   仍然只写哈希、绝不落明文；失败只记日志/水位线，**绝不影响主操作**。
#   放在 _require_admin 之后，使登录/改密/set-pwd 等路由（均定义在其后）都能前向引用到。
def _auto_export_seed(changed=None) -> dict:
    """账号变更后的自动同步（v1.9.27 导出 → v1.9.28 直接上云）。

    changed：本次被改动的账号用户名。会被标为 force_users 定向强制覆盖线上哈希 ——
      因为刚重置过随机码的账号，线上可能早已 cleared=1（员工先前自设过密码），
      按保守合并规则不会被覆盖，那样新码在线上照样登不上。

    返回线上推送结果 dict（供接口回执给前端显示"是否已生效于线上"）。
    线上侧的每次写操作（员工自设密码）也会走这里 —— 但 PUBLIC 模式**不推**（自己推自己没意义），
    只写本地种子，等本地拉取时回流。
    """
    online = None
    try:
        local_seed = Path(PATHS["root"]) / "accounts.seed.json"
        data = acc_mod.export_seed(str(local_seed))
        n = len(data.get("accounts", []))
        note = ""
        # 发布工程与 osaka-mvp 是兄弟目录：data/ -> osaka-mvp/ -> 工作根 -> osaka-house-publish
        pub_dir = Path(PATHS["root"]).parent.parent / "osaka-house-publish" / "app_local" / "data"
        if pub_dir.is_dir():
            try:
                acc_mod.export_seed(str(pub_dir / "accounts.seed.json"))
                note = "；已镜像发布工程"
            except Exception as _e2:
                note = "；⚠ 镜像发布工程失败：" + str(_e2)
        else:
            note = "；未找到发布工程目录，仅写本地"
        log(f"[PRD-19] 账号变更 → 已自动导出种子（{n} 个账号）：{local_seed}{note}")
    except Exception as _e:
        log(f"[PRD-19] ⚠ 自动导出种子失败（不影响主流程）：{_e}")

    # v1.9.28：紧接着把种子推到线上（免去"每次改完都得重新发布"这一步）
    if not PUBLIC:
        try:
            online = acc_sync.push_seed(CFG, log=log,
                                        force_users=[changed] if changed else None,
                                        timeout=8)
            if online.get("ok") and not online.get("skipped"):
                log(f"[PRD-19] 账号已同步上云（{changed or '全量'}）")
        except Exception as _e3:                              # noqa: BLE001
            online = {"ok": False, "errors": [f"{type(_e3).__name__}: {_e3}"]}
            log(f"[PRD-19] ⚠ 上云同步异常（不影响本地操作）：{_e3}")
    return online or {}


def _sync_digest(online: dict | None) -> dict:
    """把 push_seed 的结果压成前端够用的一小块（给 /staff 提示"是否已生效于线上"）。"""
    if not online:
        return {"ok": False, "hint": "线上未同步（线上模式或未配置 endpoint）"}
    if online.get("ok") and online.get("skipped"):
        return {"ok": True, "skipped": True, "hint": "线上已是同一份，无需重复推送"}
    if online.get("ok"):
        return {"ok": True, "stat": online.get("stat") or {}, "hint": "已同步上云，员工可立即登录"}
    return {"ok": False, "hint": "；".join(online.get("errors") or ["上云失败"])[:300]}


@app.route("/login", methods=["GET"])
def login_page():
    """登录页（PRD-19）。next= 登录后回跳地址；backup_url= 过渡期临时备份页入口（PRD-19 §14）。"""
    backup_url = (CFG.get("public") or {}).get("backup_url", "") or ""
    return render_template("login.html", next=request.args.get("next", ""),
                           auth_enabled=AUTH_ENABLED, public=PUBLIC, backup_url=backup_url)


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"ok": False, "error": "用户名与密码不能为空"}), 400
    ok, row, reason = acc_mod.authenticate(username, password, ip=request.remote_addr)
    if not ok:
        msg = {"no_such_user": "用户不存在", "disabled": "账户已禁用",
               "locked": "账户已锁定，请稍后再试", "bad_password": "密码错误"}.get(reason, "登录失败")
        return jsonify({"ok": False, "error": msg, "reason": reason}), 401
    session["user_id"] = row["username"]
    session["role"] = row["role"]
    session.pop("emergency", None)
    return jsonify({"ok": True, "role": row["role"], "cleared": row["cleared"],
                    "need_change_pwd": row["cleared"] == 0})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    return jsonify({"ok": True, "me": _current_user()})


@app.route("/api/auth/change-pwd", methods=["POST"])
def api_auth_change_pwd():
    """本人改密（需真实登录账户，应急码不能改密）。"""
    me = _current_user()
    if not me or me.get("emergency"):
        return jsonify({"ok": False, "error": "请先以账户登录后再改密"}), 401
    data = request.get_json(silent=True) or {}
    old_pw = data.get("old_password") or ""
    new_pw = data.get("new_password") or ""
    ok, msg = acc_mod.change_password(me["username"], old_pw, new_pw, actor=me["username"])
    if not ok:
        code = 400
        if msg == "old_password_wrong":
            code = 401
        return jsonify({"ok": False, "error": {"weak_password": "密码需 8–20 位且含字母与数字",
                                                "old_password_wrong": "原密码错误"}.get(msg, msg)}), code
    _auto_export_seed(me["username"])   # v1.9.28：改密成功后同步（含上云）
    return jsonify({"ok": True})


@app.route("/api/auth/set-pwd", methods=["POST"])
def api_auth_set_pwd():
    """首次登录**强制设置密码**（PRD-19 v2.0 主流程 ⑤ · P0 缺口）。

    与 change-pwd 的区别：**不需要原密码** —— 原密码是管理员发的一次性随机码，
    员工登录后直接设正式密码即可。设完 cleared=1，
    此后同步不会再拿种子覆盖他的哈希（UPSERT「不覆盖已改密码」才真正生效）。
    """
    me = _current_user()
    if not me or me.get("emergency"):
        return jsonify({"ok": False, "error": "请先以账户登录"}), 401
    data = request.get_json(silent=True) or {}
    new_pw = data.get("new_password") or ""
    ok, msg = acc_mod.set_initial_password(me["username"], new_pw, actor=me["username"])
    if not ok:
        return jsonify({"ok": False, "error": {"weak_password": "密码需 8–20 位且含字母与数字",
                                                "disabled": "账户已禁用"}.get(msg, msg)}), 400
    # v1.9.28：首次设密（cleared 0→1）后同步；线上侧不推（PUBLIC），本地推+回流
    online = _auto_export_seed(me["username"])
    return jsonify({"ok": True, "online_sync": _sync_digest(online)})


@app.route("/api/accounts", methods=["GET", "POST"])
def api_accounts():
    me, err = _require_admin()
    if err:
        return err
    if request.method == "GET":
        rows = acc_mod.list_accounts()
        return jsonify({"ok": True, "accounts": [dict(r) for r in rows]})
    # POST：新建员工
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") or acc_mod.ROLE_STAFF
    display_name = data.get("display_name") or None
    random_code = bool(data.get("random_code"))
    if not random_code and not password:
        return jsonify({"ok": False, "error": "请填初始密码，或勾选「生成一次性随机码」"}), 400
    ok, msg = acc_mod.create_account(username, password, role=role, created_by=me["username"],
                                      display_name=display_name, random_code=random_code)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    # random_code=True 时 msg 是明文随机码，前端一次性展示
    # v1.9.28：新建后同步（否则线上认不出这个新账号）—— 新账号必须 force，因线上根本不存在
    online = _auto_export_seed(username)
    return jsonify({"ok": True, "one_time_code": msg if random_code else None,
                    "online_sync": _sync_digest(online)})


@app.route("/api/accounts/<username>", methods=["PATCH", "DELETE"])
def api_account_detail(username):
    me, err = _require_admin()
    if err:
        return err
    if request.method == "DELETE":
        ok, msg = acc_mod.set_disabled(username, True, by_admin=me["username"])
        if not ok:
            return jsonify({"ok": False, "error": msg}), 400
        online = _auto_export_seed(username)
        return jsonify({"ok": True, "online_sync": _sync_digest(online)})
    # PATCH：改角色 / 禁用 / 重置随机码
    data = request.get_json(silent=True) or {}
    if "role" in data:
        ok, msg = acc_mod.set_role(username, data["role"], by_admin=me["username"])
        if not ok:
            return jsonify({"ok": False, "error": msg}), 400
    if "disabled" in data:
        ok, msg = acc_mod.set_disabled(username, bool(data["disabled"]), by_admin=me["username"])
        if not ok:
            return jsonify({"ok": False, "error": msg}), 400
    if data.get("reset_code"):
        ok, code = acc_mod.reset_random_code(username, by_admin=me["username"])
        if not ok:
            return jsonify({"ok": False, "error": code}), 400
        # ⚠ v1.9.28 最关键的一处：重置随机码 = 换哈希（cleared 归 0），
        #   必须立刻同步上云（并**点名强制覆盖该账号**）—— 否则线上拿旧哈希，
        #   员工手持新码报「密码错误」。以前这一步靠"重新发布"，是两次事故的共同根因。
        online = _auto_export_seed(username)
        return jsonify({"ok": True, "one_time_code": code,
                        "online_sync": _sync_digest(online)})
    online = _auto_export_seed(username)   # v1.9.28：改角色/禁用后同步
    return jsonify({"ok": True, "online_sync": _sync_digest(online)})


@app.route("/api/accounts/<username>/code", methods=["GET"])
def api_account_code(username):
    """【仅本地管理员】取账户当前待改密明文码（v1.9.29「复制现有密码」按钮用）。

    不要求 X-Publish-Token（这是浏览器管理员会话，走普通 _require_admin 守卫），
    也绝不返回任何哈希 / 不写审计敏感信息。明文码只存在于本地 accounts.current_code，
    不会经此接口泄漏到线上（线上账户根本没这列的有效值）。
    """
    me, err = _require_admin()
    if err:
        return err
    ok, code = acc_mod.get_current_code(username)
    if not ok:
        return jsonify({"ok": False, "reason": code}), 200
    return jsonify({"ok": True, "username": username, "code": code})


# ============================================================
# v1.9.28 账号双向同步 · 机器对机器接口（本地 ⇄ 线上）
#   与 /api/ingest 同源同锁：凭 `publish.ingest_token`（X-Publish-Token 头），
#   无令牌一律 401；已在 _access_guard 里豁免（上传器没有浏览器会话）。
#   只传**哈希**，明文密码永不经过这条通道。
# ============================================================
def _sync_token_ok() -> bool:
    _refresh_cfg()
    want = str(((CFG.get("publish") or {}).get("ingest_token") or "")).strip()
    got = str(request.headers.get("X-Publish-Token") or "").strip()
    return bool(want) and got == want


@app.post("/api/accounts/seed-sync")
def api_accounts_seed_sync():
    """【本地 → 线上】收下本地推来的账号种子，按 UPSERT 规则落库（v1.9.28）。

    为什么需要它：线上账号的唯一来源原是"重新发布"（容器重建 + 起停空窗）。
    有了这个接口，管理员在本地 /staff 重置随机码后**几秒内**线上就认这个新码，
    不必再等重新发布 —— 这正是两次「拿着新码报密码错误」事故的根因消除。

    force_users：被点名的账号强制覆盖哈希/cleared（对应"管理员刚重置了它"这一事件）；
      其余账号走保守规则：线上仍是随机码(cleared=0)才覆盖，已自设密码(cleared=1)的一律保留。
    """
    if not _sync_token_ok():
        return jsonify({"ok": False, "error": "令牌不对（401）"}), 401
    body = request.get_json(force=True, silent=True) or {}
    rows = body.get("accounts")
    if not isinstance(rows, list):
        return jsonify({"ok": False, "error": "accounts 必须是数组"}), 400
    force = {str(u).strip() for u in (body.get("force_users") or []) if str(u or "").strip()}
    try:
        stat = acc_mod.upsert_seed(rows, actor="system(sync:in)", force_users=force)
    except Exception as e:                                    # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    log(f"☁ 账号种子已同步：{stat}（强制覆盖 {len(force)}）")
    return jsonify({"ok": True, "stat": stat, "exported_at": body.get("exported_at"),
                    "force_users": sorted(force)})


@app.get("/api/accounts/state")
def api_accounts_state():
    """【线上 → 本地】回传本机账户状态（**含哈希**），供本地回流对账（v1.9.28）。

    用途：员工在线上自设密码后，本地 /staff 才能知道"这个账号已被认领"（不再显示「待改密」）。
    ⚠ 含哈希 → 只允许这条带令牌的机器通道，且绝不进任何页面。
    """
    if not _sync_token_ok():
        return jsonify({"ok": False, "error": "令牌不对（401）"}), 401
    try:
        rows = acc_mod.export_state()
    except Exception as e:                                    # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    return jsonify({"ok": True, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "count": len(rows), "accounts": rows})


@app.post("/api/accounts/sync-now")
def api_accounts_sync_now():
    """【本地按钮】立即「推 + 拉」一次（管理员）。用于网络抖动后手动补同步。"""
    me, err = _require_admin()
    if err:
        return err
    try:
        res = acc_sync.sync_now(CFG, log=log)
    except Exception as e:                                    # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    return jsonify({"ok": bool(res.get("ok")),
                    "push": _sync_digest(res.get("push")),
                    "pull": res.get("pull") or {},
                    "status": acc_sync.status(CFG)})


if __name__ == "__main__":
    log(f"本地站点启动：http://{CFG['web']['host']}:{CFG['web']['port']}")
    log(f"数据落盘目录：{PATHS['root']}")
    try:
        ok, st = acc_mod.bootstrap_admin(CFG)   # PRD-19：config.local.yaml 配了首管理员则自动建
        if ok:
            log(f"[PRD-19] 首管理员账户：{st}")
    except Exception as _e:
        log(f"[PRD-19] bootstrap_admin 跳过：{_e}")
    if not PUBLIC:
        if CFG.get("schedule", {}).get("enabled"):
            SCHED.start()
        if (CFG.get("publish") or {}).get("auto_enabled"):
            PUB_LOOP.start()
        # PRD 25：AI 抽取定时（每晚 23:00 窗口）—— 只读本地 PDF，与抓取时段不冲突
        if AI_SCHED is not None and (CFG.get("schedule_ai") or {}).get("enabled"):
            AI_SCHED.start()
    else:
        log("☁ 线上展示版：不启动本地抓取与上传定时器（只读）")
    app.run(host=CFG["web"]["host"], port=int(CFG["web"]["port"]),
            debug=False, use_reloader=False, threaded=True)
