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
   - 机器端 `/api/emp/export`、`/api/emp/import` → 在 `_access_guard` 里
     **显式豁免**（本地 worker 没有也不该有浏览器会话），接口内部自己用
     `_relay_token_ok()` 校验 `X-Publish-Token`
     （与 /api/ingest、/api/ai/relay 同款，见 web/app.py）。
     import 是 v1.9.108 为「v100/2104 双库员工数据合并迁移」新增的**单向写通道**。

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

import json
import re
import sqlite3
from datetime import datetime

PREFIX = "/api/emp"

# 机器通道（需在 web/app.py 的 _access_guard 中豁免，接口内自行校验令牌）
#   v1.9.108：新增 import —— v100/2104 双库员工数据合并迁移用（单向 2104 → v100）。
MACHINE_PATHS = (PREFIX + "/export", PREFIX + "/import")

# ---- 导入（机器写通道）涉及的八张表 ----
#   与 EXPORT_TABLES 对齐，另加 emp_action_log（本地追加日志，导出不带、导入兼容）。
IMPORT_TABLES = ("customers", "tags",                 # ① 主键被引用的先做
                 "favorites",                          # ② 无外键
                 "property_tags",                      # ③ 引用 tags.id
                 "customer_properties",                #    引用 customers.id
                 "customer_tags",                      #    引用 customers.id + tags.id
                 "customer_owner_log",                 #    引用 customers.id
                 "emp_action_log")

# 各表的"业务键"：判定两行是不是**同一个实体**。
#   ⚠ 绝不能只看 id —— 2104 / v100 是两个独立库，autoincrement 各自生长，
#     **同一 id 可能是完全不同的两个人/两条关联**（2026-10-03 实测：
#     customer_properties 的 id=28/29 在两端分别是不同房源 → 若按 id 直接
#     INSERT OR REPLACE，v100 独有的 2 条关联会被静默吃掉）。
_IMPORT_KEY_COLS = {
    "favorites":            ("owner_username", "property_no"),
    "tags":                 ("owner_username", "category", "name"),
    "property_tags":        ("owner_username", "property_no", "tag_id"),
    "customers":            ("owner_username", "name"),
    "customer_properties":  ("customer_id", "property_no"),
    "customer_owner_log":   ("customer_id", "to_owner", "changed_at"),
    "customer_tags":        ("owner_username", "customer_id", "tag_id"),
    "emp_action_log":       ("emp_username", "action", "target_type",
                             "target_id", "created_at"),
}

# 外键列 → 被引用表。源库 id 若在目标库被重排（撞号避让），这里做**同义 id 重映射**，
#   保证 property_tags.tag_id / customer_properties.customer_id 等仍然指得对。
_IMPORT_FK_COLS = {
    "property_tags":       {"tag_id": "tags"},
    "customer_properties": {"customer_id": "customers"},
    "customer_tags":       {"customer_id": "customers", "tag_id": "tags"},
    "customer_owner_log":  {"customer_id": "customers"},
}

# 导出涉及的表（PRD §3.2–§3.6 的 T1–T6）
EXPORT_TABLES = ("favorites", "tags", "property_tags", "customers",
                 "customer_properties", "customer_owner_log",
                 # v2.0.x：客户 ⇄ 客户类标签，同样要参与本地回流同步
                 "customer_tags")

# 各表的"水位线列"：老行 updated_at 可能是 NULL → 用 COALESCE 兜 created_at
_WATERMARK_SQL = {
    "favorites": "COALESCE(updated_at, created_at)",
    "tags": "COALESCE(updated_at, created_at)",
    "property_tags": "COALESCE(updated_at, created_at)",
    "customers": "COALESCE(updated_at, created_at)",
    "customer_properties": "COALESCE(updated_at, created_at)",
    "customer_owner_log": "changed_at",
    "customer_tags": "COALESCE(updated_at, created_at)",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm_phone(p: str | None) -> str:
    """手机号归一化（仅留数字），用于查重/检索。"""
    return re.sub(r"\D", "", str(p or ""))


def _json():
    from flask import request
    return request.get_json(silent=True) or {}


def _merge_rows(con, table, rows, id_maps) -> dict:
    """把 rows 按「业务键」合并进 table（严格取并集，绝不删目标已有行）。

    ⚠ 为什么不用 INSERT OR REPLACE：REPLACE 遇冲突是「先删后插」。两库 id 各自
      autoincrement → **撞号 ≠ 同实体**，直接 REPLACE 会吃掉目标库独有的行。

    合并规则：
      · 同 id 且同业务键  → 就地 UPDATE（刷新字段）
      · 业务键命中唯一行  → 合并进那一行（**保留目标 id**）
      · 目标无此业务键    → INSERT（源 id 已被别的实体占用时，让自增分配新 id）

    返回 {inserted, updated, skipped, remapped}；并把「源 id → 目标 id」写入
    id_maps[table]，供下游表做外键重映射。
    """
    tcols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table).fetchall()]
    if not tcols:
        return {"inserted": 0, "updated": 0, "skipped": 0, "remapped": 0,
                "error": "表不存在"}
    keycols = _IMPORT_KEY_COLS.get(table) or ()
    fkmap = _IMPORT_FK_COLS.get(table) or {}

    def _key(r):
        return tuple(r.get(c) for c in keycols)

    exist, by_key = {}, {}
    for _r in con.execute("SELECT * FROM %s" % table).fetchall():
        d = dict(_r)
        k = _key(d)
        exist[d.get("id")] = k
        by_key.setdefault(k, []).append(d.get("id"))
    used = set(exist.keys())

    ins = upd = skip = remap = 0
    my_map = {}
    for row in rows:
        if not isinstance(row, dict):
            skip += 1
            continue
        r = dict(row)
        # ① 外键重映射：源 id 若在目标库被重排过，指向重排后的新 id
        for col, ref in fkmap.items():
            if r.get(col) is not None:
                m = id_maps.get(ref) or {}
                if r[col] in m and m[r[col]] != r[col]:
                    r[col] = m[r[col]]
                    remap += 1
        k = _key(r)
        sid = r.get("id")
        tid = None
        if sid is not None and sid in exist:
            if exist[sid] == k:
                tid = sid                       # 同 id 同实体 → 就地刷新字段
            else:
                # ⚠ id 被**另一个实体**占着 → 绝不覆盖它
                cands = by_key.get(k) or []
                tid = cands[0] if len(cands) == 1 else None
        else:
            cands = by_key.get(k) or []
            tid = cands[0] if cands else None

        sets = {c: v for c, v in r.items() if c in tcols and c != "id"}
        if tid is not None:
            if sets:
                con.execute("UPDATE %s SET %s WHERE id=?" % (
                    table, ",".join("%s=?" % c for c in sets)),
                    list(sets.values()) + [tid])
                upd += 1
            else:
                skip += 1
            my_map[sid] = tid
            continue
        # ② 目标没有这行 → 插入；源 id 被占则让自增分配新 id
        cols = list(sets.keys())
        if sid is not None and sid not in used:
            cols = ["id"] + cols
            vals = [sid] + [sets[c] for c in sets]
        else:
            vals = [sets[c] for c in sets]
        if not cols:
            skip += 1
            continue
        cur = con.execute("INSERT INTO %s (%s) VALUES (%s)" % (
            table, ",".join(cols), ",".join("?" * len(cols))), vals)
        nid = cur.lastrowid or sid
        ins += 1
        used.add(nid)
        exist[nid] = k
        by_key.setdefault(k, []).append(nid)
        my_map[sid] = nid
    id_maps[table] = my_map
    return {"inserted": ins, "updated": upd, "skipped": skip, "remapped": remap}


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

    def _log_action(emp_username, action, target_type, target_id, detail=None):
        """F7（定稿）：记录员工操作日志（弱依赖，失败仅警告，不阻断主业务）。"""
        try:
            c2 = sqlite3.connect(str(es.db_path()), timeout=20)
            c2.execute(
                "INSERT INTO emp_action_log(emp_username, action, target_type, target_id,"
                " detail_json, created_at) VALUES(?,?,?,?,?,?)",
                (emp_username, action, target_type, str(target_id),
                 json.dumps(detail or {}, ensure_ascii=False), _now()))
            c2.commit()
            c2.close()
        except Exception as _e:
            _log("emp_action_log 写入失败(弱依赖，忽略): %s" % _e)

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

    # ==================================================================
    # v2.0.x 辅助（勇哥 2026-09-26 拍板：收藏下沉动线 + 标签管理二分）
    # ==================================================================
    # 房源卡片字段：与查询页卡片口径一致（员工业务收藏列表要「显示完全一致」）
    def _prop_cards(nos):
        """番号批量 → 房源卡片数据。

        🔑 **直接复用查询页的 `W._row_payload()`**（web/app.py:2240）——
        勇哥 2026-09-26 要求「员工业务的收藏显示要与房源查询完全一致」，
        而 `_row_payload` 正是查询页把房源行整理成前端形状的唯一出口
        （解析 detail_json / 算 has_detail / 取 trade_type 首行 / 组装 media / 拼 pdf_url）。
        用它 = 与查询页**同源**，不再各写一份（此前两套字段已漂移出问题）。

        ⚠ 只读主库；主库线上会被 site_data.json 重灌，这里取当前快照。
        """
        nos = [str(n).strip() for n in (nos or []) if str(n).strip()]
        if not nos:
            return []
        try:
            con = W.STORE.conn
            ph = ",".join("?" * len(nos))
            rows = con.execute(
                "SELECT * FROM properties WHERE property_no IN (%s)" % ph, nos).fetchall()
            return [W._row_payload(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            _log("[emp] 取房源卡片失败：%s: %s" % (type(e).__name__, e))
            return []

    def _norm_cat(v):
        """标签分类归一化：'cust'=客户类，其余（含存量 NULL）=收藏类。

        勇哥 Q3 拍板：现有标签都是房源标签 → 存量 NULL 一律按 'fav' 处理。
        """
        return "cust" if str(v or "").strip().lower() == "cust" else "fav"

    def _page_args(default_size=50, max_size=200):
        """统一分页参数解析（勇哥 Q6：50 条/页；一律服务端分页）。"""
        try:
            page = max(1, int(request.args.get("page") or 1))
        except ValueError:
            page = 1
        try:
            size = max(1, min(int(request.args.get("size") or default_size), max_size))
        except ValueError:
            size = default_size
        return page, size

    # ---------- 收藏 ----------
    @app.get(PREFIX + "/fav")
    def _emp_fav_list():
        """我的收藏（v2.0.x：**返回完整房源卡片** + 50/页服务端分页）。

        勇哥要求「员工业务里的收藏，显示内容与房源查询页完全一致」→
        这里直接带出房源字段（_prop_cards），前端复用同一套卡片渲染。
        """
        owner, err = _need_owner()
        if err:
            return err
        page, size = _page_args()
        # v1.9.95 C9/R42：收藏栏标签墙点击 → 按标签筛选（?tag=<tag_id>）
        try:
            tag = int(request.args.get("tag") or 0) or None
        except (TypeError, ValueError):
            tag = None
        con = _conn()
        try:
            if tag:
                w = (" FROM favorites f JOIN property_tags pt ON pt.property_no=f.property_no"
                     " WHERE f.owner_username=? AND pt.tag_id=?")
                args = (owner, tag)
            else:
                # ⚠ 必须同样给别名 f —— 下面 SELECT/ORDER BY 都引用 f.*，
                #    漏别名会报 "no such column: f.id" ⇒ 收藏列表整体 500（2026-09-27 已踩）。
                w = " FROM favorites f WHERE f.owner_username=?"
                args = (owner,)
            total = con.execute("SELECT COUNT(*) c" + w, args).fetchone()["c"]
            rows = con.execute(
                "SELECT f.property_no AS property_no, f.created_at AS created_at" + w
                + " ORDER BY f.created_at DESC, f.id DESC LIMIT ? OFFSET ?",
                args + (size, (page - 1) * size)).fetchall()
            nos = [r["property_no"] for r in rows]
            cards = {c["property_no"]: c for c in _prop_cards(nos)}
            items = []
            for r in rows:
                d = dict(cards.get(r["property_no"])
                         or {"property_no": r["property_no"], "gone": True})
                d["fav_created_at"] = r["created_at"]
                items.append(d)
            return jsonify({"ok": True, "owner": owner, "items": items,
                            "total": total, "page": page, "size": size})
        finally:
            con.close()

    @app.get(PREFIX + "/fav/nos")
    def _emp_fav_nos():
        """我收藏的全部番号（轻量）。供查询页/详情页一次性标记「已收藏」，
        避免列表 50 条各发一次请求（R3 风险应对）。"""
        owner, err = _need_owner()
        if err:
            return err
        con = _conn()
        try:
            rows = con.execute("SELECT property_no FROM favorites WHERE owner_username=?",
                               (owner,)).fetchall()
            return jsonify({"ok": True, "owner": owner,
                            "nos": [r["property_no"] for r in rows]})
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
                _log_action(owner, "uncollect", "property", no, {"property_no": no})
                return jsonify({"ok": True, "added": False, "property_no": no})
            con.execute("INSERT INTO favorites(owner_username, property_no, created_at, updated_at)"
                        " VALUES(?,?,?,?)", (owner, no, _now(), _now()))
            con.commit()
            _log_action(owner, "collect", "property", no, {"property_no": no})
            return jsonify({"ok": True, "added": True, "property_no": no})
        except sqlite3.IntegrityError:
            return jsonify({"ok": True, "added": True, "property_no": no})
        finally:
            con.close()

    # ---------- 标签（v2.0.x：分「收藏类 fav / 客户类 cust」两类）----------
    @app.get(PREFIX + "/tags")
    def _emp_tag_list():
        """标签列表。?category=fav|cust 过滤（不传=全部）。

        v2.0.x：每项带 `count` = 该标签关联的对象数
        （收藏类=房源数 / 客户类=客户数），供标签管理页显示「N 个」。
        """
        owner, err = _need_owner()
        if err:
            return err
        cat = str(request.args.get("category") or "").strip()
        con = _conn()
        try:
            rows = con.execute(
                "SELECT id, name, color, COALESCE(category,'fav') AS category, created_at,"
                " COALESCE(updated_at, created_at) AS updated_at FROM tags"
                " WHERE owner_username=? ORDER BY id", (owner,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d["category"] == "cust":
                    d["count"] = con.execute(
                        "SELECT COUNT(*) c FROM customer_tags ct JOIN customers c"
                        " ON c.id=ct.customer_id WHERE ct.tag_id=? AND COALESCE(c.deleted,0)=0",
                        (d["id"],)).fetchone()["c"]
                else:
                    d["count"] = con.execute(
                        "SELECT COUNT(*) c FROM property_tags WHERE tag_id=?",
                        (d["id"],)).fetchone()["c"]
                out.append(d)
            if cat:
                want = _norm_cat(cat)
                out = [d for d in out if d["category"] == want]
            return jsonify({"ok": True, "owner": owner, "items": out})
        finally:
            con.close()

    @app.post(PREFIX + "/tags")
    def _emp_tag_create():
        """新建标签。body: {name, color?, category?: 'fav'|'cust'}（默认 fav）。

        ⚠ 名称**全局唯一**（表内 UNIQUE(owner,name)）：同名直接复用已有标签，
        不新建（SQLite 改不了 UNIQUE，且"禁止 DROP/重建"是铁律）。
        """
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        name = str(d.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "标签名不能为空"}), 400
        cat = _norm_cat(d.get("category"))
        con = _conn()
        try:
            # 唯一约束是 (owner, category, name) → **同类同名**才算重复；
            # 收藏类与客户类**可以同名**（v2.0.x 建新表迁移解除旧限制）
            old = con.execute("SELECT id, COALESCE(category,'fav') AS category FROM tags"
                              " WHERE owner_username=? AND name=? AND COALESCE(category,'fav')=?",
                              (owner, name, cat)).fetchone()
            if old:
                return jsonify({"ok": True, "id": old["id"], "dup": True,
                                "category": old["category"]})
            cur = con.execute("INSERT INTO tags(owner_username, name, color, category,"
                              " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                              (owner, name, d.get("color"), cat, _now(), _now()))
            con.commit()
            return jsonify({"ok": True, "id": cur.lastrowid, "category": cat})
        finally:
            con.close()

    @app.patch(PREFIX + "/tags/<int:tag_id>")
    def _emp_tag_update(tag_id):
        """标签改名 / 改色（W6 同款边界：只能改自己的）。"""
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        con = _conn()
        try:
            row = con.execute("SELECT id FROM tags WHERE id=? AND owner_username=?",
                              (tag_id, owner)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "标签不存在或不是你的"}), 404
            sets, vals = [], []
            if "name" in d and str(d.get("name") or "").strip():
                sets.append("name=?")
                vals.append(str(d["name"]).strip())
            if "color" in d:
                sets.append("color=?")
                vals.append(d.get("color"))
            if not sets:
                return jsonify({"ok": False, "error": "没有可更新字段"}), 400
            sets.append("updated_at=?")
            vals.append(_now())
            vals.append(tag_id)
            try:
                con.execute("UPDATE tags SET %s WHERE id=?" % ",".join(sets), vals)
                con.commit()
            except sqlite3.IntegrityError:
                return jsonify({"ok": False, "error": "同名标签已存在"}), 409
            return jsonify({"ok": True, "id": tag_id})
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
            tk = con.execute("SELECT id, COALESCE(category,'fav') AS category FROM tags"
                             " WHERE id=? AND owner_username=?", (tag_id, owner)).fetchone()
            if not tk:
                return jsonify({"ok": False, "error": "标签不存在或不是你的"}), 404
            if tk["category"] != "fav":
                return jsonify({"ok": False,
                                "error": "这是客户类标签，不能绑到房源（两类完全分开）"}), 400
            if d.get("unbind"):
                con.execute("DELETE FROM property_tags WHERE owner_username=? AND property_no=?"
                            " AND tag_id=?", (owner, no, tag_id))
                con.commit()
                _log_action(owner, "tag_del", "tag", tag_id,
                            {"tag_id": tag_id, "property_no": no, "category": tk["category"]})
                return jsonify({"ok": True, "bound": False})
            con.execute("INSERT OR IGNORE INTO property_tags"
                        "(owner_username, property_no, tag_id, created_at, updated_at)"
                        " VALUES(?,?,?,?,?)", (owner, no, tag_id, _now(), _now()))
            con.commit()
            _log_action(owner, "tag_add", "tag", tag_id,
                        {"tag_id": tag_id, "property_no": no, "category": tk["category"]})
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

    @app.get(PREFIX + "/tags/<int:tag_id>/items")
    def _emp_tag_items(tag_id):
        """点标签下钻（勇哥 C-4）：收藏类→列出房源卡片；客户类→列出客户。

        **50/页服务端分页**（勇哥 Q6；且按铁律「统计/分页一律服务端 SQL」）。
        """
        owner, err = _need_owner()
        if err:
            return err
        page, size = _page_args()
        con = _conn()
        try:
            t = con.execute("SELECT id, name, color, COALESCE(category,'fav') AS category"
                            " FROM tags WHERE id=? AND owner_username=?",
                            (tag_id, owner)).fetchone()
            if not t:
                return jsonify({"ok": False, "error": "标签不存在或不是你的"}), 404
            cat = t["category"]
            if cat == "cust":
                total = con.execute(
                    "SELECT COUNT(*) c FROM customer_tags ct JOIN customers c"
                    " ON c.id=ct.customer_id WHERE ct.tag_id=? AND COALESCE(c.deleted,0)=0",
                    (tag_id,)).fetchone()["c"]
                rows = con.execute(
                    "SELECT c.id, c.name, c.phone, c.note, c.owner_username, c.created_at,"
                    " COALESCE(c.updated_at, c.created_at) AS updated_at"
                    " FROM customer_tags ct JOIN customers c ON c.id=ct.customer_id"
                    " WHERE ct.tag_id=? AND COALESCE(c.deleted,0)=0"
                    " ORDER BY updated_at DESC, c.id DESC LIMIT ? OFFSET ?",
                    (tag_id, size, (page - 1) * size)).fetchall()
                items = [dict(r) for r in rows]
            else:
                total = con.execute("SELECT COUNT(*) c FROM property_tags WHERE tag_id=?",
                                    (tag_id,)).fetchone()["c"]
                rows = con.execute(
                    "SELECT property_no, created_at FROM property_tags WHERE tag_id=?"
                    " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                    (tag_id, size, (page - 1) * size)).fetchall()
                nos = [r["property_no"] for r in rows]
                cards = {c["property_no"]: c for c in _prop_cards(nos)}
                items = []
                for r in rows:
                    d = dict(cards.get(r["property_no"])
                             or {"property_no": r["property_no"], "gone": True})
                    d["fav_created_at"] = r["created_at"]
                    items.append(d)
            return jsonify({"ok": True, "tag": dict(t), "category": cat, "items": items,
                            "total": total, "page": page, "size": size})
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
        # v2.0.x：q = 关键字（名称 / 手机号 / 备注），供「添加客户」选择器搜索老客户
        q = str(request.args.get("q") or "").strip()
        # v1.9.95 C12/R45：客户栏标签墙点击 → 按客户标签筛选（?tag=<tag_id>）
        try:
            tag = int(request.args.get("tag") or 0) or None
        except (TypeError, ValueError):
            tag = None
        con = _conn()
        try:
            sql = ("SELECT id, owner_username, name, phone, note, created_at,"
                   " COALESCE(updated_at, created_at) AS updated_at FROM customers"
                   " WHERE COALESCE(deleted,0)=0")
            args = []
            all_scope = bool(scope_all and _is_admin())
            if not all_scope:
                sql += " AND owner_username=?"
                args.append(owner)
            if tag:
                sql += " AND id IN (SELECT customer_id FROM customer_tags WHERE tag_id=?)"
                args.append(tag)
            if q:
                sql += " AND (name LIKE ? OR phone LIKE ? OR COALESCE(note,'') LIKE ?)"
                like = "%" + q + "%"
                args += [like, like, like]
            sql += " ORDER BY updated_at DESC, id DESC LIMIT 200"
            rows = con.execute(sql, args).fetchall()
            items = [dict(r) for r in rows]
            # v2.0.x：附上每个客户的标签（勇哥：在「添加客户」里要能看到客户**已有标签**）
            for it in items:
                it["tags"] = [dict(x) for x in con.execute(
                    "SELECT t.id, t.name, t.color FROM customer_tags ct"
                    " JOIN tags t ON t.id=ct.tag_id"
                    " WHERE ct.customer_id=? ORDER BY t.id", (it["id"],)).fetchall()]
                # v1.9.95 C11/R44：关联房源数。口径唯一 = T5 customer_properties
                # （即「绑房源」动作），PRD §4.2 明确不得用 T7 客户标签间接推导。
                it["prop_count"] = con.execute(
                    "SELECT COUNT(*) c FROM customer_properties WHERE customer_id=?",
                    (it["id"],)).fetchone()["c"]
            return jsonify({"ok": True, "owner": owner,
                            "scope": ("all" if all_scope else "mine"),
                            "items": items, "total": len(items)})
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
        """客户 ⇄ 意向房源。body: {property_no, intent?, unbind?}

        v1.9.95 C11 补充：`unbind=true` 时**解除**该客户与此房源的关联
        （勇哥：客户卡片展开的房源里点「已添加客户」= 取消该客户对这套房的关联）。
        """
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        no = str(d.get("property_no") or "").strip()
        if not no:
            return jsonify({"ok": False, "error": "缺少 property_no"}), 400
        con = _conn()
        try:
            row = con.execute(
                "SELECT id, owner_username, COALESCE(deleted,0) AS deleted"
                " FROM customers WHERE id=?", (cid,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "客户不存在"}), 404
            if row["deleted"] == 1:
                return jsonify({"ok": False, "error": "客户已删除"}), 404
            # F4（定稿）：归属校验（W6）—— 仅归属员工可绑/解；管理员对他人客户只读
            if row["owner_username"] != owner:
                return jsonify({"ok": False, "error": "只能操作自己的客户"}), 403
            if d.get("unbind"):
                con.execute("DELETE FROM customer_properties"
                            " WHERE customer_id=? AND property_no=?", (cid, no))
                con.commit()
                _log_action(owner, "unbind", "customer", cid,
                            {"customer_id": cid, "property_no": no})
                return jsonify({"ok": True, "customer_id": cid, "property_no": no,
                                "unbound": True})
            con.execute("INSERT OR IGNORE INTO customer_properties"
                        "(customer_id, property_no, intent, created_at, created_by, updated_at)"
                        " VALUES(?,?,?,?,?,?)",
                        (cid, no, d.get("intent"), _now(), owner, _now()))
            con.commit()
            _log_action(owner, "bind", "customer", cid,
                        {"customer_id": cid, "property_no": no})
            return jsonify({"ok": True, "customer_id": cid, "property_no": no})
        finally:
            con.close()

    @app.get(PREFIX + "/customers/<int:cid>/tags")
    def _emp_cust_tags(cid):
        """某客户已打的标签（客户类）。"""
        owner, err = _need_owner()
        if err:
            return err
        con = _conn()
        try:
            row = con.execute("SELECT id FROM customers WHERE id=? AND owner_username=?",
                              (cid, owner)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "客户不存在或不是你的"}), 404
            rows = con.execute(
                "SELECT t.id, t.name, t.color FROM customer_tags ct JOIN tags t ON t.id=ct.tag_id"
                " WHERE ct.customer_id=? AND ct.owner_username=? ORDER BY t.id",
                (cid, owner)).fetchall()
            return jsonify({"ok": True, "items": [dict(r) for r in rows]})
        finally:
            con.close()

    @app.post(PREFIX + "/customers/<int:cid>/tags")
    def _emp_cust_tags_set(cid):
        """给客户设标签（**覆盖式**）。body: {tag_ids:[...]}

        建新标签走 `POST /api/emp/tags`（category=cust），这里只管绑定关系。
        ⚠ 只接受**客户类**标签，收藏类会被忽略（勇哥：两类完全分开、无相关性）。
        """
        owner, err = _need_owner()
        if err:
            return err
        d = _json()
        try:
            ids = [int(x) for x in (d.get("tag_ids") or [])]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "tag_ids 非法"}), 400
        con = _conn()
        try:
            row = con.execute("SELECT id FROM customers WHERE id=? AND owner_username=?",
                              (cid, owner)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "客户不存在或不是你的"}), 404
            valid = {r["id"] for r in con.execute(
                "SELECT id FROM tags WHERE owner_username=?"
                " AND COALESCE(category,'fav')='cust'", (owner,)).fetchall()}
            keep = [i for i in ids if i in valid]
            con.execute("DELETE FROM customer_tags WHERE customer_id=? AND owner_username=?",
                        (cid, owner))
            for i in keep:
                con.execute("INSERT OR IGNORE INTO customer_tags"
                            "(owner_username, customer_id, tag_id, created_at, updated_at)"
                            " VALUES(?,?,?,?,?)", (owner, cid, i, _now(), _now()))
            con.commit()
            return jsonify({"ok": True, "customer_id": cid, "bound": keep})
        finally:
            con.close()

    @app.get(PREFIX + "/cust/nos")
    def _emp_cust_nos():
        """我已关联过客户的房源番号集合（轻量）。

        勇哥 2026-09-26：查询页/详情页的「添加客户」按钮要能**一眼看出这套房加过客户**
        → 前端一次性拉这个集合做选中态（避免列表 50 条各查一次）。
        （一个房源可关联多个客户，这里只要「至少关联了一个我的客户」即算已添加。）
        """
        owner, err = _need_owner()
        if err:
            return err
        con = _conn()
        try:
            rows = con.execute(
                "SELECT DISTINCT cp.property_no FROM customer_properties cp"
                " JOIN customers c ON c.id=cp.customer_id"
                " WHERE c.owner_username=? AND COALESCE(c.deleted,0)=0",
                (owner,)).fetchall()
            return jsonify({"ok": True, "owner": owner,
                            "nos": [r["property_no"] for r in rows]})
        finally:
            con.close()

    @app.get(PREFIX + "/customers/by-property")
    def _emp_cust_by_property():
        """v1.9.95 C10/R43：给定房源番号，返回与它**已关联**的客户列表。

        勇哥：点「已添加客户」要能一眼看到「这套房关联了哪些客户」，
        而不是只弹出添加框。口径与 _emp_cust_nos 一致：默认只认「我的」客户；
        管理员带 scope=all 时看全部（W6 隔离）。
        """
        owner, err = _need_owner()
        if err:
            return err
        no = str(request.args.get("no") or "").strip()
        if not no:
            return jsonify({"ok": False, "error": "缺少 no"}), 400
        scope_all = bool(request.args.get("scope") == "all" and _is_admin())
        con = _conn()
        try:
            sql = ("SELECT c.id, c.name, c.phone, c.owner_username, c.note,"
                   " cp.created_at AS bound_at FROM customers c"
                   " JOIN customer_properties cp ON cp.customer_id=c.id"
                   " WHERE cp.property_no=? AND COALESCE(c.deleted,0)=0")
            args = [no]
            if not scope_all:
                sql += " AND c.owner_username=?"
                args.append(owner)
            sql += " ORDER BY c.id"
            rows = con.execute(sql, args).fetchall()
            return jsonify({"ok": True, "no": no, "owner": owner,
                            "scope": ("all" if scope_all else "mine"),
                            "items": [dict(r) for r in rows], "total": len(rows)})
        finally:
            con.close()

    @app.get(PREFIX + "/customers/<int:cid>/properties")
    def _emp_cust_properties(cid):
        """v1.9.95 C11/R44：某客户关联的房源（**完整房源卡片**）。

        勇哥：客户卡片上点「关联 N 套」就地展开，直接看到推给这个客户的房，
        样式与房源查询页一致（复用 _prop_cards）。W6 隔离：仅归属人本人 / 管理员可读。
        计数与列表口径均为 T5 customer_properties（PRD §4.2）。
        """
        owner, err = _need_owner()
        if err:
            return err
        con = _conn()
        try:
            row = con.execute(
                "SELECT owner_username FROM customers WHERE id=? AND COALESCE(deleted,0)=0",
                (cid,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "客户不存在"}), 404
            if row["owner_username"] != owner and not _is_admin():
                return jsonify({"ok": False, "error": "无权查看他人客户"}), 403
            nos = [r["property_no"] for r in con.execute(
                "SELECT property_no FROM customer_properties WHERE customer_id=? ORDER BY id",
                (cid,)).fetchall()]
            cards = _prop_cards(nos)
            return jsonify({"ok": True, "id": cid, "items": cards, "total": len(cards)})
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

    # 合并实现见模块级 `_merge_rows`（纯 sqlite3、不依赖 Flask，便于离线单测）。

    # ---------- 机器通道：幂等导入（供 v100/2104 双库合并迁移）----------
    @app.post(PREFIX + "/import")
    def _emp_import():
        """把一批员工业务行**合并**进本库（v1.9.108 · v100 迁移专用）。

        为什么不是 `INSERT OR REPLACE`
        ------------------------------
        REPLACE 的语义是「有冲突就先删再插」。两库 id 各自生长 → 撞号时那一行
        在目标库其实是**另一个实体**，REPLACE 会把它删掉 —— 实测 2104 的
        customer_properties id=28/29 会吃掉 v100 独有的 2 条客户↔房源关联。
        本端点改为 **业务键合并（严格取并集）**：
          · 同 id 且同业务键   → 就地 UPDATE（刷新字段）
          · 业务键命中唯一行   → 合并进那一行（**保留目标 id**，源 id 记入重映射表）
          · 业务键在目标不存在 → INSERT；若源 id 已被别的实体占用，则让自增分配新 id
        结果 = 源 ∪ 目标，**两端都不丢一行**。

        鉴权：与 export 同款 —— `_access_guard` 豁免 + 本接口 `X-Publish-Token` 校验。
        入参：{"tables": {表名: [行...]}, "dry": false}
        """
        if not W._relay_token_ok():
            return jsonify({"ok": False, "error": "令牌不对（401）"}), 401
        body = _json()
        incoming = body.get("tables") or {}
        dry = bool(body.get("dry"))
        if not isinstance(incoming, dict) or not incoming:
            return jsonify({"ok": False, "error": "入参 tables 为空"}), 400

        # 幂等建表（线上容器重建后 employee.db 可能是空的）
        try:
            es.ensure_employee_tables(str(es.db_path()))
        except Exception as _ee:                                   # noqa: BLE001
            return jsonify({"ok": False, "error": "建表失败：%s" % _ee}), 500

        con = _conn()
        stats, id_maps = {}, {}
        try:
            con.execute("BEGIN")
            for t in IMPORT_TABLES:
                if t not in incoming:
                    continue
                rows = incoming.get(t) or []
                if not isinstance(rows, list):
                    continue
                stats[t] = _merge_rows(con, t, rows, id_maps)
            if dry:
                con.execute("ROLLBACK")
            else:
                con.execute("COMMIT")
        except Exception as _e:                                    # noqa: BLE001
            try:
                con.execute("ROLLBACK")
            except Exception:                                      # noqa: BLE001
                pass
            con.close()
            return jsonify({"ok": False, "error": f"{type(_e).__name__}: {_e}"}), 500
        con.close()
        total = {k: (v or {}) for k, v in stats.items()}
        _log("[员工API] import%s → %s"
             % ("（dry）" if dry else "", json.dumps(total, ensure_ascii=False)))
        return jsonify({"ok": True, "dry": dry, "tables": total,
                        "remap": {k: len(v) for k, v in id_maps.items()}})

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
