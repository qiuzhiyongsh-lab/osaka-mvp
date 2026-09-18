# -*- coding: utf-8 -*-
"""v1.9.4 _read_total 优化校验：py_compile + 落盘标记 + mock 单测（不连 REINS）。"""
import ast, time, sys, subprocess, io, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRAWLER = os.path.join(ROOT, "core", "crawler.py")
RESULT = os.path.join(ROOT, "tools", "_verify_read_total_v194.result.txt")
PY = r"C:\Users\25374\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

out = []


def log(s):
    out.append(str(s))


# 1) py_compile
r = subprocess.run([PY, "-m", "py_compile", CRAWLER], capture_output=True, text=True)
log("py_compile exit=%d  %s" % (r.returncode, (r.stderr.strip()[:300] if r.returncode else "OK")))
ok = r.returncode == 0

# 2) 落盘标记
src = open(CRAWLER, encoding="utf-8").read()
markers = [
    'timeout_s = 18.0',
    '首頁0行 → 记「未知」',
    '以首頁 %d 行估算総数',
]
for m in markers:
    hit = m in src
    log("落盘标记 %-28s : %s" % (repr(m), "OK" if hit else "MISSING"))
    ok = ok and hit

# 3) 抽取真实 _read_total 源码做 mock 单测
tree = ast.parse(src)
fn = None
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "_read_total":
        fn = ast.unparse(node)
        break
assert fn, "_read_total not found"

ns = {"time": time, "random": __import__("random")}


def _safe_text(page, locator):
    try:
        return page.locator(locator).inner_text()
    except Exception:
        return ""


ns["_safe_text"] = _safe_text
exec(fn, ns)
_read_total = ns["_read_total"]


class FakePage:
    def __init__(self, total, rows):
        self._total = total
        self._rows = rows
        self.polls = 0

    def locator(self, sel):
        if "result_rows" in sel or "p-table-body-row" in sel:
            rows = self._rows

            class R:
                def count(self): return rows
            return R()
        total = self._total

        class T:
            def inner_text(self): return total or ""
        return T()

    def wait_for_timeout(self, ms):
        self.polls += 1
        time.sleep(0.001)  # 让循环接近真实节奏，避免 busy-spin


def run(total, rows, timeout_s=0.5):
    p = FakePage(total, rows)
    t0 = time.time()
    txt = _read_total(p, {"result_total": "div.text-dark.ml-3",
                          "result_rows": "div.p-table-body-row"},
                      log=log, timeout_s=timeout_s)
    return txt, time.time() - t0, p.polls


# 用例 A：计数区正常 → 立即返回原文（不轮询）
a, dt, polls = run("1～50件 ／ 267件", 267)
log("A 计数区正常 -> %r  (用时%.2fs, polls=%d)" % (a, dt, polls))
ok = ok and a == "1～50件 ／ 267件" and polls <= 2

# 用例 B：计数区缺失 但 有行 -> 行数兜底
b, dt, polls = run(None, 5)
log("B 有表无计数区 -> %r  (用时%.2fs)" % (b, dt))
ok = ok and b == "5件（首頁行数・総数不明）"

# 用例 C：计数区缺失 且 0行 -> 记未知（返回空）
c, dt, polls = run(None, 0)
log("C 无表无计数区 -> %r  (用时%.2fs)" % (c, dt))
ok = ok and c == ""

# 用例 D：超时上限被钉死（关键优化点）——无计数区时不再等 60-90s
d, dt, polls = run(None, 0, timeout_s=0.5)
log("D 超时上限验证 -> 用时%.2fs（应≈0.5s，远小于旧 60-90s）" % dt)
ok = ok and dt < 2.0

log("=" * 40)
log("RESULT: %s" % ("PASS" if ok else "FAIL"))

open(RESULT, "w", encoding="utf-8").write("\n".join(out))
sys.exit(0 if ok else 1)
