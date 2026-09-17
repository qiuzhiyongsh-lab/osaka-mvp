# -*- coding: utf-8 -*-
"""REINS 账号本地加密存储。

设计红线（与 auth.py 一致）：
  · 账号密码只存在你本机，不联网、不上传、不共享
  · 用 Fernet 对称加密（密钥 data/.cred_key，文件权限 600）
  · 程序只在你本账号自动登录时使用；绝不识别验证码、绝不代输给第三方

安全说明：这是"本机单人使用"级别的防护，不是银行级。
若有人能读到你本机磁盘（或你开着页面给别人看），密码仍可泄露——
所以页面"显示密码"只在 localhost 下可用，且用完请点「清空」。
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


class CredentialsError(RuntimeError):
    """账号读取/解密失败。"""


def _cred_path(root: Path) -> Path:
    return root / "credentials.json"


def _key_path(root: Path) -> Path:
    return root / ".cred_key"


def _load_key(root: Path) -> bytes:
    """拿到（或生成）加密密钥。密钥只在本地，不进 Git。"""
    kp = _key_path(root)
    if kp.exists():
        return kp.read_bytes()
    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        kp.write_bytes(key)
        try:
            os.chmod(kp, 0o600)
        except Exception:
            pass
        return key
    except Exception:
        # 极端兜底：cryptography 不可用时，用固定混淆串（非加密，仅防明文泄露）
        fb = base64.b64encode(b"osaka-mvp-local-fallback-key")
        kp.write_bytes(fb)
        return fb


def _fernet(key: bytes):
    from cryptography.fernet import Fernet
    return Fernet(key)


def save(root: Path, member_id: str, password: str,
          account_name: str = "", site_name: str = "") -> dict[str, Any]:
    """保存账号（含账号名称 / 网站名称标签，加密密码）。返回状态。

    account_name：给这个账号起个昵称，如"主账号" / "备用"。
    site_name：    这个账号的**网站地址（登录地址）**，如
                   "https://system.reins.jp/login/main/KG/GKG001200"。
                   · 填的是网址 → 自动登录会直接用它（换机构/换账号时改这一格即可）；
                   · 留空或填的不是网址 → 回退用 config.yaml 的 site.login_url。
                   两者都只存本机，不联网、不上传。
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    key = _load_key(root)
    try:
        enc = _fernet(key).encrypt(password.encode("utf-8"))
        algorithm = "fernet"
    except Exception:
        enc = base64.b64encode(("obf:" + password).encode("utf-8"))
        algorithm = "obf"
    data = {
        "member_id": member_id,
        "password_enc": base64.b64encode(enc).decode("ascii"),
        "algorithm": algorithm,
        "account_name": account_name or "",
        "site_name": site_name or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    p = _cred_path(root)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    return {"saved": True, "algorithm": algorithm, "updated_at": data["updated_at"],
            "account_name": account_name, "site_name": site_name}


def load(root: Path) -> dict[str, Any] | None:
    """读取账号（解密密码）。未保存返回 None。"""
    p = _cred_path(root)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise CredentialsError(f"账号文件损坏：{e}")
    key = _load_key(root)
    enc = base64.b64decode(data.get("password_enc", ""))
    try:
        if data.get("algorithm") == "fernet":
            pw = _fernet(key).decrypt(enc).decode("utf-8")
        else:
            pw = base64.b64decode(enc).decode("utf-8")[4:]
    except Exception as e:
        raise CredentialsError(f"密码解密失败（可能密钥已被改动）：{e}")
    return {"member_id": data.get("member_id", ""),
            "password": pw,
            "account_name": data.get("account_name", ""),
            "site_name": data.get("site_name", ""),
            "updated_at": data.get("updated_at")}


def clear(root: Path) -> None:
    """清空已保存的账号。"""
    p = _cred_path(root)
    if p.exists():
        p.unlink()


def status(root: Path) -> dict[str, Any]:
    """给页面用的状态（不返回明文密码）。"""
    p = _cred_path(root)
    if not p.exists():
        return {"saved": False}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        mid = d.get("member_id", "")
        masked = (mid[:2] + "***" + mid[-2:]) if len(mid) > 4 else ("***" if mid else "—")
        return {"saved": True, "member_id_mask": masked,
                "updated_at": d.get("updated_at"),
                "algorithm": d.get("algorithm"),
                "account_name": d.get("account_name", ""),
                "site_name": d.get("site_name", "")}
    except Exception:
        return {"saved": False, "error": "账号文件读取失败"}
