# -*- coding: utf-8 -*-
"""F1 对账：本地与线上房源集合差异量化（默认 dry-run，只读优先）。

为什么需要：
    2026-09-28 数据漂移事故中，本地与线上差了约 300 条，只能靠两张截图肉眼比对才发现。
    根因是「平台缺线上房源对账通道」。本脚本 + 线上 `GET /api/reconcile` 即根治「看不见」。

用法：
  python tools/reconcile_push.py                     # 全量对账（dry-run，只报不动手）
  python tools/reconcile_push.py --day 2026-09-28    # 只对该日（登録/変更）对照
  python tools/reconcile_push.py --push              # 对账后自动重推补齐

设计约束：
  - 本地库以 `mode=ro` 打开；线上仅 `GET /api/reconcile`，**只读**。
  - 线上端点未部署（404）或令牌不对（401/403）时**明确报阻塞并退出**，**绝不伪造结果**。
  - 补齐复用已实证的 `core.publisher.publish(mode="full")`：走 /api/ingest 的 **upsert**，
    只补改行、**不删行**（2026-09-28 实测 6199 行 / 26s / 线上逐批确认）。
    ⚠ 因此「线上多余」的行不会被本脚本删除，需人工按清单处置。
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core import config as cfgmod  # noqa: E402

_KEYS = ("reg_date_iso", "chg_date_iso", "last_changed_at", "price", "is_active", "updated_at")
_SQL = ("SELECT property_no, reg_date_iso, chg_date_iso, last_changed_at, price, is_active, "
        "COALESCE(last_seen_at, first_seen_at) AS updated_at FROM properties")


def fetch_online(base: str, token: str, day: str = "", timeout: int = 60):
    """拉线上番号摘要。失败返回 (None, 原因)。"""
    url = base.rstrip("/") + "/api/reconcile"
    if day:
        url += "?day=" + day
    req = urllib.request.Request(url, headers={"X-Publish-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        why = "端点未部署/未生效" if e.code == 404 else "令牌不匹配或未授权"
        return None, "HTTP %s（%s）" % (e.code, why)
    except Exception as e:                                    # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)
    if not d.get("ok"):
        return None, "线上返回 ok=false：%s" % d.get("error")
    return d.get("rows") or [], None


def load_local(db: Path, day: str = ""):
    """本地库只读载入，返回 (dict, 重复番号列表)。"""
    con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    try:
        if day:
            rows = con.execute(_SQL + " WHERE substr(reg_date_iso,1,10)=? "
                                      "OR substr(chg_date_iso,1,10)=?", (day, day)).fetchall()
        else:
            rows = con.execute(_SQL).fetchall()
    finally:
        con.close()
    out, dup = {}, []
    for r in rows:
        no = str(r[0] or "")
        if not no:
            continue
        if no in out:
            dup.append(no)
            continue
        out[no] = dict(zip(_KEYS, tuple(r)[1:]))
    return out, dup


def index_online(rows):
    """线上行 → (dict, 重复番号列表)。"""
    out, dup = {}, []
    for r in rows:
        no = str(r.get("property_no") or "")
        if not no:
            continue
        if no in out:
            dup.append(no)
            continue
        out[no] = {k: r.get(k) for k in _KEYS}
    return out, dup


def main() -> int:
    day = ""
    if "--day" in sys.argv:
        i = sys.argv.index("--day")
        if i + 1 < len(sys.argv):
            day = sys.argv[i + 1].strip()
    want_push = "--push" in sys.argv

    cfg = cfgmod.load()
    pub = cfg.get("publish") or {}
    endpoint = str(pub.get("endpoint") or "").strip()
    token = str(pub.get("ingest_token") or "").strip()
    db = Path(str(cfgmod.paths(cfg)["db"]))

    print("→ endpoint=%s" % (endpoint or "(未配置)"))
    print("→ db=%s" % db)
    print("→ day=%s  push=%s" % (day or "(全部)", want_push))
    if not endpoint or not token:
        print("✗ 未配置 publish.endpoint 或 publish.ingest_token")
        return 2
    if not db.exists():
        print("✗ 找不到本地库：%s" % db)
        return 2

    online_rows, err = fetch_online(endpoint, token, day)
    if err:
        print("✗ 线上对账受阻：%s" % err)
        print("  说明：/api/reconcile 属**线上代码**，须随站点部署后本脚本才能出数；")
        print("        2104 发布记录已被平台删除、无法 redeploy，")
        print("        只能经 v100（记录完好）部署，或等官方恢复 2104 发布记录。")
        return 3

    online, dup_online = index_online(online_rows)
    local, dup_local = load_local(db, day)

    missing = sorted(set(local) - set(online))     # 本地有 / 线上无
    extra = sorted(set(online) - set(local))       # 线上有 / 本地无
    drift, drift_price = [], []
    for no in sorted(set(local) & set(online)):
        a, b = local[no], online[no]
        if (a.get("reg_date_iso") or "") != (b.get("reg_date_iso") or "") or \
           (a.get("chg_date_iso") or "") != (b.get("chg_date_iso") or ""):
            drift.append(no)
        elif str(a.get("price") or "") != str(b.get("price") or ""):
            drift_price.append(no)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "=== osaka-mvp 本地↔线上对账报告 ===",
        "时间：%s" % stamp,
        "端点：%s    口径：%s" % (endpoint, day or "全部"),
        "",
        "本地行数：%d（重复 %d）" % (len(local), len(dup_local)),
        "线上行数：%d（重复 %d）" % (len(online), len(dup_online)),
        "",
        "① 线上缺失（本地有/线上无，需补推）：%d" % len(missing),
        "② 线上多余（线上有/本地无，影子行）：%d" % len(extra),
        "③ 日期字段漂移（reg/chg 不一致）：%d" % len(drift),
        "④ 价格漂移：%d" % len(drift_price),
        "",
    ]

    def dump(title, nos, n=20):
        lines.append("--- %s（最多列 %d 条）---" % (title, n))
        for x in nos[:n]:
            lines.append("  " + str(x))
        if len(nos) > n:
            lines.append("  ...（其余 %d 条略）" % (len(nos) - n))
        lines.append("")

    dump("① 线上缺失", missing)
    dump("② 线上多余", extra)
    dump("③ 日期漂移", drift)
    dump("④ 价格漂移", drift_price)
    if dup_online:
        dump("线上重复番号", dup_online)

    report = ROOT / "data" / ("reconcile_%s.txt" % datetime.now().strftime("%Y%m%d"))
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print("→ 报告已落盘：%s" % report)

    gap = len(missing) + len(drift) + len(drift_price)
    if not want_push:
        print("→ dry-run 结束（未改动任何数据）。缺口 %d 条，加 --push 可自动补齐。" % gap)
        return 0
    if gap == 0:
        print("→ 无缺口，无需重推。")
        return 0

    from core.publisher import publish
    con = sqlite3.connect(str(db))
    try:
        res = publish(cfg, con, mode="full", force_ai=True,
                      log=lambda *a: print(" ".join(str(x) for x in a), flush=True))
    finally:
        con.close()
    ok = bool(res.get("ok"))
    print("→ 重推：ok=%s sent=%s（upsert，不删行；线上多余的 %d 条仍需人工处置）"
          % (ok, res.get("sent"), len(extra)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
