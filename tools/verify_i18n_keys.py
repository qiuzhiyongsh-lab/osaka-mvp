#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/verify_i18n_keys.py —— 「JS 动态文案不得漏出裸 key」回归门禁

背景（勇哥 2026-09-17 截图反馈）：
    查询页「排序」下拉里显示的**不是中文**，而是 `sort.area` / `sort.price` /
    `sort.unit_sqm` 这样的裸 i18n key，客户一眼就能看到。

根因（真实、已定位）：
    i18n.js 的 zh-CN 走的是**两条不同路径**：
      · 静态元素（`<span data-i18n="k">中文</span>`）→ 用元素原始中文（`el.__o`）兜底，
        **不需要** ZH 字典，漏 key 也看不出来；
      · JS 动态串（`t('k')` / `window.t('k')`）→ **只能查 ZH 字典**（`t()` 里
        `if (l === 'zh-CN') { v = ZH[key] }`），ZH 里没有就把 key 原样返回并
        `console.warn('[i18n] 缺失翻译 key: …')`，界面上就是裸 key。
    v1.8.1 加 R20/R21/R22 时，`sort.*` / `view.toggle` / `filter.edit` /
    `search.today_short` 只写进了 `EXT['zh-TW']`（繁体），**忘了写 ZH** →
    繁体界面正常、简体界面裸 key。开发时看着繁体截图验收，就漏过去了。

    ⇒ 单看某一语言的字典齐不齐是不够的，必须**按「谁会被 t() 动态取用」逐 key 查 ZH**。

本脚本做的 4 项断言（静态扫描，不需要起服务）：
  ① 字面量：模板/JS 里 `t('k')` / `tf('k',…)` / `window.t('k')` 的每个 key 必须在 ZH 里；
  ② 动态前缀：`t('sort.' + x)` 这类拼接，用**全部语言字典里出现过的同前缀 key**当候选取集
     （即「谁会被拼出来」），逐个查 ZH —— 专治「繁体有、简体没有」；
  ③ 四语齐套（软）：① ② 里用到的 key 应在 zh-TW / en / ja 也有词条（缺则 `t()` 回退中文，
     不算裸 key，故只告警不判红）；
  ④ 元素属性：`data-i18n` / `data-i18n-ph` / `data-i18n-title` 的 key 至少在一个语言字典里
     存在（zh-CN 走元素原文，故不强制进 ZH）。

用法：
    python tools/verify_i18n_keys.py
退出码：0 = 全绿（可能有告警）；1 = 有红项（会出现裸 key）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                       # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent.parent
I18N_JS = ROOT / "web" / "static" / "i18n.js"
SCAN_GLOBS = [
    ("web/templates", "*.html"),
    ("web/static", "*.js"),
]

# 动态 key 的候选集来源：这些「命名空间」下的 key 一定是被 t() 拼出来的
DYNAMIC_NS = ("sort", "ch", "search.date_caliber", "view", "filter")

LANGS = ("zh-TW", "en", "ja")


# --------------------------------------------------------------------------
# 1) 解析 i18n.js：取出 ZH 字典的 key 集合 + 各语言字典 key 集合
# --------------------------------------------------------------------------
def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)//[^\n]*$", "", src)
    return src


def parse_i18n() -> tuple[set[str], dict[str, set[str]], set[str]]:
    """返回 (ZH keys, {lang: keys}, 所有字典里出现过的全部 key)"""
    src = I18N_JS.read_text(encoding="utf-8")

    # ZH 字面块：var ZH = { ... };
    m = re.search(r"var ZH\s*=\s*\{(.*?)\n  \};", _strip_js_comments(src), re.S)
    zh = set(re.findall(r"'([A-Za-z0-9_.\-]+)'\s*:", m.group(1))) if m else set()

    # 后置补丁赋值：ZH['k'] = 'x' / ZH.k = 'x'
    for k in re.findall(r"\bZH\[\s*'([^']+)'\s*\]\s*=", src):
        zh.add(k)
    for k in re.findall(r"\bZH\.([A-Za-z0-9_]+)\s*=", src):
        zh.add(k)

    others: dict[str, set[str]] = {lang: set() for lang in LANGS}
    # DICT['lang']['k'] = 'v'  （后置补丁）
    for lang, k in re.findall(r"DICT\[\s*'([^']+)'\s*\]\[\s*'([^']+)'\s*\]\s*=", src):
        if lang in others:
            others[lang].add(k)
    # 大块字面量里的 key（含嵌套，统一按「引号:」形态抓）
    all_kv = set(re.findall(r"'([A-Za-z0-9_.\-]+)'\s*:\s*'", src))
    for lang in LANGS:
        others[lang] |= all_kv
    # ⚠ `all_keys` 必须并入**各语言**的 key：动态前缀的候选取集就是靠它
    #   （首版漏了 others → kpi.today_down_tip 这种只有 DICT 补丁的 key 被判不存在）
    all_keys = set(all_kv) | zh
    for lang in LANGS:
        all_keys |= others[lang]
    return zh, others, all_keys


# --------------------------------------------------------------------------
# 2) 扫描模板 / JS：收集 t() 用法
# --------------------------------------------------------------------------
# 只认「后面是 ) 或 ,」的调用 —— 否则 `t('ch.' + x)` 里的 'ch.' 会被误当成完整 key
LIT = re.compile(r"(?:window\.)?\btf?\(\s*'([A-Za-z0-9_.\-]+)'\s*[,)]")
DYN = re.compile(r"(?:window\.)?\btf?\(\s*'([A-Za-z0-9_.\-]*)'\s*\+\s*[A-Za-z_$]")
ATTR = re.compile(r"data-i18n(?:-ph|-title)?\s*=\s*\"([A-Za-z0-9_.\-]+)\"")


def _strip_comments(txt: str) -> str:
    """去掉注释，避免把**示例/说明里的 t('key')** 当成真实用法（首版踩过：
    i18n.js 文件头 `window.t('key')` 的用法说明被误判为缺失 key）。
    `//` 只在**不紧跟冒号**时去掉，保护 `https://…` 这类字符串。"""
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
    txt = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
    return re.sub(r"(?<![:\"'/])//[^\n]*", "", txt)


def scan_usage() -> tuple[dict[str, set[str]], set[str], set[str]]:
    literal: dict[str, set[str]] = {}
    dynamic_prefix: dict[str, set[str]] = {}
    attrs: set[str] = set()
    for sub, pat in SCAN_GLOBS:
        for f in sorted((ROOT / sub).glob(pat)):
            txt = _strip_comments(f.read_text(encoding="utf-8", errors="replace"))
            rel = f"{sub}/{f.name}"
            for k in LIT.findall(txt):
                literal.setdefault(k, set()).add(rel)
            for p in DYN.findall(txt):
                if p:
                    dynamic_prefix.setdefault(p.rstrip("."), set()).add(rel)
            for k in ATTR.findall(txt):
                attrs.add(k)
    return literal, dynamic_prefix, attrs


def main() -> int:
    if not I18N_JS.exists():
        print("✗ 找不到 web/static/i18n.js")
        return 1
    zh, others, all_keys = parse_i18n()
    literal, dyn_prefix, attrs = scan_usage()

    red: list[str] = []
    warn: list[str] = []
    ok: list[str] = []

    # ---- ① 字面量 t('k') 必须在 ZH ----
    miss_lit = sorted(k for k in literal if k not in zh)
    if miss_lit:
        red.append(f"① 字面量 t('key') 在 ZH 缺失 {len(miss_lit)} 个：")
        for k in miss_lit:
            red.append(f"     · {k}   ← 用于 {'、'.join(sorted(literal[k]))}")
    else:
        ok.append(f"① 字面量 t() 共 {len(literal)} 个 key → ZH 全覆盖")

    # ---- ② 动态前缀：候选取集 = 字典里同前缀的全部 key ----
    #   `t('sort.' + x)` → 前缀 'sort.'（拼出来带点）；
    #   `t('row.tag_' + x)` / `t('collect.switch_' + x)` → 前缀 'row.tag_'（拼出来不带点）。
    #   所以候选 = startswith(前缀) 或 startswith(前缀 + '.')，首版只查了后者会误报。
    cand: dict[str, dict[str, set[str]]] = {}
    for p in sorted(dyn_prefix):
        hit = sorted(k for k in all_keys
                     if k != p and (k.startswith(p) or k.startswith(p + ".")))
        if not hit:
            warn.append(f"② 动态前缀 '{p}' 在字典里找不到任何同前缀 key"
                        f"（用于 {'、'.join(sorted(dyn_prefix[p]))}）—— 无法校验，请人工确认")
            continue
        cand[p] = {"missing": {k for k in hit if k not in zh}, "all": set(hit)}
    miss_dyn = sorted({k for v in cand.values() for k in v["missing"]})
    if miss_dyn:
        red.append(f"② 动态拼接可产生的 key 在 ZH 缺失 {len(miss_dyn)} 个"
                   f"（简体界面会显示裸 key）：")
        for p in sorted(cand):
            if cand[p]["missing"]:
                red.append(f"     · 前缀 '{p}' ← 用于 {'、'.join(sorted(dyn_prefix[p]))}")
                for k in sorted(cand[p]["missing"]):
                    red.append(f"         - {k}")
    else:
        n_c = sum(len(v["all"]) for v in cand.values())
        ok.append(f"② 动态拼接 {len(cand)} 个前缀 / {n_c} 个候选 key → ZH 全覆盖")

    # ---- ③ 四语齐套（软告警） ----
    used = set(literal) | {k for v in cand.values() for k in v["all"]}
    for lang in LANGS:
        miss = sorted(k for k in used if k not in others[lang])
        if miss:
            warn.append(f"③ {lang} 缺 {len(miss)} 个词条（t() 会回退成中文，非裸 key）："
                        f"{'、'.join(miss[:12])}{' …' if len(miss) > 12 else ''}")
        else:
            ok.append(f"③ {lang} 词条齐套（{len(used)} 个 key）")

    # ---- ④ 元素属性 key 至少要存在于任一语言字典（软：zh-CN 走元素原文，不会裸 key） ----
    miss_attr = sorted(k for k in attrs if k not in all_keys)
    if miss_attr:
        warn.append(f"④ data-i18n 属性里 {len(miss_attr)} 个 key 未进任何字典"
                    f"（简体不受影响，繁/英/日会显示中文原文）：{'、'.join(miss_attr)}")
    else:
        ok.append(f"④ data-i18n* 属性共 {len(attrs)} 个 key → 字典有收录")

    # ---- 输出 ----
    print("=" * 66)
    print("i18n 门禁 · JS 动态文案不得漏出裸 key")
    print("=" * 66)
    for line in ok:
        print("  ✓ " + line)
    for line in warn:
        print("  ⚠ " + line)
    if red:
        print("-" * 66)
        for line in red:
            print("  ✗ " + line)
        print("-" * 66)
        print(f"✗ I18N_KEYS_FAIL：{len(miss_lit)} 个字面量 + {len(miss_dyn)} 个动态 key 缺失")
        print("  修法：把这些 key 补进 i18n.js 的 `var ZH = {…}`（可写在文件后段的"
              "`ZH['k']='中文';` 补丁区），并同步 EXT 的四语。")
        return 1
    print("-" * 66)
    print("✓ I18N_KEYS_OK  （zh-CN 不会出现裸 key）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
