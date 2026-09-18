# -*- coding: utf-8 -*-
"""离线取证：REINS 结果页 HTML 里「詳細」到底是什么元素、带不带 URL。

目的：判定 v1.9.1「阶段B」依赖的 detail_href 能不能从列表行取到。
输出 → _probe_detail_elem.out.txt
"""
import re
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRCS = [
    ROOT / "data" / "probe" / "result_page.html",
    ROOT / "data" / "probe" / "diag_after_search.html",
    ROOT / "data" / "probe" / "live_search_page.html",
]
OUT = Path(__file__).resolve().parent / "_probe_detail_elem.out.txt"
L = []


def p(s=""):
    L.append(str(s))


def main():
    for src in SRCS:
        p("=" * 74)
        p("SRC = %s (exists=%s size=%s)" % (src.name, src.exists(),
                                            src.stat().st_size if src.exists() else "-"))
        if not src.exists():
            continue
        html = src.read_text(encoding="utf-8", errors="replace")
        p("length = %s" % len(html))
        idxs = [m.start() for m in re.finditer("詳細", html)]
        p("「詳細」 出现次数 = %s" % len(idxs))
        for k, i in enumerate(idxs[:6]):
            seg = html[max(0, i - 420): i + 260]
            seg = re.sub(r"\s+", " ", seg)
            p("")
            p("--- #%d (offset %d) ---" % (k + 1, i))
            p("   ...%s..." % seg)
        # 锚点线索
        p("")
        p("含 GBK 的片段数 = %s" % len(re.findall(r"GBK", html)))
        for m in list(re.finditer(r"GBK[0-9A-Za-z_\-?=&.%]{0,80}", html))[:6]:
            p("   GBK: %s" % m.group(0))
        p("按钮类名线索（含 詳細/detail 的 class 片段）:")
        for m in list(re.finditer(r"class=\"[^\"]{0,120}(?:detail|btn)[^\"]{0,120}\"", html))[:8]:
            p("   %s" % m.group(0)[:150])
        p("是否出现 <button> = %s ; 出现次数 = %s"
          % ("<button" in html, len(re.findall(r"<button", html))))
        # 直接找 detail button 选择器候选
        p("含 '詳細' 的标签名统计:")
        tags = re.findall(r"<(\w+)[^>]{0,300}詳細", html)
        p("   %s" % (tags[:20],))
    p("")
    p("PROBE_DONE")


try:
    main()
except Exception:
    p("EXCEPTION:")
    p(traceback.format_exc())
OUT.write_text("\n".join(L), encoding="utf-8")
raise SystemExit(0)
