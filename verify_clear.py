# -*- coding: utf-8 -*-
"""回归测试：点「清空」后，页面必须立刻反映"已清空"（查询页的行勾选 / 对比页的表格）。"""
import asyncio, json
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"
IMG = "docs/UI_PRD/img"


async def main():
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 1000})
        pg = await ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        pg.on("console", lambda m: errors.append("console." + m.type + ": " + m.text)
              if m.type in ("error", "warning") else None)
        pg.on("dialog", lambda d: asyncio.ensure_future(d.accept()))   # 自动点「确定」

        # ---------- A. 查询页：加 3 套 → 清空 ----------
        await pg.goto(BASE + "/search", wait_until="networkidle")
        await pg.wait_for_timeout(500)
        for i in (1, 2, 3):
            await pg.eval_on_selector_all(f".qitem:nth-child({i}) .qcmp", "b=>b[0].click()")
            await pg.wait_for_timeout(150)
        out["A_added_on_buttons"] = await pg.eval_on_selector_all(".qcmp.on", "e=>e.length")
        out["A_added_picked_rows"] = await pg.eval_on_selector_all(".qitem.picked", "e=>e.length")
        out["A_bar_shown"] = await pg.eval_on_selector_all(
            "#cmpbar.show", "e=>e.length")
        out["A_nav_badge"] = await pg.eval_on_selector("#nav-cmp-n", "e=>e.textContent")
        out["A_slots"] = await pg.eval_on_selector_all("#cmpslots .cmpslot", "e=>e.length")
        await pg.screenshot(path=f"{IMG}/v2_search_desk_cmp.png", full_page=False)

        # 点「清空」→ confirm 自动确定
        await pg.click("#cmpclear")
        await pg.wait_for_timeout(600)
        out["A_after_clear_on_buttons"] = await pg.eval_on_selector_all(".qcmp.on", "e=>e.length")
        out["A_after_clear_picked_rows"] = await pg.eval_on_selector_all(".qitem.picked", "e=>e.length")
        out["A_after_clear_btn_texts"] = sorted(set(
            await pg.eval_on_selector_all(".qcmp", "els=>els.map(e=>e.textContent.trim())")))
        out["A_after_clear_bar_shown"] = await pg.eval_on_selector_all("#cmpbar.show", "e=>e.length")
        out["A_after_clear_nav_badge"] = await pg.eval_on_selector("#nav-cmp-n", "e=>e.textContent")
        out["A_after_clear_body_class"] = await pg.evaluate(
            "document.body.classList.contains('has-cmpbar')")
        out["A_after_clear_store"] = await pg.evaluate("localStorage.getItem('osaka.compare.v1')")
        out["A_rows_still_there"] = await pg.eval_on_selector_all(".qitem", "e=>e.length")

        # ---------- B. 重新选 3 套 → 对比页 → 清空对比栏 ----------
        for i in (1, 2, 3):
            await pg.eval_on_selector_all(f".qitem:nth-child({i}) .qcmp", "b=>b[0].click()")
            await pg.wait_for_timeout(150)
        nos = await pg.eval_on_selector_all(".qitem", "els=>els.slice(0,3).map(e=>e.getAttribute('data-no'))")
        await pg.goto(BASE + "/compare?nos=" + ",".join(nos), wait_until="networkidle")
        await pg.wait_for_timeout(800)
        out["B_cols_before"] = await pg.eval_on_selector_all(
            ".cmptable .cc-head", "e=>e.length")
        out["B_nav_badge_before"] = await pg.eval_on_selector("#nav-cmp-n", "e=>e.textContent")
        await pg.screenshot(path=f"{IMG}/v2_compare_desk.png", full_page=False)

        await pg.click("#cmpClear")
        await pg.wait_for_timeout(900)
        out["B_cols_after"] = await pg.eval_on_selector_all(".cmptable .cc-head", "e=>e.length")
        out["B_table_gone"] = (await pg.eval_on_selector_all(".cmptable", "e=>e.length")) == 0
        out["B_empty_hint"] = await pg.eval_on_selector("#cmpWrap", "e=>e.textContent.trim().slice(0,40)")
        out["B_nav_badge_after"] = await pg.eval_on_selector("#nav-cmp-n", "e=>e.textContent")
        out["B_store_after"] = await pg.evaluate("localStorage.getItem('osaka.compare.v1')")
        out["B_url_after"] = pg.url
        await pg.screenshot(path=f"{IMG}/v2_compare_cleared.png", full_page=False)

        # ---------- C. 手机视口：查询页清空 ----------
        mp = await ctx.new_page()
        await mp.set_viewport_size({"width": 390, "height": 844})
        await mp.goto(BASE + "/search", wait_until="networkidle")
        await mp.wait_for_timeout(500)
        for i in (1, 2):
            await mp.eval_on_selector_all(f".qitem:nth-child({i}) .qcmp", "b=>b[0].click()")
            await mp.wait_for_timeout(150)
        out["C_added"] = await mp.eval_on_selector_all(".qcmp.on", "e=>e.length")
        mp.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await mp.eval_on_selector("#cmpclear", "e=>e.click()")
        await mp.wait_for_timeout(700)
        out["C_after_clear_on"] = await mp.eval_on_selector_all(".qcmp.on", "e=>e.length")
        out["C_after_clear_picked"] = await mp.eval_on_selector_all(".qitem.picked", "e=>e.length")
        await mp.screenshot(path=f"{IMG}/v2_search_mobile_bar.png", full_page=False)

        out["errors"] = errors
        out["overflow_px"] = await pg.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - window.innerWidth)")
        await b.close()

    ok = (out["A_after_clear_on_buttons"] == 0 and out["A_after_clear_picked_rows"] == 0
          and out["A_after_clear_btn_texts"] == ["加入对比"]
          and out["A_after_clear_bar_shown"] == 0 and out["A_after_clear_nav_badge"] == ""
          and out["A_after_clear_store"] == "[]" and out["A_rows_still_there"] == 11
          and out["B_cols_after"] == 0 and out["B_table_gone"] and out["B_store_after"] == "[]"
          and out["B_url_after"].endswith("/compare")
          and out["C_after_clear_on"] == 0 and out["C_after_clear_picked"] == 0
          and not out["errors"])
    out["ALL_PASS"] = ok
    print(json.dumps(out, ensure_ascii=False, indent=2))


asyncio.run(main())
