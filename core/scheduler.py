# -*- coding: utf-8 -*-
"""更新调度器：三种更新方式都在这里。

  ① 定时更新  interval —— 你设定固定间隔（1 / 2 / 4 / 8 小时），到点就跑一轮
  ② 随机更新  random   —— 开关一开，每轮间隔在 [随机下限, 随机上限] 之间随机（更像真人）
  ③ 手动更新  manual   —— 点按钮，立刻按"当前时间节点"跑一轮

三种方式跑的是**同一套抓取管道**，只是"什么时候触发"不同。
只在 schedule.window 内执行；window 与维护时段一律以**日本时间**为准（schedule.timezone，默认 Asia/Tokyo）。
REINS 日本时间 23:00–次日 07:00 是维护时段（非运行时段），故默认运行窗口为白天 07:00–23:00。
"""
from __future__ import annotations

import contextlib
import random
import threading
import time
from datetime import datetime, timedelta, timezone

from .crawler import run_round


# 最小间隔（分钟）：低于这个值等于高频轰炸 REINS，违背"像人"原则，一律夹到 5 分钟
MIN_INTERVAL_MIN = 5.0


def _fmt_min(v: float) -> str:
    """5.0 → '5'；12.5 → '12.5'（展示用，去掉无意义的 .0）。"""
    f = float(v)
    return str(int(f)) if abs(f - int(f)) < 1e-9 else str(f)


def _parse_hhmm(s: str, default=(7, 0)) -> tuple[int, int]:
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:
        return default


# 日本标准时间（REINS 维护/运行时段均以日本时间为准，避免本机时区不同步算错）
def _tz_offset_hours(cfg: dict) -> int:
    """schedule.timezone 解析：默认日本时间(UTC+9)。

    本机若在中国时间(UTC+8)而配置写的是日本时间，曾经会差 1 小时，
    导致 23:00 维护被当成次日 00:00、白天窗口被整体平移 1 小时。统一从这里取偏移。
    """
    tz = (cfg.get("schedule", {}) or {}).get("timezone", "Asia/Tokyo")
    if isinstance(tz, (int, float)):
        return int(tz)
    if isinstance(tz, str):
        t = tz.strip().upper()
        if t in ("JST", "ASIA/TOKYO", "UTC+9", "+09:00", "UTC+0900"):
            return 9
        if t in ("CST", "ASIA/SHANGHAI", "UTC+8", "+08:00", "CHINA"):
            return 8
    return 9  # 兜底：日本时间


def _now_tz(cfg: dict) -> datetime:
    """当前时刻，换算到 schedule.timezone 所指时区（默认日本时间 UTC+9）。"""
    off = _tz_offset_hours(cfg)
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=off)))


class Scheduler:
    def __init__(self, store, cfg: dict, log_fn=None):
        self.store = store
        self.cfg = cfg
        self._log = log_fn or (lambda m: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._busy = threading.Event()
        self._rearm = threading.Event()      # v1.9.9：配置热改时打断 sleep、按新参数重算下一轮
        # 「指定日期下载」要独占：暂停自动轮次 + 挡住其它触发（详见 exclusive()）
        self._paused = False
        self._paused_reason = ""
        self.last_result: dict | None = None
        self.next_run_at: datetime | None = None
        self.last_error: str = ""

    # ---------------- 状态 ----------------
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _sched(self) -> dict:
        return self.cfg.get("schedule", {}) or {}

    def _in_window(self, t: datetime | None = None) -> bool:
        t = t or _now_tz(self.cfg)
        w = self._sched().get("window", {}) or {}
        sh, sm = _parse_hhmm(w.get("start", "07:00"))
        eh, em = _parse_hhmm(w.get("end", "23:00"))
        cur = t.hour * 60 + t.minute
        start = sh * 60 + sm
        end = eh * 60 + em
        if start <= end:
            # 同日窗口，如 07:00–23:00
            return start <= cur <= end
        # 跨午夜窗口，如 23:00–次日 07:00：cur 落在 start 之后或 end 之前都算在内
        return cur >= start or cur <= end

    def _secs_until_window(self) -> int:
        w = self._sched().get("window", {}) or {}
        sh, sm = _parse_hhmm(w.get("start", "07:00"))
        now = _now_tz(self.cfg)
        nxt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return max(30, int((nxt - now).total_seconds()))

    def _rand_minutes(self) -> tuple[float, float]:
        """随机间隔（分钟）：优先读分钟键；两者都缺时用老的小时键 ×60 换算。"""
        s = self._sched()
        lo_m, hi_m = s.get("random_min_minutes"), s.get("random_max_minutes")
        if lo_m is None and hi_m is None:
            lo_m = float(s.get("random_min_hours", 1) or 1) * 60.0
            hi_m = float(s.get("random_max_hours", 3) or 3) * 60.0
        lo = float(lo_m if lo_m is not None else 20.0)
        hi = float(hi_m if hi_m is not None else 60.0)
        if hi < lo:
            lo, hi = hi, lo
        return max(MIN_INTERVAL_MIN, lo), max(MIN_INTERVAL_MIN, hi)

    def _next_delay(self) -> float:
        """本轮结束到下一轮的等待秒数。间隔统一走「分钟制 · 随机」。"""
        lo, hi = self._rand_minutes()
        return max(30.0, random.uniform(lo, hi) * 60.0)

    def describe(self) -> str:
        s = self._sched()
        if not s.get("enabled"):
            return "已停止"
        lo, hi = self._rand_minutes()
        core = f"随机间隔 {_fmt_min(lo)}–{_fmt_min(hi)} 分钟"
        w = s.get("window", {}) or {}
        return f"{core}（日本时间 {w.get('start','07:00')}–{w.get('end','23:00')} 内）"

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self, reason: str = "指定日期下载") -> None:
        """暂停「当天的下载」：调度线程继续活着，但不再发起新的自动轮次。

        指定日期下载开始前调用；跑完由 resume() 恢复。这样保证同一时刻
        只有一条 REINS 抓取在跑（既不为难站点，也不让两边互相覆盖）。
        """
        self._paused = True
        self._paused_reason = reason
        self._log(f"⏸ 当天的下载已暂停（{reason}），以指定日期的下载为准")

    def resume(self) -> None:
        """恢复「当天的下载」（指定日期下载跑完后调用）。"""
        if not self._paused:
            return
        self._paused = False
        self._paused_reason = ""
        self._log("▶ 指定日期下载已结束，当天的下载已恢复")

    @contextlib.contextmanager
    def exclusive(self, reason: str = "指定日期下载"):
        """独占抓取：期间 ① 自动轮次跳过；② 其它触发（手动/真实下载）一律被挡。

        用法：
            with SCHED.exclusive("指定日期下载"):
                ...跑指定日期的抓取...
        """
        self.pause(reason)
        self._busy.set()
        try:
            yield
        finally:
            self._busy.clear()
            self.resume()

    def status(self) -> dict:
        _rl = self._rand_minutes()
        return {
            "enabled": bool(self._sched().get("enabled")),
            "thread_alive": self.running,
            "busy": self._busy.is_set(),
            "paused": self._paused,
            "paused_reason": self._paused_reason,
            "describe": self.describe(),
            "mode": "random",
            "interval_hours": self._sched().get("interval_hours", 2),
            "random_min_hours": self._sched().get("random_min_hours", 1),
            "random_max_hours": self._sched().get("random_max_hours", 3),
            "random_min_minutes": _fmt_min(_rl[0]),
            "random_max_minutes": _fmt_min(_rl[1]),
            "window": self._sched().get("window", {"start": "07:00", "end": "23:00"}),
            "in_window": self._in_window(),
            "next_run_at": self.next_run_at.strftime("%Y-%m-%d %H:%M:%S") if self.next_run_at else None,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }

    # ---------------- 启停 ----------------
    def start(self) -> str:
        self._sched()["enabled"] = True
        if self.running:
            return "已在运行"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        self._log(f"调度已启动：{self.describe()}")
        return "已启动"

    def stop(self) -> str:
        self._sched()["enabled"] = False
        self._stop.set()
        self.next_run_at = None
        self._log("调度已停止")
        return "已停止"

    def rearm(self) -> None:
        """v1.9.9：更新方式（间隔/时段）热改后，立刻按新参数重算下一轮。

        - 置位 _rearm → _loop 的分片 sleep 立即跳出、清位后回到循环顶重算 delay
          （不跑轮次、不丢弃已排程、不重连 REINS）。
        - 解决「改了设置下一轮时间不变」的 bug：之前只改 cfg 不重算，
          下一轮仍按旧参数走。
        """
        self._rearm.set()
        self._log("⟳ 更新方式已改动，下一轮将按新参数重算")

    # ---------------- 手动 ----------------
    def trigger_manual(self, trigger: str = "manual", scale: float = 0.25,
                       trial: bool = False, resume: bool = False) -> dict:
        """立即跑一轮（手动更新）。若正在跑则拒绝，避免并发抓取。

        resume=True（v1.5.2）：从上次中断的房型接着跑（断点续传）。
        """
        if self._busy.is_set():
            return {"status": "busy", "message": "已有任务在跑，请稍后"}
        self._busy.set()
        try:
            res = run_round(self.store, self.cfg, trigger=trigger,
                            progress_cb=self._log, scale=scale, trial=trial,
                            resume=resume)
            self.last_result = res
            return res
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return {"status": "error", "message": self.last_error}
        finally:
            self._busy.clear()

    # ---------------- 指定日期下载（手动选项 + 独占） ----------------
    def trigger_specified_preview(self, opts: dict) -> dict:
        """实时预览：按手动选项连 REINS 查询，返回条数 + 样本（不落库）。
        带 busy 守卫，避免与自动轮次 / 下载互相打架。
        """
        if self._busy.is_set():
            return {"status": "busy", "message": "已有任务在跑，请稍后"}
        self._busy.set()
        try:
            from .crawler import probe_query_options
            res = probe_query_options(self.store, self.cfg, opts,
                                      progress_cb=self._log, limit=8)
            return {"status": "ok", **res}
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            return {"status": "error", "message": str(e)}
        finally:
            self._busy.clear()

    def trigger_specified_download(self, opts: dict, trial: bool = False) -> dict:
        """指定日期正式下载：在 exclusive 内跑（暂停当天下载 + 挡其它触发），
        跑完自动 resume。返回 run_round_options 的统计字典。
        trial=True 时限制抓取量（用于「试下载」/安全验证，不刷爆 REINS 配额）。
        """
        if self._busy.is_set():
            return {"status": "busy", "message": "已有任务在跑，请稍后"}
        with self.exclusive("指定日期下载"):
            from .crawler import run_round_options
            return run_round_options(self.store, self.cfg, opts,
                                     progress_cb=self._log, trial=trial)

    # ---------------- 主循环 ----------------
    def _loop(self):
        s = self._sched()
        if s.get("run_on_start"):
            self._tick("startup")

        while not self._stop.is_set():
            delay = self._next_delay()
            target = _now_tz(self.cfg) + timedelta(seconds=delay)
            self.next_run_at = target
            self._log(f"下一轮：{target.strftime('%H:%M:%S')}")

            # 分片 sleep，便于随时停止；配置热改时由 rearm() 打断重算
            waited = 0.0
            while waited < delay and not self._stop.is_set() and not self._rearm.is_set():
                time.sleep(min(5.0, delay - waited))
                waited += 5.0
            if self._stop.is_set():
                break
            # v1.9.9：配置热改 → 立刻按新参数重算下一轮（不跑轮次、不丢排程）
            if self._rearm.is_set():
                self._rearm.clear()
                self._log("⟳ 下一轮已按新参数重算")
                continue

            if not self._sched().get("enabled"):
                break

            if not self._in_window():
                wait = self._secs_until_window()
                self._log(f"当前不在运行时段（{self.describe()}），等待 {wait//60} 分钟后继续")
                end = time.time() + wait
                while time.time() < end and not self._stop.is_set():
                    time.sleep(10)
                continue

            self._tick("random" if (self._sched().get("mode") == "random") else "interval")

        self._log("调度线程已退出")

    def _tick(self, trigger: str):
        if self._paused:
            # 指定日期下载进行中：让路，本轮不发车
            self._log(f"⏸ 当天的下载暂停中（{self._paused_reason}），本轮跳过")
            return
        self._busy.set()
        try:
            res = run_round(self.store, self.cfg, trigger=trigger,
                            progress_cb=self._log, scale=0.25)
            self.last_result = res
            # v1.5.19：一轮抓完 → 自动把增量推给线上（同事立刻能看到，不用重新发布）。
            #   推失败只记日志，绝不影响本地抓取；也绝不阻塞本轮结束。
            self._auto_push()
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            self._log(f"✗ 本轮异常：{self.last_error}")
        finally:
            self._busy.clear()

    def _auto_push(self):
        """把本轮增量 POST 到线上（后台线程，尽力而为）。

        前提：config.publish.enabled=true 且 mode=auto 且填了 endpoint，否则直接跳过。
        """
        pub = (self.cfg.get("publish") or {})
        if not pub.get("enabled"):
            return
        if str(pub.get("mode") or "").strip() != "auto":
            return
        if not str(pub.get("endpoint") or "").strip():
            return

        def _go():
            try:
                from . import publisher
                r = publisher.publish(self.cfg, self.store.conn, mode="incr",
                                      log=self._log)
                if r.get("ok"):
                    self._log(f"☁ 已推到线上 {r.get('sent')} 行"
                              + ("（本轮无变化，跳过）" if r.get("skipped") else ""))
                else:
                    self._log("☁ 推线上失败：" + ("; ".join(r.get("errors") or []) or "未知"))
            except Exception as e:                                # noqa: BLE001
                self._log(f"☁ 推线上跳过：{type(e).__name__}: {e}")

        try:
            threading.Thread(target=_go, daemon=True).start()
        except Exception as e:                                    # noqa: BLE001
            self._log(f"☁ 推送线程未启动：{e}")
