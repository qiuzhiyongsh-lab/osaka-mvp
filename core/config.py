# -*- coding: utf-8 -*-
"""配置读写：把 config.yaml 读成 dict，并支持网页端热改（写回文件）。"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent          # osaka-mvp/
CONFIG_PATH = ROOT / "config.yaml"
# v1.7.5：本机私密文件（**不入库**，已被 .gitignore 忽略）。
#   config.yaml      = 模板 / 非敏感配置 → 入库
#   config.local.yaml= 真凭据（token / 密码 / api_key）→ 只在本机
# 加载时 local **浅覆盖**模板；local 不存在时行为与以前完全一致（向后兼容）。
LOCAL_PATH = ROOT / "config.local.yaml"

_DEFAULTS: dict[str, Any] = {
    "app": {"name": "大阪房源查询系统（本地版 MVP）", "mode": "demo", "output_root": "./data"},
    "schedule": {
        "enabled": False, "mode": "interval", "interval_hours": 2,
        "random_min_hours": 1, "random_max_hours": 3,
        "timezone": "Asia/Tokyo",
        "window": {"start": "07:00", "end": "22:00"}, "run_on_start": False,
    },
    # v1.9.26 · PRD 25：两节**带默认值** → 老机器升级后不用手改 config.yaml 也能跑
    "local_extract": {
        "enabled": True, "prefer_local": True, "min_fields": 3,
        "ocr_enabled": True, "ocr_dpi": 300, "ocr_lang": "jpn",
        "ocr_timeout_sec": 60, "ocr_max_pages": 1, "tesseract_path": "",
        "fallback_model": True, "fallback_max_per_run": 50,
    },
    "schedule_ai": {
        "enabled": True, "timezone": "local",
        "window": {"start": "23:00", "end": "06:00"},
        "max_per_run": 300, "workers": 2, "run_on_start": False,
        "force_rerun": False, "backfill_all": False, "backfill_batch": 300,
    },
    # v1.9.31 / PRD-25：AI 读取范围（设置项手动勾选哪些種目走 AI 读取）。
    # 故意放顶层、不放在 ai 块内 —— ai 块含真 api_key（在 config.local.yaml），
    # cfgmod.save 会整块剔除私密键，read_kinds 写不回；顶层 ai_read_scope 可正常持久化。
    "ai_read_scope": [],
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:                                    # noqa: BLE001
        # 坏文件不能让整个服务起不来 —— 宁可退化为「不加载」，也比崩掉好
        return {}


def load() -> dict:
    """`_DEFAULTS` ← `config.yaml`（模板）← `config.local.yaml`（本机真值，覆盖）。"""
    if not CONFIG_PATH.exists():
        save(_DEFAULTS)
    tmpl = _read_yaml(CONFIG_PATH)
    local = _read_yaml(LOCAL_PATH)
    return _deep_merge(_deep_merge(_DEFAULTS, tmpl), local)


# ---------------------------------------------------------------------------
# v1.9.0 修 **VULN-01（高危 · 系统性）**：
#   save() 原来把「三层合并后的整份配置」写回 config.yaml —— 但 config.yaml 是
#   **入库文件**，于是 config.local.yaml 里的真凭据（token / ingest_token / access_code
#   / 以后加的密码）就被顺手写进 git。触发面极大：网页上任意一次「改配置→保存」
#   （发布设置 / 调度 / 采集设置 / 运行模式）都会中招，不需要任何人做错事。
#
#   修法（两道闸，只**减少**写入内容，不新增/不重排，所以正常配置照旧能保存）：
#     ① 键名闸 SECRET_KEYS / SECRET_PARTS：名字就像凭据的键，一律不带真值落盘；
#     ② 来源闸：凡 config.local.yaml 里定义过的键路径（如 publish.ingest_token），
#        写回时一律还原成 **config.yaml 模板里原本的值**（模板没有该键则置空占位）。
#        —— local 就是「不该入库」的那一层，这是权威口径，不依赖键名。
# ---------------------------------------------------------------------------
SECRET_KEYS = {
    "password", "passwd", "pwd", "secret", "token", "cookie", "session",
    "session_id", "api_key", "apikey", "access_key", "access_code",
    "user", "username", "userid", "user_id", "login_id", "mail",
    "email", "tel", "private_key", "cred_key",
}
SECRET_PARTS = ("password", "passwd", "secret", "token", "cookie", "cred",
                "access_code", "private_key")


def _key_paths(obj: Any, prefix: str = "") -> set:
    """收集 dict 里所有键路径（含中间层，如 publish 与 publish.token）。list 不展开。"""
    out: set = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.add(p)
            out |= _key_paths(v, p)
    return out


def _lookup(obj: Any, path: str, default: Any = None) -> Any:
    """按 "a.b.c" 取模板里的原值（找不到返回 default）。"""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _blank_like(v: Any) -> Any:
    """「模板里没有这个键」时的占位值：保留键、不保留任何真实信息。"""
    if isinstance(v, str):
        return ""
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    return None


def _strip_secrets(obj: Any, blocked: set, tmpl: Any, prefix: str = ""):
    """递归剔除 / 还原私密键值。返回 (新对象, 被处理的键路径列表)。"""
    handled: list = []
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            lk = str(k).lower()
            if path in blocked:
                # 来源闸：local 里有的键 → 用模板原值（模板没有就置空）
                out[k] = _lookup(tmpl, path, _blank_like(v))
                handled.append(path)
                continue
            if lk in SECRET_KEYS or any(p in lk for p in SECRET_PARTS):
                # 键名闸：名字就像凭据（且不是 local 那一层能解释的）→ 直接不带值落盘
                out[k] = _blank_like(v)
                handled.append(path)
                continue
            nv, d = _strip_secrets(v, blocked, _lookup(tmpl, path, {}), path)
            handled += d
            out[k] = nv
        return out, handled
    if isinstance(obj, list):
        out = []
        for i, v in enumerate(obj):
            nv, d = _strip_secrets(v, blocked, None, f"{prefix}[{i}]")
            handled += d
            out.append(nv)
        return out, handled
    return obj, handled


def save(cfg: dict) -> None:
    """写回 config.yaml（**入库的模板文件**）。真凭据绝不落进来 —— 见上方 VULN-01 说明。"""
    local = _read_yaml(LOCAL_PATH)
    tmpl = _read_yaml(CONFIG_PATH)
    clean, handled = _strip_secrets(cfg, _key_paths(local), tmpl)
    if handled:
        print(f"[config] save(): 已阻止 {len(set(handled))} 个私密键写入入库文件 "
              f"(config.yaml)：{sorted(set(handled))}")
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(clean, f, allow_unicode=True, sort_keys=False)


def output_root(cfg: dict) -> Path:
    """本地落盘根目录（相对路径按 osaka-mvp/ 解析）。"""
    raw = str(cfg.get("app", {}).get("output_root") or "./data")
    p = Path(raw)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p


def paths(cfg: dict) -> dict[str, Path]:
    """一次性拿到所有本地落盘目录，并确保存在。"""
    root = output_root(cfg)
    # v1.6 双版本：可用 OSAKA_DB 环境变量指向工作库（如 jproperty_v16.db），
    # 不设则用默认主库 jproperty.db。默认行为不变，主库路径不受影响。
    db_env = os.environ.get("OSAKA_DB")
    db_path = Path(db_env).resolve() if db_env else root / "jproperty.db"
    out = {
        "root": root,
        "db": db_path,
        "attachments": root / "attachments",     # PDF 落盘目录
        "exports": root / "exports",             # Excel 落盘目录
        "daily": root / "daily",                 # 按天 HTML 页
        "logs": root / "logs",
        "session": root / "session.json",
    }
    for k, v in out.items():
        if k != "db" and k != "session":
            v.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    return out
