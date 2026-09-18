# -*- coding: utf-8 -*-
"""v1.9.5 断言式校验：_read_total / _read_zero_note / _read_tab_count。

为什么不 import core.crawler：那边 import playwright，沙箱不一定装得上。
这里用 ast 把**真实源码**里的这几个函数抠出来 exec（谁也没改一行），
再用假 page + 假时钟跑确定性单测 —— 等价于「读真实代码 + mock 单测」，
而不是"我以为我改了"。
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "core", "crawler.py")
OUT = os.path.join(ROOT, "tools", "test_read_total_zero_page.out.txt")
SEL = {"result_total": "div.text-dark.ml-3", "result_rows": "div.p-table-body-row"}

lines: list[str] = []
ok_all = True


def rep(flag: bool, msg: str) -> None:
    global ok_all
    if not flag:
        ok_all = False
    lines.append(("  [OK]   " if flag else "  [FAIL] ") + msg)


# ---------------- 假时钟：wait_for_timeout 推进时间，18s 上界不用真等 ----------------
class CLK:
    t = 1000.0

    @classmethod
    def time(cls):
        return cls.t

    @classmethod
    def advance(cls, s):
        cls.t += s


class N:
    def __init__(self, txt):
        self._t = txt

    def inner_text(self, timeout=None):
        if self._t is None:
            raise RuntimeError("not found")
        return self._t


class Loc:
    def __init__(self, texts):
        self._texts = list(texts)

    def count(self):
        return len(self._texts)

    def nth(self, i):
        return N(self._texts[i] if 0 <= i < len(self._texts) else None)

    @property
    def first(self):
        return N(self._texts[0] if self._texts else None)


class Page:
    """note_danger / total / tabs / rows 全可真机对齐；total_after_s = 计数区几秒后才渲染。"""

    def __init__(self, note_danger=(), note_all=None, total=None, tabs=(),
                 rows=0, total_after_s=0.0):
        self._nd = list(note_danger)
        self._na = list(note_all) if note_all is not None else list(note_danger)
        self._tot = total
        self._tabs = list(tabs)
        self._rows = rows
        self._ta = total_after_s
        self.t0 = CLK.t

    def locator(self, sel):
        if sel == "div.p-note-danger":
            return Loc(self._nd)
        if sel == "div.p-note":
            return Loc(self._na)
        if sel == "ul.card-header-tabs a.nav-link":
            return Loc(self._tabs)
        if "p-table-body-row" in sel:
            return Loc(["row"] * self._rows)
        if self._tot and (CLK.t - self.t0) >= self._ta:
            return Loc([self._tot])
        return Loc([])

    def wait_for_timeout(self, ms):
        CLK.advance(ms / 1000.0)


def main() -> None:
    src = open(SRC, encoding="utf-8").read()
    tree = ast.parse(src)
    want = {"_read_zero_note", "_read_tab_count", "_read_total", "_safe_text", "_parse_total"}
    segs = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            segs.append(ast.get_source_segment(src, node))
    found = sorted({n.name for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name in want})
    lines.append("=== v1.9.5 _read_total 校验 ===")
    lines.append("源码: " + SRC)
    rep(sorted(want) == found, "ast 抽到全部目标函数: " + ", ".join(found))
    rep("py_compile 前置: 见主流程", "")

    ns = {"time": CLK, "re": re}
    exec(compile("\n\n".join(segs), "<crawler-extract>", "exec"), ns)
    read_total = ns["_read_total"]
    parse_total = ns["_parse_total"]

    logs: list[str] = []
    lg = logs.append

    def run(page, budget=None):
        CLK.t = 1000.0
        t0 = CLK.time()
        page.t0 = t0
        r = read_total(page, SEL, log=lg)
        return r, CLK.time() - t0

    # ---- A 戸建/マンション 正常页：主选择器即时返回（行为不许被兜底改动）----
    logs.clear()
    r, el = run(Page(total="1～50件 ／ 138件", total_after_s=0.5, rows=50))
    rep(r == "1～50件 ／ 138件", "A 有结果页取原文: %r" % r)
    rep(el < 2.0, "A 耗时 <2s（既有『即时返回』未被拖慢）: %.1fs" % el)

    # ---- B 0 件页（真机 DOM：p-note-danger + 无计数区 + 无结果表）----
    logs.clear()
    r, el = run(Page(note_danger=["検索結果が0件です。 検索条件を再入力してください。"],
                     tabs=["売一戸建(0件)"], rows=0))
    rep(r == "0件", "B 0件页 → %r" % r)
    rep(el <= 1.0, "B 0件页即时定性（旧版空等 60–90s）: %.1fs" % el)
    rep(any("非漏抓" in x for x in logs), "B 日志明示『非漏抓』: " + (logs[0] if logs else "-"))

    # ---- C 有结果但计数区结构不同（タウン 假想）：tab 见出し兜底 ----
    logs.clear()
    r, el = run(Page(tabs=["売一戸建(9件)"], rows=9))
    rep(r == "9件（タブ見出し）", "C tab 兜底 → %r" % r)
    rep(3.5 <= el < 8.0, "C 4s 宽限后用 tab（<8s，远小于 18s 上界）: %.1fs" % el)

    # ---- C2 tab 求和稳健性（未检索種別 渲成 0件 也不影响）----
    logs.clear()
    r, _ = run(Page(tabs=["売土地(0件)", "売一戸建(9件)"], rows=9))
    rep(r == "9件（タブ見出し）", "C2 多 tab 求和 (=9) → %r" % r)

    # ---- D 无 tab 有行：行数兜底 ----
    logs.clear()
    r, el = run(Page(tabs=[], rows=12))
    rep(r == "12件（首頁行数・総数不明）", "D 行数兜底 → %r" % r)
    rep(7.5 <= el < 9.5, "D 8s 宽限后用行数: %.1fs" % el)

    # ---- E 全空：记「未知」但仍在 18s 上界内收手 ----
    logs.clear()
    r, el = run(Page())
    rep(r == "", "E 全空 → %r（未知）" % r)
    rep(17.0 <= el <= 18.6, "E 上界钉死在 18s（非旧 60–90s）: %.1fs" % el)

    # ---- F 不许误判：500 件确认框类 note 里没有「0件」→ 不可判 0 ----
    logs.clear()
    r, _ = run(Page(note_danger=["検索結果が500件を超えています。続行しますか？"], rows=3))
    rep(r != "0件", "F 「500件を超えています」不误判 0（500件 含子串「0件」）→ %r" % r)
    logs.clear()
    r, _ = run(Page(note_danger=["該当する物件は30件です"], rows=3))
    rep(r != "0件", "F2 「30件」不误判 0 → %r" % r)
    logs.clear()
    r, el = run(Page(note_danger=["該当物件は０件でした"], rows=0))
    rep(r == "0件", "F3 全角「０件」仍能判 0 → %r (%.1fs)" % (r, el))
    logs.clear()
    r, _ = run(Page(note_danger=["対象件数 0 件（条件を見直してください）"], rows=0))
    rep(r == "0件", "F4 「0 件」（带空格）仍能判 0 → %r" % r)

    # ---- G 下架作用域守卫：0 件组不得进作用域 ----
    def in_scope(txt):
        return bool(txt) and parse_total(txt) != 0

    rep(in_scope("") is False, "G 未知 → 不进作用域")
    rep(in_scope("0件") is False, "G 确认 0 件 → **不进**作用域（不误下架）")
    rep(in_scope("9件（タブ見出し）") is True, "G tab 兜底 N>0 → 进作用域")
    rep(in_scope("12件（首頁行数・総数不明）") is True, "G 行数兜底 N>0 → 进作用域")
    rep(in_scope("1～50件 ／ 138件") is True, "G 正常总数 → 进作用域")

    # ---- H 落盘标记（防 Edit 假成功）----
    marks = {
        "_read_zero_note 定义": "def _read_zero_note(page) -> str:",
        "_read_tab_count 定义": "def _read_tab_count(page) -> int | None:",
        "0件告示判定": r're.search(r"(?<![\d,，０-９])[0０]\s*件", _t)',
        "tab 求和": 'page.locator("ul.card-header-tabs a.nav-link")',
        "4s 宽限": "_grace_tab = 4.0",
        "8s 宽限": "_grace_row = 8.0",
        "下架守卫": "if total_txt and _parse_total(total_txt) != 0:",
        "0条取证日志": "本组 0 条入账，但線上报告",
    }
    for k, v in marks.items():
        rep(v in src, "H 落盘 " + k)

    lines.append("")
    lines.append("RESULT: " + ("PASS" if ok_all else "FAIL"))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
