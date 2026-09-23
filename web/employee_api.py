# -*- coding: utf-8 -*-
"""员工业务 API（收藏 / 标签 / 客户）· PRD v2.0.1 §14 步骤 0「接口隔离设计」。

============================================================================
★ 隔离契约（改这个文件前必读，四条都是踩过坑才定下来的）
============================================================================
1) **路由前缀隔离**：一律 `/api/emp/...`。
   —— 与 `/api/ingest`（**财产数据**写通道）彻底分开：财产数据由爬虫推，
      员工业务数据由人写，两者混在一条通道上会互相污染（R-1）。

2) **绝不可加进 `PUBLIC_HIDDEN_APIS`**。
   —— β 拓扑（勇哥 2026-09-23 拍板）：**员工的主 workspace 就是线上站**，
      这些写接口在线上必须可用。一旦误加，线上全部 403，整个模块废掉。
      （线上只读的是 `/download/`、`/api/pdf_cloud` 这类"只有本机才有意义"的能力。）

3) **两类端点、两套鉴权**，不许混用：
   - 员工端 `/api/emp/{fav,tags,customers}/*` → 走 `_access_guard` 登录守卫，
     归属人 = `_current_user()['username']`；
   - 机器端 `/api/emp/export` → 在 `_access_guard` 里**显式豁免**（本地 worker
     没有也不该有浏览器会话），接口内部自己用 `_relay_token_ok()` 校验
     `X-Publish-Token`（与 /api/ingest、/api/ai/relay 同款，见 web/app.py）。

4) **owner 作用域 + W6 管理员边界**：
   - 所有查询**强制**按 `owner_username` 过滤；管理员要看全部必须显式 `?scope=all`；
   - **W6（勇哥已定）：管理员不能编辑他人客户** → 写接口一律校验
     `owner == 当前登录用户名`，管理员也不例外。唯一例外是"转移归属"
     （属管理动作，不是字段编辑），单独授权给 admin。

============================================================================
★ 数据落点
============================================================================
独立库 `data/employee.db`（见 core/employee_schema.py）。
**不塞主库 jproperty.db**：主库线上会被 `site_data.json` 按 stamp 整表重灌，
员工数据混进去会被冲掉。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

PREFIX = "/api/emp"

# 机器通道（需在 web/app.py 的 _access_guard 中豁免，接口内自行校验令牌）
MACHINE_PATHS = (PREFIX + "/export",)

# 导出涉及的表（PRD §3.2–§3.6 的 T1–T6）
EXPORT_TABLES = ("favorites", "tags", "property_tags", "customers",
                 "customer_properties", "customer_owner_log")

# 各表的"水位线列"：老行 updated_at 可能是 NULL → 用 COALESCE 兜 created_at
_WATERMARK_SQL = {
    "favorites": "COALESCE(updated_at, created_at)",
    "tags": "COALESCE(updated_at, created_at)",
    "property_tags": "COALESCE(updated_at, created_at)",
    "customers": "COALESCE(updated_at, created_at)",
    "customer_properties": "COALESCE(updated_at, created_at)",
    "customer_owner_log": "changed_at",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm_phone(p: str | None) -> str:
    """手机号归一化（仅留数字），用于查重/检索。"""
    return re.sub(r"\D", "", str(p or ""))


def _json():
    from flask import request
    return request.get_json(silent=True) or {}


def register(app):
    """把员工业务路由挂到 Flask app 上。

    延迟 `from web import app as W`：web/app.py 在模块级调用本函数，
    若在这里顶层 import 会形成循环导入。
    """
    from flask import jsonify, request

    from web import app as W                                   # noqa: F401（延迟导入，避免循环）
    from core import employee_schema as es

    _log = getattr(W, "log", None) or (lambda m: None)

    def _conn() -> sqlite3.Connection:
        con = sqlite3.connect(str(es.db_path()), timeout=20)
        con.row_factory = sqlite3.Row
        return con

    def _me():
        return W._current_user()

    def _owner() -> str:
        """当前归属人。无登录态时（本地 AUTH_ENABLED=False）退化为 'local'。"""
        me = _me()
        if me and me.get("username"):
            return str(me["username"])
        return str((W.CFG.get("accounts") or {}).get("default_owner") or "local")

    def _is_admin() -> bool:
        me = _me()
        return bool(me and str(me.get("role") or "") == "admin")

    def _need_owner() -> tuple[str, object] | tuple[None, None]:
        """员工端统一入口：未登录 → 401。"""
        me = _me()
        if not me:
            return None, (jsonify({"ok": False, "error": "未登录"}), 401)
        return str(me["username"]), None

    # ---------- 通用：查自己的收藏 ----------
    @app.get(PREFIX + "/fav")
    def _emp_fav_list():
        owner, err = _need_owner()
        if err:
            return err
        con = _conn()
        try:
            rows = con.execute(
                "SELECT property_no, created_at FROM favorites"
                " WHERE owner_username=? ORDER BY created_at DESC, id DESC", (owner,)).fetchall()
            return jsonify({"ok": True, "owner": owner,
                            "items": [dict(r) for r in rows]})
        finally:
            con.close()

    @app.post(PREFIX + "/fav/toggle")
    def _emp_fav_toggle():
        """加/取消收藏（幂等切换）。"""
        owner, err = _need_owner()
        if err:
            return err
        no = str((_json().get("property_no") or "")).strip()
        if not no:
            return jsonify({"ok": False, "error": "缺少 property_no"}), 400
        con = _conn()
        try:
            row = con.execute("SELECT id FROM favorites WHERE owner_username=? AND property_no=?",
                              (owner, no)).fetchone()
            if row:
                con.execute("DELETE FROM favorites WHERE id=?", (row["id"],))
                con.commit()
                return jsonify({"ok": True, "added": False, "property_no": no})
            con.execute("INSERT INTO favorites(owner_username, property_no, created_at, updated_at)"
                        " VALUES(?,?,?,?)", (owner, no, _now(), _now()))
            con.commit()
            return jsonify({"ok": True, "added": True, "property_no": no})
        except sqlite3.IntegrityError:
            return jsonify({"ok": True, "added": True, "property_no": no})
        finally:
            con.close()

    # ---------- 标签 ----------
    @app.get(PREFIX + "/tags")
    def _emp_tag_list():
        owner, err = _need_owner()
        if err:
            return err
        con = _conn()
        try:
            rows = con.execute("SELECT id, name, color, created_at FROM tags"
                               " WHERE owner_username=? ORDER BY id", (owner,)).fetchall()
            return jsonify({"ok": True, "owner": owner, "items": [dict(r) for r in rows]})
        finally:
            con.close()

    @app.post(PREFIX + "/tags")
    def _emp_tag_create():
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        name = str(d.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "标签名不能为空"}), 400
        con = _conn()
        try:
            old = con.execute("SELECT id FROM tags WHERE owner_username=? AND name=?",
                              (owner, name)).fetchone()
            if old:
                return jsonify({"ok": True, "id": old["id"], "dup": True})
            cur = con.execute("INSERT INTO tags(owner_username, name, color, created_at, updated_at)"
                              " VALUES(?,?,?,?,?)",
                              (owner, name, d.get("color"), _now(), _now()))
            con.commit()
            return jsonify({"ok": True, "id": cur.lastrowid})
        finally:
            con.close()

    @app.delete(PREFIX + "/tags/<int:tag_id>")
    def _emp_tag_delete(tag_id):
        """删标签（W6：只能删自己的）。同步解绑房源上的引用，避免孤儿数据。"""
        owner, err = _need_owner()
        if err:
            return err
        con = _conn()
        try:
            cur = con.execute("DELETE FROM tags WHERE id=? AND owner_username=?", (tag_id, owner))
            con.execute("DELETE FROM property_tags WHERE tag_id=? AND owner_username=?",
                        (tag_id, owner))
            con.commit()
            if cur.rowcount != 1:
                return jsonify({"ok": False, "error": "标签不存在或不是你的"}), 404
            return jsonify({"ok": True, "id": tag_id})
        finally:
            con.close()

    @app.post(PREFIX + "/tags/bind")
    def _emp_tag_bind():
        """房源 ⇄ 标签 绑定/解绑。body: {property_no, tag_id, unbind?}"""
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        no = str(d.get("property_no") or "").strip()
        try:
            tag_id = int(d.get("tag_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "tag_id 非法"}), 400
        if not no:
            return jsonify({"ok": False, "error": "缺少 property_no"}), 400
        con = _conn()
        try:
            tk = con.execute("SELECT id FROM tags WHERE id=? AND owner_username=?",
                             (tag_id, owner)).fetchone()
            if not tk:
                return jsonify({"ok": False, "error": "标签不存在或不是你的"}), 404
            if d.get("unbind"):
                con.execute("DELETE FROM property_tags WHERE owner_username=? AND property_no=?"
                            " AND tag_id=?", (owner, no, tag_id))
                con.commit()
                return jsonify({"ok": True, "bound": False})
            con.execute("INSERT OR IGNORE INTO property_tags"
                        "(owner_username, property_no, tag_id, created_at, updated_at)"
                        " VALUES(?,?,?,?,?)", (owner, no, tag_id, _now(), _now()))
            con.commit()
            return jsonify({"ok": True, "bound": True})
        finally:
            con.close()

    @app.get(PREFIX + "/tags/of")
    def _emp_tags_of():
        """某房源上我打的标签。query: ?property_no=xxx"""
        owner, err = _need_owner()
        if err:
            return err
        no = str(request.args.get("property_no") or "").strip()
        if not no:
            return jsonify({"ok": False, "error": "缺少 property_no"}), 400
        con = _conn()
        try:
            rows = con.execute(
                "SELECT t.id, t.name, t.color FROM property_tags pt JOIN tags t ON t.id=pt.tag_id"
                " WHERE pt.owner_username=? AND pt.property_no=? ORDER BY t.id", (owner, no)).fetchall()
            return jsonify({"ok": True, "items": [dict(r) for r in rows]})
        finally:
            con.close()

    # ---------- 客户 ----------
    @app.get(PREFIX + "/customers")
    def _emp_cust_list():
        """我的客户；管理员 `?scope=all` 看全部（W6：看可以，改不行）。"""
        owner, err = _need_owner()
        if err:
            return err
        scope_all = request.args.get("scope") == "all"
        con = _conn()
        try:
            if scope_all and _is_admin():
                rows = con.execute(
                    "SELECT id, owner_username, name, phone, note, created_at,"
                    " COALESCE(updated_at, created_at) AS updated_at FROM customers"
                    " WHERE COALESCE(deleted,0)=0"
                    " ORDER BY updated_at DESC, id DESC").fetchall()
                return jsonify({"ok": True, "scope": "all", "items": [dict(r) for r in rows]})
            rows = con.execute(
                "SELECT id, owner_username, name, phone, note, created_at,"
                " COALESCE(updated_at, created_at) AS updated_at FROM customers"
                " WHERE owner_username=? AND COALESCE(deleted,0)=0"
                " ORDER BY updated_at DESC, id DESC", (owner,)).fetchall()
            return jsonify({"ok": True, "owner": owner, "items": [dict(r) for r in rows]})
        finally:
            con.close()

    @app.post(PREFIX + "/customers")
    def _emp_cust_create():
        """建档。W3 已定：只记 created_at（登记日期），无任何时间权限字段。"""
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        name = str(d.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "客户名称不能为空"}), 400
        phone = str(d.get("phone") or "").strip() or None
        con = _conn()
        try:
            cur = con.execute(
                "INSERT INTO customers(owner_username, name, phone, phone_norm, note,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (owner, name, phone, _norm_phone(phone), d.get("note"), _now(), _now()))
            con.commit()
            return jsonify({"ok": True, "id": cur.lastrowid, "owner": owner})
        finally:
            con.close()

    @app.patch(PREFIX + "/customers/<int:cid>")
    def _emp_cust_update(cid):
        """改客户。**W6：只有归属本人能改，管理员也不行**（管理员只可读）。"""
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        con = _conn()
        try:
            row = con.execute("SELECT owner_username FROM customers WHERE id=?", (cid,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "客户不存在"}), 404
            if row["owner_username"] != owner:
                return jsonify({"ok": False, "error": "W6：只能编辑归属自己的客户"}), 403
            sets, vals = [], []
            for col in ("name", "note"):
                if col in d:
                    sets.append(f"{col}=?")
                    vals.append(d.get(col))
            if "phone" in d:
                ph = str(d.get("phone") or "").strip() or None
                sets += ["phone=?", "phone_norm=?"]
                vals += [ph, _norm_phone(ph)]
            if not sets:
                return jsonify({"ok": False, "error": "没有可更新字段"}), 400
            sets.append("updated_at=?")
            vals.append(_now())
            vals.append(cid)
            con.execute("UPDATE customers SET %s WHERE id=?" % ",".join(sets), vals)
            con.commit()
            return jsonify({"ok": True, "id": cid})
        finally:
            con.close()

    @app.delete(PREFIX + "/customers/<int:cid>")
    def _emp_cust_delete(cid):
        """**软删**（R-5 缓解）：只打 deleted 标记，不物理删除，保留对账轨迹。
        W6：只能删自己的。"""
        owner, err = _need_owner()
        if err:
            return err
        con = _conn()
        try:
            row = con.execute("SELECT owner_username FROM customers WHERE id=?", (cid,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "客户不存在"}), 404
            if row["owner_username"] != owner:
                return jsonify({"ok": False, "error": "W6：只能删除归属自己的客户"}), 403
            con.execute("UPDATE customers SET deleted=1, deleted_at=?, updated_at=? WHERE id=?",
                        (_now(), _now(), cid))
            con.commit()
            return jsonify({"ok": True, "id": cid, "soft": True})
        finally:
            con.close()

    @app.post(PREFIX + "/customers/<int:cid>/transfer")
    def _emp_cust_transfer(cid):
        """转移归属（**管理动作**，仅 admin）。W6 说"不能编辑他人客户"，但转移归属
        属管理权而非字段编辑，单独授权。T6 留痕 + 刷 updated_at 供增量同步捕获。"""
        owner, err = _need_owner()
        if err:
            return err
        if not _is_admin():
            return jsonify({"ok": False, "error": "仅管理员可转移归属"}), 403
        d = _json()
        to = str(d.get("to_username") or "").strip()
        if not to:
            return jsonify({"ok": False, "error": "缺少 to_username"}), 400
        con = _conn()
        try:
            row = con.execute("SELECT owner_username FROM customers WHERE id=?", (cid,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "客户不存在"}), 404
            old = row["owner_username"]
            if old == to:
                return jsonify({"ok": True, "id": cid, "noop": True})
            con.execute("UPDATE customers SET owner_username=?, updated_at=? WHERE id=?",
                        (to, _now(), cid))
            con.execute("INSERT INTO customer_owner_log(customer_id, from_owner, to_owner,"
                        " reason, changed_by, changed_at) VALUES(?,?,?,?,?,?)",
                        (cid, old, to, d.get("reason"), owner, _now()))
            con.commit()
            return jsonify({"ok": True, "id": cid, "from": old, "to": to})
        finally:
            con.close()

    @app.post(PREFIX + "/customers/<int:cid>/bind")
    def _emp_cust_bind(cid):
        """客户 ⇄ 意向房源。body: {property_no, intent?}"""
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        no = str(d.get("property_no") or "").strip()
        if not no:
            return jsonify({"ok": False, "error": "缺少 property_no"}), 400
        con = _conn()
        try:
            row = con.execute("SELECT id FROM customers WHERE id=?", (cid,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "客户不存在"}), 404
            con.execute("INSERT OR IGNORE INTO customer_properties"
                        "(customer_id, property_no, intent, created_at, created_by, updated_at)"
                        " VALUES(?,?,?,?,?,?)",
                        (cid, no, d.get("intent"), _now(), owner, _now()))
            con.commit()
            return jsonify({"ok": True, "customer_id": cid, "property_no": no})
        finally:
            con.close()

    # ---------- 机器通道：增量导出（供本地 employee_data_sync 回流）----------
    @app.get(PREFIX + "/export")
    def _emp_export():
        """批量导出 T1–T6 增量。**R-6 风险面**：必须在 `MACHINE_PATHS` 里被
        `_access_guard` 豁免、并在此处用 `_relay_token_ok()` 严格校验令牌；
        否则等于把全员的收藏/客户数据裸奔在公网。

        query: ?since=YYYY-MM-DD HH:MM:SS（留空=全量首拉）
        """
        if not W._relay_token_ok():
            return jsonify({"ok": False, "error": "令牌不对（401）"}), 401
        since = str(request.args.get("since") or "").strip()
        try:
            limit = max(1, min(int(request.args.get("limit") or 5000), 20000))
        except ValueError:
            limit = 5000
        con = _conn()
        try:
            out = {}
            for t in EXPORT_TABLES:
                wm = _WATERMARK_SQL[t]
                if since:
                    rows = con.execute(
                        "SELECT * FROM %s WHERE %s > ? ORDER BY %s ASC, id ASC LIMIT ?"
                        % (t, wm, wm), (since, limit)).fetchall()
                else:
                    rows = con.execute(
                        "SELECT * FROM %s ORDER BY id ASC LIMIT ?" % t, (limit,)).fetchall()
                out[t] = [dict(r) for r in rows]
            return jsonify({"ok": True, "since": since or None,
                            "next_since": _now(), "counts": {k: len(v) for k, v in out.items()},
                            "tables": out})
        finally:
            con.close()

    # ---------- 自检（QA 用）----------
    @app.get(PREFIX + "/status")
    def _emp_status():
        """当前登录态 + 库/表健康。QA 真机验收用（不暴露他人数据）。"""
        me = _me()
        con = _conn()
        try:
            tabs = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            return jsonify({"ok": True, "me": me, "owner": _owner(),
                            "is_admin": _is_admin(), "public": bool(W.PUBLIC),
                            "db": str(es.db_path()), "tables": tabs})
        finally:
            con.close()

    _log("[员工API] 已挂载 %s/*（导出端点 %s 走 X-Publish-Token 机器通道）"
         % (PREFIX, "/".join(MACHINE_PATHS)))
    return app
