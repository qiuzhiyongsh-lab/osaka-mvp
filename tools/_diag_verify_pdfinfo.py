# -*- coding: utf-8 -*-
"""验证 v1.9.37 新增「PDF 文件生成出来的内容」蓝色区：
   1) 用真实 STORE + _detail_pairs 复刻「详细信息」值集
   2) 对 ai_structure 跑 Python 版 aiNorm 去重，确认 相同值不显示 / 不同值显示
   3) 生成静态预览 HTML（docs/pdfinfo_preview_v1.9.37.html）给勇哥看蓝色 5 列效果
"""
import sys, os, re, json, sqlite3

ROOT = r"C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from web import app as appmod
STORE = appmod.STORE

PNO = "300139241123"

def aiNorm(s):
    return re.sub(r"\s+", "", str(s if s is not None else ""))\
        .replace(",", "").replace("￥", "").replace("$", "").replace("¥", "")\
        .replace("（", "").replace("）", "").replace("(", "").replace(")", "").lower()

# ---- 1) 详细信息值集（真实渲染路径） ----
row = STORE.get_property(PNO)
pairs = appmod._detail_pairs(row)
info_vals = set()
for label, value, agency in pairs:
    nv = aiNorm(value)
    if nv:
        info_vals.add(nv)

# ---- 2) ai_structure 去重 ----
ai = sqlite3.connect(os.path.join(ROOT, "data/ai_pdf_store.db"))
sj = ai.execute("SELECT structure_json FROM ai_structure WHERE property_no=?", (PNO,)).fetchone()[0]
groups = json.loads(sj)["groups"]

shown, dedup, total = [], 0, 0
for g in groups:
    for f in g.get("fields", []):
        total += 1
        nv = aiNorm(f.get("value"))
        lab = (f.get("label") or {})
        lab_cn = lab.get("zh") or f.get("col") or ""
        if not nv or nv in info_vals:
            dedup += 1
        else:
            shown.append((lab_cn, f.get("value")))

print("=== 去重验证（%s）===" % PNO)
print("详细信息字段数(pairs):", len(pairs), " | 非空值集合:", len(info_vals))
print("ai_structure 字段总数:", total, " | 去重跳过:", dedup, " | 蓝色区显示:", len(shown))
print("--- 将被显示的字段（蓝色区）---")
for cn, v in shown:
    print("  ", cn, "=", v)

# ---- 3) 生成静态预览 HTML ----
def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

info_html = ""
for label, value, agency in pairs[:18]:  # 取前 18 个演示详细信息
    info_html += '<div class="df"><span class="dk">%s</span><span class="dv">%s</span></div>' % (esc(label), esc(value))

pdf_html = ""
for cn, v in shown:
    pdf_html += '<div class="pdf-df"><span class="pdf-dk">%s</span><span class="pdf-dv">%s</span></div>' % (esc(cn), esc(v))

HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{font-family:-apple-system,Segoe UI,"Noto Sans CJK SC",sans-serif;background:#f5f7fb;margin:0;padding:22px;color:#182033}
.wrap{max-width:1100px;margin:0 auto}
h3{font-size:14px;color:#6b7690;font-weight:600;margin:0 0 12px}
.card{background:#fff;border:1px solid #e6eaf3;border-radius:14px;padding:18px;margin-bottom:14px;box-shadow:0 1px 3px rgba(20,30,60,.06)}
.card h2{margin:0 0 12px;font-size:15.5px;display:flex;align-items:center;gap:9px;color:#182033}
.dinfo{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1px 16px;font-size:12.5px}
.df{display:flex;gap:7px;padding:1px 0;border-bottom:1px dotted #eef1f7}
.dk{color:#6b7690;flex:0 0 auto;min-width:76px;font-weight:700}
.dv{color:#182033;word-break:break-word}
.pdf-info{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:2px 18px;margin-top:4px}
.pdf-df{display:flex;gap:7px;padding:1px 0;border-bottom:1px dotted #eef1f7}
.pdf-dk{color:#185f9f;flex:0 0 auto;min-width:76px;font-weight:700}
.pdf-dv{color:#185f9f;word-break:break-word}
.note{color:#6b7690;font-size:12px;margin-top:8px;line-height:1.6}
</style></head><body><div class="wrap">
<h3>v1.9.37 预览 · 房源 %s（静态模拟，真实数据）</h3>
<div class="card"><h2>详细信息</h2><div class="dinfo">%s</div>
<div class="note">（仅展示前 18 个字段；与 PDF 重叠的值如「価格」会触发去重，不进入下方蓝色区）</div></div>
<div class="card"><h2>PDF 文件生成出来的内容</h2><div class="pdf-info">%s</div>
<div class="note">蓝色字 = PDF 抽取、且与「详细信息」值不同（或详细信息没有）的字段；值相同已去重不显示。固定 5 列，超 5 列自动换行。</div></div>
</div></body></html>""" % (PNO, info_html, pdf_html)

out = os.path.join(ROOT, "docs/pdfinfo_preview_v1.9.37.html")
open(out, "w", encoding="utf-8").write(HTML)
print("\n预览已生成:", out)
