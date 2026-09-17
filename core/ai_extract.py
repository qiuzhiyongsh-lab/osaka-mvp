# -*- coding: utf-8 -*-
"""AI 提取：把房源 PDF 转成结构化 JSON（v1.7.4 · PRD 09 需求 4）。

定位
----
- PDF 有两层：文字层（表格文字，直接读）+ 图片层（扫描件/户型图，要视觉模型）。
- 提取结果只写 ai_extractions 派生表，**绝不改 properties 主表**（不覆盖人工数据）。
- 模型走 OpenAI 兼容协议，默认火山方舟（豆包）；换 base_url/model 即可兼容
  DeepSeek（纯文本）或本地 Ollama（离线，合规最稳）。

依赖（用到才装，缺了会给中文提示）::
    pip install pymupdf openai

单份试跑（不写库）::
    python core/ai_extract.py "data/attachments/300140629916.pdf" --api-key <Key>
批量跑见 tools/ai_extract_batch.py。
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

PROMPT_VERSION = "pv-2026-09-17-v1"

# 输出 schema（对详情页「AI 解读」卡片的约定）：
# {
#   "title":      广告标题/物件名一行,
#   "highlights": [3~6 条卖点，中文],
#   "fields":     [{"label": 中文名, "value": 原文或中文}, ...],
#   "notes":      备注/合规提示（可空）
# }
SYSTEM_PROMPT = """你是日本不动产广告单页（チラシ）的信息提取助手。从给定 PDF 内容中提取结构化信息。
规则：
1. 只提取单页上真实出现的信息，看不清/没有的项不要编造（宁缺勿错）。
2. 用中文输出 label；value 保留数字与单位原样（万円/㎡/帖/円），日文专名（駅名、社名）保留原文。
3. 输出严格 JSON：{"title": "...", "highlights": ["...", ...], "fields": [{"label": "...", "value": "..."}], "notes": "..."}
4. highlights 放 3~6 条最有卖点的信息（楼层/角户/駅直結/降价/开发商/品牌施工等）。
5. fields 覆盖：価格、所在地、交通、専有面積、間取り、所在階、構造、築年月、用途地域、
   土地権利、管理費、修繕積立金、事業主/施工/管理、設備・特徴、中介公司、中介电话（有才放）。
"""


def read_pdf(pdf_path: str | Path) -> tuple[str, bytes | None]:
    """读 PDF → (文字层全文, 第一页 PNG 字节)。文字层为空（扫描件）时调用方要传图给视觉模型。

    缺 pymupdf 时抛 RuntimeError（中文提示装依赖）。
    """
    try:
        import pymupdf
    except ImportError as e:
        raise RuntimeError("缺依赖 pymupdf：请先执行 pip install pymupdf openai") from e
    doc = pymupdf.open(str(pdf_path))
    texts, first_png = [], None
    for i, page in enumerate(doc):
        texts.append(page.get_text())
        if i == 0:
            first_png = page.get_pixmap(dpi=150).tobytes("png")
    doc.close()
    return "\n".join(texts).strip(), first_png


def call_model(text: str, first_png: bytes | None, ai_cfg: dict) -> dict:
    """调多模态模型 → 解析出 dict。缺 openai 包时抛 RuntimeError。"""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("缺依赖 openai：请先执行 pip install pymupdf openai") from e
    client = OpenAI(
        api_key=ai_cfg["api_key"],
        base_url=ai_cfg.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3",
    )
    content: list[dict] = []
    if text:
        content.append({"type": "text",
                        "text": f"PDF 文字层内容：\n{text}\n\n请按规则输出 JSON。"})
    if first_png:
        content.append({"type": "text",
                        "text": "文字层为空或不含所需信息，请再从页面图片中提取。"})
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/png;base64,"
                                            + base64.b64encode(first_png).decode()}})
    resp = client.chat.completions.create(
        model=ai_cfg.get("model") or "doubao-seed-1.6",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
    )
    raw = (resp.choices[0].message.content or "").strip()
    # 容错：剥掉可能的 ```json 围栏
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def extract_property(property_no: str, paths: dict, ai_cfg: dict) -> tuple[dict, dict]:
    """对单个物件跑 AI 提取。返回 (result, meta)。PDF 不存在抛 FileNotFoundError。"""
    pdf = Path(paths["attachments"]) / f"{property_no}.pdf"
    if not pdf.exists():
        raise FileNotFoundError(str(pdf))
    t0 = time.time()
    text, first_png = read_pdf(pdf)
    result = call_model(text, first_png, ai_cfg)
    meta = {"model": ai_cfg.get("model") or "doubao-seed-1.6",
            "prompt_version": PROMPT_VERSION,
            "cost_ms": int((time.time() - t0) * 1000)}
    return result, meta


if __name__ == "__main__":
    # 单份试跑入口：python core/ai_extract.py <pdf路径> --api-key <Key> [--model <模型ID>]
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--model", default="doubao-seed-1.6")
    ap.add_argument("--base-url", default="https://ark.cn-beijing.volces.com/api/v3")
    a = ap.parse_args()
    text, png = read_pdf(a.pdf)
    print(f"[info] 文字层 {len(text)} 字符；第一页图 {'有' if png else '无'}")
    out = call_model(text, png, {"api_key": a.api_key, "model": a.model, "base_url": a.base_url})
    print(json.dumps(out, ensure_ascii=False, indent=2))
