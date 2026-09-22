# -*- coding: utf-8 -*-
"""把本地 PDF 传到腾讯云 COS，生成预签名 URL 并写回主库 `properties.pdf_url`。

设计要点（2026-09-23 MVP）
--------------------------
· 密钥**只从 config.local.yaml 的 cos 段读取**，绝不写死在代码里（该文件已 .gitignore，
  且 sync_to_publish 的 SECRET_KEYS 保证其永不上云）。
· 采用「私有读 + 本地预签名」：COS 桶私有读写，访问靠 7 天有效的预签名 URL；
  **线上容器不持有任何密钥**（线上 app_local/ 无 config.local.yaml）。
· 幂等：COS key 就是 `<物件番号>.pdf`，重复跑只覆盖写 / 重新签名，不产生垃圾。

用法
----
    python tools/pdf_to_cos.py --check                       # 只验配置与连通性
    python tools/pdf_to_cos.py --test-one 300140494543       # 单份上传（MVP）
    python tools/pdf_to_cos.py --backfill                    # 全量补传（历史 3725 份）
    python tools/pdf_to_cos.py --resign 300140494543         # 仅重签（不重传）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfgmod                              # noqa: E402


def _client():
    """构建 COS 客户端。密钥缺失/仍是占位符时直接给出可操作的报错。"""
    from qcloud_cos import CosConfig, CosS3Client              # noqa: PLC0415

    cos = cfgmod.load().get("cos") or {}
    miss = [k for k in ("secret_id", "secret_key", "bucket", "region") if not cos.get(k)]
    if miss:
        raise SystemExit("[COS] 配置缺失 %s → 请检查 config.local.yaml 的 cos 段" % miss)
    sk = str(cos["secret_key"])
    if sk.startswith("PASTE") or len(sk) < 20:
        raise SystemExit("[COS] secret_key 仍是占位符 → 请先把真 SecretKey 填进 config.local.yaml")

    cli = CosS3Client(CosConfig(Region=cos["region"], SecretId=cos["secret_id"], SecretKey=sk))
    return cos, cli


def pdf_path_of(no: str) -> Path:
    return ROOT / "data" / "attachments" / ("%s.pdf" % no)


def _write_db(no: str, url: str) -> bool:
    db = ROOT / "data" / "jproperty.db"
    conn = sqlite3.connect(str(db), timeout=15)
    try:
        cur = conn.execute("UPDATE properties SET pdf_url=? WHERE property_no=?", (url, no))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def upload_one(no: str, cos: dict, cli, expire: int = 7 * 24 * 3600, do_db: bool = True) -> dict:
    """上传单份 PDF + 生成预签名 URL + 写回主库。返回结构化结果（供调用方判成败）。"""
    src = pdf_path_of(no)
    if not src.exists():
        return {"ok": False, "no": no, "stage": "local", "msg": "本地无此 PDF"}

    key = "%s.pdf" % no
    try:
        cli.head_object(Bucket=cos["bucket"], Key=key)
        uploaded = False                      # 云端已有 → 不重复传
    except Exception:                         # noqa: BLE001 - 404 即未存在
        cli.upload_file(
            Bucket=cos["bucket"], Key=key, LocalFilePath=str(src),
            PartSize=10, MAXThread=4, EnableMD5=False,
        )
        uploaded = True

    url = cli.get_presigned_download_url(Bucket=cos["bucket"], Key=key, Expired=expire)

    written = _write_db(no, url) if do_db else False
    return {
        "ok": True, "no": no, "uploaded": uploaded, "written_db": written,
        "size": src.stat().st_size, "url": url,
        "expire_days": round(expire / 86400, 2),
    }


def backfill(cos: dict, cli, limit: int | None = None, only_with_row: bool = True) -> None:
    """全量补传：扫描 data/attachments/*.pdf。

    only_with_row=True 时，仅对「主库里有对应 property_no」的番号写 pdf_url；
    孤儿虽上传（勇哥口径：取下来就上云），但不挂链接，详情页按无 PDF 处理。
    """
    db = ROOT / "data" / "jproperty.db"
    valid = set()
    if only_with_row:
        conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
        try:
            valid = {r[0] for r in conn.execute("SELECT property_no FROM properties")}
        finally:
            conn.close()

    files = sorted((ROOT / "data" / "attachments").glob("*.pdf"))
    if limit:
        files = files[:limit]

    ok = skip = fail = 0
    t0 = time.time()
    for i, f in enumerate(files, 1):
        no = f.stem
        try:
            r = upload_one(no, cos, cli, do_db=(no in valid))
            ok += 1
        except Exception as e:                                  # noqa: BLE001
            fail += 1
            print("[COS] ✗ %s %s" % (no, e), flush=True)
            continue
        if i % 100 == 0:
            print("[COS] 进度 %d/%d  用时 %.0fs" % (i, len(files), time.time() - t0), flush=True)
    print("[COS] 补传完成：成功 %d / 失败 %d / 共用时 %.0fs" % (ok, fail, time.time() - t0), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF → 腾讯云 COS（私有读 + 本地预签名）")
    ap.add_argument("--check", action="store_true", help="只验配置与桶连通性")
    ap.add_argument("--test-one", metavar="NO", help="上传单份（物件番号）")
    ap.add_argument("--resign", metavar="NO", help="仅重新签名（不重传）")
    ap.add_argument("--backfill", action="store_true", help="全量补传")
    ap.add_argument("--limit", type=int, default=None, help="补传数量上限（调试用）")
    ap.add_argument("--expire", type=int, default=7 * 24 * 3600, help="预签名有效期秒数（≤7天=604800）")
    args = ap.parse_args()

    cos, cli = _client()

    if args.check:
        # ⚠ 不要用 list_objects：最小权限策略只给了 GetObject/PutObject/HeadObject，
        #   没有 GetBucket，列举必然 AccessDenied（属预期，不是配置错）。
        #   改用 head_object 探测：404=权限正常（对象不存在）｜403=权限不足。
        from qcloud_cos.cos_exception import CosServiceError      # noqa: PLC0415
        state = "未知"
        try:
            cli.head_object(Bucket=cos["bucket"], Key="__probe_not_exist__.pdf")
            state = "对象居然存在（意外）"
        except CosServiceError as e:                              # noqa: PERF203
            code = None
            try:
                code = e.get_status_code()
            except Exception:                                     # noqa: BLE001
                code = None
            if code == 404:
                state = "权限正常 ✅（探测返回 404=对象不存在，符合预期）"
            elif code == 403:
                state = "⛔ 权限不足 403 —— 请检查 osaka-pdf-only 是否已关联到该子账号"
            else:
                state = "返回 HTTP %s" % code
        print("[COS] 连通 OK｜bucket=%s region=%s" % (cos["bucket"], cos["region"]))
        print("[COS] head 探测：%s" % state)
        return 0

    if args.test_one:
        r = upload_one(args.test_one, cos, cli, expire=args.expire)
        print("[COS] 结果：%s" % {k: v for k, v in r.items() if k != "url"}, flush=True)
        if r.get("ok"):
            print("[COS] URL=%s" % r["url"], flush=True)
        return 0 if r.get("ok") else 1

    if args.resign:
        no = args.resign
        url = cli.get_presigned_download_url(
            Bucket=cos["bucket"], Key="%s.pdf" % no, Expired=args.expire)
        print("[COS] 重签：%s" % (_write_db(no, url) and "已写库" or "库内无此房源"))
        print("[COS] URL=%s" % url)
        return 0

    if args.backfill:
        backfill(cos, cli, limit=args.limit)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
