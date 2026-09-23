# -*- coding: utf-8 -*-
"""PDF → 腾讯云 COS 上云（私有读 + 本地预签名），CLI 与「设置页」共用同一份逻辑。

设计（2026-09-23 勇哥拍板）
--------------------------
· 日期口径：**本地下载日**（PDF 落盘到 data/attachments 的那天，取文件 mtime）。
· 触发：① 设置页按日期段手工上传 ② 自动重签（签名剩余 < 2 天时刷新并回推线上）。
· 密钥只从 `config.local.yaml` 的 cos 段读，绝不写死在代码里。
· 线上不持密钥：靠本地生成的 7 天预签名直链（COS 桶私有读写）。
· 回推线上复用 `publisher.post_rows`（走 /api/ingest，**不需要发版**）。

⚠ 术语：判断"这条有没有 AI 内容"看 `overall` 是否非空；本模块只管 PDF 文件与 pdf_url。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 预签名有效期：腾讯云上限 7 天
DEFAULT_EXPIRE = 7 * 24 * 3600
# 剩余不足 2 天就重签（留足缓冲，避免刚好到期）
RESIGN_THRESHOLD = 2 * 24 * 3600

# 上传任务状态（本地单实例，内存态即可；UI 轮询用）
_TASKS: dict = {}
_TASKS_LOCK = threading.Lock()
_task_seq = [0]


def _cfg():
    from core import config as cfgmod                        # 延迟导入，避免循环依赖
    return cfgmod.load()


def _cos_conf(cfg: dict | None = None) -> dict:
    cfg = cfg or _cfg()
    cos = cfg.get("cos") or {}
    miss = [k for k in ("secret_id", "secret_key", "bucket", "region") if not cos.get(k)]
    if miss:
        raise RuntimeError("COS 未配置（缺 %s）→ 见 config.local.yaml 的 cos 段" % miss)
    sk = str(cos["secret_key"])
    if sk.startswith("PASTE") or len(sk) < 20:
        raise RuntimeError("COS secret_key 仍是占位符 → 请先填入真 SecretKey")
    return cos


def client(cfg: dict | None = None):
    """返回 (cos_conf, CosS3Client)。"""
    from qcloud_cos import CosConfig, CosS3Client             # 延迟导入，未装 SDK 时不炸
    cos = _cos_conf(cfg)
    return cos, CosS3Client(CosConfig(Region=cos["region"], SecretId=cos["secret_id"], SecretKey=cos["secret_key"]))


def db_path() -> Path:
    return ROOT / "data" / "jproperty.db"


def attachments_dir() -> Path:
    return ROOT / "data" / "attachments"


def _connect():
    return sqlite3.connect(str(db_path()), timeout=15)


# ---------------------------------------------------------------- 扫描 / 预览
def scan(date_from: str | None = None, date_to: str | None = None,
         only_not_uploaded: bool = False) -> list[dict]:
    """按**本地下载日**（文件 mtime）扫描 PDF。返回 [{no, mtime, size, uploaded}]。

    date_from / date_to：`YYYY-MM-DD`（含当天）。为空表示不限。
    """
    d = attachments_dir()
    if not d.exists():
        return []
    out = []
    for f in d.glob("*.pdf"):
        st = f.stat()
        day = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        out.append({
            "no": f.stem,
            "day": day,
            "mtime": st.st_mtime,
            "size": st.st_size,
        })

    out.sort(key=lambda x: x["mtime"])
    if not out:
        return out

    # 补上"是否已上传"：库内 pdf_url 非空即视为已上传
    con = _connect()
    try:
        q = "SELECT property_no, pdf_url FROM properties WHERE property_no IN (%s)" % \
            ",".join("?" * len(out))
        m = {r[0]: (r[1] or "") for r in con.execute(q, [x["no"] for x in out])}
    finally:
        con.close()
    for x in out:
        x["in_db"] = x["no"] in m
        x["uploaded"] = bool(m.get(x["no"]))

    if only_not_uploaded:
        out = [x for x in out if not x["uploaded"]]
    return out


def status() -> dict:
    """设置页卡片状态：本地份数 / 已上传 / 待上传 / 最早过期天数。"""
    try:
        _cos_conf()
        cos_ok = True
        cos_err = ""
    except Exception as e:                                    # noqa: BLE001
        cos_ok, cos_err = False, str(e)

    files = list(attachments_dir().glob("*.pdf")) if attachments_dir().exists() else []
    con = _connect()
    try:
        rows = con.execute("SELECT property_no, pdf_url FROM properties WHERE pdf_url IS NOT NULL AND pdf_url != ''").fetchall()
        have = {r[0]: r[1] for r in rows}
    finally:
        con.close()

    # 最早过期：从 pdf_url 里的 q-sign-time 第二段（unix 秒）解析
    soonest = None
    now = time.time()
    for u in have.values():
        try:
            tail = u.split("q-sign-time=")[1].split("&")[0]
            end = int(tail.split("%3B")[1] if "%3B" in tail else tail.split(";")[1])
        except Exception:                                     # noqa: BLE001
            continue
        left = end - now
        if soonest is None or left < soonest:
            soonest = left

    return {
        "cos_ok": cos_ok,
        "cos_err": cos_err,
        "local_total": len(files),
        "uploaded": len(have),
        "pending": max(0, len(files) - len(have)),
        "expire_in_days": round(soonest / 86400.0, 2) if soonest is not None else None,
    }


# ---------------------------------------------------------------- 上传
def _write_pdf_url(no: str, url: str) -> bool:
    con = _connect()
    try:
        cur = con.execute("UPDATE properties SET pdf_url=? WHERE property_no=?", (url, no))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def upload_one(no: str, cos: dict, cli, expire: int = DEFAULT_EXPIRE) -> dict:
    """上传单份 + 写回 pdf_url。幂等：云端已有则不重传，只重签。"""
    src = attachments_dir() / ("%s.pdf" % no)
    if not src.exists():
        return {"ok": False, "no": no, "msg": "本地无此 PDF"}
    key = "%s.pdf" % no
    try:
        cli.head_object(Bucket=cos["bucket"], Key=key)
        uploaded = False
    except Exception:                                         # noqa: BLE001 - 404 即未存在
        cli.upload_file(Bucket=cos["bucket"], Key=key, LocalFilePath=str(src),
                        PartSize=10, MAXThread=4, EnableMD5=False)
        uploaded = True
    url = cli.get_presigned_download_url(Bucket=cos["bucket"], Key=key, Expired=expire)
    return {"ok": True, "no": no, "uploaded": uploaded,
            "written": _write_pdf_url(no, url), "size": src.stat().st_size, "url": url}


def push_urls(cfg: dict, pairs: list[tuple], log=None) -> dict:
    """把 (property_no, pdf_url) 增量回推线上（走 /api/ingest，**不发版**）。"""
    if not pairs:
        return {"ok": True, "sent": 0}
    from core import publisher
    rows = [{"property_no": n, "pdf_url": u} for n, u in pairs]
    return publisher.post_rows(cfg, rows, log=log)


def start_upload(nos: list[str], push_online: bool = True, log=None,
                 progress_every: int = 50) -> str:
    """起后台线程批量上传，返回 task_id（UI 轮询 status）。

    log：可选日志回调 —— 上传过程写进设置页「实时日志」（勇哥 2026-09-23 要求：
         「上传在日志里面没有看到，需要把这个放在日志里面」）。
    """
    say = log or (lambda *_a, **_k: None)
    with _TASKS_LOCK:
        _task_seq[0] += 1
        tid = "t%d" % _task_seq[0]
        _TASKS[tid] = {"state": "running", "total": len(nos), "done": 0,
                       "ok": 0, "fail": 0, "current": "", "errors": [],
                       "started_at": datetime.now().strftime("%H:%M:%S")}

    def _run():
        cfg = _cfg()
        t0 = time.time()
        bucket = (cfg.get("cos") or {}).get("bucket")
        say("[PDF云] 开始上传 %d 份 PDF → COS（%s）" % (len(nos), bucket))
        try:
            cos, cli = client(cfg)
        except Exception as e:                                # noqa: BLE001
            say("[PDF云] ✗ 初始化失败：%s" % e)
            with _TASKS_LOCK:
                _TASKS[tid].update(state="error", errors=[str(e)])
            return

        pairs = []
        for i, no in enumerate(nos, 1):
            with _TASKS_LOCK:
                _TASKS[tid]["current"] = no
            try:
                r = upload_one(no, cos, cli)
                with _TASKS_LOCK:
                    _TASKS[tid]["done"] += 1
                    if r.get("ok"):
                        _TASKS[tid]["ok"] += 1
                        pairs.append((no, r["url"]))
                    else:
                        _TASKS[tid]["fail"] += 1
                        _TASKS[tid]["errors"].append("%s %s" % (no, r.get("msg")))
            except Exception as e:                            # noqa: BLE001
                with _TASKS_LOCK:
                    _TASKS[tid]["done"] += 1
                    _TASKS[tid]["fail"] += 1
                    _TASKS[tid]["errors"].append("%s %s: %s" % (no, type(e).__name__, e))
            if i % progress_every == 0:
                with _TASKS_LOCK:
                    s = dict(_TASKS[tid])
                say("[PDF云] 进度 %d/%d（成功 %d / 失败 %d）· 用时 %.0fs"
                    % (i, len(nos), s["ok"], s["fail"], time.time() - t0))

        with _TASKS_LOCK:
            s = dict(_TASKS[tid])
        say("[PDF云] 上传完成：成功 %d / 失败 %d，用时 %.0fs"
            % (s["ok"], s["fail"], time.time() - t0))
        if s["errors"]:
            say("[PDF云] 失败样例（最多 5 条）：%s" % "; ".join(s["errors"][:5]))

        pushed = {"ok": True, "sent": 0}
        if push_online and pairs:
            try:
                pushed = push_urls(cfg, pairs)
            except Exception as e:                            # noqa: BLE001
                pushed = {"ok": False, "errors": [str(e)]}
            say("[PDF云] 回推线上 pdf_url %d 条：%s"
                % (len(pairs), "成功 %s 条" % pushed.get("sent") if pushed.get("ok")
                   else ("失败 %s" % (pushed.get("errors") or ""))))
        with _TASKS_LOCK:
            _TASKS[tid].update(state="done", current="", pushed=pushed)

    threading.Thread(target=_run, daemon=True, name="pdf-upload-%s" % tid).start()
    return tid


def task_status(tid: str) -> dict:
    with _TASKS_LOCK:
        return dict(_TASKS.get(tid) or {"state": "unknown"})


# ---------------------------------------------------------------- 下载轮自动上云
# v1.9.65（勇哥 2026-09-23：「只要我们这边能正常取得数据，这个 PDF 文件同时也会
# 上传到线上」）—— 下载轮每落一份 PDF（Store.set_pdf 回写时）就 kick 进待传队列，
# 单例 worker 每 30s 把积累的番号批量交给 start_upload（自带日志/进度/回推线上）。
_AUTO_PENDING: set = set()
_AUTO_LOCK = threading.Lock()
_AUTO_WORKER_STARTED = [False]
_AUTO_LOG = [None]           # 由 web/app.log 注入（写 server.log + 设置页实时日志）
_AUTO_BATCH = 500            # 单批最多传多少份（避免一轮抓 1000 份时任务过大）
_AUTO_DELAY_S = 30           # 攒批间隔：下载完等 30s 再传，减少碎片任务


def kick_auto_upload(nos, log=None) -> None:
    """把「本轮新下载」的 PDF 番号踢进自动上云队列（幂等合并，绝不阻塞下载线程）。"""
    nos = [str(n) for n in (nos or []) if n]
    if not nos:
        return
    if log is not None:
        _AUTO_LOG[0] = log
    with _AUTO_LOCK:
        _AUTO_PENDING.update(nos)
        if not _AUTO_WORKER_STARTED[0]:
            _AUTO_WORKER_STARTED[0] = True
            threading.Thread(target=_auto_worker, daemon=True,
                             name="pdf-cloud-auto").start()


def _auto_worker():
    def say(*a, **_k):
        cb = _AUTO_LOG[0]
        if cb:
            try:
                cb(*a)
            except Exception:                                 # noqa: BLE001
                pass
        else:
            print("[PDF云·自动]", *a, flush=True)
    while True:
        time.sleep(_AUTO_DELAY_S)
        try:
            with _AUTO_LOCK:
                batch = sorted(_AUTO_PENDING)[:_AUTO_BATCH]
            if not batch:
                continue
            # 幂等：库里已有 pdf_url 的跳过（续期归自动重签线程管）
            con = _connect()
            try:
                ph = ",".join("?" * len(batch))
                have = {r[0] for r in con.execute(
                    "SELECT property_no FROM properties WHERE property_no IN (%s) "
                    "AND pdf_url LIKE 'http%%'" % ph, batch)}
            finally:
                con.close()
            todo = [n for n in batch if n not in have]
            if todo:
                say("[PDF云·自动] 检测到 %d 份新 PDF（已上云跳过 %d）→ 自动上传"
                    % (len(todo), len(batch) - len(todo)))
                start_upload(todo, push_online=True, log=say)
            with _AUTO_LOCK:
                _AUTO_PENDING.difference_update(batch)
        except Exception as e:                                # noqa: BLE001
            say("[PDF云·自动] 队列处理异常（番号保留重试）：%s: %s"
                % (type(e).__name__, e))


# ------------------------------------------------- 欠账巡检（v1.9.67 兜底自愈）
# 为什么要巡检：PDF 落库有**多条写入路径**——`pipeline.ingest` 整行 upsert、
#   `store.set_pdf`、手工补抓…。v1.9.66 把自动上云钩子只挂在 `store.set_pdf`
#   上，而主路径其实走 `pipeline.ingest` → 钩子从未触发（09-23 实测 4 份新 PDF
#   没上云、全日志 0 条自动上云记录）。巡检以「库 + 盘」为准，天然覆盖所有写入
#   路径，并顺带自愈历史欠账与上传失败。
_SWEEP_STARTED = [False]
_SWEEP_INTERVAL_MIN = 10      # 巡检间隔（分钟）
_SWEEP_FIRST_DELAY_S = 20     # 启动后第一次巡检延迟（等服务起稳、不抢启动资源）
_SWEEP_BATCH = 500            # 单轮最多补多少份


def pending_nos() -> list:
    """本地库中「有 PDF 落盘、但还没上云」的番号（文件必须在盘上才算欠账）。

    `pdf_path` 存的是**相对工程根**的路径（如 `data/attachments/xxx.pdf`），
    绝对路径也兼容；两者都不在时再按标准命名 `attachments/<番号>.pdf` 兜底。
    """
    con = _connect()
    try:
        rows = con.execute(
            "SELECT property_no, pdf_path FROM properties "
            "WHERE pdf_path IS NOT NULL AND (pdf_url IS NULL OR pdf_url NOT LIKE 'http%')"
        ).fetchall()
    finally:
        con.close()
    out = []
    for no, p in rows:
        cand = Path(str(p or ""))
        if not cand.is_absolute():
            cand = ROOT / cand
        if not cand.exists():
            cand = attachments_dir() / ("%s.pdf" % no)
        if cand.exists():
            out.append(str(no))
    return out


def _has_running_upload() -> bool:
    with _TASKS_LOCK:
        return any(t.get("state") == "running" for t in _TASKS.values())


def sweep_pending(push_online: bool = True, log=None, cap: int = _SWEEP_BATCH) -> dict:
    """补齐「本地有 PDF 但未上云」的欠账（幂等，可反复跑；上传中则不抢跑）。"""
    say = log or (lambda *_a, **_k: None)
    if log is not None:
        _AUTO_LOG[0] = log
    nos = pending_nos()[:cap]
    if not nos:
        return {"ok": True, "pending": 0, "task": None}
    if _has_running_upload():
        return {"ok": True, "pending": len(nos), "task": None,
                "skipped": "upload_running"}
    say("[PDF云·巡检] 发现 %d 份本地有 PDF 但未上云 → 自动上传" % len(nos))
    tid = start_upload(nos, push_online=push_online, log=say)
    return {"ok": True, "pending": len(nos), "task": tid}


def _sweep_worker(interval_min: int, log=None):
    time.sleep(_SWEEP_FIRST_DELAY_S)
    first = True
    while True:
        try:
            res = sweep_pending(push_online=True, log=log)
            if first and not res.get("pending"):
                cb = _AUTO_LOG[0]
                if cb:
                    try:
                        cb("[PDF云·巡检] 首检完成：无欠账")
                    except Exception:                         # noqa: BLE001
                        pass
        except Exception as e:                                # noqa: BLE001
            cb = _AUTO_LOG[0]
            if cb:
                try:
                    cb("[PDF云·巡检] 异常（下轮重试）：%s: %s"
                       % (type(e).__name__, e))
                except Exception:                             # noqa: BLE001
                    pass
        first = False
        time.sleep(max(60, int(interval_min) * 60))


def _start_auto_sweep_loop(interval_min: int = _SWEEP_INTERVAL_MIN, log=None) -> None:
    """常驻「PDF 上云巡检」线程（幂等，本地启动时由启动器拉起；线上绝不调用）。"""
    if _SWEEP_STARTED[0]:
        return
    _SWEEP_STARTED[0] = True
    if log is not None:
        _AUTO_LOG[0] = log
    threading.Thread(target=_sweep_worker,
                     kwargs={"interval_min": interval_min, "log": log},
                     daemon=True, name="pdf-cloud-sweep").start()
    if log:
        log("[PDF云·巡检] 线程已启动（每 %d 分钟扫一次「本地有 PDF 未上云」）"
            % interval_min)


# ---------------------------------------------------------------- 自动重签
def resign_due(threshold: int = RESIGN_THRESHOLD, push_online: bool = True,
               log=None) -> dict:
    """重签"剩余有效期 < threshold"的 pdf_url，并回推线上（勇哥：本地常驻 + 自动重签）。"""
    say = log or (lambda *_a, **_k: None)
    cfg = _cfg()
    try:
        cos, cli = client(cfg)
    except Exception as e:                                    # noqa: BLE001
        say("[PDF云] 未配置/不可用，跳过重签：%s" % e)
        return {"ok": False, "resigned": 0, "msg": str(e)}

    con = _connect()
    try:
        rows = con.execute(
            "SELECT property_no, pdf_url FROM properties WHERE pdf_url IS NOT NULL AND pdf_url != ''").fetchall()
    finally:
        con.close()

    now = time.time()
    due = []
    for no, u in rows:
        try:
            tail = str(u).split("q-sign-time=")[1].split("&")[0]
            end = int(tail.split("%3B")[1] if "%3B" in tail else tail.split(";")[1])
        except Exception:                                     # noqa: BLE001
            due.append(no)          # 解析不出到期时间的，一律重签（最安全）
            continue
        if end - now < threshold:
            due.append(no)

    if not due:
        say("[PDF云] 无需重签（%d 条均在有效期内）" % len(rows))
        return {"ok": True, "resigned": 0, "checked": len(rows)}

    pairs, fails = [], []
    for no in due:
        try:
            if not (attachments_dir() / ("%s.pdf" % no)).exists():
                continue
            url = cli.get_presigned_download_url(
                Bucket=cos["bucket"], Key="%s.pdf" % no, Expired=DEFAULT_EXPIRE)
            if _write_pdf_url(no, url):
                pairs.append((no, url))
        except Exception as e:                                # noqa: BLE001
            fails.append("%s %s" % (no, e))

    pushed = {"ok": True, "sent": 0}
    if push_online and pairs:
        try:
            pushed = push_urls(cfg, pairs, log=say)
        except Exception as e:                                # noqa: BLE001
            pushed = {"ok": False, "errors": [str(e)]}

    say("[PDF云] 自动重签 %d 条（共 %d 条在册），回推 %s"
        % (len(pairs), len(rows), "成功" if pushed.get("ok") else "失败"))
    return {"ok": True, "resigned": len(pairs), "checked": len(rows),
            "pushed": pushed, "fails": fails}


def repush_all(log=None) -> dict:
    """把本地库里**全部非空 pdf_url** 回推线上（一键补齐）。

    用途（2026-09-23 真实场景）：线上 `/api/ingest` 曾把 pdf_url 过滤掉，
    导致批量上传"回推成功但线上不显示"；修复收数侧后，用本函数一键把已生成的
    pdf_url 补推上线，**不必重传 COS、也不必重签**。
    """
    say = log or (lambda *_a, **_k: None)
    cfg = _cfg()
    con = _connect()
    try:
        rows = con.execute(
            "SELECT property_no, pdf_url FROM properties WHERE pdf_url IS NOT NULL AND pdf_url != ''").fetchall()
    finally:
        con.close()
    pairs = [(n, u) for n, u in rows]
    if not pairs:
        say("[PDF云] 本地没有已上传的 pdf_url，无需回推")
        return {"ok": True, "total": 0, "sent": 0}
    say("[PDF云] 开始全量回推 %d 条 pdf_url 到线上…" % len(pairs))
    try:
        res = push_urls(cfg, pairs, log=say)
    except Exception as e:                                    # noqa: BLE001
        say("[PDF云] ✗ 回推异常：%s" % e)
        return {"ok": False, "total": len(pairs), "sent": 0, "errors": [str(e)]}
    say("[PDF云] 回推完成：%s（本批 %d 条）"
        % (("成功 %s 条" % res.get("sent")) if res.get("ok") else ("失败 %s" % (res.get("errors") or "")),
           res.get("sent") or 0))
    return {"ok": bool(res.get("ok")), "total": len(pairs), "sent": res.get("sent"),
            "errors": res.get("errors")}
