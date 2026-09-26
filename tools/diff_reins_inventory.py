#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REINS 标准清单 vs 本地库 精确 diff 工具
======================================
用法:
  python tools/diff_reins_inventory.py <勇哥清单文件.txt/.md/.csv>
  (清单里可以是纯 12 位番号列，也可以是带「売一戸建/中古テラス/売マンション」等
   类别标题的原样粘贴；脚本会自动提取番号并按最近类别标注)

输出:
  - 每个类别下「清单有但本地库没有」的缺失番号
  - 汇总：各类别缺失数 / 总缺失数
  - 反向（可选）：本地库有但清单没有的番号（用于发现清单遗漏）
"""
import sqlite3, re, sys, os

DB = os.path.join(os.path.dirname(__file__), "..", "data", "jproperty.db")

# 类别识别：按行扫描，遇到类别关键词则切换当前类别，其后番号归入该类别
CATEGORY_RULES = [
    (("テラス",), "テラス(露台)"),
    (("一戸建", "戸建"), "売一戸建"),
    (("マンション",), "売マンション"),
    (("土地", "売地"), "売土地"),
]

def detect_category(line, current):
    for keys, name in CATEGORY_RULES:
        if any(k in line for k in keys):
            return name
    return current

def parse_inventory(text):
    """返回 [(番号, 类别)] 列表（去重保留首次出现类别）"""
    lines = text.splitlines()
    cur = "未分类"
    result = []
    seen = {}
    for ln in lines:
        cur = detect_category(ln, cur)
        for m in re.finditer(r"\b(\d{12})\b", ln):
            no = m.group(1)
            if no not in seen:
                seen[no] = cur
                result.append((no, cur))
    return result, seen

def local_nos():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT property_no, property_subtype, is_active FROM properties")
    rows = cur.fetchall()
    con.close()
    return rows

def main():
    if len(sys.argv) < 2:
        print("用法: python tools/diff_reins_inventory.py <清单文件>")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print("文件不存在:", path)
        sys.exit(1)
    text = open(path, encoding="utf-8", errors="ignore").read()
    inv, seen = parse_inventory(text)
    local = local_nos()
    local_set = {r[0] for r in local}
    local_subtype = {r[0]: (r[1], r[2]) for r in local}

    print(f"清单解析到番号: {len(inv)} 个（去重）")
    print(f"本地库番号: {len(local_set)} 个")
    print("=" * 60)

    # 缺失：清单有，本地无
    missing = [(no, cat) for no, cat in inv if no not in local_set]
    # 反向：本地有，清单无
    inv_set = set(seen.keys())
    extra = [no for no in local_set if no not in inv_set]

    # 按类别汇总缺失
    from collections import defaultdict
    miss_by_cat = defaultdict(list)
    for no, cat in missing:
        miss_by_cat[cat].append(no)

    print(f"【缺失】清单有但本地库没有: 共 {len(missing)} 个")
    for cat in sorted(miss_by_cat):
        nos = miss_by_cat[cat]
        print(f"  ▶ {cat}: {len(nos)} 个")
        print("    " + "  ".join(nos))
    print("-" * 60)
    print(f"【反向】本地库有但清单没有: {len(extra)} 个（供你核对清单是否漏列）")

    # 落盘缺失清单
    out = os.path.join(os.path.dirname(__file__), "..", "data", "_diff_missing_nos.txt")
    with open(out, "w", encoding="utf-8") as f:
        for no, cat in missing:
            f.write(f"{no}\t{cat}\n")
    print(f"\n缺失清单已导出: {out}")

if __name__ == "__main__":
    main()
