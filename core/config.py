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


def save(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


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
