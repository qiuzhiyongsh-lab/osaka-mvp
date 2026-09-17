# -*- coding: utf-8 -*-
"""回归：会话过期必须被识别成「未登录」（不能被 URL 判据误判成已登录）。

现象：会话超时后 REINS 返回一个只有提示文字的短页（地址仍是 main/...），
旧逻辑只看地址 → 误判已登录 → 抓取静默入库 0 条。
本脚本用当前保存的会话实机验证修复后的判定。
"""
import os
import sys
from pathlib import Path

ROOT = Path(r"C:/Users/25374/WorkBuddy/2026-09-11-09-50-22/osaka-mvp")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core import config as cfgmod                     # noqa: E402
from core.auth import Auth, SessionExpired, _sync_playwright  # noqa: E402

FAIL = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


cfg = cfgmod.load()
paths = cfgmod.paths(cfg)
auth = Auth(cfg, paths["session"])

print("== 1) 打开检索引擎的検索条件入力页（使用现有会话）==")
with _sync_playwright() as p:
    b = auth.launch(p, headless=False)
    ctx = auth.new_context(b)
    page = ctx.new_page()
    page.set_default_timeout(30000)
    page.goto(cfg["site"]["search_url"], wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)
    print(f"  url = {page.url}")

    reason = auth.expired_reason(page)
    logged = auth.looks_logged_in(page)
    print(f"  expired_reason  = {reason!r}")
    print(f"  looks_logged_in = {logged}")

    if reason:
        # 会话确实过期：必须判为未登录，且 assert 必须抛 SessionExpired
        ck("过期页被判为未登录", logged is False)
        ck("原因文案含「会话已超时」", "超时" in reason, reason)
        try:
            auth.assert_logged_in(page)
            ck("assert_logged_in 抛 SessionExpired", False, "没有抛异常")
        except SessionExpired as e:
            ck("assert_logged_in 抛 SessionExpired", True, str(e))
    else:
        # 会话还有效：应判为已登录，且页面上确实有条件下拉框
        ck("有效会话被判为已登录", logged is True)
        try:
            n = page.locator(cfg["selectors"]["saved_condition_select"]).count()
        except Exception as e:
            n = -1
        ck("搜索页能定位到「保存した検索条件」下拉框", n > 0, f"count={n}")

    b.close()

print()
if FAIL:
    print("SOME_FAIL ->", FAIL)
    sys.exit(1)
print("ALL_PASS")
