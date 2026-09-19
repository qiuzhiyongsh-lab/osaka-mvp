# -*- coding: utf-8 -*-
"""v1.9.19 门禁③ 冒烟：验证 `/api/ingest` 不再被「强制登录」守卫拦死。

用法:  python _smoke_ingest.py <工程目录>
       <工程目录> 需含 web/ 与 core/（osaka-mvp 或 osaka-house-publish/app_local）

隔离性：把 cwd 切到临时目录 → 库/账户全落在 TMP/data，绝不碰 8765 的真实库。
线上同款条件：先把 accounts.seed.json 拷进 TMP/data，否则启动兜底会把
AUTH_ENABLED 降级为 False，就复现不出「守卫拦 401」这一现场。
"""
import os
import shutil
import sys
import tempfile

TARGET = os.path.abspath(sys.argv[1])
SRC_SEED = os.path.join(TARGET, "data", "accounts.seed.json")

TMP = tempfile.mkdtemp(prefix="ingest_smoke_")
os.makedirs(os.path.join(TMP, "data"), exist_ok=True)
if os.path.exists(SRC_SEED):
    shutil.copy2(SRC_SEED, os.path.join(TMP, "data", "accounts.seed.json"))
    print("[setup] 已拷入账户种子（线上同款条件）")
else:
    print("[setup] ⚠ 无账户种子 —— 启动会降级开放态，本测试无意义")
os.chdir(TMP)
# ⚠ 把「脚本自己所在的目录」(osaka-mvp) 从 sys.path 里摘掉，否则：
#   app_local/core/ 少了 __init__.py（被同步脚本的 `_*.py` 跳过规则吃掉）→ 只是
#   namespace package，优先级低于 osaka-mvp/core/ 的**常规**包 → `import core` 会
#   悄悄加载 osaka-mvp/core，测的就不是线上那一份了（2026-09-19 实测踩到）。
_HERE = os.path.abspath(os.path.dirname(os.path.abspath(__file__)))
sys.path = [p for p in sys.path
            if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, TARGET)
os.environ["OSAKA_PUBLIC"] = "1"          # 线上同款：只读展示站
# ⚠ 关键隔离（2026-09-19 血的教训）：core/config.py 的 ROOT 是**模块常量**
#   （ROOT = Path(__file__).parent.parent），output_root("./data") 永远解析到
#   工程自己的 data/，**chdir 完全无效** —— 上一次冒烟就是这样把 2 条 SMOKE 假数据
#   写进了勇哥的真实主库。唯一可靠的办法：用 OSAKA_DB 环境变量把库指到临时目录。
_SMOKE_DB = os.path.join(TMP, "data", "smoke.db")
os.environ["OSAKA_DB"] = _SMOKE_DB

import core.config as cfgmod                                    # noqa: E402

# 自证隔离（在 import web.app 之前就拦住，避免任何写入落到真实库）
print("[setup] core.config 来自:", cfgmod.__file__)
_resolved_db = str(cfgmod.paths(cfgmod.load())["db"])
print("[setup] 目标库:", _resolved_db)
if TMP.lower() not in _resolved_db.lower():
    print("✗ 拒绝执行：目标库不在临时目录内（会污染勇哥的真实主库）")
    sys.exit(3)

from web.app import create_app                                  # noqa: E402

app = create_app()
print("[setup] web.app 来自:", sys.modules["web.app"].__file__)
print("[setup] AUTH_ENABLED =", sys.modules["web.app"].AUTH_ENABLED)

cfg = cfgmod.load()
pub = cfg.get("publish") or {}
token = str(pub.get("token") or pub.get("ingest_token") or "").strip()
print("[setup] 令牌已配置:", bool(token), "len =", len(token))

cli = app.test_client()

print("\n--- ① 页面仍受保护（期望 302 → /login）---")
r = cli.get("/")
print("   GET /  ->", r.status_code, "| Location:", r.headers.get("Location"))

print("\n--- ② 无令牌 POST /api/ingest ---")
r = cli.post("/api/ingest", json={"rows": [{"property_no": "SMOKE_NO_TOKEN"}]})
print("   ->", r.status_code, r.get_json())

print("\n--- ③ 错令牌 POST /api/ingest ---")
r = cli.post("/api/ingest", json={"rows": [{"property_no": "SMOKE_BAD"}]},
             headers={"X-Publish-Token": "definitely-wrong"})
print("   ->", r.status_code, r.get_json())

print("\n--- ④ 正确令牌 POST /api/ingest（期望 200 + upserted>=1）---")
r = cli.post("/api/ingest",
             json={"rows": [{"property_no": "SMOKE_OK_1", "building_name": "冒烟测试楼",
                             "kind": "中古マンション", "price": 19990000}]},
             headers={"X-Publish-Token": token})
print("   ->", r.status_code, r.get_json())

print("\n--- ⑤ 正确令牌 + 伪造 PDF 列（期望 PDF 被丢弃、行仍入库）---")
r = cli.post("/api/ingest",
             json={"rows": [{"property_no": "SMOKE_OK_2", "pdf_path": "x.pdf",
                             "pdf_url": "http://evil/x.pdf"}]},
             headers={"X-Publish-Token": token})
print("   ->", r.status_code, r.get_json())

print("\n[TMP]", TMP)
