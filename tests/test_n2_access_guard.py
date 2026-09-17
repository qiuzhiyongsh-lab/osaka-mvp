# -*- coding: utf-8 -*-
"""N2 访问码门禁（v1.8.0）：PUBLIC 模式下 public.access_code 设值时，

- 未带口令的 API（除 /api/ping）必须 401；
- 带正确 ?code= 或 Cookie osaka_access_code 放行（200）；
- /api/ping 永远放行（健康检查）；
- 只读收口不因为 N2 失效：隐藏页 404 / 隐藏 API 403；
- N1 新鲜度：/api/query 的 library 必带 data_updated_at。

向后兼容：若 config 里没设 public.access_code，则全部 200（不拦截）。

⚠ 隔离：OSAKA_PUBLIC 只在 fixture 内设置并在 teardown 还原，
   绝不污染其他测试模块的全局环境（否则 test_api_query 会误入 PUBLIC 模式）。
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 绝不碰真库：复用 conftest 已设的副本；未设则自建一份
if not os.environ.get("OSAKA_DB"):
    import shutil
    import tempfile
    REAL_DB = ROOT / "data" / "jproperty.db"
    d = Path(tempfile.mkdtemp(prefix="osaka_n2_"))
    if REAL_DB.exists():
        shutil.copy2(REAL_DB, d / "jproperty.db")
    os.environ["OSAKA_DB"] = str(d / "jproperty.db")


def _code(pub):
    return (pub.CFG.get("public") or {}).get("access_code") or ""


@pytest.fixture(scope="module")
def pub():
    """仅在 fixture 作用域内打开 PUBLIC 模式，结束后还原，避免影响其他测试。"""
    prev = os.environ.get("OSAKA_PUBLIC")
    os.environ["OSAKA_PUBLIC"] = "1"
    # 强制重新导入，使 PUBLIC / CFG 按当前环境变量生效（避免被别处已导入的实例绑死）
    for m in [m for m in list(sys.modules) if m == "web.app"]:
        del sys.modules[m]
    import web.app as wa  # noqa: E402
    yield wa
    # teardown：还原环境变量，保证与 test_api_query 等模块顺序无关
    if prev is None:
        os.environ.pop("OSAKA_PUBLIC", None)
    else:
        os.environ["OSAKA_PUBLIC"] = prev


@pytest.fixture(scope="module")
def client(pub):
    return pub.app.test_client()


def test_public_mode_on(pub):
    assert pub.PUBLIC is True


def test_no_code_api_blocked(client, pub):
    """无码 API 必须 401（/api/ping 除外）。"""
    code = _code(pub)
    if not code:
        pytest.skip("config 未设 public.access_code，跳过 N2 拦截断言（向后兼容模式）")
    assert client.get("/api/query").status_code == 401
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/compare").status_code == 401


def test_ping_always_open(client):
    """健康检查免码（便于监控/告警）。"""
    assert client.get("/api/ping").status_code == 200


def test_code_param_allows(client, pub):
    code = _code(pub)
    if not code:
        pytest.skip("config 未设 public.access_code")
    assert client.get("/api/query?code=" + code).status_code == 200
    # 无码页面应返回访问码输入页（HTTP 200，非 401）
    assert client.get("/search").status_code == 200
    assert client.get("/search?code=" + code).status_code == 200


def test_cookie_allows(client, pub):
    code = _code(pub)
    if not code:
        pytest.skip("config 未设 public.access_code")
    assert client.get("/api/query",
                      headers={"Cookie": "osaka_access_code=" + code}).status_code == 200


def test_readonly_guard_still_active(client):
    """只读收口不因 N2 失效：隐藏页 404 / 隐藏 API 403。"""
    assert client.get("/collect").status_code == 404
    assert client.get("/api/status").status_code == 403


def test_data_updated_at_present(client, pub):
    """N1 新鲜度：带码查 /api/query，library 必带 data_updated_at。"""
    code = _code(pub)
    if not code:
        pytest.skip("config 未设 public.access_code")
    j = client.get("/api/query?code=" + code).get_json()
    assert "data_updated_at" in (j.get("library") or {}), \
        "library 缺少 data_updated_at —— N1 新鲜度字段未透出"
