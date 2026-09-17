# -*- coding: utf-8 -*-
"""tests/ 公共夹具。

铁律（与项目标准一致）：
  · 一律用 **副本库**（copies of data/jproperty.db 到临时目录），绝不碰真库；
  · 绝不碰勇哥的 8765 进程；需要 Web 行为时用 Flask 测试客户端，不监听端口。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "web"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REAL_DB = ROOT / "data" / "jproperty.db"

# 铁律补充：任何「import app」若发生在未设 OSAKA_DB 时，会落到默认的真实库 data/jproperty.db，
# 触发回填写真库。这里在测试收集期就钉一个临时库，保证 import app 永不碰真库。
#
# ⚠ v1.7.5 踩坑修正：以前这里给的是**空文件**，结果一旦有测试在导入期先 import app，
#   app.STORE 就永久绑死在空库上 —— 后面 webapp fixture 改 OSAKA_DB 也无效（模块级单例），
#   于是「单独跑 test_api_query.py 全过、全量跑却 8 个失败（全部 0 条）」。
#   教训：**保护性假库必须是「真库副本」而不是空库**，否则它保护了不该保护的、破坏了要测的。
if not os.environ.get("OSAKA_DB"):
    _tmpdir = Path(tempfile.mkdtemp(prefix="osaka_test_"))
    _tmp = _tmpdir / "jproperty.db"
    if REAL_DB.exists():
        shutil.copy2(REAL_DB, _tmp)
    os.environ["OSAKA_DB"] = str(_tmp)


@pytest.fixture(scope="session")
def real_db_copy(tmp_path_factory) -> Path:
    """真库副本（整个测试会话共用一份，只读用途）。"""
    if not REAL_DB.exists():
        pytest.skip("真库不存在：%s" % REAL_DB)
    dst = tmp_path_factory.mktemp("realdb") / "jproperty.db"
    shutil.copy2(REAL_DB, dst)
    return dst


@pytest.fixture(scope="session")
def webapp(real_db_copy):
    """web/app.py 模块（先把 OSAKA_DB 指向副本，再导入，确保它不碰真库）。

    v1.7.5：若 app 已在别处被导入（模块级单例 STORE 会绑死在当时的库上），
    这里必须**强制重新导入**，否则端点用例查到的是空库 —— 单跑能过、全量跑全挂。
    """
    os.environ["OSAKA_DB"] = str(real_db_copy)
    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]
    import app  # noqa: E402

    return app


@pytest.fixture()
def store(tmp_path):
    """空的临时库（用 core.store 建表）。"""
    from core.store import Store

    s = Store(str(tmp_path / "t.db"))
    yield s
    s.close()


@pytest.fixture()
def fieldmap():
    """tools/verify_field_labels.py 里的词典解析工具（避免测试重复实现解析）。"""
    import verify_field_labels as v

    return v
