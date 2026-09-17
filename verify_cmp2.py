# -*- coding: utf-8 -*-
"""对比功能实测 + 截图（v2.2：含中文标签/金额格式化校验）。"""
import asyncio, json, sys
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"
IMG = "docs/UI_PRD/img"
W = ["300140682742", "300140687504", "300140703476"]  # 3 套，覆盖有/无管理费

async def main():
    res = {"errors": [], "label_ok": False, "fee_ok": False, "extra_raw_en": []}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        # ---- 桌面：对比页（带 3 套）----
        pg = await b.new_page(viewport={"width": 1440, "height": 1000})
        perr = []
        pg.on("pageerror", lambda e: perr.append(str(e)))
        pg.on("console", lambda m: perr.append(f"console:{m.type}:{m.text}") if m.type in ("error","warning") else None)
        await pg.goto(f"{BASE}/compare?nos=" + ",".join(W), wait_until="networkidle")
        await pg.wait_for_timeout(800)
        txt = await pg.inner_text("body")
        # 中文标签应出现
        for cn in ["交易方式", "用途地域", "修缮积立金（每月）", "中介公司", "公开状态"]:
            if cn in txt: res.setdefault("labels_seen", []).append(cn)
        # 不应出现裸英文键
        for en in ["trade_type", "use_zone", "repair_fund", "broker", "public_status"]:
            if en in txt: res["extra_raw_en"].append(en)
        res["label_ok"] = set(["交易方式","用途地域","修缮积立金（每月）","中介公司","公开状态"]).issubset(set(res.get("labels_seen",[])))
        # fee 格式化：裸整数 1326000 不应出现，应带 円
        res["fee_ok"] = ("1326000" not in txt) or ("円" in txt)
        await pg.screenshot(path=f"{IMG}/v2_compare_desk.png", full_page=True)
        # 隐藏相同项 + 只看核心
        await pg.check("#tHideSame"); await pg.check("#tCore")
        await pg.wait_for_timeout(300)
        await pg.screenshot(path=f"{IMG}/v2_compare_desk_core.png", full_page=True)
        await pg.uncheck("#tHideSame"); await pg.uncheck("#tCore")
        res["errors"].extend(perr)

        # ---- 桌面：查询页 + 选中 6 套 + 底部对比栏 ----
        pg2 = await b.new_page(viewport={"width": 1440, "height": 1000})
        perr2 = []
        pg2.on("pageerror", lambda e: perr2.append(str(e)))
        pg2.on("console", lambda m: perr2.append(f"console:{m.type}:{m.text}") if m.type in ("error","warning") else None)
        await pg2.goto(f"{BASE}/search", wait_until="networkidle")
        await pg2.wait_for_timeout(600)
        nos = await pg2.eval_on_selector_all(".qitem", "els => els.slice(0,7).map(e => e.getAttribute('data-no'))")
        # 点前 6 个加入对比
        picks = nos[:6]
        for i in range(6):
            await pg2.eval_on_selector_all(f".qitem:nth-child({i+1}) .qcmp", "b => b[0].click()")
        await pg2.wait_for_timeout(400)
        bar_visible = await pg2.eval_on_selector("#cmpbar", "el => el.classList.contains('show')")
        slots = await pg2.eval_on_selector_all("#cmpslots .cmpslot", "els => els.length")
        # 第 7 套应被拦截
        await pg2.eval_on_selector_all(f".qitem:nth-child({7}) .qcmp", "b => b[0].click()")
        await pg2.wait_for_timeout(200)
        # 拦截只弹 alert，仍可继续；确认 alert 文案
        alert_txt = []
        pg2.on("dialog", lambda d: (alert_txt.append(d.message), d.accept()))
        await pg2.wait_for_timeout(200)
        cmpcount = await pg2.eval_on_selector_all("#cmpslots .cmpslot", "els => els.length")
        res["picks"] = picks
        res["bar_visible"] = bar_visible
        res["slots_after_6"] = slots
        res["slots_after_7"] = cmpcount
        res["seventh_alert"] = alert_txt
        await pg2.screenshot(path=f"{IMG}/v2_search_desk.png", full_page=True)
        await pg2.screenshot(path=f"{IMG}/v2_search_desk_cmp.png", full_page=True)
        res["errors"].extend(perr2)

        # ---- 手机视口：查询页 + 对比栏 ----
        pm = await b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        await pm.goto(f"{BASE}/search", wait_until="networkidle")
        await pm.wait_for_timeout(600)
        await pm.screenshot(path=f"{IMG}/v2_search_mobile.png", full_page=True)
        await pm.eval_on_selector_all(".qitem:nth-child(1) .qcmp", "b => b[0].click()")
        await pm.wait_for_timeout(300)
        await pm.screenshot(path=f"{IMG}/v2_search_mobile_bar.png", full_page=True)
        await pm.goto(f"{BASE}/compare?nos=" + ",".join(picks[:3]), wait_until="networkidle")
        await pm.wait_for_timeout(600)
        await pm.screenshot(path=f"{IMG}/v2_compare_mobile.png", full_page=True)

        # ---- 详情页（紧凑）----
        pd = await b.new_page(viewport={"width": 1440, "height": 1000})
        await pd.goto(f"{BASE}/p/{W[0]}", wait_until="networkidle")
        await pd.wait_for_timeout(500)
        await pd.screenshot(path=f"{IMG}/v2_detail_desk.png", full_page=True)

        await b.close()
    print(json.dumps(res, ensure_ascii=False, indent=2))

asyncio.run(main())
