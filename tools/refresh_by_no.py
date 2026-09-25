# -*- coding: utf-8 -*-
"""定向番号検索刷新工具（v1.9.86）。

用途：对指定物件番号列表，用与下载相同的 REINS 登录会话做「番号検索 → 详情 → 落库」，
      刷新 price / subtype / address 等字段并前推 last_seen_at（让增量推送按水位线重推）。

适用场景：
  · 根治价格漂移的收尾——那 7 条已带 chg=今天 标签、但今天日期同步已跑完，
    不会再被重抓；用本工具按番号定点刷新最稳（不受日期限制）。
  · 1 条缺失（如 300140865451）定点补抓。
  · 2 条残缺（subtype/price/address 为 NULL）回填。

运行（勇哥本机；需 REINS 登录态 + Edge + 代理 127.0.0.1:7890；建议在 REINS 运行时段
      日本 07:00–23:00 JST 内跑）：
  cd osaka-mvp
  python tools/refresh_by_no.py 300140865451 300139730214 300138617167 ...
  或从文件读（每行一个番号）：
  python tools/refresh_by_no.py --file tools/_drift_nos.txt

注意：
  · 会打开一个**有窗口的 Edge 会话**（REINS 拦截无头浏览器，故必须用窗口模式），
    复用 data/session.json；与 8765 共用同一会话文件，可能触发 8765 侧一次会话重登
    （正常，不影响数据）。若自动登录失败，会弹窗让你手工登录。
  · 必须和 8765 同 cwd / 同 OSAKA_DB 环境变量运行，否则会指向不同的库。
  · 落库前会校验「properties 表非空」，若指向空壳库则直接中止，绝不写脏。
"""
from __future__ import annotations
import sys
from pathlib import Path

# v1.9.87 修复：以脚本所在目录推导项目根并注入 sys.path，
# 否则 `python tools/refresh_by_no.py` 运行时 sys.path[0]=tools/，找不到 core 包。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
import argparse

from core import config as cfgmod
from core import pipeline
from core.store import Store
from core.crawler import (Auth, _sync_playwright, _mask_webdriver,
                          _check_maintenance, _fetch_detail_by_no)
from core.auth import SessionExpired  # v1.9.87：捕获会话失效以转手工登录兜底


def main() -> None:
    ap = argparse.ArgumentParser(description="定向番号検索刷新（REINS 登录态）")
    ap.add_argument("nos", nargs="*", help="物件番号（可多个）")
    ap.add_argument("--file", help="从文件读番号（每行一个）")
    args = ap.parse_args()

    nos = [s.strip() for s in args.nos if s.strip()]
    if args.file:
        # v1.9.86：支持 # 注释行与 "番号,价格,No." CSV 快照格式（取第一列）。
        # 旧版只跳空行，会把 _reins_90_snapshot.txt 的注释行当成番号 → 番号検索白跑。
        with open(args.file, encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if not s or s.startswith("#"):
                    continue
                s = s.split(",")[0].strip()
                if s:
                    nos.append(s)
    nos = list(dict.fromkeys(nos))  # 去重保序
    if not nos:
        print("无番号，退出")
        return

    cfg = cfgmod.load()
    paths = cfgmod.paths(cfg)
    db_path = paths["db"]
    print("[refresh_by_no] 目标库:", db_path)

    # 护栏：避免误写空壳库
    try:
        import sqlite3
        _c = sqlite3.connect(str(db_path))
        _n = _c.execute("select count(*) from properties").fetchone()[0]
        _c.close()
        if _n == 0:
            print("✗ properties 表为空（疑似空壳库），中止。检查 OSAKA_DB / cwd 是否和 8765 一致。")
            return
        print("[refresh_by_no] 现有房源 %d 条，开始刷新 %d 个番号" % (_n, len(nos)))
    except Exception as e:
        print("✗ 无法打开目标库：%s: %s" % (type(e).__name__, e))
        return

    store = Store(db_path)
    sel = (cfg.get("selectors") or {})
    run_id = "refresh_by_no_%d" % int(time.time())
    log = lambda *a, **k: print(*a)

    auth = Auth(cfg, paths["session"])
    ok = skip = fail = 0
    with _sync_playwright() as p:
        # v1.9.87：必须 headless=False（有窗口 Edge）。REINS 会拦截无头浏览器
        # （core/auth.py 自身警告），强制无头会导致搜索页深链被拒
        # （ERR_HTTP_RESPONSE_CODE_FAILURE），表现为"自动登录成功却进不去"。
        # 这与 8765 主抓取保持同一模式。
        browser = auth.launch(p, headless=False)
        try:
            ctx, page = auth.open_authed_page(browser, log=log)
        except SessionExpired:
            # 自动登录（含会话自愈）失败时，转「手工登录」：弹一个可见 Edge 窗口，
            # 你亲手输入 REINS 账号密码，程序识别成功后会话入库，再继续抓取。
            print("⚠ 自动登录失败（可能是账号/密码问题或会话过期）。")
            print("→ 将打开一个可见的 Edge 窗口，请手工输入 REINS 账号密码登录；")
            print("  登录成功后程序会自动识别并继续，无需在窗口里做任何额外操作。")
            if not auth.manual_login(wait_seconds=300):
                print("✗ 手工登录也未成功，退出。请确认 data/ 下账号密码是否正确、REINS 账号是否可用。")
                browser.close()
                return
            ctx, page = auth.open_authed_page(browser, log=log)
        _mask_webdriver(ctx)
        for no in nos:
            before = store.get_property(no)
            bprice = before.get("price") if before else None
            bsub = before.get("property_subtype") if before else None
            try:
                rec = _fetch_detail_by_no(ctx, page, no, sel, cfg, store=store)
            except Exception as e:  # noqa: BLE001
                print("  ✗ %s 异常：%s: %s" % (no, type(e).__name__, e))
                fail += 1
                continue
            if not rec:
                print("  · %s 无详情（0件/解析失败，跳过）" % no)
                skip += 1
                continue
            rec["property_no"] = no
            try:
                pipeline.ingest([rec], store, cfg, run_id)
            except Exception as e:  # noqa: BLE001
                print("  ✗ %s 落库失败：%s: %s" % (no, type(e).__name__, e))
                fail += 1
                continue
            after = store.get_property(no)
            aprice = after.get("price") if after else None
            asub = after.get("property_subtype") if after else None
            alast = after.get("last_seen_at") if after else None
            tag = "NEW" if before is None else "UPD"
            print("  ✓ %s [%s] price %s → %s | subtype %s → %s | last_seen=%s"
                  % (no, tag, bprice, aprice, bsub, asub, alast))
            ok += 1
        browser.close()

    print("完成：成功 %d / 跳过 %d / 失败 %d（共 %d）" % (ok, skip, fail, len(nos)))
    if ok:
        print("提示：刷新后 last_seen 已前推，下一轮增量推送会按水位线把这些房源重推到线上。")


if __name__ == "__main__":
    main()
