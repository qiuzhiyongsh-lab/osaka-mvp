#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""每小时热层快照：employee.db + ai_pdf_store.db（合计约 8.4MB，成本近乎为零）

设计要点：
- 用 SQLite 的 VACUUM INTO 做原子快照（不停机、不锁死、产出紧凑副本），
  严禁直接 copy 正在被写入的库文件。
- 只备"热层"（变化快、丢失代价高、体积小的两个库）；
  房源主库 jproperty.db 由每日 23:30 的 AI 定时任务负责。
- 全程不碰 8765、不 deploy、不碰线上地址、不写任何凭据。
- 本脚本不使用 AI，由 Windows 任务计划程序每小时调用。
- 可用 pythonw.exe 无窗口运行：因此**不使用 print**，全部输出写日志文件
  （pythonw 下 sys.stdout 为 None，print 会抛异常）。
"""
import os
import io
import sqlite3
import datetime
import sys

BASE = r"C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp"
OUT = os.path.join(BASE, ".backups", "hot")
LOG = os.path.join(OUT, "snapshot.log")
ASSETS = ["data/employee.db", "data/ai_pdf_store.db"]
KEEP = 48          # 每个资产保留最近 48 份 ≈ 2 天
LOG_MAX_LINES = 2000

# 关键表：用于行数自检（为 0 视为异常）
# 实测表名（2026-09-28）：customers / favorites / property_tags / tags / customer_tags
# 注意：不是 prop_tag，是 property_tags
CHECK_TABLES = {
    "employee": ["customers", "favorites", "property_tags"],
}


def log(msg):
    """写日志；pythonw 无控制台环境下也能安全记录。"""
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # 日志写不出来也不能影响主任务


def rotate_log():
    """日志超过 LOG_MAX_LINES 行时，只保留后半部分。"""
    try:
        if not os.path.exists(LOG):
            return
        with io.open(LOG, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        if len(lines) > LOG_MAX_LINES:
            with io.open(LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[-(LOG_MAX_LINES // 2):])
    except Exception:
        pass


def main():
    os.makedirs(OUT, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ok_any = False
    log("---- run start ----")

    for rel in ASSETS:
        src = os.path.join(BASE, rel).replace("\\", "/")
        if not os.path.exists(src):
            log("MISSING %s" % src)
            continue
        name = os.path.basename(rel).replace(".db", "")
        dst = os.path.join(OUT, "%s_%s.db" % (name, ts)).replace("\\", "/")
        try:
            con = sqlite3.connect(src)
            con.execute("VACUUM INTO '%s'" % dst)  # 原子、不停机
            con.close()
        except Exception as e:
            log("FAIL %s %r" % (src, e))
            continue

        size = os.path.getsize(dst)
        log("OK %s %d bytes" % (dst, size))

        # 行数自检：为 0 视为异常（不中断，仅记告警）
        if name in CHECK_TABLES:
            try:
                c = sqlite3.connect("file:%s?mode=ro" % dst.replace("\\", "/"), uri=True)
                for t in CHECK_TABLES[name]:
                    try:
                        n = c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                        log("   count %s.%s = %d%s" % (name, t, n, "  <== ALERT: 0" if n == 0 else ""))
                    except Exception:
                        log("   count %s.%s = (table not found)" % (name, t))
                c.close()
            except Exception as e:
                log("   verify skipped: %r" % (e,))
        ok_any = True

    # 保留最近 KEEP 份，其余删除
    for rel in ASSETS:
        name = os.path.basename(rel).replace(".db", "")
        try:
            files = sorted(f for f in os.listdir(OUT) if f.startswith(name + "_"))
        except FileNotFoundError:
            continue
        for old in files[:-KEEP]:
            try:
                os.remove(os.path.join(OUT, old))
                log("PRUNED %s" % old)
            except Exception as e:
                log("PRUNE FAIL %s %r" % (old, e))

    log("---- run end: %s ----" % ("OK" if ok_any else "NO SNAPSHOT"))
    rotate_log()
    return 0 if ok_any else 1


if __name__ == "__main__":
    sys.exit(main())
