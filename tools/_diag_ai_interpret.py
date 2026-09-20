# -*- coding: utf-8 -*-
"""独立诊断：验证 AI 解读重命名 + editor 留痕修复 + 设置项 read_kinds（不碰 8765，用临时库副本，零污染）。

验证点：
  1) 旧路由 /api/ai-structure/<no> 登录后应 404（已移除）；新路由 /api/ai-interpret/<no> 应 200。
  2) 模拟真实员工登录后 POST edit，ai_edit_log.editor 应 == 该员工（修复前恒为空串）。
  3) 详情页 /p/<no> HTML 应包含「AI 解读」且不含「AI 结构」。
  4) POST /api/ai/settings 写 read_kinds 应持久化到 config.yaml（备份+还原，零污染）。
  5) read_kinds 过滤：未勾选種目调 /api/ai/<no>/run 应 400 提示「未启用 AI 读取」；勾选该種目应放行（不回 400）。
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))

import web.app as app_mod
from core.ai_structure_store import AIStructureStore

REAL_AI_DB = os.path.join(ROOT, "data", "ai_pdf_store.db")
REAL_MAIN_DB = os.path.join(ROOT, "data", "jproperty.db")
CFG_PATH = os.path.join(ROOT, "config.yaml")

# 真实员工（accounts 表存在 → _current_user() 才返回有效用户，editor 才非空）
m = sqlite3.connect(REAL_MAIN_DB)
uid = m.execute("SELECT username FROM accounts WHERE disabled=0 ORDER BY id LIMIT 1").fetchone()
m.close()
assert uid, "accounts 表无可用员工"
uid = uid[0]
print("[*] 模拟登录员工:", uid)

# 主库取一个真实房源 + property_subtype（run 接口要求房源在主库；filter 读 property_subtype 归一）
mm = sqlite3.connect(REAL_MAIN_DB)
mm.row_factory = sqlite3.Row
rw = mm.execute("SELECT property_no, property_subtype FROM properties WHERE property_subtype IS NOT NULL AND property_subtype <> '' LIMIT 1").fetchone()
mm.close()
assert rw, "主库 properties 表无可用房源（property_subtype 空）"
no_main = rw["property_no"]
subtype_main = rw["property_subtype"]
cat_main = app_mod._normalize_kind(subtype_main)
print("[*] 主库测试番号:", no_main, "｜property_subtype:", subtype_main, "｜大类:", cat_main)
assert cat_main, "该房源 property_subtype 无法归一为 REINS 大类（映射缺口）"

aiconn = sqlite3.connect(REAL_AI_DB)
row = aiconn.execute("SELECT property_no, structure_json FROM ai_structure LIMIT 1").fetchone()
aiconn.close()
assert row, "AI 库 ai_structure 表为空"
no = row[0]
# 取该房源一个真实存在的字段名（edit_field 校验字段必须存在）
_st = json.loads(row[1] or "{}")
_col = None
for _g in (_st.get("groups") or []):
    if _g.get("fields"):
        _col = _g["fields"][0]["col"]
        break
assert _col, "该房源无可用字段"
print("[*] AI 库测试番号:", no, "｜真实字段:", _col)

tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
shutil.copy2(REAL_AI_DB, tmp)
orig_store = app_mod.AI_STORE
app_mod.AI_STORE = AIStructureStore(tmp)

# 备份 config.yaml（设置接口会写回，finally 还原，零污染）
cfg_bak = CFG_PATH + ".diag_bak"
shutil.copy2(CFG_PATH, cfg_bak)

try:
    app_mod.app.config["TESTING"] = True
    app_mod.app.config["SECRET_KEY"] = "diag"
    c = app_mod.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid  # 真实员工登录态，绕过守卫且 _current_user 有效

    # 1) 路由重命名
    r_old = c.get(f"/api/ai-structure/{no}")
    r_new = c.get(f"/api/ai-interpret/{no}")
    print(f"[1] 旧路由 /api/ai-structure 状态 = {r_old.status_code}（期望 404，确证已移除）")
    print(f"[1] 新路由 /api/ai-interpret 状态 = {r_new.status_code}（期望 200）")
    assert r_old.status_code == 404, "旧路由未移除！"
    assert r_new.status_code == 200 and r_new.get_json().get("ok"), "新路由不通！"

    # 2) editor 留痕（已登录真实员工，用真实存在的字段名）
    r = c.post(f"/api/ai-interpret/{no}/edit", json={"col": _col, "value": "99.99"})
    print(f"[2] POST edit 状态 = {r.status_code} body = {r.get_json()}")
    db = sqlite3.connect(tmp)
    db.row_factory = sqlite3.Row
    erow = db.execute(
        "SELECT editor FROM ai_edit_log WHERE property_no=? AND col_name=? "
        "ORDER BY id DESC LIMIT 1", (no, _col)
    ).fetchone()
    db.close()
    editor = erow["editor"] if erow else None
    print(f"[2] ai_edit_log.editor = {editor!r}（期望 {uid!r}，修复前为空串）")
    assert editor == uid, "editor 留痕修复失败！"

    # 3) 详情页含「AI 解读」
    html = c.get(f"/p/{no}").data.decode("utf-8", "replace")
    has_new = "AI 解读" in html
    has_old = "AI 结构" in html
    print(f"[3] 详情页含「AI 解读」= {has_new}；含「AI 结构」= {has_old}（期望 新 True / 旧 False）")
    assert has_new and not has_old, "详情页重命名未生效！"

    # 4) 设置项 /api/ai/settings 持久化 read_kinds
    r_set = c.post("/api/ai/settings", json={"read_kinds": ["売マンション"]})
    j_set = r_set.get_json()
    print(f"[4] POST /api/ai/settings 状态 = {r_set.status_code} body = {j_set}")
    assert r_set.status_code == 200 and j_set.get("status") == "ok", "设置接口失败！"
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        saved = f.read()
    assert "ai_read_scope" in saved and "売マンション" in saved, "config.yaml 未写入 ai_read_scope！"
    print("[4] config.yaml 已写入 ai_read_scope（设置项持久化 OK）")

    # 5) read_kinds 过滤：用不存在的種目拦截任意主库房源 → 期望 400 + 未启用提示
    c.post("/api/ai/settings", json={"read_kinds": ["__无此種目__"]})
    r_rej = c.post(f"/api/ai/{no_main}/run", json={})
    j_rej = r_rej.get_json() or {}
    print(f"[5] 非勾选種目 ai-run 状态 = {r_rej.status_code} body = {j_rej}")
    assert r_rej.status_code == 400 and "未启用 AI 读取" in j_rej.get("error", ""), "read_kinds 拦截失败！"
    # 接受侧：勾选该房源真实大类 → 不应被 read_kinds 拦截（可能 404 无 PDF / 200 已跑，但绝不回 read_kinds 400）
    c.post("/api/ai/settings", json={"read_kinds": [cat_main]})
    r_acc = c.post(f"/api/ai/{no_main}/run", json={})
    j_acc = r_acc.get_json() or {}
    print(f"[5] 勾选種目 ai-run 状态 = {r_acc.status_code} body = {j_acc}")
    assert not (r_acc.status_code == 400 and "未启用 AI 读取" in j_acc.get("error", "")), "read_kinds 放行失败！"
    print("[5] read_kinds 过滤双向 OK（未勾选拦截 / 勾选放行）")

    print("\n✅ 全部诊断通过：重命名 + editor 留痕 + 设置项 read_kinds 均生效，且未污染真实数据。")
finally:
    app_mod.AI_STORE = orig_store
    try:
        os.unlink(tmp)
    except Exception:
        pass
    # 还原 config.yaml（消除设置接口写回，零污染）
    try:
        if os.path.exists(cfg_bak):
            shutil.move(cfg_bak, CFG_PATH)
    except Exception:
        print("[!] 警告：config.yaml 还原失败，请手动检查", CFG_PATH)
