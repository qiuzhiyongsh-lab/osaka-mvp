# -*- coding: utf-8 -*-
"""导出：① 当天 Excel ② 当天 HTML 页（可离线看，解决 23:00 后 REINS 打不开）"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from . import config as cfgmod

EXPORT_COLUMNS = [
    ("property_no", "物件番号"), ("building_name", "房源名称"), ("property_subtype", "物件種目"),
    ("kind", "大类"), ("ward", "区"), ("address", "地址"), ("line_station", "沿线・駅"),
    ("price", "总价(円)"), ("previous_price", "变更前价格(円)"),
    ("land_area", "土地面積(㎡)"), ("exclusive_area", "専有面積(㎡)"),
    ("unit_price_sqm", "㎡単価"), ("unit_price_tsubo", "坪単価"),
    ("built_year_month", "築年月"), ("layout", "間取り"), ("floor", "所在階"),
    ("image_count", "画像数"), ("pdf_path", "PDF路径"), ("source_url", "原网页链接"),
    ("registration_date", "登録年月日"), ("change_date", "変更年月日"),
    ("first_seen_at", "首次发现"), ("last_seen_at", "最近更新"),
]


def export_day_excel(store, cfg: dict, date: str) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    paths = cfgmod.paths(cfg)
    rows = store.export_rows(None)          # 全量导出（按天页做筛选）
    wb = Workbook()
    ws = wb.active
    ws.title = date.replace("-", "")

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="2F5597")
    for c, (_k, title) in enumerate(EXPORT_COLUMNS, 1):
        cell = ws.cell(row=1, column=c, value=title)
        cell.font, cell.fill = head_font, head_fill

    for r, row in enumerate(rows, 2):
        for c, (k, _t) in enumerate(EXPORT_COLUMNS, 1):
            ws.cell(row=r, column=c, value=row[k] if k in row.keys() else "")
    ws.freeze_panes = "A2"

    out = paths["exports"] / f"大阪房源_{date.replace('-', '')}.xlsx"
    wb.save(out)
    return str(out)


def export_daily_page(store, cfg: dict, date: str) -> str:
    """生成"这一天"的离线页面：含新盘/变更标记 + 点进详情 + 打开 PDF。"""
    paths = cfgmod.paths(cfg)
    day = date
    rows = list(store.conn.execute(
        "SELECT * FROM properties WHERE substr(COALESCE(first_seen_at,last_seen_at),1,10)=?"
        " ORDER BY property_no", (day,)))

    changes = {}
    for c in store.conn.execute(
            "SELECT property_no, change_type FROM changes WHERE substr(detected_at,1,10)=?",
            (day,)):
        changes.setdefault(c["property_no"], set()).add(c["change_type"])

    badge = {"new": ("新規", "#0a7"), "price_down": ("降价", "#d33"),
             "price_up": ("涨价", "#c80"), "modified": ("变更", "#36c")}

    items = []
    for r in rows:
        tags = changes.get(r["property_no"], set())
        tag_html = "".join(
            f'<span class="tag" style="background:{badge[t][1]}">{badge[t][0]}</span>'
            for t in tags if t in badge)
        pdf = ""
        if r["pdf_path"]:
            pdf = (f'<a class="btn" href="../{html.escape(r["pdf_path"])}" '
                   f'target="_blank">打开 PDF</a>')
        # v1.5.3：画像数 = -1 是"还没抓到详情、未知"，不要写成 0 枚；
        # 改显示 画/図/所 三个标记（与 REINS 列表一致）。
        try:
            _ic = int(r["image_count"])
        except Exception:
            _ic = -1
        _flags = "".join(
            f'<span class="mdb {c} {"on" if r[k] else "off"}">{zh}</span>'
            for c, k, zh in (("ga", "has_photo", "画"), ("zu", "has_floorplan", "図"),
                             ("sho", "has_map", "所")))
        imgs = _flags + (f' 画像 {_ic} 枚' if _ic >= 0 else '')
        items.append(f"""
        <div class="card">
          <div class="t">{html.escape(r['address'] or '')} {tag_html}</div>
          <div class="s">{html.escape(r['building_name'] or '')}　
            <code>{html.escape(r['property_no'])}</code></div>
          <div class="m">
            <b>{(r['price'] or 0)//10000:,} 万円</b>
            <span>{html.escape(r['property_subtype'] or '')}</span>
            <span>专有 {r['exclusive_area'] or '-'} / 土地 {r['land_area'] or '-'} ㎡</span>
            <span>{html.escape(r['layout'] or '')}</span>
            <span>{html.escape(r['floor'] or '')}</span>
            <span>{html.escape(r['line_station'] or '')}</span>
            <span>{imgs}</span>
          </div>
          <div class="a">{pdf}
            <a class="btn ghost" href="{html.escape(r['source_url'] or '#')}"
               target="_blank">原网页</a>
          </div>
        </div>""")

    out_html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>大阪房源 · {day}</title>
<style>
 body{{margin:0;background:#f4f6fa;font-family:-apple-system,"Noto Sans CJK SC",sans-serif;color:#1a2033}}
 header{{background:#1f3a6e;color:#fff;padding:16px 18px}}
 header h1{{margin:0;font-size:18px}} header p{{margin:6px 0 0;font-size:12px;opacity:.85}}
 .wrap{{max-width:980px;margin:0 auto;padding:14px}}
 .card{{background:#fff;border-radius:10px;padding:12px 14px;margin-bottom:10px;
        box-shadow:0 1px 3px rgba(0,0,0,.07)}}
 .t{{font-size:15px;font-weight:700;line-height:1.5}}
 .s{{font-size:12px;color:#65708a;margin:4px 0 8px}}
 .m{{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:#33405c}}
 .m b{{color:#c0392b;font-size:15px}}
 .a{{margin-top:9px;display:flex;gap:8px}}
 .btn{{display:inline-block;background:#1f3a6e;color:#fff;text-decoration:none;
       font-size:12px;padding:5px 12px;border-radius:6px}}
 .btn.ghost{{background:#eef1f7;color:#1f3a6e}}
 .tag{{color:#fff;font-size:11px;border-radius:4px;padding:1px 6px;margin-left:6px}}
 .empty{{background:#fff;border-radius:10px;padding:26px;text-align:center;color:#8a93a8}}
</style></head><body>
<header>
  <h1>大阪房源 · {day}</h1>
  <p>共 {len(rows)} 条 ｜ 生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ｜ 数据存于本地</p>
</header>
<div class="wrap">
{''.join(items) if items else '<div class="empty">这一天还没有数据。先跑一轮抓取试试。</div>'}
</div></body></html>"""

    out = paths["daily"] / f"{day}.html"
    out.write_text(out_html, encoding="utf-8")
    return str(out)
