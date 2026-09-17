# -*- coding: utf-8 -*-
"""离线演示数据源（DEMO 模式）。

为什么要它：真实抓取需要 REINS 会员账号 + 手工登录，第一次跑之前
你无法确认"取数 → 落库 → 落盘 → 查询"这条链路是否通。
DEMO 模式用本地生成的样例数据把同一条链路跑一遍，
让你先验证"东西到底有没有落到我本地"。

它产出的字段、落盘方式、查询方式，与真实模式**完全一致**。
"""
from __future__ import annotations

import random
from datetime import datetime

from .pipeline import pseudo_pdf

WARDS = ["北区", "都島区", "福島区", "中央区", "西区", "港区", "大正区", "天王寺区",
         "浪速区", "西淀川区", "淀川区", "東淀川区", "東成区", "生野区", "旭区",
         "城東区", "阿倍野区", "住吉区", "東住吉区", "西成区", "鶴見区", "住之江区", "平野区"]

LINES = [("大阪メトロ御堂筋線", "本町"), ("大阪メトロ四つ橋線", "本町"),
         ("JR大阪環状線", "天王寺"), ("近鉄奈良線", "大阪難波"),
         ("大阪メトロ谷町線", "谷町四丁目"), ("阪急京都線", "十三"),
         ("南海本線", "難波"), ("JR大阪東線", "新大阪"), ("大阪メトロ中央線", "森ノ宮")]

BUILDINGS = ["ザ・セントラルマークタワー", "新大阪コーポビアネーズ", "グランドメゾン本町",
             "リバーガーデン大阪", "シティタワー天王寺", "ブリリアタワー中之島",
             "プレサンス堺筋本町", "ローレルタワー難波", "ジオ阿倍野", "エステムコート谷町"]

SUBTYPES = [("売マンション", "中古マンション", 0.62), ("売マンション", "新築マンション", 0.08),
            ("売一戸建", "中古戸建", 0.18), ("売一戸建", "新築戸建", 0.05),
            ("売土地", "土地", 0.07)]

LAYOUTS = ["1R", "1K", "1LDK", "2LDK", "3LDK", "4LDK", "3-05"]


def _weighted_subtype() -> tuple[str, str]:
    r, acc = random.random(), 0.0
    for kind, st, w in SUBTYPES:
        acc += w
        if r <= acc:
            return kind, st
    return SUBTYPES[0][0], SUBTYPES[0][1]


def _one_new(i: int) -> dict:
    kind, subtype = _weighted_subtype()
    ward = random.choice(WARDS)
    line, station = random.choice(LINES)
    chome = random.randint(1, 6)
    banchi = random.randint(1, 25)
    no = f"3001407{i:05d}"

    if subtype == "土地":
        price = random.randint(1800, 9800) * 10000
        land = round(random.uniform(80, 320), 2)
        ex = None
    elif kind == "売一戸建":
        price = random.randint(2200, 9800) * 10000
        land = round(random.uniform(60, 200), 2)
        ex = None
    else:
        price = random.randint(1800, 15800) * 10000
        land = None
        ex = round(random.uniform(25, 95), 2)

    area_for_unit = ex or land or 50
    return {
        "property_no": no,
        "kind": kind,
        "property_subtype": subtype,
        "ward": ward,
        "address": f"大阪府大阪市{ward}{chome}丁目{banchi}番",
        "building_name": random.choice(BUILDINGS) if kind == "売マンション" else "",
        "line_station": f"{line} {station}",
        "price": price,
        "previous_price": None,
        "land_area": land,
        "exclusive_area": ex,
        "building_area": None if kind == "売マンション" else round(random.uniform(70, 180), 2),
        "unit_price_sqm": int(price / area_for_unit) if area_for_unit else None,
        "unit_price_tsubo": int(price / area_for_unit * 3.30578) if area_for_unit else None,
        "built_year_month": f"{random.randint(1995, 2025)}年{random.randint(1,12)}月",
        "layout": random.choice(LAYOUTS) if kind != "売土地" else "",
        "floor": f"{random.randint(1, 28)}階" if kind == "売マンション" else "",
        "above_ground_floors": f"地上{random.randint(3, 40)}階" if kind == "売マンション" else "",
        "image_count": random.choice([0, 0, 3, 5, 8, 12, 20]),
        "registration_date": datetime.now().strftime("%Y%m%d"),
        "change_date": "",
        "pdf_bytes": pseudo_pdf(f"REINS 図面 | {no} | {kind}/{subtype} | {ward}"
                                f" | {price//10000}万円"),
    }


def _one_change(old) -> dict:
    """把库里已有的一条房源"改价"，模拟降价（用于验证变更/降价识别）。"""
    price = int(old["price"] or 0)
    new_price = max(int(price * random.uniform(0.90, 0.97)), 100 * 10000)
    return {
        "property_no": old["property_no"],
        "kind": old["kind"],
        "property_subtype": old["property_subtype"],
        "ward": old["ward"],
        "address": old["address"],
        "building_name": old["building_name"],
        "line_station": old["line_station"],
        "price": new_price,
        "previous_price": price,
        "land_area": old["land_area"],
        "exclusive_area": old["exclusive_area"],
        "building_area": old["building_area"],
        "unit_price_sqm": old["unit_price_sqm"],
        "unit_price_tsubo": old["unit_price_tsubo"],
        "built_year_month": old["built_year_month"],
        "layout": old["layout"],
        "floor": old["floor"],
        "above_ground_floors": old["above_ground_floors"],
        "image_count": old["image_count"],
        "registration_date": old["registration_date"],
        "change_date": datetime.now().strftime("%Y%m%d"),
        "pdf_bytes": pseudo_pdf(f"REINS 図面(変更) | {old['property_no']}"
                                f" | {new_price//10000}万円"),
    }


def generate(store, cfg: dict, scale: float = 0.25) -> list[dict]:
    """产出"这一轮"的样例数据。scale 用来把量调小，方便验证。"""
    d = cfg.get("demo", {}) or {}
    n_new = max(1, int((d.get("daily_new", 60)) * scale))
    n_chg = max(0, int((d.get("daily_change", 18)) * scale))

    items = [_one_new(random.randint(10000, 99999)) for _ in range(n_new)]

    # 变更：优先拿库里已有的房源改价；库太空就跳过
    existing = list(store.conn.execute(
        "SELECT * FROM properties ORDER BY RANDOM() LIMIT ?", (n_chg,)))
    for row in existing:
        items.append(_one_change(row))

    random.shuffle(items)
    return items
