# -*- coding: utf-8 -*-
"""对比功能实测：桌面 + 手机双视口截图，并抓控制台报错。"""
import json, sys, pathlib
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
IMG = pathlib.Path(__file__).resolve().parent / "docs" / "UI_PRD" / "img"
IMG.mkdir(parents=True, exist_ok=True)

errors = []

def run():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width":1440,"height":900})
        page.on("console", lambda m: errors.append("CONSOLE:"+m.type+": "+m.text) if m.type in ("error","warning") else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR:"+str(e)))

        # ===== 桌面：查询页（每页 50 默认） =====
        page.goto(BASE + "/search", wait_until="networkidle")
        page.wait_for_selector(".qitem")
        page.wait_for_timeout(250)

        # ---- 取当页的物件番号（直接读渲染出来的 DOM） ----
        nos = page.eval_on_selector_all(
            ".qitem", "els => els.map(e => e.getAttribute('data-no'))")
        print("nos[0:6] =", nos[:6])
        print("本页行数 =", len(nos))

        page.screenshot(path=str(IMG/"v2_search_desk.png"), full_page=False)
        page.screenshot(path=str(IMG/"v2_search_desk_full.png"), full_page=True)

        # ---- 选中 6 套（点前 6 个「加入对比」） ----
        btns = page.query_selector_all(".qcmp")
        print("qcmp buttons:", len(btns))
        for i in range(6):
            btns[i].click()
            page.wait_for_timeout(60)
        page.wait_for_selector("#cmpbar.show", timeout=2000)
        page.wait_for_timeout(150)
        page.screenshot(path=str(IMG/"v2_search_desk_cmp.png"), full_page=False)
        cnt = page.evaluate("CompareBox.count()")
        print("selected count =", cnt)

        # ---- 测「超 6 套拦截」：尝试点第 7 个并捕获 alert ----
        alert_msg = []
        page.on("dialog", lambda d: (alert_msg.append(d.message), d.dismiss()))
        if len(btns) > 6:
            btns[6].click()
            page.wait_for_timeout(150)
        print("max6 alert =", alert_msg[0][:60] if alert_msg else "(n/a)")

        # ===== 桌面：对比页（6 套） =====
        page.goto(BASE + "/compare?nos=" + ",".join(nos[:6]), wait_until="networkidle")
        page.wait_for_selector(".cmptable", timeout=4000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(IMG/"v2_compare_desk.png"), full_page=False)
        page.screenshot(path=str(IMG/"v2_compare_desk_full.png"), full_page=True)
        # 切换「隐藏相同项」
        page.check("#tHideSame")
        page.wait_for_timeout(150)
        page.screenshot(path=str(IMG/"v2_compare_desk_hidesame.png"), full_page=False)
        page.uncheck("#tHideSame")
        # 「只看核心参数」
        page.check("#tCore")
        page.wait_for_timeout(150)
        page.screenshot(path=str(IMG/"v2_compare_desk_core.png"), full_page=False)
        page.uncheck("#tCore")
        # 测「列删除」
        page.click("button.x")
        page.wait_for_timeout(150)
        left = page.evaluate("CompareBox.count()")
        print("after dropCol count =", left)

        # ===== 单套详情页紧凑 =====
        page.goto(BASE + "/p/" + nos[0], wait_until="networkidle")
        page.wait_for_selector(".dinfo", timeout=4000)
        page.wait_for_timeout(200)
        page.screenshot(path=str(IMG/"v2_detail_desk.png"), full_page=True)

        # ===== 手机：查询页 + 对比栏 =====
        m = b.new_page(viewport={"width":390,"height":844})
        m.on("pageerror", lambda e: errors.append("MPAGEERR:"+str(e)))
        m.goto(BASE + "/search", wait_until="networkidle")
        m.wait_for_selector(".qitem")
        m.wait_for_timeout(200)
        mbtns = m.query_selector_all(".qcmp")
        for i in range(3):
            mbtns[i].click(); m.wait_for_timeout(60)
        m.wait_for_selector("#cmpbar.show")
        m.wait_for_timeout(150)
        m.screenshot(path=str(IMG/"v2_search_mobile.png"), full_page=False)
        # 手机滚动到最底看对比栏
        m.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        m.wait_for_timeout(150)
        m.screenshot(path=str(IMG/"v2_search_mobile_bar.png"), full_page=False)

        # ===== 手机：对比页 =====
        m.goto(BASE + "/compare?nos=" + ",".join(nos[:4]), wait_until="networkidle")
        m.wait_for_selector(".cmptable")
        m.wait_for_timeout(250)
        m.screenshot(path=str(IMG/"v2_compare_mobile.png"), full_page=False)
        m.screenshot(path=str(IMG/"v2_compare_mobile_full.png"), full_page=True)

        b.close()
    # 汇总
    print("\n=== JS 报错/警告 ===")
    for e in errors:
        print(" -", e)
    if not errors:
        print("（无）")
    with open(str(IMG.parent / "_verify_cmp.json"), "w", encoding="utf-8") as f:
        json.dump({"nos": nos[:6], "errors": errors}, f, ensure_ascii=False, indent=2)
    print("截图已写入", IMG)

if __name__ == "__main__":
    run()
