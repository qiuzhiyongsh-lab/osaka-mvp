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

v1.9.87 已修的三个坑（都是"跑到一半才炸"型，务必别再回退）：
  ① `No module named 'core'`：脚本曾漏 sys.path 注入（sys.path[0]=tools/）。
  ② `SessionExpired: ERR_HTTP_RESPONSE_CODE_FAILURE`：曾用 headless=True 被 REINS 拦，
     必须 headless=False；且会话失效时转手工登录兜底。
  ③ `AttributeError: 'sqlite3.Row' object has no attribute 'get'`：Store.get_property
     返回 sqlite3.Row，没有 .get()，必须用 _g() 取值。旧代码在第 1 个番号就崩，
     表现为"刚打印『开始刷新 N 个番号』就 Traceback"，一条都抓不到。

长跑参数（735 个番号 ≈ 数十分钟起，强烈建议带上）：
  --no-pdf   只补详情字段+价格，不点下载 PDF（明显提速；PDF 由日常主轮次自然补）
  --resume   跳过 tools/_refresh_done_nos.txt 里已成功的番号，中断后只补漏的
  --every N  每 N 个打一行进度心跳（含 ETA），默认 25
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


def _g(row, key):
    """v1.9.87 修复：Store.get_property 返回 sqlite3.Row，**没有 .get()** 方法。

    旧写法 `row.get("price")` 会在第 1 个番号就抛
    `AttributeError: 'sqlite3.Row' object has no attribute 'get'`，
    表现为"刚打印完『开始刷新 N 个番号』就崩"，一条都抓不到。
    sqlite3.Row 支持 [] 下标 + keys()，故用 keys() 做安全取值。
    """
    if row is None:
        return None
    try:
        if key in row.keys():
            return row[key]
    except Exception:  # noqa: BLE001
        pass
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="定向番号検索刷新（REINS 登录态）")
    ap.add_argument("nos", nargs="*", help="物件番号（可多个）")
    ap.add_argument("--file", help="从文件读番号（每行一个）")
    # v1.9.87 增：长跑韧性参数
    ap.add_argument("--resume", action="store_true",
                    help="跳过 tools/_refresh_done_nos.txt 里已成功的番号，续跑不再重抓")
    ap.add_argument("--no-pdf", action="store_true",
                    help="只补详情字段、不下载 PDF（明显提速；PDF 可日后由主轮次补）")
    ap.add_argument("--every", type=int, default=25,
                    help="每 N 个打印一次进度心跳（默认 25）")
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

    # v1.9.87：断点续跑——成功过的番号记在 done 文件里，--resume 时跳过。
    # 735 个番号要跑很久（每个 ≈10s 起），中途被 Ctrl+C / 网络断 / 会话失效打断时，
    # 没有这个机制就得从第 1 个重跑，白白多花几十分钟。
    done_path = Path(__file__).resolve().parent / "_refresh_done_nos.txt"
    if args.resume and done_path.exists():
        done = set()
        for ln in done_path.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                done.add(s.split(",")[0].strip())
        n_all = len(nos)
        nos = [n for n in nos if n not in done]
        print("[refresh_by_no] --resume：已记录成功 %d 个，跳过 %d 个，剩余 %d 个"
              % (len(done), n_all - len(nos), len(nos)))
        if not nos:
            print("✓ 清单内番号全部已刷新过，无需重跑。")
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
    # changes.run_id 列是 INTEGER，与其他抓取调用保持一致（避免混类型写 TEXT）。
    run_id = int(time.time())
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
        need_pdf = not args.no_pdf
        t0 = time.time()
        hard_fail = False
        done_f = open(done_path, "a", encoding="utf-8")
        n_total = len(nos)
        for idx, no in enumerate(nos, 1):
            before = store.get_property(no)
            bprice = _g(before, "price")
            bsub = _g(before, "property_subtype")

            rec = None
            err = None
            for attempt in (1, 2):
                try:
                    rec = _fetch_detail_by_no(ctx, page, no, sel, cfg,
                                              need_pdf=need_pdf, store=store, log=log)
                    break
                except SessionExpired:
                    # v1.9.87：735 个番号是长跑，中途 REINS 会话失效几乎必然发生。
                    # 旧实现一旦 SessionExpired 就整体终止 → 前面抓的白抓（除 done 文件外）。
                    # 这里自动重登并重试本番号；重登也失败才转手工登录。
                    if attempt == 2:
                        err = "会话失效（重登后仍失败）"
                        break
                    print("  ⚠ %s 会话失效 → 自动重登后重试" % no)
                    try:
                        ctx, page = auth.open_authed_page(browser, log=log)
                    except SessionExpired:
                        if not auth.manual_login(wait_seconds=300):
                            err = "会话失效且手工登录未成功"
                            hard_fail = True
                            break
                        try:
                            ctx, page = auth.open_authed_page(browser, log=log)
                        except SessionExpired:
                            err = "会话失效且手工登录后仍不可用"
                            hard_fail = True
                            break
                    _mask_webdriver(ctx)
                except Exception as e:  # noqa: BLE001
                    err = "%s: %s" % (type(e).__name__, e)
                    break

            if hard_fail:
                print("✗ 会话无法恢复，已中止本次刷新（本段成功 %d / 跳过 %d / 失败 %d）。"
                      % (ok, skip, fail))
                print("  已完成部分已写入 %s，可用 --resume 续跑，不必从头再来。" % done_path.name)
                break
            if err:
                print("  ✗ %s 异常：%s" % (no, err))
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
            aprice = _g(after, "price")
            asub = _g(after, "property_subtype")
            alast = _g(after, "last_seen_at")
            tag = "NEW" if before is None else "UPD"
            print("  ✓ %s [%s] price %s → %s | subtype %s → %s | last_seen=%s"
                  % (no, tag, bprice, aprice, bsub, asub, alast))
            ok += 1
            try:
                done_f.write("%s,%s\n" % (no, aprice or ""))
                done_f.flush()
            except Exception:  # noqa: BLE001
                pass

            if args.every and idx % args.every == 0:
                el = time.time() - t0
                rate = el / idx
                eta = rate * (n_total - idx)
                print("—— 进度 %d/%d | 成功 %d 跳过 %d 失败 %d | 已用 %.0f分 | 预计剩余 %.0f分 "
                      "(%.1f 秒/个) ——" % (idx, n_total, ok, skip, fail, el / 60, eta / 60, rate))
        try:
            done_f.close()
        except Exception:  # noqa: BLE001
            pass
        browser.close()

    print("完成：成功 %d / 跳过 %d / 失败 %d（共 %d）" % (ok, skip, fail, len(nos)))
    if skip or fail:
        print("⚠ 有跳过/失败：可加 --resume 重跑同一条命令（已成功的番号自动跳过，只补漏的）；")
        print("  断点记录文件：%s" % done_path)
    if ok:
        print("提示：刷新后 last_seen 已前推，下一轮增量推送会按水位线把这些房源重推到线上。")


if __name__ == "__main__":
    main()
