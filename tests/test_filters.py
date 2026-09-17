# -*- coding: utf-8 -*-
"""
v1.7.4 筛选回归测试：日期区间（起~止）+ 取引態様（R8）

重点锁死两类「静默错」——它们不报错、不崩，只是**查出来的数不对**，
是最难发现也最伤客户信任的那一类：

  A. 日期区间：
     ① any 口径必须是「任一日落在区间内」= (reg BETWEEN) OR (chg BETWEEN)
        （PRD 11 §3.5 不变量 2），不能写成跨字段 AND。
     ② **参数顺序**必须与占位符顺序一致。初版把 args 按 [起,起,止,止] 追加，
        而占位符顺序是 reg起,reg止,chg起,chg止 → 实际筛成 reg∈[起,起]，
        869 条只剩 344 条。本文件用「与手写正确 SQL 对拍」锁死它。
     ③ download（下载日）口径在区间模式下必须按 last_seen_at 筛，
        旧版 range 分支漏了这一档，静默退化成 any。
     ④ 只填一端 = 开区间（PRD §3.3 #6）。

  B. 取引態様（R8）：值在 detail_json 里、且是多行枚举，必须包含匹配（LIKE），
     精确等值会漏掉「売主\nオーナーチェンジ」这类变体。

只读真实库**副本**（conftest.real_db_copy），不碰真库、不监听端口。
"""
import sqlite3

import pytest


@pytest.fixture(scope="module")
def rstore(real_db_copy):
    """指向真实库副本的 Store"""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for p in (str(root), str(root / "core"), str(root / "web")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from core.store import Store

    s = Store(str(real_db_copy))
    yield s
    s.close()


@pytest.fixture(scope="module")
def span(rstore):
    """真实库里最近 3 个登録日 → (起, 止)"""
    con = sqlite3.connect(rstore.conn.execute("PRAGMA database_list").fetchone()[2])
    ds = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT reg_date_iso FROM properties "
            "WHERE is_active=1 AND reg_date_iso IS NOT NULL AND reg_date_iso<>'' "
            "ORDER BY reg_date_iso DESC LIMIT 3"
        ).fetchall()
    ]
    con.close()
    if len(ds) < 2:
        pytest.skip("真实库日期样本不足")
    return min(ds), max(ds)


def _total(store, **kw):
    _rows, total = store.search(kw, limit=1, offset=0)
    return int(total)


def _sql_count(dbpath, sql, args=()):
    con = sqlite3.connect(dbpath)
    try:
        return con.execute(sql, args).fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------- A. 日期区间

def test_any_range_equals_or_semantics(rstore, span, real_db_copy):
    """any = 任一日落在区间内。与手写正确 SQL 对拍（锁死参数顺序坑）。"""
    df, dt = span
    got = _total(rstore, date_from=df, date_to=dt, date_caliber="any")
    expect = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND ("
        "(reg_date_iso IS NOT NULL AND reg_date_iso<>'' AND reg_date_iso BETWEEN ? AND ?)"
        " OR "
        "(chg_date_iso IS NOT NULL AND chg_date_iso<>'' AND chg_date_iso BETWEEN ? AND ?))",
        (df, dt, df, dt),
    )
    assert got == expect, (
        "any 区间应对齐「任一日落在区间内」，实得 %d / 期望 %d（参数顺序错位会让结果锐减）"
        % (got, expect)
    )
    assert got > 0, "区间内应有数据，0 条说明筛选条件拼错了"


def test_registration_range(rstore, span, real_db_copy):
    df, dt = span
    got = _total(rstore, date_from=df, date_to=dt, date_caliber="registration")
    expect = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 "
        "AND reg_date_iso IS NOT NULL AND reg_date_iso<>'' AND reg_date_iso BETWEEN ? AND ?",
        (df, dt),
    )
    assert got == expect


def test_change_range(rstore, span, real_db_copy):
    df, dt = span
    got = _total(rstore, date_from=df, date_to=dt, date_caliber="change")
    expect = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 "
        "AND chg_date_iso IS NOT NULL AND chg_date_iso<>'' AND chg_date_iso BETWEEN ? AND ?",
        (df, dt),
    )
    assert got == expect


def test_download_range_uses_last_seen(rstore, span, real_db_copy):
    """download 口径必须按 last_seen_at 筛 —— 旧版在 range 分支漏了它，静默退化成 any。"""
    df, dt = span
    got = _total(rstore, date_from=df, date_to=dt, date_caliber="download")
    any_n = _total(rstore, date_from=df, date_to=dt, date_caliber="any")
    expect = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND "
        "substr(COALESCE(last_seen_at, first_seen_at),1,10) BETWEEN ? AND ?",
        (df, dt),
    )
    assert got == expect, "download 区间应按 last_seen_at 筛，实得 %d / 期望 %d" % (got, expect)
    # 回归铁证：不能与 any 相同（相同就说明又退化成 any 了）
    assert got != any_n or expect == any_n, (
        "download 区间结果不应与 any 完全相同（相同 = 又退化成 any）"
    )


def test_one_sided_range_is_open(rstore, span, real_db_copy):
    """只填一端 = 开区间（PRD §3.3 #6），不能退化成等值单日。"""
    df, dt = span
    only_from = _total(rstore, date_from=df, date_caliber="any")
    both = _total(rstore, date_from=df, date_to=dt, date_caliber="any")
    expect_from = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND ("
        "(reg_date_iso IS NOT NULL AND reg_date_iso<>'' AND reg_date_iso >= ?)"
        " OR "
        "(chg_date_iso IS NOT NULL AND chg_date_iso<>'' AND chg_date_iso >= ?))",
        (df, df),
    )
    assert only_from == expect_from
    assert only_from >= both, "只填起（开区间）应 >= 两端都填"


def test_reversed_range_returns_zero(rstore, span):
    """起 > 止：后端返回 0 条（前端负责自动交换，这里只保证不崩、不误捞）。"""
    df, dt = span
    got = _total(rstore, date_from=dt, date_to=df, date_caliber="any")
    assert got == 0


# ---------------------------------------------------------------- B. 取引態様

def test_trade_type_seller_includes_variants(rstore, real_db_copy):
    """売主 必须包含「売主\nオーナーチェンジ」变体（精确等值会漏 103 条）。"""
    got = _total(rstore, trade_type="売主")
    expect = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND "
        "json_extract(detail_json,'$.trade_type') LIKE ?",
        ("%売主%",),
    )
    strict = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND "
        "json_extract(detail_json,'$.trade_type') = ?",
        ("売主",),
    )
    assert got == expect
    assert got >= strict, "包含匹配的结果不应少于精确等值"
    assert got > 0


def test_trade_type_empty_means_no_filter(rstore):
    """不传 trade_type = 不筛选（不能把「无该字段」的房源全过滤掉）。"""
    no_filter = _total(rstore)
    explicit_empty = _total(rstore, trade_type="")
    assert no_filter == explicit_empty


def test_trade_type_filters(rstore, real_db_copy):
    """各態様筛选结果都应与 LIKE 语义一致，且互不串味。"""
    for val in ("売主", "専任", "専属", "代理", "一般"):
        got = _total(rstore, trade_type=val)
        expect = _sql_count(
            str(real_db_copy),
            "SELECT COUNT(*) FROM properties WHERE is_active=1 AND "
            "json_extract(detail_json,'$.trade_type') LIKE ?",
            ("%" + val + "%",),
        )
        assert got == expect, "取引態様 %s 实得 %d / 期望 %d" % (val, got, expect)
        assert got > 0, "真实库里应有 %s 的房源" % val


def test_trade_type_combines_with_date(rstore, span, real_db_copy):
    """取引態様 + 日期区间要能叠加（AND），不能互相覆盖。"""
    df, dt = span
    got = _total(rstore, trade_type="売主", date_from=df, date_to=dt, date_caliber="any")
    expect = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND "
        "json_extract(detail_json,'$.trade_type') LIKE ? AND ("
        "(reg_date_iso IS NOT NULL AND reg_date_iso<>'' AND reg_date_iso BETWEEN ? AND ?)"
        " OR "
        "(chg_date_iso IS NOT NULL AND chg_date_iso<>'' AND chg_date_iso BETWEEN ? AND ?))",
        ("%売主%", df, dt, df, dt),
    )
    assert got == expect


# ---------------------------------------------------------------- C. 多选 OR（v1.7.6：区 / 取引態様）

def test_ward_multi_select_or(rstore, real_db_copy):
    """区 多选 = OR 并集，与逐区求和一致（ward 单值互斥，无重叠）。"""
    con = sqlite3.connect(str(real_db_copy))
    real_wards = [r[0] for r in con.execute(
        "SELECT DISTINCT ward FROM properties WHERE is_active=1 AND ward IS NOT NULL AND ward<>'' "
        "ORDER BY ward LIMIT 3")]
    con.close()
    if len(real_wards) < 2:
        pytest.skip("真实库区样本不足")
    w = real_wards[:2]
    got = _total(rstore, wards=w)
    expect = sum(_total(rstore, wards=[x]) for x in w)
    assert got == expect, "区多选 OR 应等于逐区求和，实得 %d / 期望 %d" % (got, expect)
    assert got > 0


def test_ward_single_value_back_compat(rstore, real_db_copy):
    """单值 ward= 仍兼容（退回成单元素 IN）。"""
    con = sqlite3.connect(str(real_db_copy))
    w = con.execute(
        "SELECT ward FROM properties WHERE is_active=1 AND ward IS NOT NULL AND ward<>'' LIMIT 1"
    ).fetchone()[0]
    con.close()
    got = _total(rstore, ward=w)
    expect = _total(rstore, wards=[w])
    assert got == expect == _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND ward=?", (w,))


def test_trade_type_multi_select_or(rstore, real_db_copy):
    """取引態様 多选 = OR 并集，与手写并集 SQL 对拍。"""
    vals = ["売主", "専任"]
    got = _total(rstore, trade_types=vals)
    union = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND ("
        + " OR ".join("json_extract(detail_json,'$.trade_type') LIKE ?" for _ in vals) + ")",
        tuple("%" + v + "%" for v in vals),
    )
    per = [_sql_count(str(real_db_copy),
            "SELECT COUNT(*) FROM properties WHERE is_active=1 AND "
            "json_extract(detail_json,'$.trade_type') LIKE ?", ("%" + v + "%",)) for v in vals]
    assert got == union, "取引態様多选 OR 应等于并集，实得 %d / 期望 %d" % (got, union)
    assert got >= max(per), "并集不应小于任一单值"
    assert got > 0


def test_trade_type_single_value_back_compat(rstore, real_db_copy):
    """单值 trade_type= 仍兼容（退回成单元素 OR）。"""
    got = _total(rstore, trade_type="売主")
    expect = _total(rstore, trade_types=["売主"])
    assert got == expect
    assert got > 0


def test_ward_and_trade_type_combine_and(rstore, real_db_copy):
    """区 与 取引態様 两组之间必须 AND（不同维度），不能互相吞。"""
    con = sqlite3.connect(str(real_db_copy))
    w = con.execute(
        "SELECT ward FROM properties WHERE is_active=1 AND ward IS NOT NULL AND ward<>'' LIMIT 1"
    ).fetchone()[0]
    con.close()
    got = _total(rstore, wards=[w], trade_types=["売主"])
    expect = _sql_count(
        str(real_db_copy),
        "SELECT COUNT(*) FROM properties WHERE is_active=1 AND ward=? AND "
        "json_extract(detail_json,'$.trade_type') LIKE ?", (w, "%売主%"))
    assert got == expect
