# -*- coding: utf-8 -*-
"""AI 抽取三级降级编排：L1 文字层 → L2 本地 OCR → L3 云端模型。

勇哥 D1 决策（2026-09-20）写死在本文件的最前面，**谁都不许改**：
    「先用本地电脑读取或做 OCR，读不出来（本地没有）才发第三方模型。」

因此本文件的核心约束是：
    **L1/L2 出结果了，就绝不允许走到 L3** —— 不是"尽量"，是顺序上的硬短路。
    L3 每次调用都会在 ai_run_log 里留痕（local_hit/cloud_used 分开计数），
    勇哥随时能查「这一晚到底花了几次云端调用」。

三级怎么判
----------
  L1  文字层 ≥ 过闸门字段数阈值（默认 3）→ 收工，0 元
  L2  L1 不够 → 本地 OCR（tesseract jpn）→ 合并 L1，够阈值就收工，0 元
  L3  还不够 → 且 fallback_model=true 且配了 api_key → 云端（花钱，计入 cloud_used）
      否则：不调用，标记 need_cloud=True 交给人工/详情页二次确认

产出结构统一为 fields：{列名: {"value","level","note","source"}}
  level: ok 正常 / warn 存疑→页面**红字** / danger 特别存疑→**红字+黄底**（勇哥口径）
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from . import ai_quality as Q
from . import ocr_local as L2mod
from . import pdf_local_extract as L1mod

SOURCE_TAG = "PDF本地提取"          # 写进 structure.field.source，便于区分 DB 源列


# ---------------------------------------------------------------- 工具
def pdf_hash(pdf_path: str | Path) -> str:
    """PDF 指纹（sha256 前 16 位）。PDF 没变就不重跑 —— 省时省力。"""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _merge_raw(rec_a: dict, rec_b: dict) -> dict:
    """合并两份原始抽取结果（**先到者胜**，不去破坏已取到的值）。"""
    out = dict(rec_b or {})
    for k, v in (rec_a or {}).items():
        out.setdefault(k, v)
    return out


def grade(raw: dict, ctx: dict | None = None) -> tuple[dict, list[tuple[str, str, str]]]:
    """把原始抽取结果过质量闸门 + 模糊认定兜底。

    返回 (fields, rejected)
      fields   —— {col: {"value","level","note","source"}}，可直接落库
      rejected —— [(col, 原值, 驳回原因)]，进「OCR待核对」给人裁决
    """
    ctx = ctx or {}
    fields: dict[str, dict] = {}
    rejected: list[tuple[str, str, str]] = []

    for col, v in (raw or {}).items():
        ok, reason, norm = Q.check(col, v, ctx)
        if ok:
            fields[col] = {"value": norm if norm is not None else v,
                           "level": "ok", "note": "", "source": SOURCE_TAG}
            continue
        # ---- 闸门没过 → ① 特殊 busy 单独标出 ② 模糊认定 ③ 驳回 ----
        if L1mod.is_special(col, str(v)):
            fields[col] = {"value": str(v), "level": "warn",
                           "note": "特殊情况·待勇哥定夺", "source": SOURCE_TAG}
            continue
        fn = L1mod.FUZZY.get(col)
        got = None
        if fn:
            try:
                got = fn(str(v))
            except Exception:                              # noqa: BLE001
                got = None
        if got not in (None, ""):
            # 模糊认定来的值必须红字（勇哥口径：将来应用取值默认不取）
            ok2, reason2, norm2 = Q.check(col, got, ctx)
            fields[col] = {"value": norm2 if norm2 is not None else got,
                           "level": "ok" if ok2 else "warn",
                           "note": "模糊认定：" + (f"{str(v)[:24]} → " if ok2 else "")
                                   + str(got)[:40],
                           "source": SOURCE_TAG}
            continue
        rejected.append((col, str(v)[:60], reason or "未通过质量闸门"))
    return fields, rejected


# ---------------------------------------------------------------- 单物件跑一轮
def run_one(property_no: str, paths: dict, cfg: dict, ai_store=None, *,
            ctx: dict | None = None, force: bool = False,
            allow_cloud: bool | None = None) -> dict:
    """对一个物件跑三级降级。返回统一结果 dict（**不直接落库**，由调用方决定）。

    cfg: {"ocr_enabled","ocr_lang","ocr_dpi","ocr_timeout_sec","fallback_model",
          "min_fields","tesseract_path","ai": {...}}
    allow_cloud: None = 按 cfg.fallback_model；True/False 显式指定（详情页用）
    """
    lc = cfg or {}
    ai_cfg = lc.get("ai") or {}
    min_fields = int(lc.get("min_fields", 3))
    t0 = time.time()

    pdf = Path(paths["attachments"]) / f"{property_no}.pdf"
    if not pdf.exists():
        raise FileNotFoundError(str(pdf))
    h = pdf_hash(pdf)

    # ---- ① PDF 没变 & 非强制 → 跳过（D3 不重复烧 CPU）----
    if ai_store is not None and not force:
        if ai_store.pdf_hash_of(property_no) == h:
            return {"ok": True, "skipped": True, "no": property_no,
                    "reason": "pdf_hash 未变", "route": [], "fields": {},
                    "rejected": [], "pdf_hash": h, "ms": 0}

    route: list[str] = []
    raw: dict = {}
    text = ""

    # ---- ② L1 文字层 ----
    try:
        text = L1mod.text_of(pdf)
    except RuntimeError as e:                              # 缺 pymupdf 等
        return {"ok": False, "no": property_no, "error": str(e),
                "route": route, "fields": {}, "rejected": [], "pdf_hash": h,
                "ms": int((time.time() - t0) * 1000)}
    if text:
        # 两条路都跑：正则（擅长会社住所/E-MAIL/注意事項）+ 标签切分（擅长表格型）
        raw = _merge_raw(L1mod.extract(text), L2mod.extract_lines(text))
        if raw:
            route.append("L1:local-text")

    fields, rejected = grade(raw, ctx)

    # ---- ③ L2 本地 OCR（仅当 L1 不够）----
    if len(fields) < min_fields and lc.get("ocr_enabled", True):
        try:
            ocr_txt = L2mod.ocr_pdf(
                pdf, lang=lc.get("ocr_lang", "jpn"),
                dpi=int(lc.get("ocr_dpi", 300)),
                timeout=int(lc.get("ocr_timeout_sec", 60)),
                tesseract=str(lc.get("tesseract_path") or ""),
                max_pages=int(lc.get("ocr_max_pages", 1)))
            if ocr_txt.strip():
                route.append("L2:local-ocr")
                raw2 = _merge_raw(raw, L2mod.extract_lines(ocr_txt))
                f2, r2 = grade(raw2, ctx)
                if len(f2) >= len(fields):                 # 只接受不劣化的结果
                    fields, rejected = f2, r2
        except Exception as e:                             # noqa: BLE001
            route.append(f"L2:failed({type(e).__name__})")

    # ---- ④ L3 云端兜底（D1：必须本地失败才来）----
    need_cloud = len(fields) < min_fields
    cloud_ok = allow_cloud if allow_cloud is not None else bool(lc.get("fallback_model"))
    used_cloud = False
    model_name = ""
    if need_cloud and cloud_ok and (ai_cfg.get("api_key") or "").strip():
        try:
            from . import ai_extract                       # noqa: PLC0415
            result, meta = ai_extract.extract_property(property_no, paths, ai_cfg)
            model_name = meta.get("model", "")
            route.append(f"L3:cloud({model_name})")
            used_cloud = True
            cloud_result = result
        except Exception as e:                             # noqa: BLE001
            route.append(f"L3:failed({type(e).__name__})")
            cloud_result = None
    else:
        cloud_result = None

    out = {
        "ok": True, "skipped": False, "no": property_no,
        "route": route, "fields": fields, "rejected": rejected,
        "pdf_hash": h, "ms": int((time.time() - t0) * 1000),
        "local_hit": len(fields) >= min_fields,
        "cloud_used": used_cloud,
        "need_cloud": need_cloud and not used_cloud,
        "cloud_result": cloud_result,
        "model": model_name,
    }
    return out


# ---------------------------------------------------------------- 落库
def to_structure(fields: dict, meta: dict[str, dict] | None = None) -> dict:
    """把 {col: {...}} 包成 ai_structure 的 groups 形状（groups/label 与既有数据一致）。

    meta —— ai_store.field_meta()，提供分组 key 与四语言 label。
           没有 meta 时退化成单个「PDF补充」分组（功能不减，只是分组信息少）。
    """
    meta = meta or {}
    groups: dict[str, dict] = {}
    order: list[str] = []

    def _grp_key(col: str) -> tuple[str, dict]:
        m = meta.get(col) or {}
        key = m.get("group_key") or "comment"
        if key not in groups:
            groups[key] = {
                "key": key,
                "label": {"zh": m.get("group_cn") or "其他",
                          "zh_tw": m.get("group_cn") or "其他",
                          "en": key, "ja": m.get("group_cn") or "その他"},
                "fields": [],
            }
            order.append(key)
        return key, m

    for col, f in (fields or {}).items():
        key, m = _grp_key(col)
        groups[key]["fields"].append({
            "col": col,
            "label": {"zh": m.get("zh") or col, "zh_tw": m.get("zh_tw") or col,
                      "en": m.get("en") or col, "ja": m.get("ja") or col},
            "value": f.get("value"),
            "level": f.get("level", "ok"),
            "note": f.get("note", ""),
            "source": f.get("source") or SOURCE_TAG,
            "edited": False,
        })
    return {"groups": [groups[k] for k in order]}


def save_fields(ai_store, property_no: str, result: dict, *,
                note: str = "") -> dict:
    """把 pipeline 结果落到 AI 独立库（**人工值由 upsert 内部保护**，见 R1）。

    注意：radar/conclusion 属解读层，本地管线不产出 → 沿用旧值，绝不置空。
    """
    if ai_store is None:
        return {"ok": False, "error": "没有 ai_store"}
    old = ai_store.get(property_no) or {}
    try:
        fmeta = ai_store.field_meta()
    except Exception:                                      # noqa: BLE001
        fmeta = {}
    structure = to_structure(result.get("fields") or {}, fmeta)

    anomalies = [{"field": c, "level": f.get("level", "warn"),
                  "reason": f.get("note") or "本地抽取标记"}
                 for c, f in (result.get("fields") or {}).items()
                 if f.get("level") != "ok"]

    # 本轮读到但没过闸门的列 → 传给 upsert，旧值不得再当 ok 用（会留「月額」这种错值）
    rejected_map = {c: r for c, _v, r in (result.get("rejected") or [])
                    if isinstance(c, str)}

    merge_info = ai_store.upsert(
        property_no,
        structure=structure,
        radar=old.get("radar") or {},
        conclusion=old.get("conclusion") or "",
        anomalies=anomalies,
        overall=old.get("overall"),
        source_file=result.get("pdf_hash") or "",
        extracted_at=(result.get("extracted_at") or ""),
        rejected=rejected_map,
    )
    return {"ok": True, "merge": merge_info}


def to_extraction_result(result: dict, ctx: dict | None = None) -> dict:
    """转成主库 ai_extractions 需要的 result 结构（详情页「AI 解读」卡片用的形状）。

    {"title", "highlights", "fields":[{"label","value","level"}], "notes"}
    """
    ctx = ctx or {}
    fields = result.get("fields") or {}
    local = not result.get("cloud_used")
    return {
        "title": ctx.get("title") or "",
        "highlights": [],
        "fields": [{"label": c, "value": str(f.get("value")),
                    "level": f.get("level", "ok"),
                    "note": f.get("note", "")}
                   for c, f in fields.items()],
        "notes": ("本地提取完成，未消耗 Token" if local
                  else f"云端模型补充（{result.get('model','')}）"),
    }
