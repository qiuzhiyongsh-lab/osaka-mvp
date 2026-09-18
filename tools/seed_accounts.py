# -*- coding: utf-8 -*-
"""一次性账户播种（PRD-19 §17.3 · 线下建号 → 同步上云）。

用法（在 osaka-mvp 根目录执行）：
    python tools/seed_accounts.py             # 播种/更新默认账户（保护线上已改密码）
    python tools/seed_accounts.py --force     # 连已改过密码的账户也重置随机码
    python tools/seed_accounts.py --export    # 只重导出种子 json（不动账户）

做四件事：
1. `secrets` 真随机给每个账户生成 **12 位**一次性随机码（大小写+数字，剔除
   易混字符 0/O/1/l/I）
2. 按 username UPSERT 写进本机主库 accounts 表
3. 导出 `accounts.seed.json`（**只含 pbkdf2 哈希，绝不含明文**）到本机 data/，
   并同步一份到发布工程 `osaka-house-publish/app_local/data/`
4. **仅此一次**在屏幕上打印「用户名 = 随机码」对照表 → 勇哥线下（微信/口头）分发

⚠ 安全约定：随机码**不落任何文件**（不进 git / 日志 / 记忆），只有屏幕这一次。
   请不要把本脚本输出重定向到日志文件。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
os.chdir(ROOT)                       # 保证 config 里相对 output_root 解析一致
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# 默认账户（PRD-19 §17.1 · 勇哥拍板：含演示禁用态的 wangfang）
#   cleared: 1=正常  0=待改密（首次登录强制改密）
#   disabled: 1=禁用（不可登录）
# ---------------------------------------------------------------------------
SEED_ACCOUNTS = [
    {"username": "yongge",  "role": "admin", "display_name": "勇哥",   "cleared": 1, "disabled": 0},
    {"username": "zhangyc", "role": "staff", "display_name": "张云川", "cleared": 1, "disabled": 0},
    {"username": "liwen",   "role": "staff", "display_name": "李雯",   "cleared": 0, "disabled": 0},
    {"username": "wangfang", "role": "staff", "display_name": "王芳",  "cleared": 1, "disabled": 1},
]

_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _gen12() -> str:
    """12 位一次性随机码（去易混字符）。"""
    return "".join(secrets.choice(_ALPHABET) for _ in range(12))


def main() -> int:
    ap = argparse.ArgumentParser(description="PRD-19 §17 账户播种（线下 → 同步上云）")
    ap.add_argument("--force", action="store_true",
                    help="强制重置已有账户的随机码（会覆盖已改过的密码）")
    ap.add_argument("--export", action="store_true",
                    help="只重导出种子 json，不改账户、不生成新随机码")
    args = ap.parse_args()

    from core.config import load, paths
    from core import accounts as acc

    cfg = load()
    P = paths(cfg)
    acc.init(P["db"])

    issued = []
    stat = None
    if not args.export:
        rows = []
        for a in SEED_ACCOUNTS:
            code = _gen12()
            issued.append((a["username"], a["display_name"], code))
            rows.append({**a, "pwd_hash": acc.hash_password(code)})
        stat = acc.upsert_seed(rows, actor="system(seed)", force_reset=args.force)

    # 导出种子（只含哈希）
    seed_local = P["root"] / "accounts.seed.json"
    data = acc.export_seed(str(seed_local))

    pub = ROOT.parent / "osaka-house-publish" / "app_local" / "data" / "accounts.seed.json"
    pub_done = False
    if pub.parent.exists():
        import shutil
        shutil.copy2(seed_local, pub)
        pub_done = True

    print("=" * 66)
    print("账户播种完成（PRD-19 §17.3）")
    print(f"  主库           : {P['db']}")
    if stat:
        print(f"  新建 {stat['inserted']} 条 / 更新 {stat['updated']} 条 "
              f"（改码 {stat['pwd_reset']} · 保留已改密码 {stat['pwd_kept']} · 跳过 {stat['skipped']}）")
    print(f"  种子(仅哈希)   : {seed_local}")
    if pub_done:
        print(f"  已同步发布工程 : {pub}")
    else:
        print(f"  ⚠ 发布工程 data 目录不存在，未同步：{pub}")
    print(f"  种子内账户数   : {len(data['accounts'])}")
    if issued:
        print("-" * 66)
        print("【用户名 = 初始随机码】此表只在屏幕上出现这一次，请立即线下分发：")
        for u, d, c in issued:
            print(f"    {u:<10} {d or '':<6} {c}")
        print("  ⚠ 随机码未写入任何文件 / 日志 / git；员工首次登录后建议立即改密。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
