# -*- coding: utf-8 -*-
"""终极回归门禁：实跑 tools/verify_field_labels.py（真实库全量 + 五断言）。

这是「后台机器语言不得上屏」的永久回归门禁，覆盖：
  ① 全库键覆盖 ② 四语文案齐 ③ 枚举值覆盖 ④ Python 渲染路径 ⑤ JS 渲染路径。
退出码 0 = 全绿。
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = r"C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe"


def test_verify_field_labels_gate(real_db_copy):
    if not Path(VENV_PY).exists():
        pytest.skip("venv 不存在：%s" % VENV_PY)
    r = subprocess.run(
        [str(VENV_PY), str(ROOT / "tools" / "verify_field_labels.py"), "--db", str(real_db_copy)],
        capture_output=True, text=True,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
    assert r.returncode == 0, "verify_field_labels 红项未过"
