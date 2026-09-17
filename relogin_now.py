# -*- coding: utf-8 -*-
"""立即用本机保存的账号刷新一次 REINS 会话（等价于页面上的「测试登录」按钮）。

· 会用「有窗口」的 Edge 打开（REINS 拦截无头浏览器）——屏幕上会闪出一个窗口，几秒后自动关闭。
· 只做一件事：填表 → 点登录 → 保存会话到 data/session.json。
· 不触发任何抓取，不碰正在运行的服务进程。
"""
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp")
sys.path.insert(0, str(ROOT))

from core import config as cfgmod      # noqa: E402
from core import credentials as creds  # noqa: E402
from core.auth import Auth             # noqa: E402


def main() -> int:
    cfg = cfgmod.load()
    paths = cfgmod.paths(cfg)
    st = creds.status(paths["root"])
    print("[账号] saved=%s name=%s mid=%s url=%s"
          % (st.get("saved"), st.get("account_name"), st.get("member_id_mask"),
             st.get("site_name")), flush=True)
    if not st.get("saved"):
        print("NO_CREDS", flush=True)
        return 2

    c = creds.load(paths["root"])
    auth = Auth(cfg, paths["session"])
    print("[登录地址] " + auth.login_url(), flush=True)
    print("[会话文件] %s（上次写入 %s）"
          % (paths["session"], paths["session"].stat().st_mtime
             if paths["session"].exists() else "不存在"), flush=True)

    ok = auth.auto_login(c["member_id"], c["password"], wait_seconds=60,
                         log=lambda m: print("  " + m, flush=True))
    print("RESULT: " + ("OK" if ok else "FAIL"), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
