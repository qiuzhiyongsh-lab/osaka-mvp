# -*- coding: utf-8 -*-
"""登录与会话（真实模式的核心，也是最不能乱来的部分）。

设计原则（红线，不解释、不妥协）：
  · 只做两件事：① 你手工登录一次 → 存下"门禁卡"；② 之后复用这张卡
  · 绝不自动输账号密码、绝不重试登录、绝不换 IP、绝不识别验证码
  · 检测到会话失效 → 立即停机 + 告警，等人来处理
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


class SessionExpired(RuntimeError):
    """会话失效：本轮立即中断，由上层记一条失败日志。"""


class PlaywrightMissing(RuntimeError):
    """没有安装 Playwright（属于环境问题，不是抓取失败）。
    用 RuntimeError 而不是 SystemExit，避免在后台线程里静默打断调度循环。"""


class LoginPageUnreachable(RuntimeError):
    """登录页打不开 / 表单找不到（配置或登录入口地址问题，不是代码崩）。"""


def friendly_error(e: BaseException) -> str:
    """把 Playwright / 网络层的原始报错翻译成人话。

    网页"上次测试结果"里直接展示这段，避免用户看到 TypeError / net::ERR 这类天书。
    """
    msg = str(e)
    if "ERR_HTTP_RESPONSE_CODE_FAILURE" in msg:
        # 2026-09-11 真机实测结论：REINS 会拦截**无头浏览器**，返回畸形响应。
        # 同一个地址：命令行 curl 200、有头 Edge 200、无头 Chromium 必失败。
        return ("登录页被站点拒绝加载（无头浏览器被拦截）。\n"
                "已确认为 REINS 的反自动化策略：无窗口浏览器会被返回异常响应。\n"
                "处理：本程序已改为「有窗口」模式打开浏览器，请确认你屏幕上能看到 "
                "Edge 窗口弹出；若仍失败，稍等 1–2 分钟再点一次（站点偶发抖动）。")
    if "ERR_CONNECTION_REFUSED" in msg:
        return "连不上登录服务器（连接被拒绝）。请检查网络 / VPN 后重试。"
    if "ERR_NAME_NOT_RESOLVED" in msg:
        return "登录域名解析失败（DNS）。请检查网络后重试。"
    if "Timeout" in msg or "timeout" in msg:
        return ("页面加载超时——可能是网络慢，或站点正在维护"
                "（REINS 维护时段：日本时间 22:00–次日 07:00）。")
    if "net::ERR" in msg:
        code = msg.split("net::", 1)[-1].strip()
        return f"浏览器打开登录页失败（{code}）。请检查网络后重试。"
    # 兜底：直接返回异常信息即可——本项目的自定义异常（LoginPageUnreachable 等）
    # 本身已携带中文说明，不要再拼类名（避免页面出现 "✗ LoginPageUnreachable: ..." 这类天书）。
    # 完整堆栈永远在 data/logs/server.log，排查不靠这一行。
    return msg


def net_error_hint(e) -> str:
    """把浏览器网络层异常翻成「能照着做」的中文说明（非网络错误则原样返回）。

    实测 2026-09-15 早：用户机器上 Windows 系统代理 = 127.0.0.1:7890（Edge 只读这个），
    但代理软件没运行 → 该端口无人监听 → Edge 一律 net::ERR_PROXY_CONNECTION_FAILED。
    这种失败和"抓取代码坏了"长得一模一样，必须靠这条诊断区分开。
    """
    msg = str(e) or type(e).__name__
    if "ERR_PROXY_CONNECTION_FAILED" not in msg:
        return msg
    lines = ["连不上 REINS：浏览器走的是你 Windows 的系统代理，但那个代理现在连不上。"]
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enable = winreg.QueryValueEx(k, "ProxyEnable")[0]
        server = winreg.QueryValueEx(k, "ProxyServer")[0]
        lines.append(f"  · 系统代理开关：{'开' if enable else '关'}，代理地址：{server}")
        if server:
            host, _, port = str(server).rpartition(":")
            try:
                import socket
                s = socket.socket()
                s.settimeout(2)
                s.connect((host or "127.0.0.1", int(port)))
                s.close()
                lines.append("  · 该端口**有**服务在听（代理软件是开着的）→ 可能是代理本身没连通外网。")
            except Exception:                                # noqa: BLE001
                lines.append("  · 该端口**没有**任何服务在听 → 你的代理软件（Clash / v2ray 等）没启动或已退出。")
        lines.append("怎么办（二选一）：")
        lines.append("  1) 把代理软件启动，确认它能正常访问 https://system.reins.jp；")
        lines.append("  2) 若走直连就能上：Windows「Internet 选项 → 连接 → 局域网(LAN)设置」"
                     "里取消「为 LAN 使用代理服务器」。")
        lines.append("  ⚠ 这不是抓取代码的故障——网络通了以后原样重跑即可。")
    except Exception:                                        # noqa: BLE001
        lines.append("  （读不到系统代理设置）请检查代理软件是否运行。")
    return "\n".join(lines)


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:                       # pragma: no cover
        raise PlaywrightMissing(
            "✗ 没有安装 Playwright（浏览器自动化工具）。请执行：\n"
            "    pip install playwright\n"
            "  用 Edge 的话不需要再下载浏览器（playwright install chromium 可跳过）。"
        ) from exc
    return sync_playwright()


class Auth:
    def __init__(self, cfg: dict, session_file: Path):
        self.cfg = cfg
        self.session_path = Path(session_file)
        self.session_path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- 登录地址（可在「账号管理」里改，改完立即生效）----------
    def login_url(self) -> str:
        """解析登录页地址。

        优先顺序：
          ① 「账号管理」页里保存的**网站地址**（你自己填/改的那个，随时可改）；
          ② 兜底：config.yaml 的 site.login_url。

        为什么允许用户改：REINS 的登录地址会随**所属机构**变化
        （形如 …/login/main/KG/GKG001200，末尾段因机构而异），
        换机构或换账号时只改这一格即可，不必去动配置文件。
        """
        cfg_url = (self.cfg.get("site", {}) or {}).get("login_url", "") or ""
        saved = ""
        try:
            from core import credentials as _creds          # noqa: PLC0415
            c = _creds.load(self.session_path.parent) or {}
            saved = (c.get("site_name") or "").strip()
        except Exception:                                   # 读不到就用配置，不影响主流程
            saved = ""
        if saved.lower().startswith(("http://", "https://")):
            return saved
        return cfg_url

    def base_url(self) -> str:
        """站点根地址：从登录地址里取 scheme://host，换了域名也能自动跟上。"""
        from urllib.parse import urlsplit                     # noqa: PLC0415
        cfg_base = (self.cfg.get("site", {}) or {}).get("base_url") or ""
        try:
            sp = urlsplit(self.login_url())
            if sp.scheme and sp.netloc:
                return f"{sp.scheme}://{sp.netloc}"
        except Exception:
            pass
        return cfg_base

    # ---------- 浏览器 ----------
    def launch(self, p, headless: bool):
        channel = (self.cfg.get("browser", {}).get("channel") or "msedge").strip()
        kw = {}
        # v1.5.5：浏览器**必须显式指定代理**。
        #   实测 2026-09-15：进程里 HTTPS_PROXY=http://127.0.0.1:56076 且 urllib 直连 200，
        #   但 Playwright 开的 Edge 一律 net::ERR_PROXY_CONNECTION_FAILED —— Edge 不认
        #   进程环境变量（它读 Windows 系统代理设置），两套不是一回事。
        #   显式传 proxy 后立刻 200 / title=REINS IP。
        #   优先级：config.browser.proxy（显式指定，最可靠） > 进程环境变量（临时注入，可能变）。
        #   ⚠ 注意：双击 start_mvp.bat 启动的 8765 进程里通常**没有** HTTP_PROXY 环境变量，
        #     那种情况下只能靠 config 显式配置，或靠 Windows 系统代理（Edge 默认行为）。
        _px = str((self.cfg.get("browser", {}) or {}).get("proxy") or "").strip()
        if not _px:
            _px = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                   or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "").strip()
        if _px:
            kw["proxy"] = {"server": _px}
        if channel.lower() in ("edge", "msedge", "microsoft edge"):
            try:
                return p.chromium.launch(channel="msedge", headless=headless, **kw)
            except Exception as e:                   # 退回内置 Chromium
                print(f"⚠ 启动 Edge 失败（{e}），改用内置 Chromium。", file=sys.stderr)
        return p.chromium.launch(headless=headless, **kw)

    def new_context(self, browser):
        if not self.session_path.exists():
            raise SessionExpired(
                f"没有会话文件 {self.session_path}。\n"
                f"  {self.cfg.get('auth', {}).get('manual_hint', '')}")
        return browser.new_context(storage_state=str(self.session_path))

    # ---------- 手工登录（整个项目只需一次）----------
    def manual_login(self, wait_seconds: int = 300, interactive: bool = False) -> bool:
        """打开浏览器窗口 → 你手工登录 → 自动检测成功 → 保存会话。

        wait_seconds：最多等这么久（默认 5 分钟）。网页按钮和命令行都用它，
        所以不依赖 input()（网页里没有终端可输入）。
        """
        print("=" * 62)
        print("即将打开浏览器窗口，请手工输入账号密码登录 REINS。")
        print(f"登录成功后程序会自动识别并保存会话（最多等 {wait_seconds} 秒）。")
        print("=" * 62)
        with _sync_playwright() as p:
            browser = self.launch(p, headless=False)
            ctx = browser.new_context()
            page = ctx.new_page()
            try:
                page.goto(self.cfg["site"]["login_url"],
                          wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                print("⚠ 登录页打不开：" + friendly_error(e), file=sys.stderr)
                browser.close()
                return False
            page.wait_for_timeout(800)            # 给 JS 驱动页面一点渲染时间

            if interactive:
                try:
                    input(">>> 登录完成后按回车继续...")
                except Exception:
                    pass

            ok = False
            step = 2
            for _ in range(max(1, int(wait_seconds / step))):
                if self.looks_logged_in(page):
                    ok = True
                    break
                import time as _t
                _t.sleep(step)

            if not ok:
                print("⚠ 未检测到登录成功标志。", file=sys.stderr)
                print(f"  当前地址：{page.url}", file=sys.stderr)
            ctx.storage_state(path=str(self.session_path))
            print(f"✓ 会话已保存到 {self.session_path}")
            browser.close()
            return ok

    # ---------- 状态判断 ----------
    # REINS 在「会话超时」/「浏览器环境不支持」时，会返回一个只有提示文字的短页
    # （标题 REINS IP，正文「セッションがタイムアウトしました。」）。
    # 这类页面必须判为「未登录」，否则抓取会在第一步就静默失败：
    # 页面里没有任何表单 → 条件/検索按钮全找不到 → 每一轮都"成功"入库 0 条。
    _EXPIRED_MARKS = (
        "セッションがタイムアウト",                    # 会话超时
        "お使いの環境はサポートされていません",          # 浏览器环境不受支持
    )

    def expired_reason(self, page) -> str:
        """页面是否是「会话超时 / 环境不支持 / 被踢回登录」这类不可用页。

        正常返回 ""；否则返回一句中文原因。只读、不抛异常。
        用 page.content()（含隐藏节点）而非 inner_text：超时页的两个提示块
        靠 CSS 的 .if-unsupported / .unless-unsupported 二选一显示，直接扫原文最稳。
        """
        try:
            html = page.content() or ""
        except Exception:
            html = ""
        for m in self._EXPIRED_MARKS:
            if m in html:
                if "サポートされていません" in m:
                    return ("浏览器环境不受支持：REINS 返回「お使いの環境はサポートされていません」。"
                            "请确认用的是有窗口的 Edge（本程序默认如此）。")
                return ("登录会话已超时：REINS 返回「セッションがタイムアウトしました」。"
                        "请重新登录后再继续。")
        try:
            if "/login/" in (page.url or ""):
                return "已被跳回登录页，登录会话已失效。请重新登录后再继续。"
        except Exception:
            pass
        return ""

    def looks_logged_in(self, page) -> bool:
        """是否已登录。判据顺序（先排反向证据，再找正向证据，最后才用地址兜底）：

          ⓪ 页面是「会话超时 / 环境不支持」短页 → 一律算未登录（**之前的漏洞就在这**：
             旧逻辑只要地址是 system.reins.jp 且不含 /login/ 就直接返回 True，
             于是超时页被误判成"已登录"，抓取一路静默失败）。
          ① 地址还含 /login/（仍在登录页）→ 未登录；
          ② 页面上出现配置的成功标识文本（默认「メインメニュー」）→ 已登录；
          ③ 兜底：域名是 REINS 且不在登录页 → 已登录。
        """
        if self.expired_reason(page):
            return False
        try:
            url = page.url or ""
        except Exception:
            url = ""
        if "/login/" in url:
            return False
        indicator = self.cfg.get("auth", {}).get("success_indicator", "")
        if indicator:
            try:
                if page.locator(f"text={indicator}").first.is_visible(timeout=1500):
                    return True
            except Exception:
                pass
        return "system.reins.jp" in url

    def _wait_logged_in(self, page, wait_seconds: int, step: int = 2) -> bool:
        import time as _t
        for _ in range(max(1, int(wait_seconds / step))):
            if self.looks_logged_in(page):
                return True
            _t.sleep(step)
        return False

    def assert_logged_in(self, page) -> None:
        if self.looks_logged_in(page):
            return
        # 带上具体原因（超时 / 环境不支持 / 被踢回登录页），别只说"已失效"
        raise SessionExpired(
            self.expired_reason(page)
            or self.cfg.get("auth", {}).get("manual_hint", "会话已失效，请重新登录。"))

    # ---------- 打开登录页（深链重试 + 首页回退）----------
    def goto_login_page(self, page, log=None) -> None:
        """打开 REINS 登录页，直到「密码输入框」出现为止。

        2026-09-11 真机实测：登录深链偶发 ERR_HTTP_RESPONSE_CODE_FAILURE（站点抖动/风控）。
        因此先重试深链，再回退到「首页 → 点 ログイン」这条更接近真人操作的路径。
        """
        say = log or (lambda *_a, **_k: None)
        login_url = self.login_url()
        base_url = self.base_url() or login_url
        last: BaseException | None = None

        for attempt in range(1, 3):
            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_selector("input[type='password']", timeout=20000)
                say(f"· 已打开登录页（深链第 {attempt} 次成功）")
                return
            except Exception as e:                        # noqa: BLE001
                last = e

        say("· 深链登录页打不开，改用「首页 → 点 ログイン」这条真人路径…")
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(1200)
            page.locator("a:has-text('ログイン')").first.click(timeout=8000)
            page.wait_for_selector("input[type='password']", timeout=20000)
            say("· 已通过首页进入登录页")
            return
        except Exception as e:                            # noqa: BLE001
            last = e

        raise LoginPageUnreachable(
            friendly_error(last) if last else "登录页无法打开（未知原因）")

    # ---------- 自动登录（用本地保存的账号）----------
    def auto_login(self, member_id: str, password: str,
                   headless: bool | None = None, wait_seconds: int = 60,
                   log=None) -> bool:
        """用保存的账号自动填表登录 REINS，并保存会话。

        v1.5.5：**必须在独立线程里跑**。
          实测 2026-09-15：会话过期触发自动重登时抛
          「It looks like you are using Playwright Sync API inside the asyncio loop」。
          根因：`open_authed_page` 是在 crawler 的 `with _sync_playwright() as p:` 里面被调用的，
          而自动重登又开了一个 `_sync_playwright()` —— 同一个线程里嵌套 sync_playwright，
          Playwright 直接拒绝。于是「会话过期自动重登」这条 v1.2.1 的自愈路径**从来就没成功过**，
          只会回落成「请手工登录」。
          修法：若当前线程已有 asyncio 事件循环在跑，就把整段重登挪到一个新线程执行
          （新线程有自己的事件循环），跑完把结果带回来。
        """
        try:
            import asyncio as _asyncio
            _asyncio.get_running_loop()
            _in_loop = True
        except RuntimeError:
            _in_loop = False
        if not _in_loop:
            return self._auto_login_impl(member_id, password, headless, wait_seconds, log)

        import threading as _threading
        _box: dict = {}

        def _runner():
            try:
                _box["v"] = self._auto_login_impl(member_id, password,
                                                  headless, wait_seconds, log)
            except BaseException as _e:                       # noqa: BLE001
                _box["e"] = _e

        _t = _threading.Thread(target=_runner, daemon=True)
        _t.start()
        _t.join(timeout=max(60, int(wait_seconds or 60) + 90))
        if "e" in _box:
            raise _box["e"]
        return bool(_box.get("v", False))

    def _auto_login_impl(self, member_id: str, password: str,
                         headless: bool | None = None, wait_seconds: int = 60,
                         log=None) -> bool:
        """用保存的账号自动填表登录 REINS，并保存会话（真正干活的那个）。

        红线：只填表提交、不重复提交、不识别验证码。若页面出现验证码或登录失败，
        返回 False，由上层提示改用「手工登录」。
        选择器来自 config → selectors.login（已按真实 Vue SPA 页面校准）。
        """
        say = log or (lambda *_a, **_k: None)
        headless = (self.cfg.get("browser", {}).get("headless", False)
                    if headless is None else headless)
        if headless:
            say("⚠ REINS 会拦截无头浏览器，当前为无头模式，很可能失败。")

        login_sel = self.cfg.get("selectors", {}).get("login", {})
        id_sel = login_sel.get("id_field", "input[type='text']")
        pw_sel = login_sel.get("pw_field", "input[type='password']")
        submit_sel = login_sel.get("submit", "button:has-text('ログイン')")
        agree_sel = login_sel.get("agree_checkbox", "")

        with _sync_playwright() as p:
            browser = self.launch(p, headless=headless)
            ctx = browser.new_context(locale="ja-JP",
                                      viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            try:
                self.goto_login_page(page, log=say)

                # 填表：Vue SPA 页面没有 name 属性，用稳定的 class 选择器
                try:
                    page.locator(id_sel).first.fill(member_id, timeout=8000)
                    page.locator(pw_sel).first.fill(password, timeout=8000)
                except Exception as e:                    # noqa: BLE001
                    raise LoginPageUnreachable(
                        "登录页已打开，但没找到账号 / 密码输入框（选择器不匹配）。\n"
                        "请把登录页两个输入框的 class 或 id 发我，"
                        "我改 config.yaml → selectors.login。") from e

                # 必勾：所属机构规程与指南（不勾选则登录按钮保持 disabled）
                if agree_sel:
                    try:
                        page.locator(agree_sel).first.click(timeout=5000)
                        say("· 已勾选「所属機構の規程及びガイドラインを遵守します」")
                    except Exception:
                        say("· 未找到「遵守条款」复选框（可能已勾选或页面结构变化）")

                # 等登录按钮解除禁用（Vue 校验通过后才会启用）
                try:
                    page.wait_for_function(
                        "() => { var b=[].slice.call(document.querySelectorAll('button'))"
                        ".filter(function(x){return x.textContent.indexOf('ログイン')>=0;})[0];"
                        " return b && !b.disabled; }", timeout=8000)
                except Exception:
                    pass

                page.locator(submit_sel).first.click(timeout=8000)
                say("· 已提交登录，等待跳转…")

                ok = self._wait_logged_in(page, wait_seconds)
                if ok:
                    ctx.storage_state(path=str(self.session_path))
                    say(f"✓ 自动登录成功，会话已保存到 {self.session_path}")
                else:
                    say("✗ 自动登录未成功（可能需要验证码或账号有误）。"
                        "请改用「手工登录」。")
                return ok
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    # ---------- 会话自愈（v1.2.1）：过期就用本地账号自动重登一次 ----------
    def has_saved_account(self) -> bool:
        """本机是否存有可用于自动登录的账号（会员ID + 密码）。"""
        try:
            from . import credentials as creds
            c = creds.load(self.session_path.parent)
        except Exception:
            return False
        return bool(c and c.get("member_id") and c.get("password"))

    def try_relogin(self, log=None) -> bool:
        """会话失效时的自愈：用本地保存的账号自动重登一次，刷新会话文件。

        成功 → True；失败 / 没有账号 → False（**不抛异常**，由调用方决定是否停机）。
        红线不变：只填表提交一次，不重试、不识别验证码、不换 IP。
        """
        say = log or (lambda *_a, **_k: None)
        if not self.has_saved_account():
            say("· 本机未保存账号，无法自动重新登录（请到「账号管理」录入，或点「手工登录」）")
            return False
        try:
            from . import credentials as creds
            c = creds.load(self.session_path.parent)
        except Exception as e:                              # noqa: BLE001
            say("· 读取本地账号失败：" + friendly_error(e))
            return False
        say("· 检测到登录会话已过期 → 用本地保存的账号自动重新登录…")
        try:
            ok = bool(self.auto_login(c["member_id"], c["password"], log=say))
        except Exception as e:                              # noqa: BLE001
            say("· 自动重新登录失败：" + friendly_error(e))
            return False
        if ok:
            say("✓ 已自动重新登录，会话已刷新")
        else:
            say("✗ 自动重新登录未成功（可能需要验证码）——请点「手工登录」")
        return ok

    # ---------- 拿一个「已确认登录」的页面（所有抓取入口统一走这里）----------
    def _new_ctx_page(self, browser):
        ctx = self.new_context(browser)
        page = ctx.new_page()
        page.set_default_timeout(self.cfg.get("browser", {}).get("timeout_ms", 30000))
        return ctx, page

    def _open_search_page(self, page) -> None:
        """打开検索条件入力页并确认仍在登录态；没登录则抛 SessionExpired。

        v1.5.5：网络层失败（尤其代理）单独做**可行动的中文诊断**——
        以前只会抛一行 `net::ERR_PROXY_CONNECTION_FAILED`，用户完全看不懂，
        误以为是"抓取坏了"。现在直接说清：系统代理指向哪、那个端口活着没、该怎么做。
        """
        try:
            page.goto(self.cfg["site"]["search_url"],
                      wait_until="domcontentloaded", timeout=30000)
        except Exception as e:                                # noqa: BLE001
            raise SessionExpired(net_error_hint(e)) from e
        page.wait_for_timeout(1200)
        self.assert_logged_in(page)

    def open_authed_page(self, browser, log=None):
        """打开一个「已确认登录」的 page，返回 (ctx, page)。

        这是修掉「会话一过期、自动更新就整轮空转」的关键：
          ① 正常 → 直接返回；
          ② 发现 REINS 回了「セッションがタイムアウト」→ 立即用本地账号自动重登一次，
             成功则用新会话重建 context 再开一次页面；仍失效才抛 SessionExpired。
        这样无人值守时大概率能自己扛过去，不必每轮都人工点「重新登录」。
        """
        say = log or (lambda *_a, **_k: None)
        self.ensure_session()                    # 没有会话文件时先尝试用本地账号自动登录
        ctx, page = self._new_ctx_page(browser)
        try:
            self._open_search_page(page)
            return ctx, page
        except SessionExpired as exc:
            first = str(exc)
        except Exception:
            try:
                ctx.close()
            except Exception:
                pass
            raise

        say("✗ 会话已失效：" + first)
        try:
            ctx.close()
        except Exception:
            pass
        base = first.replace("请重新登录后再继续。", "").strip()
        if not self.try_relogin(log=say):
            raise SessionExpired(
                base + " 已尝试用本地账号自动重新登录但未成功，"
                       "请到「账号管理」页点「手工登录」。")
        ctx, page = self._new_ctx_page(browser)
        try:
            self._open_search_page(page)
        except SessionExpired as exc2:
            raise SessionExpired("自动重新登录后仍无法进入：" + str(exc2)) from exc2
        return ctx, page

    # ---------- 会话保障（供抓取前调用）----------
    def ensure_session(self) -> None:
        """保证有一个可用会话：已有则复用；没有则尝试用本地账号自动登录。

        都不行 → 抛 SessionExpired（提示去录入账号或手工登录）。
        """
        if self.session_path.exists():
            return
        try:
            from . import credentials as creds
            c = creds.load(self.session_path.parent)
        except Exception:
            c = None
        if c and c.get("member_id"):
            print("· 没有会话文件，尝试用本地账号自动登录…", flush=True)
            # 不传 headless → 用 config.browser.headless（默认 false=有头；REINS 拦截无头）
            ok = self.auto_login(c["member_id"], c["password"],
                                 log=lambda m: print(m, flush=True))
            if ok:
                return
            raise SessionExpired(
                "自动登录失败（可能需要验证码或账号有误）。\n"
                "  请到「账号管理」点「手工登录」，或重新录入账号后重试。")
        raise SessionExpired(
            self.cfg.get("auth", {}).get("manual_hint",
                                        "尚无会话，请先录入账号或手工登录。"))
