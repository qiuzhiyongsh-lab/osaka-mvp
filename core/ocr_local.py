# -*- coding: utf-8 -*-
"""L2 · 本地 OCR（图像型 PDF → 文字）。**离线、不限量、零 token**。

这层是 PRD 25 的第二级：约 35% 的 REINS 单页是图片版 PDF（文字层为空），
只能把页面渲成图再 OCR。引擎跑在**本机**，不联网、不计费。

引擎选型（踩过的坑，别改回去了）
--------------------------------
  ✅ tesseract 5.5.3 + jpn.traineddata —— 实测「鉄骨造 / 民泊運営中 / オートロック」全对。
  ❌ rapidocr-onnxruntime —— 它的中文模型会把日文汉字写成简体：
     「鉄骨造 → 铁骨造」，整份资料面目全非。**日文场景禁用**。

三类必须修的 OCR 损伤（来自 repair_text 的实测）
---------------------------------------------
  1. 字符被空格打散：「管 理 費 5, 0 0 0円/月」
  2. 复选框 □/■ 被认成汉字：「田 所 在 地」（「田」是框）
  3. 千分位逗号被认成句点：「12.890円」实为「12,890円」
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

# 取值列及标签候选（含 OCR 常见错字变体）—— 与 fill_ai_cols 的 26 列对齐
TARGET: dict[str, list[str]] = {
    "物件コメント": ["物件コメント", "セールスポイント", "物件説明", "アピールポイント", "物件の特徴"],
    "構造（原文）": ["構造・階数", "規模・構造", "建物構造", "構造"],
    "総戸数（戸）": ["総区画数", "一棟の総戸数", "総戸数", "維戸数"],
    "専有面積（坪）": ["専有面積", "占有面積", "壁芯面積"],
    "バルコニー": ["バルコニー面積", "ﾊﾞﾙｺﾆｰ面積", "ツレコニー面積", "バルコニー", "ﾊﾞﾙｺﾆｰ", "ツレコニー"],
    "管理組合": ["管理組合", "管理組含"],
    "管理体制": ["管理体制", "管理形態", "管理方式", "管理人状況", "管理人"],
    "積立金（円/月）": ["修繕積立金", "修繕積立費", "修籍積立金", "積立金", "経費積立金"],
    "その他（円/月）": ["その他の費用", "他の費用内訳", "その他費用", "その他", "その他の"],
    "管理費・積立金等合計（円/月）": ["管理費・積立金等合計", "管理費・修繕積立金等合計",
                                    "管理費等合計", "費用合計", "合計"],
    "駐車場": ["敷地内駐車場", "駐車場料金", "駐車場"],
    "現況": ["現況", "現状"],
    "契約形態": ["契約形態", "契約の種類", "貸借形態", "契約期間"],
    "設備・条件": ["設備・条件", "設備", "条件"],
    "建物引渡": ["引渡可能時期", "建物引渡", "引渡時期", "引渡日", "引き渡し", "引渡"],
    "報酬形態": ["報酬形態", "報酬"],
    "手数料": ["仲介手数料", "手数料"],
    "広告": ["広告の可否", "広告"],
    "担当者連絡先": ["担当者名", "担当者", "担当"],
    "会社住所": ["会社住所", "所在地"],
    "FAX": ["ＦＡＸ", "FAX", "Fax"],
    "E-MAIL": ["E-Mail", "E-MAIL", "E-mail", "Email", "Mail", "メール"],
    "注意事項": ["注意事項", "留意事項", "備考"],
    # v1.9.34（PRD-25 扩字段）：勇哥点名要的「具体地址+区」「更详细的地铁站」「会所」
    "所在地": ["所在地", "住居表示"],
    "最寄駅1": ["最寄駅", "最寄り駅", "交通"],
    "共用施設": ["共用施設", "共有施設", "集会所", "ライブラリー", "ジム", "プール"],
    # v1.9.36 加列
    "ペット（飼育可）": ["ペット", "愛玩動物", "飼育可", "ペット可"],
    "権利形態": ["権利形態", "権利", "土地権利"],
    "前面道路": ["前面道路", "接道", "道路幅員"],
    "セキュリティ": ["セキュリティ", "防犯", "防犯設備"],
    "駐輪場・バイク置場": ["駐輪場", "バイク置場", "駐輪"],
    "エレベーター": ["エレベーター", "EV"],
    "建蔽率": ["建ぺい率", "建蔽率", "建ペい率"],
    "容積率": ["容積率"],
    "地目": ["地目"],
    "バルコニー方向": ["バルコニー方向", "方角", "方位"],
    "リフォーム履歴": ["リフォーム", "改装", "内装リフォーム"],
}

# 仅用于切分边界的邻居标签（不取值，但能挡住跨标签黏连）
BOUNDARY: list[str] = [
    "所在地", "物件番号", "物件No", "交通", "最寄駅", "価格", "販売価格", "土地権利",
    "権利形態", "所有権", "間取り", "間取", "築年月", "建築年月", "新築年月日", "築年",
    "所在階", "地上階", "階部分", "専有面積", "面積", "総戸数", "管理会社", "分譲会社",
    "施工会社", "設計会社", "事業主", "売主", "施主", "管理費", "表面利回り", "利回り",
    "年間賃料", "月額賃料", "賃料", "用途地域", "地目", "都市計画", "建ぺい率", "容積率",
    "接道", "私道", "地積", "土地面積", "取引態様", "取引形態", "取引業態", "業態",
    "会社名", "業者名", "免許番号", "宅建免許", "TEL", "ＴＥＬ", "Tel", "電話", "定休日", "営業時間",
    "ﾊﾞﾙｺﾆｰ", "バルコニー",
    "方角", "方位", "バルコニー方向", "ペット", "権利形態", "接道", "前面道路",
    "セキュリティ", "駐輪場", "バイク置場", "エレベーター", "建蔽率", "リフォーム", "改装",
]

# 最长优先，避免「管理費」抢在「管理費・積立金等合計」前面命中
_ENTRIES: list[tuple[str, str | None]] = (
    [(l, c) for c, ls in TARGET.items() for l in ls] + [(l, None) for l in BOUNDARY]
)
_ENTRIES.sort(key=lambda x: -len(x[0]))


def find_tesseract(explicit: str = "") -> str:
    """定位 tesseract.exe：显式路径 > PATH > 默认安装目录。找不到返回 ''。"""
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("tesseract")
    if found:
        return found
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              "/usr/bin/tesseract", "/usr/local/bin/tesseract"):
        if Path(p).exists():
            return p
    return ""


# ---------------------------------------------------------------- OCR 文本修复
_CJK = r"\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef"
_CHECKBOX_NOISE = "田画画囝囮囲図圏國因团由申甲日曰目自一|｜!！>」』）)】"


def collapse_gaps(s: str) -> str:
    """数字内部空格「5, 0 0 0」→「5,000」；CJK 之间空格「管 理 費」→「管理費」。"""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(\d)\s+(\d)", r"\1\2", s)
        s = re.sub(r"(\d)\s*,\s*", r"\1,", s)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(rf"([{_CJK}])\s+([{_CJK}])", r"\1\2", s)
    return s


def strip_checkbox(line: str) -> str:
    """去掉行首复选框噪音（□ 被认成「田」等汉字）。"""
    return re.sub(rf"^[\s{_CHECKBOX_NOISE}]{{0,6}}(?=\S)", "", line)


def fix_thousands(s: str) -> str:
    """千分位句点还原：「12.890円」→「12,890円」（仅三位尾分组）。"""
    return re.sub(r"(\d)\.(\d{3})(?=[^\d]|$)", r"\1,\2", s)


def repair(txt: str) -> str:
    """整段 OCR 文本修复（noop-safe）。"""
    lines = [collapse_gaps(strip_checkbox(ln)) for ln in (txt or "").splitlines()]
    s = "\n".join(lines)
    s = fix_thousands(s)
    return re.sub(r"[ \t]+", " ", s)


# ---------------------------------------------------------------- 渲染 + 识别
def render_pdf_pages(pdf_path: str | Path, dpi: int = 300,
                     max_pages: int = 1) -> list[bytes]:
    """把 PDF 前 N 页渲成 PNG 字节。**渲染是最慢的一步**，默认只渲首页。"""
    try:
        import pymupdf                                     # noqa: PLC0415
    except ImportError as e:                               # pragma: no cover
        raise RuntimeError("缺依赖 pymupdf：请先执行 pip install pymupdf") from e
    out: list[bytes] = []
    doc = pymupdf.open(str(pdf_path))
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            out.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    finally:
        doc.close()
    return out


def ocr_png(png_bytes: bytes, lang: str = "jpn", timeout: int = 60,
            tesseract: str = "", psm: int = 6) -> str:
    """单张图 → 文本。psm 6 = 假定为统一文本块（这批チラシ实测最好）。"""
    exe = find_tesseract(tesseract)
    if not exe:
        raise RuntimeError(
            "未找到 tesseract：请安装后把路径填到 config.yaml → "
            "local_extract.tesseract_path（本机默认 C:\\Program Files\\Tesseract-OCR\\tesseract.exe）")
    with tempfile.TemporaryDirectory() as td:
        img = Path(td) / "page.png"
        img.write_bytes(png_bytes)
        try:
            r = subprocess.run(
                [exe, str(img), "stdout", "-l", lang, "--psm", str(psm)],
                capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ""
        if r.returncode != 0:
            raise RuntimeError("tesseract 失败：" + (r.stderr.decode("utf-8", "ignore")[:200]))
        return r.stdout.decode("utf-8", errors="ignore")


def ocr_pdf(pdf_path: str | Path, *, lang: str = "jpn", dpi: int = 300,
            timeout: int = 60, tesseract: str = "", max_pages: int = 1,
            psms: Iterable[int] = (6, 3)) -> str:
    """PDF → OCR 文本（多张/多 psm 结果合并，给后续取标签更多机会）。

    为什么跑两个 psm：psm 6（整块）对竖排/多栏更稳，psm 3（全自动分页）
    对标准表格更全。两份都留下，按行合并，抽取值时取首个有效值。
    """
    pages = render_pdf_pages(pdf_path, dpi=dpi, max_pages=max_pages)
    chunks: list[str] = []
    for png in pages:
        for psm in psms:
            t = ocr_png(png, lang=lang, timeout=timeout, tesseract=tesseract, psm=psm)
            if t.strip():
                chunks.append(t)
    return repair("\n".join(chunks))


# ---------------------------------------------------------------- 按标签位置切分值
def clean_val(v: str) -> str:
    # 行首斜杠也是这批 OCR 的常见噪声：「/空室」「/大阪市...」（ checkbox 竖线被认成 /）
    v = re.sub(r"^[\s:：・•．,，、|｜/／\]\)）】」』]+", "", v)
    v = re.sub(r"\s+", " ", v)
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)     # 非法控制字符（写 xlsx 会崩）
    return v.strip(" 　:：・")


_STRUCT_HEAD = ["鉄骨鉄筋コンクリート造", "鉄筋鉄骨コンクリート造", "鉄筋コンクリート造",
                "鉄骨コンクリート造", "軽量鉄骨造", "重量鉄骨造", "鉄骨造", "木造",
                "コンクリート造", "ブロック造", "ＲＣ造", "RC造", "ＳＲＣ造", "SRC造",
                "Ｓ造", "S造", "Ｗ造", "W造"]


def _num(s):
    if s in (None, ""):
        return None
    m = re.search(r"[\d][\d,]*", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:                                      # noqa: BLE001
        return None


def extract_lines(txt: str) -> dict[str, object]:
    """按【标签出现位置】切分值域（不依赖「标签后必须有冒号」）。

    OCR 常把「構造:RC造」输出成「構造RC造」，正则会漏；这里的值域边界
    = 下一个已知标签的出现位置，能挡住跨标签黏连。
    """
    rec: dict[str, object] = {}
    lines = [l.strip() for l in repair(txt or "").splitlines()]
    for li, line in enumerate(lines):
        if not line:
            continue
        hits, i = [], 0
        while i < len(line):
            m = None
            for lab, col in _ENTRIES:
                if line.startswith(lab, i):
                    m = (i, i + len(lab), lab, col)
                    break
            if m:
                hits.append(m)
                i = m[1]
            else:
                i += 1
        if not hits:
            continue
        for k, (s0, e0, lab, col) in enumerate(hits):
            if col is None:
                continue
            end = hits[k + 1][0] if k + 1 < len(hits) else len(line)
            val = clean_val(line[e0:end])
            if not val and li + 1 < len(lines):            # 值被挤到下一行
                nxt = lines[li + 1].strip()
                if nxt and not any(nxt.startswith(l) for l, _ in _ENTRIES):
                    val = clean_val(nxt)
            if not val or len(val) > 160 or col in rec:
                continue
            rec[col] = val

    # 数值列归一化 + 合理性闸门
    for c in ("積立金（円/月）", "その他（円/月）", "管理費・積立金等合計（円/月）"):
        if rec.get(c) is not None:
            f = _num(rec[c])
            if f is None or f <= 0 or f > 5_000_000:
                rec.pop(c, None)
            else:
                rec[c] = int(f)
    if rec.get("総戸数（戸）") is not None:
        f = _num(rec["総戸数（戸）"])
        if f is None or f <= 0 or f > 5000:
            rec.pop("総戸数（戸）", None)
        else:
            rec["総戸数（戸）"] = int(f)
    if rec.get("専有面積（坪）"):
        m = re.search(r"([\d,]+\.?\d*)\s*坪", str(rec["専有面積（坪）"]))
        if m:
            rec["専有面積（坪）"] = float(m.group(1).replace(",", ""))
        else:
            rec.pop("専有面積（坪）", None)
    if rec.get("バルコニー") and re.search(r"\d{4,}", str(rec["バルコニー"])):
        rec.pop("バルコニー", None)                        # 抽到金额/过长 → 丢弃
    if rec.get("構造（原文）"):
        for h in _STRUCT_HEAD:
            if h in str(rec["構造（原文）"]):
                rec["構造"] = h
                break
    return rec


if __name__ == "__main__":                                 # 单份自测
    import sys
    if len(sys.argv) > 1:
        t = ocr_pdf(sys.argv[1])
        print(f"[L2] OCR {len(t)} 字符")
        for k, v in extract_lines(t).items():
            print(f"  {k} = {v!r}")
