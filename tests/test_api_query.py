# -*- coding: utf-8 -*-
"""
v1.7.4 · /api/query **端点级**集成测试（补上一层此前完全没有的测试）

【为什么要这一层】
先前所有筛选测试都直接调 `Store.search()`，而**没人测中间的路由参数归一化**。
2026-09-17 勇哥报告「软件一点数据都查不出来」——选了 09-11~09-17 区间却是 0 条，
根因正在那一层：app.py 先把 f["date"] 清空以避免双重过滤，
紧接着「默认填今天」的分支又把它填回来，于是「区间 AND 单日=今天」同时生效，
今天还没数据时就是 0 条；而摘要行还显示「日期 2026-09-17」——用户根本看不出
自己的区间被吞了。

这类 bug 的特征：**下层函数是对的，错在调用它之前的那几行**。
只测下层必然漏。所以这里一律**走真实 HTTP 端点**，并与 store 层对拍。

【断言策略】不写死具体条数（数据每天会变），而是：
    端点结果  ==  直接调 Store.search(同参数) 的结果
任一侧错位（叠加日期 / 吞参数 / 口径不对）都会立刻不等。
"""
import pytest


@pytest.fixture(scope="module")
def client(webapp):
    return webapp.app.test_client()


@pytest.fixture(scope="module")
def store(webapp, real_db_copy):
    """与同一个副本库对应的 Store（用于端点 vs 直调 对拍）"""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for p in (str(root), str(root / "core")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from core.store import Store

    s = Store(str(real_db_copy))
    yield s
    s.close()


def api_total(client, qs: str) -> int:
    r = client.get("/api/query?limit=1&" + qs)
    assert r.status_code == 200, "端点未返回 200：%s" % r.status_code
    return int(r.get_json()["total"])


def db_total(store, **kw) -> int:
    _rows, total = store.search(kw, limit=1, offset=0)
    return int(total)


# ---------------------------------------------------------------- 日期区间

def test_range_not_overridden_by_default_today(client, store):
    """★ 核心回归：区间模式下不得叠加「默认今天」，否则今天的量会把区间挤成 0。"""
    got = api_total(client, "date_from=2026-09-11&date_to=2026-09-17&date_caliber=any")
    want = db_total(store, date_from="2026-09-11", date_to="2026-09-17", date_caliber="any")
    assert got == want, (
        "端点返回 %d，直调 store 返回 %d —— 端点多半又叠加了 date=今天" % (got, want)
    )
    # 且必须比「今天」多：今天没数据时这条最能暴露问题（历史上就是 0 vs 1312）
    today = api_total(client, "")
    assert got >= today, "区间结果(%d) 不应小于单日今天(%d)" % (got, today)


def test_range_has_real_data(client, store):
    """区间必须真查出东西，防止「0 条也算通过」。"""
    got = api_total(client, "date_from=2026-09-11&date_to=2026-09-17&date_caliber=any")
    all_rows = db_total(store)
    assert got > 0, "09-11~09-17 应有数据，实际 0 条"
    assert got <= all_rows


def test_only_from_is_open_range(client, store):
    """只填起 = 开区间（PRD §3.3 #6），不能退化成等值单日。"""
    got = api_total(client, "date_from=2026-09-11&date_caliber=any")
    want = db_total(store, date_from="2026-09-11", date_caliber="any")
    assert got == want and got > 0


def test_caliber_is_honored_in_range_mode(client, store):
    """date_caliber 必须在区间模式下也生效（旧版只在单日分支传，导致静默错口径）。"""
    for cal in ("any", "registration", "change", "download"):
        got = api_total(
            client,
            "date_from=2026-09-14&date_to=2026-09-16&date_caliber=%s" % cal,
        )
        want = db_total(
            store, date_from="2026-09-14", date_to="2026-09-16", date_caliber=cal
        )
        assert got == want, "口径 %s：端点 %d vs 直调 %d" % (cal, got, want)


def test_date_all_means_no_filter(client, store):
    """date=all = 不按日期过滤（概览 KPI 点进来用）。"""
    got = api_total(client, "date=all")
    want = db_total(store)
    assert got == want and got > 0


def test_no_date_defaults_to_today(client):
    """不传任何日期 → 退回「今天」单日（老习惯，保持）。"""
    j = client.get("/api/query?limit=1").get_json()
    assert j["date"], "不传日期时应回填今天，实际为空"
    assert len(j["date"]) == 10


def test_reversed_range(client, store):
    """起 > 止：后端返回 0（前端负责自动交换，后端不该崩也不该误捞）。"""
    got = api_total(client, "date_from=2026-09-17&date_to=2026-09-11&date_caliber=any")
    want = db_total(store, date_from="2026-09-17", date_to="2026-09-11", date_caliber="any")
    assert got == want


# ---------------------------------------------------------------- R8 取引態様

def test_trade_type_via_endpoint(client, store):
    """R8：取引態様筛选要走通端点（不只是 store 层能用）。"""
    got = api_total(client, "trade_type=%E5%A3%B2%E4%B8%BB&date=all")  # 売主
    want = db_total(store, trade_type="売主")
    assert got == want and got > 0


def test_trade_type_combines_with_range(client, store):
    """R8 + 区间 叠加（不能互相覆盖）。"""
    got = api_total(
        client, "trade_type=%E5%A3%B2%E4%B8%BB&date_from=2026-09-11&date_to=2026-09-17"
        "&date_caliber=any"
    )
    want = db_total(
        store,
        trade_type="売主",
        date_from="2026-09-11",
        date_to="2026-09-17",
        date_caliber="any",
    )
    assert got == want


def test_rows_carry_trade_type(client):
    """列表行必须带 trade_type —— 前端「売主」徽章的数据源；没有它徽章永远不显示。"""
    j = client.get("/api/query?limit=20&date=all").get_json()
    rows = j.get("rows") or []
    assert rows, "全库查询应有行"
    hit = [r for r in rows if r.get("trade_type")]
    assert hit, "返回的 20 行里没有一行带 trade_type —— 徽章会永远不显示"
    # 多行枚举必须只取第一行（否则徽章会撑破行）
    for r in hit:
        assert "\n" not in (r.get("trade_type") or ""), "trade_type 含换行，未做首行截断"


# ---------------------------------------------------------------- v1.7.6 多选 OR（URL 重复参数）

def test_ward_multi_via_url(client, store):
    """区 多选：URL 重复 ?ward=a&ward=b → 端点收成 OR 并集，与直调 store 对拍。"""
    # 取真实库实际出现的前两个区，避免写死
    con = store.conn
    ws = [r[0] for r in con.execute(
        "SELECT DISTINCT ward FROM properties WHERE is_active=1 AND ward IS NOT NULL AND ward<>'' "
        "ORDER BY ward LIMIT 2")]
    if len(ws) < 2:
        pytest.skip("真实库区样本不足")
    got = api_total(client, "date=all&" + "&".join("ward=%s" % w for w in ws))
    want = db_total(store, wards=ws)
    assert got == want and got > 0


def test_trade_type_multi_via_url(client, store):
    """取引態様 多选：URL 重复 ?trade_type=売主&trade_type=専任 → OR 并集。"""
    got = api_total(
        client,
        "date=all&trade_type=%E5%A3%B2%E4%B8%BB&trade_type=%E5%B0%82%E4%BB%BB",  # 売主&専任
    )
    want = db_total(store, trade_types=["売主", "専任"])
    assert got == want and got > 0


def test_ward_trade_type_url_and_combine(client, store):
    """区 与 取引態様 两组维度之间必须 AND（不同维度互不吞）。"""
    con = store.conn
    w = con.execute(
        "SELECT ward FROM properties WHERE is_active=1 AND ward IS NOT NULL AND ward<>'' LIMIT 1"
    ).fetchone()[0]
    got = api_total(client, "date=all&ward=" + w + "&trade_type=%E5%A3%B2%E4%B8%BB")
    want = db_total(store, wards=[w], trade_types=["売主"])
    assert got == want
