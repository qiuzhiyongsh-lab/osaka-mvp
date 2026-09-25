# -*- coding: utf-8 -*-
"""抓取 → 落库 的统一管道（真实模式与演示模式共用同一段逻辑）。

一次"入库一批"做的事：
  1. 与本地库比对（增量 / 变更识别）
  2. 写 properties 主档 + snapshots 快照 + changes 变更流水
  3. 把 PDF 复制/落到 data/attachments/<物件番号>.pdf
  4. 更新水位线
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from . import config as cfgmod
from . import differ
from .store import now
from .pdf_verify import verify_gate


def _ward_of(address: str) -> str:
    """从地址里抠出「区」。例：大阪府大阪市西区… → 西区"""
    if not address:
        return ""
    i = address.find("市")
    j = address.find("区", i + 1 if i >= 0 else 0)
    if i >= 0 and j > i:
        return address[i + 1:j + 1]
    if j >= 0:
        return address[:j + 1]
    return ""


def _source_url(property_no: str, site: dict) -> str:
    """列表阶段（尚无详情页）时，给一个**诚实**的 REINS 入口：物件番号検索页 GBK004100。

    v1.4.3：不再伪造 ``GBK001210?bknno=`` 検索頁当「详情直链」——REINS 详情页**没有**
    永久 URL，只能在登录会话内点击「詳細」打开（GET 带 bknno= 一律 E2171 报错）。
    伪造的 bknno= 链接会把用户带到一个検索頁/报错頁；更危险的是若被回灌进 _fetch_detail，
    Playwright 会把検索頁当詳細頁解析 → 写脏 detail_json（v1.4.2 的致命回归，已回退）。
    真正抓到详情时，_fetch_detail 会用自己的 ``dp.url``（GBK003100）覆盖此值。
    """
    return f"{site.get('base_url','https://system.reins.jp')}/main/BK/GBK004100"


def ingest(items: list[dict], store, cfg: dict, run_id: int,
           watermark_key: str = "default") -> dict:
    """把一批房源记录写进本地。items: list[dict]（真实/演示都一样）。"""
    paths = cfgmod.paths(cfg)
    att_dir: Path = paths["attachments"]
    stats = {"scanned": len(items), "fetched": 0, "new": 0, "changed": 0,
             "pdf_saved": 0, "errors": []}
    seen = []
    # v1.9.77 F2/F4：跨房源楼名反查表（每批取一次，落盘/上云闸门共用）。
    # 失败则退化为空表（闸门退化为「不反查」，仅自检自家命中）。
    try:
        bnames = store.all_building_names()
    except Exception:  # noqa: BLE001
        bnames = []
    today = datetime.now().strftime("%Y-%m-%d")
    today_iso = datetime.now().strftime("%Y%m%d")

    for rec in items:
        no = rec.get("property_no")
        if not no:
            continue
        seen.append(no)
        lo = bool(rec.pop("_list_only", False))   # v1.4.0：阶段1列表模式，不写详情/PDF
        try:
            # v1.9.87 修复：Store.get_property 返回 sqlite3.Row，**没有 .get()**。
            # 旧代码 old.get("price") / old.get("pdf_path") 会在 ingest 时炸
            # AttributeError（且被 try 吞掉 → 表现为"changed=1 实际却没写进去"）。
            # 统一转成 dict，后续 old["x"] / old.get("x") 都安全。
            _raw = store.get_property(no)
            old = dict(_raw) if _raw is not None else None

            # ---- 1. 落 PDF（仅详情模式；列表模式跳过，留待阶段2）----
            pdf_path = ""
            if (not lo) and cfg.get("download", {}).get("pdf", True):
                pdf_path = _save_pdf(rec, no, att_dir)
                if pdf_path:
                    stats["pdf_saved"] += 1
                    # v1.9.77 F2：PDF 落盘一致性闸门
                    # 新下载的 PDF 必须自证属于本房源，否则可能是 run74 式「列表重排→开错房」
                    # 造成的串号。match→放行；unverified/no_text/unknown→放过但标 pdf_unverified
                    # 待 F5 重抓；mismatch→拒落盘（删掉错文件 + 告警），绝不让错误 PDF 挂上本房源。
                    abs_path = att_dir / f"{no}.pdf"
                    try:
                        verdict, note = verify_gate(no, rec, str(abs_path), bnames)
                    except Exception as e:  # noqa: BLE001
                        verdict, note = "no_text", "verify_gate 异常: %s" % e
                    if verdict == "mismatch":
                        try:
                            abs_path.unlink(missing_ok=True)
                        except Exception:  # noqa: BLE001
                            pass
                        store.add_notification(
                            "pdf_mismatch",
                            "番号 %s 新抓 PDF 实为「%s」，已拒落盘（疑似串号，待 F5 重抓）" % (no, note))
                        pdf_path = ""          # 不放行：不写 pdf_path、不触发上云钩子
                        rec.pop("pdf_bytes", None)
                        rec.pop("pdf_src", None)
                        stats["pdf_saved"] -= 1
                    elif verdict == "match":
                        # 自证成功：清掉历史遗留的待复核标记
                        store.set_pdf_unverified(no, "match")
                    elif verdict in ("unverified", "no_text", "unknown"):
                        store.set_pdf_unverified(no, verdict, note)

            # ---- 2. 组装主档 ----
            rec.setdefault("ward", _ward_of(rec.get("address", "")))
            rec.setdefault("source_url", _source_url(no, cfg.get("site", {})))
            # v1.5.4：**不再拿下载日冒充「登録年月日」**。
            # 旧代码在这里 setdefault(today_iso)，于是"这轮没抓到详情"的房源被盖上
            # 「登録日 = 今天」（本机实测 1019/1103 行全是这种假日期）。
            # 用户明确要求"日期＝平台新建/变更日，不是下载日"，假日期会让筛选与详情页同时失真。
            # 现在：抓不到就是 NULL（未知），reg_date_iso 也保持 NULL —— 宁可空着，绝不造假。
            # v1.5.3：image_count 语义 = **-1 未知 / >=0 已知张数**。
            # 旧代码无条件写 int(... or 0)，把"还没抓到详情"写成 0 → 界面全显示「画像 0 枚」。
            # 现在：详情页真给出了张数才写；新行没给写成 -1（未知）；已有行则**不动**（别覆盖已知值）。
            if rec.get("image_count") is not None:
                rec["image_count"] = int(rec["image_count"])
            elif old is None:
                rec["image_count"] = -1
            else:
                rec.pop("image_count", None)
            if pdf_path:
                rec["pdf_path"] = pdf_path
                # v1.9.67（09-23 血案）：本函数是**整行 upsert**写库，不走
                #   `store.set_pdf`。v1.9.66 把「PDF 自动上云」钩子只挂在 set_pdf 上
                #   → 而详情主路径恰恰走这里 → 钩子从未触发（实测 4 份新 PDF 未上云、
                #   全日志 0 条自动上云记录）。这里补挂同一个钩子（快速通道）；
                #   另有 `pdf_cloud` 的「欠账巡检」常驻线程兜底，双保险。
                _hook = getattr(store, "on_pdf_downloaded", None)
                if _hook is not None:
                    try:
                        _hook(no, str(pdf_path))
                    except Exception:                             # noqa: BLE001
                        pass      # 钩子失败绝不影响主写入流程

            fp = differ.fingerprint(rec)
            if lo:
                # 列表模式：不动 detail_json（保留已有完整详情；新盘保持 NULL → 阶段2补详情）
                if old is not None and old["detail_json"]:
                    rec.pop("detail_json", None)
            else:
                detail = dict(rec)
                detail["__fp__"] = fp
                rec["detail_json"] = json.dumps(detail, ensure_ascii=False, default=str)
                # v1.5.6：详情抓到了 → 打上「平台日期已核验」标记。
                #   这样 `_needs_detail()` 就不会再让它重复抓第二次；
                #   缺平台日期的老房源因此只补抓一次，不是每轮都抓。
                rec["pdate_checked"] = 1
            rec["last_seen_at"] = now()

            # ---- 3. 变更判定 ----
            changes = differ.classify(old, rec, fp)
            if old is None:
                stats["new"] += 1
            stats["changed"] += sum(1 for c in changes if c["type"] != "new")

            # v1.5.4：first_seen_at 只在**首次入库**时定下来（store 的 INSERT 分支也会补）。
            # 旧代码无条件 setdefault，于是 rec 每轮都带着 now() 走进 UPDATE，
            # 把"我方第一次见到这套房"改写成"最后一次下载时间"
            # （本机实测 1103/1103 行 first_seen_at 全等于 last_seen_at，按天查历史必然全空）。
            if old is None:
                rec.setdefault("first_seen_at", now())
            if any(c["type"] in ("price_down", "price_up", "modified") for c in changes):
                rec["last_changed_at"] = now()

            # v1.9.77 F6：价格异常熔断（±40% 跳变疑似串号/错位）
            # 命中则暂停自动推送（price_hold=1）、进待决窗口等勇哥裁决，并把「有 PDF 的」
            # 房源入重抓队列（F3b：用新抓的 PDF 反查复核是否串号）。原价格变更仍照常入 changes。
            if old is not None and rec.get("price") not in (None, "", 0) and old.get("price"):
                try:
                    _old_p, _new_p = float(old["price"]), float(rec["price"])
                    if _old_p and abs(_new_p - _old_p) / abs(_old_p) >= 0.40:
                        store.set_price_hold(no, 1)
                        store.add_decision(
                            run_id, "price_hold",
                            {"property_no": no, "old_price": int(_old_p),
                             "new_price": int(_new_p)},
                            options=["confirm", "revert"],
                            default_action="confirm", timeout_s=0)
                        store.add_notification(
                            "price_hold",
                            "番号 %s 价格 %d→%d 跳变 %.0f%%，已熔断暂停推送（待复核）"
                            % (no, int(_old_p), int(_new_p),
                               abs(_new_p - _old_p) / abs(_old_p) * 100))
                        if rec.get("pdf_path") or old.get("pdf_path"):
                            store.enqueue_pdf_recrawl(no, priority=5, reason="price_jump_verify")
                except (TypeError, ValueError):
                    pass

            store.upsert_property(rec)
            store.add_snapshot(no, rec.get("price"), "active", fp)
            for c in changes:
                store.add_change(no, c["type"], c["old"], c["new"], c["field"], run_id)

            if not lo:
                stats["fetched"] += 1

        except Exception as e:                                   # 单条失败不影响整体
            stats["errors"].append(f"{no}: {type(e).__name__}: {e}")

    # ---- 4. 水位线：记住本批"最新一条" ----
    if seen:
        store.set_watermark(watermark_key, seen[0])

    return stats


def _save_pdf(rec: dict, property_no: str, att_dir: Path) -> str:
    """把 PDF 落到本地。返回本地相对路径（相对 output_root）或空串。"""
    att_dir.mkdir(parents=True, exist_ok=True)
    target = att_dir / f"{property_no}.pdf"

    src = rec.pop("pdf_src", None)          # 演示模式：给一个现成文件
    blob = rec.pop("pdf_bytes", None)       # 真实模式：给字节流

    if src:
        try:
            p = Path(src)
            if p.exists():
                shutil.copyfile(p, target)
                return f"attachments/{property_no}.pdf"
        except Exception:
            return ""
    if blob:
        try:
            target.write_bytes(blob)
            return f"attachments/{property_no}.pdf"
        except Exception:
            return ""
    return ""


def pseudo_pdf(text: str) -> bytes:
    """生成一个最小可打开的 PDF（演示模式用，证明"确实落盘到本地"）。"""
    safe = (text or "").replace("(", "").replace(")", "").replace("\\", "")
    body = f"BT /F1 14 Tf 60 760 Td ({safe}) Tj ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n"
    out += (f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n")
    return out.encode("latin-1", "replace")


def content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]
