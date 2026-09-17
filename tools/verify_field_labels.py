#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/verify_field_labels.py —— 「后台机器语言不得上屏」回归门禁

背景（勇哥 2026-09-16 反馈）：
    查询页展开区 / 详情页出现 has_map、has_photo、unit_price_sqm、ward 这类**机器键**，
    直接暴露在客户面前。根因不是某一行写错，而是**字段词典有缺口、且没有门禁拦**：
      · web/static/fieldmap.js 的 FIELD_CN 漏收录这几个键，而旧逻辑是
        「未收录 → 保留原始键」，于是英文键原样上屏；
      · web/app.py 的 JP2CN 同样漏，且 pdf_url 没进内部键表（也会漏出来）。

本脚本把「真实库」当输入，做 5 项断言（不靠猜、不靠人工点）：

  ① 全库键覆盖     detail_json 出现过的每个键 → 必须「有中文标签」或「明确属内部键」
  ② 四语文案齐     fieldmap 引用到的每个 i18n 键（xf.*）在 zh/zh-TW/en/ja 都有词条
  ③ 枚举值覆盖     trade_type / use_zone / public_status 的真实取值都能翻中文
  ④ Python 渲染路径 实跑 app._detail_pairs（详情页用的那条路）→ 标签不得含机器键特征
  ⑤ JS 渲染路径     按 fieldmap.js 的词典与规则复刻（查询页展开区那条路）→ 同样断言

用法：
    python tools/verify_field_labels.py             # 自动复制 data/jproperty.db 到临时副本跑
    python tools/verify_field_labels.py --db X.db   # 指定库
退出码：0 = 全绿；1 = 有红项。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELD_JS = ROOT / "web" / "static" / "fieldmap.js"
I18N_JS = ROOT / "web" / "static" / "i18n.js"
APP_PY = ROOT / "web" / "app.py"
_re_str = re.compile(r"""(['"])((?:[^'"\\]|\\.)*?)\1""")

RED: list[str] = []
YEL: list[str] = []


def red(msg: str) -> None:
    RED.append(msg)
    print("  [红] " + msg)


def yel(msg: str) -> None:
    YEL.append(msg)
    print("  [黄] " + msg)


# ---------------------------------------------------------------- 解析 JS
def _object_block(src: str, var: str) -> str:
    """取出 `var XXX = { ... };` 里的对象字面量文本（按大括号配平）。"""
    m = re.search(r"var\s+%s\s*=\s*\{" % re.escape(var), src)
    if not m:
        raise SystemExit("找不到 %s 定义" % var)
    i = src.index("{", m.start())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1:j]
    raise SystemExit("%s 对象未闭合" % var)


def _pairs(block: str) -> dict[str, str]:
    """把对象字面量里的 '键': '值' / "键": "值" / '键': 1 抽成 dict（保留原始值文本）。"""
    out: dict[str, str] = {}
    pat = re.compile(
        r"""(['"])((?:[^'"\\]|\\.)*?)\1\s*:\s*(?:(\d+|false|true)|(['"])((?:[^'"\\]|\\.)*?)\4)""",
        re.X,
    )
    for m in pat.finditer(block):
        k = m.group(2)
        v = m.group(3) if m.group(3) is not None else m.group(5)
        out[k.replace("\\'", "'").replace('\\"', '"')] = v
    return out


def _nested(block: str) -> dict[str, dict[str, str]]:
    """抽出 { 'k': { 'a': 'b', ... }, ... } 这种两层结构（两种引号都支持）。"""
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"""(['"])([A-Za-z_][A-Za-z0-9_]*)\1\s*:\s*\{""", block):
        key = m.group(2)
        i = block.index("{", m.start())
        depth = 0
        for j in range(i, len(block)):
            if block[j] == "{":
                depth += 1
            elif block[j] == "}":
                depth -= 1
                if depth == 0:
                    out[key] = _pairs(block[i + 1:j])
                    break
    return out


def load_js() -> dict:
    src = FIELD_JS.read_text(encoding="utf-8")
    field_key = _pairs(_object_block(src, "FIELD_KEY"))
    field_cn = _pairs(_object_block(src, "FIELD_CN"))
    field_skip = {k: v for k, v in _pairs(_object_block(src, "FIELD_SKIP")).items() if v == "1"}
    enum_cn = _nested(_object_block(src, "ENUM_CN"))
    return {"key": field_key, "cn": field_cn, "skip": field_skip, "enum": enum_cn}


def load_i18n() -> dict[str, set[str]]:
    src = I18N_JS.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {"zh": set(), "zh-TW": set(), "en": set(), "ja": set()}
    out["zh"] |= set(re.findall(r"ZH\['([^']+)'\]\s*=", src))
    for lang in ("zh-TW", "en", "ja"):
        out[lang] |= set(re.findall(r"DICT\['%s'\]\['([^']+)'\]\s*=" % re.escape(lang), src))
    return out


def load_py() -> dict:
    src = APP_PY.read_text(encoding="utf-8")

    def _dict(var: str) -> dict[str, str]:
        m = re.search(r"^%s\s*=\s*\{" % re.escape(var), src, re.M)
        if not m:
            raise SystemExit("找不到 %s" % var)
        i = src.index("{", m.start())
        depth = 0
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return _pairs(src[i + 1:j])
        raise SystemExit("%s 未闭合" % var)

    def _set(var: str) -> set[str]:
        m = re.search(r"^%s\s*=\s*\{(.*?)\}" % re.escape(var), src, re.M | re.S)
        if not m:
            raise SystemExit("找不到 %s" % var)
        return {m2.group(2).replace("\\'", "'").replace('\\"', '"')
                for m2 in _re_str.finditer(m.group(1))}

    enum_block = src[src.index("_ENUM_CN = {"):]
    return {
        "jp2cn": _dict("JP2CN"),
        "core": _set("_CORE_KEYS"),
        "internal": _set("_INTERNAL_KEYS"),
        "enum": _nested(enum_block),
    }


# ------------------------------------------------------- JS 渲染规则复刻
MACHINE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")

# REINS 里表示「没填」的占位符：既不是枚举值、也不该显示成内容（渲染为空是正确行为）
BLANK_VALUES = {"-", "－", "—", "None", "none", "", "なし"}


def is_machine_label(label: str) -> bool:
    return bool(label) and (bool(MACHINE_KEY.match(label)) or "_" in label)


def js_field_label(k: str, js: dict) -> str:
    if not k or k in js["skip"]:
        return ""
    return js["cn"].get(k, "")


def js_field_value(k: str, v) -> str:
    # 与 fieldmap.js fieldValue 对齐：空 / 半角"-" / 全角"－" 都视为无值，不渲染
    if v is None or v == "" or v == "-" or v == "－":
        return ""
    s = str(v).strip()
    if s in ("", "-"):
        return ""
    if k in ("has_photo", "has_floorplan", "has_map"):
        return "无" if s.lower() in ("0", "false", "none") else "有"
    # v1.7.3 口径统一：>=1万 走万円(一位小数)，小值走円 —— 与 fieldmap.js / app.unit() 一致
    if k in ("unit_price_sqm", "unit_price_tsubo") and s.replace(".", "").isdigit():
        per = "㎡" if k == "unit_price_sqm" else "坪"
        n = float(s)
        if n <= 0:
            return ""
        if n < 10000:
            return "{:,.0f} 円/{}".format(n, per)
        return "{:,.1f} 万円/{}".format(n / 10000, per)
    if k in ("management_fee", "repair_fund") and s.replace(".", "").isdigit():
        return "{:,} 円".format(int(float(s)))
    return s


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    args = ap.parse_args()

    src_db = Path(args.db) if args.db else ROOT / "data" / "jproperty.db"
    if not src_db.exists():
        print("找不到数据库：%s" % src_db)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="fieldlabel_")) / "jproperty.db"
    shutil.copy2(src_db, tmp)
    os.environ["OSAKA_DB"] = str(tmp)
    print("库副本：%s（不碰真库）" % tmp)

    js = load_js()
    i18n = load_i18n()
    py = load_py()

    sys.path.insert(0, str(ROOT / "web"))
    import app  # noqa: E402  （导入即完成建表/回填，均幂等）

    con = sqlite3.connect(str(tmp))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM properties WHERE detail_json IS NOT NULL AND detail_json<>''"
    ).fetchall()
    print("库内带详情的行数：%d" % len(rows))

    all_keys: dict[str, int] = {}
    enum_vals: dict[str, dict[str, int]] = {"trade_type": {}, "use_zone": {}, "public_status": {}}
    for r in rows:
        try:
            d = json.loads(r["detail_json"])
        except Exception:
            continue
        for k, v in d.items():
            all_keys[k] = all_keys.get(k, 0) + 1
            if k in enum_vals and v not in (None, ""):
                for part in re.split(r"[\n/]", str(v)):
                    part = part.strip()
                    if part and part not in BLANK_VALUES:
                        enum_vals[k][part] = enum_vals[k].get(part, 0) + 1

    # ① 全库键覆盖
    print("\n① 全库键覆盖（%d 个键）" % len(all_keys))
    for k, n in sorted(all_keys.items(), key=lambda x: -x[1]):
        internal = k.startswith("_") or k in py["internal"] or k in js["skip"]
        if internal:
            if k in js["cn"] or k in py["jp2cn"]:
                yel("%s(%d) 既是内部键又有标签，确认是否有意为之" % (k, n))
            continue
        js_lab = js_field_label(k, js)
        py_lab = py["jp2cn"].get(k) or (k in py["core"] and "（核心列，页头单独渲染）") or ""
        if not js_lab:
            red("%s(%d) 查询页无标签 → 会被隐藏（旧逻辑＝英文键上屏）" % (k, n))
        if not py_lab:
            red("%s(%d) 详情页无标签 → 会被隐藏" % (k, n))

    # ② 四语文案齐
    print("\n② i18n 四语言覆盖（fieldmap 引用的 xf.* 键）")
    miss_lang = {"zh": [], "zh-TW": [], "en": [], "ja": []}
    for k, i18n_key in js["key"].items():
        for lang in miss_lang:
            if i18n_key not in i18n[lang]:
                miss_lang[lang].append("%s→%s" % (k, i18n_key))
    for lang, miss in miss_lang.items():
        if miss:
            red("%s 缺 %d 条：%s" % (lang, len(miss), ", ".join(miss[:6])))
        else:
            print("  [绿] %-6s 齐" % lang)

    # ③ 枚举值覆盖
    print("\n③ REINS 枚举值 → 中文")
    for field, vals in enum_vals.items():
        for v, n in sorted(vals.items(), key=lambda x: -x[1]):
            js_ok = v in js["enum"].get(field, {})
            py_ok = v in py["enum"].get(field, {})
            if not (js_ok and py_ok):
                red("%s 的值 %r(%d) 缺翻译：js=%s py=%s" % (field, v, n, js_ok, py_ok))
        print("  %-14s 真实取值 %d 个已核对" % (field, len(vals)))

    # ④ Python 渲染路径（详情页）
    print("\n④ 详情页渲染路径实跑（app._detail_pairs）")
    bad4 = []
    for r in rows:
        for label, value, _ag in app._detail_pairs(r):
            if is_machine_label(str(label)):
                bad4.append("%s → %s" % (r["property_no"], label))
    if bad4:
        red("详情页仍有机器键上屏 %d 处，例：%s" % (len(bad4), bad4[:5]))
    else:
        print("  [绿] %d 行全跑过，无机器键标签" % len(rows))
    if app._MISSING_KEYS:
        red("详情页遇到词典未收录键（已隐藏，但必须补词典）：%s"
            % ", ".join(sorted(app._MISSING_KEYS)))
    else:
        print("  [绿] 词典未收录键 = 0")

    # ⑤ JS 渲染路径（查询页展开区）
    print("\n⑤ 查询页展开区渲染路径复刻（按 fieldmap.js 规则）")
    bad5 = []
    for r in rows:
        try:
            d = json.loads(r["detail_json"])
        except Exception:
            continue
        for k, v in d.items():
            if k.startswith("_") or k in js["skip"]:
                continue
            lab = js_field_label(k, js)
            if not lab:
                bad5.append("%s → 隐藏 %s" % (r["property_no"], k))
                continue
            if is_machine_label(lab):
                bad5.append("%s → %s" % (r["property_no"], lab))
            if not js_field_value(k, v) and str(v).strip() not in BLANK_VALUES:
                yel("%s 的 %s 值 %r 渲染为空" % (r["property_no"], k, v))
    if bad5:
        red("查询页仍有机器键/空标签 %d 处，例：%s" % (len(bad5), bad5[:5]))
    else:
        print("  [绿] %d 行全跑过，无机器键标签" % len(rows))

    # 汇总
    print("\n" + "=" * 68)
    if RED:
        print("结果：红 %d 项 / 黄 %d 项 —— 不通过" % (len(RED), len(YEL)))
        return 1
    print("结果：全绿（黄 %d 项）—— FIELD_LABELS_OK" % len(YEL))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
