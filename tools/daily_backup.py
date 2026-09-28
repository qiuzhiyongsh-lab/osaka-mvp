#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
osaka-mvp 每日备份（原子快照 + 完整性校验 + 压缩 + PDF 增量）
用法: python tools/daily_backup.py
红线: 只读活动库，绝不重启 8765、绝不 deploy、绝不删活动库。
"""
import gzip
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BAK = os.path.join(ROOT, ".backups", "db")
PDFINC = os.path.join(ROOT, ".backups", "pdf_inc")

ASSETS = [
    # (资产名, 相对路径, 关键表list)
    ("jproperty", "jproperty.db", ["properties"]),
    ("ai_pdf_store", "ai_pdf_store.db", ["ai_field_meta", "ai_structure", "ai_edit_log"]),
    ("employee", "employee.db", ["customers", "favorites", "property_tags"]),
]

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
DAY = datetime.now().strftime("%Y%m%d")


def git_short():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "nogit"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def live_count(rel, table):
    """只读方式统计活动库行数；失败返回 -1"""
    try:
        c = sqlite3.connect("file:" + os.path.join(DATA, rel).replace("\\", "/") + "?mode=ro", uri=True)
        n = c.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        c.close()
        return n
    except Exception as e:
        print("   ! live_count %s.%s 失败: %s" % (rel, table, e))
        return -1


def main():
    sha = git_short()
    os.makedirs(BAK, exist_ok=True)
    report_rows = []
    ok_all = True

    for name, rel, tables in ASSETS:
        t0 = time.time()
        src = os.path.join(DATA, rel)
        if not os.path.exists(src):
            print("[跳过] %s 不存在" % src)
            continue
        raw_size = os.path.getsize(src)
        out = os.path.join(BAK, "%s_%s_%s.db" % (name, STAMP, sha))
        print("=" * 70)
        print("[1] VACUUM INTO %s -> %s" % (rel, os.path.basename(out)))

        # ---------- 第一步：原子快照 ----------
        c = sqlite3.connect(src, timeout=60)
        try:
            c.execute("VACUUM INTO ?", (out,))
        finally:
            c.close()
        snap_size = os.path.getsize(out)

        # ---------- 第二步：完整性 ----------
        ic = "FAIL"
        try:
            v = sqlite3.connect("file:" + out.replace("\\", "/") + "?mode=ro", uri=True)
            ic = v.execute("PRAGMA integrity_check").fetchone()[0]
            v.close()
        except Exception as e:
            ic = "ERR:%s" % e

        rows_info = []
        rows_ok = True
        for tb in tables:
            try:
                v = sqlite3.connect("file:" + out.replace("\\", "/") + "?mode=ro", uri=True)
                snap_n = v.execute("SELECT COUNT(*) FROM %s" % tb).fetchone()[0]
                v.close()
            except Exception as e:
                snap_n = -1
                print("   ! 快照计数失败 %s.%s: %s" % (name, tb, e))
            live_n = live_count(rel, tb)
            verdict = "ok"
            if snap_n <= 0:
                verdict = "FAIL(0行)"
                rows_ok = False
            elif live_n > 0 and snap_n < live_n * 0.95:
                verdict = "FAIL(少于活动库5%以上)"
                rows_ok = False
            rows_info.append("%s=%d(活动%d)%s" % (tb, snap_n, live_n, "" if verdict == "ok" else " " + verdict))
        if ic != "ok":
            rows_ok = False
        ok_all = ok_all and rows_ok

        sh = sha256_file(out)

        # ---------- 第三步：压缩 ----------
        gz = out + ".gz"
        tg = time.time()
        with open(out, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
        gz_size = os.path.getsize(gz)
        gz_cost = time.time() - tg

        report_rows.append(dict(
            asset=name, raw=raw_size, snap=snap_size, gz=gz_size,
            rows="; ".join(rows_info), ic=ic, sha=sh, ok=rows_ok,
            cost=time.time() - t0, gz_cost=gz_cost, path=out, gzpath=gz,
        ))
        print("   size=%d -> gz=%d (%.1f%%) integrity=%s rows=%s" % (
            snap_size, gz_size, 100.0 * gz_size / max(snap_size, 1), ic, "; ".join(rows_info)))

    # ---------- 第二步(1): sha256 清单 ----------
    shalist = os.path.join(BAK, "%s.sha256.txt" % datetime.now().strftime("%Y-%m-%d"))
    with open(shalist, "w", encoding="utf-8") as f:
        f.write("# osaka-mvp 每日备份 sha256 清单  %s\n" % datetime.now().isoformat(timespec="seconds"))
        f.write("# git HEAD: %s\n" % sha)
        for r in report_rows:
            f.write("%s  %s\n" % (r["sha"], os.path.basename(r["path"])))
    print("\n[2] sha256 清单 -> %s" % shalist)

    # ---------- 第四步：PDF 增量 ----------
    attach = os.path.join(DATA, "attachments")
    pdfdir = os.path.join(PDFINC, DAY)
    pdf_n = 0
    pdf_bytes = 0
    if os.path.isdir(attach):
        cutoff = time.time() - 24 * 3600
        os.makedirs(pdfdir, exist_ok=True)
        for fn in os.listdir(attach):
            if not fn.lower().endswith(".pdf"):
                continue
            p = os.path.join(attach, fn)
            try:
                if os.path.getmtime(p) < cutoff:
                    continue
            except OSError:
                continue
            shutil.copy2(p, os.path.join(pdfdir, fn))
            pdf_n += 1
            pdf_bytes += os.path.getsize(p)
    print("[4] PDF 增量 -> %s : %d 个 / %.1f MB" % (pdfdir, pdf_n, pdf_bytes / 1048576.0))

    # ---------- 输出结果 JSON 供上层读取 ----------
    import json
    meta = dict(stamp=STAMP, day=DAY, sha=sha, rows=report_rows,
                pdf_inc=dict(dir=pdfdir, count=pdf_n, bytes=pdf_bytes),
                shalist=shalist, ok_all=ok_all)
    with open(os.path.join(BAK, "_last_backup_%s.json" % DAY), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("\n[META] %s" % os.path.join(BAK, "_last_backup_%s.json" % DAY))
    print("OK_ALL =", ok_all)


if __name__ == "__main__":
    main()
