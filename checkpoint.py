# -*- coding: utf-8 -*-
"""存档检查点 / 回滚工具（AI 改代码前必跑）

两条线的约定
------------
本项目分两条线：
  · 产品线 = docs/** 与根目录 README.md（PRD 稿、方案、沟通记录）
  · 研发线 = core/** web/** config.yaml 等源码与脚本
本工具**一次执行就把两条线一起备份**（外加房源库数据），并分别报告各线改了什么。
理由：拆成两个仓库会让回滚变成半截（代码退了、文档没退），比不回滚更危险。

它做三件事
----------
1. 备份房源库 data/jproperty.db → .backups/db/（保留最近 20 份，内容相同不重复存）
2. 按「产品线 / 研发线」分别清点改动
3. 两条线一起 git 提交，形成一个检查点

用法（在本目录下）
------------------
    python checkpoint.py "改了什么"        存档一个检查点（两条线一起）
    python checkpoint.py --lines          看两条线各自的状态
    python checkpoint.py --list           看所有检查点
    python checkpoint.py --diff           看当前未存档的改动
    python checkpoint.py --restore HEAD~1 回到上一个检查点（会先自动存档现场）
    python checkpoint.py --restore 03de262    回到指定检查点
    python checkpoint.py --status         看当前整体状态

注意：不需要重启服务，本脚本不碰正在运行的服务进程。
"""
from __future__ import annotations

import glob
import hashlib
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "jproperty.db"
BACKUP_DIR = ROOT / ".backups" / "db"
KEEP_BACKUPS = 20
MAX_LIST = 8                     # 每线最多列出几个文件名

PRODUCT_LABEL = "产品线"
CODE_LABEL = "研发线"
OTHER_LABEL = "其他"

# 内容类别 → 归类规则
PRODUCT_DIRS = ("docs/",)
PRODUCT_ROOT_FILES = ("README.md",)
CODE_DIRS = ("core/", "web/")
CODE_EXTS = (".py", ".bat", ".yaml", ".yml", ".txt", ".js", ".css", ".html", ".json", ".gitignore")


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def find_git() -> str:
    """找 git：先看 PATH，再找本机 WorkBuddy 自带的 PortableGit。"""
    p = shutil.which("git")
    if p:
        return p
    for pat in (
        r"C:\Users\25374\.workbuddy\binaries\PortableGit\**\cmd\git.exe",
        r"C:\Users\25374\.workbuddy\binaries\PortableGit\**\bin\git.exe",
    ):
        hits = glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    print("[错误] 找不到 git。请确认 WorkBuddy 的 PortableGit 还在原位。")
    sys.exit(2)


GIT = None


def git(*args: str, check: bool = False) -> str:
    r = subprocess.run([GIT, "-C", str(ROOT), *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        print("[git 失败] %s\n%s%s" % (" ".join(args), r.stdout, r.stderr))
        sys.exit(r.returncode)
    return (r.stdout or "").strip()


def classify(path: str) -> str:
    """把一个仓库内路径归到「产品线 / 研发线 / 其他」。"""
    p = path.replace("\\", "/")
    if p.startswith(PRODUCT_DIRS) or p in PRODUCT_ROOT_FILES:
        return PRODUCT_LABEL
    if p.startswith(CODE_DIRS):
        return CODE_LABEL
    if "/" in p:                                  # 其他子目录
        return OTHER_LABEL
    if p == ".gitignore" or os.path.splitext(p)[1].lower() in CODE_EXTS:
        return CODE_LABEL
    return OTHER_LABEL


def dirty_entries() -> list[tuple[str, str]]:
    """返回 [(状态, 路径)]，已处理重命名与引号。"""
    out = git_raw("status", "--porcelain", "-uall")   # -uall：未跟踪目录展开成具体文件
    items = []
    for line in out.splitlines():
        if not line.strip():
            continue
        code, _, path = line[:2], line[2:3], line[3:]
        path = path.strip()
        if " -> " in path:                        # 重命名：取新名
            path = path.split(" -> ")[-1]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        items.append((code.strip(), path))
    return items


def group_by_line(items: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {PRODUCT_LABEL: [], CODE_LABEL: [], OTHER_LABEL: []}
    for code, path in items:
        groups[classify(path)].append((code, path))
    return groups


def git_raw(*args: str) -> str:
    """同 git()，但**不做 strip** —— 供解析 git 输出用。

    坑：`git status --porcelain` 的首行可能是「 M README.md」，前导空格是有意义的
    （表示"已修改未暂存"）。若对整段输出 strip()，首行会变成「M README.md」，
    按 XY<空格>PATH 解析就会少掉路径的第一个字符（实测踩过：README.md → EADME.md）。
    """
    r = subprocess.run([GIT, "-C", str(ROOT), *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout or ""


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_db() -> str:
    """备份房源库；内容与已有备份相同则跳过。返回一句人话描述。"""
    if not DB.exists():
        return "房源库不存在，跳过备份"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    digest = _md5(DB)
    for f in BACKUP_DIR.glob("jproperty_*.db"):
        if digest[:8] in f.name:
            return "房源库与已有备份一致，跳过（%s）" % f.name
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / ("jproperty_%s_%s.db" % (ts, digest[:8]))
    shutil.copy2(DB, dst)
    old = sorted(BACKUP_DIR.glob("jproperty_*.db"),
                 key=lambda p: p.stat().st_mtime, reverse=True)
    for p in old[KEEP_BACKUPS:]:
        try:
            p.unlink()
        except Exception:
            pass
    kb = DB.stat().st_size / 1024
    return "房源库已备份 → .backups/db/%s（%.0f KB，共留 %d 份）" % (
        dst.name, kb, min(len(old), KEEP_BACKUPS))


def _print_line_block(label: str, items: list[tuple[str, str]], idx: int) -> None:
    scope = "docs/ + README.md" if label == PRODUCT_LABEL else (
        "core/ web/ config.yaml 等" if label == CODE_LABEL else "未归类")
    if not items:
        print("%d) %s（%s）：无改动" % (idx, label, scope))
        return
    print("%d) %s（%s）：%d 个文件" % (idx, label, scope, len(items)))
    for code, path in items[:MAX_LIST]:
        print("     %-2s %s" % (code, path))
    if len(items) > MAX_LIST:
        print("     … 另 %d 个" % (len(items) - MAX_LIST))


def save(message: str) -> None:
    print("— 存档检查点（两条线一起）" + "—" * 22)
    db_note = backup_db()
    print("1) 数据：" + db_note)

    items = dirty_entries()
    if not items:
        print("2) 产品线 / 研发线：都没有需要提交的改动")
        print("   → 当前已经是检查点：%s" % (git("log", "-1", "--oneline") or "(暂无)"))
        return

    groups = group_by_line(items)
    n = 2
    _print_line_block(PRODUCT_LABEL, groups[PRODUCT_LABEL], n)
    n += 1
    _print_line_block(CODE_LABEL, groups[CODE_LABEL], n)
    n += 1
    if groups[OTHER_LABEL]:
        _print_line_block(OTHER_LABEL, groups[OTHER_LABEL], n)
        n += 1

    git("add", "-A", check=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = ["存档时间：%s" % stamp,
            "产品线：%d 个文件" % len(groups[PRODUCT_LABEL]),
            "研发线：%d 个文件" % len(groups[CODE_LABEL]),
            "数据：%s" % db_note,
            "（本检查点由 checkpoint.py 生成，两条线一起备份，用于改坏后回滚）"]
    if groups[OTHER_LABEL]:
        body.insert(3, "其他：%d 个文件" % len(groups[OTHER_LABEL]))
    msg = "%s\n\n%s" % (message or "checkpoint", "\n".join(body))
    git("commit", "-q", "-m", msg, check=True)
    print("%d) 两条线已一起提交 → 新检查点：%s" % (n, git("log", "-1", "--oneline")))
    print("\n回滚方式：python checkpoint.py --restore HEAD~1")


def lines_status() -> None:
    print("两条线状态 —— 产品线（PRD 稿 / 方案）与研发线（源码）\n")
    items = dirty_entries()
    groups = group_by_line(items)
    for label in (PRODUCT_LABEL, CODE_LABEL):
        scope = ("docs/ + README.md" if label == PRODUCT_LABEL else "core/ web/ config.yaml 等")
        pathspec = ["docs"] if label == PRODUCT_LABEL else ["core", "web",
                                                           "config.yaml", "*.py", "*.bat"]
        n_tracked = len([l for l in git("ls-files", *pathspec).splitlines() if l.strip()])
        last = git("log", "-1", "--date=format:%m-%d %H:%M",
                   "--pretty=format:%h  %ad  %s", "--", *pathspec) or "(本线暂无提交)"
        print("── %s（%s）" % (label, scope))
        print("   已入库文件：%d 个" % n_tracked)
        print("   最近一次改动：%s" % last)
        g = groups[label]
        print("   未存档改动：%d 个" % len(g))
        for code, path in g[:MAX_LIST]:
            print("     %-2s %s" % (code, path))
        if len(g) > MAX_LIST:
            print("     … 另 %d 个" % (len(g) - MAX_LIST))
        print()
    both = len(groups[PRODUCT_LABEL]) + len(groups[CODE_LABEL])
    print("一次 python checkpoint.py \"说明\" 会把上面两条线（＋房源库）一起备份。" if both
          else "当前两条线都没有未存档改动；一次 checkpoint 会把两条线＋房源库一起备份。")


def list_points() -> None:
    print("检查点列表（最新在上）：\n")
    out = git("log", "--oneline", "--date=format:%m-%d %H:%M",
              "--pretty=format:%h  %ad  %s")
    for line in out.splitlines():
        print("  " + line)
    n = len(dirty_entries())
    print("\n当前状态：" + ("工作区干净（无未存档改动）" if not n else "有 %d 个未存档改动" % n))


def show_diff() -> None:
    items = dirty_entries()
    if not items:
        print("工作区干净，没有未存档改动。")
        return
    groups = group_by_line(items)
    print("当前未存档改动（共 %d 个文件）：" % len(items))
    for label in (PRODUCT_LABEL, CODE_LABEL, OTHER_LABEL):
        if groups[label]:
            print("\n【%s】" % label)
            for code, path in groups[label]:
                print("  %-2s %s" % (code, path))
    print("\n" + "—" * 46)
    print(git("diff", "--stat"))
    print(git("diff", "--cached", "--stat"))


def status() -> None:
    print("仓库目录：" + str(ROOT))
    print("最近检查点：" + (git("log", "-1", "--oneline") or "(暂无)"))
    items = dirty_entries()
    groups = group_by_line(items)
    print("未存档改动：产品线 %d 个 ｜ 研发线 %d 个%s" % (
        len(groups[PRODUCT_LABEL]), len(groups[CODE_LABEL]),
        "  ｜ 其他 %d 个" % len(groups[OTHER_LABEL]) if groups[OTHER_LABEL] else ""))
    for code, path in items[:MAX_LIST]:
        print("   %-2s %s" % (code, path))
    if len(items) > MAX_LIST:
        print("   … 另 %d 个" % (len(items) - MAX_LIST))
    print("房源库备份：%d 份（.backups/db/）" % len(list(BACKUP_DIR.glob("jproperty_*.db"))))


def restore(ref: str) -> None:
    if not ref:
        print("[用法] python checkpoint.py --restore HEAD~1   （或 --restore <检查点编号>）")
        sys.exit(1)
    cur = git("log", "-1", "--oneline")
    print("— 回滚到检查点：%s " % ref + "—" * 20)
    if dirty_entries():
        git("add", "-A")
        git("commit", "-q", "-m",
            "safety: 回滚前的现场存档（%s）" % datetime.now().strftime("%Y-%m-%d %H:%M"))
        print("1) 已把当前未存档改动存成 safety 提交（万一回滚错了还能回来）")
    else:
        print("1) 工作区干净，无需现场存档")
    target = git("rev-parse", "--short", ref)
    if not target:
        print("[错误] 找不到检查点：%s" % ref)
        sys.exit(1)
    git("reset", "--hard", ref, check=True)
    print("2) 已回滚两条线：%s  →  %s" % (cur, git("log", "-1", "--oneline")))
    print("\n提醒：本 .py 改动需重启 start_mvp.bat 才生效；"
          "模板/JS/CSS 热重载，刷新页面即可。"
          "\n      文档（产品线）改动立即生效，直接打开文件即可。")


def main() -> None:
    global GIT
    _utf8_stdout()
    GIT = find_git()
    if not (ROOT / ".git").exists():
        print("[错误] 本目录还不是 git 仓库。")
        sys.exit(2)
    args = sys.argv[1:]
    if not args:
        save("checkpoint: 手动存档")
    elif args[0] in ("--lines", "-L"):
        lines_status()
    elif args[0] in ("--list", "-l"):
        list_points()
    elif args[0] == "--diff":
        show_diff()
    elif args[0] in ("--status", "-s"):
        status()
    elif args[0] == "--restore":
        restore(args[1] if len(args) > 1 else "")
    elif args[0] in ("-h", "--help"):
        print(__doc__)
    else:
        save(" ".join(args))


if __name__ == "__main__":
    main()
