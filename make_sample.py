# -*- coding: utf-8 -*-
"""화면 확인용 샘플 데이터 생성기.

실제 API 수집(collect.py) 전에 index.html 의 동작을 확인하기 위한 가짜 데이터다.
실제 데이터를 받으면 collect.py 가 같은 경로(data/apt_data.json)를 덮어쓴다.
가격 수준·사이클 모양만 그럴듯하게 흉내낼 뿐 실제 시세가 아니다.

  py -3 make_sample.py
"""
import json
import math
import os
import random
from datetime import date

from complexes import COMPLEXES, REGION_ORDER, SGG_LABEL, TIER_ORDER

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

START, END = 201601, 202607

# 권역별 84㎡ 시작가(만원) 대략치 — 샘플이므로 어림값이다.
BASE_PRICE = {
    "도심권": 78000, "동북권": 45000, "서북권": 58000,
    "서남권": 62000, "동남권": 125000, "경기권": 55000,
}
# 급지가 낮을수록(1급지) 비싸게 — 급지표가 화면에서 드러나도록 배수를 준다
TIER_ADJ = {1: 2.6, 2: 1.9, 3: 1.45, 4: 1.2, 5: 1.0,
            6: 0.88, 7: 0.78, 8: 0.7, 9: 0.62, 10: 0.55}
SGG_ADJ = {
    "강남구": 1.9, "서초구": 1.75, "송파구": 1.25, "강동구": 0.95,
    "용산구": 1.35, "종로구": 1.0, "중구": 0.95, "성동구": 1.15,
    "광진구": 1.0, "마포구": 1.1, "양천구": 1.1, "영등포구": 1.2,
    "동작구": 1.0, "강서구": 0.85, "구로구": 0.75, "금천구": 0.7,
    "관악구": 0.8, "노원구": 0.8, "도봉구": 0.72, "강북구": 0.72,
    "성북구": 0.9, "중랑구": 0.75, "동대문구": 0.9, "서대문구": 0.9,
    "은평구": 0.82,
    "성남분당구": 1.1, "과천시": 1.3, "수원영통구": 0.75, "화성시": 0.62,
    "광명시": 0.8, "안양동안구": 0.8, "용인수지구": 0.68, "하남시": 0.75,
}


def months(a, b):
    out, y, m = [], a // 100, a % 100
    while y * 100 + m <= b:
        out.append(y * 100 + m)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def cycle(i, n):
    """2016~2021 상승 → 2022~2023 조정 → 이후 완만 회복 모양."""
    t = i / n
    up = 1 + 0.95 * (1 / (1 + math.exp(-9 * (t - 0.42))))
    dip = -0.16 * math.exp(-((t - 0.68) ** 2) / 0.006)
    return up + dip


def main():
    yms = months(START, END)
    n = len(yms)
    rnd = random.Random(20260818)
    out = []
    for c in COMPLEXES:
        base84 = (BASE_PRICE[c["region"]] * SGG_ADJ.get(c["sgg"], 1.0)
                  * TIER_ADJ.get(c["tier"], 1.0) * rnd.uniform(0.92, 1.08))
        entry = {k: c[k] for k in
                 ("id", "region", "sgg", "dong", "name", "tier", "area_name", "src")}
        entry["sgg_label"] = SGG_LABEL.get(c["sgg"], c["sgg"])
        by = rnd.choice([1979, 1986, 1994, 2001, 2006, 2013, 2017, 2021, 2024])
        entry["build_year"] = by
        entry["built"] = "%d-%02d" % (by, rnd.randint(1, 12))
        entry["units"] = rnd.choice([517, 866, 1341, 1772, 2529, 3410, 4424, 6864, 9510])
        entry["dong_cnt"] = rnd.randint(5, 40)
        entry["areas"] = {}
        for akey, rep, ratio in (("59", 59.9, 0.72), ("84", 84.9, 1.0)):
            if rnd.random() < 0.12:                    # 해당 평형이 없는 단지
                entry["areas"][akey] = {"rep": None, "trade": [], "rent": [],
                                        "n_trade": 0, "n_rent": 0}
                continue
            lvl = base84 * ratio
            trade, rentv, drift = [], [], 0.0
            for i, ym in enumerate(yms):
                drift += rnd.gauss(0, 0.012)
                p = lvl * cycle(i, n) * (1 + drift)
                if rnd.random() < 0.22:                # 거래 없는 달
                    continue
                cnt = rnd.randint(1, 6)
                med = int(round(p * rnd.uniform(0.97, 1.03) / 100.0) * 100)
                trade.append([ym, med, cnt, int(med * 0.95), int(med * 1.06)])
                jr = 0.62 - 0.18 * (i / n) + rnd.uniform(-0.03, 0.03)
                jd = int(round(med * jr / 100.0) * 100)
                rentv.append([ym, jd, max(1, cnt + rnd.randint(-1, 2)),
                              int(jd * 0.93), int(jd * 1.08)])
            entry["areas"][akey] = {
                "rep": rep, "trade": trade, "rent": rentv,
                "n_trade": sum(x[2] for x in trade),
                "n_rent": sum(x[2] for x in rentv),
            }
        out.append(entry)

    payload = {
        "meta": {
            "generated": date.today().isoformat(),
            "start": str(START), "end": str(END),
            "types": ["trade", "rent"],
            "buckets": {"59": [52.0, 68.0], "84": [74.0, 92.0]},
            "regions": REGION_ORDER,
            "tiers": TIER_ORDER,
            "sample": True,
        },
        "complexes": out,
    }
    os.makedirs(DATA, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    p = os.path.join(DATA, "apt_data.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(blob)
    with open(os.path.join(DATA, "apt_data.js"), "w", encoding="utf-8") as f:
        f.write("window.APT_DATA = " + blob + ";\n")
    print("샘플 생성: %s (%.0f KB, 단지 %d개)"
          % (p, os.path.getsize(p) / 1024.0, len(out)))


if __name__ == "__main__":
    main()
