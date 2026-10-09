# -*- coding: utf-8 -*-
"""真·JS 回归：用 Node 实跑 web/static/prop_card.js 的 PropCard.full，
断言员工收藏卡片（与查询页同构渲染链）正确渲染中介公司 + 电话，且 broker 空时不渲染。

与 test_detail_pairs.py（Python 侧 _detail_pairs / _row_payload）及 search.html 母本 grep
共同构成「中介公司信息三链一致性门禁」，防止重构时漏掉某条链导致信息再次丢失。
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "prop_card_js_harness.js"
PROP_JS = ROOT / "web" / "static" / "prop_card.js"

# ---------------------------------------------------------------------------
# 跨机门禁修复（2026-10-09，仅改 tests/，不抬 core/version.py：不影响任何线上产物）：
# 原实现把 Node 路径硬编码为某一台机器的绝对路径，
# 换机（如二巴工作区 E:\workbuddy\...）后该路径不存在 → pytest.skip →
# 「三链 broker 防回归门禁」的 JS 链静默失效，等于没门禁。
# 现改为多机可移植探测：环境变量 > PATH > 常见托管路径 > 旧硬编码路径；
# 且探测不到时 fail（不再 skip）——门禁不允许悄悄失效。
# ---------------------------------------------------------------------------
_FALLBACK_NODE = r"C:\Users\25374\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"


def _find_node():
    env = os.environ.get("OSAKA_NODE_BIN")
    if env and Path(env).exists():
        return env
    which = shutil.which("node") or shutil.which("node.exe")
    if which:
        return which
    home = Path.home()
    for rel in (
        r".workbuddy\binaries\node\versions\22.22.2-6\node.exe",
        r".workbuddy\binaries\node\versions\22.22.2-6\node",
        r"AppData\Local\Programs\nodejs\node.exe",
    ):
        p = home / rel
        if p.exists():
            return str(p)
    if Path(_FALLBACK_NODE).exists():
        return _FALLBACK_NODE
    return None


def test_real_js_prop_card_broker():
    node = _find_node()
    if not node:
        pytest.fail(
            "未找到 Node，JS 链门禁无法执行（可设环境变量 OSAKA_NODE_BIN 指向 node.exe）："
            "已尝试 PATH、%s 及常见托管路径" % _FALLBACK_NODE
        )
    if not PROP_JS.exists():
        pytest.fail("prop_card.js 不存在：%s" % PROP_JS)
    r = subprocess.run([str(node), str(HARNESS), str(PROP_JS)],
                       capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
    assert r.returncode == 0, "prop_card.js broker 真机断言失败"
