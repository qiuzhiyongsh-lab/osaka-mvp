# -*- coding: utf-8 -*-
"""
QA 离线验证：v1.6.3 检索条件横幅 (_log_search_banner)

勇哥要求：测试经理多来几轮、多增加几个条件验证显示合适。
本脚本不登录 REINS，直接驱动真实函数 _log_search_banner，覆盖：
  · 6 种目（新築/中古 戸建·マンション·タウン）
  · 区域模式：直录 / 保存条件套用
  · 日期轴：登録年月日 / 変更年月日 / 双轴 / 全期間(无)
  · 日期范围：当日 / 指定日 / 全期間
  · 轮次：主轮 / 指定日期下载 / 当日日期同步
对每个组合捕获输出并断言关键字段正确，最后打印样本横幅供肉眼核对。
"""
import io
import sys
import traceback

ROOT = r"C:\Users\25374\WorkBuddy\2026-09-11-09-50-22\osaka-mvp"
sys.path.insert(0, ROOT)
import core.crawler as C  # noqa: E402


def run_case(case):
    """跑一个组合，返回 (captured_lines, error)。"""
    captured = []
    log = captured.append
    try:
        C._log_search_banner(
            log,
            seq=case.get("seq"), total=case.get("total"),
            kind=case.get("kind", ""), subtypes=case.get("subtypes", []),
            cfg=case.get("cfg"), date_fields=case.get("date_fields"),
            date_range=case.get("date_range", ""), round_tag=case.get("round_tag", ""),
        )
        return captured, None
    except Exception as e:  # noqa: BLE001
        return captured, traceback.format_exc() + "\n" + repr(e)


def find(lines, prefix):
    for ln in lines:
        if ln.strip().startswith("·") and prefix in ln:
            return ln
    return None


def assert_case(name, case, expects):
    lines, err = run_case(case)
    if err:
        print("❌ %s — 抛异常:\n%s" % (name, err))
        return False
    ok = True
    for field, want in expects.items():
        ln = find(lines, field)
        if ln is None:
            print("❌ %s — 缺少字段行: %s" % (name, field))
            ok = False
            continue
        if want not in ln:
            print("❌ %s — %s 期望含「%s」实际: %s" % (name, field, want, ln))
            ok = False
    if ok:
        print("✅ %s" % name)
    return ok


def main():
    print("=" * 64)
    print("QA · v1.6.3 检索条件横幅 多组合验证")
    print("=" * 64)

    cfg_direct = {"search": {"use_saved_condition": False}}
    cfg_saved = {"search": {"use_saved_condition": True}}

    cases = []
    # —— 主轮 6 种目（全期間，直录）——
    for i, sub in enumerate(
            ["新築戸建", "中古戸建", "新築マンション", "中古マンション", "新築タウン", "中古タウン"], 1):
        cases.append(("主轮-%s" % sub, {
            "seq": i, "total": 6, "kind": "売一戸建" if "戸建" in sub else (
                "売マンション" if "マンション" in sub else "売タウン"),
            "subtypes": [sub], "cfg": cfg_direct,
            "date_fields": [], "date_range": "全期間（不限日期）",
            "round_tag": "主轮(全期間安全网)"},
            {"房屋类型": sub, "新築/中古": ("新築" if sub.startswith("新築") else "中古"),
             "区域": "大阪府・大阪市", "直录": "直录", "日期轴": "未指定 = 全期間",
             "日期范围": "全期間（不限日期）", "轮次": "主轮"}))

    # —— 指定日期下载：登録/変更/双轴 × 当日/指定日 ——
    combo = [
        ("登録+当日", ["登録年月日"], "指定日 2026-09-16"),
        ("変更+当日", ["変更年月日"], "指定日 2026-09-16"),
        ("登録+指定日", ["登録年月日"], "指定日 2026-09-10"),
        ("双轴+全期間", ["登録年月日", "変更年月日"], "全期間（登録/変更 两轴分两次并集）"),
    ]
    for i, (nm, df, dr) in enumerate(combo, 1):
        cases.append(("指定下载-%s" % nm, {
            "seq": "1/6 组 · %d/4 日期轮" % i, "kind": "売マンション",
            "subtypes": ["新築マンション", "中古マンション"], "cfg": cfg_direct,
            "date_fields": df, "date_range": dr, "round_tag": "指定日期下载"},
            {"房屋类型": "新築マンション·中古マンション", "新築/中古": "新築+中古",
             "区域": "大阪府・大阪市", "日期轴": "/".join(df), "日期范围": dr,
             "轮次": "指定日期下载"}))

    # —— 当日同步：登録/変更 各一轮 ——
    for i, fld in enumerate(["登録年月日", "変更年月日"], 1):
        cases.append(("当日同步-%s" % fld, {
            "seq": i, "total": 2, "kind": "売一戸建",
            "subtypes": ["新築戸建", "中古戸建"], "cfg": cfg_direct,
            "date_fields": [fld], "date_range": "当日 2026-09-16",
            "round_tag": "当日日期同步"},
            {"房屋类型": "新築戸建·中古戸建", "新築/中古": "新築+中古",
             "区域": "大阪府・大阪市", "日期轴": fld, "日期范围": "当日 2026-09-16",
             "轮次": "当日日期同步"}))

    # —— 区域模式对比：保存条件套用 ——
    cases.append(("保存条件模式-中古マンション", {
        "seq": 1, "total": 1, "kind": "売マンション", "subtypes": ["中古マンション"],
        "cfg": cfg_saved, "date_fields": [], "date_range": "全期間（不限日期）",
        "round_tag": "主轮(全期間安全网)"},
        {"房屋类型": "中古マンション", "新築/中古": "中古", "区域": "大阪府・大阪市",
         "保存条件套用": "保存条件套用", "日期轴": "未指定 = 全期間"}))

    passed = failed = 0
    for name, case, expects in cases:
        if assert_case(name, case, expects):
            passed += 1
        else:
            failed += 1

    print()
    print("=" * 64)
    print("断言结果: %d 通过 / %d 失败" % (passed, failed))
    print("=" * 64)

    # —— 打印 3 个样本横幅供肉眼核对（不同轮次/条件）——
    print("\n【样本横幅 1】主轮 新築戸建（全期間·直录）")
    lines, _ = run_case(cases[0][1])
    print("\n".join(lines))
    print("\n【样本横幅 2】指定下载 双轴+全期間（新築+中古 マンション）")
    lines, _ = run_case([c for c in cases if c[0] == "指定下载-双轴+全期間"][0][1])
    print("\n".join(lines))
    print("\n【样本横幅 3】当日同步 変更年月日（新築+中古 戸建）")
    lines, _ = run_case([c for c in cases if c[0] == "当日同步-変更年月日"][0][1])
    print("\n".join(lines))

    # —— v1.6.4：下载结束「条件 + 结果」汇总框 渲染验证 ——
    def qa_summary():
        cap = []
        C._log_session_summary(
            cap.append, "指定日期下载结束",
            ["種目组：6 组（売一戸建×[新築戸建,中古戸建] ｜ …）",
             "日期轴：登録年月日／変更年月日（每轴分次检索，合并=按番号并集）",
             "日期范围：全期間（登録/変更 两轴分两次并集）",
             "区域：大阪府・大阪市（直录）"],
            [{"tag": "売一戸建×[新築戸建,中古戸建] / 登録年月日", "online": 50,
              "downloaded": 48},
             {"tag": "売一戸建×[新築戸建,中古戸建] / 変更年月日", "online": 30,
              "downloaded": 30}],
            {"fetched": 78, "new": 5, "changed": 10, "pdf_saved": 20},
            online_total=80,
        )
        text = "\n".join(cap)
        checks = {
            "【下载条件】": "下载条件小节",
            "【下载结果】": "下载结果小节",
            "【逐条件结果】": "逐条件结果小节",
            "线上报告合计 80 件": "线上合计",
            "本地落库 78 条（新盘 5 / 变更 10）/ PDF 20 份": "落库明细",
            "1. 売一戸建×[新築戸建,中古戸建] / 登録年月日 → 线上 50 / 下载 48": "逐条件行1",
        }
        ok = True
        for want, desc in checks.items():
            if want not in text:
                print("❌ 汇总框 缺少: %s" % desc)
                ok = False
        print("✅ v1.6.4 汇总框渲染正确（条件/结果/逐条件三小节齐全）"
              if ok else "❌ v1.6.4 汇总框渲染异常")
        print("---- 汇总框样本 ----")
        print(text)
        return ok
    s_ok = qa_summary()

    print("\nQA 结论: %s" % ("全部通过 ✅" if (failed == 0 and s_ok) else "存在失败 ❌"))
    return failed == 0 and s_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
