# -*- coding: utf-8 -*-
"""真·JS 回归：用 Node 实跑 web/static/fieldmap.js 的 fieldLabel/fieldValue/fieldSkip。

这是查询页展开区/对比页前端实际使用的代码路径，不能只靠 Python 复刻断言。
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "fieldmap_js_harness.js"
FIELD_JS = ROOT / "web" / "static" / "fieldmap.js"
NODE = r"C:\Users\25374\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"


def test_real_js_fieldmap():
    if not Path(NODE).exists():
        pytest.skip("Node 不存在：%s" % NODE)
    r = subprocess.run([str(NODE), str(HARNESS), str(FIELD_JS)],
                       capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
    assert r.returncode == 0, "fieldmap.js 真机断言失败"
