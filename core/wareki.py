# -*- coding: utf-8 -*-
"""和暦（令和 / 平成 / 昭和）→ 公元年 的轻量转换。

【为什么单独一个模块·非显然】
REINS 的「変更年月日 / 登録年月日」都是和暦（如 `令和8年9月12日`）。
本转换在 **两处** 都要用，且必须口径一致：
  1) `core/store.py`  —— 回填存量变更记录时，把平台変更年月日当作事件日写进 changes.detected_at；
  2) `web/app.py`     —— 模板/接口把变更日展示成 `2026-09-12` 给销售看。
如果各写一份正则，很容易出现"库里存的是 2026-09-12、页面上显示 令和8年"这种对不上的问题。

和暦对照（与 `core/crawler._to_era_ymd` 反向）：
    令和 = AD - 2018   平成 = AD - 1988   昭和 = AD - 1925
"""
from __future__ import annotations

import re

_ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925}
_RE = re.compile(r"(令和|平成|昭和)\s*(\d+)\s*年(?:\s*(\d+)\s*月)?(?:\s*(\d+)\s*日)?")


def to_ad(text, fallback: str = "") -> str:
    """和暦字符串 → 'YYYY' / 'YYYY-MM' / 'YYYY-MM-DD'（能解到哪一级就给到哪一级）。

    - 非和暦字符串（例如已经是 `2026-09-12`）：**原样返回**，方便直接透传。
    - 空值（None / 空串）：返回 `fallback`（默认空串）。
    """
    if text is None:
        return fallback
    s = str(text)
    if not s.strip():
        return fallback
    m = _RE.search(s)
    if not m:
        return s
    out = "%d" % (_ERA_BASE[m.group(1)] + int(m.group(2)))
    if m.group(3):
        out += "-%02d" % int(m.group(3))
        if m.group(4):
            out += "-%02d" % int(m.group(4))
    return out
