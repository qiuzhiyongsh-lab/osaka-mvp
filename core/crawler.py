# -*- coding: utf-8 -*-
"""一轮抓取。两种模式共用同一套入库/落盘管道。

  demo 模式：本地生成样例数据 → 直接进管道（用于验证"数据确实落到本地"）
  live 模式：Edge + 已保存会话 → REINS 用「保存検索条件」一键套用 → 検索 →
             翻页 → 网格结果解析 → （新盘）进详情页抓全字段 + 下载図面 PDF

调用方式（外部只需要这一句）：
    stats = run_round(store, cfg, trigger="manual", progress_cb=print)
"""
from __future__ import annotations

import os
import queue
import random
import re
import threading
import time
import urllib.request
import sqlite3
# v1.8.2：timedelta 供「前日补齐」算前一天（D2）
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config as cfgmod
from . import differ, pipeline
from .auth import Auth, SessionExpired, _sync_playwright


# ===========================================================================
# v1.3.0 · 后台 PDF 下载器（受限并发，最大在途 = 线程数）
# ---------------------------------------------------------------------------
# 为什么改：旧做法是 `dp.expect_download()` —— 点链接后**等整个文件落盘**，
# 这 5–10 秒主流程完全空转（用户 2026-09-13 指出："下载 PDF 的时候可以继续浏览别的"）。
# 现在：主线程只把「PDF 直链」丢进队列就继续抓下一条详情；后台线程用**同一会话的
# Cookie / User-Agent / Referer** 直接 GET 并落盘。并发上限＝worker 线程数（用户拍板 = 2）。
#
# 确定性（用户硬要求）：先写 `xxx.pdf.part`，成功后 `os.replace()` **原子改名**；
# 所以磁盘上永远要么没有、要么是一个完整文件，绝不会留半个；
# 中断时**最多丢"正在下的那一个"**，此前所有 PDF 都已落盘。
# ===========================================================================
class _GeoFailed(RuntimeError):
    """地区基线（大阪府/大阪市）直录失败——调用方应捕获并跳过该组，不杀整轮。

    v1.9.2 引入：原直录分支失败直接 raise RuntimeError("中止本轮")，会把当天 12 组同步
    全打死（2026-09-18 勇哥实抓：第 4 组 REINS 慢渲染 8s 超时 → 整轮中止、当天漏抓 8 组）。
    改为专用异常，让调用方区分「地区直录瞬断」与「真问题（会话过期/真改版）」：
    捕获 _GeoFailed → 跳过该组继续；其他异常（SessionExpired 等）仍向上中止保护数据。
    """
class _PdfWorker:
    def __init__(self, out_dir: Path, cookies: str, ua: str, log,
                 max_inflight: int = 2, timeout: float = 60.0):
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.cookie = cookies or ""
        self.ua = ua or "Mozilla/5.0"
        self.log = log
        self.timeout = timeout
        self.q: "queue.Queue" = queue.Queue()
        self.done: "queue.Queue" = queue.Queue()
        self.n_ok = self.n_fail = 0
        self.ms_total = 0
        self._threads = []
        for i in range(max(1, int(max_inflight))):
            t = threading.Thread(target=self._run, name="pdfworker%d" % i, daemon=True)
            t.start()
            self._threads.append(t)

    # ---- 主线程接口 ----
    def submit(self, no: str, url: str, referer: str = "") -> bool:
        """把一条 PDF 任务丢进队列（非阻塞）。已有同名完整文件的直接跳过。"""
        if not no or not url:
            return False
        dst = self.out / (no + ".pdf")
        try:
            if dst.exists() and dst.stat().st_size > 0:
                return False                      # 灵活匹配：已有就不重复下
        except Exception:
            pass
        self.q.put((no, url, referer))
        return True

    def drain(self) -> list:
        """主线程在每条房源之后调用：取回已下完的结果 [(no, path, ms, err)]。"""
        got = []
        while True:
            try:
                got.append(self.done.get_nowait())
            except queue.Empty:
                return got

    def join(self, timeout: float | None = None) -> None:
        """收尾：等队列里的 PDF 都下完（轮次结束前调用）。"""
        t0 = time.monotonic()
        while not self.q.empty() or self._busy():
            if timeout is not None and (time.monotonic() - t0) > timeout:
                self.log("   · 后台 PDF 收尾超时，剩余任务留给下一轮（已下完的不受影响）")
                return
            time.sleep(0.4)
    def _busy(self) -> bool:
        return any(getattr(t, "_working", False) for t in self._threads)

    # ---- 后台线程 ----
    def _run(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                return
            no, url, referer = item
            threading.current_thread()._working = True     # 供 _busy() 判断"还有在下的"
            t0 = time.perf_counter()
            dst = None
            err = ""
            try:
                headers = {"User-Agent": self.ua, "Accept": "*/*"}
                if self.cookie:
                    headers["Cookie"] = self.cookie
                if referer:
                    headers["Referer"] = referer
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = r.read()
                if not data:
                    raise RuntimeError("空文件")
                tmp = self.out / (no + ".pdf.part")
                tmp.write_bytes(data)
                final = self.out / (no + ".pdf")
                os.replace(tmp, final)            # 原子改名：不会留半个文件
                dst = final
                self.n_ok += 1
            except Exception as e:               # noqa: BLE001
                err = "%s: %s" % (type(e).__name__, e)
                self.n_fail += 1
            finally:
                threading.current_thread()._working = False
                ms = int((time.perf_counter() - t0) * 1000)
                self.ms_total += ms
                self.done.put((no, str(dst) if dst else None, ms, err))

    @property
    def avg_ms(self) -> int:
        n = self.n_ok + self.n_fail
        return int(self.ms_total / n) if n else 0


def _pdf_file(store_root: Path, no: str) -> Path:
    return Path(store_root) / "attachments" / (no + ".pdf")


def _pdf_exists(root: Path, no: str) -> bool:
    """**以磁盘为准**判断 PDF 是否已有（确定性存储：文件在 = 已下载）。"""
    try:
        p = _pdf_file(root, no)
        return p.exists() and p.stat().st_size > 0
    except Exception:
        return False


def _photo_count_of(dp):
    """v1.5.3：从详情页读**真实照片张数**（读不到返回 None = 未知，绝不瞎填 0）。

    REINS 详情页有独立的「物件画像」区块（h2 文字），两种形态（真机快照坐实）：
      · 没有照片 → 区块里就一句「物件画像は登録されていません。」→ 返回 0（确定是 0）；
      · 有照片   → 区块里的图片元素 → 数出来（img / 背景图 / 缩略图都算）。
    【为什么不能用旧的 dp.locator("img").count()】REINS 详情页整页 <img> 恒为 0
    （图片走背景/懒加载），所以旧代码永远写 0 → 界面上全显示「画像 0 枚」。
    """
    js = """() => {
      const hs = Array.from(document.querySelectorAll('h2, h3'));
      const h = hs.find(x => (x.innerText || '').indexOf('物件画像') >= 0);
      if (!h) return null;
      let box = h.nextElementSibling, guard = 0;
      while (box && box.tagName !== 'DIV' && guard++ < 6) box = box.nextElementSibling;
      if (!box) return null;
      const t = box.innerText || '';
      if (t.indexOf('登録されていません') >= 0) return 0;
      let n = box.querySelectorAll('img').length;
      if (!n) {
        n = box.querySelectorAll(
          '[style*="background-image"], .swiper-slide, figure, [class*="thumbnail"], [class*="photo"]'
        ).length;
      }
      return n > 0 ? n : null;   // 区块在但数不出图 → 未知，别写 0
    }"""
    try:
        v = dp.evaluate(js)
    except Exception:
        return None
    try:
        return None if v is None else int(v)
    except Exception:
        return None


def _pdf_url_of(page, sel) -> str:
    """从详情页里取 PDF 直链（拿不到就返回空 → 退回原来的点击下载方式）。"""
    sels = [sel.get("pdf_link") or "a:has-text('PDF')",
            "a:has-text('図面')", "a:has-text('物件資料')"]
    for s in sels:
        try:
            # v1.4.1：超时 2500 → 1200。三个选择器里只要有两个不存在，原来就要白等 5 秒
            # （详情阶段每条都做一次）；DOM 早已加载完，短超时足够，拿不到就退回复用点击下载。
            href = page.locator(s).first.get_attribute("href", timeout=1200)
            if href and str(href).strip():
                h = str(href).strip()
                if h.startswith("//"):
                    h = "https:" + h
                elif h.startswith("/"):
                    h = "https://system.reins.jp" + h
                return h
        except Exception:
            continue
    return ""


# v1.5.6：PDF 只能靠**点按钮**拿，页面上根本没有带 href 的 PDF 链接。
#   真机 DOM 实证（data/probe/clickflow_detail.html）：
#       <h2>物件図面</h2>
#       <div class="col-auto">帝塚山セントポリア 2280万円.pdf</div>
#       <div class="col-auto"><button type="button" class="btn p-button btn-outline">図面参照</button></div>
#   —— 是 <button>図面参照</button>，**整页没有一个 <a href> 指向 PDF**。
#   所以 `_pdf_url_of` 必然返回空（今天 385 条新增、0 份 PDF 就是这个原因）。
#   ⚠ 顺序很重要：页面底部导航里还有个 <button>画像・図面</button>，
#     老代码用 `button:has-text('図面')` + .first，很可能点到那个导航按钮 → 点了没反应。
#     这里把「図面参照」排在最前，并且逐个试、失败就换下一个。
_PDF_CLICK_SELECTORS = [
    "button:has-text('図面参照')",
    "a:has-text('図面参照')",
    "button:text-is('図面')",
    "a:has-text('図面')",
    "button:has-text('PDF')",
    "a:has-text('PDF')",
    "a:has-text('物件資料')",
]


def _download_pdf_by_click(dp, log=None, timeout_ms: int = 20000):
    """点「図面参照」按钮把 PDF 下下来。返回 bytes；拿不到返回 None **并记日志**。

    【为什么要记日志】老代码这里 `except: pass` 全吞了，
    于是"PDF 一份没下"在日志里完全看不出来（只显示"成功 0 / 失败 0"），
    问题藏了整整一天。现在失败一定留痕。
    """
    say = log or (lambda *_a, **_k: None)
    for s in _PDF_CLICK_SELECTORS:
        try:
            loc = dp.locator(s).first
            if loc.count() == 0:
                continue
            with dp.expect_download(timeout=timeout_ms) as dinfo:
                loc.click(timeout=8000)
            p = dinfo.value
            data = Path(p.path()).read_bytes()
            if data:
                return data
        except Exception:                                    # noqa: BLE001
            continue
    try:
        # 日志里留个证据：页面到底有没有図面区块
        has = dp.locator("button:has-text('図面'), a:has-text('図面')").count()
        say("  · PDF 未取到（页面上図面类控件 %d 个；本套可能本来就没有図面）" % has)
    except Exception:                                        # noqa: BLE001
        pass
    return None


def _href_from_row(row) -> str:
    """从列表行里取详情直链（**短超时探测，绝不返回假直链**）。

    v1.4.1 修复（真机实测的隐性开销）：
      原实现是 `row.locator("a:has-text('詳細')").first.get_attribute("href", timeout=3000)`，
      但 REINS 列表页里「詳細」是 `<button>`、**整页 0 个含"詳細"的 `<a>`**（实测 58 button / 0 a），
      于是 Playwright 会**干等满 3000ms 超时**；随后退路 `row.evaluate("el => el.querySelector('a')")`
      又抓到了行内唯一的 `<a>`（中介公司链接），返回 `"#"` 这个**假直链**。
      实测：5 行 = 15.11s → **3.02s/行**纯浪费（约占列表阶段耗时的 69%）。

    现在：先数锚点（`count()` 不等待），只认「文字含 詳細」或「href 含 GBK」的锚点，
    每次取值都带**短超时**；拿不到就老实返回空串（调用方可据此走点击退路或跳过），
    绝不猜、不伪造地址。
    """
    if row is None:
        return ""
    try:
        anchors = row.locator("a[href]")
        n = anchors.count()
    except Exception:
        return ""
    for i in range(n):
        try:
            a = anchors.nth(i)
            h = a.get_attribute("href", timeout=800) or ""
            if not h or h == "#":
                continue
            txt = a.inner_text(timeout=500) or ""
            if "詳細" in txt or "GBK" in h:
                return _abs_reins_url(h)
        except Exception:
            continue
    return ""


def _detail_href_of(row) -> str:
    """从列表行里取「詳細」链接的绝对地址（v1.4.0 两阶段：阶段1先记下，阶段2再打开）。

    拿不到就返回空串 —— 调用方据此本轮跳过详情（下轮仍会补，因为 detail_json 还是空的）。
    v1.4.1：改为委托 `_href_from_row`（短超时、不返回假直链）。
    """
    return _href_from_row(row)


# ---------------------------------------------------------------------------
# 结果列表页：网格坐标 → 本地字段  （2026-09-11 真机落盘 HTML 实测坐标）
#   坐标来自每个 .p-table-body-item 的 style="grid-row-start:N; grid-column:..."
# ---------------------------------------------------------------------------
LIST_FIELD_MAP = {
    (1, 2): "property_no",          # 物件番号
    (1, 5): "property_subtype",     # 物件種目
    (1, 10): "exclusive_area",      # 専有面積
    (1, 13): "address",             # 所在地
    (2, 2): "trade_type",           # 取引態様（进 detail_json）
    (2, 5): "price",                # 価格
    (2, 8): "use_zone",             # 用途地域（进 detail_json）
    (2, 10): "unit_price_sqm",      # ㎡単価
    (2, 13): "building_name",       # 建物名
    (2, 19): "floor",               # 所在階
    (2, 23): "layout",              # 間取り
    (3, 2): "public_status",        # 公開状況（进 detail_json）
    (3, 5): "management_fee",       # 管理費（进 detail_json）
    (3, 10): "repair_fund",         # 修繕積立金（进 detail_json）
    (3, 13): "line_station",        # 沿線駅
    (3, 19): "access",              # 交通（徒歩）→ 追加到 line_station
    (4, 13): "broker",              # 仲介会社（进 detail_json）
    (5, 5): "built_year_month",     # 築年月
    (5, 13): "broker_tel",          # 電話番号（进 detail_json）
}

# 详情页标签 → 本地字段（覆盖列表缺的 土地面積/建物面積/登録・変更年月日 等）
LABEL_MAP = {
    "物件番号": "property_no", "物件種目": "property_subtype",
    "所在地名1": "address", "所在地": "address", "建物名": "building_name",
    "基本価格": "price", "価格": "price", "変更前価格": "previous_price",
    "専有面積": "exclusive_area", "土地面積": "land_area",
    "建物面積": "building_area",
    "㎡単価": "unit_price_sqm", "坪単価": "unit_price_tsubo",
    "築年月": "built_year_month", "間取り": "layout", "所在階": "floor",
    "地上階層": "above_ground_floors", "沿線": "line_station", "駅名": "_station",
    "登録年月日": "registration_date", "変更年月日": "change_date",
    "取引態様": "trade_type", "用途地域": "use_zone",
    "管理費": "management_fee", "修繕積立金": "repair_fund",
    "仲介会社名": "broker", "電話番号": "broker_tel",
}


class _BatchSink:
    """边抓边落库（v1.2.2）。

    老流程是「整轮抓完 → 最后一次性 ingest」：一轮真实抓取按模拟人工节奏可能跑几小时，
    这段时间里本地库 / PDF / Excel **全都是空的** —— 用户看到日志一直在刷 ✓，
    去查却一条数据都没有，会误判成"下载成功了但没数据"；
    而且所有房源的 pdf_bytes 全堆在内存里，中途重启/崩溃就是整轮白跑。

    改成攒批落库后：
      · 抓一部分就能在「房源查询」里看到一部分（今天的数据边抓边出现）；
      · PDF 即时落到 data/attachments（「本地文件」页立刻可见）；
      · runs 表实时刷新进度（页面能看到"已落库 N 条"在涨）；
      · 中途被中断，也只丢最后不足一批的几条。
    """

    def __init__(self, store, cfg: dict, log, run_id: int, flush_every: int = 5):
        self.store = store
        self.cfg = cfg
        self.log = log
        self.run_id = run_id
        self.every = max(1, int(flush_every or 5))
        self.buf: list[dict] = []
        self.seen: set = set()
        self.stats = {"scanned": 0, "fetched": 0, "new": 0, "changed": 0,
                      "pdf_saved": 0, "errors": [],
                      "ms_pdf": 0, "n_pdf": 0,        # 埋点：PDF 累计耗时 / 计数
                      "ms_detail": 0, "n_detail": 0,   # 埋点：详情页取数耗时 / 计数
                      "ms_delay": 0}                   # 埋点：每条之间的故意停顿耗时

    def add(self, rec: dict, force: bool = False) -> bool:
        """收下一条记录；攒够 flush_every 条就落库一次。轮内重复的番号直接丢弃。"""
        no = rec.get("property_no")
        if no:
            if no in self.seen:
                return False
            self.seen.add(no)
        self.buf.append(rec)
        if rec.get("_ms_pdf") is not None:      # 埋点：累计 PDF 耗时（用于决定提速方案）
            self.stats["ms_pdf"] += int(rec["_ms_pdf"])
            self.stats["n_pdf"] += 1
        if rec.get("_ms_detail") is not None:   # 埋点：累计详情页取数耗时
            self.stats["ms_detail"] += int(rec["_ms_detail"])
            self.stats["n_detail"] += 1
        if force or len(self.buf) >= self.every:
            self.flush()
        return True

    def flush(self) -> None:
        """把缓冲里的记录真正落盘（主档 + 快照 + 变更 + PDF），并刷新 runs 进度。"""
        if not self.buf:
            return
        batch, self.buf = self.buf, []
        st = pipeline.ingest(batch, self.store, self.cfg, self.run_id)
        for k in ("scanned", "fetched", "new", "changed", "pdf_saved"):
            self.stats[k] += st[k]
        self.stats["errors"] += st["errors"]
        try:
            self.store.progress_run(self.run_id, self.stats["scanned"], self.stats["fetched"],
                                    self.stats["new"], self.stats["changed"],
                                    pdf_saved=self.stats["pdf_saved"])  # v1.4.0：PDF 层实时进度
        except Exception:
            pass
        # 日志节流：v1.3.0 起 flush_every=1（每条都落库），但**日志仍每 5 条打一次**，
        # 否则日志会被"已落库 1 条/2 条/3 条…"刷屏、反而看不清。
        # v1.4.0：阶段1是「列表模式」，fetched 不涨（详情还没抓），所以节流与展示都改用
        # scanned（＝已收列表条数）——否则阶段1 期间日志一直停在"已落库 0 条"，看着像卡死。
        if self.stats["scanned"] % 5 == 0 or self.stats["scanned"] <= 1:
            parts = ["   · 列表已收 " + str(self.stats["scanned"]) + " 条"
                     "（已落库 " + str(self.stats["fetched"]) + " 条"
                     "｜新盘 " + str(self.stats["new"]) + " / 变更 " + str(self.stats["changed"])
                     + " / PDF " + str(self.stats["pdf_saved"]) + "）"]
            # 埋点：把"单条耗时"精确拆成三段，回答"那 20 秒到底归谁"
            if self.stats["n_detail"]:
                parts.append("｜详情均耗时 %.1fs" % (self.stats["ms_detail"] / self.stats["n_detail"] / 1000.0))
                parts.append("｜停顿均耗时 %.1fs" % (self.stats["ms_delay"] / self.stats["n_detail"] / 1000.0))
            if self.stats["n_pdf"]:
                parts.append("｜PDF 均耗时 %.1fs（n=%d）" % (self.stats["ms_pdf"] / self.stats["n_pdf"] / 1000.0,
                                                             self.stats["n_pdf"]))
            self.log("".join(parts))


# ============================================================
# v1.5.2 · 断点记录 + 「问人」窗口（勇哥 2026-09-14 提的三点，已拍板）
#   ① 断点记录：每房型/每页把进度写进 crawl_state，重启后可从断点接着跑。
#   ② 只在出问题时问一句：会话失效 / 连续多条行内点击失败 / 重启后发现未完成的轮次。
#   ③ 15 秒没点 → 按默认动作走（"按正常情况去处理"），**绝不卡住整轮**。
#   触发点之外的正常抓取流程完全不打扰人。
# ============================================================
def _ask_operator(store, run_id, kind: str, params: dict | None = None,
                  options: list | None = None, default_action: str = "",
                  timeout_s: int = 15, log=None) -> str:
    """在页面上发起一次最多 `timeout_s` 秒的询问，返回最终动作。

    · 人点了某个按钮 → 立刻返回那个动作（这就是"点了就做该操作"的断点续传衔接）；
    · 15 秒没人点 → 把默认动作写回库，按正常流程继续（绝不无限等待）；
    · 库/表出任何问题 → 直接返回默认动作，绝不因此中断抓取。
    """
    log = log or (lambda *_a, **_k: None)
    try:
        did = store.add_decision(run_id, kind, params or {}, options or [],
                                 default_action, timeout_s)
    except Exception as e:                                   # noqa: BLE001
        log("  · （询问窗口起不来：%s）→ 按默认「%s」继续，不卡住" % (type(e).__name__, default_action))
        return default_action
    log("⏳ 需要你决定（%s）：%s 秒内可在页面选择；没选就按默认「%s」继续"
        % (kind, timeout_s, default_action))
    deadline = time.time() + max(1, int(timeout_s))
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            a = store.decision_result(did)
        except Exception:                                    # noqa: BLE001
            a = None
        if a:
            log("  · 你选了「%s」→ 照办" % a)
            return a
    try:
        store.decide(did, default_action, by_whom="timeout")
    except Exception:                                        # noqa: BLE001
        pass
    log("  · %s 秒无人操作 → 按默认「%s」继续（该干嘛干嘛）" % (timeout_s, default_action))
    return default_action


def run_round(store, cfg: dict, trigger: str = "manual", progress_cb=None,
              scale: float = 0.25, trial: bool = False, resume: bool = False,
              _retry: bool = False) -> dict[str, Any]:
    """跑完一轮。返回统计字典，并写入 runs 表。

    trial=True：试跑模式（第一次连真实站点时用）——只跑 1 个保存条件、最多 3 条、
    1 页列表、最小请求量，验证「能不能登录、能不能查到、能不能落到本地」。
    resume=True：从上次中断的房型接着跑（v1.5.2 断点续传，配合 crawl_state 表）。
    """
    log = progress_cb or (lambda *_a, **_k: None)
    mode = (cfg.get("app", {}).get("mode") or "demo").lower()
    run_id = store.start_run(trigger)
    _ask = cfg.get("crawl", {}).get("operator_ask", {}) or {}
    _ask_on = bool(_ask.get("enabled", True))
    _ask_secs = int(_ask.get("timeout_s", 15) or 15)
    stats: dict[str, Any] = {"run_id": run_id, "mode": mode, "scanned": 0,
                            "fetched": 0, "new": 0, "changed": 0, "pdf_saved": 0,
                            "errors": [], "status": "ok", "source": mode}

    try:
        log(f"▶ 开始一轮抓取（模式：{'离线演示' if mode == 'demo' else '真实 REINS'}，触发：{trigger}）")

        sink = None
        items: list[dict] = []
        online_total = 0          # v1.4.0：本轮线上总数（列表层分母）
        # v1.8.2 修 NameError：收尾汇总框要用的「本轮实际覆盖几个種目组」。
        #   ⚠ 旧代码在收尾处直接引用 `groups`，但 `groups` 只存在于 `_live_items` 的局部作用域
        #   → NameError 被下面的宽 except 吞成「本轮失败」，手动更新永远收不到完成通知。
        #   这里由 `_live_items` 把真实组数带出来（试跑模式只跑 1 组，必须用真实值）。
        live_groups_n = 0
        succeeded_subtypes: set[str] = set()   # 本轮真正成功的種目（下架作用域；主轮被关时保持空）
        date_sync_nos: set[str] = set()   # R1（v1.9.76）：本轮「今日日期同步」成功抓到的番号，併入收尾 seen 用于复活
        # v1.8.4（勇哥拍板 A）：全期間主轮 **默认关**。
        #   原 D1/R2 默认开，理由是「①数据主力（补详情/PDF）+ ②下架基线（本轮并集判下架）」。
        #   但勇哥 challenge 命中要害：bulk 搜索受 REINS 500 上限，老房源被挤出窗口即不在
        #   本轮并集 → 连续 N 轮 → 误标 is_active=0（7/1 房源即此场景）。「缺席≠下架」。
        #   所以关掉主轮：只跑当日日期同步（列表壳），mark_delisted 作用域为空
        #   → complete=False → 只复活、不判下架（宁可漏判绝不误杀）。
        #   代价：不再自动补详情/PDF；真正的下架判定待另起 per-番号 复核 pass（架构正确解）。
        #   关掉时走下方 elif 降级分支，items/succeeded_subtypes 保持函数开头预声明空值，
        #   收尾 mark_delisted(seen=set, scope=set, complete=False) 安全只复活。
        main_round_on = bool((cfg.get("crawl", {}) or {}).get("main_round_enabled", False))

        # ---- v1.5.12：先跑「今日(登録/変更)」日期同步，把新盘(列表壳)先落库 ----
        #   再交给下面 _live_items 的阶段2 同轮补详情+PDF，消除「一轮滞后」
        #   （v1.5.11 把它放在轮末，导致新增的空壳要等下一轮才补详情）。
        #   自包含开浏览器；失败绝不影响本轮主抓取，只记日志。
        if mode == "live" and not trial and cfg.get("crawl", {}).get("sync_dates_enabled", True):
            try:
                _ds = sync_today_dates(store, cfg, log, today=None, run_id=run_id)
                stats["date_sync"] = _ds
                date_sync_nos = set(_ds.get("property_nos") or [])
                if _ds.get("inlined"):
                    log("✓ 概览页日期同步内联补详情 %d 条（主链统一，无需阶段B 兜底）" % _ds["inlined"])
            except Exception as e:                                 # noqa: BLE001
                stats["errors"].append(f"今日日期同步失败：{type(e).__name__}: {e}")
                log(f"⚠ 今日日期同步失败（不影响本轮）：{e}")

        if mode == "demo":
            items = _demo_items(store, cfg, 0.05 if trial else scale, log)
        elif not main_round_on and not trial:
            # v1.8.2（D1/R2）：开关关掉时的**显式**降级路径（默认不会走到）。
            #   必须把后果打全，否则以后有人看到"跑得挺快"就以为没差别。
            log("⚠ 全期間主轮已被设置关闭（crawl.main_round_enabled=false）")
            log("  · 本轮只做「当日日期同步」→ 只有列表壳，**不补详情、不下 PDF**")
            log("  · 本轮**不判下架**（作用域为空 → 只做复活；长期关会攒出已成交仍在架的脏数据）")
            stats["main_round_skipped"] = True
            online_total = 0
            # v1.9.1（解耦阶段B）：即便主轮关，也独立补「详情+PDF」，
            # 让一轮下载默认就完整（阶段C 下架基线仍由 main_round 门控、默认关）。
            try:
                _bf = _backfill_details_pdfs(store, cfg, log, run_id)
                stats["detail_backfilled"] = _bf.get("fetched", 0)
            except Exception as _e:                    # noqa: BLE001
                log("⚠ 阶段B 补详情失败（不影响本轮列表同步）：%s"
                    % (type(_e).__name__ + ": " + str(_e)))
        else:
            # 真实模式：边抓边落库（每 flush_every 条写一次库 + PDF），不再等整轮结束
            sink = _BatchSink(store, cfg, log, run_id,
                              cfg.get("crawl", {}).get("flush_every", 5))
            (items, online_total, succeeded_subtypes,
             live_groups_n) = _live_items(store, cfg, log, trial=trial, sink=sink,
                                          resume=resume)

        if sink is not None:
            sink.flush()                     # 收尾：把最后不足一批的也落库
            st = sink.stats
        elif items:
            st = pipeline.ingest(items, store, cfg, run_id)
        else:
            st = {"scanned": 0, "fetched": 0, "new": 0, "changed": 0,
                  "pdf_saved": 0, "errors": []}

        # v1.4.0：scanned（列表层）与 fetched（详情层）分开记。
        # 只刷新了列表字段的轮次 fetched=0 但 scanned>0 —— 在"房源都已下载过"时是常态，
        # 如实记录才不会让人误以为这轮白跑了。
        stats["scanned"] = st.get("scanned", 0)
        stats["pdf_saved"] = st.get("pdf_saved", 0)
        # v1.9.78 F4：main_round 关（当前默认，v1.8.4 勇哥拍板）的降级分支 items 为空
        #   → st 全 0，但本轮实际跑了「当日日期同步」（列表壳）与「阶段B 补详情」。
        #   必须把这些真实数量回填进 stats，禁止全 0 掩盖真实抓取（PRD F4：
        #   random 轮 scanned/fetched/new/change 全 0 即属此类）。
        if not stats["scanned"] and date_sync_nos:
            stats["scanned"] = len(date_sync_nos)
        if not stats.get("fetched") and stats.get("detail_backfilled"):
            stats["fetched"] = stats["detail_backfilled"]
        if st["fetched"]:
            stats.update({k: st[k] for k in ("fetched", "new", "changed")})
            log(f"✓ 已入库：扫描 {stats['scanned']} 条 / 落库 {stats['fetched']} 条 "
                f"/ 新盘 {stats['new']} 条 / 变更 {stats['changed']} 条 / PDF {stats['pdf_saved']} 份")
        else:
            log("· 本轮没有需要新增/更新的房源（全部已在本地）")
        stats["errors"] += st["errors"]

        # ---- 收尾：导出 Excel + 生成按天页 ----
        try:
            from .exporter import export_day_excel, export_daily_page
            if cfg.get("download", {}).get("export_excel", True):
                p = export_day_excel(store, cfg, datetime.now().strftime("%Y-%m-%d"))
                log(f"✓ Excel 已落盘：{p}")
            if cfg.get("download", {}).get("export_daily_page", True):
                p = export_daily_page(store, cfg, datetime.now().strftime("%Y-%m-%d"))
                log(f"✓ 按天页已落盘：{p}")
        except Exception as e:
            stats["errors"].append(f"导出失败：{type(e).__name__}: {e}")
            log(f"⚠ 导出失败：{e}")

        # ---- v1.4.0 收尾①：本轮全量快照（变更差异一律本地比对，不回线上查、不碰风控）----
        try:
            n_snap = store.snapshot_run(run_id)
            if n_snap:
                log(f"· 本轮快照已存 {n_snap} 条（任意两轮差异可本地算）")
        except Exception as e:                                   # noqa: BLE001
            stats["errors"].append(f"快照失败：{type(e).__name__}: {e}")

        # ---- v1.5.0 收尾④：下架/成交检测（is_active=0）----
        # 本轮 12 子查询并集里消失的房源，连续 N 轮不在才标（避免单次漏抓误标）。
        if mode == "live" and cfg.get("crawl", {}).get("delist_enabled", True):
            try:
                seen = {r.get("property_no") for r in items if r.get("property_no")}
                # R1（v1.9.76）：主轮关闭时 items 恒空 → seen 恒空 → mark_delisted:1588 安全阀直接 return
                #   复活分支成死代码。把「今日日期同步」本轮成功抓到的番号并入 seen，
                #   使"只复活、不判下架"的设计语义真正落地（_scope 仍空 → complete=False → 不判下架）。
                if date_sync_nos:
                    seen |= date_sync_nos
                _consec = int(cfg.get("crawl", {}).get("delist_consecutive_runs", 2) or 2)
                # v1.5.8：作用域 = 本轮**真正成功抓到**的種目（succeeded_subtypes），
                #   不再是"打算覆盖"的全集。失败组（結果未知/検索超时）不进作用域
                #   → 它们的房源不会被误判下架（2026-09-15 中古戸建/中古マンション/新築マンション
                #   连续失败 → 误标 1189 条 is_active=0 的事故根因）。
                #   没配进来的種目（如 売土地）自然也不在 succeeded_subtypes 里 → 同样豁免。
                # complete：试跑 / 没任何组成功时不判下架，只做复活（宁可漏判，绝不误杀）。
                _scope = set(succeeded_subtypes)
                _complete = (not trial) and bool(_scope)
                res = store.mark_delisted(seen, run_id, _consec,
                                          scope_subtypes=_scope, complete=_complete)
                if res["revived"]:
                    log(f"· 复活 {res['revived']} 条（重新出现在结果里 → 恢复在架）")
                if res["delisted"]:
                    log(f"· 下架/成交检测：标 is_active=0 共 {res['delisted']} 条"
                        f"（连续 {_consec} 轮不在本轮 {len(_scope)} 个種目并集内）")
                else:
                    log("· 下架/成交检测：本轮无新增下架（在架房源均在结果并集内）")
            except Exception as e:                                 # noqa: BLE001
                stats["errors"].append(f"下架检测失败：{type(e).__name__}: {e}")
                log(f"⚠ 下架检测失败（不影响本轮）：{e}")

        # v1.5.15：发射「导出与收尾」阶段事件（前端 .phaser 面板实时进度）
        if mode == "live":
            try:
                store.save_crawl_state(run_id, phase="export")
            except Exception:                        # noqa: BLE001
                pass

        # ---- v1.4.0 收尾②：同步状态的两个新分子/分母一起写进 runs ----
        store.finish_run(run_id, stats["scanned"], stats["fetched"],
                         stats["new"], stats["changed"], "ok",
                         online_total=online_total,
                         pdf_saved=stats["pdf_saved"])
        # v1.6.4：本轮结束打印「条件 + 结果」汇总框
        #   v1.8.2 修（R1）：旧版这里引用 `groups` 与 `run_online_total`，
        #     两者**都只存在于 `_live_items` 的局部作用域** → 每轮必然 NameError，
        #     被外层宽 except 吞成「本轮失败（不影响抓取结果，但状态/通知全丢）」。
        #     真值来源：`live_groups_n`（本函数作用域，由 _live_items 返回）
        #               `online_total`（本函数作用域，= _live_items 的 run_online_total）
        #               `st`（demo/live 两条路都会赋值，替代只在该分支存在的 sink.stats）
        #   D1/R2：这段文案同步改成「全期間主轮（数据主力 + 下架判定基线）」，别让后人误砍。
        _use_sc = bool(((cfg or {}).get("search", {}) or {}).get("use_saved_condition", False))
        _live_spec = [
            ("種目组：%d 组（全期間主轮 = 数据主力 + 下架判定基线，防误下架）" % live_groups_n)
            if main_round_on else
            "種目组：全期間主轮**本轮已关闭**（只跑当日日期同步：无详情/PDF，且不判下架）",
            "区域：大阪府・大阪市（%s）" % ("保存条件套用" if _use_sc else "直录"),
        ]
        live_results: list[dict] = []   # v1.6.5：主轮无离散条件，置空避免 NameError 崩溃
        _log_session_summary(log, "本轮结束（全期間主轮：数据主力 + 下架判定基线）", _live_spec,
                             live_results, st, online_total)

        # ---- v1.5.6 收尾④：自动上传（设置里选了「① 自动完成」才跑）----
        #   只在本轮**成功**时才推，失败轮不推（避免把脏数据推上去）。
        #   上传失败绝不影响本轮抓取结果，只记日志。
        try:
            _pub = (cfg.get("publish") or {})
            _mode = str(_pub.get("mode") or "auto").strip()
            if _pub.get("enabled") and _mode == "auto" and mode == "live":
                log("· 上传设置＝自动完成 → 开始增量推送到线上…")
                import sqlite3 as _sq
                from .publisher import publish as _publish
                _con = _sq.connect(str(store.db_path))
                _res = _publish(cfg, _con, mode="incr", log=log)
                if _res.get("skipped"):
                    log("  （本轮没有新增变化，跳过推送）")
                elif _res.get("ok"):
                    log("  ✓ 已推送 %d 条到线上" % (_res.get("sent") or 0))
                else:
                    log("  ⚠ 推送失败（不影响本轮）：" + "; ".join(_res.get("errors") or []))
        except Exception as _e:                                # noqa: BLE001
            log("  ⚠ 自动上传异常（不影响本轮）：%s: %s" % (type(_e).__name__, _e))
        # ---- v1.4.0 收尾③：手动触发的轮次写一条通知，前端轮询到就提示"已跑完" ----
        if trigger in ("manual", "real-download", "test-download"):
            store.add_notification(
                "round_done",
                "手动更新完成：列表 %d 条 / 落库 %d 条 / 新盘 %d / 变更 %d / PDF %d 份"
                % (stats["scanned"], stats["fetched"], stats["new"],
                   stats["changed"], stats["pdf_saved"]))

    except SessionExpired as e:
        # v1.5.2：会话失效 = 「出问题了」，按勇哥的规矩问一句（15 秒）。
        #   选「我已处理，重登后再跑一次」→ 只重试一次（_retry 守卫，防递归）；
        #   15 秒没点 → 按默认「停止本轮」= 以前的行为，下次调度自然再跑。
        act = "stop"
        if _ask_on and not _retry:
            act = _ask_operator(
                store, run_id, "session_expired", {"reason": str(e)},
                [{"action": "retry", "label": "我已处理，重登后再跑一次"},
                 {"action": "stop", "label": "停止本轮（等下次调度）"}],
                "stop", _ask_secs, log)
        if act == "retry":
            try:
                store.finish_run(run_id, 0, 0, 0, 0, "error",
                                 f"会话失效（已按你的选择重试）：{e}")
            except Exception:                                # noqa: BLE001
                pass
            log("· 按你的选择：重登后重跑本轮（只重试这一次）")
            return run_round(store, cfg, trigger, progress_cb, scale, trial,
                             resume=False, _retry=True)
        store.finish_run(run_id, 0, 0, 0, 0, "error", f"会话失效：{e}")
        stats.update({"status": "error", "errors": [f"会话失效：{e}"]})
        log(f"✗ 会话失效，已停机：{e}")

    except Exception as e:
        store.finish_run(run_id, 0, 0, 0, 0, "error", f"{type(e).__name__}: {e}")
        stats.update({"status": "error", "errors": [f"{type(e).__name__}: {e}"]})
        log(f"✗ 本轮失败：{e}")

    return stats


# ============================================================
# DEMO：本地生成样例数据
# ============================================================
def _demo_items(store, cfg, scale: float, log) -> list[dict]:
    from . import demo
    items = demo.generate(store, cfg, scale=scale)
    log(f"· 演示数据源产出 {len(items)} 条（含新盘与改价，用于验证增量与降价识别）")
    return items


# ============================================================
# LIVE：真实 REINS 抓取
# ============================================================
def _grid_pos(style: str):
    """从单元格 style 里抠出 (grid-row, grid-column)。"""
    rm = re.search(r"grid-row-start:\s*(\d+)", style)
    if not rm:
        rm = re.search(r"grid-row:\s*(\d+)", style)
    r = int(rm.group(1)) if rm else None
    cm = re.search(r"grid-column-start:\s*(\d+)", style)
    if not cm:
        cm = re.search(r"grid-column:\s*(\d+)", style)
    c = int(cm.group(1)) if cm else None
    return r, c


def _money_to_yen(text: str):
    """'2,498万円' → 24980000；'9,690円' → 9690；'確認中' → None。"""
    if not text:
        return None
    t = text.replace(",", "").replace(" ", "").replace("　", "")
    if "万" in t:
        num = "".join(ch for ch in t if ch.isdigit() or ch == ".")
        try:
            return int(round(float(num) * 10000))
        except ValueError:
            return None
    num = "".join(ch for ch in t if ch.isdigit())
    return int(num) if num else None


def _area_to_float(text: str):
    if not text:
        return None
    num = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    try:
        return float(num)
    except ValueError:
        return None


# 修繕積立金（月额）的物理合理上限：超过即判定「这一格抓到的是坪単価列」。
# 实测依据：全库 management_fee 最大 91,000 円/月（抓对了的那列可作参照）；
#   而 repair_fund 有 960/1060 条 >= 100万，其中 781 条与 unit_price_tsubo 完全相等。
# 取 200,000 円/月 —— 相对大阪正常水平（数千~数万円）已留 2 倍以上余量。
_FUND_MAX_YEN = 200_000


def _postprocess_list_rec(rec: dict[str, Any]) -> dict[str, Any]:
    """把「原始文本字段」规范化成本地字段（逐格读 / 批量读共用同一套规则）。"""
    out: dict[str, Any] = {}
    for k, text in rec.items():
        if not text:
            continue
        if k in ("price", "unit_price_sqm", "unit_price_tsubo",
                 "management_fee", "repair_fund"):
            out[k] = _money_to_yen(text)
        elif k == "exclusive_area":
            out[k] = _area_to_float(text)
        elif k == "access":
            out["line_station"] = (out.get("line_station", "") + " " + text).strip()
        else:
            out[k] = text
    # v1.7.3 根治（勇哥 2026-09-16 反馈：修繕積立金 10,346,000 円 荒谬）；
    #   替代 v1.6 那条只覆盖「売地」的 BUG-1 补丁。
    # 教训：v1.6 补丁按**種目**判断（只处理 売地），并断言「マンション 类 (3,10) 是真正的
    #   修繕積立金」—— 已被真实数据证伪：全库 1060 条 repair_fund 中 781 条(73.7%)
    #   与 unit_price_tsubo 完全相等、960 条(90.6%) >= 100万。换个種目就复发。
    # 改为**与種目无关的值域判断**：修繕積立金是月额，正常 0~数万円；超过
    #   _FUND_MAX_YEN 即说明这一格抓到的是坪単価列 → 迁到 unit_price_tsubo（若空）
    #   或直接丢弃，绝不写进 repair_fund。
    rf = out.get("repair_fund")
    if rf is not None and isinstance(rf, (int, float)) and rf >= _FUND_MAX_YEN:
        out.pop("repair_fund", None)
        if out.get("unit_price_tsubo") is None:
            out["unit_price_tsubo"] = rf
    return out


def _parse_list_row(row) -> dict[str, Any]:
    """把一个结果网格行解析成本地表（按网格坐标映射）。

    v1.4.1 起列表阶段已改用 `_bulk_list_rows`（一次 evaluate 取整页）；本函数保留给
    任何「手上已有单个行对象」的场景（例如旧链路），规则与批量读完全一致。
    """
    rec: dict[str, Any] = {}
    items = row.locator("div.p-table-body-item")
    for i in range(items.count()):
        it = items.nth(i)
        try:
            style = it.get_attribute("style") or ""
            r, c = _grid_pos(style)
        except Exception:
            continue
        if r is None or c is None:
            continue
        field = LIST_FIELD_MAP.get((r, c))
        if not field:
            continue
        try:
            text = (it.inner_text(timeout=800) or "").strip()
        except Exception:
            continue
        if not text:
            continue
        rec[field] = text
    # v1.5.3：画/図/所 三个图标（与批量读口径一致，保证两条路结果相同）
    # ⚠ 必须在 _postprocess_list_rec **之后**再写：那个函数会跳过空值（`if not text: continue`），
    #   标志位的 0 会被当成"空"丢掉 → 两条路一个写 0、一个不写，口径不一致。
    out = _postprocess_list_rec(rec)
    for cls, key in (("p-icon-type-ga", "has_photo"),
                     ("p-icon-type-zu", "has_floorplan"),
                     ("p-icon-type-sho", "has_map")):
        try:
            out[key] = 1 if row.locator("div." + cls).count() else 0
        except Exception:
            out[key] = 0
    return out


# ---------------------------------------------------------------------------
# v1.4.1 性能：**一次 evaluate 取回整页所有行**（替代「每格一次 get_attribute/inner_text」）
#
# 实测（真机落盘的结果页 data/probe/result_page.html，58 行）：
#   · 逐格读：客户端→浏览器往返 2842 次 / 6.78s（117 ms/行）
#   · 批量读：1 次往返 / 0.037s（1 ms/行）→ **185×**
#   两者抽取结果**逐字段完全一致**（_diag_listread.py 有一致性断言）。
#
# 【为什么不算加风控风险】这只是把**本地浏览器里已经渲染好的 DOM**读一遍，
# 不产生任何新的网络请求；REINS 看到的请求数完全不变（仍是「每次翻页 1 个请求」）。
# ---------------------------------------------------------------------------
_LIST_EXTRACT_JS = """
([rowsSel, triples]) => {
  const map = new Map();
  for (let i = 0; i < triples.length; i++) map.set(triples[i][0] + "," + triples[i][1], triples[i][2]);
  const out = [];
  const rows = document.querySelectorAll(rowsSel);
  for (const row of rows) {
    const rec = {};
    const cells = row.querySelectorAll("div.p-table-body-item");
    for (const it of cells) {
      const st = it.getAttribute("style") || "";
      let m = /grid-row-start:\\s*(\\d+)/.exec(st) || /grid-row:\\s*(\\d+)/.exec(st);
      const r = m ? parseInt(m[1], 10) : null;
      m = /grid-column-start:\\s*(\\d+)/.exec(st) || /grid-column:\\s*(\\d+)/.exec(st);
      const c = m ? parseInt(m[1], 10) : null;
      if (r === null || c === null) continue;
      const f = map.get(r + "," + c);
      if (!f) continue;
      const t = (it.innerText || "").trim();
      if (t) rec[f] = t;
    }
    // 详情直链：只认「文字含 詳細」的锚点；退而求其次取含 GBK 的锚点。
    // 绝不回落到行内任意首个 <a>（那是中介公司链接 href="#" —— 无意义的假直链）。
    let href = "";
    const anchors = row.querySelectorAll("a[href]");
    for (const a of anchors) {
      const h = a.getAttribute("href") || "";
      if (!h || h === "#") continue;
      const txt = a.innerText || a.textContent || "";
      if (txt.indexOf("詳細") >= 0) { href = h; break; }
      if (!href && h.indexOf("GBK") >= 0) href = h;
    }
    // v1.5.3：右侧三个图标（画/図/所）—— 一次 evaluate 顺手取回，零额外往返。
    const icons = { has_photo: 0, has_floorplan: 0, has_map: 0 };
    for (const el of row.querySelectorAll("div.p-icon")) {
      const cn = el.className || "";
      if (cn.indexOf("p-icon-type-ga")  >= 0) icons.has_photo = 1;
      if (cn.indexOf("p-icon-type-zu")  >= 0) icons.has_floorplan = 1;
      if (cn.indexOf("p-icon-type-sho") >= 0) icons.has_map = 1;
    }
    out.push({ f: rec, h: href, ic: icons });
  }
  return out;
}
"""


# v1.5.3：REINS 列表行右侧的三个小图标（真机快照坐实类名）——
#   p-icon-type-ga  = 画（照片/画像）
#   p-icon-type-zu  = 図（間取図/図面）
#   p-icon-type-sho = 所（所在図/周辺地図）
# 这三个标记**列表页就能读到、永远准确**，是"这套房有没有照片"的可靠答案；
# 详情页的图片张数才是"有几张"。详见 docs/PRD/10 §15。
_LIST_ICON_JS = """(rowsSel) => {
  const out = [];
  for (const row of document.querySelectorAll(rowsSel)) {
    const ic = { has_photo: 0, has_floorplan: 0, has_map: 0 };
    for (const el of row.querySelectorAll("div.p-icon")) {
      const cn = el.className || "";
      if (cn.indexOf("p-icon-type-ga")  >= 0) ic.has_photo = 1;
      if (cn.indexOf("p-icon-type-zu")  >= 0) ic.has_floorplan = 1;
      if (cn.indexOf("p-icon-type-sho") >= 0) ic.has_map = 1;
    }
    out.push(ic);
  }
  return out;
}"""


def _abs_reins_url(h: str) -> str:
    """把 REINS 相对链接补成绝对地址；空/"#" 一律返回空串（不伪造直链）。"""
    h = str(h or "").strip()
    if not h or h == "#":
        return ""
    if h.startswith("//"):
        return "https:" + h
    if h.startswith("/"):
        return "https://system.reins.jp" + h
    return h


def _bulk_list_rows(page, rows_sel: str) -> list[dict[str, Any]]:
    """一次往返取回整页所有行 → [{"rec": {...}, "href": "详情直链或空"}]。

    v1.5.3：rec 里额外带上 `has_photo / has_floorplan / has_map`（列表行 画/図/所 三个图标）。
    """
    triples = [[r, c, f] for (r, c), f in LIST_FIELD_MAP.items()]
    try:
        raw = page.evaluate(_LIST_EXTRACT_JS, [rows_sel, triples]) or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        rec = _postprocess_list_rec(item.get("f") or {})
        rec.update(item.get("ic") or {})     # v1.5.3：画/図/所
        out.append({"rec": rec, "href": _abs_reins_url(item.get("h") or "")})
    return out


def _expand_search_panel(page, sel) -> None:
    try:
        page.locator(sel["collapse_toggle"]).first.click(timeout=8000)
        page.wait_for_timeout(1000)
    except Exception:
        pass


def _dismiss_modal(page) -> bool:
    """読込/検索 后常弹出确认 modal（如「保存条件を読み込みますか？」「検索結果が500件を超えています」）。

    返回 True 表示确实关掉了一个 modal。只点确认类按钮（はい/OK/実行/読込/確定/検索/登録/btn-primary），
    绝不点 いいえ/キャンセル/閉じる。
    v1.5.10：count()==0 时不等待、立即返回（不拖慢没有 modal 的常规路径）；
    检测到 modal 后先等它真正 visible（动画/延迟渲染，最多 4 秒）再点，避免抢点在 hidden 态上。
    """
    try:
        modal = page.locator(
            "div.modal.show, .modal.show, div[role='dialog'].show, "
            "div[role='dialog'][aria-modal='true'], [aria-modal='true'].show").first
        if modal.count() == 0:
            return False
        try:
            modal.wait_for(state="visible", timeout=4000)
        except Exception:
            pass
        btn = (modal.locator("button:has-text('はい'), button:has-text('OK'), "
                             "button:has-text('実行'), button:has-text('読込'), "
                             "button:has-text('確定'), button:has-text('検索'), "
                             "button:has-text('登録'), button.btn-primary").first)
        if btn.count():
            btn.click(timeout=6000)
            page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    return False


def _load_saved_condition(page, sel, cond_value: str, log) -> None:
    """选保存条件 → 点 読込（会弹确认 modal → 自动确认）→ 把条件套进表单。

    v1.5.7 修：REINS の検索条件页是 **Vue 前端渲染**，冷启动 / 慢网时控件要十几秒才出现。
    原来只等 5 秒，等不到就「继续手动套用」——结果后面 物件種目 / 所有権 / 登録・変更年月日
    全部 NO_TITLE，最后连「検索」按钮都找不到，整组落库 0 条（run64 就这样挂掉了 4/6 组）。
    现在：先展开检索面板 → 等下拉框真正可见（25 秒）→ 再操作。
    """
    _expand_search_panel(page, sel)
    sc = page.locator(sel["saved_condition_select"]).first
    sc.wait_for(state="visible", timeout=25000)
    sc.scroll_into_view_if_needed(timeout=25000)
    sc.select_option(value=cond_value, timeout=15000)
    page.wait_for_timeout(500)
    page.locator(sel["load_button"]).first.click(timeout=8000)
    page.wait_for_timeout(1500)
    if _dismiss_modal(page):
        log(f"· 已确认読込弹窗，套用保存条件 {cond_value}")
    else:
        log(f"· 已套用保存条件 {cond_value}（読込）")
    page.wait_for_timeout(1500)


def _needs_detail(existing) -> bool:
    """这套房需不需要（重新）抓详情页。

    三条规则，满足任一就要抓：
      ① 库里根本没有这套房                      → 抓
      ② 库里有，但 detail_json 是空的            → 抓
      ③ 库里有、也有详情，但**缺平台日期**且还没核验过 → 抓一次（v1.5.6）

    【为什么要有第 ③ 条】本机有 229 条缺「平台登録/変更日」，其中 205 条
    是**老版本程序抓的详情**（那时还没有 reg_date_iso / chg_date_iso 这两列）。
    旧规则「有 detail_json 就不重抓」会让这 205 条**永远补不上**，
    于是它们按平台日期永远筛不出来（勇哥查历史日期就查不到）。
    现在允许补抓一次；抓完打上 `pdate_checked=1` 标记，以后不再重复抓，
    所以只是一次性成本，不会每轮都浪费。
    """
    if existing is None:
        return True
    try:
        if not existing["detail_json"]:
            return True
    except Exception:                                        # noqa: BLE001
        return True
    # ③ 缺平台日期 且 还没核验过 → 补抓一次
    try:
        checked = existing["pdate_checked"]
    except Exception:                                        # noqa: BLE001
        checked = None
    if checked:
        return False
    try:
        reg = existing["reg_date_iso"]
        chg = existing["chg_date_iso"]
    except Exception:                                        # noqa: BLE001
        return False
    return not (reg or chg)


def _read_zero_note(page) -> str:
    """v1.9.5：识别 REINS 的「検索結果が0件です」无结果页，返回原文（供日志），否则空串。

    【实证 · 2026-09-18 勇哥真机 DOM（PRD-19 R4 真根因）】
        <div class="p-note mt-0 p-note-danger"> 検索結果が0件です。
        検索条件を再入力してください。</div>
      · 该页 **没有** `div.text-dark.ml-3`（计数区）→ 旧代码一路空等到 60–90s 上界；
      · 该页 **也没有** `div.p-table-body-row`（结果表）→「本组收集 0 条」是**对**的，
        不是漏抓！タウン 4 组「卡 65–85s + 0 条」= REINS 当日确実 0 件，却被误记「未知」。
    只认「告示文案里真含 0件」的那条 note；500 件确认框 / 维护通知一律不认。
    """
    for _sel in ("div.p-note-danger", "div.p-note"):
        try:
            _loc = page.locator(_sel)
            for _i in range(min(_loc.count(), 5)):
                _t = (_loc.nth(_i).inner_text() or "").replace("\n", " ").strip()
                # ⚠ 绝不能用 `"0件" in _t`：`500件` / `30件` / `10件` 里**都含子串「0件」**，
                #   而「検索結果が500件を超えています」正是**有 500+ 条**的事前確認框
                #   → 会被误判成 0 件、把 中古戸建/中古マンション 的条数清零（mock 单测 F 捕获）。
                #   故判「0 件」且**前一位不是数字/逗号**；半角 0 与全角 ０ 都认。
                if re.search(r"(?<![\d,，０-９])[0０]\s*件", _t):
                    return _t[:80]
        except Exception:                                    # noqa: BLE001
            continue
    return ""


def _read_tab_count(page) -> int | None:
    """v1.9.5：从结果页 tab 见出し「売一戸建(9件)」读**総件数** —— 種目无关的第 2 计数源。

    【实证】同一批真机 DOM 里就有这个数，且与计数区同源：
        0 件页：`<a class="nav-link active">売一戸建(0件)</a>`
        有结果页：`<a class="nav-link active">売一戸建(9件)</a>`（同页计数区 = `1～9件 ／ 9件`）
    【为什么求和】单種別检索只有 1 个 tab；万一 REINS 把检索外的種別也渲成 (0件)，
    求和 = 0+0+…+N = N，两种解释下都等于该種別总数 —— 所以求和是稳的。
    取每个 tab 里**最后**一个 `N件`（「a～b件 ／ N件」类文案取总数）。
    一个都没匹配到 → None（≠0，绝不把「读不到」当「0 件」）。
    """
    try:
        _loc = page.locator("ul.card-header-tabs a.nav-link")
        _n = _loc.count()
    except Exception:                                        # noqa: BLE001
        return None
    _tot, _hit = 0, False
    for _i in range(min(_n, 10)):
        try:
            _t = (_loc.nth(_i).inner_text() or "").replace(" ", "")
        except Exception:                                    # noqa: BLE001
            continue
        _nums = re.findall(r"([\d,]+)\s*件", _t)
        if _nums:
            _tot += int(_nums[-1].replace(",", ""))
            _hit = True
    return _tot if _hit else None


def _read_total(page, sel, log=None, timeout_s: float | None = None) -> str:
    """读「结果 N 件」那段文案 —— **等它真的出现再读**。

    【为什么要等】REINS 结果页是服务端渲染，房源越多渲染越慢。
    老代码点完「検索」固定睡 2.5 秒就去读 `div.text-dark.ml-3`，
    结果**大房型（中古戸建 387 条 / 中古マンション 640 条）还没渲染出来**，
    读回空串 → 日志打印「结果 未知」→ 线上覆盖的分母记不上
    → 概览页那个覆盖率百分比偏高、不可信（今天显示 98.9% 就是这么来的）。
    这里改成轮询等待，最多等 timeout_s 秒；期间页面在动就继续等。

    v1.9.5（PRD-19 R4 真根因修正）：**0 件页既没有计数区、也没有结果表**，
    所以「等不到计数区」≠「渲染慢」≠「漏抓」。改三级判定：
        ① 每轮先看「検索結果が0件です」告示 → 命中即刻定 `0件`（<1s，不再空等）；
        ② 主选择器 result_total（div.text-dark.ml-3）→ 戸建/マンション 走这条（实测 <2s）；
        ③ 宽限 4s 后仍无 ②：读 tab 见出し件数（種目无关）→「N件（タブ見出し）」；
           8s 后再退到首頁行数 →「N件（首頁行数・総数不明）」；最后才记「未知」。
    保留 4s 宽限的意义：② 在戸建/マンション 上「<2s 即返」的既有行为不被兜底拖慢。
    （v1.9.4 只把上限 60–90s 砍到 18s，仍属止血；v1.9.5 才把「0 件 = 成功」判对。）
    """
    say = log or (lambda *_a, **_k: None)
    if timeout_s is None:
        # v1.9.4（PRD-19 R4 止血）：实测戸建/マンション 结果页 <2s 即出计数区；
        #  タウン 类整组 65–85s 超时（真因见 v1.9.5：0 件页**没有**计数区，纯空等，
        #  60–90s 是在追 phantom）。18s 只作最终上界，正常 ①② 早就返回了。
        timeout_s = 18.0
    locator = sel.get("result_total") or "div.text-dark.ml-3"
    _t0 = time.time()
    deadline = _t0 + max(1.0, timeout_s)
    _grace_tab = 4.0        # 4s 后才用 tab 件数兜底（不抢常用路径）
    _grace_row = 8.0        # 8s 后才用行数兜底（更弱的口径，宁可多等一会）
    while time.time() < deadline:
        # ① v1.9.5：明确 0 件页 → 立即定 0，绝不空等（实证 DOM 见 _read_zero_note）
        _z = _read_zero_note(page)
        if _z:
            say("  · 結果 0 件（REINS 明示「" + _z + "」）→ 本組確実 0 件，非漏抓")
            return "0件"
        try:
            txt = _safe_text(page, locator).strip()
        except Exception:                                    # noqa: BLE001
            txt = ""
        if txt:
            return txt
        _el = time.time() - _t0
        # ③ 宽限期后仍无计数区 → 種目无关的兜底，不再死等
        if _el >= _grace_tab:
            _tab = _read_tab_count(page)
            if _tab is not None:
                if _tab == 0:
                    say("  · 結果 0 件（タブ見出し 0 件）→ 本組確実 0 件，非漏抓")
                    return "0件"
                say("  · 計数区未検出（%.0fs）；以タブ見出し %d 件 記総数（種目非依存の第2計数源）"
                    % (_el, _tab))
                return "%d件（タブ見出し）" % _tab
            if _el >= _grace_row:
                try:
                    _nr = page.locator(
                        sel.get("result_rows") or "div.p-table-body-row").count()
                except Exception:                            # noqa: BLE001
                    _nr = 0
                if _nr > 0:
                    # 有结果表但计数区缺失：以首頁行数估分母，不漏抓
                    # （翻页循环仍按 result_rows 抓全行）。首頁行数≠総数时仅分母偏小，数据完整。
                    say("  · 計数区未検出（%.0fs）；以首頁 %d 行 估算総数" % (_el, _nr))
                    return "%d件（首頁行数・総数不明）" % _nr
        try:
            page.wait_for_timeout(400)
        except Exception:                                    # noqa: BLE001
            return ""
    # 不到 0 件告示 / 不到计数区 / tab 与行数都读不到 → 记「未知」但不久等
    # （绝不臆断 0，避免把「结构不同但有结果」误判成 0 件漏抓）
    say("  · 结果条数区域迟迟没出现（%s 秒），且无 0件告示 / 无 tab 件数 / 首頁0行 → 记「未知」"
        % timeout_s)
    return ""


def _parse_total(txt) -> int | None:
    """从 REINS 结果条数文案里取「线上总数」。

    实测文案形如 `1～50件 ／ 138件`（前面是当前页范围、后面是总数），也有 `84件`。
    取**最后一个** `N件` 的数字；取不到返回 None（绝不猜）。
    """
    if not txt:
        return None
    nums = re.findall(r"([\d,]+)\s*件", str(txt))
    if not nums:
        nums = re.findall(r"([\d,]+)", str(txt))
    if not nums:
        return None
    try:
        return int(nums[-1].replace(",", ""))
    except Exception:
        return None


def _mask_webdriver(ctx) -> None:
    """风控加固（v1.4.0）：把 navigator.webdriver 抹成 undefined。

    REINS 若做自动化检测，navigator.webdriver 是最直白的一条指纹。用 add_init_script
    在每个页面/iframe 的**文档创建前**执行，比 goto 之后再 evaluate 更彻底（后者来不及）。
    只改这一个属性，不影响任何正常交互；失败也不阻断（best-effort）。
    """
    try:
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    except Exception:
        pass


def _live_items(store, cfg, log, trial: bool = False, sink=None,
                resume: bool = False) -> tuple[list, int]:
    """按「種目分组」翻页抓取（真实站点）。

    返回 (collected, online_total)：online_total 是本轮各组「REINS 结果 N 件」之和，
    作为「列表层分母」写进 runs.online_total（v1.4.0 同步状态用）。

    v1.2.3 起**不再按 REINS 保存条件遍历**——更新固定覆盖
        一戸建 前 2 个 + 公寓 前 4 个 = 6 个種目（config → `search.update_groups`）。
    每组先用保存条件带出「大阪府/大阪市」地区基线，再覆盖物件種別/種目。

    【根因·非显然】旧版是按条件 01（売土地+売一戸建）/ 02（売マンション）顺序跑，
    条件 01 排在前，一旦撞上单轮时长上限就整轮 `break` → 排在后面的公寓**永远轮不到**，
    表现就是用户报的"更新只下一户建、公寓一个没下"。

    时长上限 `crawl.max_minutes_per_group`（分钟，0=不限）**按组计算**：到点只收尾本组、
    继续下一组，保证 6 个種目都有机会被覆盖（旧键 `max_minutes_per_run` 仍作兜底读取）。

    sink 非空时（v1.2.2 起由 run_round 自动传入）：每抓到一条就交给 sink，
    攒够一批**立刻落库**（主档 + PDF），不必等整轮结束。

    resume=True（v1.5.2 断点续传）：从 crawl_state 里记下的房型接着跑，前面已跑完的房型跳过。
    粒度只到"房型"——详情页级别不用续，因为 v1.4 起就是边抓边落库，
    已抓过的房源下一轮自动跳过详情，中断最多丢当前这一条。
    """
    sel = cfg["selectors"]
    cr = cfg.get("crawl", {})
    # v1.5.2：断点记录 + 询问窗口
    _ask = cr.get("operator_ask", {}) or {}
    _ask_on = bool(_ask.get("enabled", True))
    _ask_secs = int(_ask.get("timeout_s", 15) or 15)
    _inline_fail_max = int(_ask.get("inline_fail_threshold", 5) or 5)
    _run_id = getattr(sink, "run_id", None) if sink is not None else None
    delay = [float(x) for x in cr.get("delay_range_seconds", [20, 40])]
    # v1.4.1：列表阶段不再「逐行 sleep 0.5–2.0s」（行内不产生任何请求，纯浪费），
    # 改为**翻页停顿** —— 那才是 REINS 唯一看得见的动作（每页 1 个请求）。
    _pp = cr.get("list_page_pause_seconds") or cr.get("list_pause_seconds") or [8, 20]
    page_pause = [float(x) for x in _pp]
    _dl = cr.get("delay_long_range_seconds")
    delay_long = [float(x) for x in _dl] if _dl else None   # 偶尔的"仔细看"长停顿（更像人）
    delay_long_prob = float(cr.get("delay_long_prob", 0.0) or 0.0)
    # v1.5.0：点 詳細 的统一节奏（行内 / 阶段2 共用），2~4s 随机（真机推翻旧 20s）。
    detail_gap = [float(x) for x in cr.get("detail_gap_seconds",
                                           cr.get("delay_range_seconds", [2, 4]))]
    # v1.5.0：12 子查询之间每组停顿。
    subquery_interval = [float(x) for x in cr.get("subquery_interval_seconds", [10, 20])]
    # v1.5.0：列表页行内点 詳細 补「无直链」房源详情（试跑模式不触发，先验证链路）。
    inline_detail = bool(cr.get("inline_detail", True)) and not trial
    max_items = int(cr.get("max_items_per_run", 200))
    max_pages = int(cr.get("max_pages", 7))
    groups = _update_groups(cfg)
    max_minutes = float(cr.get("max_minutes_per_group",
                               cr.get("max_minutes_per_run", 0)) or 0)

    if trial:
        max_items = min(max_items, 3)
        max_pages = 1
        delay = [1.0, 2.0]
        groups = groups[:1]
        log(f"· 试跑模式：1 个種目组 / 最多 {max_items} 条 / 1 页（最小请求量验证链路）")

    all_subs = [s for g in groups for s in g["subtypes"]]
    log("· 本次更新固定覆盖 " + str(len(groups)) + " 组 / " + str(len(all_subs))
        + " 个物件種目：" + "、".join(all_subs))

    # ---- v1.5.2 断点续传：上一轮被打断在哪一组，就从那一组接着跑 ----
    _resume_from = 0
    if resume and not trial:
        try:
            _ir = store.last_interrupted_run()
            _st = store.load_crawl_state(_ir["id"]) if _ir else None
            if _st and int(_st.get("group_index") or 0) > 0:
                _resume_from = int(_st["group_index"])
                log("· 断点续传：上一轮 run%s 在第 %d/%d 组（%s）被打断 → 前面 %d 组跳过，从那里接着跑"
                    % (_ir["id"], _resume_from, len(groups), _st.get("group_label") or "",
                       _resume_from - 1))
            else:
                log("· 断点续传：没找到可用断点，按正常跑完整 %d 组" % len(groups))
        except Exception as _e:                              # noqa: BLE001
            log("· 断点续传跳过（读断点失败：%s）→ 按正常跑完整轮" % _e)
    _inline_fail = [0]      # 连续行内点詳細 失败计数（触发"问人"用）
    _skip_group = [False]   # 「跳过本房型」被选中 → 跳出本组的翻页循环
    # ⚠ P0（2026-09-14 run52 整轮崩在这里）：这个计数器**必须**在函数开头就建好。
    #   它有三处使用者，其中两处在 `if pending:` 之外（阶段1 行内失败、阶段2 收尾汇总）。
    #   旧代码只在「阶段2 有待处理条目」时才 `n_skip_no_href = [0]`，
    #   于是"本轮没有需要补详情的房源"时收尾那行 `if n_skip_no_href[0]:`
    #   直接 UnboundLocalError → 整轮被记 error、scanned=0（run52 就是这样崩的）。
    n_skip_no_href = [0]    # v1.4.1：无直链而跳过的条数（原来静默跳过、日志看不出）

    auth = Auth(cfg, cfgmod.paths(cfg)["session"])
    collected: list[dict] = []
    run_online_total = 0   # v1.4.0：本轮各组线上总数之和（列表层分母）

    with _sync_playwright() as p:
        browser = auth.launch(p, headless=cfg["browser"].get("headless", False))
        # 统一入口：会话失效会自动用本地账号重登一次（v1.2.1），不必每轮人工介入
        ctx, page = auth.open_authed_page(browser, log=log)
        _mask_webdriver(ctx)          # v1.4.0：抹掉自动化指纹（webdriver）
        log("✓ 会话有效，已进入検索条件入力页")

        # ---- v1.3.0 运行时参数（后台 PDF / 灵活匹配 / 页内小幅乱序）----
        root_dir = Path(cfgmod.paths(cfg)["root"])
        want_pdf = bool(cfg.get("download", {}).get("pdf", True))
        skip_prob = float(cr.get("skip_prob", 0.12) or 0.0)
        worker = None
        if want_pdf and cr.get("pdf_background", True):
            try:
                ua = page.evaluate("navigator.userAgent") or ""
                ck = "; ".join(("%s=%s" % (c.get("name"), c.get("value")))
                               for c in ctx.cookies() if c.get("name"))
                n_inflight = max(1, int(cr.get("pdf_max_inflight", 2)))
                worker = _PdfWorker(root_dir / "attachments", ck, ua, log,
                                    max_inflight=n_inflight)
                log("· PDF 后台下载已开启（最大在途 %d；主线程继续抓详情，不再干等）" % n_inflight)
            except Exception as e:
                log("· PDF 后台下载启动失败 → 退回同步下载：%s: %s" % (type(e).__name__, e))
                worker = None

        # v1.5.0：子查询去重。REINS 的検索条件里**没有**独立的「変更」轴 ⇒「新增」与「変更」
        # 两轴的检索条件逐字节相同，真跑 12 次 = 同样 6 个房型各检索两遍（多一倍 REINS 请求、
        # 多一倍轮次时长，且不增任何信息量——「新增/变更」本来就是本地 changes 引擎判的）。
        # 用户 09-14 说"拆成 12 次"的本意是"拆细到单个房型才不撞 500 条上限"，这一层由
        # split_new_changed 已经做到了。故这里把重复的「変更」轴直接复用、不再请求。
        # 勇哥 2026-09-14 拍板：默认**真跑满 12 次**（reuse_axis_result: false）；
        # 想省一半时间/请求就把它改 true（重复轴不再请求 REINS）。
        _searched = {}          # (kind, subtypes) → 本轮已真跑过的轴
        reuse_axis = bool(cr.get("reuse_axis_result", False))
        succeeded_subtypes = set()   # v1.5.8：本轮**真正成功取到结果条数**的種目（失败组不进，下架判定豁免）

        for gi, g in enumerate(groups, 1):
            kind, subs = g["kind"], g["subtypes"]
            axis = g.get("axis") or ""
            cond_base = len(collected)   # 每组各自一份配额，别互相挤（旧版共用 200 条）
            g_t0 = time.monotonic()      # 时长上限**按组**算，避免前面的组吃光时间
            g_timed_out = False
            _skip_group[0] = False   # v1.5.2：每组重置「跳过本房型」标记
            label = kind + " × " + "·".join(subs) + (("（" + axis + "）") if axis else "")
            _key = (kind, tuple(subs))
            if reuse_axis and _key in _searched and _searched[_key] != axis:
                log("—— 第 " + str(gi) + "/" + str(len(groups)) + " 组：" + label
                    + " —— 与「" + _searched[_key] + "」轴检索条件完全相同"
                    "（REINS 无「変更」筛选）→ 不重复请求，直接沿用本轮结果")
                continue
            _searched[_key] = axis
            if _resume_from and gi < _resume_from:
                # v1.5.2 断点续传：这一组上一轮已经跑完，跳过（不发请求）
                continue
            # v1.5.2 断点记录：进组就记一笔，进程被关/崩溃后知道断在哪
            if _run_id is not None:
                try:
                    store.save_crawl_state(_run_id, phase="list", group_index=gi,
                                           group_label=label, axis=axis, page=1)
                except Exception:                            # noqa: BLE001
                    pass
            # v1.4.1 诊断计数：让真机日志能一次性回答「读表花了多久 / 详情直链命中率」
            read_ms = [0]      # 本组「整页取回」累计毫秒
            n_need = [0]       # 本组需要抓详情的条数
            n_href_ok = [0]    # 其中拿到直链的条数
            n_href_bare = [0]  # 直链疑似「通用详情页」（无 ? 参数）的条数 —— 需关注
            n_href_miss = [0]  # 完全没拿到直链的条数（阶段2 将只能走点击退路）
            n_pdf_skip = [0]   # v1.5.13：本组无「図」图标、跳过 PDF 下载的条数
            try:
                if gi > 1:   # 第 1 组的页面已由 open_authed_page 打开，不重复导航
                    page.goto(cfg["site"]["search_url"],
                              wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(800)
                _check_maintenance(page, log)
                _dismiss_modal(page)        # v1.5.9：进组先清掉上一组残留的确认 modal
                #   （如「〇〇件ありますが続けますか」）。否则残留 modal 会拦截本组 読込/検索
                #   点击 → 整组 0 条（ekUCcb 里 新築マンション 即因此被 modal 拦截 検索 30s 超时）。
                _expand_search_panel(page, sel)
                log("—— 第 " + str(gi) + "/" + str(len(groups)) + " 组：" + label + " ——")
                # v1.6.3：先打条件横幅，肉眼确认区域/日期轴/房屋类型对不对
                _axis = (axis or "").strip()
                _log_search_banner(log, seq=gi, total=len(groups), kind=kind, subtypes=subs,
                                   cfg=cfg,
                                   date_fields=[_axis + "年月日"] if _axis else [],
                                   date_range=("全期間（不限日期）" if not _axis else _axis + "轴单搜"),
                                   round_tag="全期間主轮(数据主力+下架基线)")
                # 保存条件只用来带出地区；物件種別/種目由此处覆盖（v1.2.3）
                try:
                    _apply_manual_conditions(page, sel,
                                             {"kind": kind, "subtypes": subs}, log, cfg)
                except _GeoFailed as _ge:
                    log("⚠ 跳过本组（地区基线直录失败，已重试）：" + str(_ge)[:160])
                    continue
                _dismiss_modal(page)        # 検索前也可能有确认弹窗

                page.locator(sel["search_button"]).first.click()
                page.wait_for_load_state("networkidle", timeout=40000)
                page.wait_for_timeout(1500)
                # v1.5.10：点「検索」后若弹「検索結果が500件を超えています」事前確認框，
                # 必须点「はい/続行」确认，否则结果页不渲染 → 整组记「未知」甚至 0 条。
                # 用户明确要求：遇到此确认框要真去点，不能跳过/直接跳回检索页。
                _hit500 = _dismiss_modal(page)
                if _hit500:
                    log("· 已确认「検索結果が500件」事前確認框，继续显示结果")
                    page.wait_for_timeout(1500)   # 确认后结果页才真正渲染
                total_txt = _read_total(page, sel, log=log)
                log(f"· {label} → 线上报告 {total_txt or '未知'} 件")
                # v1.9.81 §17⑧：按「每次检索」留痕（读不到记 NULL，绝不记 0 ——「读不到」≠「0 件」）
                try:
                    _n = _parse_total(total_txt) if total_txt else None
                    store.log_search(kind="list", cond=label, result_count=_n,
                                     hit_limit=bool(_hit500), elapsed_s=0.0,
                                     run_id=(sink.run_id if sink is not None else None))
                except Exception as _e:
                    log(f"   · 检索日志写入失败（不影响抓取）：{type(_e).__name__}: {_e}")
                # v1.5.8：只有真正取到结果条数的组才算"抓过"，才进下架判定的作用域；
                # 「結果 未知」/ 検索超时 等失败组一律不进 → 不会被误判下架。
                # v1.9.5 追加：**确认 0 件**（0件告示 / tab 0 件）的组也不进 ——
                #   「0 条」不能证明任何房源下架（原教旨「缺席≠下架」）；若放进去，
                #   mark_delisted 会把库里该種目在架房源连续 N 轮标 is_active=0
                #   （2026-09-15 误标 1189 条事故同型）。宁可漏判，绝不误杀。
                if total_txt and _parse_total(total_txt) != 0:
                    succeeded_subtypes.update(g["subtypes"])
                # 线上覆盖（v1.2.7）：把这组的「線上総数」落库，回答「我下全了没有」
                try:
                    _on = _parse_total(total_txt)
                    if _on:
                        run_online_total += int(_on)      # v1.4.0：列表层分母
                        if sink is not None:
                            store.add_online_stat(sink.run_id, kind, label, subs, _on)
                            log(f"   · 线上覆盖已记录：{_on} 件")
                except Exception as _e:
                    log(f"   · 线上覆盖记录失败（不影响抓取）：{type(_e).__name__}: {_e}")

                for pg in range(1, max_pages + 1):
                    # v1.5.2 断点记录：翻页也记一笔（粒度到"房型+页码"就够，
                    # 更细的"第几条"没必要——边抓边落库已保证中断最多丢当前一条）
                    if _run_id is not None:
                        try:
                            store.save_crawl_state(_run_id, page=pg,
                                                   scanned=len(collected) - cond_base)
                        except Exception:                    # noqa: BLE001
                            pass
                    rows = page.locator(sel["result_rows"])
                    n = rows.count()
                    # v1.4.1：**整页一次 evaluate 取回**（原来逐格读：58 行 = 2842 次往返 / 6.78s；
                    # 现在 1 次往返 / 0.04s）。这只读本地已渲染好的 DOM，不额外请求 REINS。
                    _t_read = time.perf_counter()
                    page_rows = _bulk_list_rows(page, sel["result_rows"])
                    read_ms[0] += int((time.perf_counter() - _t_read) * 1000)
                    if not page_rows:                 # 兜底：批量读失败就退回逐格读，绝不漏抓
                        page_rows = [{"rec": _parse_list_row(rows.nth(i)), "href": ""}
                                     for i in range(n)]
                    # v1.3.0 · 真人不会严格逐行点到底。
                    # 用户原话："这个页面必须是全存储，但存储顺序可以有一些在前、有一些在后，
                    # 不是完全按列表顺序。" ⇒ 本页**一条都不少**，只把 1~2 条挪到本页末尾再处理
                    # （纯处理顺序；数据已一次取全，所以不会有漏抓）。
                    order = list(range(len(page_rows)))
                    if len(order) >= 4 and random.random() < skip_prob:
                        for _ in range(random.randint(1, 2)):
                            if len(order) > 1:
                                order.append(order.pop(random.randrange(1, len(order))))
                    for i in order:
                        if len(collected) - cond_base >= max_items:
                            break
                        pr = page_rows[i]
                        rec = pr["rec"]
                        no = rec.get("property_no")
                        if not no:
                            continue
                        existing = store.get_property(no)
                        # 灵活匹配（v1.3.0）：详情数据 / PDF 文件**分别**判断要不要抓
                        need_detail = _needs_detail(existing)
                        # v1.8.3（冲突3 勇哥拍板「以详情页为准」）：移除列表「図」图标门禁。
                        #   列表图标与详情页図面参照同源，勇哥选详情页为唯一判据；故图标只作统计，
                        #   不再拦截——每套都进详情页由「図面参照」按钮是否存在决定下不下 PDF。
                        _floorplan = rec.get("has_floorplan", 0)  # 仅统计/日志，不再拦截
                        need_pdf = bool(want_pdf) and not _pdf_exists(root_dir, no)
                        # ---- v1.4.0 阶段1：只落列表字段，先把「全列表」对齐 ----
                        # 详情与 PDF 一律推到阶段2。这样一轮被中断时列表已一致，
                        # 不会出现"列表下一半、详情全无"的半套状态（用户要的就是列表先一致）。
                        rec["_list_only"] = True
                        rec["_need_detail"] = need_detail
                        if need_detail:
                            n_need[0] += 1
                            # 记下详情直链，阶段2 直接打开，不必为了找链接重扫列表。
                            # v1.4.1：直链随整页一次取回（零额外往返、零超时等待）。
                            href = pr["href"]
                            # v1.4.3：不再退回库里已存的 source_url 当直链——
                            # 那其实是 GBK001210 検索頁（pipeline._source_url 伪造的），
                            # 喂给 _fetch_detail 会把検索頁当詳細頁解析 → 写脏 detail_json。
                            # 真拿不到直链（「詳細」是 button、行内无 GBK 锚点）的行，
                            # 阶段2 如实记 n_skip_no_href 跳过，详情保持空（下轮用点击退路补）。
                            if href:
                                n_href_ok[0] += 1
                                if "?" not in href:
                                    n_href_bare[0] += 1
                                rec["_detail_href"] = href
                            else:
                                n_href_miss[0] += 1
                                # v1.5.0：行内无直链 → 当场在列表页点「詳細」补详情
                                # （旧版只能跳过，371 空详情缺口的根因）。点完 go_back 走
                                # bfcache 回列表，瞬时、不重抓。失败=跳过（绝不写脏，下轮重试）。
                                if inline_detail and not trial:
                                    _t_d = time.perf_counter()
                                    d, _touched = _fetch_detail_inline(
                                        ctx, page, rows, i, no, sel, cfg,
                                        pdf_worker=worker, want_pdf=need_pdf, log=log)
                                    if d:
                                        merged = dict(rec)
                                        merged.update(d)
                                        for _k in ("_list_only", "_need_detail", "_detail_href"):
                                            merged.pop(_k, None)
                                        # 详情直连 pipeline.ingest：sink.seen 已记过此番号，
                                        # 用 sink.add 会被当"轮内重复"丢弃（同阶段2 的坑）。
                                        _st2 = pipeline.ingest([merged], store, cfg, sink.run_id)
                                        sink.stats["scanned"] += _st2["scanned"]
                                        sink.stats["fetched"] += _st2["fetched"]
                                        sink.stats["new"] += _st2["new"]
                                        sink.stats["changed"] += _st2["changed"]
                                        sink.stats["pdf_saved"] += _st2["pdf_saved"]
                                        sink.stats["errors"] += _st2["errors"]
                                        rec["_need_detail"] = False   # 已补，阶段2 不再处理
                                        rec["_inline_done"] = True    # 已直连 ingest，下面不再走 sink.add
                                        rec.pop("_detail_href", None)
                                        rec["_ms_detail"] = int((time.perf_counter() - _t_d) * 1000)
                                        log("  ✓(行内) " + no + " " + str(rec.get("price", ""))
                                            + " " + str((rec.get("address") or ""))[:18]
                                            + (" [PDF→后台]" if (want_pdf and d.get("pdf_url")) else ""))
                                    else:
                                        log("  ✗(行内) " + no + " 詳細点击/解析失败，下轮重试"
                                            "（详情保持空，不写脏）")
                                        n_skip_no_href[0] += 1
                                        _inline_fail[0] += 1
                                    if d:
                                        _inline_fail[0] = 0
                                    # v1.5.2：连续失败到阈值 = 「出问题了」→ 问一句（15 秒）。
                                    #   默认 = 跳过本房型继续下一个（"按正常情况处理"，不卡整轮）。
                                    if _ask_on and _inline_fail[0] >= _inline_fail_max:
                                        _act = _ask_operator(
                                            store, _run_id, "inline_fail",
                                            {"property_no": no, "failed": _inline_fail[0],
                                             "group": label},
                                            [{"action": "keep_going",
                                              "label": "继续尝试（可能只是这几条没「詳細」）"},
                                             {"action": "skip_group",
                                              "label": "跳过本房型，继续下一个"}],
                                            "skip_group", _ask_secs, log)
                                        _inline_fail[0] = 0
                                        if _act == "skip_group":
                                            _skip_group[0] = True
                                            break
                                    # 行内点詳細 的节奏（人防机器人），同阶段2 的 2~4s。
                                    # 只在**真的惊动了 REINS**（点开了详情页）时才停——
                                    # 连点都没点成（按钮找不到/超时）就没有停顿的必要，
                                    # 否则几百条失败 ×3s 又是一轮隐形空转。
                                    if _touched:
                                        time.sleep(random.uniform(detail_gap[0], detail_gap[1]))
                        elif need_pdf:
                            # 详情已抓过、只缺 PDF：记下已有直链，阶段2直接补，不必重开详情页
                            url = (existing["pdf_url"] or "") if existing is not None else ""
                            if url:
                                rec["_existing_pdf_url"] = url
                                rec["_existing_src"] = ((existing["source_url"] or "")
                                                        if existing is not None else "")
                        if rec.get("_inline_done"):
                            # 行内已直连 ingest 详情，跳过 sink.add 避免重复 list-only 落库
                            collected.append(rec)
                        elif sink is None or sink.add(rec):
                            collected.append(rec)
                        # 【v1.4.1】这里原来有 time.sleep(random.uniform(0.5, 2.0))。
                        # 列表阶段**不发任何请求**（只读本地 DOM），所以这个停顿对 REINS
                        # 完全不可见、纯粹拖慢自己（50 行白等 25–100 秒）；已删。
                        # 「像人」的节奏改由下面翻页时的停顿承担。
                        if max_minutes and (time.monotonic() - g_t0) > max_minutes * 60:
                            g_timed_out = True
                            break

                    if _skip_group[0]:      # v1.5.2：人（或默认）选了「跳过本房型」
                        break
                    if len(collected) - cond_base >= max_items:
                        break
                    if g_timed_out:
                        break
                    nxt = page.locator(sel["next_page"]).first
                    if nxt.count() == 0 or not nxt.is_enabled():
                        break
                    # 翻页停顿：REINS 唯一看得见的动作就是「每页 1 个请求」。
                    # 真人翻页也要几秒～几十秒，所以把节奏放在这里，观感更真、总耗时大降。
                    time.sleep(random.uniform(page_pause[0], page_pause[1]))
                    nxt.click()
                    page.wait_for_load_state("networkidle", timeout=30000)
                    page.wait_for_timeout(random.randint(600, 1100))

            except SessionExpired:
                raise
            except Exception as e:
                # 兜底：若这次失败其实是撞上了「会话超时 / 环境不支持」短页，
                # 必须升级成 SessionExpired，否则会被当成"组失败"吞掉，
                # 整轮仍被标记成功、入库 0 条（这就是 2026-09-12 静默空数据的成因）。
                reason = auth.expired_reason(page)
                if reason:
                    log(f"  ✗ {label} 中断：{reason}")
                    raise SessionExpired(reason)
                log(f"  ✗ {label} 失败：{type(e).__name__}: {e}")

            log("  · 本组落库 " + str(len(collected) - cond_base) + " 条")
            log("  · 本组读表 " + str(read_ms[0]) + " ms（整页一次取回）｜详情直链命中 "
                + str(n_href_ok[0]) + "/" + str(n_need[0])
                + "（未命中 " + str(n_href_miss[0])
                + ("，其中疑似通用页 " + str(n_href_bare[0]) if n_href_bare[0] else "") + "）")
            if n_pdf_skip[0]:
                log("  · 本组无「図」图标、跳过 PDF 下载 " + str(n_pdf_skip[0]) + " 套（平台无図面）")
            if g_timed_out:
                log("⏱ 本组已到时长上限（" + str(int(max_minutes)) + " 分钟）：先收尾本组、"
                    "**继续下一组**，保证 6 个種目都被跑到（已入库的下轮会跳过详情、跑得很快）")
            if gi < len(groups):
                # v1.5.0：12 子查询之间每组停顿 10~20s（user 拍板）。
                time.sleep(random.uniform(subquery_interval[0], subquery_interval[1]))

        # ===================================================================
        # v1.4.0 阶段2：列表已全量对齐后，再回头补「详情页全字段 + PDF」。
        # 用户拍板：一轮内「先全列表再全详情」。只处理阶段1标记过的两类：
        #   · _detail_href   → 详情还没抓过（新盘 / 以前没抓到详情的）
        #   · _existing_pdf_url → 详情已有但 PDF 缺（按已有直链直接补，不重开详情页）
        # 其余（详情+PDF 都有）本轮不再动。
        # 每条详情都真打 REINS 服务端，所以只有这里才套用原来那条"真人"停顿。
        # ===================================================================
        pending = [r for r in collected if r.get("_need_detail") or r.get("_existing_pdf_url")]
        if pending:
            log("—— 阶段2：补详情 + PDF（待处理 " + str(len(pending)) + " 条）——")
            p2_t0 = time.monotonic()
            p2_done = 0
            # v1.5.15：发射阶段转换事件（前端 .phaser 面板实时显示）
            if _run_id is not None:
                try:
                    _nd = sum(1 for r in pending if r.get("_need_detail"))
                    _np = 0
                    for r in pending:
                        if r.get("has_floorplan", 0) == 1 and want_pdf \
                                and not _pdf_exists(root_dir, r.get("property_no")):
                            _np += 1
                    store.save_crawl_state(_run_id, phase="detail_pdf",
                                           need_detail=_nd, need_pdf=_np)
                except Exception:                    # noqa: BLE001
                    pass
            # （计数器已在函数开头建好 —— 别在这里重建，否则"没待处理条目"时收尾会崩）
            for rec in pending:
                no = rec.get("property_no")
                if not no:
                    continue
                # 先取出本条要走哪条路，再清掉内部标记（不让它进 detail_json）
                href = rec.get("_detail_href")
                pdf_url = rec.get("_existing_pdf_url")
                pdf_src = rec.get("_existing_src", "")
                for _k in ("_list_only", "_need_detail", "_detail_href",
                           "_existing_pdf_url", "_existing_src"):
                    rec.pop(_k, None)
                did_work = False   # v1.5.0：本条是否真的访问了 REINS（决定要不要拟人停顿）

                # v1.4.3：不再退回库里已存的 source_url 当直链——那是 GBK001210 検索頁（伪造的），
                # 喂给 _fetch_detail 会把検索頁当詳細頁解析 → 写脏 detail_json。
                # 真没直链的行，落到下面的 elif/else：PDF-only 退路或 n_skip_no_href 如实记账。
                # v1.4.1：详情优先走阶段1记下的 `_detail_href`；
                # 没有才走 PDF-only 退路。原来这里是 `if href:` 后面直接 `try`，拿不到直链时
                # **静默跳过**、日志看不出原因（今日在架 392 条中有 304 条详情为空，主因就在这里）。
                # 现把「双空（既没详情直链、也没 PDF 直链）」的跳过如实记到 n_skip_no_href，
                # 且绝不给 `_fetch_detail` 传假的 "#" 直链（见 _href_from_row 修复）。
                if href:
                    did_work = True
                    _t_d = time.perf_counter()
                    # v1.8.3（冲突3）：以详情页为准，移除列表图标门禁
                    _floorplan2 = rec.get("has_floorplan", 0)
                    _need_pdf2 = bool(want_pdf) and not _pdf_exists(root_dir, no)
                    try:
                        d = _fetch_detail(ctx, page, None, no, sel, cfg,
                                          pdf_worker=worker, need_pdf=_need_pdf2, href=href)
                        if d:
                            rec.update(d)
                            log("  ✓ " + no + " " + str(rec.get("price", "")) + " "
                                + str(rec.get("address", ""))[:18]
                                + (" [PDF→后台]" if (want_pdf and rec.get("pdf_url")) else ""))
                        else:
                            log("  ✗ " + no + " 详情为空（直链打不开或页面没内容，下轮重试）")
                    except SessionExpired:
                        raise
                    except Exception as e:                         # noqa: BLE001
                        log("  ✗ " + no + " 详情失败：" + type(e).__name__ + ": " + str(e))
                        rec["_ms_detail"] = int((time.perf_counter() - _t_d) * 1000)
                        if sink is not None:
                            # 阶段2 按「正常模式」再落一次库（写 detail_json + PDF）。
                            # 【坑·非显然】这里**不能**用 sink.add()：sink 的 seen 集合在阶段1
                            # 已经记下这个物件番号，再 add 会被当成"轮内重复"直接丢弃，
                            # 详情就永远写不进库。所以直连 pipeline.ingest，再把统计并回 sink。
                            _st2 = pipeline.ingest([rec], store, cfg, sink.run_id)
                            for _k in ("scanned", "fetched", "new", "changed", "pdf_saved"):
                                sink.stats[_k] += _st2[_k]
                            sink.stats["errors"] += _st2["errors"]
                elif pdf_url:
                    if worker is not None:
                        did_work = True
                        worker.submit(no, pdf_url, referer=pdf_src)
                        log("  · 已存在 " + no + "（详情已有，只补 PDF）")
                    else:
                        log("  · 已存在 " + no + "（未开后台 PDF，留待下一轮补）")
                else:
                    # 既无详情直链、也无 PDF 直链：这条路走不通，如实记账后跳过
                    # （详情仍为空，下轮的阶段1 行内点詳細会再试）。
                    # ⚠ v1.5.0 起**不再打印每一条**（375 条 ×1 行的刷屏毫无信息量），
                    # 只打前 5 条示例，末尾给汇总数。
                    n_skip_no_href[0] += 1
                    if n_skip_no_href[0] <= 5:
                        log("  · " + no + " 无详情直链，跳过（示例；同类不再逐条打印）")

                # 回收后台已下完的 PDF：**立即记账、立即落盘**（确定性存储）
                for _no, _p, _ms, _err in (worker.drain() if worker else []):
                    if _p:
                        store.set_pdf(_no, _p)
                        log("  ↓ PDF 已落盘 " + _no + "（%.1fs）" % (_ms / 1000.0))
                    else:
                        log("  ✗ PDF 失败 " + _no + "：" + str(_err))

                if max_minutes and (time.monotonic() - p2_t0) > max_minutes * 60:
                    log("⏱ 阶段2已到时长上限：剩余详情留待下一轮补"
                        "（已抓到的都已落库；列表本轮就是完整的）")
                    break

                # ===========================================================
                # 拟人停顿：**只在本条真的访问了 REINS 时才停**。
                # ⚠ v1.5.0 修复（勇哥 2026-09-14 报「一直在跑、空不下来，他在干嘛？」）：
                #   旧代码不判断本条到底干没干活，一律 sleep —— 375 条「无直链跳过」
                #   也照睡 375×~4.6s ≈ 28 分钟**纯空转**（run49/50/51 实测整轮 fe=0、
                #   耗时 24~28 分钟，几乎全是这段假停顿）。什么都没做就不该装人；
                #   停顿原本的目的只是"别在 REINS 眼里像机器"，没发请求就没有这回事。
                # ===========================================================
                if not did_work:
                    continue
                gap = random.uniform(detail_gap[0], detail_gap[1])
                if delay_long and random.random() < delay_long_prob:
                    gap = random.uniform(delay_long[0], delay_long[1])
                _t_g = time.perf_counter()
                time.sleep(gap)
                if sink is not None:
                    sink.stats["ms_delay"] += int((time.perf_counter() - _t_g) * 1000)
        else:
            log("· 阶段2：本轮房源的详情与 PDF 都已齐备，无需补抓")
        # v1.4.1：把「因拿不到详情直链而跳过」的条数说清楚 —— 原来这里静默跳过，
        # 真机上只表现为「有些房源永远没详情」，日志里完全看不出原因。
        if n_skip_no_href[0]:
            log("· 阶段2：有 " + str(n_skip_no_href[0]) + " 条没拿到详情直链、本轮跳过"
                "（v1.5.0 起这批已由阶段1 行内点詳細兜底；仍然空的说明点击也失败，下轮会再试）")

        if worker is not None:
            log("· 等后台 PDF 收尾…（已经下完的 PDF 不受影响）")
            worker.join(timeout=180)
            for _no, _p, _ms, _err in worker.drain():
                if _p:
                    store.set_pdf(_no, _p)
                    log(f"  ↓ PDF 已落盘 {_no}（{_ms / 1000.0:.1f}s）")
            log("· 后台 PDF：成功 %d / 失败 %d ｜ 平均 %.1fs"
                % (worker.n_ok, worker.n_fail, worker.avg_ms / 1000.0))
        browser.close()
    # v1.8.2：第 4 个返回值 = 本轮实际覆盖的**種目组数**（试跑时是 1），
    #   供 run_round 的收尾汇总框使用（旧版在那边引用未定义的 `groups` → NameError）。
    return collected, run_online_total, succeeded_subtypes, len(groups)


def _fetch_detail(ctx, page, row, property_no: str, sel, cfg,
                  pdf_worker=None, need_pdf: bool = True, href: str = "") -> dict | None:
    """进详情页抓全字段 + 下载図面/PDF（best-effort，单条失败不影响整体）。

    关键点：详情页用「独立新标签」打开，绝不让列表页 page 跳转——
    否则取完一条后列表就丢了，后续行解析为空（已踩过这个坑）。
    优先拿 詳細 链接的 href 直接 goto 新标签；拿不到再退回点击方式。

    v1.4.0：`href` 可直接传入（阶段2 复用阶段1记下的直链，row 可传 None，
    不必为了找链接重新扫一遍列表）。
    """
    rec: dict[str, Any] = {}

    # 1) 拿详情链接的 href（最稳，不碰列表页）；阶段2 已传入则直接用
    #    v1.4.1：原实现用 timeout=4000 去等一个可能**根本不存在**的「詳細」锚点
    #    （REINS 列表页里它是 <button>），每行白等 4 秒；退路还会把行内首个 <a>
    #    （中介公司链接 href="#"）当成直链。现在改为短超时探测，只认「含詳細 / 含 GBK」的锚点。
    if not href:
        href = _href_from_row(row)
    if not href and row is None:
        # 【必须挡住的一条隐患】阶段2 传进来的 row 就是 None。若此时又拿不到直链，
        # 原代码会走到下面的点击退路 → row.locator 抛 AttributeError → 被 except 吞掉 →
        # dp = page，于是把**列表页**当成详情页解析，写进一堆"字段名对不上内容"的 detail_json
        # （真机库里已出现 source_url 是列表页 / about:blank 的条目）。
        # 宁可返回 None：详情仍为空 → 下一轮自动重试；绝不写脏数据。
        return None

    dp = None
    try:
        if href and str(href).startswith("http"):
            dp = ctx.new_page()
            dp.set_default_timeout(cfg["browser"].get("timeout_ms", 30000))
            dp.goto(href, wait_until="domcontentloaded", timeout=30000)
        else:
            # 退路：点击 詳細，期望新开标签
            try:
                with ctx.expect_page(timeout=12000) as pinfo:
                    row.locator(sel["detail_button"]).first.click(timeout=8000)
                dp = pinfo.value
            except Exception:
                dp = page

        dp.wait_for_timeout(600)   # 砍掉 networkidle：REINS 详情页服务端渲染，domcontentloaded 后短等即可
        dp.wait_for_timeout(500)
        rec = _parse_detail(dp, property_no, cfg)

        # v1.9.77 F1：详情页一致性闸门 —— 列表重排导致 nth(idx) 开错房是串号根因。
        # 开页后先核「本页物件番号」是否等于期望番号；不一致直接丢弃（不写脏数据、
        # 不下载错 PDF 落到本番号名下）。抽不到番号时不挡（防误杀）。
        if not _detail_no_consistent(dp, property_no):
            print("[F1] 番号 %s 详情页物件番号不一致（疑似串号），丢弃本页" % property_no)
            return None

        # 下载図面 / PDF（v1.3.0 两种模式）
        _t_pdf = time.perf_counter()
        rec["pdf_url"] = _pdf_url_of(dp, sel) if need_pdf else ""
        try:
            rec["source_url"] = dp.url          # 详情页地址（下次补 PDF 时当 Referer）
        except Exception:
            pass
        if cfg.get("download", {}).get("pdf", True) and need_pdf:
            if pdf_worker is not None and rec["pdf_url"]:
                # 走后台：主线程不再等，立刻去抓下一条详情（最大在途由 worker 控制）
                pdf_worker.submit(property_no, rec["pdf_url"], referer=rec.get("source_url", ""))
            else:
                # 拿不到直链（或没开后台）→ 退回"点击并等待"。
                # v1.5.6：改用 `_download_pdf_by_click`（优先点「図面参照」，
                # 不再点错页脚那个「画像・図面」导航按钮；失败会记日志，不再静默）。
                _b = _download_pdf_by_click(dp)
                if _b:
                    rec["pdf_bytes"] = _b
        rec["_ms_pdf"] = int((time.perf_counter() - _t_pdf) * 1000)

        # v1.5.3：真实照片张数（读不到就**不写**，让"未知"与"真的是 0"分开；
        # 也不动 has_photo —— 那个值来自列表行的「画」图标，本就准确，别拿不准的覆盖准的）
        _pc = _photo_count_of(dp)
        if _pc is not None:
            rec["image_count"] = _pc
            rec["has_photo"] = 1 if _pc > 0 else 0

        # 同标签跳转的情况：取完详情后退回列表，保住列表上下文（否则后续行解析为空）
        if dp is page:
            try:
                page.go_back(wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(800)
                page.wait_for_selector(sel["result_rows"], timeout=15000)
            except Exception:
                pass
        return rec
    finally:
        if dp is not None and dp is not page:
            try:
                dp.close()
            except Exception:
                pass


def _fetch_detail_inline(ctx, page, rows_locator, idx, property_no, sel, cfg,
                         pdf_worker=None, want_pdf: bool = True, log=None):
    """v1.5.0：列表页行内点「詳細」→ 抓详情 + 触发 PDF → go_back 回列表(bfcache)。

    专补「詳細 是 <button>、行内无 GBK 直链」的房源（旧版只能跳过，371 空详情的根因）。
    勇哥真机实测：点完詳細→详情出来→点返回，列表走 bfcache 瞬时恢复、中间无等待；
    故返回用 page.go_back()（同标签跳转），不重新 goto 列表。

    返回 `(rec, touched)`：
      · rec     —— 解析成功为 dict（可直接 merged 进列表行 rec），失败为 None；
      · touched —— **本条是否真的惊动了 REINS**（点了詳細 / 开了详情页）。
        调用方据此决定要不要拟人停顿：没惊动就**不该**停（v1.5.0 修「空转」的关键）。

    安全：任何解析异常都返回 (None, touched)（绝不写脏数据）；SessionExpired 原样上抛，
    让整轮走「会话失效」处理（统一重登/停机）。
    """
    log = log or (lambda *_a, **_k: None)
    no = property_no
    dp = page
    touched = False
    try:
        rows_locator.nth(idx).locator(sel["detail_button"]).first.click(timeout=8000)
        touched = True
    except Exception as e:
        log(f"  · {no} 行内詳細 点击失败（{type(e).__name__}），跳过")
        return None, False
    try:
        # 同标签跳转到详情页（GBK003100）；等渲染完再解析
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.wait_for_timeout(900)
        # 防错：若 URL 仍像列表页（点的是 <a target=_blank> 开了新标签），去找最新新标签
        if "GBK003100" not in (page.url or "") and "物件詳細" not in (page.title() or ""):
            others = [p for p in ctx.pages if p is not page]
            if others:
                dp = others[-1]
                dp.set_default_timeout(cfg["browser"].get("timeout_ms", 30000))
        rec = _parse_detail(dp, no, cfg)
        # v1.9.77 F1：详情页一致性闸门（同 _fetch_detail）。不一致直接丢弃本页结果，
        # 不写脏数据、不触发错 PDF 下载。
        if not _detail_no_consistent(dp, no):
            log(f"  ✗ {no} 详情页物件番号不一致（疑似串号），丢弃")
            return None, touched
        rec["property_no"] = no
        if want_pdf and cfg.get("download", {}).get("pdf", True):
            rec["pdf_url"] = _pdf_url_of(dp, sel)
            try:
                rec["source_url"] = dp.url
            except Exception:
                pass
            # 触发即算（user 拍板：不校验是否真落盘）；后台 worker 异步落盘。
            if pdf_worker is not None and rec.get("pdf_url"):
                pdf_worker.submit(no, rec["pdf_url"], referer=rec.get("source_url", ""))
            elif not rec.get("pdf_url"):
                # v1.5.6：**补上点击退路** —— 这条路径（行内点詳細）以前只有"读直链"一条，
                # 而 REINS 详情页的図面是 <button>図面参照</button>、压根没有 href，
                # 于是直链恒为空 → PDF 恒为 0（今天 385 条新增、0 份就是这么来的）。
                # 现在拿不到直链就点按钮下载，失败会记日志。
                _b = _download_pdf_by_click(dp, log=log)
                if _b:
                    rec["pdf_bytes"] = _b
        # v1.5.3：真实照片张数（同 _fetch_detail，读不到就不写 → 保持"未知"）
        _pc = _photo_count_of(dp)
        if _pc is not None:
            rec["image_count"] = _pc
            rec["has_photo"] = 1 if _pc > 0 else 0
        return rec, touched
    except SessionExpired:
        raise
    except Exception as e:
        log(f"  ✗ {no} 行内詳細 解析失败：{type(e).__name__}: {e}")
        return None, touched
    finally:
        # 回列表：同标签跳转走 go_back（bfcache 瞬时）；新标签的关掉新标签、列表仍在。
        try:
            if dp is page:
                page.go_back(wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(500)
                page.wait_for_selector(sel["result_rows"], timeout=15000)
            else:
                dp.close()
        except Exception:
            pass


def _detail_no_consistent(dp, expected_no: str) -> bool:
    """v1.9.77 F1 详情页一致性闸门：确认刚打开的详情页真的属于 `expected_no`。

    串号根因（run74）：列表重排致 `nth(idx)` 点了错行 → 详情页是别人的房。
    这里开页后直接从 DOM 抽「物件番号」（th/label 含 物件番号/No/番号 的兄弟文本，
    或标题里的 10+ 位数字），与期望番号归一比较：
      · 抽不到（详情页结构异常）→ 返回 True（不挡，避免误杀）；
      · 抽到了且明显不等于期望 → 返回 False（串号，调用方应丢弃本页结果）。
    """
    if not expected_no:
        return True
    try:
        txt = dp.evaluate("""() => {
            const labels = ['物件番号','物件Ｎｏ','物件No','物件NO','No.','番号'];
            const nodes = document.querySelectorAll('th,dt,.p-label-title');
            for (const el of nodes) {
                const t = (el.innerText||'').replace(/[：:]/g,'').trim();
                if (labels.indexOf(t) >= 0) {
                    let v = '';
                    const sib = el.nextElementSibling;
                    if (sib) v = (sib.innerText||'').trim();
                    if (!v) { const box = el.closest('.p-label'); if (box) { const s = box.nextElementSibling; if (s) v = (s.innerText||'').trim(); } }
                    if (v) return v;
                }
            }
            const h = ((document.querySelector('h1,h2,.p-title')||{}).innerText) || '';
            const m = h.match(/[0-9]{10,}/);
            return m ? m[0] : '';
        }""")
    except Exception:  # noqa: BLE001
        return True
    if not txt:
        return True
    import unicodedata
    def _n(s):
        return "".join(unicodedata.normalize("NFKC", s)).replace("-", "").replace(" ", "")
    return _n(txt) == _n(expected_no)


def _parse_detail(dp, property_no: str, cfg) -> dict:
    """详情页：从 th/td、dt/dd 等常见结构里抠字段（不依赖某个固定 class）。"""
    rec: dict[str, Any] = {"property_no": property_no, "source_url": dp.url}
    try:
        pairs = dp.evaluate("""() => {
            const out = [];
            document.querySelectorAll('th').forEach(th => {
                const td = th.nextElementSibling;
                if (td && td.tagName === 'TD') out.push([th.innerText.trim(), td.innerText.trim()]);
            });
            document.querySelectorAll('dt').forEach(dt => {
                const dd = dt.nextElementSibling;
                if (dd && dd.tagName === 'DD') out.push([dt.innerText.trim(), dd.innerText.trim()]);
            });
            // 退路：带冒号的 .p-label-title 后一个兄弟
            document.querySelectorAll('.p-label-title').forEach(t => {
                const box = t.closest('.p-label');
                const sib = box && box.nextElementSibling;
                const v = sib ? (sib.innerText || '').trim() : '';
                if (t.textContent.trim() && v) out.push([t.textContent.trim(), v]);
            });
            return out;
        }""")
    except Exception:
        return rec

    for k, v in pairs:
        k = k.replace("：", "").replace(":", "").strip()
        field = LABEL_MAP.get(k)
        if not field:
            continue
        v = (v or "").strip()
        if field == "_station":
            rec["line_station"] = f"{rec.get('line_station','')} {v}".strip()
        elif field in ("price", "previous_price", "unit_price_sqm", "unit_price_tsubo"):
            rec[field] = _money_to_yen(v)
        elif field in ("exclusive_area", "land_area", "building_area"):
            rec[field] = _area_to_float(v)
        else:
            rec[field] = v
    return rec


def probe_query(store, cfg: dict, limit: int = 5) -> dict[str, Any]:
    """测试查询：登录后用「保存検索条件」套用 → 検索 → 返回条数 + 样本（不落库）。

    用于「测试查询」按钮——只验证"能不能查到"，不写进正式房源库。
    """
    sel = cfg["selectors"]
    conditions = cfg.get("search", {}).get("saved_conditions", ["02"])
    cond = conditions[0]
    name = f"保存条件 {cond}"

    auth = Auth(cfg, cfgmod.paths(cfg)["session"])

    with _sync_playwright() as p:
        browser = auth.launch(p, headless=cfg["browser"].get("headless", False))
        ctx, page = auth.open_authed_page(browser)   # 会话失效会自动用本地账号重登一次

        _expand_search_panel(page, sel)
        # 控件没出来 → 多半是会话过期，直接给出人话提示，别抛 Playwright 天书
        try:
            page.wait_for_selector(sel["saved_condition_select"], timeout=8000)
        except Exception as e:
            raise SessionExpired(
                "搜索条件页没加载出「保存した検索条件の選択」下拉框，"
                "通常是登录会话已过期（或站点改版）。请先重新登录再试。") from e
        # v1.5.4：同样不碰「ワンタッチ検索」下拉（config: search.use_saved_condition）。
        if ((cfg.get("search", {}) or {}).get("use_saved_condition", True)):
            _load_saved_condition(page, sel, cond, lambda *_: None)
            _dismiss_modal(page)
        page.locator(sel["search_button"]).first.click()
        page.wait_for_load_state("networkidle", timeout=40000)
        page.wait_for_timeout(2500)
        # v1.5.10：同主循环，点検索后可能弹「500件事前確認框」，需点はい确认
        if _dismiss_modal(page):
            page.wait_for_timeout(1500)
        total_txt = _read_total(page, sel)
        sample: list[str] = []
        rows = page.locator(sel["result_rows"])
        n = min(rows.count(), limit)
        for i in range(n):
            try:
                txt = rows.nth(i).inner_text(timeout=2000)
            except Exception:
                continue
            sample.append(txt.replace("\n", " ").strip()[:140])
        browser.close()

    return {"round": name, "total": total_txt or "未知",
            "count": len(sample), "sample": sample}


# ---------------- 小工具 ----------------
def _safe_text(page, selector: str) -> str:
    try:
        loc = page.locator(selector).first
        return (loc.inner_text(timeout=3000) or "").strip()
    except Exception:
        return ""


def _check_maintenance(page, log) -> None:
    try:
        body = page.inner_text("body")
    except Exception:
        return
    if "メンテナンス" in body or "ご利用いただけません" in body:
        log("⚠ REINS 正在维护（日本时间 23:00–次日 07:00，年末 12/27–1/4），停止本轮")
        raise SessionExpired("REINS メンテナンス中")


# ============================================================
# 指定日期下载：手动选项套用 + 实时查询 / 正式下载
# ============================================================
def _to_era_ymd(ymd: str):
    """'YYYY-MM-DD' → (era_code, year_in_era, month, day)。
    era_code: 'R'(令和) / 'H'(平成) / 'S'(昭和)。解析失败返回 None。
    和暦对照：令和=2019- → N=AD-2018；平成=1989- → N=AD-1988；昭和=1926- → N=AD-1925。
    """
    import re as _re
    m = _re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", (ymd or "").strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 2019 <= y:
        return ("R", y - 2018, mo, d)
    if 1989 <= y <= 2018:
        return ("H", y - 1988, mo, d)
    if 1926 <= y <= 1988:
        return ("S", y - 1925, mo, d)
    return None


def _fill_date_block(page, field: str, era, log) -> str:
    """用 JS（与 BVID 无关）把 登録/変更 年月日 设为「日付を指定」+ 指定和暦日期。

    era = (era_code 'R'/'H'/'S', year_in_era, month, day)。
    返回 'OK' 或错误原因字符串。点击 radio 后等待 450ms 让其解锁 date 输入框再填写。
    """
    era_code, y, m, d = era
    js = r"""
    (args) => {
      const title = args.title;
      const era = args.era, y = String(args.y), mo = String(args.m), dd = String(args.d);
      const t = Array.prototype.slice.call(document.querySelectorAll('.p-label-title'))
                   .find(function(e){ return e.textContent.trim() === title; });
      if (!t) return 'NO_TITLE:' + title;
      let box = t.closest('.col, .container, .row');
      if (!box) box = t.parentElement.parentElement;
      const radios = Array.prototype.slice.call(box.querySelectorAll('input[type=radio]'));
      let target = null;
      for (const r of radios) {
        const c = r.closest('.custom-control');
        if (!c) continue;
        const lab = c.querySelector('label');
        if (lab && lab.textContent.indexOf('日付を指定') >= 0) { target = r; break; }
      }
      if (!target) return 'NO_RADIO';
      const c = target.closest('.custom-control');
      const lab = c && c.querySelector('label');
      if (lab) lab.click(); else target.click();
      return new Promise(function(resolve){
        setTimeout(function(){
          const nums = Array.prototype.slice.call(box.querySelectorAll("input[type=text][inputmode='numeric']"));
          const eras = Array.prototype.slice.call(box.querySelectorAll('select')).filter(function(s){
            const os = Array.prototype.slice.call(s.options);
            return os.some(function(o){ return ['令和','平成','昭和'].indexOf(o.textContent.trim()) >= 0; });
          });
          if (!eras.length || nums.length < 3) { resolve('NO_DATE_INPUTS'); return; }
          function setEra(sel, code){ sel.value = code; sel.dispatchEvent(new Event('change',{bubbles:true})); }
          function setNum(inp, v){ if(!inp) return; inp.value = v; inp.dispatchEvent(new Event('input',{bubbles:true})); }
          setEra(eras[0], era);
          setNum(nums[0], y); setNum(nums[1], mo); setNum(nums[2], dd);
          if (eras[1]) setEra(eras[1], era);
          setNum(nums[3], y); setNum(nums[4], mo); setNum(nums[5], dd);
          resolve('OK');
        }, 450);
      });
    }
    """
    try:
        return page.evaluate(js, {"title": field, "era": era_code,
                                  "y": y, "m": m, "d": d})
    except Exception as e:  # noqa: BLE001
        return "JS_ERR:" + type(e).__name__ + ":" + str(e)


def _reset_date_block(page, field: str, log) -> str:
    """把 登録/変更 年月日 那排**显式复位为「指定なし(全期間)」**（与 BVID 无关）。

    【为什么必须有这个函数】保存した検索条件（01 名字里带「当天」）点「読込」之后，
    会把这两排日期设成**当日**。老代码在「不指定日期」时直接 return，什么也不做，
    于是「当天」残留 → 更新轮只能抓到当天那点房源 → 历史房源连续两轮不在并集
    → 被误标下架（2026-09-14 604 条误标事件的最后一环）。

    返回 'OK' 或错误原因字符串。
    """
    js = r"""
    (args) => {
      const title = args.title;
      const t = Array.prototype.slice.call(document.querySelectorAll('.p-label-title'))
                   .find(function(e){ return e.textContent.trim() === title; });
      if (!t) return 'NO_TITLE:' + title;
      let box = t.closest('.col, .container, .row');
      if (!box) box = t.parentElement.parentElement;
      const radios = Array.prototype.slice.call(box.querySelectorAll('input[type=radio]'));
      for (const r of radios) {
        const c = r.closest('.custom-control');
        if (!c) continue;
        const lab = c.querySelector('label');
        if (lab && lab.textContent.indexOf('指定なし') >= 0) {
          if (lab) lab.click(); else r.click();
          return 'OK';
        }
      }
      return 'NO_RADIO';
    }
    """
    try:
        return page.evaluate(js, {"title": field})
    except Exception as e:  # noqa: BLE001
        return "JS_ERR:" + type(e).__name__ + ":" + str(e)


def _ctrl_select(page, title: str):
    """定位 select 类検索条件控件，兼容「控件是 .p-label 的后代」与「控件在相邻 div 里」两种结构
    （REINS 实测：控件在 .p-label 的相邻 div 内，故用 `+ div select`）。"""
    return page.locator(
        f"div.p-label:has(span.p-label-title:has-text('{title}')) select, "
        f"div.p-label:has(span.p-label-title:has-text('{title}')) + div select").first


def _ctrl_input(page, title: str, exact: bool = False, area: str = ""):
    """定位 input 类検索条件控件（相邻 div 内，REINS 实测结构）。
    exact=True 时用 :text-is 精确匹配 label（避免 価格 误中 成約価格）。

    ⚠ v1.6.2【踩坑记录·真机血泪】REINS 検索条件入力页里「都道府県名」「所在地名１」
    这类字段存在**两套同名控件**（真机 DOM 快照 data/probe/diag_form_after_apply.html）：
      · 「所在地範囲選択１/２/３」区块 —— input 带 `disabled="disabled"`，
        只能靠「保存した検索条件の選択 → 読込」带出，**无法手填**；
      · 「所在地１/２/３」区块 —— 字段名完全相同，**无 disabled、可手填**
        （就是勇哥截图里那一块，含「建物名」）。
    不加限定时 `has-text('都道府県名')` 会**同时命中 9 个**元素，Playwright 严格模式下
    `locator.wait_for()` 直接抛 `Error`（注意：不是 TimeoutError）→ 填写静默失败
    → 检索退化成「无地区条件」→ 结果恒为「未知」（v1.6.0/1.6.1 线上事故根因）。
    故凡涉及这套重复字段，**必须**用 area='所在地１' 限定到可手填的那个区块。
    """
    m = ":text-is" if exact else "has-text"
    base = f"div.p-label:has(span.p-label-title:{m}('{title}')) + div input.p-textbox-input"
    if area:
        # h3 标题所在的 row，其紧邻兄弟即该区块的 container
        scope = f"div.row:has(> div > h3:text-is('{area}')) + div.container"
        return page.locator(f"{scope} {base}")
    return page.locator(base)


def _tick_ownership_only(page, log) -> str:
    """勾「土地権利/借地権種類 = 所有権のみ」—— 和用户线上口径一致（正常人就这么点）。

    找不到该控件就按「指定なし（＝全部）」继续，只告警不阻断
    （用户原话：「如果真的没有，就用全部」）。
    """
    js = r"""
    (args) => {
      const want = args.want;
      const title = Array.prototype.slice.call(document.querySelectorAll('.p-label-title'))
        .find(function(e){ return (e.textContent || '').replace(/\s/g,'').indexOf('土地権利') >= 0; });
      if (!title) return 'NO_TITLE';
      let node = title;
      for (let k = 0; k < 8 && node; k++) {
        node = node.parentElement;
        if (node && node.querySelectorAll('input[type=radio]').length >= 2) break;
      }
      if (!node) return 'NO_BOX';
      const radios = Array.prototype.slice.call(node.querySelectorAll('input[type=radio]'));
      for (const r of radios) {
        const c = r.closest('.custom-control') || r.parentElement;
        const lab = c ? c.querySelector('label') : null;
        const txt = (lab ? lab.textContent : '').replace(/\s/g,'');
        if (txt.indexOf(want) >= 0) { (lab || r).click(); return 'OK'; }
      }
      return 'NO_RADIO';
    }
    """
    try:
        res = page.evaluate(js, {"want": "所有権のみ"})
    except Exception as e:  # noqa: BLE001
        res = "JS_ERR:" + type(e).__name__
    if res == "OK":
        log("· 已勾 土地権利/借地権種類 = 所有権のみ（与线上口径一致）")
    else:
        log("⚠ 未能勾上「所有権のみ」（" + str(res) + "），本次按「全部」检索")
    page.wait_for_timeout(500)
    return str(res)


def _fill_subtype_slots(page, kind: str, subs: list[str], log) -> int:
    """按 REINS「基本条件」原生槽位填写 物件種別/物件種目 —— 一次检索最多容纳 4 个種目。

    面板结构（用户截图实证，正常人就是这么点的）：
        物件種別１ [種別]  物件種目１ [種目]  物件種目２ [種目]
        物件種別２ [種別]  物件種目１ [種目]  物件種目２ [種目]
    同一个種別可以占两行（截图里 売マンション 就写了两行，共 4 个種目）。
    任何槽位填不进去就跳过（留空 ＝ 全部），绝不阻断。返回成功填上的種目数。
    """
    subs = [str(s).strip() for s in (subs or []) if str(s).strip()]
    if not subs:
        return 0

    def sel_all(label: str):
        return page.locator(
            "div.p-label:has(span.p-label-title:has-text('" + label + "')) + div select")

    def pick(loc_all, idx: int, value: str, what: str) -> bool:
        try:
            if loc_all.count() > idx:
                loc_all.nth(idx).select_option(label=value, timeout=6000)
                page.wait_for_timeout(400)
                log("· 已选 " + what + " = " + value)
                return True
        except Exception as e:  # noqa: BLE001
            log("⚠ " + what + " 选择失败（" + value + "）：" + type(e).__name__)
        return False

    def clear_slot(loc_all, idx: int) -> None:
        """把槽位恢复成「指定なし」。

        【非显然·v1.2.3】套用保存条件后，两行槽位都会带着保存条件里的種別/種目。
        现在只填行 1，若不清掉行 2，检索范围就会多出保存条件自带的種目
        （例如想只抓 新築/中古マンション，却把行 2 的 タウン 也一起查出来）。
        仅当第一个 option 确实是「空 / 指定なし」类才选它，避免误选成真实種目。
        """
        try:
            if loc_all.count() <= idx:
                return
            sel = loc_all.nth(idx)
            first = sel.locator("option").first
            txt = (first.inner_text() or "").strip()
            val = (first.get_attribute("value") or "").strip()
            if val == "" or txt == "" or ("指定" in txt) or ("すべて" in txt) or ("選択" in txt):
                sel.select_option(index=0, timeout=3000)
                page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001
            pass

    kinds1 = sel_all("物件種別１")
    kinds2 = sel_all("物件種別２")
    it1 = sel_all("物件種目１")     # 行1種目1 与 行2種目1 同名（DOM 里两个）
    it2 = sel_all("物件種目２")     # 行1種目2 与 行2種目2 同名（DOM 里两个）

    # ---- 0) 先从干净状态开始：清掉两个保存条件可能留下的種別/種目残留 ----
    for _loc in (kinds1, kinds2):
        for _i in range(_loc.count()):
            clear_slot(_loc, _i)
    for _loc in (it1, it2):
        for _i in range(_loc.count()):
            clear_slot(_loc, _i)
    log("· 已复位 物件種別/種目 全部槽位（清掉保存条件的残留，避免多抓）")

    k = (kind or "").strip()
    n = 0
    # ---- 行 1：種別１ + 種目１ + 種目２ ----
    if k:
        pick(kinds1, 0, k, "物件種別１")
    if pick(it1, 0, subs[0], "物件種目１(行1)"):
        n += 1
    if len(subs) > 1 and pick(it2, 0, subs[1], "物件種目２(行1)"):
        n += 1
    # ---- 行 2：仅当还有第 3/4 个種目时才用 ----
    if len(subs) > 2:
        if k:
            pick(kinds2, 0, k, "物件種別２")
        if pick(it1, 1, subs[2], "物件種目１(行2)"):
            n += 1
    if len(subs) > 3 and pick(it2, 1, subs[3], "物件種目２(行2)"):
        n += 1
    if len(subs) > 4:
        log("⚠ 一次检索最多 4 个物件種目，已忽略：" + "、".join(subs[4:]))
    return n


def _apply_geo_manual_retry(page, sel, pref, city, log, cfg, max_attempts=3) -> bool:
    """直录地区基线（所在地１ 区块：都道府県名 + 所在地名１），REINS 慢渲染时重试，
    避免因瞬时超时把整轮 12 组全打死。

    v1.9.2 健壮性修复（勇哥 09-18 报「第 4 组地区直录 TimeoutError 整轮中止」）：
    · 原 _apply_manual_conditions 直录分支 wait_for timeout=8000 写死，且失败即
      raise RuntimeError("中止本轮") —— REINS 偏慢（同轮结果区曾等 45s）时，偶发一组
      控件晚于 8s 渲染 → 整轮 12 组全废、当天漏抓。
    · 现：超时提到 25000ms；失败先 page.goto 重进検索条件入力页 + 重展面板重试
      （默认 3 次含首次）；仍失败返回 False，由调用方决定「跳过该组」而非杀整轮。
    · 注意：地区直录是 _apply_manual_conditions 内**首步**页面操作（日期仅解析、種目在其后
      才 fill），故重试 goto 重进条件页不会丢已填条件；重试成功后函数继续填種目。
    """
    _search_url = ((cfg or {}).get("site", {}) or {}).get("search_url")
    for _attempt in range(1, max_attempts + 1):
        try:
            _pi = _ctrl_input(page, "都道府県名", area="所在地１").first
            _pi.wait_for(state="visible", timeout=25000)
            _pi.fill(pref, timeout=25000)
            page.wait_for_timeout(400)
            _ci = _ctrl_input(page, "所在地名１", area="所在地１").first
            _ci.wait_for(state="visible", timeout=25000)
            _ci.fill(city, timeout=25000)
            page.wait_for_timeout(400)
            log("· 已直录 地区基线：都道府県名=" + pref + " / 所在地名１=" + city
                + "（所在地１ 区块，第 " + str(_attempt) + " 次成功）")
            return True
        except Exception as _e:  # noqa: BLE001
            if _attempt < max_attempts and _search_url:
                log("⚠ 地区直录第 " + str(_attempt) + " 次失败（" + type(_e).__name__
                    + ": " + str(_e)[:120] + "），重进条件页重试…")
                try:
                    page.goto(_search_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1200)
                    _expand_search_panel(page, sel)
                    page.wait_for_timeout(800)
                except Exception as _ne:  # noqa: BLE001
                    log("⚠ 重进条件页也失败（" + type(_ne).__name__ + "），继续下次重试")
            else:
                log("✗ 地区基线直录失败（" + type(_e).__name__ + ": " + str(_e)[:160] + "）")
    return False


def _apply_manual_conditions(page, sel, opts, log, cfg=None) -> None:
    """把用户手动选项套到 REINS 検索条件表单（label 锚定，与 BVID 无关）。

    实现要点（REINS 实测约束 · v1.6.2 校正）：
    · 検索要求「所在地 / 所在地範囲 / 沿線駅 / バス路線 / その他交通」其一必填；
      物件種別 单独不够。
    · 【关键】页面上「都道府県名 / 所在地名１」有**两套同名控件**（真机 DOM 已核）：
        ①「所在地範囲選択１/２/３」——input 带 `disabled="disabled"`，**只能**靠
          「保存した検索条件の選択 → 読込」把地区带出来（＝ use_saved_condition=true 走这条）；
        ②「所在地１/２/３」——字段名完全相同但**无 disabled、可手填**（勇哥截图那块，
          含「建物名」）→ ＝ use_saved_condition=false 走这条。
      两条路二选一，都能拿到 大阪府/大阪市 基线。
    · use_saved_condition=true（当前默认）：以 物件種別 映射保存条件（01=売土地+売一戸建 /
      02=売マンション）作为地区基线 → 保存条件**只负责带出地区**，抓什么種目由
      update_groups / 前端选择决定；其名字里「当天」的日期副作用已由 _reset_date_block 消除。
    · use_saved_condition=false：直接用「所在地１」区块手填（v1.6.2 修正 locator 后才可用）。

    v1.2.3：**date 变为可选**——更新轮次（自动/手动）不设日期（＝全期间口径，
    靠「最近順 + 翻页到底」把 6 个種目抓全）；只有「指定日期下载」才填日期块。

    opts: kind/subtype(s)/groups/ward/price_min/max/area_min/max/line/station/date_type/date
    """
    # ---- 日期（可选）----
    # v1.5.5（2026-09-15 修正）：date 支持按「登録年月日 / 変更年月日」检索。
    #   ⚠ 实测：REINS 两排**同时**设 = AND（交集），非 OR。故「登録或変更」这种并集诉求
    #   由调用方把两日期**拆成两次检索、按物件番号合并**实现（run_round_options 的 rounds；
    #   sync_today_dates），本函数一次只负责把被请求的那个/几个日期块填好（并先复位另一排）。
    date = (opts.get("date") or "").strip()
    _raw_dts = opts.get("date_types")
    if isinstance(_raw_dts, str):
        _raw_dts = [_raw_dts]
    dfields: list[str] = [x for x in (_raw_dts or []) if x in ("登録年月日", "変更年月日")]
    if not dfields:
        _f0 = (opts.get("date_type") or "登録年月日").strip()
        dfields = [_f0 if _f0 in ("登録年月日", "変更年月日") else "登録年月日"]
    era = None
    if date:
        era = _to_era_ymd(date)
        if not era:
            raise SessionExpired("指定日期格式无法解析：" + date + "（需 YYYY-MM-DD）")

    # ---- 1) 地区基线（大阪府/大阪市）----
    # v1.5.4：用户明确指示「检索页那个『ワンタッチ検索』下拉不要做任何操作」。
    #   ① 该下拉的选择与后续检索结果无关（地区本就由登录后的默认所在地决定）；
    #   ② 更要命的是保存条件 01 名字里带「当天」，点「読込」会把日期筛选设成当日
    #      → 正常更新轮只能抓到当天那点房源，历史房源连续两轮不在并集 → 被误标下架。
    #   所以默认不再点它（config: search.use_saved_condition=false），地区沿用默认所在地。
    kind = (opts.get("kind") or "").strip()
    if ((cfg or {}).get("search", {}) or {}).get("use_saved_condition", True):
        cond = (_geo_cond(cfg, kind) if cfg is not None
                else {"売マンション": "02", "売土地": "01", "売一戸建": "01"}.get(kind, "02"))
        try:
            _load_saved_condition(page, sel, cond, log)
            page.wait_for_timeout(800)
        except Exception as e:  # noqa: BLE001
            log("⚠ 保存条件加载失败（" + type(e).__name__ + ": " + str(e) + "），继续手动套用")
    else:
        # v1.6.2（R25 修正）：不碰「保存条件/読込」时，改用**可手填**的「所在地１」区块
        #   直接录入地区基线（都道府県名=大阪府 / 所在地名１=大阪市）。
        #   ⚠ 必须 area='所在地１' 限定：同名控件在「所在地範囲選択１〜３」里还有一份，
        #     那些带 disabled 且会让 locator 命中多元素直接抛 Error（详见 _ctrl_input 注释）。
        _pref = ((cfg or {}).get("search", {}) or {}).get("prefecture", "大阪府")
        _city = ((cfg or {}).get("search", {}) or {}).get("city", "大阪市")
        # v1.9.2：直录超时 8000→25000ms + 失败重进条件页重试（默认 3 次）；
        # 仍失败抛 _GeoFailed（非 RuntimeError），由调用方「跳过该组」而非杀整轮。
        _geo_ok = _apply_geo_manual_retry(page, sel, _pref, _city, log, cfg)
        if not _geo_ok:
            # ⚠ v1.6.2 的底线仍成立：REINS 检索**要求 所在地/沿線/バス 其一必填**，
            #   没有地区条件时检索恒为空或不可预期，绝不能静默继续（否则抓 0 条记成完成）。
            #   但 v1.9.2 把「整轮中止」改为「仅本组跳过」——瞬时超时（REINS 慢渲染）
            #   不该杀死当天全部 12 组同步；真改版（持续失败）时该组当天漏抓会在日志标出。
            raise _GeoFailed(
                "地区基线（大阪府/大阪市）直录失败，且未启用「読込」加载保存条件 —— "
                "已重试仍失败（可能 REINS 検索条件入力页改版）。检索将无地区限定，"
                "本组跳过以免抓空/污染；其余组不受影响。"
                "请核 REINS 検索条件入力页是否改版；或临时把 config search.use_saved_condition 置回 true。")

    # ---- 2) 物件種別 / 物件種目：按 REINS 原生槽位，一次检索最多填 4 个種目 ----
    #        （物件種別１/種目１/種目２ + 物件種別２/種目１/種目２）
    #        填不进的槽位留空 ＝ 全部（用户：「如果真的没有，就用全部」）。
    subs = [s for s in _normalize_subs(opts) if s]
    if subs:
        got = _fill_subtype_slots(page, kind, subs[:4], log)
        if got == 0:
            # v1.5.7：这里原来是「按『全部』检索」继续跑。实测那是个坑——
            # 页面没渲染完时，物件種目 / 所有権 / 登録・変更年月日 全是 NO_TITLE，
            # 接着连「検索」按钮都等不到（白等 30 秒），最后本组落库 0 条；
            # 万一按钮点到了，更糟：会按「全部種目」检索，把范围外的房源也拉进来。
            # 所以改成**快速失败**：本组跳过（轮次继续下一组），绝不按「全部」乱搜。
            raise RuntimeError(
                "物件種目 一个都没填进去（检索页未渲染完 / 保存条件未套用）"
                "→ 跳过本组，避免按「全部」检索污染数据")
    else:
        log("· 未指定物件種目 → 按保存条件的整组检索")

    # ---- 2b) 土地権利/借地権種類 = 所有権のみ（与线上口径一致；找不到则按全部）----
    if opts.get("ownership_only", True):
        _tick_ownership_only(page, log)

    # ---- 3) 区（所在地名１，optional narrowing）----
    ward = (opts.get("ward") or "").strip()
    if ward:
        try:
            # v1.6.2：同样限定「所在地１」可手填区块（不限定会命中 9 个同名控件 → Error）
            loc = _ctrl_input(page, "所在地名１", area="所在地１").first
            loc.wait_for(state="visible", timeout=8000)
            if loc.is_disabled():
                log("⚠ 区 输入框仍禁用，跳过区筛选")
            else:
                loc.fill(ward, timeout=8000)
                page.wait_for_timeout(500)
                log("· 已填 区 = " + ward)
        except Exception as e:  # noqa: BLE001
            log("⚠ 区 填写失败（" + str(e) + "），忽略")

    # ---- 4) 価格 min/max（万円）----
    for key, idx in (("price_min", 0), ("price_max", 1)):
        v = opts.get(key)
        if v in (None, ""):
            continue
        try:
            locs = _ctrl_input(page, "価格", exact=True).all()
            if len(locs) > idx:
                locs[idx].fill(str(v), timeout=6000)
                page.wait_for_timeout(400)
                log("· 已填 価格 " + key + " = " + str(v) + " 万円")
        except Exception as e:  # noqa: BLE001
            log("⚠ 価格 " + key + " 填写失败（" + str(e) + "），忽略")

    # ---- 5) 面積（専有面積 min/max，㎡）----
    for key, idx in (("area_min", 0), ("area_max", 1)):
        v = opts.get(key)
        if v in (None, ""):
            continue
        try:
            locs = _ctrl_input(page, "専有面積").all()
            if len(locs) > idx:
                locs[idx].fill(str(v), timeout=6000)
                page.wait_for_timeout(400)
                log("· 已填 専有面積 " + key + " = " + str(v) + " ㎡")
        except Exception as e:  # noqa: BLE001
            log("⚠ 面積 " + key + " 填写失败（" + str(e) + "），忽略")

    # ---- 6) 沿線名 / 駅名 ----
    line = (opts.get("line") or "").strip()
    if line:
        try:
            loc = _ctrl_input(page, "沿線名")
            loc.wait_for(timeout=6000)
            loc.fill(line, timeout=6000)
            page.wait_for_timeout(400)
            log("· 已填 沿線名 = " + line)
        except Exception as e:  # noqa: BLE001
            log("⚠ 沿線名 填写失败（" + str(e) + "），忽略")
    station = (opts.get("station") or "").strip()
    if station:
        try:
            loc = _ctrl_input(page, "駅名")
            loc.wait_for(timeout=6000)
            loc.fill(station, timeout=6000)
            page.wait_for_timeout(400)
            log("· 已填 駅名 = " + station)
        except Exception as e:  # noqa: BLE001
            log("⚠ 駅名 填写失败（" + str(e) + "），忽略")

    # ---- 7) 指定日期（可选；不填＝不设日期筛选）----
    # 【2026-09-15 实测修正·关键】REINS 在**同一检索**里同时设 登録年月日 + 変更年月日
    #   = AND（交集），**不是 OR**。两排同设只返回「当天既新登録又変更」的极少数
    #   （实测 866 真并集 vs 145 交集）。因此：
    #   ① 无论请求几个日期，都**先把两排都复位成「指定なし(全期間)」**，杜绝上一次
    #      检索 / 保存条件残留的日期污染本轮（2026-09-14 604 条误标事件的最后一环）；
    #   ② 「登録/変更 同时命中」必须由「分两次检索、按物件番号合并」实现
    #      （见 run_round_options 的 rounds 拆分 与 sync_today_dates），单次检索做不到 OR。
    _n_ok = 0
    for _f in ("登録年月日", "変更年月日"):
        _r = _reset_date_block(page, _f, log)
        if _r == "OK":
            _n_ok += 1
        else:
            log("⚠ 日期复位失败（" + _f + "）：" + _r + " —— 可能残留保存条件的日期条件")
    if not date:
        if _n_ok == 2:
            log("· 未指定日期 → 已把 登録/変更 年月日 两排复位为「指定なし(全期間)」"
                "（清掉保存条件自带的「当天」）")
        return
    for _f in dfields:
        res = _fill_date_block(page, _f, era, log)
        if res != "OK":
            raise SessionExpired("指定日期填写失败（" + _f + "）：" + res)
    log("· 日期筛选：" + "、".join(dfields) + " = " + date
        + ("（注意：REINS 同屏多排=AND，已改由调用方分多次检索合并）" if len(dfields) > 1 else ""))


def _normalize_subs(opts: dict) -> list[str]:
    """把 opts 里的種目收拢成「要各跑一遍的種目名列表」（去重、保序）。

    opts["subtypes"] 优先；没有就退回单个 opts["subtype"]；都没有 → [""]
    （空串表示不限種目 = 保存条件自带的整组）。
    """
    raw = opts.get("subtypes")
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for x in (raw or []):
        t = str(x or "").strip()
        if t and t not in out:
            out.append(t)
    if not out:
        t = str(opts.get("subtype") or "").strip()
        out = [t] if t else [""]
    return out


# ============================================================
# v1.6.3：每次检索的「条件横幅」—— 勇哥要求 DOS 框内直接看到本次搜索条件
#
# 显示：序号 / 房屋类型(新築·中古) / 区域(大阪府大阪市 是否直录) / 日期轴 / 日期范围 / 轮次。
# 设计意图：让"区域到底有没有选上、日期轴取的是登録还是変更、房屋类型对不对"在日志里一眼可见，
# 避免 v1.6.0/1.6.1 那种"地区没录进去却记成完成"的事故再次无声发生。
# ============================================================
def _log_search_banner(log, *, seq=None, total=None, kind="", subtypes=None,
                        cfg=None, date_fields=None, date_range="", round_tag=""):
    """在 DOS 框 / 日志内打印一次检索的条件横幅，便于直观核对。"""
    subs = list(subtypes or [])
    house = "·".join(subs) if subs else (kind or "—")
    new_u = any(str(s).startswith("新築") for s in subs)
    old_u = any(str(s).startswith("中古") for s in subs)
    if new_u and old_u:
        build = "新築+中古"
    elif new_u:
        build = "新築"
    elif old_u:
        build = "中古"
    else:
        build = "—"
    # 区域：R25(v1.6.2) 之后由 crawler 直录 大阪府+大阪市；
    # use_saved_condition=true 时地区来自保存条件。统一显示为「大阪府・大阪市」基线。
    use_sc = bool(((cfg or {}).get("search", {}) or {}).get("use_saved_condition", False))
    region_mode = "保存条件套用(大阪府大阪市)" if use_sc else "直录 大阪府・大阪市"
    region_text = "大阪府・大阪市"
    df = "/".join(date_fields) if date_fields else "（未指定 = 全期間）"
    dr = date_range or "全期間（不限日期）"
    seq_s = ("%s/%s" % (seq, total)) if (seq is not None and total) else str(seq or "—")
    lines = [
        "========== 检索条件 ==========",
        "· 序号    : " + seq_s,
        "· 房屋类型: " + house + "   ［新築/中古 = " + build + "］",
        "· 区域    : " + region_text + "   ［" + region_mode + "］",
        "· 日期轴  : " + df,
        "· 日期范围: " + dr,
    ]
    if round_tag:
        lines.append("· 轮次    : " + round_tag)
    lines.append("================================")
    for ln in lines:
        log(ln)


def _log_session_summary(log, title, spec, results, stats, online_total):
    """打印一次下载任务的「条件 + 结果」汇总框，便于直观核对（v1.6.4）。

    spec:    list[str] 条件行（種目组/日期轴/日期范围/区域…）
    results: list[dict] 逐条件结果，每项 {tag, online, downloaded}
    stats:   dict 含 scanned/fetched/new/changed/pdf_saved
    """
    bar = "=" * 38
    lines = [bar, "■ " + title, "", "【下载条件】"]
    for s in (spec or []):
        lines.append("  · " + s)
    lines.extend(["", "【下载结果】"])
    lines.append("  · 线上报告合计 %s 件"
                 % (online_total if online_total is not None else "—"))
    lines.append("  · 本地落库 %s 条（新盘 %s / 变更 %s）/ PDF %s 份"
                 % (stats.get("fetched", 0), stats.get("new", 0),
                    stats.get("changed", 0), stats.get("pdf_saved", 0)))
    if stats.get("detail_backfilled"):
        lines.append("  · 阶段B 自动补详情 %s 条（兜底补齐列表壳的详情/PDF，无需手动）"
                     % stats["detail_backfilled"])
    if results:
        lines.extend(["", "【逐条件结果】（线上报告 / 本次下载）"])
        for i, r in enumerate(results, 1):
            lines.append("  %d. %s → 线上 %s / 下载 %s"
                         % (i, r.get("tag", "?"), r.get("online", "—"),
                            r.get("downloaded", "—")))
    lines.append(bar)
    for ln in lines:
        log(ln)


# ============================================================
# v1.2.3：更新 / 下载的「種目分组」
#
# 用户口径：**更新时把「一戸建 前 2 个 + 公寓 前 4 个」一共 6 个種目全下**，
# 不能只下一户建、也不能听命于 REINS 上「保存条件」里装了什么。
#
# 为什么必须拆组：REINS「基本条件」原生只有
#     物件種別１[種別]+種目１+種目２ / 物件種別２[種別]+種目１+種目２
# 一行 2 个種目、两行共 4 个，**且同一行只能挂一个種別**。跨種別（一户建 ↔ 公寓）
# 只能分多次检索，故拆成：
#     組1 売一戸建 × [新築戸建, 中古戸建]
#     組2 売マンション × [新築マンション, 中古マンション]
#     組3 売マンション × [新築タウン, 中古タウン]
# ============================================================
def _norm_groups(raw) -> list[dict]:
    """把配置 / 前端给的種目表收拢成 [{"kind","subtypes":[...]}]。

    自动去重、保序；每组最多 2 个種目（超出就顺次拆成多组），
    以匹配 REINS 原生槽位（一次检索一行只放 2 个種目）。
    """
    out: list[dict] = []
    seen: set = set()
    for g in (raw or []):
        if not isinstance(g, dict):
            continue
        kind = str(g.get("kind") or "").strip()
        subs: list[str] = []
        for s in (g.get("subtypes") or []):
            t = str(s or "").strip()
            if t and t not in subs:      # 去重保序，避免同一種目被重复检索
                subs.append(t)
        if not kind or not subs:
            continue
        for i in range(0, len(subs), 2):
            chunk = subs[i:i + 2]
            key = (kind, tuple(chunk))
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": kind, "subtypes": chunk})
    return out


def _update_groups(cfg) -> list[dict]:
    """更新轮次（自动 / 手动 / 正式下载）固定要覆盖的種目组。

    v1.5.5：把「拆平成单个種目」与「新增 / 変更 第二轴」**解耦**
    —— 勇哥 2026-09-14 拍板：「组数是 6」。
      · `crawl.split_subtypes`（默认 true）→ 把配置里的種目组拆平成**一个種目一组**：
        新築戸建 / 中古戸建 / 新築マンション / 中古マンション /
        新築タウン / 中古タウン = **6 组**，每组各检索一次。
      · `crawl.split_new_changed`（默认 false）→ **不再**把每组按「新增 / 変更」再跑一遍。
        REINS 的検索条件里**没有**独立的「変更」轴，两轴的检索条件逐字节相同，
        真跑 12 次 = 同样结果请求两遍（用户 09-14：「感觉都是在操作两次，这完全是多余的动作」）。
    合并成一次检索不会漏：这 6 组两两互斥、并集就是原来 3 组（每组 2 種目）的并集。
    优先读 `search.update_groups`；没配就退回 `search.kinds`。
    """
    s = cfg.get("search", {}) or {}
    cr = cfg.get("crawl", {}) or {}
    base = _norm_groups(s.get("update_groups") or s.get("kinds"))
    if cr.get("split_subtypes", True):
        flat: list[dict] = [{"kind": g["kind"], "subtypes": [sub]}
                            for g in base for sub in (g.get("subtypes") or [])]
    else:
        flat = [{"kind": g["kind"], "subtypes": list(g.get("subtypes") or [])}
                for g in base]
    if cr.get("split_new_changed", False):
        out: list[dict] = []
        for g in flat:
            for axis in ("新增", "変更"):
                out.append(dict(g, axis=axis))
        return out
    return flat


def _groups_from_opts(opts: dict, cfg) -> list[dict]:
    """「指定日期下载」用：解析出要跑哪几组 (kind, 種目)。

    优先级：前端传的 groups（多选面板）→ 单个 kind+subtypes（老调用）→ 配置里的更新组。
    这样「指定日期下载 / 正式下载」与「更新」口径一致，都会覆盖那 6 个種目。
    """
    raw = opts.get("groups")
    gs = _norm_groups(raw) if raw else []
    if not gs:
        kind = str(opts.get("kind") or "").strip()
        subs = [s for s in _normalize_subs(opts) if s]
        if kind and subs:
            gs = _norm_groups([{"kind": kind, "subtypes": subs}])
    return gs or _update_groups(cfg)


def _geo_cond(cfg, kind: str) -> str:
    """物件種別 → 用来带出「大阪府/大阪市」地区基线的保存条件编号。

    【非显然】REINS 没有独立的 大阪府 下拉，都道府県名 输入框默认禁用，
    「大阪全市」只能靠套用「保存した検索条件」得到 —— 所以这里必须借一个保存条件，
    但它**只提供地区**，物件種別/種目随后会被覆盖掉（见 _fill_subtype_slots）。
    """
    m = (cfg.get("search", {}) or {}).get("geo_condition_by_kind") or {}
    return str(m.get(kind) or m.get("default") or "02")


def _collect_from_results(page, store, cfg, sel, log, max_items, max_pages, delay,
                          collected, sink=None, worker=None):
    """从已点完「検索」的结果页开始：翻页 + 解析列表 + 进详情抓全字段/PDF + 入库。
    供 run_round_options 使用（与 _live_items 的翻页/详情循环逻辑一致）。

    max_items 是**本次调用自己的配额**（不是 collected 的全局上限）——
    多種目逐个跑时，每个種目都能各自翻到底，不会被前一个種目挤占。

    sink 非空时边抓边落库（v1.2.2）；返回 (本次条数, 是否到单轮时长上限)。

    v1.4.0：`worker` 传入后台 PDF 下载器后，PDF 也走后台（主线程不必干等），
    补齐 v1.3.0 留下的缺口 —— 这条路径以前一直是同步等下载。
    """
    max_items = int(max_items)
    max_pages = int(max_pages)
    _cr = cfg.get("crawl", {})
    _dl = _cr.get("delay_long_range_seconds")
    delay_long = [float(x) for x in _dl] if _dl else None
    delay_long_prob = float(_cr.get("delay_long_prob", 0.0) or 0.0)
    max_minutes = float(_cr.get("max_minutes_per_group",
                               _cr.get("max_minutes_per_run", 0)) or 0)
    root_dir = Path(cfgmod.paths(cfg)["root"])
    want_pdf = bool(cfg.get("download", {}).get("pdf", True))
    t0 = time.monotonic()
    added = 0                     # 本次调用真正收集到的条数（相对配额）
    truncated = False
    timed_out = False
    for pg in range(1, max_pages + 1):
        rows = page.locator(sel["result_rows"])
        n = rows.count()
        for i in range(n):
            if added >= max_items:
                truncated = True
                break
            row = rows.nth(i)
            rec = _parse_list_row(row)
            no = rec.get("property_no")
            if not no:
                continue
            existing = store.get_property(no)
            if existing is None or not existing["detail_json"]:
                # v1.4.0：详情与 PDF 分别判断，PDF 交给后台 worker（主线程继续翻页）
                # v1.8.3（冲突3）：以详情页为准，移除列表图标门禁
                need_pdf = bool(want_pdf) and not _pdf_exists(root_dir, no)
                try:
                    det = _fetch_detail(page.context, page, row, no, sel, cfg,
                                        pdf_worker=worker, need_pdf=need_pdf)
                    if det:
                        rec.update(det)
                    log("  ✓ " + no + " " + str(rec.get("price", "")) + " "
                        + str(rec.get("address", "")[:18])
                        + (" [PDF→后台]" if (need_pdf and rec.get("pdf_url")) else ""))
                except SessionExpired:
                    raise
                except Exception as e:  # noqa: BLE001
                    log("  ✗ " + no + " 详情失败：" + type(e).__name__ + ": " + str(e))
                # 回收后台已下完的 PDF：**立即记账、立即落盘**（确定性存储）
                for _no, _p, _ms, _err in (worker.drain() if worker else []):
                    if _p:
                        store.set_pdf(_no, _p)
                        log("  ↓ PDF 已落盘 " + _no + "（%.1fs）" % (_ms / 1000.0))
                    else:
                        log("  ✗ PDF 失败 " + _no + "：" + str(_err))
                gap = random.uniform(delay[0], delay[1])
                if delay_long and random.random() < delay_long_prob:
                    gap = random.uniform(delay_long[0], delay_long[1])
                time.sleep(gap)
            else:
                log("  · 已存在 " + no + "（仅刷新列表字段）")
            if sink is None or sink.add(rec):
                collected.append(rec)
            added += 1
            if max_minutes and (time.monotonic() - t0) > max_minutes * 60:
                timed_out = True
                break
        if added >= max_items:
            truncated = True
            break
        if timed_out:
            break
        nxt = page.locator(sel["next_page"]).first
        if nxt.count() == 0 or not nxt.is_enabled():
            break
        nxt.click()
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(1200)
    if truncated:
        log("⚠ 本次已到配额上限 " + str(max_items) + " 条，可能还有未下载的（可提高 crawl.max_items_per_run）")
    if timed_out:
        log("⏱ 已到单轮时长上限（" + str(int(max_minutes)) + " 分钟）：先收尾入库，剩下留给下一轮")
    return added, timed_out


def _prev_day_needs_backfill(store, cfg, today: str, log=None) -> tuple[str, str] | None:
    """（v1.8.2 · D2）判断「前一日」的日期是否需要补搜，需要就返回 (日期, 原因)。

    【为什么要补】当日日期同步只在**跑轮的时候**把「今天」灌进去。如果某一天的尾巴断了
    （最后一轮跑早了 / 整轮失败 / 机器没开 / 23:00 后平台又登録了房源），那一批房源的
    reg_date_iso / chg_date_iso 就**永久缺失**（详情页在"仅刷新列表字段"路径下不再重抓），
    查询页按日期筛就会一直少这批数据 —— 静默、不可自愈。

    【只读本地库，不额外发请求】三条判据（任一成立才补）：
      ① 前一日本地一条「登録或変更=前日」都没有 → 那天像没同步过；
      ② 前一日最后一次**成功**轮次结束得早于 cutoff（默认 20:00；调度窗 07:00–23:00）
         → 尾巴可能断了；
      ③ 已经补过就不重复补 —— 用 watermark('date_backfill').last_no 记「最近补过哪天」。
    ⚠ 设计取向是**宁可少补**：判据不成立就不补（不每天平白多打 12 次 REINS 检索）。
    """
    _lg = log or (lambda *_a, **_k: None)
    try:
        _d0 = datetime.strptime(today, "%Y-%m-%d")
    except Exception:                                        # noqa: BLE001
        return None
    prev = (_d0 - timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff = str((cfg.get("crawl", {}) or {}).get("sync_dates_prev_day_cutoff") or "20:00")

    # ③ 已补过 → 不重复（一轮里被调多次也不会重复打请求）
    try:
        wm = store.get_watermark("date_backfill")
        if wm and (wm["last_no"] or "") == prev:
            return None
    except Exception:                                        # noqa: BLE001
        pass

    n_prev = 0
    last_ok = ""
    try:
        con = sqlite3.connect(str(store.db_path))
        try:
            n_prev = con.execute(
                "select count(*) from properties where reg_date_iso=? or chg_date_iso=?",
                (prev, prev)).fetchone()[0]
            # 只看「跑起来的轮次」（finished_at 有值且 status=ok）
            _r = con.execute(
                "select max(finished_at) from runs where status='ok' and finished_at like ?",
                (prev + "%",)).fetchone()
            last_ok = (_r[0] or "") if _r else ""
        finally:
            con.close()
    except Exception as e:                                   # noqa: BLE001
        _lg("· 前日补齐判据读取失败（跳过补齐，不影响本轮）：%s: %s" % (type(e).__name__, e))
        return None

    if n_prev == 0:
        return prev, "本地前一日 0 条（那天看起来没同步过）"
    if not last_ok:
        return prev, "前一日没有任何成功轮次"
    _hhmm = last_ok[11:16] if len(last_ok) >= 16 else ""
    if _hhmm and _hhmm < cutoff:
        return prev, "前一日最后一轮 %s 结束（早于 %s，尾巴可能断了）" % (_hhmm, cutoff)
    return None


def sync_today_dates(store, cfg, log, today=None, progress_cb=None, run_id=None,
                     backfill_prev: bool | None = None) -> dict:
    """（v1.5.11）把「今日(登録日 或 変更日)」平台物件日期灌进本地 DB。

    【为什么需要】REINS 列表行**没有日期字段**，且已有 detail_json 的物件在更新轮里
    不再重抓详情（_collect_from_results 的「仅刷新列表字段」）——库里已有物件的平台日期
    (reg_date_iso/chg_date_iso) 因此永不刷新。查询页「日期=今天」于是只出 3 条，
    而平台当天真实是 866 条（2026-09-15 复盘，勇哥报「金额数量不对」）。

    原理：REINS 支持按「登録年月日 / 変更年月日 = 指定日」检索，搜出来的每条物件其对应
    日期**必然**等于该检索日，故直接按检索条件写日期，完全不依赖详情页。
    ⚠ 两日期排**必须分两次检索再合并**（REINS 同屏多排 = AND 交集，不是 OR）。

    自包含：自己开浏览器；可被 run_round 末尾 / 每日定时 / 手动触发调用。
    返回 {"searched","collected","updated","new","reg","chg","union"}。
    """
    log = progress_cb or log or (lambda *_a, **_k: None)
    if today is None:
        # ⚠ 本模块是 `from datetime import datetime`（类），不是 `import datetime`（模块）——
        #   写成 datetime.datetime.now() 会 AttributeError（2026-09-15 17:05 真机首发命中，
        #   此前该函数从未被真正执行过：一次性同步脚本是自己实现检索的）。
        today = datetime.now().strftime("%Y-%m-%d")
    sel = cfg["selectors"]
    cr = cfg.get("crawl", {}) or {}
    max_pages = int(cr.get("max_pages", 10))
    groups = _update_groups(cfg)
    date_fields = ("登録年月日", "変更年月日")
    col_of = {"登録年月日": "reg_date_iso", "変更年月日": "chg_date_iso"}
    BASE_KEYS = ("property_subtype", "price", "address", "building_name",
                 "exclusive_area", "layout", "line_station", "built_year_month",
                 "floor", "has_photo", "has_floorplan", "has_map")
    log("▶ 今日日期同步（" + today + "）：覆盖 " + str(len(groups)) + " 组 × 2 日期 = "
        + str(len(groups) * 2) + " 次检索")

    # ---- v1.8.2（D2）：前日补齐（只在该补的时候才补，正常每天 0 次） ----
    days: list[str] = [today]
    prev_fixed: str | None = None
    if backfill_prev is None:
        backfill_prev = bool(cr.get("sync_dates_backfill_prev_day", True))
    if backfill_prev:
        _need = _prev_day_needs_backfill(store, cfg, today, log=log)
        if _need:
            prev_fixed = _need[0]
            days = [prev_fixed, today]
            log("· 前日补齐：本轮先补 " + prev_fixed + " —— " + _need[1])
        else:
            log("· 前日补齐：判据不成立，不补（省 12 次检索）")
    n_axis_per_day = len(groups) * len(date_fields)
    log("· 本轮日期同步排期：" + " → ".join(days)
        + "（共 %d 次检索）" % (n_axis_per_day * len(days)))

    collected: dict[str, dict] = {}
    inlined_total = 0             # v1.9.8（P1）：跨组累计「内联补详情」条数
    # v1.5.15：发射「平台日期同步」阶段事件（前端 .phaser 面板实时显示）
    if run_id is not None:
        try:
            store.save_crawl_state(run_id, phase="date_sync")
        except Exception:                            # noqa: BLE001
            pass
    auth = Auth(cfg, cfgmod.paths(cfg)["session"])
    with _sync_playwright() as p:
        browser = auth.launch(p, headless=cfg["browser"].get("headless", False))
        # 统一入口：会话失效会自动用本地账号重登一次
        ctx, page = auth.open_authed_page(browser, log=log)
        _mask_webdriver(ctx)          # v1.4.0：抹掉自动化指纹（webdriver）
        # v1.9.8（P1）：概览页日期同步也内联补详情/PDF（与设置页统一，详情回主链一等公民）
        root_dir = Path(cfgmod.paths(cfg)["root"])
        want_pdf = bool(cfg.get("download", {}).get("pdf", True))
        worker = None
        if want_pdf and cr.get("pdf_background", True):
            try:
                ua = page.evaluate("navigator.userAgent") or ""
                ck = "; ".join(("%s=%s" % (c.get("name"), c.get("value")))
                               for c in ctx.cookies() if c.get("name"))
                n_inflight = max(1, int(cr.get("pdf_max_inflight", 2)))
                worker = _PdfWorker(root_dir / "attachments", ck, ua, log,
                                    max_inflight=n_inflight)
                log("· PDF 后台下载已开启（最大在途 %d）" % n_inflight)
            except Exception as _we:
                log("· PDF 后台下载启动失败 → 退回同步：%s: %s" % (type(_we).__name__, _we))
                worker = None
        nav_done = False
        # v1.8.2（D2）：把「日期 × 组 × 日期轴」拍平成一条作业队列——
        #   补前日时 days=[前日, 今天]，正常时 days=[今天]（行为与旧版逐字等价）。
        #   拍平还有一个好处：进度横幅的「第 N/M」是**整个排期**的序号，不再每天从 1 重数。
        _jobs = [(day, gi, g, field)
                 for day in days
                 for gi, g in enumerate(groups, 1)
                 for field in date_fields]
        for _seq, (day, gi, g, field) in enumerate(_jobs, 1):
            col = col_of[field]
            if nav_done:
                page.goto(cfg["site"]["search_url"],
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)
            nav_done = True
            _check_maintenance(page, log)
            _expand_search_panel(page, sel)
            page.wait_for_timeout(800)
            _dismiss_modal(page)
            # 每次只设一个日期排（另一排由 _apply_manual_conditions 复位成「指定なし」）
            opts = {"date": day, "date_types": [field],
                    "kind": g["kind"], "subtypes": list(g["subtypes"])}
            try:
                _apply_manual_conditions(page, sel, opts, log, cfg)
            except _GeoFailed as _ge:
                log("⚠ 跳过本组（地区基线直录失败，已重试）：" + str(_ge)[:160])
                continue
            _dismiss_modal(page)
            page.locator(sel["search_button"]).first.click()
            page.wait_for_load_state("networkidle", timeout=40000)
            page.wait_for_timeout(2500)
            _dismiss_modal(page)
            total = _read_total(page, sel, log=log)
            # v1.6.3：先打条件横幅，确认 当日/地区/房屋类型
            # v1.8.2：seq/total 改用**整个排期**的序号（补前日时不会每天从 1 重数）
            _log_search_banner(log, seq=_seq, total=len(_jobs),
                               kind=g["kind"], subtypes=list(g["subtypes"]), cfg=cfg,
                               date_fields=[field],
                               date_range="当日 " + str(day),
                               round_tag=("前日补齐 " + day)
                                         if (prev_fixed and day == prev_fixed)
                                         else "当日日期同步")
            log("—— 日期同步 " + str(_seq) + "/" + str(len(_jobs)) + " "
                + day + " " + g["kind"] + "×" + "·".join(g["subtypes"]) + " / " + field
                + " → " + (total or "未知"))
            got = 0
            inlined = 0
            rows_loc = page.locator(sel["result_rows"])
            for pg in range(1, max_pages + 1):
                rows = _bulk_list_rows(page, sel["result_rows"])
                for i, r in enumerate(rows):
                    rec = r.get("rec") or {}
                    no = rec.get("property_no")
                    if not no:
                        continue
                    e = collected.setdefault(no, {"_base": {}})
                    e[col] = day
                    e["detail_href"] = r.get("href") or ""
                    for k in BASE_KEYS:
                        v = rec.get(k)
                        if v not in (None, "") and k not in e["_base"]:
                            e["_base"][k] = v
                    # v1.9.8（P1）：每行与本地详情页比对——有则跳过，无则内联补详情+PDF
                    existing = store.get_property(no)
                    if _needs_detail(existing):
                        need_pdf = bool(want_pdf) and not _pdf_exists(root_dir, no)
                        try:
                            det, _touched = _fetch_detail_inline(
                                ctx, page, rows_loc, i, no, sel, cfg,
                                pdf_worker=worker, want_pdf=need_pdf, log=log)
                            if det:
                                det["property_no"] = no
                                try:
                                    pipeline.ingest([det], store, cfg, run_id)
                                    inlined += 1
                                    log("  ✓ " + no + " 内联补详情"
                                        + (" [PDF→后台]" if (need_pdf and det.get("pdf_url")) else ""))
                                except Exception as _ie:
                                    log("  ✗ " + no + " 内联写库失败："
                                        + type(_ie).__name__ + ": " + str(_ie))
                            else:
                                log("  · " + no + " 内联详情为空（下轮重试）")
                        except SessionExpired:
                            raise
                        except Exception as _ex:
                            log("  ✗ " + no + " 内联详情失败："
                                + type(_ex).__name__ + ": " + str(_ex))
                        # 回收后台已下完的 PDF（立即记账、立即落盘）
                        if worker is not None:
                            for _no, _p, _ms, _err in worker.drain():
                                if _p:
                                    store.set_pdf(_no, _p)
                                    log("  ↓ PDF 已落盘 " + _no + "（%.1fs）" % (_ms / 1000.0))
                                else:
                                    log("  ✗ PDF 失败 " + _no + "：" + str(_err))
                        gap = random.uniform(3.0, 5.0)
                        time.sleep(gap)
                    else:
                        log("  · 已存在 " + no + "（仅刷新列表字段）")
                    got += 1
                nxt = page.locator(sel["next_page"]).first
                if nxt.count() == 0 or not nxt.is_enabled():
                    break
                nxt.click()
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(1200)
            inlined_total += inlined
            log("   本组收集 " + str(got) + " 条 / 内联补详情 " + str(inlined) + " 条")
            time.sleep(random.uniform(4, 8))
        # v1.9.8（P1）：收尾回收后台 PDF 下载器（与阶段B 同款，避免泄漏/丢尾）
        if worker is not None:
            try:
                worker.join(timeout=180)
                for _no, _p, _ms, _err in worker.drain():
                    if _p:
                        store.set_pdf(_no, _p)
                        log("  ↓ PDF 已落盘 " + _no + "（%.1fs）" % (_ms / 1000.0))
                    else:
                        log("  ✗ PDF 失败 " + _no + "：" + str(_err))
            except Exception as _we2:
                log("· PDF 收尾异常：" + type(_we2).__name__ + ": " + str(_we2))
        browser.close()

    # ---- 写库（update 已有 / insert 新建，都只动日期列 + 补缺失基础字段）----
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(store.db_path))
    con.execute("PRAGMA busy_timeout=30000")
    cur = con.cursor()
    upd = ins = 0
    # v1.5.14 双保险：写 ISO 的同时把原文（registration_date/change_date）也写成今天，
    # 这样 8765 重启跑 backfill_platform_dates（只在"有原文"才回算该列）会算出同一天，
    # 绝不会把平台当日日期洗成 NULL / 旧值。
    RAW_OF = {"reg_date_iso": "registration_date", "chg_date_iso": "change_date"}
    for no, e in collected.items():
        base = e.get("_base", {})
        cols = {k: v for k, v in e.items() if k != "_base"}
        for _c, _raw in RAW_OF.items():
            if _c in cols:
                cols[_raw] = cols[_c]
        row = cur.execute("select property_no from properties where property_no=?", (no,)).fetchone()
        if row:
            sets = ", ".join(c + "=?" for c in cols)
            cur.execute("update properties set " + sets + " where property_no=?",
                        list(cols.values()) + [no])
            upd += 1
        else:
            keys = ["property_no"] + list(base.keys()) + list(cols.keys()) \
                   + ["first_seen_at", "last_seen_at"]
            vals = [no] + list(base.values()) + list(cols.values()) + [now, now]
            ph = ", ".join("?" for _ in keys)
            cur.execute("insert into properties (" + ", ".join(keys) + ") values (" + ph + ")", vals)
            ins += 1
    con.commit()

    def _cnt(sql, params):
        return cur.execute(sql, params).fetchone()[0]
    n_reg = _cnt("select count(*) from properties where is_active=1 and reg_date_iso=?", (today,))
    n_chg = _cnt("select count(*) from properties where is_active=1 and chg_date_iso=?", (today,))
    n_union = _cnt("select count(*) from properties where is_active=1 and (reg_date_iso=? or chg_date_iso=?)",
                   (today, today))
    con.close()
    # v1.8.2（D2）：补过前日就记一笔水位 —— 同一天后续的轮次不再重复补（省 12 次检索）
    if prev_fixed:
        try:
            store.set_watermark("date_backfill", prev_fixed)
        except Exception as _e:                              # noqa: BLE001
            log("· 前日补齐水位写入失败（下次可能重复补一次，不影响数据）：%s" % _e)
    log("· 日期同步完成：搜集 " + str(len(collected)) + " / 更新 " + str(upd)
        + " / 新建 " + str(ins) + " / **内联补详情 " + str(inlined_total) + "**"
        + "；本地[今 登録或変更]=" + str(n_union)
        + "（reg=" + str(n_reg) + " / chg=" + str(n_chg) + "）"
        + ("；本轮已补前日 " + prev_fixed if prev_fixed else ""))
    return {"searched": n_axis_per_day * len(days), "collected": len(collected),
            "updated": upd, "new": ins, "inlined": inlined_total,
            "reg": n_reg, "chg": n_chg, "union": n_union,
            "property_nos": list(collected.keys()),
            "days": list(days), "backfill": prev_fixed}


# ===================================================================
# v1.9.1（解耦阶段B）：独立「详情+PDF 补抓」环节。
# 背景：v1.8.4 把 main_round 默认关（防误标下架），但阶段B(详情+PDF)被一并关掉，
#       导致「一轮下载」只下列表壳、详情永不补 → 前端长期挂「详情待补」。
# 本函数把阶段B 从 main_round 解耦：无论主轮开不开，run_round 降级分支都调它，
# 让一轮默认就完整；阶段C(全期間下架基线)仍由 main_round 门控（默认关）。
# 复用 _live_items 同一套 auth + _fetch_detail machinery；href 来自 sync_today_dates
# 落库的 detail_href（绝不猜 URL、绝不写脏）。范围：is_active 且 detail_href 非空
# 且 (detail_json 空 或 本地缺 PDF)，按 last_seen_at 倒序取前 N（默认 300），逐轮渐进清积压。
# ===================================================================
# ===================================================================
# v1.9.1（REINS 番号検索后端登录式 / Task B）：解决真机反馈「番号没录入/没点検索/未登录」。
# 纯前端 window.open 因跨域无法代填 REINS 搜索框、也共享不了登录态，
# 故改为后端用与下载相同的 Auth 会话（自动重登）代操作：
#   打开番号検索页 → 填番号 → 点検索 → 截图结果区 → 回传应用内展示。
# ⚠ 番号検索页的 URL / 输入框 / 検索按钮 / 结果容器 选择器**走 config.yaml**
#   （site.bukken_search_url + selectors.bukken_search_*）而非硬编码 ——
#   需勇哥在真实 DOM 上取证后填（门禁②）。任一未配置即返回 ok:False + 明确提示，
#   绝不发带猜 selector 的请求（避免写脏/误点）。
# ===================================================================
def reins_bukken_search(cfg, log, nos) -> dict:
    """后端登录式「物件番号検索」：填号(**可多个**) + 点最下面「検索」 + 截图。

    回传 {ok, image_url, reins_url, filled}。
    v1.9.1：支持多个番号 —— 依次填进「物件番号１/２/…」多个框（勇哥：多个编号分别
    填到下面的多个框），再点最下面的「検索」。nos 可为字符串或列表。
    选择器全部走 config.yaml（site.bukken_search_url + selectors.bukken_search_inputs/
    button/result），**绝不硬编码猜 locator**（门禁②）；未配置或匹配不到输入框 → 明确报错。
    """
    if isinstance(nos, str):
        nos = [nos]
    nos = [str(x).strip() for x in (nos or []) if x is not None and str(x).strip()]
    if not nos:
        return {"ok": False, "error": "番号为空"}
    sel = (cfg.get("selectors") or {})
    site = (cfg.get("site") or {})
    url = (site.get("bukken_search_url") or "").strip()
    # 优先多框键 bukken_search_inputs；兼容旧单框键 bukken_search_input
    inp = (sel.get("bukken_search_inputs") or sel.get("bukken_search_input") or "").strip()
    btn = (sel.get("bukken_search_button") or "").strip()
    res = (sel.get("bukken_result") or "").strip()
    if not (url and inp and btn):
        return {"ok": False, "error": "番号検索 URL/输入框/検索按钮 未配置"
                                  "（待真机：在 config.yaml 填 site.bukken_search_url 与"
                                  " selectors.bukken_search_inputs/button 的真实 DOM）"}
    root_dir = Path(cfgmod.paths(cfg)["root"])
    shots_dir = root_dir / "bukken_shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]", "", "_".join(nos))[:40]
    fname = "bukken_%s.png" % (safe or "x")
    fpath = shots_dir / fname
    filled = 0
    zeros = ""            # v1.9.20：0 件页告示原文（有值 = REINS 真回 0 件）
    auth = Auth(cfg, cfgmod.paths(cfg)["session"])
    with _sync_playwright() as p:
        # ⚠ v1.9.20（勇哥 2026-09-19 真机反馈）：**强制无头**。
        #   以前跟随 cfg.browser.headless(=false) → 点一次按钮就真的弹出一个 Edge 窗口，
        #   搜完 browser.close() 把它关掉；真机现象「另开一个浏览器窗口、看到结果几秒就没了」。
        #   番号検索的结果一律回本页弹窗（永不自关），所以这里绝不能再有可见窗口。
        browser = auth.launch(p, headless=True)
        ctx, page = auth.open_authed_page(browser, log=log)
        _mask_webdriver(ctx)
        log("✓ 阶段Bukken 会话有效，进入番号検索（%d 个番号：%s）" % (len(nos), "、".join(nos)))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
            _check_maintenance(page, log)
            boxes = page.locator(inp)                 # 一次匹配「物件番号１..N」多个框
            n_box = boxes.count()
            if n_box < 1:
                # v1.9.10：番号検索页是 Nuxt SPA（客户端渲染），输入框可能晚于
                # domcontentloaded 才挂上 → 首次 count=0 时补等一次，仍 0 才判不匹配。
                page.wait_for_timeout(3000)
                n_box = boxes.count()
            if n_box < 1:
                return {"ok": False, "error": "没找到物件番号输入框"
                                          "（选择器不匹配，待真机核对 selectors.bukken_search_inputs=%r）"
                                          % inp}
            log("· 番号検索页输入框命中 %d 个（选择器 %s）" % (n_box, inp))
            mismatched = []
            for i, one in enumerate(nos):
                if i >= n_box:
                    log("· 番号数(%d)超过输入框数(%d)，多余的忽略" % (len(nos), n_box))
                    break
                tb = boxes.nth(i)                      # 物件番号１、２、…
                # ⚠ v1.9.20（真机「0 件」根因）：**必须逐键真实键盘输入**。
                #   番号框是 PrimeVue 的 p-textbox-type-digit（数字掩码组件）。旧版用
                #   fill() 直接赋 DOM 值、绕过键盘事件 → 掩码组件不把它写进框架状态 →
                #   REINS 实际拿「空条件」去检索 → 真机现象：框里看得见号、回读也一致，
                #   结果却是「検索結果が0件です」（勇哥 2026-09-19 截图 + server.log 7 次）。
                #   回读只能证明 DOM 有值、证明不了框架认账，故改成真键盘输入。
                try:
                    tb.click(timeout=8000)             # 掩码组件要先拿到真实焦点
                except Exception:  # noqa: BLE001
                    pass
                try:
                    tb.clear(timeout=5000)
                except Exception:  # noqa: BLE001
                    pass
                tb.press_sequentially(one, delay=40, timeout=20000)
                # v1.9.10 门禁③：**填完必须回读**——回读值 ≠ 期望值即判失败，绝不静默计数
                # （旧版只 filled += 1，无法证明真录进去了，正是「点了按钮编号没进去」这类
                #   假成功的盲区）。
                try:
                    got = (tb.input_value(timeout=5000) or "").strip()
                except Exception as _re:  # noqa: BLE001
                    got = "<回读异常:%s>" % type(_re).__name__
                if got != one.strip():
                    mismatched.append("框#%d 期望%s 实得%s" % (i + 1, one.strip(), got))
                else:
                    filled += 1
            if mismatched:
                return {"ok": False, "error": "回读不一致（编号未真正录入）："
                                          + "；".join(mismatched[:5])}
            log("· 已填入 %d 个物件番号（回读一致）→ 点最下面的「検索」" % filled)
            page.locator(btn).last.click(timeout=8000)  # 最下面的「検索」
            page.wait_for_load_state("networkidle", timeout=40000)
            page.wait_for_timeout(2000)
            # v1.9.20：**结果页要自证**——REINS 回 0 件时明确报错并把番号写进日志，
            #   不再把一张「0件」截图当成功回传（那样真机排查极易误判为"数据给错了"）。
            #   复用 v1.9.5 的真机实证识别器 —— 它能区分「0 件」与「500件を超えています」。
            zeros = _read_zero_note(page)
            if zeros:
                log("· 番号検索结果页告示：" + zeros)
            try:
                if res:
                    page.locator(res).first.screenshot(path=str(fpath))
                else:
                    page.screenshot(path=str(fpath))
            except Exception as _e:
                log("· 阶段Bukken 截图失败，改全页截图：" + type(_e).__name__)
                try:
                    page.screenshot(path=str(fpath))
                except Exception:
                    pass
            reins_url = page.url
        finally:
            try:
                browser.close()
            except Exception:
                pass
    if not fpath.exists():
        return {"ok": False, "error": "截图未生成（REINS 可能未返回结果或选择器不匹配）"}
    if zeros:
        log("✗ 阶段Bukken REINS 返回 0 件（番号：%s）；若在 REINS 手工能搜到同一番号，"
            "则说明是输入方式/默认条件差异，不是数据错" % "、".join(nos))
        return {"ok": False, "zero": True, "image_url": "/bukken_shot/" + fname,
                "reins_url": reins_url, "filled": filled,
                "error": "REINS 返回 0 件（番号 %s）" % "、".join(nos)}
    return {"ok": True, "image_url": "/bukken_shot/" + fname, "reins_url": reins_url,
            "filled": filled}


def _fetch_detail_by_no(ctx, page, property_no: str, sel, cfg,
                        pdf_worker=None, need_pdf: bool = True, log=None,
                        store=None) -> dict | None:
    """v1.9.6：阶段B 用「物件番号検索」打开详情页抓全字段 + PDF。

    REINS 列表行「詳細」是 <button> 无直链 → detail_href 全库恒空（jproperty.db 实测
    2560 在架、非空=0），旧阶段B「必须带 href 才补」永远 0 候选、详情永远补不上。
    唯一无 URL 的进详情方式 = 番号検索（site.bukken_search_url +
    selectors.bukken_search_inputs/button，全部走 config.yaml，绝不猜 locator）。
    流程：goto 番号検索页 → 填番号 → 点検索 → 点结果行「詳細」→ 解析 + PDF。
    任一解析异常返回 None（绝不写脏）；SessionExpired 原样上抛。
    """
    log = log or (lambda *_a, **_k: None)

    # v1.9.81 G-3：终局跳过 —— 该番号已连续 N 次「0 件」（成約済/取り下げ等），
    #   后续轮次**不再发起检索**。原实现每轮都重查，实测白跑 ≈10s/号（如
    #   300140791556 / 100140789119 / 100140779224）。勇哥 09-25 定：跑 2 次后结束不再跑。
    def _miss(reason: str) -> None:
        """记一次「0 件 / 无結果」；达阈值(2)即置终局跳过。"""
        if store is None:
            return
        try:
            # §17⑧：番号检索按次留痕（此处是**真 0 件**，不是「读不到」，故记 0 而非 NULL）
            store.log_search(kind="bukken", cond=property_no, result_count=0,
                             hit_limit=False, elapsed_s=0.0)
            r = store.no_miss_bump(property_no, threshold=2)
            if r["terminal"]:
                log("  · " + property_no + " " + reason
                    + "（连续 %d 次 → 已标记终局跳过，后续不再检索）" % r["count"])
        except Exception:
            pass

    if store is not None:
        try:
            if store.no_miss_terminal(property_no):
                log("  · " + property_no + " 终局跳过（番号検索连续 0 件已达阈值，不再检索）")
                return None
        except Exception:
            pass

    site = (cfg.get("site") or {})
    url = (site.get("bukken_search_url") or "").strip()
    inp = (sel.get("bukken_search_inputs") or "").strip()
    btn = (sel.get("bukken_search_button") or "").strip()
    if not (url and inp and btn):
        return None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)
        _check_maintenance(page, log)
        boxes = page.locator(inp)
        if boxes.count() < 1:
            log("  · " + property_no + " 番号検索 输入框未匹配，跳过")
            return None
        boxes.nth(0).fill(property_no, timeout=8000)
        page.locator(btn).last.click(timeout=8000)
        page.wait_for_load_state("networkidle", timeout=40000)
        page.wait_for_timeout(1500)
        # 真机取证（2026-09-21，forensics_bug2_dom.py）：番号検索返回
        # 「検索結果が0件です」时结果页 0 行、根本没有「詳細」按钮 → 原代码
        # 会卡 8000ms 超时、每轮误报"打开详情失败"。根因 = 该物件已 成約済/取り下げ
        # （不在公开检索池，番号検索本就查不到），不是 locator 写错（硬门禁②已看真实 DOM）。
        # 修法 = 先判 0 件 / 结果 0 行 / 无「詳細」按钮，直接优雅跳过，不再卡超时。
        _html0 = (page.content() or "")
        if ("検索結果が0件" in _html0) or ("0件です" in _html0):
            log("  · " + property_no + " 番号検索 0件（成約済/取り下げ等，无詳細可补，跳过）")
            _miss("番号検索 0件")
            return None
        _rows0 = sel.get("result_rows") or "div.p-table-body-row"
        try:
            if page.locator(_rows0).count() == 0:
                log("  · " + property_no + " 番号検索 结果 0 行（无詳細可补，跳过）")
                _miss("结果 0 行")
                return None
        except Exception:
            pass
        # 点「詳細」前确认按钮存在，避免对不存在元素卡 8000ms 超时
        _det = page.locator(sel["detail_button"])
        if _det.count() == 0:
            log("  · " + property_no + " 番号検索 结果页无「詳細」按钮（跳过）")
            _miss("无「詳細」按钮")
            return None
        # 结果列表点「詳細」（REINS 是 <button>，可能同标签或开新标签）
        _det.first.click(timeout=8000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.wait_for_timeout(900)
        dp = page
        if ("物件詳細" not in (page.title() or "")) and ("GBK003100" not in (page.url or "")):
            others = [p for p in ctx.pages if p is not page]
            if others:
                dp = others[-1]
                dp.set_default_timeout(cfg["browser"].get("timeout_ms", 30000))
        rec = _parse_detail(dp, property_no, cfg)
        rec["property_no"] = property_no
        # v1.9.81 G-3：检索成功 → 清零终局台账（房源可能重新上架，不能被永久跳过）
        if store is not None:
            try:
                store.no_miss_clear(property_no)
            except Exception:
                pass
        if need_pdf and bool(cfg.get("download", {}).get("pdf", True)):
            rec["pdf_url"] = _pdf_url_of(dp, sel)
            try:
                rec["source_url"] = dp.url
            except Exception:
                pass
            if pdf_worker is not None and rec.get("pdf_url"):
                pdf_worker.submit(property_no, rec["pdf_url"], referer=rec.get("source_url", ""))
            elif not rec.get("pdf_url"):
                # 详情页図面也是 <button>図面参照</button> 无 href → 点按钮下载
                _b = _download_pdf_by_click(dp, log=log)
                if _b:
                    rec["pdf_bytes"] = _b
        _pc = _photo_count_of(dp)
        if _pc is not None:
            rec["image_count"] = _pc
            rec["has_photo"] = 1 if _pc > 0 else 0
        return rec
    except SessionExpired:
        raise
    except Exception as e:
        log("  · " + property_no + " 番号検索 打开详情失败：" + type(e).__name__ + ": " + str(e))
        return None
    finally:
        # 回番号検索结果页，下轮迭代会重新 goto 番号検索页，这里只是兜底清理
        try:
            page.go_back(wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(400)
        except Exception:
            pass


def _backfill_via_inline(store, cfg, log, run_id, cand) -> dict:
    """v1.9.6 兜底：番号検索选择器未配置时，复用「指定日期下载」(run_round_options) 的
    行内点詳細路径补详情——这条路径今天已实补 400 条（已证可用），不依赖任何新选择器。
    按待补壳的登録/変更日期分组，每组发一次指定日期下载（覆盖全部 6 種目）；
    run_round_options 自带会话+落库、幂等（已补的会跳过）。"""
    from datetime import datetime, timedelta
    dates = set()
    for r in cand:
        r = dict(r)
        for col in ("reg_date_iso", "chg_date_iso"):
            v = (r.get(col) or "").strip()
            if v:
                dates.add(v)
    if not dates:
        dates = {datetime.now().strftime("%Y-%m-%d")}
    # 仅取最近 14 天，避免历史海量日期把一轮拖死（壳都是近期同步产生的）
    cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    dates = sorted(d for d in dates if d >= cutoff)
    if not dates:
        dates = [datetime.now().strftime("%Y-%m-%d")]
    log("· 阶段B 兜底（行内点詳細）：待补壳跨 %d 个日期，将逐日调用指定日期下载：%s"
        % (len(dates), "、".join(dates)))
    total = 0
    for d in dates:
        try:
            st = run_round_options(store, cfg,
                                   {"date": d,
                                    "date_types": ["登録年月日", "変更年月日"]},
                                   progress_cb=log)
            total += int(st.get("fetched", 0) or 0)
            log("  · 日期 %s 补详情 %d 条" % (d, int(st.get("fetched", 0) or 0)))
        except SessionExpired:
            raise
        except Exception as e:
            log("  ✗ 日期 %s 兜底补详情失败：" % d + type(e).__name__ + ": " + str(e))
    log("· 阶段B 兜底完成：共补详情 %d 条 / 跨 %d 天（模式：行内点詳細，复用指定日期下载）"
        % (total, len(dates)))
    return {"fetched": total, "skipped": 0, "mode": "inline_fallback"}


def _backfill_details_pdfs(store, cfg, log, run_id=None, _inner=False) -> dict:
    """后台补「详情页全字段 + PDF」—— 解耦自 main_round 的独立环节。"""
    if _inner:
        # 由 run_round_options 自身收尾调用时 no-op：避免与它的行内补详情递归/重复。
        return {"fetched": 0, "skipped": 0}
    sel = cfg["selectors"]
    cr = (cfg.get("crawl") or {})
    root_dir = Path(cfgmod.paths(cfg)["root"])
    want_pdf = bool(cfg.get("download", {}).get("pdf", True))
    cap = int(cr.get("backfill_cap", 300))
    max_minutes = float(cr.get("backfill_max_minutes", 20)) or 0.0

    # v1.9.6：取待补清单＝在架「无详情的壳」（REINS 詳細 是 <button> 无直链，
    # detail_href 全库恒空，旧 SQL 要求 detail_href 非空 → 永远 0 候选、详情永远补不上）。
    # 不再依赖 detail_href；改为按「在架且 detail_json 空」选壳，每轮渐进清积压。
    # 额外取 reg/chg 日期，供「行内点詳細」兜底按日期分组复用指定日期下载。
    try:
        cand = store.conn.execute(
            "SELECT property_no, reg_date_iso, chg_date_iso FROM properties "
            "WHERE is_active=1 AND (detail_json IS NULL OR detail_json='') "
            "ORDER BY last_seen_at DESC LIMIT ?", (cap,)).fetchall()
    except Exception as e:
        log("· 阶段B 取待补清单失败：" + type(e).__name__ + ": " + str(e))
        return {"fetched": 0, "skipped": 0}
    if not cand:
        log("· 阶段B：没有需要补详情的房源（在架壳都已齐备）")
        return {"fetched": 0, "skipped": 0}

    # v1.9.6：番号検索 选择器是否就绪（REINS 詳細 无直链，只能靠番号検索进详情页）。
    # 未配置 → 不猜 locator，改用「行内点詳細」兜底（复用已证的指定日期下载路径）。
    bs_inp = (sel.get("bukken_search_inputs") or "").strip()
    bs_btn = (sel.get("bukken_search_button") or "").strip()
    if not (bs_inp and bs_btn):
        log("· 阶段B：番号検索选择器未配置 → 改用「行内点詳細」兜底"
            "（复用指定日期下载 run_round_options，今日已实补 400 条，已证可用）")
        return _backfill_via_inline(store, cfg, log, run_id, cand)

    auth = Auth(cfg, cfgmod.paths(cfg)["session"])
    with _sync_playwright() as p:
        browser = auth.launch(p, headless=cfg["browser"].get("headless", False))
        ctx, page = auth.open_authed_page(browser, log=log)
        _mask_webdriver(ctx)
        log("✓ 阶段B 会话有效，进入補详情")
        worker = None
        if want_pdf and cr.get("pdf_background", True):
            try:
                ua = page.evaluate("navigator.userAgent") or ""
                ck = "; ".join(("%s=%s" % (c.get("name"), c.get("value")))
                               for c in ctx.cookies() if c.get("name"))
                n_inflight = max(1, int(cr.get("pdf_max_inflight", 2)))
                worker = _PdfWorker(root_dir / "attachments", ck, ua, log,
                                    max_inflight=n_inflight)
            except Exception:
                worker = None
        if run_id is not None:
            try:
                store.save_crawl_state(run_id, phase="detail_pdf",
                                       need_detail=len(cand), need_pdf=0)
            except Exception:
                pass
        log("—— 阶段B：补详情 + PDF（待处理 %d 条）——" % len(cand))
        t0 = time.monotonic()
        done = 0
        skipped = 0
        for row in cand:
            no = row["property_no"]
            existing = store.get_property(no)
            have_detail = bool(existing and (existing["detail_json"]))
            need_pdf = bool(want_pdf) and not _pdf_exists(root_dir, no)
            if have_detail and not need_pdf:
                skipped += 1
                continue
            # v1.9.6：REINS 詳細 是 <button> 无直链 → 走「番号検索」打开详情页
            # （selectors.bukken_search_inputs/button 已就绪才会进到这里）。
            try:
                d = _fetch_detail_by_no(ctx, page, no, sel, cfg,
                                       pdf_worker=worker, need_pdf=need_pdf, log=log,
                                       store=store)
            except SessionExpired:
                raise
            except Exception as e:
                log("  ✗ " + no + " 阶段B 详情失败：" + type(e).__name__ + ": " + str(e))
                continue
            if not d:
                # v1.9.81 G-3：终局跳过 ≠ 失败 —— 不计进失败、也不再重试
                if store is not None and store.no_miss_terminal(no):
                    skipped += 1
                    continue
                log("  ✗ " + no + " 阶段B 详情为空（番号検索未打开详情，下轮重试）")
                continue
            d["property_no"] = no
            try:
                pipeline.ingest([d], store, cfg, run_id)
                done += 1
                log("  ✓ " + no + " " + str(d.get("price", "")) + " "
                    + str(d.get("address", "") or "")[:18]
                    + (" [PDF→后台]" if (need_pdf and d.get("pdf_url")) else ""))
            except Exception as e:
                log("  ✗ " + no + " 阶段B 写库失败：" + type(e).__name__ + ": " + str(e))
            if max_minutes and (time.monotonic() - t0) > max_minutes * 60:
                log("⏱ 阶段B 已到时长上限，剩余留待下一轮")
                break
            time.sleep(random.uniform(3.0, 5.0))
        if worker is not None:
            worker.join(timeout=180)
            for _no, _p, _ms, _err in worker.drain():
                if _p:
                    store.set_pdf(_no, _p)
        browser.close()
    log("· 阶段B 完成（番号検索）：补详情 %d / 跳过 %d" % (done, skipped))
    return {"fetched": done, "skipped": skipped}


def run_round_options(store, cfg, opts, progress_cb=None, trial: bool = False) -> dict:
    """按用户手动选项实时连 REINS 查询 → 入库 + PDF。供「指定日期下载」使用。

    opts: kind/subtype/ward/price_min/max/area_min/max/line/station/date_type/date。
    通常由 SCHED.trigger_specified_download 在 exclusive 内调用
    （暂停当天下载 + 挡住其它触发）。
    """
    log = progress_cb or (lambda *_a, **_k: None)
    run_id = store.start_run("specified-download")
    # v1.7.0：把本轮目标日期写进断点表，供概览矩阵按「日期+轴」从 properties 实算分子
    try:
        store.save_crawl_state(run_id, target_date=opts.get("date") or "")
    except Exception as _e:                      # noqa: BLE001
        log("· target_date 记录失败（不影响抓取）：" + type(_e).__name__ + ": " + str(_e))
    stats = {"run_id": run_id, "mode": "specified", "scanned": 0, "fetched": 0,
             "new": 0, "changed": 0, "pdf_saved": 0, "errors": [], "status": "ok",
             "source": "live"}
    try:
        log("▶ 指定日期下载：开始（手动选项实时连 REINS）")
        sel = cfg["selectors"]
        cr = cfg.get("crawl", {})
        delay = [float(x) for x in cr.get("delay_range_seconds", [1, 2])]
        max_items = int(cr.get("max_items_per_run", 200))
        max_pages = int(cr.get("max_pages", 7))
        if trial:
            max_items = min(max_items, 3)
            max_pages = 1
            delay = [1.0, 2.0]
        auth = Auth(cfg, cfgmod.paths(cfg)["session"])
        # REINS「基本条件」原生就能一行放 2 个種目、两行共 4 个（正常人就是这么点的）→
        # 一次检索即可覆盖「一戸建 2 项 / 公寓 4 项」。不再逐个種目跑多遍：
        # 请求更少 ＝ 更像人 ＝ 更不容易被风控（用户核心诉求）。
        # v1.2.3：一次下载可覆盖多个種別组（一户建 2 + 公寓 4 → 3 组），
        # 每组各跑一次检索（REINS 一行槽位只能挂一个種別）；组内再按日期各跑一次，
        # 最后按物件番号去重合并。与「更新」口径一致 —— 用户要求"哪里更新都下这 6 个"。
        groups = _groups_from_opts(opts, cfg)
        log("· 本次物件種目（共 " + str(len(groups)) + " 组检索）："
            + " / ".join(g["kind"] + "×" + "·".join(g["subtypes"]) for g in groups))
        # 日期条件可多选（v1.2.0）：当天登录＝登録年月日、当天更新＝変更年月日。
        # 【2026-09-15 修正·关键】REINS 两排日期**同一次检索同时设 = AND（交集）**，不是 OR。
        #   两排都勾只返回「当天既新登録又変更」的极少数（实测 866 并集 vs 145 交集）。
        #   故「登録或変更」的**并集**诉求 = 把每个日期**拆成独立的一次检索**、最后按
        #   物件番号去重合并（下方 rounds 已是「每日期一轮」）。这才拿到真·全量，
        #   又不违反用户「别操作两次」的本意——两次检索是不同筛选条件（登録≠変更），
        #   合并去重后才是完整当天集，省不掉。
        _raw_dt = opts.get("date_types")
        if isinstance(_raw_dt, str):
            _raw_dt = [_raw_dt]
        dts = [x for x in (_raw_dt or []) if x in ("登録年月日", "変更年月日")]
        if not dts:
            dts = [(opts.get("date_type") or "登録年月日")]
        # v1.5.11：每日期一轮（拆分后分别检索，再按物件番号合并 = OR 语义）
        rounds: list[list[str]] = [[dt] for dt in dts]
        log("· 日期条件：" + "、".join(dts)
            + ("（拆成 %d 次检索、按物件番号合并=并集）" % len(rounds) if len(dts) > 1 else ""))
        collected: list[dict] = []
        session_results: list[dict] = []   # v1.6.5：逐条件结果（供结束汇总框，避免 NameError 崩溃）
        # 边抓边落库（v1.2.2）：每 flush_every 条写一次库 + PDF，
        # 不必等整批跑完——中途去「房源查询」就能看到已经抓到的部分。
        sink = _BatchSink(store, cfg, log, run_id, cr.get("flush_every", 5))
        batch_timed_out = False
        online_total = 0          # v1.4.0：本轮线上总数（列表层分母）
        with _sync_playwright() as p:
            browser = auth.launch(p, headless=cfg["browser"].get("headless", False))
            # 统一入口：会话失效会自动用本地账号重登一次（v1.2.1）
            ctx, page = auth.open_authed_page(browser, log=log)
            _mask_webdriver(ctx)      # v1.4.0：抹掉自动化指纹（webdriver）
            # ---- v1.4.0：后台 PDF（补齐 v1.3.0 缺口：指定日期下载以前还是同步干等）----
            root_dir = Path(cfgmod.paths(cfg)["root"])
            worker = None
            if bool(cfg.get("download", {}).get("pdf", True)) and cr.get("pdf_background", True):
                try:
                    ua = page.evaluate("navigator.userAgent") or ""
                    ck = "; ".join(("%s=%s" % (c.get("name"), c.get("value")))
                                   for c in ctx.cookies() if c.get("name"))
                    n_inflight = max(1, int(cr.get("pdf_max_inflight", 2)))
                    worker = _PdfWorker(root_dir / "attachments", ck, ua, log,
                                        max_inflight=n_inflight)
                    log("· PDF 后台下载已开启（最大在途 " + str(n_inflight)
                        + "；主线程继续翻页，不再干等）")
                except Exception as e:                            # noqa: BLE001
                    log("· PDF 后台下载启动失败 → 退回同步下载："
                        + type(e).__name__ + ": " + str(e))
                    worker = None
            seen: set = set()
            nav_done = False   # 第 1 个检索页已由 open_authed_page 打开，不重复导航
            for gi, g in enumerate(groups, 1):
                for di, rfields in enumerate(rounds, 1):
                    o = dict(opts)
                    o["kind"] = g["kind"]
                    o["subtypes"] = g["subtypes"]
                    o["date_types"] = rfields          # v1.5.11：每日期一轮（拆分后分别检索，合并=并集）
                    o["date_type"] = rfields[0] if rfields else "登録年月日"
                    tag = ("第 " + str(gi) + "/" + str(len(groups)) + " 组 "
                           + g["kind"] + "×" + "·".join(g["subtypes"]))
                    if len(rfields) > 1:
                        tag += " / 日期：" + "、".join(rfields)
                    log("—— " + tag + " ——")
                    # v1.6.3：打条件横幅，确认 房屋类型/区域/日期轴 与用户选择一致
                    _today = opts.get("date")
                    if _today:
                        _dr = "指定日 " + str(_today)
                    elif len(rfields) > 1:
                        _dr = "全期間（登録/変更 两轴分两次并集）"
                    else:
                        _dr = "全期間（不限日期）"
                    _log_search_banner(
                        log,
                        seq=("%d/%d 组 · %d/%d 日期轮" % (gi, len(groups), di, len(rounds))),
                        kind=g["kind"], subtypes=g["subtypes"], cfg=cfg,
                        date_fields=rfields,
                        date_range=_dr,
                        round_tag="指定日期下载")
                    if nav_done:
                        page.goto(cfg["site"]["search_url"],
                                  wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(1200)
                    nav_done = True
                    _check_maintenance(page, log)
                    if gi == 1 and di == 1:
                        log("✓ 会话有效，已进入検索条件入力页")
                    _expand_search_panel(page, sel)
                    page.wait_for_timeout(1000)
                    try:
                        _apply_manual_conditions(page, sel, o, log, cfg)
                    except _GeoFailed as _ge:
                        log("⚠ 跳过本组（地区基线直录失败，已重试）：" + str(_ge)[:160])
                        continue
                    page.locator(sel["search_button"]).first.click()
                    page.wait_for_load_state("networkidle", timeout=40000)
                    page.wait_for_timeout(2500)
                    _dismiss_modal(page)
                    total_txt = _read_total(page, sel, log=log)
                    log("· 查询 → 结果 " + (total_txt or "未知"))
                    _on = None
                    try:
                        _on = _parse_total(total_txt)
                        if _on:
                            online_total += int(_on)      # v1.4.0：列表层分母
                            store.add_online_stat(run_id, g["kind"],
                                                  g["kind"] + " × " + "·".join(g["subtypes"]),
                                                  g["subtypes"], _on,
                                                  axis=rfields[0])
                            log("   · 线上覆盖已记录：" + str(_on) + " 件")
                    except Exception as _e:
                        log("   · 线上覆盖记录失败（不影响抓取）："
                            + type(_e).__name__ + ": " + str(_e))
                    before = len(collected)
                    _, batch_timed_out = _collect_from_results(
                        page, store, cfg, sel, log, max_items, max_pages,
                        delay, collected, sink=sink, worker=worker)
                    # 去重：同一物件可能同时命中多个组 / 「当天登录」与「当天更新」
                    dedup: list[dict] = []
                    for rec in collected[before:]:
                        no = rec.get("property_no")
                        if no and no in seen:
                            continue
                        if no:
                            seen.add(no)
                        dedup.append(rec)
                    got, kept = len(collected) - before, len(dedup)
                    if kept != got:
                        log("  · 去重：" + str(got) + " → " + str(kept)
                            + " 条（与前面组 / 日期重复）")
                    # v1.9.5（PRD-19 R4 取证）：線上报告有条数、本组却 0 条入账
                    #   → 列表行选择器疑不中，日志当场点出来（下一轮即可定性，不必再猜）
                    if kept == 0 and _on:
                        log("  ⚠ 本组 0 条入账，但線上报告 " + str(_on)
                            + " 件 → 疑 result_rows(div.p-table-body-row) 不匹配，待查")
                    collected[before:] = dedup
                    # v1.6.5：累计逐条件结果（线上报告 / 本次下载），供结束汇总框
                    try:
                        _sr_on = int(_on) if _on else None
                    except Exception:  # noqa: BLE001
                        _sr_on = None
                    session_results.append({
                        "tag": tag,
                        "online": _sr_on,
                        "downloaded": kept,
                    })
                    if batch_timed_out:
                        log("⏱ 已到单轮时长上限：本次先收尾入库，剩余房源下次再抓"
                            "（已入库的下次会跳过详情、跑得很快）")
                        break
                    if di < len(rounds):
                        time.sleep(random.uniform(6, 14))
                if batch_timed_out:
                    break
                if gi < len(groups):
                    time.sleep(random.uniform(6, 14))
            if worker is not None:
                log("· 等后台 PDF 收尾…（已经下完的 PDF 不受影响）")
                worker.join(timeout=180)
                for _no, _p, _ms, _err in worker.drain():
                    if _p:
                        store.set_pdf(_no, _p)
                        log("  ↓ PDF 已落盘 " + _no + "（%.1fs）" % (_ms / 1000.0))
                    else:
                        log("  ✗ PDF 失败 " + _no + "：" + str(_err))
                log("· 后台 PDF：成功 %d / 失败 %d ｜ 平均 %.1fs"
                    % (worker.n_ok, worker.n_fail, worker.avg_ms / 1000.0))
            browser.close()
        sink.flush()                      # 收尾：把最后不足一批的也落库
        st = sink.stats
        stats["errors"] += st["errors"]
        stats["scanned"] = st.get("scanned", 0)
        stats["pdf_saved"] = st.get("pdf_saved", 0)
        if not st["fetched"]:
            log("· 指定条件下没有匹配房源（或已全部在本地库里）")
        else:
            stats.update({k: st[k] for k in ("fetched", "new", "changed")})
            log("✓ 已入库：扫描 " + str(st["scanned"]) + " 条 / 落库 "
                + str(st["fetched"]) + " 条 / 新盘 " + str(st["new"]) + " 条 / 变更 "
                + str(st["changed"]) + " 条 / PDF " + str(st["pdf_saved"]) + " 份")
        # v1.9.1（解耦阶段B · 统一各下载入口策略）：指定日期下载同样收一个独立「补详情+PDF」
        #   pass，与 run_round 降级分支**共用同一个 _backfill_details_pdfs** ——
        #   保证"任何入口下完的房源都含详情"（勇哥：几个下载位置都对应相同策略）。
        try:
            _bf = _backfill_details_pdfs(store, cfg, log, run_id, _inner=True)
            stats["detail_backfilled"] = _bf.get("fetched", 0)
        except Exception as _e:                    # noqa: BLE001
            log("⚠ 阶段B 补详情失败（不影响列表）：%s" % (type(_e).__name__ + ": " + str(_e)))
        # ---- 收尾：导出 Excel + 按天页 ----
        try:
            from .exporter import export_day_excel, export_daily_page
            if cfg.get("download", {}).get("export_excel", True):
                ep = export_day_excel(store, cfg, datetime.now().strftime("%Y-%m-%d"))
                log("✓ Excel 已落盘：" + str(ep))
            if cfg.get("download", {}).get("export_daily_page", True):
                dp = export_daily_page(store, cfg, datetime.now().strftime("%Y-%m-%d"))
                log("✓ 按天页已落盘：" + str(dp))
        except Exception as e:  # noqa: BLE001
            stats["errors"].append("导出失败：" + type(e).__name__ + ": " + str(e))
            log("⚠ 导出失败：" + str(e))
        # ---- v1.4.0 收尾①：本轮全量快照（差异本地比对，不回线上查、不碰风控）----
        try:
            n_snap = store.snapshot_run(run_id)
            if n_snap:
                log("· 本轮快照已存 " + str(n_snap) + " 条（任意两轮差异可本地算）")
        except Exception as e:                                   # noqa: BLE001
            stats["errors"].append("快照失败：" + type(e).__name__ + ": " + str(e))
        store.finish_run(run_id, stats["scanned"], stats["fetched"], stats["new"],
                         stats["changed"], "ok",
                         online_total=online_total,
                         pdf_saved=stats["pdf_saved"])
        # v1.6.4：下载结束打印「条件 + 结果」汇总框（直观核对这次下了什么、下了多少）
        _use_sc = bool(((cfg or {}).get("search", {}) or {}).get("use_saved_condition", False))
        _spec = [
            "種目组：%d 组（%s）" % (len(groups), " ｜ ".join(
                g["kind"] + "×[" + "·".join(g["subtypes"]) + "]" for g in groups)),
        ]
        _df_all = ["、".join(rf) for rf in rounds]
        _spec.append("日期轴：%s（每轴分次检索，合并=按番号并集）" % " ／ ".join(_df_all))
        if opts.get("date"):
            _spec.append("日期范围：指定日 " + str(opts["date"]))
        elif len(rounds[0]) > 1:
            _spec.append("日期范围：全期間（登録/変更 两轴分两次并集）")
        else:
            _spec.append("日期范围：全期間（不限日期）")
        _spec.append("区域：大阪府・大阪市（%s）" % ("保存条件套用" if _use_sc else "直录"))
        _log_session_summary(log, "指定日期下载结束", _spec, session_results,
                             stats, online_total)
        # ---- v1.4.0 收尾②：写完就通知，前端轮询到即提示（不用一直盯日志）----
        store.add_notification(
            "specified_done",
            "指定日期下载完成：列表 %d 条 / 落库 %d 条 / 新盘 %d / 变更 %d / PDF %d 份"
            % (stats["scanned"], stats["fetched"], stats["new"],
               stats["changed"], stats["pdf_saved"]))
    except SessionExpired as e:
        store.finish_run(run_id, 0, 0, 0, 0, "error", "会话失效：" + str(e))
        stats.update({"status": "error", "errors": ["会话失效：" + str(e)]})
        log("✗ 会话失效，已停机：" + str(e))
    except Exception as e:  # noqa: BLE001
        store.finish_run(run_id, 0, 0, 0, 0, "error",
                         type(e).__name__ + ": " + str(e))
        stats.update({"status": "error",
                      "errors": [type(e).__name__ + ": " + str(e)]})
        log("✗ 本轮失败：" + str(e))
    return stats


def probe_query_options(store, cfg, opts, progress_cb=None, limit: int = 8) -> dict:
    """实时预览：按手动选项连 REINS 查询，返回条数 + 样本（不落库、不下 PDF）。
    供「指定日期下载」的「确定」按钮。

    v1.2.3：同样按種目分组（一户建 2 + 公寓 4 → 3 组）各查一次，
    把各组条数与样本合并返回 —— 保证「预览看到的」＝「正式下载会抓的」。
    """
    log = progress_cb or (lambda *_a, **_k: None)
    sel = cfg["selectors"]
    auth = Auth(cfg, cfgmod.paths(cfg)["session"])
    groups = _groups_from_opts(opts, cfg)
    per: list[dict] = []
    sample: list[str] = []
    with _sync_playwright() as p:
        browser = auth.launch(p, headless=cfg["browser"].get("headless", False))
        ctx, page = auth.open_authed_page(browser, log=log)   # 会话失效会自动重登一次
        nav_done = False
        per_group_limit = max(1, int(limit) // max(1, len(groups)))
        for gi, g in enumerate(groups, 1):
            o = dict(opts)
            o["kind"] = g["kind"]
            o["subtypes"] = g["subtypes"]
            label = g["kind"] + "×" + "·".join(g["subtypes"])
            try:
                if nav_done:
                    page.goto(cfg["site"]["search_url"],
                              wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1200)
                nav_done = True
                _check_maintenance(page, log)
                _expand_search_panel(page, sel)
                page.wait_for_timeout(1000)
                try:
                    _apply_manual_conditions(page, sel, o, log, cfg)
                except _GeoFailed as _ge:
                    log("⚠ 跳过本组（地区基线直录失败，已重试）：" + str(_ge)[:160])
                    continue
                page.locator(sel["search_button"]).first.click()
                page.wait_for_load_state("networkidle", timeout=40000)
                page.wait_for_timeout(2500)
                _dismiss_modal(page)
                total_txt = _read_total(page, sel, log=log)
                per.append({"label": label, "total": total_txt or "未知"})
                log("· " + label + " → 结果 " + (total_txt or "未知"))
                rows = page.locator(sel["result_rows"])
                n = min(rows.count(), per_group_limit)
                for i in range(n):
                    try:
                        txt = rows.nth(i).inner_text(timeout=2000)
                    except Exception:
                        continue
                    sample.append("[" + label + "] "
                                  + txt.replace("\n", " ").strip()[:120])
            except SessionExpired:
                raise
            except Exception as e:  # noqa: BLE001
                reason = auth.expired_reason(page)
                if reason:
                    raise SessionExpired(reason)
                log("⚠ " + label + " 预览失败：" + type(e).__name__ + ": " + str(e))
                per.append({"label": label, "total": "失败"})
            if gi < len(groups):
                time.sleep(random.uniform(4, 9))
        browser.close()
    summary = " ／ ".join(x["label"] + " " + str(x["total"]) for x in per)
    return {"total": summary or "未知", "count": len(sample),
            "sample": sample, "groups": per}
