# -*- coding: utf-8 -*-
r"""
把 config.yaml 里的**敏感值**迁移到 config.local.yaml（本机、不入库），
并把 config.yaml 里的对应位置清成空串，让它回归「模板」角色。

为什么必须做：
    config.yaml 目前**没有**被 .gitignore 忽略，而它里面已经存着真凭据
    （实测：publish.token / publish.ingest_token 各 32 字符、site.login_url 等）。
    一旦 `git add -A` 或某个自动化批量提交，凭据就进了版本库 —— 且**历史 commit
    无法用 .gitignore 撤回**（必须轮换 token）。本脚本是止血第一步。

安全设计：
    · 默认 **--dry-run**（只报要做哪些，不写任何文件）；显式 `--apply` 才执行。
    · `--apply` 前自动备份 config.yaml。
    · **永不打印**任何真实值，只打码显示长度。
    · 迁移后 core.config.load() 仍能读到同样的值（local 覆盖），行为不变。

用法：
    python tools/migrate_config_local.py            # dry-run
    python tools/migrate_config_local.py --apply    # 真迁移（先备份）
"""
from __future__ import annotations

import argparse
import copy
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yaml"
LOCAL = ROOT / "config.local.yaml"

# 需要迁移到本机的敏感键（路径 → 迁移理由）
SENSITIVE_PATHS = [
    ("site", "login_url", "含会话/跳转凭据"),
    ("publish", "token", "推送令牌"),
    ("publish", "ingest_token", "导入令牌"),
    ("ai", "api_key", "第三方 API 密钥"),
]


def mask(v) -> str:
    s = "" if v is None else str(v)
    return "<空>" if not s else "<有值 len=%d>" % len(s)


def collect(cfg: dict):
    """返回 [(路径元组, 当前值)]，只收「有值」的。"""
    out = []
    for section, key, why in SENSITIVE_PATHS:
        sec = cfg.get(section)
        if not isinstance(sec, dict):
            continue
        v = sec.get(key)
        if v not in (None, "", []):
            out.append(((section, key), v, why))
    return out


def set_path(d: dict, path, value):
    cur = d
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
    cur[path[-1]] = value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写文件（默认 dry-run）")
    args = ap.parse_args()

    if not CONFIG.exists():
        print("找不到 config.yaml：%s" % CONFIG)
        return 1

    cfg = yaml.safe_load(open(CONFIG, "r", encoding="utf-8")) or {}
    hits = collect(cfg)

    print("=" * 68)
    print("待迁移到 config.local.yaml 的敏感项：%d 个（dry-run=%s）" % (len(hits), not args.apply))
    print("=" * 68)
    for path, v, why in hits:
        print("  %-28s %-14s %s" % (".".join(path), mask(v), why))
    if not hits:
        print("  （没有需要迁移的有值敏感项）")

    # ai.api_key 即使为空也要在 local 里占位，方便勇哥直接填
    has_ai = any(p == ("ai", "api_key") for p, _v, _w in hits)
    print()
    print("  ai.api_key 将在 local 中占位：%s" % ("已有值，迁移" if has_ai else "留空待填"))

    if not args.apply:
        print()
        print("[dry-run] 未改动任何文件。确认无误后加 --apply。")
        return 0

    # ---- apply ----
    bak = CONFIG.with_suffix(
        ".yaml.bak_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    shutil.copy2(CONFIG, bak)
    print("\n已备份 config.yaml → %s" % bak.name)

    local = {}
    if LOCAL.exists():
        local = yaml.safe_load(open(LOCAL, "r", encoding="utf-8")) or {}

    new_cfg = copy.deepcopy(cfg)
    moved = 0
    for path, v, _why in hits:
        set_path(local, path, v)
        set_path(new_cfg, path, "")          # 模板里清空
        moved += 1
    # ai.api_key 占位（即使为空）
    local.setdefault("ai", {}).setdefault("api_key", local.get("ai", {}).get("api_key", ""))

    with open(LOCAL, "w", encoding="utf-8") as f:
        f.write(
            "# -*- 本机私密配置（**绝不入库**，已被 .gitignore 忽略） -*-\n"
            "# 由 tools/migrate_config_local.py 生成于 %s\n"
            "# 这里的值会**覆盖** config.yaml 的同名键。\n"
            "# 需要给别人模板时：只发 config.yaml，不要发本文件。\n"
            % datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        yaml.safe_dump(local, f, allow_unicode=True, sort_keys=False)

    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write(
            "# -*- 配置**模板**：非敏感配置 → 入库 -*-\n"
            "# 凭据类（token / 密码 / api_key）请写进 config.local.yaml（不入库）。\n"
            "# 加载顺序：_DEFAULTS ← config.yaml ← config.local.yaml（后者覆盖前者）。\n"
        )
        yaml.safe_dump(new_cfg, f, allow_unicode=True, sort_keys=False)

    print("已迁移 %d 项 → config.local.yaml；config.yaml 对应位置已清空。" % moved)
    print()
    print("下一步（必须）：")
    print("  1. 把 config.local.yaml 加进 .gitignore（本仓库已规划，请确认生效）")
    print("  2. **轮换**已进过库的 token —— .gitignore 只管未来，历史 commit 里的仍在")
    print("  3. 重启 8765 让新加载逻辑生效（内存里还是旧配置）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
