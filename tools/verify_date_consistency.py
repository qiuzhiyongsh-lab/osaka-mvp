# -*- coding: utf-8 -*-
"""平台 vs 本地 一致性验证工具（可复用）

用法：
  python tools/verify_date_consistency.py --date 2026-09-08                 # 立即比对（当前库内快照）
  python tools/verify_date_consistency.py --date 2026-09-08 --wait          # 等本轮「指定日期下载」结束后比对
  python tools/verify_date_consistency.py --date 2026-09-08 --wait --platform "g1=68,g2=194,g3=0"
  python tools/verify_date_consistency.py --date 2026-09-08 --platform "g1=68,g2=194,g3=0" --report out.md

分组 id：
  g1 = 売一戸建（新築戸建 + 中古戸建）
  g2 = 売マンション（新築マンション + 中古マンション[含 オーナーチェンジ]）
  g3 = 売マンション（新築タウン + 中古タウン）

做什么：
  1. 解析 server.log 里最近一次「指定日期下载」会话，逐轴抽取**平台报告件数**（登録/変更 分开）；
  2. 统计本地库 (reg_date_iso=日期 OR chg_date_iso=日期) 的落库件数，按组归类；
  3. 与平台数字逐项比对，输出差值 + 归因（漏采/多采/一致）。
"""
import argparse
import io
import os
import re
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "jproperty.db")
LOG = os.path.join(ROOT, "data", "logs", "server.log")

# 组定义：id → (中文标签, 本地 property_subtype 集合)
GROUPS = [
    ("g1", "売一戸建（新築戸建+中古戸建）", ["新築戸建", "中古戸建"]),
    ("g2", "売マンション（新築+中古マンション）",
     ["新築マンション", "中古マンション", "中古マンション\n／\nオーナーチェンジ"]),
    ("g3", "売マンション（新築+中古タウン）", ["新築タウン", "中古タウン"]),
]


def parse_log_session(log_path: str):
    """最近一次「指定日期下载」会话 → {"finished":bool,"banners":[{group,axis,date,total}]}"""
    if not os.path.exists(log_path):
        return None
    lines = io.open(log_path, "r", encoding="utf-8", errors="replace").readlines()
    start = None
    for i, ln in enumerate(lines):
        if "指定日期下载：开始" in ln:
            start = i
    if start is None:
        return None
    seg = lines[start:]
    finished = any("指定日期下载结束" in ln for ln in seg)
    banners, cur = [], None
    for ln in seg:
        if "========== 检索条件 ==========" in ln:
            cur = {"group": None, "axis": None, "date": None, "total": None,
                   "round": None}
            banners.append(cur)
        elif cur is not None:
            if cur["group"] is None:
                m = re.search(r"房屋类型:\s*([^［\[]+?)\s*[［\[]", ln)
                if m:
                    cur["group"] = m.group(1).strip()
            if cur["axis"] is None:
                m = re.search(r"日期轴\s*:\s*(.+)", ln)
                if m:
                    cur["axis"] = m.group(1).strip()
            if cur["date"] is None:
                m = re.search(r"日期范围:\s*(.+)", ln)
                if m:
                    cur["date"] = m.group(1).strip()
        m = re.search(r"查询 → 结果\s*(?:1～\d+件\s*／\s*)?(\d+)件", ln)
        if m and banners:
            banners[-1]["total"] = int(m.group(1))
    return {"finished": finished, "banners": banners, "start_line": start}


def db_counts(day: str):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    per_group = {}
    for gid, label, subs in GROUPS:
        ph = ",".join("?" * len(subs))
        row = con.execute(
            f"SELECT COUNT(*) a, SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) b "
            f"FROM properties WHERE property_subtype IN ({ph}) "
            f"AND (reg_date_iso=? OR chg_date_iso=?)",
            tuple(subs) + (day, day),
        ).fetchone()
        per_group[gid] = (row["a"] or 0, row["b"] or 0)
    detail = [(r["s"], r["n"]) for r in con.execute(
        "SELECT property_subtype s, COUNT(*) n FROM properties "
        "WHERE reg_date_iso=? OR chg_date_iso=? GROUP BY s ORDER BY n DESC",
        (day, day)).fetchall()]
    total = con.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    con.close()
    return per_group, detail, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--timeout", type=int, default=9000)
    ap.add_argument("--platform", default="", help="如 g1=68,g2=194,g3=0")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    print("=== 平台(手动查询) vs 本地(软件库) 一致性验证 · 基准日 %s ===" % args.date)
    sess = parse_log_session(LOG)
    if sess:
        print("日志：找到「指定日期下载」会话（%s）" %
              ("已结束" if sess["finished"] else "进行中"))
        for b in sess["banners"]:
            if b["total"] is not None:
                print("   · %s / %s / %s  →  平台报告 %d 件"
                      % (b["group"], b["axis"], b["date"], b["total"]))
    else:
        print("日志：未找到「指定日期下载」会话")

    if args.wait and sess and not sess["finished"]:
        print("\n等待本轮下载结束（每 30s 查一次，上限 %ds）…" % args.timeout)
        t0 = time.time()
        while time.time() - t0 < args.timeout:
            time.sleep(30)
            s = parse_log_session(LOG)
            if s and s["finished"]:
                sess = s
                print("→ 检测到「指定日期下载结束」，开始比对")
                break
            print("   …仍在下载（已等 %ds）" % int(time.time() - t0))
        else:
            print("⚠ 等待超时，按当前库内数据出结果（可能偏少）")

    per_group, detail, total = db_counts(args.date)
    plat = {}
    for kv in args.platform.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            plat[k.strip()] = int(v.strip())

    print("\n" + "=" * 84)
    print("【比对表】")
    print("=" * 84)
    print("  %-38s %8s %8s %8s %6s  %s" % ("種目组", "平台", "本地全量", "本地活跃", "差", "判定"))
    print("  " + "-" * 84)
    rows = []
    for gid, label, subs in GROUPS:
        local_all, local_act = per_group[gid]
        pk = plat.get(gid)
        if pk is None:
            print("  %-38s %8s %8d %8d %6s  %s" % (label, "—", local_all, local_act, "—", "未提供平台数"))
            rows.append((label, "—", local_all, local_act, "—", "未提供平台数"))
            continue
        d = local_all - pk
        verdict = "✅一致" if d == 0 else ("⚠漏采" if d < 0 else "⚠多采")
        print("  %-38s %8d %8d %8d %+6d  %s" % (label, pk, local_all, local_act, d, verdict))
        rows.append((label, pk, local_all, local_act, "%+d" % d, verdict))

    print("\n【本地库 %s 命中明细（按 subtype）】" % args.date)
    for s, n in detail:
        print("   %-46r %d" % (s, n))
    print("\n库总行数：%d" % total)

    if args.report:
        with io.open(args.report, "w", encoding="utf-8") as f:
            f.write("# 平台 vs 本地 一致性验证报告 · %s\n\n" % args.date)
            f.write("- 生成时间：%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("- 会话状态：%s\n\n" % ("已结束" if sess and sess["finished"] else "进行中/未找到"))
            f.write("## 逐轴平台报告件数\n\n| 種目组 | 日期轴 | 日期范围 | 平台报告 |\n|---|---|---|---|\n")
            for b in (sess["banners"] if sess else []):
                f.write("| %s | %s | %s | %s |\n" % (b["group"], b["axis"], b["date"], b["total"]))
            f.write("\n## 平台 vs 本地\n\n| 種目组 | 平台 | 本地全量 | 本地活跃 | 差 | 判定 |\n|---|---|---|---|---|---|\n")
            for r in rows:
                f.write("| %s | %s | %s | %s | %s | %s |\n" % r)
            f.write("\n## 本地库明细\n\n| property_subtype | 件数 |\n|---|---|\n")
            for s, n in detail:
                f.write("| %s | %d |\n" % (s, n))
        print("\n报告已写入 %s" % args.report)


if __name__ == "__main__":
    main()
