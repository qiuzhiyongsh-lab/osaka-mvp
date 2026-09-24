# -*- coding: utf-8 -*-
"""v1.9.77 F3+F5：按番号重抓 PDF，落盘后接 F4 上云一致性闸门。

用法（必须在项目根目录运行；需 REINS 登录态 + 选择器已配置）：
  python tools/recrawl_pdf.py 300136017615 300140845984 ...   # 指定番号重抓
  python tools/recrawl_pdf.py --queue                         # 排空 pdf_recrawl_queue
  python tools/recrawl_pdf.py --queue --limit 50              # 排空前 50 条

流程：
  1) 登录 REINS（复用 crawler.Auth + _sync_playwright + _mask_webdriver，强制无头）
  2) 对每个番号调 crawler._fetch_detail_by_no 拿详情 + PDF bytes
  3) 落盘到 data/attachments/<番号>.pdf
  4) 调 pdf_cloud.upload_one(..., triggered_by="recrawl") → F4 上云前校验
       · mismatch（PDF 实为别的房源）→ 拦截上云 + 标 pdf_unverified + 告警
       · 其余（match/unverified/no_text/unknown）→ 照常上云、写回 pdf_url、清旧标记
  5) 更新 pdf_recrawl_queue 状态

依赖：REINS 选择器（site.bukken_search_url + selectors.bukken_search_inputs/button）。
      未配置 → _fetch_detail_by_no 直接返回 None，该番号标记 skip。
      已成約済/取り下げ的房源番号検索 0 件，无法补 PDF（标记 skip）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfgmod
from core import store as storemod
from core import crawler
from core import pdf_cloud


def _say(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _save_bytes(no: str, data: bytes) -> str:
    d = pdf_cloud.attachments_dir()
    d.mkdir(parents=True, exist_ok=True)
    dst = d / ("%s.pdf" % no)
    dst.write_bytes(data)
    return str(dst)


def recrawl_one(store, cfg, cos, cli, no, ctx, page) -> dict:
    """重抓单个番号 PDF，落盘后接 F4 上云闸门。返回结果 dict。"""
    sel = cfg.get("selectors") or {}
    rec = crawler._fetch_detail_by_no(ctx, page, no, sel, cfg, need_pdf=True, log=_say,
                                      store=store)
    if not rec:
        return {"no": no, "ok": False, "msg": "详情/PDF 未抓到（0件/选择器未配/解析失败）"}
    # 落盘（优先点击下载的 bytes；直链兜底用 urllib 带 Referer 下载）
    saved = False
    if rec.get("pdf_bytes"):
        _save_bytes(no, rec["pdf_bytes"])
        saved = True
    elif rec.get("pdf_url"):
        try:
            import urllib.request
            req = urllib.request.Request(
                rec["pdf_url"],
                headers={"Referer": (rec.get("source_url") or "")})
            data = urllib.request.urlopen(req, timeout=30).read()
            if data:
                _save_bytes(no, data)
                saved = True
        except Exception as e:  # noqa: BLE001
            _say("  · %s 直链下载失败：%s" % (no, e))
    if not saved:
        return {"no": no, "ok": False, "msg": "PDF 未落盘（缺図面或无直链）"}
    if cli is None:
        # 无 COS：仅落盘，等配置后手动上传
        store.mark_pdf_recrawl_done(no, ok=True)
        return {"no": no, "ok": True, "verdict": "local_only", "msg": "已落盘未上云"}
    # F4 上云前校验
    r = pdf_cloud.upload_one(no, cos, cli, triggered_by="recrawl")
    if not r.get("ok"):
        store.set_pdf_unverified(no, "mismatch", r.get("msg") or "recrawl 上云拦截")
        store.mark_pdf_recrawl_done(no, ok=False)
        return {"no": no, "ok": False, "verdict": r.get("verdict"),
                "msg": r.get("msg") or "上云失败"}
    store.set_pdf_unverified(no, r.get("verdict") or "match")
    store.mark_pdf_recrawl_done(no, ok=True)
    return {"no": no, "ok": True, "verdict": r.get("verdict"), "url": r.get("url")}


def main(argv: list) -> None:
    cfg = cfgmod.load()
    store = storemod.Store(cfgmod.paths(cfg)["db"])
    try:
        cos, cli = pdf_cloud.client()
    except Exception as e:  # noqa: BLE001
        _say("✗ COS 未配置，仅重抓落盘、不回推上云：%s" % e)
        cos = cli = None

    # 收集番号
    nos = [a for a in argv if not a.startswith("--")]
    limit = 50
    if "--queue" in argv:
        try:
            idx = argv.index("--limit")
            limit = int(argv[idx + 1])
        except (ValueError, IndexError):
            pass
        pending = store.pdf_recrawl_pending(limit=limit)
        nos = [r["property_no"] for r in pending] + nos
    if not nos:
        _say("用法：python tools/recrawl_pdf.py <番号...> [--queue] [--limit N]")
        return
    _say("待重抓 %d 个番号" % len(nos))

    with crawler._sync_playwright() as p:
        auth = crawler.Auth(cfg, cfgmod.paths(cfg)["session"])
        browser = auth.launch(p, headless=True)
        ctx, page = auth.open_authed_page(browser, log=_say)
        crawler._mask_webdriver(ctx)
        try:
            ok_n = fail_n = 0
            for i, no in enumerate(nos, 1):
                _say("· [%d/%d] %s" % (i, len(nos), no))
                try:
                    r = recrawl_one(store, cfg, cos, cli, no, ctx, page)
                except Exception as e:  # noqa: BLE001
                    r = {"no": no, "ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
                if r.get("ok"):
                    _say("  ✓ %s（verdict=%s）" % (r.get("msg", "成功"), r.get("verdict")))
                    ok_n += 1
                else:
                    _say("  ✗ %s" % r["msg"])
                    fail_n += 1
            _say("完成：成功 %d / 失败 %d" % (ok_n, fail_n))
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main(sys.argv[1:])
