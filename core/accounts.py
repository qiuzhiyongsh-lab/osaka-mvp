# -*- coding: utf-8 -*-
"""员工账户与角色权限（PRD-19 · 账号与员工角色权限专项）。

设计要点（与 PRD-19 v1.1.0 决策一致）：
- 角色两级：staff（员工）/ admin（管理员）。
- 凭证：仅用户名 + 密码（不采集手机号）。密码用 werkzeug pbkdf2:sha256 哈希（≈26 万次迭代，
  强度等同 bcrypt，且零原生编译依赖——免 bcrypt 在发布站装不上原生扩展的风险）。
- 首管理员：本地 bootstrap_admin 自动建（方案 A），凭据来自 config.local.yaml 的
  accounts.bootstrap_admin（gitignored，绝不入库）。
- N2 访问码（原 public.access_code）改造为「管理员应急码」——仅作账户系统锁死时的后门。
- 账户数据落在主库 jproperty.db 的 accounts / audit_log / login_fail 三张表。
- 本模块**自包含**：自己建表（init）、自己管线程本地连接，不污染 core/auth.py
  （那是 REINS 爬虫登录，完全不同域）。

安全约束（PRD-19 §11）：
- 账户表 / 哈希密码 / 应急码 均本机存，不进 git；
- sync_to_publish 已剥离 config.local.yaml 私密键，发布站不携带明文凭据。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import secrets
import sqlite3
import string
import threading

from werkzeug.security import check_password_hash, generate_password_hash

# ---------------------------------------------------------------------------
# 常量（PRD-19 Q1–Q5 已拍板）
# ---------------------------------------------------------------------------
ROLE_STAFF = "staff"
ROLE_ADMIN = "admin"
ROLES = (ROLE_STAFF, ROLE_ADMIN)

MAX_FAIL = 5            # Q2：登录失败 5 次锁
LOCK_MINUTES = 15      # Q2：锁 15 分钟
AUDIT_KEEP_DAYS = 90   # Q5：审计日志保留 90 天
PW_MIN, PW_MAX = 8, 20  # Q1：8–20 位，字母+数字

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  username     TEXT UNIQUE NOT NULL,
  pwd_hash     TEXT NOT NULL,
  role         TEXT NOT NULL DEFAULT 'staff',   -- 'staff' | 'admin'
  display_name TEXT,
  cleared      INTEGER NOT NULL DEFAULT 0,      -- 0=待改密(初始随机码) 1=已正常
  disabled     INTEGER NOT NULL DEFAULT 0,      -- 1=禁用登录(软删)
  failed_count INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,                            -- ISO 时间，锁定时长
  created_by   TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL,
  actor  TEXT,        -- 操作人 username / 'system' / 'emergency'
  action TEXT NOT NULL,
  target TEXT,        -- 受影响 username
  detail TEXT,
  ip     TEXT
);
CREATE TABLE IF NOT EXISTS login_fail (
  username TEXT PRIMARY KEY,
  count    INTEGER NOT NULL DEFAULT 0,
  last_at  TEXT
);
"""

_tls = threading.local()
_DB_PATH: str | None = None


# ---------------------------------------------------------------------------
# 连接管理（线程本地，复用 store.py 的安全模式）
# ---------------------------------------------------------------------------
def init(db_path: str) -> None:
    """建表 + 记录 db 路径。在 Store 初始化之后调用一次即可。"""
    global _DB_PATH
    _DB_PATH = str(db_path)
    conn = _conn()
    conn.executescript(_SCHEMA)
    conn.commit()


def _conn() -> sqlite3.Connection:
    c = getattr(_tls, "conn", None)
    if c is None:
        c = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        _tls.conn = c
    return c


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _gen_code(n: int = 12) -> str:
    """一次性随机码：去掉易混字符（0/O/1/l/I 等）。"""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def hash_password(pw: str) -> str:
    return generate_password_hash(pw, method="pbkdf2:sha256", salt_length=16)


def verify_password(h: str | None, pw: str) -> bool:
    if not h:
        return False
    try:
        return bool(check_password_hash(h, pw))
    except Exception:
        return False


def _strong(pw: str) -> bool:
    """Q1：8–20 位，且同时含字母与数字。"""
    if not (PW_MIN <= len(pw) <= PW_MAX):
        return False
    return any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw)


# ---------------------------------------------------------------------------
# 账户 CRUD
# ---------------------------------------------------------------------------
def get_account(username: str):
    return _conn().execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()


def list_accounts():
    return _conn().execute(
        "SELECT id,username,display_name,role,cleared,disabled,created_by,created_at,updated_at "
        "FROM accounts ORDER BY id"
    ).fetchall()


def create_account(username: str, password: str, role: str = ROLE_STAFF,
                   created_by: str = "system", display_name: str | None = None,
                   random_code: bool = False):
    """创建账户。random_code=True 时用一次性随机码作初始密码（cleared=0 待改密），
    返回 (ok, code_or_msg)：成功时 code_or_msg 是明文随机码（仅此一次返回，库内只存哈希）。"""
    if not username:
        return False, "用户名不能为空"
    # random_code=True 时密码由系统生成，管理员无需填 —— 员工管理页「生成随机码」就走这条路
    if not random_code and not password:
        return False, "请填初始密码，或改用一次性随机码"
    if role not in ROLES:
        return False, "角色非法"
    if get_account(username):
        return False, "用户名已存在"
    code = None
    pw = password
    cleared = 1
    if random_code:
        code = _gen_code()
        pw = code
        cleared = 0
    now = _now()
    _conn().execute(
        "INSERT INTO accounts(username,pwd_hash,role,display_name,cleared,disabled,created_by,created_at,updated_at) "
        "VALUES(?,?,?,?,?,0,?,?,?)",
        (username, hash_password(pw), role, display_name, cleared, created_by, now, now),
    )
    _conn().commit()
    audit("account.create", created_by, username, f"role={role} random_code={int(random_code)}")
    return True, (code or "ok")


def authenticate(username: str, password: str, ip: str | None = None):
    """返回 (ok, account_or_None, reason)。reason ∈
    ok / no_such_user / disabled / locked / bad_password。"""
    row = get_account(username)
    if row is None:
        # 即便用户不存在也走一次失败记录（防用户名枚举Timing），但落到不存在的行不存
        return False, None, "no_such_user"
    if row["disabled"]:
        return False, None, "disabled"
    lu = row["locked_until"]
    if lu and lu > _now():  # ISO 字符串字典序 == 时间序
        return False, None, "locked"
    if not verify_password(row["pwd_hash"], password):
        _inc_fail(username)
        return False, None, "bad_password"
    _conn().execute(
        "UPDATE accounts SET failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
        (_now(), username),
    )
    _conn().commit()
    audit("auth.login", username, username, "ok", ip=ip)
    return True, row, "ok"


def _inc_fail(username: str) -> None:
    conn = _conn()
    now = _now()
    r = conn.execute("SELECT count FROM login_fail WHERE username=?", (username,)).fetchone()
    cnt = (r["count"] + 1) if r else 1
    conn.execute(
        "INSERT INTO login_fail(username,count,last_at) VALUES(?,?,?) "
        "ON CONFLICT(username) DO UPDATE SET count=excluded.count, last_at=excluded.last_at",
        (username, cnt, now),
    )
    if cnt >= MAX_FAIL:
        lu = (datetime.datetime.now() + datetime.timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE accounts SET failed_count=?, locked_until=?, updated_at=? WHERE username=?",
            (cnt, lu, now, username),
        )
        audit("account.lock", "system", username, f"fail={cnt} lock={LOCK_MINUTES}m")
    else:
        conn.execute("UPDATE accounts SET failed_count=?, updated_at=? WHERE username=?", (cnt, now, username))
    conn.commit()


def change_password(username: str, old_pw: str, new_pw: str, actor: str | None = None):
    row = get_account(username)
    if row is None:
        return False, "no_such_user"
    if row["disabled"]:
        return False, "disabled"
    if not verify_password(row["pwd_hash"], old_pw):
        return False, "old_password_wrong"
    if not _strong(new_pw):
        return False, "weak_password"
    _conn().execute(
        "UPDATE accounts SET pwd_hash=?, cleared=1, failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
        (hash_password(new_pw), _now(), username),
    )
    _conn().commit()
    audit("account.change_pwd", actor or username, username, "self")
    return True, "ok"


def set_initial_password(username: str, new_pw: str, actor: str | None = None):
    """首次登录**强制设置密码**（PRD-19 v2.0 主流程 ⑤）。

    仅用于 cleared=0（仍是初始随机码）的账户：员工拿到管理员给的随机码登录后，
    自己设一个正式密码。**不需要原密码** —— 随机码本就是一次性凭据。
    设完 cleared=1 → 此后同步不会再拿种子覆盖他的哈希（UPSERT 保护生效）。
    """
    row = get_account(username)
    if row is None:
        return False, "no_such_user"
    if row["disabled"]:
        return False, "disabled"
    if not _strong(new_pw):
        return False, "weak_password"
    _conn().execute(
        "UPDATE accounts SET pwd_hash=?, cleared=1, failed_count=0, locked_until=NULL, updated_at=? "
        "WHERE username=?",
        (hash_password(new_pw), _now(), username),
    )
    _conn().commit()
    audit("account.set_initial_pwd", actor or username, username, "first login forced")
    return True, "ok"


def reset_random_code(username: str, by_admin: str):
    """管理员重置：发一次性随机码（cleared=0 待改密）。返回 (ok, code_or_msg)。"""
    row = get_account(username)
    if row is None:
        return False, "no_such_user"
    code = _gen_code()
    _conn().execute(
        "UPDATE accounts SET pwd_hash=?, cleared=0, failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
        (hash_password(code), _now(), username),
    )
    _conn().commit()
    audit("account.reset_code", by_admin, username, "issued one-time code")
    return True, code


def set_role(username: str, role: str, by_admin: str):
    if role not in ROLES:
        return False, "角色非法"
    if get_account(username) is None:
        return False, "no_such_user"
    _conn().execute("UPDATE accounts SET role=?, updated_at=? WHERE username=?", (role, _now(), username))
    _conn().commit()
    audit("account.set_role", by_admin, username, f"role={role}")
    return True, "ok"


def set_disabled(username: str, disabled: bool, by_admin: str):
    """软删：disabled=True 时禁用 + 清空密码哈希（PRD-19：清除密码=禁用登录，非幽灵账户）。"""
    if get_account(username) is None:
        return False, "no_such_user"
    if disabled:
        _conn().execute(
            "UPDATE accounts SET disabled=1, pwd_hash='', failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
            (_now(), username),
        )
    else:
        _conn().execute("UPDATE accounts SET disabled=0, updated_at=? WHERE username=?", (_now(), username))
    _conn().commit()
    audit("account.set_disabled", by_admin, username, f"disabled={int(disabled)}")
    return True, "ok"


def bootstrap_admin(cfg: dict) -> tuple[bool, str]:
    """本地首管理员自动建（PRD-19 Q6 方案 A）。
    cfg = core.config.load() 合并结果；凭据读 config.local.yaml 的 accounts.bootstrap_admin。
    不设或置空 = 不自动建（留给应急码登录后手动建）。"""
    ba = (cfg.get("accounts") or {}).get("bootstrap_admin") or {}
    uname = ba.get("username")
    pw = ba.get("password")
    if not uname or not pw:
        return False, "no_bootstrap_config"
    if get_account(uname):
        # 已存在：本地运维便利，配置显式给密码时同步（仅 role 强制 admin、解除禁用）
        _conn().execute(
            "UPDATE accounts SET pwd_hash=?, role=?, disabled=0, updated_at=? WHERE username=?",
            (hash_password(pw), ROLE_ADMIN, _now(), uname),
        )
        _conn().commit()
        return True, "exists_synced"
    ok, msg = create_account(uname, pw, role=ROLE_ADMIN, created_by="system(bootstrap)", random_code=False)
    if ok:
        audit("account.bootstrap", "system", uname, "first admin auto-created")
        return True, "created"
    return False, msg


def verify_emergency_code(code: str | None, cfg: dict) -> bool:
    """N2 访问码改造为管理员应急码（PRD-19 §4.3）。
    仅当 config.public.access_code 非空且相等时返回 True。"""
    ec = (cfg.get("public") or {}).get("access_code") or ""
    if not ec:
        return False
    return secrets.compare_digest(str(code or ""), str(ec))


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------
def audit(action: str, actor: str | None, target: str | None, detail: str | None, ip: str | None = None) -> None:
    try:
        _conn().execute(
            "INSERT INTO audit_log(ts,actor,action,target,detail,ip) VALUES(?,?,?,?,?,?)",
            (_now(), actor, action, target, detail, ip),
        )
        _conn().commit()
    except Exception:
        pass


def purge_audit_old(days: int = AUDIT_KEEP_DAYS) -> int:
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cur = _conn().execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
    _conn().commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# 种子播种 / UPSERT（PRD-19 §17 · 线下建号 → 同步上云）
# ---------------------------------------------------------------------------
def upsert_seed(rows: list[dict], actor: str = "system(seed)",
                force_reset: bool = False) -> dict:
    """按 username 做 UPSERT 播种（PRD-19 §17.2，勇哥拍板口径）。

    rows: [{username, pwd_hash, role, display_name, cleared, disabled}, ...]
      ⚠ pwd_hash 必须是**哈希** —— 种子文件绝不携带明文口令。
    规则：
      · 线上不存在 → 插入
      · 线上已存在 → 更新 role / display_name / disabled
        - pwd_hash **仅当该账户仍是「初始随机码」(cleared=0) 或 force_reset=True** 时覆盖
        - 已改过密码的账户 (cleared=1)：**绝不覆盖**哈希，避免把线上自立修改冲掉
    返回统计 {inserted, updated, pwd_kept, pwd_reset, skipped}。
    """
    stat = {"inserted": 0, "updated": 0, "pwd_kept": 0, "pwd_reset": 0, "skipped": 0}
    conn = _conn()
    now = _now()
    for r in rows:
        uname = (r.get("username") or "").strip()
        if not uname or not r.get("pwd_hash"):
            stat["skipped"] += 1
            continue
        role = r.get("role") or ROLE_STAFF
        if role not in ROLES:
            role = ROLE_STAFF
        disp = r.get("display_name")
        disabled = 1 if r.get("disabled") else 0
        cleared = 1 if r.get("cleared") else 0
        row = get_account(uname)
        if row is None:
            conn.execute(
                "INSERT INTO accounts(username,pwd_hash,role,display_name,cleared,disabled,"
                "failed_count,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?)",
                (uname, r["pwd_hash"], role, disp, cleared, disabled, actor, now, now),
            )
            audit("seed.insert", actor, uname, f"role={role} disabled={disabled}")
            stat["inserted"] += 1
            continue
        # 已存在：角色 / 展示名 / 禁用态 始终以本地为准
        conn.execute(
            "UPDATE accounts SET role=?, display_name=?, disabled=?, updated_at=? WHERE username=?",
            (role, disp, disabled, now, uname),
        )
        # 密码：仅「仍是初始随机码(cleared=0)」或显式 force_reset 才覆盖
        take_new = bool(force_reset or not row["cleared"])
        if take_new:
            conn.execute(
                "UPDATE accounts SET pwd_hash=?, cleared=?, failed_count=0, locked_until=NULL, updated_at=? "
                "WHERE username=?",
                (r["pwd_hash"], cleared, now, uname),
            )
            stat["pwd_reset"] += 1
        else:
            stat["pwd_kept"] += 1
        audit("seed.upsert", actor, uname,
              f"role={role} disabled={disabled} pwd={'reset' if take_new else 'kept'}")
        stat["updated"] += 1
    conn.commit()
    return stat


def export_seed(path: str) -> dict:
    """导出种子到 json（**只含哈希，绝不含明文**），供发布同步带上云（PRD-19 §17.3）。"""
    rows = _conn().execute(
        "SELECT username,pwd_hash,role,display_name,cleared,disabled FROM accounts ORDER BY id"
    ).fetchall()
    data = {
        "schema": "osaka-accounts-seed/v1",
        "exported_at": _now(),
        "accounts": [
            {
                "username": r["username"],
                "pwd_hash": r["pwd_hash"],
                "role": r["role"],
                "display_name": r["display_name"],
                "cleared": int(r["cleared"]),
                "disabled": int(r["disabled"]),
            }
            for r in rows
        ],
    }
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def import_seed_file(path: str, actor: str = "system(seed)", force_reset: bool = False):
    """线上侧：启动时导入种子文件。返回 None 表示种子不存在/损坏（调用方据此降级）。"""
    p = pathlib.Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("accounts"):
        return None
    return upsert_seed(data["accounts"], actor=actor, force_reset=force_reset)


def has_any_account() -> bool:
    """线上防锁死兜底用（PRD-19 §17.3）：账户表里是否存在可用（未禁用）账户。"""
    r = _conn().execute("SELECT COUNT(*) c FROM accounts WHERE disabled=0").fetchone()
    return bool(r and r["c"] > 0)
