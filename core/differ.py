# -*- coding: utf-8 -*-
"""增量与变更识别。

两个职责：
  1. 增量：列表页扫到的编号，跟本地库比对，挑出"没抓过"的（省 99% 请求）
  2. 变更：同一编号这次抓到的价格/内容 vs 上次快照，判断是新盘 / 降价 / 涨价 / 其他变更
"""
from __future__ import annotations

import hashlib
import json


def _is_int(v) -> bool:
    """宽松判定：int / 纯数字字符串（可带千分位或空格）都算；bool 排除。"""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace(" ", "")
        return s != "" and s.lstrip("-").isdigit()
    return False


def fingerprint(rec: dict) -> str:
    """把一条房源的关键字段压成一个短字符串（指纹）。指纹变了=信息变了。"""
    keys = ["price", "exclusive_area", "land_area", "layout", "floor",
            "building_year_month", "built_year_month", "address", "property_subtype"]
    payload = {k: rec.get(k) for k in keys if rec.get(k) not in (None, "")}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def pick_queue(items: list[tuple[str, str]], store) -> list[tuple[str, str]]:
    """从列表页结果里挑出需要抓详情的（本地没有 或 还没抓过详情的）。"""
    queue, seen = [], set()
    for no, url in items:
        if not no or no in seen:
            continue
        seen.add(no)
        row = store.get_property(no)
        if row is not None and row["detail_json"]:
            continue          # 已有完整详情 → 跳过（这就是增量）
        queue.append((no, url))
    return queue


def classify(old_row, new_rec: dict, new_fp: str) -> list[dict]:
    """比较新旧，返回变更列表（可能多条）。"""
    out: list[dict] = []

    if old_row is None:
        # 本机首次入库。但 REINS 自带「変更前価格」(previous_price) 与「変更年月日」(change_date)：
        # 若平台早已改过价（previous_price 与当前 price 不同），这套房对平台是"变更"而非新盘，
        # 应标「变更」、与平台对齐（详见 PRD 6.11）。仅当 previous_price 为空/相等时才算真·新盘。
        prev = new_rec.get("previous_price")
        cur = new_rec.get("price")
        if _is_int(prev) and _is_int(cur) and int(prev) != int(cur):
            ctype = "price_down" if int(prev) > int(cur) else "price_up"
            out.append({"type": ctype, "field": "price", "old": prev, "new": cur})
            return out
        out.append({"type": "new", "field": None, "old": None,
                    "new": cur})
        return out

    old_price = old_row["price"]
    new_price = new_rec.get("price")
    if old_price not in (None, "") and new_price not in (None, ""):
        if int(new_price) < int(old_price):
            out.append({"type": "price_down", "field": "price",
                        "old": old_price, "new": new_price})
        elif int(new_price) > int(old_price):
            out.append({"type": "price_up", "field": "price",
                        "old": old_price, "new": new_price})

    # 内容指纹变化（非价格的其他改动）
    # v1.4.0：列表模式(list-only)下 new_rec 没有 detail_json，而 old 的可能有，
    # 两者指纹不可比（列表只含 price/address 等，详情含 land_area 等）→ 跳过，避免误报"变更"。
    if out == [] and "detail_json" in new_rec:
        last = None
        try:
            last = old_row["detail_json"]
        except Exception:
            pass
        if last:
            try:
                prev_fp = json.loads(last).get("__fp__")
            except Exception:
                prev_fp = None
            if prev_fp and prev_fp != new_fp:
                out.append({"type": "modified", "field": None, "old": prev_fp, "new": new_fp})

    return out
