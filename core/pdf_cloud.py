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


def start_upload(nos: list[str], push_online: bool = True,
                 date_from: str | None = None, date_to: str | None = None) -> str:
    """起后台线程批量上传，返回 task_id（UI 轮询 status）。"""
    with _TASKS_LOCK:
        _task_seq[0] += 1
        tid = "t%d" % _task_seq[0]
        _TASKS[tid] = {"state": "running", "total": len(nos), "done": 0,
                       "ok": 0, "fail": 0, "current": "", "errors": [],
                       "started_at": datetime.now().strftime("%H:%M:%S")}

    def _run():
        cfg = _cfg()
        try:
            cos, cli = client(cfg)
        except Exception as e:                                # noqa: BLE001
            with _TASKS_LOCK:
                _TASKS[tid].update(state="error", errors=[str(e)])
            return
        pairs = []
        for no in nos:
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
        pushed = {"ok": True, "sent": 0}
        if push_online and pairs:
            try:
                pushed = push_urls(cfg, pairs)
            except Exception as e:                            # noqa: BLE001
                pushed = {"ok": False, "errors": [str(e)]}
        with _TASKS_LOCK:
            _TASKS[tid].update(state="done", current="", pushed=pushed)

    threading.Thread(target=_run, daemon=True, name="pdf-upload-%s" % tid).start()
    return tid


def task_status(tid: str) -> dict:
    with _TASKS_LOCK:
        return dict(_TASKS.get(tid) or {"state": "unknown"})


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
