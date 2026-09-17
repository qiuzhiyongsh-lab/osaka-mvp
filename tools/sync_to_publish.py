# -*- coding: utf-8 -*-
"""把「本地版」的代码与数据同步到「线上发布工程」，让线上页面 == 本地页面。

用法
----
    python tools/sync_to_publish.py            # 同步（代码 + 数据）
    python tools/sync_to_publish.py --dry      # 只看会做什么，不动文件

为什么需要它
------------
勇哥的要求：「页面与我本地的页面相同，后续我本地页面做一些调整，也需要同步线上调整。
除了 PDF 文件不直接上传外。」

以前线上是**另一套代码**（osaka-house-publish/src/...），本地改了页面，线上纹丝不动，
两边越走越远（这次连 /api/ping 都只有本地有，于是线上一直误报"本地服务已停止"）。

现在改成**同源**：
    osaka-mvp/core/  ─┐
    osaka-mvp/web/   ─┴─►  osaka-house-publish/app_local/
线上跑的就是本地那一份代码，只是用环境变量 OSAKA_PUBLIC=1 把"只有本机才有意义"的
能力（采集 / 账号 / 本地文件 / 指定下载 / 调度 / 发布 / 凭据 / 登录 / PDF 下载）收起来。

绝对不上传的东西
----------------
  · data/ 目录整体（含 credentials.json / session.json / .cred_key）
  · 任何 PDF（PUBLISHER 的列映射里本来就没有 pdf_path/pdf_url）
  · 凭据类配置键（config.yaml 里的密码 / token / 用户名 一律剔除）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

MVP = Path(r"C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp")
PUB = Path(r"C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-house-publish")
TARGET = PUB / "app_local"          # 线上跑的那一份（与本地同源）

sys.path.insert(0, str(MVP))

import yaml                                                       # noqa: E402
from core import publisher                                        # noqa: E402

# config.yaml 里这些键一律不带上云（凭据 / 会话 / 密钥）
SECRET_KEYS = {"password", "passwd", "pwd", "secret", "token", "cookie",
               "session", "session_id", "api_key", "apikey", "access_key",
               "user", "username", "userid", "user_id", "login_id", "mail",
               "email", "tel", "private_key", "cred_key"}
SECRET_KEY_PARTS = ("password", "passwd", "secret", "token", "cookie", "cred")
# ⚠ 例外：线上 /api/ingest 要拿它校验本机推来的数据，必须一起上云。
#   它是 32 位随机数（每次重装可再生成），不是 REINS 账号密码。
KEEP_KEYS = {"ingest_token"}
SKIP_DIRS = {"__pycache__", ".git", ".backups", "_e2e_tmp", "probe", "backups"}
PDF_KEYS = {"pdf_path", "pdf_url", "pdf_local", "pdf_rel", "pdf"}


def _sanitize(obj, path=""):
    """递归剔除凭据类键值。返回 (新对象, 被剔除的键列表)。"""
    dropped = []
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if (lk not in KEEP_KEYS
                    and (lk in SECRET_KEYS or any(p in lk for p in SECRET_KEY_PARTS))):
                dropped.append(f"{path}.{k}" if path else str(k))
                continue
            nv, d = _sanitize(v, f"{path}.{k}" if path else str(k))
            dropped += d
            out[k] = nv
        return out, dropped
    if isinstance(obj, list):
        out = []
        for i, v in enumerate(obj):
            nv, d = _sanitize(v, f"{path}[{i}]")
            dropped += d
            out.append(nv)
        return out, dropped
    return obj, dropped


def copy_tree(src: Path, dst: Path, exts=None, dry=False):
    n = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = Path(root).relative_to(src)
        out_dir = dst / rel if str(rel) != "." else dst
        if not dry:
            out_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            if exts and Path(f).suffix not in exts:
                continue
            if f.startswith("_") and f.endswith(".py"):
                continue                       # 本地临时脚本不上云
            s = Path(root) / f
            d = out_dir / f
            if not dry:
                shutil.copy2(s, d)
            n += 1
    return n


SERVE_PUBLIC = '''# -*- coding: utf-8 -*-
"""线上入口（由 tools/sync_to_publish.py 生成，不要手改）。

与本地跑的是同一份 core/ + web/ 代码，只是：
  · OSAKA_PUBLIC=1 → 只留查询能力（见 web/app.py 的 _public_guard）
  · 启动时把 data/site_data.json 灌进 SQLite（容器重建也不丢数据）
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OSAKA_PUBLIC", "1")

from core import config as cfgmod                                  # noqa: E402
from core.store import Store                                       # noqa: E402

CFG = cfgmod.load()
PATHS = cfgmod.paths(CFG)

_seed = ROOT.parent / "data" / "site_data.json"
_store = Store(PATHS["db"])
if _seed.exists():
    try:
        payload = json.loads(_seed.read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        have = _store.conn.execute(
            "SELECT COUNT(*) c FROM properties").fetchone()["c"]
        if have != len(rows) or have == 0:
            _store.upsert_many(rows, log=lambda s: print("[种子] " + s, flush=True))
        print("[种子] 数据包 %s（%d 条），库内现有 %d 条" % (
            payload.get("generated_at"), len(rows),
            _store.conn.execute("SELECT COUNT(*) c FROM properties").fetchone()["c"]),
            flush=True)
    except Exception as e:                                         # noqa: BLE001
        print("[种子] 跳过（%s）" % e, flush=True)
else:
    print("[种子] 没有 data/site_data.json，线上将是空库", flush=True)

from web.app import create_app                                     # noqa: E402

app = create_app()
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只报告，不动文件")
    args = ap.parse_args()
    dry = args.dry
    print(("=" * 62))
    print("本地 → 线上 同步", "（DRY RUN）" if dry else "")
    print("  源 :", MVP)
    print("  目标:", TARGET)
    print("=" * 62)

    # ── ① 数据：刷新 data/site_data.json（PDF 列本来就不在映射里）──
    # ⚠ v1.7.6 修复：这里**必须**走 core.config.load()（= _DEFAULTS ← config.yaml 模板
    #   ← config.local.yaml 本机真值 的三层深合并），不能直接 yaml.safe_load(config.yaml)。
    #   原因：v1.7.5 把真凭据拆去了 config.local.yaml，此后只读模板会拿到一串**空值** ——
    #   线上 config.yaml 的 publish.ingest_token 被写成 ''，本地每轮抓完的
    #   POST /api/ingest 会被线上按 401 拒掉，而且是**静默失效**（日志里只看到线上没更新）。
    #   敏感键的处理方式不变：下面 _sanitize() 照旧剔除（ingest_token 在 KEEP_KEYS 白名单里，
    #   这是设计需要 —— 线上要靠它校验本机推来的数据）。
    from core import config as cfgmod
    cfg = cfgmod.load()
    con = sqlite3.connect(str(MVP / "data" / "jproperty.db"))
    local_total = con.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    rows, _wm = publisher.build_rows(con, cfg, mode="full")
    con.close()
    leak = sum(1 for r in rows if PDF_KEYS & set(r.keys()))
    print(f"① 数据：本地库 {local_total} 行 → 数据包 {len(rows)} 行，PDF 字段泄漏 {leak}（应为 0）")
    if leak:
        print("   ✗ 发现 PDF 字段，已中止（请检查 core/publisher.py 的 FIELD_MAP）")
        return 2
    if not dry:
        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "osaka-mvp",
            "count": len(rows),
            "rows": rows,
        }
        dst = PUB / "data" / "site_data.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name("site_data.json.part")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, dst)
        print(f"   已写 {dst}（{dst.stat().st_size / 1048576.0:.2f} MB）")

    # ── ② 代码：core/ + web/ 整份复制 ──
    n1 = copy_tree(MVP / "core", TARGET / "core", exts={".py"}, dry=dry)
    n2 = copy_tree(MVP / "web" / "templates", TARGET / "web" / "templates", dry=dry)
    n3 = copy_tree(MVP / "web" / "static", TARGET / "web" / "static", dry=dry)
    if not dry:
        (TARGET / "web").mkdir(parents=True, exist_ok=True)
        shutil.copy2(MVP / "web" / "app.py", TARGET / "web" / "app.py")
        (TARGET / "web" / "__init__.py").write_text("", encoding="utf-8")
        (TARGET / "__init__.py").write_text("", encoding="utf-8")
    print(f"② 代码：core {n1} 个 .py / 模板 {n2} 个 / 静态 {n3} 个 / app.py 1（PDF 与 data/ 不复制）")

    # ── ③ 配置：剔除凭据后写入 ──
    safe, dropped = _sanitize(cfg or {})
    safe.setdefault("app", {})["output_root"] = "./data"
    safe.setdefault("schedule", {})["enabled"] = False
    safe.setdefault("web", {}).update({"host": "0.0.0.0", "port": 8000})
    print(f"③ 配置：剔除凭据键 {len(dropped)} 个" + (f" → {dropped[:6]}" if dropped else ""))
    if not dry:
        (TARGET / "config.yaml").write_text(
            yaml.safe_dump(safe, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # ── ④ 线上入口 ──
    if not dry:
        (TARGET / "serve_public.py").write_text(SERVE_PUBLIC, encoding="utf-8")
    print("④ 入口：app_local/serve_public.py（OSAKA_PUBLIC=1 + 启动灌种子）")

    print("=" * 62)
    print("同步完成。下一步：用「发布为应用」重新发布 osaka-house-publish。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
