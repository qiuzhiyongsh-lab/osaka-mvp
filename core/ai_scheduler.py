# -*- coding: utf-8 -*-
"""每晚 AI 抽取定时任务（PRD 25 · R5）—— 独立于 REINS 抓取那条调度。

为什么安排在晚上 23:00 起
-------------------------
  ① REINS 日本时间 22:00–07:00 维护；这条任务**只读本地 PDF、不碰平台**，
     夜里跑零冲突零风控；
  ② OCR 吃 CPU，白天把算力留给浏览器抓取；
  ③ 早上开工打开就有昨晚的成果。
  窗口是**本机本地时间**（勇哥在上海 = 北京时间），与 Scheduler 那条日本时间窗口互不干扰。

勇哥 D3 口径（2026-09-20）：「当日新增 + 当日未完成的」。
  所以每轮队列 = **上轮遗留** → **当日新增** →（可选）历史补跑。
  遗留项排在最前面（先补旧账），且单轮上限只是防过载，**不会裁掉任务**。

统计口径（D1 可追溯）：每轮结束写 ai_run_log：
   local_hit（0 元） / cloud_used（花钱） / skipped（hash 未变） / failed / pending
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

DEFAULTS_WIN = ("23:00", "06:00")


def _parse_hhmm(s: str, default=(23, 0)) -> tuple[int, int]:
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:                                      # noqa: BLE001
        return default


def in_window(start: str = DEFAULTS_WIN[0], end: str = DEFAULTS_WIN[1],
              t: datetime | None = None) -> bool:
    """跨午夜窗口判定（本机本地时间）。23:00–06:00 这种 start>end 才走得通。"""
    t = t or datetime.now()
    cur = t.hour * 60 + t.minute
    sh, sm = _parse_hhmm(start, (23, 0))
    eh, em = _parse_hhmm(end, (6, 0))
    s, e = sh * 60 + sm, eh * 60 + em
    if s <= e:
        return s <= cur <= e
    return cur >= s or cur <= e


def _secs_until(start: str) -> int:
    sh, sm = _parse_hhmm(start, (23, 0))
    now = datetime.now()
    nxt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return max(30, int((nxt - now).total_seconds()))


class AIScheduler:
    """每晚跑一轮 AI 抽取。UI 可 status() / run_now() / start() / stop()。"""

    def __init__(self, cfg: dict, paths: dict, ai_store, log_fn=None, ctx_fn=None):
        self.cfg = cfg
        self.paths = paths
        self.store = ai_store
        self.log = log_fn or (lambda m: None)
        self.ctx_fn = ctx_fn or (lambda no: {})
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = threading.Event()
        self._last_run_date: str = ""
        self._rearm = threading.Event()
        self.last_result: dict | None = None
        self.last_error: str = ""
        self.next_run_at: datetime | None = None

    # ---------------- 配置读取 ----------------
    def _sa(self) -> dict:
        return self.cfg.get("schedule_ai") or {}

    def _le(self) -> dict:
        return self.cfg.get("local_extract") or {}

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def describe(self) -> str:
        sa = self._sa()
        if not sa.get("enabled"):
            return "已停止"
        w = sa.get("window") or {}
        return (f"每晚 {w.get('start','23:00')}–{w.get('end','06:00')}（本机时间）"
                f"，单轮≤{sa.get('max_per_run',300)}份，{sa.get('workers',2)}并发"
                f"{'，强制重跑' if sa.get('force_rerun') else ''}"
                f"{'，历史补跑中' if sa.get('backfill_all') else ''}")

    def status(self) -> dict:
        sa, le = self._sa(), self._le()
        return {
            "enabled": bool(sa.get("enabled")),
            "thread_alive": self.running,
            "busy": self._busy.is_set(),
            "describe": self.describe(),
            "window": sa.get("window") or {},
            "in_window": in_window(**{
                "start": (sa.get("window") or {}).get("start", "23:00"),
                "end": (sa.get("window") or {}).get("end", "06:00")}),
            "prefer_local": bool(le.get("prefer_local")),
            "fallback_model": bool(le.get("fallback_model")),
            "ocr_enabled": bool(le.get("ocr_enabled")),
            "next_run_at": self.next_run_at.strftime("%Y-%m-%d %H:%M:%S") if self.next_run_at else None,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "last_run_date": self._last_run_date,
            "max_per_run": int(sa.get("max_per_run", 300)),
            "workers": int(sa.get("workers", 2)),
            "force_rerun": bool(sa.get("force_rerun")),
            "backfill_all": bool(sa.get("backfill_all")),
            "backfill_batch": int(sa.get("backfill_batch", 300)),
        }

    # ---------------- 启停 ----------------
    def start(self) -> str:
        sa = self._sa()
        if not sa.get("enabled"):
            self.log("[AI定时] 已关闭（schedule_ai.enabled=false），不启动")
            return "已关闭"
        if self.running:
            return "已在运行"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="ai-scheduler")
        self._thread.start()
        self.log(f"[AI定时] 已启动：{self.describe()}")
        return "已启动"

    def stop(self) -> str:
        self._stop.set()
        self.next_run_at = None
        self.log("[AI定时] 已发出停止信号")
        return "已停止"

    def rearm(self) -> None:
        """配置改了（时间/并发/开关）→ 打断休眠按新参数重算。"""
        self._last_run_date = ""        # 允许当天再触发一次（改了设置通常想立刻验证）
        self._rearm.set()

    # ---------------- 主循环 ----------------
    def _loop(self):
        sa = self._sa()
        if sa.get("run_on_start"):
            self.run_now("startup")
        while not self._stop.is_set():
            sa = self._sa()
            w = sa.get("window") or {}
            if not sa.get("enabled"):
                break
            if not in_window(w.get("start", "23:00"), w.get("end", "06:00")):
                wait = _secs_until(w.get("start", "23:00"))
                self.next_run_at = datetime.now() + timedelta(seconds=wait)
                self._sleep(wait)
                continue
            today = datetime.now().strftime("%Y-%m-%d")
            if self._last_run_date == today and not self._rearm.is_set():
                self._sleep(300)            # 今天跑过了，等窗口外的下一个检查点
                continue
            self._rearm.clear()
            self.run_now("nightly")
            self._sleep(300)

        self.log("[AI定时] 线程已退出")

    def _sleep(self, sec: int):
        end = time.time() + max(1, int(sec))
        while time.time() < end and not self._stop.is_set() and not self._rearm.is_set():
            time.sleep(min(5.0, end - time.time()))

    # ---------------- 队列构造（D3）----------------
    def _last_run_since(self) -> datetime:
        """增量起点：上次**跑过**的时刻（跨天停机也不会漏）。

        从没跑过时退化为 7 天前 —— 既覆盖这批昨天的 PDF，又不会一口吞下
        全部 3000+ 份把机器压垮（真要全量补跑用 backfill_all 开关）。
        """
        try:
            runs = self.store.recent_runs(5)
            for r in runs:
                if r.get("started_at"):
                    return datetime.strptime(r["started_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:                                       # noqa: BLE001
            pass
        return datetime.now() - timedelta(days=7)

    def _queue(self) -> tuple[list[str], str]:
        """返回 (待办番号列表, 队列来源说明)。"""
        att = Path(self.paths["attachments"])
        sa = self._sa()

        pending_prev: list[str] = []
        try:
            runs = self.store.recent_runs(1)
            if runs and (runs[0].get("note") or "").startswith("["):
                pending_prev = json.loads(runs[0]["note"])      # 上轮没跑完的番号
        except Exception:                                       # noqa: BLE001
            pending_prev = []

        # 增量起点：优先「上次成功跑完的时刻」—— 跨天停机也不会漏；
        # 从没跑过（或跑挂没留痕）时退化为最近 7 天，避免一次性吞掉全部 3000+ 份。
        # 勇哥 D3 说的「当日新增」是这个口径的子集（当天的新增必然 ≥ 上次运行时刻）。
        since = self._last_run_since()

        daily = []
        try:
            for p in att.glob("*.pdf"):
                try:
                    ts = datetime.fromtimestamp(p.stat().st_mtime)
                except Exception:                               # noqa: BLE001
                    continue
                if ts >= since:
                    daily.append(p.stem)
        except Exception:                                       # noqa: BLE001
            pass

        queue, seen = [], set()
        for no in pending_prev + daily:
            if no not in seen and (att / f"{no}.pdf").exists():
                seen.add(no)
                queue.append(no)

        note = f"遗留{len(pending_prev)} + 增量({since:%m-%d %H:%M}后){len(daily)}"

        # 历史补跑：一次性开关（勇哥要专门打开才会发生）
        if sa.get("backfill_all"):
            have = set(self.store.existing_property_nos())
            rest = [p.stem for p in att.glob("*.pdf") if p.stem not in have]
            for no in rest:
                if no not in seen:
                    seen.add(no)
                    queue.append(no)
            note += f" + 历史补跑{len(rest)}"

        cap = int(sa.get("max_per_run", 300))
        return queue[:cap], note + f"（本轮取 {min(len(queue), cap)}）"

    # ---------------- 跑一轮 ----------------
    def run_now(self, trigger: str = "manual", limit: int | None = None,
                force: bool | None = None) -> dict:
        """立即跑一轮（自动通道用）。返回统计 dict。已在跑则返回 busy。"""
        if self._busy.is_set():
            return {"status": "busy", "message": "已有 AI 抽取在跑，请稍后"}
        if self.store is None:
            return {"status": "error", "message": "AI 独立库未初始化"}
        self._busy.set()
        try:
            queue, qnote = self._queue()
            if limit:
                queue = queue[:int(limit)]
            return self._run_batch(queue, trigger, force, qnote)
        except Exception as e:                                 # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            self.log(f"[AI定时] run_now 异常：{self.last_error}")
            return {"status": "error", "message": self.last_error}
        finally:
            self._busy.clear()

    def generate(self, candidates: list[str], trigger: str = "manual-generate",
                 force: bool | None = None) -> dict:
        """手动「正式生成」：对给定番号列表跑 AI 抽取。返回统计 dict。

        candidates 由调用方（设置页接口）按「PDF 下载时间 + 種目范围 +
        跳过已生成」圈定后传入；与 run_now 共用 _run_batch 的管道与并发逻辑。
        """
        if self._busy.is_set():
            return {"status": "busy", "message": "已有 AI 抽取在跑，请稍后"}
        if self.store is None:
            return {"status": "error", "message": "AI 独立库未初始化"}
        self._busy.set()
        try:
            queue = [str(x) for x in (candidates or [])]
            return self._run_batch(queue, trigger, force, f"手动生成 {len(queue)} 份")
        except Exception as e:                                 # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            self.log(f"[AI定时] generate 异常：{self.last_error}")
            return {"status": "error", "message": self.last_error}
        finally:
            self._busy.clear()

    def _run_batch(self, queue: list[str], trigger: str,
                   force: bool | None, qnote: str) -> dict:
        """对 queue 跑一轮 AI 抽取（run_now / generate 共用）。"""
        t0 = time.time()
        # 绝对导入：作为 core 包成员（Web 服务）与直接跑脚本（CLI）都能用，
        # 用 `from . import` 在 `python core/ai_scheduler.py` 下会报 no known parent package。
        import importlib
        ai_pipeline = importlib.import_module("core.ai_pipeline")
        sa, le = self._sa(), self._le()
        if not le.get("enabled", True):
            return {"status": "off", "message": "local_extract.enabled=false"}

        force = bool(sa.get("force_rerun")) if force is None else force

        cfg = dict(le)
        cfg["ai"] = self.cfg.get("ai") or {}
        # D1 硬规则：prefer_local=false 时才允许无条件走云端（默认 True）
        cfg["fallback_model"] = bool(cfg.get("fallback_model")) and bool(cfg.get("prefer_local", True))

        run_id = self.store.start_run(trigger, len(queue))
        self.log(f"[AI定时] 本轮开始：{qnote}")

        stats = {"total": len(queue), "local": 0, "cloud": 0, "skip": 0,
                 "fail": 0, "pending": 0}
        cloud_budget = int(le.get("fallback_max_per_run", 50))
        pending_nos: list[str] = []
        workers = max(1, int(sa.get("workers", 2)))

        def _one(no: str) -> tuple[str, dict]:
            try:
                ctx = self.ctx_fn(no) or {}
                allow_cloud = stats["cloud"] < cloud_budget
                r = ai_pipeline.run_one(no, self.paths, cfg, self.store,
                                        ctx=ctx, force=force,
                                        allow_cloud=allow_cloud)
                if r.get("ok") and not r.get("skipped"):
                    ai_pipeline.save_fields(self.store, no, r)
                return no, r
            except FileNotFoundError:
                return no, {"ok": False, "error": "PDF 不存在"}
            except Exception as e:                          # noqa: BLE001
                return no, {"ok": False, "error": f"{type(e).__name__}: {e}"}

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for no, r in ex.map(_one, queue):
                if r.get("ok"):
                    if r.get("skipped"):
                        stats["skip"] += 1
                    elif r.get("cloud_used"):
                        stats["cloud"] += 1
                    else:
                        stats["local"] += 1
                else:
                    stats["fail"] += 1
                    pending_nos.append(no)
                    self.log(f"[AI定时] {no} 失败：{r.get('error')}")

        # 超出单轮上限的顺延到下一轮（不是裁掉）
        cap = int(sa.get("max_per_run", 300))
        stats["pending"] = len(pending_nos) + max(0, stats["total"] - cap)
        stats["done"] = stats["local"] + stats["cloud"]
        stats["ms"] = int((time.time() - t0) * 1000)
        self.store.finish_run(run_id, stats, json.dumps(pending_nos[:500]))
        self._last_run_date = datetime.now().strftime("%Y-%m-%d")
        self.last_result = stats
        hit = (f"{stats['local']}/{max(1, stats['done'])}"
               if stats["done"] else "—")
        self.log(f"[AI定时] 本轮完成：处理{stats['total']} 成功{stats['done']}"
                 f" 跳过{stats['skip']} 失败{stats['fail']}"
                 f"｜本地命中率 {hit}｜云端调用 {stats['cloud']} 次"
                 f"｜未完成 {stats['pending']}｜耗时 {stats['ms']}ms")
        return stats


if __name__ == "__main__":                                     # CLI：今晚就能手动试跑
    import argparse
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parent.parent))

    from core import config as cfgmod
    from core.ai_structure_store import AIStructureStore

    ap = argparse.ArgumentParser(description="每晚 AI 抽取（手动跑一轮）")
    ap.add_argument("--limit", type=int, default=0, help="本轮最多处理几份（0=按配置）")
    ap.add_argument("--force", action="store_true", help="忽略 pdf_hash 强制重跑")
    ap.add_argument("--no-ocr", action="store_true", help="关掉 OCR（只走文字层）")
    a = ap.parse_args()

    _cfg = cfgmod.load()
    _paths = cfgmod.paths(_cfg)
    if a.no_ocr:
        _cfg["local_extract"]["ocr_enabled"] = False
    _store = AIStructureStore(_P(_paths["root"]) / "data" / "ai_pdf_store.db")
    sch = AIScheduler(_cfg, _paths, _store, log_fn=lambda m: print(m, flush=True))
    print("窗口:", sch.describe(), "｜当前是否在窗口内:",
          in_window(**{"start": (_cfg["schedule_ai"]["window"] or {}).get("start", "23:00"),
                       "end": (_cfg["schedule_ai"]["window"] or {}).get("end", "06:00")}))
    r = sch.run_now("cli", limit=(a.limit or None), force=(a.force or None))
    print("结果:", r)
    _store.close()
