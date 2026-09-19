"""v1.9.21 上传面板修复冒烟：
  ① 定时器日志进共享缓冲 publisher.PUB_LOG（#287 统一出口）
  ② /api/publish/status 同时接受 POST（#285 致命 405 根因）
隔离：OSAKA_PUBLIC=1 避免本地定时器/调度线程启动；只读状态路由，不写库。
"""
import os, sys, tempfile, json

SRC = r"C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp"
TMP = tempfile.mkdtemp(prefix="publog_smoke_")
os.makedirs(os.path.join(TMP, "data"), exist_ok=True)
os.chdir(TMP)
sys.path.insert(0, SRC)
os.environ["OSAKA_PUBLIC"] = "1"

fails = []


def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---- ① 共享缓冲 ----
import core.publisher as pub
pub.PUB_LOG.clear()
loop = pub.PublishLoop(None, {}, log_fn=lambda m: None)
loop._log("smoke-timer-line-A")
loop._log("smoke-timer-line-B")
check("①a PUB_LOG 收到定时器日志", "smoke-timer-line-A" in pub.PUB_LOG and "smoke-timer-line-B" in pub.PUB_LOG)
# trim 上限
pub.PUB_LOG[:] = ["x"] * 500
pub.pub_log("extra")
check("①b pub_log 自动裁剪 ≤400", len(pub.PUB_LOG) <= 400)
pub.PUB_LOG.clear()

# ---- ② 路由 POST 接受（#285 致命 405 根因）----
from web.app import create_app, api_publish_status
app = create_app()
# ②a 路由装饰器现在同时接受 GET + POST（旧版只 @app.get → 前端 POST 必 405）
methods = set()
for rule in app.url_map.iter_rules():
    if rule.endpoint == "api_publish_status":
        methods |= rule.methods
check("②a 路由接受 POST（修复 405）", "POST" in methods and "GET" in methods)
# ②b/c 绕过守卫直接调视图函数，验证 body 合并（log 字段=手动+定时；loop 字段=定时器状态）
pub.PUB_LOG.clear()
pub.PUB_LOG.append("smoke-timer-x")
with app.test_request_context("/api/publish/status", method="POST", json={}):
    j = api_publish_status().get_json()
check("②b 响应含 log 字段(数组)", isinstance(j.get("log"), list))
check("②c 响应含 loop 字段(dict)", isinstance(j.get("loop"), dict))
check("②d 合并日志含定时缓冲(smoke-timer-x)", "smoke-timer-x" in (j.get("log") or []))
pub.PUB_LOG.clear()

print("\nSMOKE_" + ("PASS" if not fails else "FAIL") + ("" if not fails else " → " + ";".join(fails)))
sys.exit(1 if fails else 0)
