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
-- v1.9.28：账号双向同步水位线（本地 ⇄ 线上）。只记状态，不记任何凭据。
CREATE TABLE IF NOT EXISTS account_sync (
  id             INTEGER PRIMARY KEY CHECK (id=1),
  last_push_at   TEXT,     -- 最近一次「本地 → 线上」种子推送成功的时刻
  last_push_md5  TEXT,     -- 推送内容的指纹（相同则跳过，避免每 10 分钟白推一次）
  last_push_stat TEXT,     -- 线上回执统计（inserted/updated/pwd_reset/pwd_kept）
  last_pull_at   TEXT,     -- 最近一次「线上 → 本地」状态拉取的时刻
  last_pull_stat TEXT,     -- 回流统计（adopted/skipped/conflict）
  last_error     TEXT
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
    _ensure_current_code(conn)
    conn.commit()


def _ensure_current_code(conn) -> None:
    """v1.9.29 迁移：给 accounts 加 current_code 列（仅管理员可见的待改密明文码）。

    仅当列不存在时才加（SQLite 没有 ADD COLUMN IF NOT EXISTS 语法，必须先 PRAGMA 探测）。
    本地持久库与线上临时库启动都会跑到这里，保证两条路的 UPDATE 都不会因缺列报错。
    """
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        if "current_code" not in cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN current_code TEXT")
    except Exception:                                        # noqa: BLE001
        pass


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


def get_current_code(username: str):
    """仅管理员侧调用：取账户**当前待改密明文码**（若存在且仍未激活）。

    返回 (ok, code_or_reason)：
      · ok=True   → code_or_reason 是明文随机码（员工尚未首次登录设密，可复制重发）。
      · ok=False  → code_or_reason 是原因：
          'no_such_user' / 'disabled'（已禁用）/ 'already_set'（员工已自设密码，无一次性码）/
          'no_code'（旧数据：升级前创建的待改密账号未记录明文，需重置一次才补录）。
    安全：此函数绝不触及线上接口，current_code 也不进种子/state（export_seed/export_state
    均只选指定列），明文码永不离开本地管理员会话。
    """
    row = get_account(username)
    if row is None:
        return False, "no_such_user"
    if row["disabled"]:
        return False, "disabled"
    if row["cleared"]:
        return False, "already_set"
    code = (row["current_code"] or "").strip()
    if not code:
        return False, "no_code"
    return True, code


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
        "INSERT INTO accounts(username,pwd_hash,role,display_name,cleared,disabled,current_code,"
        "created_by,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?,?,?)",
        (username, hash_password(pw), role, display_name, cleared, code, created_by, now, now),
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
        "UPDATE accounts SET pwd_hash=?, cleared=1, current_code=NULL, "
        "failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
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
        "UPDATE accounts SET pwd_hash=?, cleared=1, current_code=NULL, "
        "failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
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
        "UPDATE accounts SET pwd_hash=?, cleared=0, current_code=?, "
        "failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
        (hash_password(code), code, _now(), username),
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
            "UPDATE accounts SET disabled=1, pwd_hash='', current_code=NULL, "
            "failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
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
                force_reset: bool = False,
                force_users: set | list | tuple | None = None) -> dict:
    """按 username 做 UPSERT 播种（PRD-19 §17.2，勇哥拍板口径）。

    rows: [{username, pwd_hash, role, display_name, cleared, disabled}, ...]
      ⚠ pwd_hash 必须是**哈希** —— 种子文件绝不携带明文口令。
    规则：
      · 线上不存在 → 插入
      · 线上已存在 → 更新 role / display_name / disabled
        - pwd_hash **仅当该账户仍是「初始随机码」(cleared=0) 或 force_reset=True** 时覆盖
        - 已改过密码的账户 (cleared=1)：**绝不覆盖**哈希，避免把线上自立修改冲掉
    返回统计 {inserted, updated, pwd_kept, pwd_reset, skipped}。

    v1.9.28 新增 force_users（**精确定向**强制覆盖，替代 / 补充 force_reset 的"全量强推"）：
      管理员在 /staff 刚重置了某个账号的随机码 → 该账号线上可能已是 cleared=1（员工早先
      自设过密码）→ 按上面的规则不会覆盖 → 新码在线上照样登不上。
      所以「变更事件」推送时把**被改动的账号**列进 force_users，只强制覆盖它自己，
      其余账号仍走保守规则（保护员工在线上自设的密码不被回退）。
    """
    stat = {"inserted": 0, "updated": 0, "pwd_kept": 0, "pwd_reset": 0, "skipped": 0}
    force_set = {str(u).strip() for u in (force_users or []) if str(u or "").strip()}
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
        # 密码：仅「仍是初始随机码(cleared=0)」、显式 force_reset、或被点名 force_users 才覆盖
        take_new = bool(force_reset or (uname in force_set) or not row["cleared"])
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


def import_seed_file(path: str, actor: str = "system(seed)", force_reset: bool = False,
                     force_users: set | list | tuple | None = None):
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
    return upsert_seed(data["accounts"], actor=actor, force_reset=force_reset,
                       force_users=force_users)


def has_any_account() -> bool:
    """线上防锁死兜底用（PRD-19 §17.3）：账户表里是否存在可用（未禁用）账户。"""
    r = _conn().execute("SELECT COUNT(*) c FROM accounts WHERE disabled=0").fetchone()
    return bool(r and r["c"] > 0)


# ---------------------------------------------------------------------------
# v1.9.28：双向同步（本地 ⇄ 线上）
#   背景（勇哥 2026-09-20 实测反馈的两个真实缺口）：
#     ① 本地 /staff 重置了码 → 线上还是旧哈希 → 员工手持新码报「密码错误」；
#     ② 员工在线上自设了密码 → 本地 /staff 永远显示「待改密」。
#   ① 的根因是"改了本地还得人工重新发布"，② 的根因是"同步只有单向，没有回流通道"。
#   这里放同步**所需的纯数据操作**，网络收发在 core/account_sync.py（保持本模块零网络依赖）。
# ---------------------------------------------------------------------------
def export_state() -> list[dict]:
    """导出全部账号的**完整**状态（含 pwd_hash 与 updated_at），供线上⇄本地对账。

    ⚠ 含哈希，**只允许**机器对机器接口（带 X-Publish-Token）返回，绝不进任何页面。
    注意与 export_seed() 的区别：种子是"给线上播种用的"（不带 updated_at），
    这个是"用来对账的"（要 updated_at 才能判断谁更新）。
    """
    rows = _conn().execute(
        "SELECT username,pwd_hash,role,display_name,cleared,disabled,updated_at "
        "FROM accounts ORDER BY id"
    ).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["cleared"] = int(d.get("cleared") or 0)
        d["disabled"] = int(d.get("disabled") or 0)
        out.append(d)
    return out


def sync_state() -> dict:
    """同步水位线（本地侧）。表不存在/无记录时返回全 None，绝不抛。"""
    empty = {"last_push_at": None, "last_push_md5": None, "last_push_stat": None,
             "last_pull_at": None, "last_pull_stat": None, "last_error": None}
    try:
        row = _conn().execute("SELECT * FROM account_sync WHERE id=1").fetchone()
    except Exception:                                        # noqa: BLE001
        return dict(empty)
    if row is None:
        return dict(empty)
    return {k: row[k] for k in row.keys()}


def set_sync_state(**kw) -> None:
    """更新同步水位线。只接受已知键，未知键忽略（防手滑写入垃圾列）。"""
    cur = sync_state()
    for k, v in kw.items():
        if k in cur:
            cur[k] = v
    _conn().execute(
        "INSERT INTO account_sync(id,last_push_at,last_push_md5,last_push_stat,"
        "last_pull_at,last_pull_stat,last_error) VALUES(1,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET last_push_at=excluded.last_push_at,"
        " last_push_md5=excluded.last_push_md5, last_push_stat=excluded.last_push_stat,"
        " last_pull_at=excluded.last_pull_at, last_pull_stat=excluded.last_pull_stat,"
        " last_error=excluded.last_error",
        (cur["last_push_at"], cur["last_push_md5"], cur["last_push_stat"],
         cur["last_pull_at"], cur["last_pull_stat"], cur["last_error"]),
    )
    _conn().commit()


def adopt_remote(rows: list[dict], last_push_at: str | None,
                 actor: str = "system(sync)") -> dict:
    """把**线上**发生的变化回流到本地主库（PRD-19 缺口的补丁 · v1.9.28）。

    只采纳**唯一一种**情形，绝不猜测、绝不批量覆盖：
      · 线上 cleared=1（员工已自设密码）
      · 本地 cleared=0（本地还以为「待改密」、未被认领）
      · 两侧哈希不同（确实是新改的，不是同一份哈希的来回搬运）
      · 本地该行在**上次成功推送之后没被动过**（local.updated_at < last_push_at）
        —— 这条把「本地刚重置了新码、线上还是旧状态」的竞态挡在门外，
           否则会把管理员刚发出去的新码冲掉（那正是坑②的反向版本）。
    命中才写本地：pwd_hash + cleared=1（对齐线上）。
    其余一律不碰：**本地是账号的权威来源**，线上只能"认领自己改的密码"。

    返回 {"adopted": [用户名...], "skipped": n, "conflict": [用户名...]}。
    """
    conn = _conn()
    now = _now()
    rep = {"adopted": [], "skipped": 0, "conflict": []}
    for r in rows or []:
        uname = str(r.get("username") or "").strip()
        if not uname:
            continue
        try:
            online_cleared = int(r.get("cleared") or 0)
        except Exception:                                    # noqa: BLE001
            online_cleared = 0
        online_hash = r.get("pwd_hash") or ""
        local = get_account(uname)
        if local is None:
            # 线上有、本地没有 → 不新建（建号权只在本地 /staff，避免线上被塞账号）
            rep["skipped"] += 1
            continue
        local_cleared = int(local["cleared"] or 0)
        local_hash = local["pwd_hash"] or ""
        if online_cleared != 1:
            rep["skipped"] += 1
            continue
        if online_hash and online_hash == local_hash:
            rep["skipped"] += 1
            continue
        if local_cleared == 1:
            # 两边都"已正常"但哈希不同：谁新说不准（跨机器时钟不可信）→ 只报冲突，不动手
            rep["conflict"].append(uname)
            continue
        if last_push_at and (local["updated_at"] or "") >= last_push_at:
            # 本地在最近一次推送之后被动过 → 那是管理员刚重置的新码，不能被线上旧状态冲掉
            rep["skipped"] += 1
            continue
        conn.execute(
            "UPDATE accounts SET pwd_hash=?, cleared=1, current_code=NULL, "
            "failed_count=0, locked_until=NULL, updated_at=? WHERE username=?",
            (online_hash, now, uname),
        )
        rep["adopted"].append(uname)
    if rep["adopted"]:
        conn.commit()
        audit("account.adopt_remote", actor, ",".join(rep["adopted"]),
              f"线上自设密码回流 {len(rep['adopted'])} 个（cleared 0→1）")
    if rep["conflict"]:
        audit("account.adopt_conflict", actor, ",".join(rep["conflict"]),
              "两侧均 cleared=1 但哈希不同，保持本地不动（需人工确认）")
    return rep
