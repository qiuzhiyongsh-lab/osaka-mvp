#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jinja2 模板内联 <script> 的 JS 语法门禁。

为什么需要它：直接对 Jinja2 模板跑 `node --check` 会在 `{{ }}` / `{% %}` 处报
假阳性并**提前中止**，于是模板里真正的 JS 语法错误（例如多余的花括号 `}`）
会被长期漏检 —— 结果是整个 <script> 解析失败、页面所有函数未定义、按钮点了没反应。
本工具先把 `{{ ... }}` 替换为 `1`、`{% ... %}` 删除，再对每个 <script> 块跑
`node --check`，从而真正验到脚本本体。

用法：
  python tools/check_html_js.py                      # 扫 web/templates/*.html
  python tools/check_html_js.py web/templates/a.html # 指定文件
退出码：0=全通过；1=有失败。
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _node() -> str | None:
    return shutil.which("node")


def check(path: str, node: str):
    html = open(path, encoding="utf-8").read()
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not blocks:
        return True, "no inline script"
    js = "\n;\n".join(blocks)
    js = re.sub(r"\{#.*?#\}", "", js, flags=re.S)        # {# 注释 #} -> (删除)
    js = re.sub(r"\{\{.*?\}\}", "1", js, flags=re.S)     # {{ expr }} -> 1
    js = re.sub(r"\{%.*?%\}", "", js, flags=re.S)        # {% tag %} -> (删除)
    fd, tmp = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        open(tmp, "w", encoding="utf-8").write(js)
        p = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
        if p.returncode == 0:
            return True, "OK"
        return False, "\n".join(p.stderr.strip().splitlines()[:6])
    finally:
        os.remove(tmp)


def main() -> int:
    node = _node()
    if not node:
        print("[check_html_js] 未找到 node，跳过")
        return 0
    args = sys.argv[1:]
    files = args or sorted(glob.glob(os.path.join(ROOT, "web", "templates", "*.html")))
    bad = 0
    for f in files:
        ok, msg = check(f, node)
        print(("  ✓ " if ok else "  ✗ ") + os.path.relpath(f, ROOT) + ("" if ok else "  -> " + msg))
        if not ok:
            bad += 1
    print("✅ HTML_JS_OK" if not bad else "❌ HTML_JS_FAIL: %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
