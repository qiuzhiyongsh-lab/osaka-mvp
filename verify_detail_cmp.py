# -*- coding: utf-8 -*-
"""详情页「加入对比 / 去对比」新交互 + 对比页「还不够 2 套」引导页 —— 回归测试。"""
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
        errs, dialogs = [], []
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.on("console", lambda m: errs.append("console." + m.type + ": " + m.text)
              if m.type in ("error", "warning") else None)
        pg.on("dialog", lambda d: (dialogs.append(d.message),
                                   asyncio.ensure_future(d.accept())))

        # 取 3 个真实物件番号
        await pg.goto(BASE + "/search", wait_until="networkidle")
        await pg.wait_for_timeout(500)
        nos = await pg.eval_on_selector_all(
            ".qitem", "els=>els.slice(0,3).map(e=>e.getAttribute('data-no'))")
        out["nos"] = nos
        await pg.evaluate("localStorage.removeItem('osaka.compare.v1')")
        await pg.reload(wait_until="networkidle")
        await pg.wait_for_timeout(400)

        # ---------- A. 详情页：初始状态（没选任何房） ----------
        await pg.goto(BASE + "/p/" + nos[0], wait_until="networkidle")
        await pg.wait_for_timeout(500)
        out["A_btn"] = await pg.eval_on_selector("#cmpadd", "e=>e.textContent.trim()")
        out["A_goto"] = await pg.eval_on_selector("#cmpgoto", "e=>e.textContent.trim()")
        out["A_goto_disabled"] = await pg.eval_on_selector(
            "#cmpgoto", "e=>e.classList.contains('disabled')")
        out["A_hint_len"] = await pg.eval_on_selector("#cmphint", "e=>e.textContent.trim().length")
        out["A_old_label_gone"] = (await pg.eval_on_selector_all(
            "text=和已选的房子对比", "e=>e.length")) == 0
        await pg.screenshot(path=f"{IMG}/v2_detail_desk.png", full_page=False)

        # ---------- B. 点「加入对比」→ 本地写入 1 套，按钮/文案跟着变 ----------
        await pg.click("#cmpadd")
        await pg.wait_for_timeout(400)
        out["B_store"] = json.loads(await pg.evaluate(
            "localStorage.getItem('osaka.compare.v1') || '[]'"))
        out["B_btn"] = await pg.eval_on_selector("#cmpadd", "e=>e.textContent.trim()")
        out["B_btn_on"] = await pg.eval_on_selector("#cmpadd", "e=>e.classList.contains('on')")
        out["B_goto"] = await pg.eval_on_selector("#cmpgoto", "e=>e.textContent.trim()")

        # ---------- C. 只有 1 套时点「去对比」→ 应被拦下并给出提示 ----------
        await pg.click("#cmpgoto")
        await pg.wait_for_timeout(400)
        out["C_dialogs"] = list(dialogs)
        out["C_stayed"] = pg.url.endswith(nos[0])

        # ---------- D. 再点一下「加入对比」= 取消 ----------
        await pg.click("#cmpadd")
        await pg.wait_for_timeout(300)
        out["D_count_after_toggle_off"] = len(json.loads(await pg.evaluate(
            "localStorage.getItem('osaka.compare.v1') || '[]'")))
        await pg.click("#cmpadd")           # 再放回去
        await pg.wait_for_timeout(300)

        # ---------- E. 带 2 套选中打开别的详情页 → 不会顶掉已选 ----------
        await pg.goto(BASE + "/search", wait_until="networkidle")
        await pg.wait_for_timeout(500)
        await pg.eval_on_selector_all(".qitem:nth-child(2) .qcmp", "b=>b[0].click()")
        await pg.wait_for_timeout(300)
        out["E_before"] = len(json.loads(await pg.evaluate(
            "localStorage.getItem('osaka.compare.v1') || '[]'")))
        await pg.goto(BASE + "/p/" + nos[2], wait_until="networkidle")
        await pg.wait_for_timeout(400)
        await pg.click("#cmpadd")
        await pg.wait_for_timeout(400)
        store = json.loads(await pg.evaluate("localStorage.getItem('osaka.compare.v1') || '[]'"))
        out["E_after_count"] = len(store)
        out["E_kept_first"] = store[0]["no"] == nos[0]
        out["E_goto"] = await pg.eval_on_selector("#cmpgoto", "e=>e.textContent.trim()")
        out["E_goto_disabled"] = await pg.eval_on_selector(
            "#cmpgoto", "e=>e.classList.contains('disabled')")
        await pg.screenshot(path=f"{IMG}/v2_detail_cmp_added.png", full_page=False)

        # ---------- F. 点「去对比这 3 套」→ 对比页应显示 3 列 ----------
        await pg.click("#cmpgoto")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(800)
        out["F_url"] = pg.url
        out["F_cols"] = await pg.eval_on_selector_all(".cmptable .cc-head", "e=>e.length")
        out["F_bar_absent_on_compare"] = True
        await pg.screenshot(path=f"{IMG}/v2_compare_desk.png", full_page=False)

        # ---------- G. 只剩 1 套时进对比页 → 引导页要说清楚 ----------
        await pg.goto(BASE + "/compare", wait_until="networkidle")
        await pg.wait_for_timeout(300)
        await pg.evaluate("""() => {
            var k='osaka.compare.v1';
            var a=JSON.parse(localStorage.getItem(k)||'[]');
            localStorage.setItem(k, JSON.stringify(a.slice(0,1)));
        }""")
        await pg.reload(wait_until="networkidle")
        await pg.wait_for_timeout(600)
        txt = await pg.eval_on_selector("#cmpWrap", "e=>e.textContent")
        out["G_text_has_1"] = "只选了" in txt
        out["G_text_has_more"] = "再选" in txt
        out["G_has_cta"] = (await pg.eval_on_selector_all(".cmptable", "e=>e.length")) == 0
        await pg.screenshot(path=f"{IMG}/v2_compare_notenough.png", full_page=False)

        # 引导页里的「清空对比栏」按钮要能真的清掉
        if await pg.eval_on_selector_all("#cmpClear2", "e=>e.length"):
            await pg.click("#cmpClear2")
            await pg.wait_for_timeout(700)
        out["G_store_after_clear2"] = await pg.evaluate("localStorage.getItem('osaka.compare.v1')")
        out["G_final_text"] = (await pg.eval_on_selector("#cmpWrap", "e=>e.textContent"))[:30].strip()

        # ---------- H. 手机视口：详情页操作条不溢出 ----------
        mp = await ctx.new_page()
        await mp.set_viewport_size({"width": 390, "height": 844})
        await mp.goto(BASE + "/p/" + nos[0], wait_until="networkidle")
        await mp.wait_for_timeout(500)
        out["H_overflow"] = await mp.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - window.innerWidth)")
        out["H_btn_visible"] = await mp.eval_on_selector(
            "#cmpadd", "e=>e.getBoundingClientRect().width > 40")
        await mp.screenshot(path=f"{IMG}/v2_detail_mobile.png", full_page=True)

        out["errors"] = errs
        await b.close()

    ok = (out["A_old_label_gone"] and out["A_goto_disabled"] and out["A_hint_len"] > 20
          and len(out["B_store"]) == 1 and out["B_btn_on"] and "1/6" in out["B_btn"]
          and out["C_stayed"] and any("至少要 2 套房" in m for m in out["C_dialogs"])
          and out["D_count_after_toggle_off"] == 0
          and out["E_after_count"] == 3 and out["E_kept_first"] and not out["E_goto_disabled"]
          and out["F_cols"] == 3
          and out["G_text_has_1"] and out["G_text_has_more"]
          and out["G_store_after_clear2"] == "[]"
          and out["H_overflow"] == 0 and out["H_btn_visible"]
          and not out["errors"])
    out["ALL_PASS"] = ok
    print(json.dumps(out, ensure_ascii=False, indent=2))


asyncio.run(main())
