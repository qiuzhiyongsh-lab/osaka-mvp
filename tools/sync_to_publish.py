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
#   v1.9.0 修 **VULN-02（高危）**：原名单漏了 access_code（N2 访问码），
#   access_code 是「对外站的唯一门锁口令」，一旦随 config 同步上云，
#   它就被写进了线上工程的仓库 → 等于把锁和钥匙一起挂出去。
#   当时结论：**访问码永不随同步上云**；线上要开只能在那台机器上就地设置。
#
#   〔2026-09-18 **临时解除 → 同日 23:5x 已回滚**〕
#   当晚一度按勇哥指令（B 方案）把 access_code 移出名单以随同步上云；
#   勇哥随后要求恢复严格保护，现 "access_code" 已加回下面两处名单。
#   ⚠ 保护只作用于**今后的同步**：若口令此前已被部署上云，云端那一份
#   不会自动消失，需清空发布工程 config 的值并**重新部署**才生效。
SECRET_KEYS = {"password", "passwd", "pwd", "secret", "token", "cookie",
               "session", "session_id", "api_key", "apikey", "access_key",
               "access_code", "accesscode",
               "user", "username", "userid", "user_id", "login_id", "mail",
               "email", "tel", "private_key", "cred_key"}
SECRET_KEY_PARTS = ("password", "passwd", "secret", "token", "cookie", "cred",
                    "access_code", "private_key")
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
# v1.9.45：种子触发**不再只比行数** —— 行数相同但内容已变（例如某列批量回填、
#   房源改价改面积）时也必须重灌。判据改成"数据包生成时间印章" site_data.stamp。
_stamp = ROOT.parent / "data" / "site_data.stamp"
_store = Store(PATHS["db"])
if _seed.exists():
    try:
        payload = json.loads(_seed.read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        gen = str(payload.get("generated_at") or "")
        have = _store.conn.execute(
            "SELECT COUNT(*) c FROM properties").fetchone()["c"]
        prev = ""
        try:
            if _stamp.exists():
                prev = _stamp.read_text(encoding="utf-8").strip()
        except Exception:                                          # noqa: BLE001
            prev = ""
        # 三者任一成立就重灌：① 空库（新容器） ② 行数变了 ③ 数据包内容印章变了
        need = (have == 0) or (have != len(rows)) or (bool(gen) and gen != prev)
        if need:
            print("[种子] 触发重灌：库内 %d 条 / 数据包 %d 条 / 印章 %s → %s"
                  % (have, len(rows), prev or "(无)", gen or "(无)"), flush=True)
            _store.upsert_many(rows, log=lambda s: print("[种子] " + s, flush=True))
            try:
                _stamp.write_text(gen, encoding="utf-8")
            except Exception as _e:                                # noqa: BLE001
                print("[种子] 印章写入失败（下次会重灌，不影响本次）：%s" % _e,
                      flush=True)
        else:
            print("[种子] 已是最新（印章 %s），跳过重灌" % (gen or "(无)"), flush=True)
        print("[种子] 数据包 %s（%d 条），库内现有 %d 条" % (
            payload.get("generated_at"), len(rows),
            _store.conn.execute("SELECT COUNT(*) c FROM properties").fetchone()["c"]),
            flush=True)
    except Exception as e:                                         # noqa: BLE001
        print("[种子] 跳过（%s）" % e, flush=True)
else:
    print("[种子] 没有 data/site_data.json，线上将是空库", flush=True)

# v1.9.50 / PRD-05 §4.1：**种子灌完之后**必须再学一次线路 / 车站字典。
#   原因：上面 `_store = Store(...)` 发生在灌种子**之前**，那时库是空的，
#   __init__ 里的字典学习什么都学不到 → 线上「全部线路」下拉会是空的。
#   这里 force=True 重学一次，保证首次启动就有 35 条真线路 / 站名映射。
try:
    _store._ensure_rail_dict(force=True)
    _nl = _store.conn.execute("SELECT COUNT(*) FROM rail_lines").fetchone()[0]
    _ns = _store.conn.execute("SELECT COUNT(*) FROM station_line_map").fetchone()[0]
    print("[线路字典] 线路 %d 条 / 站名映射 %d 条" % (_nl, _ns), flush=True)
except Exception as _e:                                            # noqa: BLE001
    print("[线路字典] 刷新失败（不影响启动，退化为含『線』判定）：%s" % _e, flush=True)

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
    # ── PRD-19 §17（2026-09-19 勇哥决策）：账户改为「线下建立 + 同步上云」 ──
    #   线上不再是「无账户的孤岛」—— 账户种子会随本次同步一并带上（见 ③b），
    #   故原先那条「强制 accounts.auth_enabled=False」的防自锁保护予以**撤销**，
    #   线上是否开启鉴权**以本地设置为准**。
    #   锁死风险改由线上启动兜底承担（见 web/app.py：种子缺失且账户表为空 → 自动降级开放态）。
    print(f"③ 配置：剔除凭据键 {len(dropped)} 个" + (f" → {dropped[:6]}" if dropped else ""))
    if not dry:
        (TARGET / "config.yaml").write_text(
            yaml.safe_dump(safe, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # ── ③a 发版前账号回流（v1.9.56 · 2026-09-22 多人登录故障的根治点）──
    #   线上 ≥ v1.9.28 时员工可在线上自设密码，但若从未回流/漏镜像，本包就带**旧种子**
    #   （cleared=0）。线上容器每次重建（每次发版都重建）都按本包种子灌库
    #   → 员工自设的密码被打回随机码 → 报「密码错误」（这就是 09-22 反复发生的闭环）。
    #   发版前先 pull 一次：把线上自设密码救回本地，并重写+镜像种子，随后 ③b 打包即为最新。
    #   尽力而为：网络不通只警告，绝不阻塞发版。
    try:
        if str(MVP) not in sys.path:
            sys.path.insert(0, str(MVP))
        from core import account_sync as _accsync          # noqa: PLC0415
        _rr = _accsync.pull_and_adopt(cfg, log=lambda m: print("     " + m))
        _ad = list((_rr or {}).get("adopted") or [])
        print("③a 账号回流：%s" % (("已采纳线上自设密码 → " + ",".join(_ad)) if _ad else "无变化"))
    except Exception as _e:                                # noqa: BLE001
        print("③a 账号回流跳过（%s: %s）—— ⚠ 若线上有人自设过密码，本包可能带旧种子"
              % (type(_e).__name__, _e))

    # ── ③b 账户种子（PRD-19 §17 · 线下建号 → 同步上云）──
    # 例外说明：本脚本从不整体上传 data/，但账户种子是**唯一特例** —— 它只含 pbkdf2
    # 哈希、不含任何明文口令；线上要靠它拿到初始账户，否则无账可登。
    src_seed = MVP / "data" / "accounts.seed.json"
    dst_seed = TARGET / "data" / "accounts.seed.json"
    if src_seed.exists():
        try:
            n_acc = len(json.loads(src_seed.read_text(encoding="utf-8")).get("accounts") or [])
        except Exception:
            n_acc = -1
        print(f"③b 账户种子：{src_seed.name}（{n_acc} 个账户，仅哈希）")
        if not dry:
            dst_seed.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_seed, dst_seed)
    else:
        print(f"③b 账户种子：**未找到** {src_seed} —— 请先跑 `python tools/seed_accounts.py`；"
              f"否则线上拿不到初始账户，将按兜底降级为开放态")

    # ── ③c AI 结构独立库（v1.9.25）：随同步带上，否则线上「AI 结构」为空 ──
    #   线上主库落点 = published root(=TARGET) / output_root(被强制为 ./data) / jproperty.db，
    #   AI 结构库与主库同目录：web/app.py 用 Path(PATHS["db"]).parent / "ai_pdf_store.db" 定位。
    src_ai = MVP / "data" / "ai_pdf_store.db"
    if src_ai.exists():
        ai_dir = TARGET / "data"
        ai_dir.mkdir(parents=True, exist_ok=True)
        dst_ai = ai_dir / "ai_pdf_store.db"
        # ⚠ v1.9.37-hotfix（2026-09-20 实测血案）：ai_pdf_store.db 是 **WAL** 模式。
        #   8765 每次抽取只往 <db>-wal 追加，主文件 md5/mtime 可以长时间不变；
        #   原实现用 shutil.copy2 只拷主文件 → **最近几条抽取结果整批漏发**。
        #   当日实测：本地含 WAL 共 210 条，同步后线上只有 205 条（少 5 套房源的
        #   AI 解读，勇哥在线上点开就是「本房源尚未纳入 PDF AI 识别」）。
        #   修法：① 先 wal_checkpoint(TRUNCATE) 把 WAL 并回主文件；
        #        ② 再用 sqlite3 backup API 做一致性快照（即便 8765 正占着也完整）；
        #        ③ 最后复核条数，不一致就显式报警，绝不静默漏发。
        n_src = -1
        how = "backup API（一致性快照）"
        if not dry:
            try:
                _s = sqlite3.connect(str(src_ai), timeout=20)
                _s.execute("PRAGMA busy_timeout=20000")
                try:
                    if str((_s.execute("PRAGMA journal_mode").fetchone() or [""])[0]
                           ).lower() == "wal":
                        _p = _s.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                        print(f"③c₀ WAL 合并：checkpoint(TRUNCATE) → {_p}")
                except Exception as _e:                            # noqa: BLE001
                    print(f"③c₀ WAL 合并跳过（8765 占用中）：{type(_e).__name__}: {_e}")
                n_src = _s.execute("SELECT COUNT(*) FROM ai_structure").fetchone()[0]
                _d = sqlite3.connect(str(dst_ai))
                _s.backup(_d)          # 读穿 WAL → 目标库与本地逐页一致
                _d.close()
                _s.close()
            except Exception as _e:                                # noqa: BLE001
                how = f"copy2 兜底（backup 失败：{type(_e).__name__}: {_e}）"
                shutil.copy2(src_ai, dst_ai)
        else:
            shutil.copy2(src_ai, dst_ai)
        try:
            _c = sqlite3.connect(str(dst_ai))
            n_ai = _c.execute("SELECT COUNT(*) FROM ai_structure").fetchone()[0]
            _c.close()
        except Exception:
            n_ai = -1
        if not dry and n_src >= 0 and n_ai != n_src:
            print(f"③c AI 结构库：⚠⚠ **条数不一致** 本地 {n_src} → 目标 {n_ai}，"
                  f"线上会缺 {n_src - n_ai} 条，请检查！")
        elif not dry:
            print(f"③c AI 结构库：{src_ai.name} → {dst_ai}，{n_ai} 条 ✓ 与本地一致（{how}）")
        else:
            print(f"③c AI 结构库：{src_ai.name} → {dst_ai}，{n_ai} 条（--dry）")
    else:
        print("③c AI 结构库：**未找到** data/ai_pdf_store.db —— 先跑 tools/import_ai_pdf_excel.py")

    # ── ④ 线上入口 ──
    if not dry:
        (TARGET / "serve_public.py").write_text(SERVE_PUBLIC, encoding="utf-8")
    print("④ 入口：app_local/serve_public.py（OSAKA_PUBLIC=1 + 启动灌种子）")

    print("=" * 62)
    print("同步完成。下一步：用「发布为应用」重新发布 osaka-house-publish。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
