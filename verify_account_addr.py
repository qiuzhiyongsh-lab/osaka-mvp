# -*- coding: utf-8 -*-
"""账号管理页「网站地址」改动实测：桌面 + 手机各截一张，并收集报错。"""
import asyncio, json, pathlib
from playwright.async_api import async_playwright

OUT = pathlib.Path("docs/UI_PRD/img")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    res = {"errors": [], "console": [], "label": None, "value": None,
           "hint": None, "shots": []}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for tag, vw, vh in (("desk", 1440, 1000), ("mobile", 390, 900)):
            pg = await b.new_page(viewport={"width": vw, "height": vh})
            pg.on("pageerror", lambda e: res["errors"].append(str(e)))
            pg.on("console", lambda m: res["console"].append(m.type + ":" + m.text)
                  if m.type in ("error", "warning") else None)
            await pg.goto("http://127.0.0.1:8765/account", wait_until="networkidle")
            await pg.wait_for_timeout(500)
            if tag == "desk":
                res["label"] = await pg.inner_text("label:has-text('网站地址')")
                res["value"] = await pg.input_value("#site_name")
                res["hint"] = await pg.inner_text(".field:has(#site_name) p")
                res["has_old_label"] = await pg.locator("text=网站名称").count()
            f = OUT / f"v2_account_{tag}.png"
            await pg.screenshot(path=str(f), full_page=True)
            res["shots"].append(str(f))
            await pg.close()
        await b.close()
    print(json.dumps(res, ensure_ascii=False, indent=2))


asyncio.run(main())
