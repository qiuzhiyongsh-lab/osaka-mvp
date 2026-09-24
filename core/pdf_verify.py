# -*- coding: utf-8 -*-
"""PDF 串号校验核心（v1.9.77 · PRD osaka_mvp_pdf_mismatch_prd.md F1/F2/F4/F7）。

判据严格复刻 `_diag_pdf_audit2.py`（2026-09-24 全库审计已实测：129 高置信串号）。
本模块是「落盘闸门(F2) / 上云闸门(F4) / 重抓修复(F5) / 审计标注(F7)」共用的纯函数层，
不含任何 IO 副作用（不写库、不上云、不下载）；副作用由调用方（pipeline / pdf_cloud / tools）负责。

判据（与审计脚本逐字一致）：
- norm：NFKC 归一 + 去所有空白
- strip_prefix：地址去「大阪府大阪市 / 大阪府 / 大阪市」前缀
- pdf_text_of：pymupdf 抽文字层（延迟 import；无文字层返回 ''）
- 自身命中：楼名≥5 字直接子串匹配，或地址命中（含「丁目」容错）
- 反查：PDF 文字层命中「别的房源」楼名（≥5 字）→ 串号
"""
from __future__ import annotations

import re
import unicodedata

# 地址前缀（去前缀后再做片段匹配，避免「大阪府大阪市」这类公共前缀污染）
PREFIXES = ("大阪府大阪市", "大阪府", "大阪市")


def norm(s) -> str:
    """NFKC 归一 + 去掉所有空白（全角空格/半角空格/换行都清掉）。"""
    return "" if not s else "".join(unicodedata.normalize("NFKC", s).split())


def strip_prefix(a: str) -> str:
    """地址去「大阪府大阪市 / 大阪府 / 大阪市」前缀。"""
    a = norm(a)
    for p in PREFIXES:
        if a.startswith(p):
            return a[len(p):]
    return a


def pdf_text_of(path: str) -> str:
    """返回 PDF 文字层；无文字层（扫描件）/打不开返回 ''；异常以 '__ERR__' 前缀返回。"""
    try:
        import pymupdf
        d = pymupdf.open(path)
        try:
            t = "".join(pg.get_text() for pg in d)
        finally:
            d.close()
        return t
    except Exception as e:  # noqa: BLE001
        return "__ERR__" + str(e)


def _own_hit(addr: str, bn: str, text: str) -> bool:
    """PDF 文字层里是否出现「本房源自身」的地址或楼名（含丁目容错）。"""
    if bn and len(bn) >= 5 and bn in text:
        return True
    if addr:
        if addr in text:
            return True
        m = re.search(r"([^市区町村]{1,6}[区市町村])(.*)$", addr)
        if m:
            core = m.group(0)
            nodan = re.sub(r"[0-9]{1,3}丁目$", "", core)
            if len(core) >= 5 and core in text:
                return True
            if len(nodan) >= 5 and nodan in text:
                return True
    return False


def verify_gate(property_no: str, rec: dict, pdf_path: str,
                bnames: list) -> tuple:
    """落盘/上云闸门：判定一份刚下载/待上传的 PDF 是否真的属于 property_no。

    返回 (verdict, note)，verdict ∈：
      'match'      —— 自家地址/楼名命中，可放心落盘/上云
      'mismatch'   —— 命中「别的房源」楼名，串号！应拒落盘 + 告警
      'unverified' —— 自身不命中、也未反查到别人（信息不足，标 pdf_unverified）
      'no_text'    —— 无文字层（扫描件），无法校验，标 pdf_unverified
      'unknown'    —— DB 本房源既无地址也无楼名，无法校验，标 pdf_unverified

    `bnames`：[(归一楼名, 物件番号), ...]，来自 store.all_building_names()（楼名≥5 字）。
    仅当自身不命中时才做跨房源反查（成本最低路径优先）。
    """
    addr = strip_prefix(rec.get("address") or "")
    bn = norm(rec.get("building_name") or "")
    if not addr and not bn:
        return "unknown", "DB 无地址无楼名"
    nt = norm(pdf_text_of(pdf_path))
    if len(nt) < 60 or nt.startswith("__ERR__"):
        return "no_text", "无文字层/打开失败"
    if _own_hit(addr, bn, nt):
        return "match", "自家地址/楼名命中"
    others = sorted({b for b, pno in bnames if pno != property_no and b and b in nt})
    if others:
        return "mismatch", "PDF 实为: " + ";".join(others[:3])
    return "unverified", "自身不命中且未反查到别人"


# verify_full 与 verify_gate 同判据（审计/修复脚本复用同一函数即可）
verify_full = verify_gate
