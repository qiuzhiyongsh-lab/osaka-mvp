# -*- coding: utf-8 -*-
"""项目总监 · 体检脚本（v1.5.19）

干什么
------
总监这个角色不写代码、不画界面，他的活是**回头检查那四个角色有没有真把关**。
这份脚本就是他的尺子 —— 不靠人汇报"做完了"，靠机器把事实跑出来。

跑法
----
    python tools/director_audit.py            # 出报告
    python tools/director_audit.py --json     # 机器可读（给自动化用）

退出码
------
    0 = 全绿    1 = 有黄    2 = 有红（必须处理）

检查项（对应四角色）
--------------------
  PM  → 待办有没有验收标准、PRD 与代码是否脱节
  RD  → 版本漂移、同步漂移（本地改了没同步线上）、调度窗口、线上收数令牌
  UI  → 对外模式是否真的收起了本机功能
  QA  → PDF 泄漏、凭据残留、测试是否存在、数据包新鲜度
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT.parent / "osaka-house-publish"

GREEN, YELLOW, RED = "绿", "黄", "红"
RESULTS: list[dict] = []


def rec(role: str, item: str, level: str, detail: str):
    RESULTS.append({"role": role, "item": item, "level": level, "detail": detail})


def md5(p: Path) -> str:
    try:
        return hashlib.md5(p.read_bytes()).hexdigest()
    except Exception:                                             # noqa: BLE001
        return ""


def hours_since(ts: str) -> float | None:
    try:
        return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    except Exception:                                             # noqa: BLE001
        return None


# ─────────────────────────── RD：版本与同步漂移 ───────────────────────────
def check_version():
    v = ROOT / "core" / "version.py"
    m = re.search(r'^(VERSION\s*=\s*)"([^"]+)"', v.read_text(encoding="utf-8"), re.M)
    ver = m.group(2) if m else "?"
    rec("RD", "本地版本号", GREEN if ver else RED, f"core/version.py = {ver}")

    # 同步漂移：本地改了代码、却没跑 sync_to_publish.py → 线上还是旧的
    pairs = [("web/app.py", "web/app.py"),
             ("web/templates/base.html", "web/templates/base.html"),
             ("web/templates/search.html", "web/templates/search.html")]
    for a, b in pairs:
        src, dst = ROOT / a, PUB / "app_local" / b
        if not dst.exists():
            rec("RD", f"同步漂移 · {a}", RED, "线上包里没有这个文件（没跑过同步？）")
            continue
        same = md5(src) == md5(dst)
        rec("RD", f"同步漂移 · {a}", GREEN if same else YELLOW,
            "与本地一致" if same else "**本地已改、线上还是旧的** → 跑 tools/sync_to_publish.py")


# ─────────────────────────── RD：配置健全性 ───────────────────────────
# v1.9.22-tools（2026-09-19）修「长期假黄」：
#   config.yaml 是**发布安全版**（core/config._sanitize 已把 token / ingest_token /
#   access_code 等私密键剔除后才入库），而真值在 config.local.yaml（gitignored）。
#   旧实现直接读 config.yaml → 「线上地址（空）」「线上收数令牌缺失」两项**必然误判为黄**
#   （长期假黄，连日误导晨会日报的决策）。现改为「主配置 + config.local.yaml」深度合并。
def _deep_merge(base: dict, over: dict) -> dict:
    """用 over 覆盖 base（仅覆盖非空值，避免本地空串清掉主配置的有值项）。"""
    out = dict(base or {})
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v not in (None, "", [], {}):
            out[k] = v
    return out


def load_cfg() -> dict:
    """读主 config.yaml 并叠加 config.local.yaml 的真值（缺失/损坏时静默退化为主配置）。"""
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    lp = ROOT / "config.local.yaml"
    if lp.exists():
        try:
            cfg = _deep_merge(cfg, yaml.safe_load(lp.read_text(encoding="utf-8")) or {})
        except Exception:                                         # noqa: BLE001
            pass
    return cfg


def check_config():
    try:
        cfg = load_cfg()
    except Exception as e:                                        # noqa: BLE001
        rec("RD", "配置可解析", RED, str(e))
        return

    s = cfg.get("schedule") or {}
    w = s.get("window") or {}
    start, end = str(w.get("start", "")), str(w.get("end", ""))

    def _hm(v: str):
        try:
            h, m = str(v).split(":")
            return int(h) * 60 + int(m)
        except Exception:                                         # noqa: BLE001
            return None

    # v1.9.85：此前判定用「起点以 2 开头」，会把合法的 20:00 起也误判，
    #   且文案写的是「REINS 22:00–07:00 维护」这一**已作废的旧前提**。
    #   正确口径：REINS 维护 = 日本时间 23:00–次日 07:00（见 core/scheduler.py 头注释）。
    st, en = _hm(start), _hm(end)
    bad = st is None or st >= 23 * 60 or st < 7 * 60 or (en is not None and en <= st)
    rec("RD", "调度窗口 vs REINS 维护期",
        RED if bad else GREEN,
        f"{start}–{end}（REINS 日本时间 23:00–07:00 维护）" + (" ← 撞维护期！" if bad else ""))

    rec("RD", "自动更新开关", YELLOW if not s.get("enabled") else GREEN,
        "enabled=false → 不会自动抓（勇哥尚未开启）" if not s.get("enabled") else "已开启")

    p = cfg.get("publish") or {}
    ep = str(p.get("endpoint") or "")
    rec("RD", "线上地址", GREEN if ep.startswith("http") else YELLOW, ep or "（空）")
    tok = str(p.get("ingest_token") or "")
    rec("RD", "线上收数令牌", GREEN if len(tok) >= 16 else YELLOW,
        f"{tok[:6]}…（{len(tok)} 位）" if tok else "缺失 → /api/ingest 会 401")


# ─────────────────────────── UI/安全：对外模式 ───────────────────────────
def check_public_mode():
    f = ROOT / "web" / "app.py"
    t = f.read_text(encoding="utf-8")
    has_guard = "def _public_guard" in t
    has_ingest = "/api/ingest" in t
    rec("UI", "对外模式守卫", GREEN if has_guard else RED,
        "_public_guard 存在" if has_guard else "缺失 → 线上会暴露本机功能")
    rec("RD", "线上收数入口", GREEN if has_ingest else YELLOW,
        "/api/ingest 已实现" if has_ingest else "缺失 → 数据没法自动推")
    hidden = t.count("PUBLIC_HIDDEN_PAGES")
    rec("UI", "隐藏页清单", GREEN if hidden else YELLOW, f"命中 {hidden} 处")


# ─────────────────────────── QA：PDF 泄漏 / 凭据残留 / 数据新鲜度 ───────────────────────────
def check_qa():
    seed = PUB / "data" / "site_data.json"
    if seed.exists():
        raw = seed.read_text(encoding="utf-8")
        # v1.9.62 **新口径**（勇哥 2026-09-23 拍板「PDF 上云 + 登录即可看」后修订）：
        #   · `pdf_path` = 本机路径 → **永远必须为 0**，出现即红（旧红线在此保留并收紧）。
        #   · `pdf_url`  = COS 预签名直链 → **允许存在**，但值必须是 http(s) 外链；
        #                  若塞了本机路径/相对路径，同样判红（防"假直链真路径"）。
        #   旧判定 `pdf_(path|url)` 一律计数 → 会把合法的 COS 直链误报为泄漏（9-23 实际发生）。
        leak_path = len(re.findall(r'"pdf_path"\s*:\s*(?!null)', raw))
        urls = re.findall(r'"pdf_url"\s*:\s*"([^"]*)"', raw)
        urls = [u for u in urls if u]
        bad_url = [u for u in urls if not u.startswith("http")]
        if leak_path or bad_url:
            rec("QA", "PDF 泄漏检查", RED,
                f"pdf_path 非空 {leak_path} 处 / 非外链 pdf_url {len(bad_url)} 处"
                + (f"（样例 {bad_url[0][:40]}）" if bad_url else ""))
        elif urls:
            rec("QA", "PDF 泄漏检查", GREEN,
                f"pdf_path 0 处；pdf_url {len(urls)} 处均为 COS 外链（符合新口径）")
        else:
            rec("QA", "PDF 泄漏检查", GREEN, "0 处（符合要求）")
        try:
            gen = json.loads(raw).get("generated_at", "")
            h = hours_since(gen)
            lvl = GREEN if (h is not None and h < 24) else YELLOW
            rec("QA", "数据包新鲜度", lvl,
                f"生成于 {gen}（{h:.1f} 小时前）" if h is not None else f"生成于 {gen}")
        except Exception:                                         # noqa: BLE001
            rec("QA", "数据包新鲜度", YELLOW, "读不出 generated_at")
    else:
        rec("QA", "数据包存在", RED, "找不到 data/site_data.json")

    bad = []
    d = PUB / "data"
    if d.is_dir():
        for n in os.listdir(d):
            if any(k in n.lower() for k in ("cred", "session", "token", "secret")):
                bad.append(n)
    rec("QA", "发布包凭据残留", RED if bad else GREEN,
        f"发现 {bad} → 立刻删" if bad else "0 个（干净）")

    tests = ROOT / "tests"
    n_test = len(list(tests.rglob("test_*.py"))) if tests.is_dir() else 0
    rec("QA", "自动化测试", GREEN if n_test >= 10 else (YELLOW if n_test else RED),
        f"tests/ 下 {n_test} 个用例" + ("" if n_test >= 10 else "（02 号文档 QA-1 要求先建 10+ 用例）"))


# ─────────────────────────── PM：PRD 与代码是否脱节 ───────────────────────────
def check_pm():
    prd = ROOT / "docs" / "PRD" / "12_客户需求评审_PRD需求稿_梓榮_JProperty.md"
    if not prd.exists():
        rec("PM", "PRD 存在", RED, "找不到 12 号需求稿")
        return
    t = prd.read_text(encoding="utf-8")
    wait = len(re.findall(r"⏳", t))
    done = len(re.findall(r"✅", t))
    rec("PM", "需求状态", YELLOW if wait else GREEN, f"待审/待做 {wait} 处 ｜ 已完成 {done} 处")
    no_ac = len([l for l in t.splitlines() if "验收" in l])
    rec("PM", "验收标准覆盖", GREEN if no_ac >= 5 else YELLOW,
        f"PRD 中出现「验收」{no_ac} 次（每条需求都该有）")


# ─────────────────────────── OPS：运维经理（归总监直管） ───────────────────────────
def _dir_mtime_hours(d: Path):
    if not d.is_dir():
        return None
    try:
        latest = max((p.stat().st_mtime for p in d.rglob("*") if p.is_file()), default=0)
    except Exception:                                             # noqa: BLE001
        return None
    return (datetime.now().timestamp() - latest) / 3600 if latest else None


def check_ops():
    """运维经理体检：备份 / 磁盘 / 日志 / 线上健康 / 推送水位 / 运行记录。

    对应 SRE 的「四黄金信号 + 备份 + 事故信号」，但按本项目实际（单机 + 一个线上只读站）裁剪，
    不搞企业级 cosplay —— 只查真会出事的。
    """
    # ① 备份新鲜度（应每日备份；>24h 黄，>72h 红）
    for name in (".backups", "backups"):
        h = _dir_mtime_hours(ROOT / name)
        if h is not None:
            lvl = GREEN if h < 24 else (YELLOW if h < 72 else RED)
            rec("OPS", "备份新鲜度", lvl, f"{name}/ 最新备份于 {h:.1f} 小时前")
            break
    else:
        rec("OPS", "备份新鲜度", RED, "找不到 .backups/ 或 backups/ —— 还没做过备份")

    # ② 磁盘余量（数据盘不能满，满了写不进库也存不了 PDF）
    try:
        usage = shutil.disk_usage(str(ROOT))
        free_gb = usage.free / 1024 ** 3
        rec("OPS", "磁盘余量", GREEN if free_gb > 10 else (YELLOW if free_gb > 3 else RED),
            f"剩余 {free_gb:.1f} GB / 共 {usage.total / 1024 ** 3:.0f} GB")
    except Exception as e:                                        # noqa: BLE001
        rec("OPS", "磁盘余量", YELLOW, f"读不到：{e}")

    # ③ 日志体积（server.log 超过 2MB 代码会自动轮转，这里只做兜底观察）
    logf = ROOT / "data" / "logs" / "server.log"
    if logf.exists():
        mb = logf.stat().st_size / 1048576
        rec("OPS", "日志体积", GREEN if mb < 2 else YELLOW, f"server.log {mb:.2f} MB")
    else:
        rec("OPS", "日志体积", YELLOW, "尚无 data/logs/server.log")

    # ④ 数据库体积与行数
    db = ROOT / "data" / "jproperty.db"
    if db.exists():
        mb = db.stat().st_size / 1048576
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            n = con.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
            act = con.execute("SELECT COUNT(*) FROM properties WHERE is_active=1").fetchone()[0]
            con.close()
            rec("OPS", "数据库", GREEN, f"{mb:.2f} MB ／ {n} 行（在架 {act}）")
        except Exception as e:                                    # noqa: BLE001
            rec("OPS", "数据库", YELLOW, f"{mb:.2f} MB，读不到行数：{e}")
    else:
        rec("OPS", "数据库", RED, "找不到 data/jproperty.db")

    # ⑤ 推送水位线（数据有没有真的推到线上）
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        row = con.execute("SELECT last_at,last_count,last_error FROM publish_state WHERE id=1").fetchone()
        con.close()
        if row and row[0]:
            h = hours_since(row[0])
            err = row[2]
            lvl = RED if err else (GREEN if (h is not None and h < 24) else YELLOW)
            rec("OPS", "线上推送水位", lvl,
                f"上次成功 {row[0]}（{h:.1f} 小时前，{row[1]} 行）" +
                (f"　⚠ 最近一次错误：{str(err)[:80]}" if err else ""))
        else:
            rec("OPS", "线上推送水位", YELLOW, "还没有成功推送过（publish_state 空）")
    except Exception as e:                                        # noqa: BLE001
        rec("OPS", "线上推送水位", YELLOW, f"读 publish_state 失败：{e}")

    # ⑥ 最近一次抓取运行（卡住/异常要看得见）
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        r = con.execute("SELECT id,status,finished_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
        if r:
            rec("OPS", "最近一轮抓取", GREEN if r[1] != "running" else YELLOW,
                f"run#{r[0]} 状态={r[1]} 结束于 {r[2]}")
        else:
            rec("OPS", "最近一轮抓取", YELLOW, "runs 表为空（还没跑过完整一轮）")
    except Exception as e:                                        # noqa: BLE001
        rec("OPS", "最近一轮抓取", YELLOW, f"读 runs 失败：{e}")

    # ⑦ 线上健康（四黄金信号里最要紧的两个：可用性 + 错误）
    try:
        import urllib.request
        with urllib.request.urlopen("https://osaka-house-v2.app.workbuddy.host/api/ping",
                                    timeout=20) as resp:
            ok = resp.status == 200
        rec("OPS", "线上可用性", GREEN if ok else RED,
            "/api/ping 200" if ok else "/api/ping 非 200")
    except Exception as e:                                        # noqa: BLE001
        rec("OPS", "线上可用性", RED, f"线上打不通：{type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    check_version()
    check_config()
    check_public_mode()
    check_qa()
    check_pm()
    check_ops()

    worst = 0
    for r in RESULTS:
        if r["level"] == RED:
            worst = 2
        elif r["level"] == YELLOW and worst < 1:
            worst = 1

    if args.json:
        print(json.dumps({"checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          "worst": ["绿", "黄", "红"][worst], "items": RESULTS},
                         ensure_ascii=False, indent=1))
        return worst

    print("=" * 70)
    print(f"项目总监体检 · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    for role in ("PM", "RD", "UI", "QA", "OPS"):
        rows = [r for r in RESULTS if r["role"] == role]
        if not rows:
            continue
        print(f"\n【{role}】")
        for r in rows:
            print(f"  [{r['level']}] {r['item']}：{r['detail']}")
    reds = [r for r in RESULTS if r["level"] == RED]
    yellows = [r for r in RESULTS if r["level"] == YELLOW]
    print("\n" + "=" * 70)
    print(f"结论：红 {len(reds)} 项 ／ 黄 {len(yellows)} 项 ／ 绿 "
          f"{len(RESULTS) - len(reds) - len(yellows)} 项")
    if reds:
        print("🔴 必须处理：" + "、".join(r["item"] for r in reds))
    if yellows:
        print("🟠 建议处理：" + "、".join(r["item"] for r in yellows))
    print("=" * 70)
    return worst


if __name__ == "__main__":
    sys.exit(main())
