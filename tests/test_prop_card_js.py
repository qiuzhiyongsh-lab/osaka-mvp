# -*- coding: utf-8 -*-
"""真·JS 回归：用 Node 实跑 web/static/prop_card.js 的 PropCard.full，
断言员工收藏卡片（与查询页同构渲染链）正确渲染中介公司 + 电话，且 broker 空时不渲染。

与 test_detail_pairs.py（Python 侧 _detail_pairs / _row_payload）及 search.html 母本 grep
共同构成「中介公司信息三链一致性门禁」，防止重构时漏掉某条链导致信息再次丢失。
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "prop_card_js_harness.js"
PROP_JS = ROOT / "web" / "static" / "prop_card.js"
# 托管 Node（实际可用版本见二进制上下文）；路径漂移时本测试自动 skip，不阻塞其它用例。
NODE = r"C:\Users\25374\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"


def test_real_js_prop_card_broker():
    if not Path(NODE).exists():
        pytest.skip("Node 不存在：%s" % NODE)
    if not PROP_JS.exists():
        pytest.skip("prop_card.js 不存在：%s" % PROP_JS)
    r = subprocess.run([str(NODE), str(HARNESS), str(PROP_JS)],
                       capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
    assert r.returncode == 0, "prop_card.js broker 真机断言失败"
