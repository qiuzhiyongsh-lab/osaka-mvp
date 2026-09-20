# -*- coding: utf-8 -*-
"""账号双向同步（v1.9.28）—— 本地主库 ⇄ 线上运行库，走已有的机器对机器通道。

【要解决的两个真实缺口 · 勇哥 2026-09-20 实测反馈】
  ① 本地 /staff 重置了随机码 → 线上仍是旧哈希 → 员工手持新码报「密码错误」。
     以前唯一的同步方式是"重新发布"这条**人工**通道，导致：
       重置 → 忘了导出/发布 → 线上对不上（连续两次踩同一个坑）。
  ② 员工在**线上**自设了密码 → 本地 /staff 永远显示「待改密」→ 线下看不到真实状态。
     根因：同步原先只有**单向**（本地 → 种子 → 发布 → 线上），没有回流通道。

【做法】复用线上已有的 `/api/ingest` 那套凭据与风格（同一个 `publish.endpoint` +
  同一个 `publish.ingest_token`），新增两个**机器对机器**接口，都要求 X-Publish-Token：
    · POST /api/accounts/seed-sync   本地 → 线上：把最新种子推上去（可点名 force_users）
    · GET  /api/accounts/state       线上 → 本地：读线上账户状态，用于回流对账
  于是「重置完立刻生效」不再依赖重新发布，/staff 也能反映员工在线上自设的密码。

【硬约束】
  · 只传**哈希**，明文密码永远不经过这条通道（种子文件本身就不含明文）。
  · 任何失败（网络不通、令牌不对、线上没升到 v1.9.28）只记日志/水位线，
    **绝不阻断本地操作** —— 重置随机码照常成功、码照常发给员工。
  · 只传到 publish 里配好的 endpoint，不猜、不外发。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import urllib.error
import urllib.request
from datetime import datetime

from core import accounts as acc_mod
from core import config as cfgmod


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conf(cfg: dict) -> tuple[str, str]:
    """返回 (endpoint, token)。键名兜底同 publisher：token / ingest_token 谁有值用谁。"""
    pub = (cfg or {}).get("publish") or {}
    endpoint = str(pub.get("endpoint") or "").strip()
    token = str(pub.get("token") or pub.get("ingest_token") or "").strip()
    return endpoint, token


def seed_path(cfg: dict) -> pathlib.Path:
    return pathlib.Path(str(cfgmod.paths(cfg)["root"])) / "accounts.seed.json"


def _post(url: str, token: str, payload: dict, timeout: int = 10) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Publish-Token": token or ""},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        txt = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(txt)
    except Exception:                                        # noqa: BLE001
        return {"ok": False, "error": "线上返回非 JSON：" + txt[:200]}


def _get(url: str, token: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, method="GET",
                                 headers={"X-Publish-Token": token or ""})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        txt = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(txt)
    except Exception:                                        # noqa: BLE001
        return {"ok": False, "error": "线上返回非 JSON：" + txt[:200]}


def _fail(msg: str, say) -> dict:
    say("⚠ 账号同步：" + msg)
    try:
        acc_mod.set_sync_state(last_error=msg[:500])
    except Exception:                                        # noqa: BLE001
        pass
    return {"ok": False, "errors": [msg]}


# ---------------------------------------------------------------------------
# 本地 → 线上：推送种子
# ---------------------------------------------------------------------------
def push_seed(cfg: dict, log=None, force_users=None, timeout: int = 10) -> dict:
    """把本地最新种子推到线上（幂等）。

    force_users：被点名的账号**强制覆盖**密码哈希/cleared —— 用于「管理员刚重置了
      这个账号的随机码」这一事件（该账号线上可能早已 cleared=1，按保守规则不会覆盖）。
      周期兜底推送不传 force_users（保护员工在线上自设的密码不被回退）。

    内容指纹与上次推送相同、且没点名 force_users 时**直接跳过**，不产生任何网络请求
    —— 于是可以被"每 10 分钟"的定时器无成本地反复调用。
    """
    say = log or (lambda *_a, **_k: None)
    endpoint, token = _conf(cfg)
    force = sorted({str(u).strip() for u in (force_users or []) if str(u or "").strip()})
    if not endpoint or not token:
        return _fail("未配置 publish.endpoint / publish.token，无法上云同步", say)
    try:
        data = acc_mod.export_seed(str(seed_path(cfg)))
    except Exception as e:                                   # noqa: BLE001
        return _fail("导出本地种子失败：%s: %s" % (type(e).__name__, e), say)
    rows = data.get("accounts") or []
    fp = hashlib.md5(json.dumps(rows, sort_keys=True, ensure_ascii=False)
                     .encode("utf-8")).hexdigest()
    if not force and acc_mod.sync_state().get("last_push_md5") == fp:
        return {"ok": True, "skipped": True, "accounts": len(rows),
                "reason": "与上次推送内容一致，跳过"}
    url = endpoint.rstrip("/") + "/api/accounts/seed-sync"
    try:
        res = _post(url, token, {"accounts": rows, "exported_at": data.get("exported_at"),
                                 "force_users": force, "source": "osaka-mvp"},
                    timeout=timeout)
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            detail = e.read().decode("utf-8", "replace")[:180]
        except Exception:                                    # noqa: BLE001
            detail = ""
        if code == 404:
            return _fail("线上还没有这个接口（404）→ 线上版本低于 v1.9.28，"
                         "需要发布一次才能启用「免重发布同步」", say)
        if code == 401:
            return _fail("线上拒绝（401）→ 两种可能：①线上版本低于 v1.9.28（该接口被旧版"
                         "登录守卫拦下，发布一次即可）；②令牌不一致（本地 publish.ingest_token "
                         "与线上 config 不符）", say)
        return _fail("推送失败 HTTP %s %s" % (code, detail), say)
    except Exception as e:                                   # noqa: BLE001
        return _fail("推送失败 %s: %s" % (type(e).__name__, e), say)
    if not res.get("ok"):
        return _fail("线上拒绝：%s" % str(res.get("error") or res)[:200], say)
    stat = res.get("stat") or {}
    acc_mod.set_sync_state(last_push_at=_now(), last_push_md5=fp,
                           last_push_stat=json.dumps(stat, ensure_ascii=False),
                           last_error=None)
    say("☁ 账号种子已上云：%d 个账号%s → 线上回执 %s"
        % (len(rows), ("（强制覆盖：%s）" % ",".join(force)) if force else "", stat))
    return {"ok": True, "accounts": len(rows), "stat": stat, "force_users": force}


# ---------------------------------------------------------------------------
# 线上 → 本地：拉状态并回流
# ---------------------------------------------------------------------------
def pull_and_adopt(cfg: dict, log=None, timeout: int = 10) -> dict:
    """读线上账户状态，把"员工在线上自设的密码"回流到本地（规则见 accounts.adopt_remote）。"""
    say = log or (lambda *_a, **_k: None)
    endpoint, token = _conf(cfg)
    if not endpoint or not token:
        return _fail("未配置 publish.endpoint / publish.token，无法回流", say)
    url = endpoint.rstrip("/") + "/api/accounts/state"
    try:
        res = _get(url, token, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return _fail("线上还没有 /api/accounts/state（404）→ 线上版本低于 v1.9.28", say)
        return _fail("拉取线上状态失败 HTTP %s" % e.code, say)
    except Exception as e:                                   # noqa: BLE001
        return _fail("拉取线上状态失败 %s: %s" % (type(e).__name__, e), say)
    if not res.get("ok"):
        return _fail("线上拒绝：%s" % str(res.get("error") or res)[:200], say)
    remote = res.get("accounts") or []
    rep = acc_mod.adopt_remote(remote, acc_mod.sync_state().get("last_push_at"))
    if rep.get("adopted"):
        try:
            acc_mod.export_seed(str(seed_path(cfg)))          # 回流后立刻重写种子（发布包也跟上）
        except Exception as e:                               # noqa: BLE001
            say("⚠ 回流后重写种子失败：" + str(e))
        say("☁ 线上自设密码已回流本地：%s（/staff 状态随之变为「正常」）" % ",".join(rep["adopted"]))
    if rep.get("conflict"):
        say("⚠ 两侧均已改密但哈希不同，保持本地不动（需人工确认）：%s" % ",".join(rep["conflict"]))
    acc_mod.set_sync_state(last_pull_at=_now(),
                           last_pull_stat=json.dumps(rep, ensure_ascii=False))
    return {"ok": True, "remote": len(remote), **rep}


# ---------------------------------------------------------------------------
# 一把梭：推 + 拉（/staff「立即同步」按钮、本地启动时各调一次）
# ---------------------------------------------------------------------------
def sync_now(cfg: dict, log=None, force_users=None, timeout: int = 10) -> dict:
    say = log or (lambda *_a, **_k: None)
    up = push_seed(cfg, log=say, force_users=force_users, timeout=timeout)
    down = pull_and_adopt(cfg, log=say, timeout=timeout)
    return {"ok": bool(up.get("ok")) and bool(down.get("ok")),
            "push": up, "pull": down}


def status(cfg: dict | None = None) -> dict:
    """给 /staff 页面看的同步状态（不含任何凭据）。"""
    try:
        st = acc_mod.sync_state()
    except Exception:                                        # noqa: BLE001
        st = {}
    endpoint, token = _conf(cfg or {})
    st["endpoint"] = endpoint
    st["configured"] = bool(endpoint and token)
    return st
