# -*- coding: utf-8 -*-
"""국토교통부 아파트 매매/전월세 실거래가 수집기.

  py -3 collect.py                      # 최근 10년, 매매+전월세
  py -3 collect.py --start 202101       # 기간 지정
  py -3 collect.py --types trade        # 매매만
  py -3 collect.py --aggregate-only     # 재수집 없이 캐시로 재집계(별칭 수정 후 사용)

수집 결과는 cache/ 에 월·구 단위로 저장되므로 중단 후 다시 실행해도 이어서 받는다.
최종 산출물: data/apt_data.json, data/apt_data.js, data/match_report.md
"""
import argparse
import gzip
import json
import os
import random
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from xml.etree import ElementTree as ET

import requests
import urllib3

# 사내 SSL 검사 프록시 대응: 윈도우 인증서 저장소를 그대로 사용한다.
try:
    import truststore
    truststore.inject_into_ssl()
    VERIFY = True
except Exception:                                    # truststore 미설치 환경
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    VERIFY = False

from complexes import (COMPLEXES, LAWD_LIST, REGION_ORDER, SGG_LABEL,
                       TARGET_DONGS, TIER_ORDER)

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
DATA = os.path.join(BASE, "data")

ENDPOINTS = {
    "trade": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "rent": "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
}

# 전용면적 버킷: (하한, 상한) — 이 범위 안에서 대표 타입 하나를 자동 선택한다.
AREA_BUCKETS = {"59": (52.0, 68.0), "84": (74.0, 92.0)}
AREA_TARGET = {"59": 59.9, "84": 84.9}

_print_lock = threading.Lock()
_stop = threading.Event()


class Limiter:
    """초당 요청수를 맞춰 내보내는 토큰버킷.

    이 API 는 짧은 시간에 몰아 보내면 429 를 던진다. 맞고 나서 길게 쉬는
    (지수 백오프) 방식은 처리량이 무너지므로, 애초에 간격을 두고 보낸다.
    429 가 나면 속도를 낮추고, 성공이 이어지면 조금씩 올린다.
    """

    def __init__(self, rate):
        self.target = float(rate)
        self.rate = float(rate)
        self.lock = threading.Lock()
        self.next_at = time.monotonic()
        self.last_hit = 0.0
        self.throttles = 0

    def wait(self):
        with self.lock:
            gap = 1.0 / self.rate
            t = max(time.monotonic(), self.next_at)
            self.next_at = t + gap
        d = t - time.monotonic()
        if d > 0:
            time.sleep(d)

    def penalize(self):
        with self.lock:
            self.rate = max(1.5, self.rate * 0.8)      # 완만하게 줄인다
            self.next_at = max(self.next_at, time.monotonic() + 0.5)
            self.last_hit = time.monotonic()
            self.throttles += 1

    def reward(self):
        # 마지막 429 로부터 시간이 지났으면 회복시킨다(성공 횟수가 아니라 시간 기준).
        with self.lock:
            if self.rate < self.target and time.monotonic() - self.last_hit > 12:
                self.rate = min(self.target, self.rate * 1.3)
                self.last_hit = time.monotonic()


_limiter = Limiter(8.0)


def log(msg):
    with _print_lock:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()


def norm(s):
    """아파트명 정규화: 공백/기호 제거, 소문자, '이편한세상'→'e편한세상'."""
    s = (s or "").strip().lower()
    s = s.replace("이편한세상", "e편한세상").replace("e-편한세상", "e편한세상")
    return re.sub(r"[^0-9a-z가-힣]", "", s)


def months(start, end):
    y, m = int(start[:4]), int(start[4:6])
    ey, em = int(end[:4]), int(end[4:6])
    out = []
    while (y, m) <= (ey, em):
        out.append("%d%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def load_key(cli_key):
    if cli_key:
        return cli_key.strip()
    if os.environ.get("MOLIT_SERVICE_KEY"):
        return os.environ["MOLIT_SERVICE_KEY"].strip()
    p = os.path.join(BASE, "apikey.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    sys.exit("서비스 키가 없습니다. --key 옵션이나 apikey.txt 를 사용하세요.")


# ── 수집 ────────────────────────────────────────────────────────────────
def cache_path(dtype, lawd, ym):
    return os.path.join(CACHE, dtype, lawd, ym + ".json.gz")


def read_cache(dtype, lawd, ym):
    p = cache_path(dtype, lawd, ym)
    if not os.path.exists(p):
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_cache(dtype, lawd, ym, rows):
    p = cache_path(dtype, lawd, ym)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)


def _text(item, *names):
    for n in names:
        el = item.find(n)
        if el is not None and el.text is not None:
            return el.text.strip()
    return ""


def parse_items(xml_text, dtype):
    root = ET.fromstring(xml_text)
    code = _text(root, ".//resultCode") or _text(root, ".//returnReasonCode")
    if code and code not in ("00", "000"):
        msg = _text(root, ".//resultMsg", ".//returnAuthMsg", ".//errMsg")
        raise RuntimeError("API 오류 %s: %s" % (code, msg))
    rows = []
    for it in root.iter("item"):
        apt = _text(it, "aptNm", "아파트")
        area = _text(it, "excluUseAr", "전용면적")
        if not apt or not area:
            continue
        try:
            area_f = float(area)
        except ValueError:
            continue
        row = {
            "apt": apt,
            "dong": _text(it, "umdNm", "법정동"),
            "area": round(area_f, 2),
            "y": _text(it, "dealYear", "년"),
            "m": _text(it, "dealMonth", "월"),
            "d": _text(it, "dealDay", "일"),
            "floor": _text(it, "floor", "층"),
            "by": _text(it, "buildYear", "건축년도"),
        }
        if dtype == "trade":
            amt = _text(it, "dealAmount", "거래금액").replace(",", "")
            if not amt:
                continue
            row["price"] = int(amt)
            row["cancel"] = _text(it, "cdealType")  # 'O' = 해제된 거래
        else:
            dep = _text(it, "deposit", "보증금액").replace(",", "")
            rent = _text(it, "monthlyRent", "월세금액").replace(",", "") or "0"
            if not dep:
                continue
            row["price"] = int(dep)
            row["rent"] = int(rent)
        rows.append(row)
    return rows


def keep_row(row, lawd, alias_index):
    """시군구의 모든 행을 그대로 보관한다.

    동/단지명으로 걸러 두면 나중에 단지를 추가할 때마다 전체를 다시 받아야 한다.
    시군구·월 단위 파일이 gz 로 수 KB~수십 KB 라 전부 남겨도 100MB 안쪽이다.
    """
    return True


def fetch_one(session, key, dtype, lawd, ym, alias_index, refresh):
    if not refresh:
        cached = read_cache(dtype, lawd, ym)
        if cached is not None:
            return len(cached), True
    rows, page = [], 1
    while True:
        if _stop.is_set():
            return 0, False
        params = {
            "serviceKey": key, "LAWD_CD": lawd, "DEAL_YMD": ym,
            "numOfRows": 1000, "pageNo": page,
        }
        last_err = None
        for attempt in range(8):
            _limiter.wait()
            try:
                r = session.get(ENDPOINTS[dtype], params=params, timeout=40, verify=VERIFY)
                if r.status_code == 429:
                    _limiter.penalize()      # 속도를 낮춰 다음 요청부터 간격을 벌린다
                    last_err = RuntimeError("429 Too Many Requests")
                    time.sleep(0.4 + random.random() * 0.4)
                    continue
                r.raise_for_status()
                got = parse_items(r.text, dtype)
                _limiter.reward()
                last_err = None
                break
            except Exception as e:
                last_err = e
                if "LIMITED_NUMBER_OF_SERVICE_REQUESTS" in str(e):
                    _stop.set()
                    log("!! 일일 트래픽 초과로 중단: %s" % e)
                    return 0, False
                time.sleep(1.0 * (attempt + 1))
        if last_err is not None:
            log("!! 실패 %s %s %s p%d: %s" % (dtype, lawd, ym, page, str(last_err)[:90]))
            return -1, False
        rows.extend(got)
        if len(got) < 1000:
            break
        page += 1
        if page > 12:
            break
    kept = [x for x in rows if keep_row(x, lawd, alias_index)]
    write_cache(dtype, lawd, ym, kept)
    return len(kept), False


def collect(key, dtypes, yms, workers, refresh):
    alias_index = {}
    for c in COMPLEXES:
        alias_index.setdefault(c["lawd"], set()).update(norm(a) for a in c["aliases"])
    jobs = [(d, l, y) for d in dtypes for l in LAWD_LIST for y in yms]
    total = len(jobs)
    done = {"n": 0, "hit": 0, "rows": 0, "fail": 0}
    session = requests.Session()

    def work(job):
        d, l, y = job
        if _stop.is_set():
            return
        n, cached = fetch_one(session, key, d, l, y, alias_index, refresh)
        done["n"] += 1
        if n < 0:
            done["fail"] += 1
        else:
            done["rows"] += n
        done["hit"] += 1 if cached else 0
        if done["n"] % 100 == 0 or done["n"] == total:
            log("  %d/%d 요청 (캐시 %d · 실패 %d) · 보관 %s건 · %.1f req/s (감속 %d회)"
                % (done["n"], total, done["hit"], done["fail"],
                   format(done["rows"], ","), _limiter.rate, _limiter.throttles))

    log("수집 시작: %s개 요청 (%s × %d구 × %d개월)"
        % (format(total, ","), ", ".join(dtypes), len(LAWD_LIST), len(yms)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, jobs))
    if done["fail"]:
        log("실패 %d건 — 캐시에 저장하지 않았으니 다시 실행하면 그 부분만 재시도합니다."
            % done["fail"])
    if _stop.is_set():
        log("중단됨 — 다시 실행하면 캐시 이후부터 이어서 받습니다.")
    return not _stop.is_set()


# ── 매칭 & 집계 ─────────────────────────────────────────────────────────
def build_matchers():
    out = []
    for c in COMPLEXES:
        out.append({
            "c": c,
            "aliases": [norm(a) for a in c["aliases"]],
            "exclude": [norm(x) for x in c["exclude"]],
            "dongs": set(c["dongs"]),
        })
    return out


def match(mt, apt_norm, dong):
    if any(x and x in apt_norm for x in mt["exclude"]):
        return False
    if mt["c"]["exact"]:
        return dong in mt["dongs"] and apt_norm in mt["aliases"]
    for al in mt["aliases"]:
        if al and al in apt_norm:
            if not mt["dongs"] or dong in mt["dongs"]:
                return True
            if len(al) >= 5:   # 동 정보가 어긋나도 이름이 충분히 고유하면 인정
                return True
    return False


def pick_area_group(rows, target):
    """버킷 안의 거래들 중 대표 타입(가장 target 에 가까운 유효 면적군)을 고른다."""
    if not rows:
        return None, []
    counts = {}
    for r in rows:
        a = round(r["area"], 1)
        counts[a] = counts.get(a, 0) + 1
    floor_n = max(3, int(len(rows) * 0.02))
    cand = [a for a, n in counts.items() if n >= floor_n] or list(counts)
    rep = min(cand, key=lambda a: (abs(a - target), -counts[a]))
    sel = [r for r in rows if abs(r["area"] - rep) <= 1.0]
    return rep, sel


def monthly(rows):
    by = {}
    for r in rows:
        try:
            ym = int("%s%02d" % (r["y"], int(r["m"])))
        except (ValueError, TypeError):
            continue
        by.setdefault(ym, []).append(r["price"])
    out = []
    for ym in sorted(by):
        v = sorted(by[ym])
        out.append([ym, int(round(statistics.median(v))), len(v), v[0], v[-1]])
    return out


def load_complex_info():
    """enrich.py 가 만든 단지 제원(세대수·준공연월). 없으면 빈 dict."""
    p = os.path.join(DATA, "complex_info.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def aggregate(dtypes, yms):
    matchers = build_matchers()
    info = load_complex_info()
    by_lawd = {}
    for mt in matchers:
        by_lawd.setdefault(mt["c"]["lawd"], []).append(mt)

    pool = {c["id"]: dict((d, []) for d in dtypes) for c in COMPLEXES}
    seen_names = {}   # (lawd, dong) -> {aptNm: 건수}  매칭 실패 진단용
    missing_cache = 0

    for dtype in dtypes:
        for lawd in LAWD_LIST:
            for ym in yms:
                rows = read_cache(dtype, lawd, ym)
                if rows is None:
                    missing_cache += 1
                    continue
                for r in rows:
                    if dtype == "trade" and r.get("cancel") == "O":
                        continue
                    if dtype == "rent" and r.get("rent", 0) != 0:
                        continue  # 순수 전세만
                    an = norm(r["apt"])
                    if r["dong"] in TARGET_DONGS.get(lawd, set()):
                        d = seen_names.setdefault((lawd, r["dong"]), {})
                        d[r["apt"]] = d.get(r["apt"], 0) + 1
                    for mt in by_lawd[lawd]:
                        if match(mt, an, r["dong"]):
                            pool[mt["c"]["id"]][dtype].append(r)
                            break

    complexes_out, report = [], []
    for c in COMPLEXES:
        entry = dict((k, c[k]) for k in
                     ("id", "region", "sgg", "dong", "name", "tier", "area_name", "src"))
        entry["sgg_label"] = SGG_LABEL.get(c["sgg"], c["sgg"])

        # 건축년도: 매칭된 거래에 실려온 buildYear 중 최빈값
        years = {}
        for dt in dtypes:
            for r in pool[c["id"]][dt]:
                y = (r.get("by") or "").strip()
                if y.isdigit() and 1900 < int(y) < 2100:
                    years[int(y)] = years.get(int(y), 0) + 1
        entry["build_year"] = max(years, key=years.get) if years else None

        inf = info.get(c["id"]) or {}
        entry["units"] = inf.get("units")          # 총 세대수
        entry["built"] = inf.get("built")          # 준공연월 'YYYY-MM'
        entry["dong_cnt"] = inf.get("dong_cnt")    # 동 수
        entry["kapt_name"] = inf.get("kapt_name")
        entry["areas"] = {}
        for akey in ("59", "84"):
            lo, hi = AREA_BUCKETS[akey]
            info = {"rep": None}
            rep = None
            for dtype in dtypes:
                bucket = [r for r in pool[c["id"]][dtype] if lo <= r["area"] < hi]
                if rep is None:
                    rep, sel = pick_area_group(bucket, AREA_TARGET[akey])
                else:
                    sel = [r for r in bucket if abs(r["area"] - rep) <= 1.0]
                info[dtype] = monthly(sel)
                info["n_" + dtype] = len(sel)
            info["rep"] = rep
            entry["areas"][akey] = info
        complexes_out.append(entry)

        tot = sum(len(pool[c["id"]][d]) for d in dtypes)
        if tot == 0:
            cands = seen_names.get((c["lawd"], c["dong"]), {})
            top = sorted(cands.items(), key=lambda kv: -kv[1])[:12]
            report.append(
                "- **%s %s %s** — 매칭 0건. %s 실거래 아파트명 후보: %s"
                % (c["sgg"], c["dong"], c["name"], c["dong"],
                   ", ".join("%s(%d)" % (n, k) for n, k in top) if top else "(해당 동 데이터 없음)")
            )
        else:
            names = {}
            for d in dtypes:
                for r in pool[c["id"]][d]:
                    names[r["apt"]] = names.get(r["apt"], 0) + 1
            spec = []
            if entry["build_year"]:
                spec.append("%d년식" % entry["build_year"])
            if entry.get("built"):
                spec.append("준공 %s" % entry["built"])
            if entry.get("units"):
                spec.append("%s세대" % format(entry["units"], ","))
            report.append(
                "- %s %s **%s** — %s건 / 매칭명: %s / 대표면적 59㎡→%s · 84㎡→%s%s"
                % (c["sgg"], c["dong"], c["name"], format(tot, ","),
                   ", ".join(sorted(names)),
                   entry["areas"]["59"]["rep"] or "없음",
                   entry["areas"]["84"]["rep"] or "없음",
                   (" / " + " · ".join(spec)) if spec else "")
            )

    meta = {
        "generated": date.today().isoformat(),
        "start": yms[0], "end": yms[-1],
        "types": dtypes,
        "buckets": AREA_BUCKETS,
        "regions": REGION_ORDER,
        "tiers": TIER_ORDER,
        "has_info": bool(info),
        "sample": False,
    }
    out = {"meta": meta, "complexes": complexes_out}
    os.makedirs(DATA, exist_ok=True)
    blob = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    # Firebase Hosting 배포용 — index.html 이 fetch 하는 원본
    with open(os.path.join(DATA, "apt_data.json"), "w", encoding="utf-8") as f:
        f.write(blob)
    # file:// 로 직접 열 때를 위한 로컬 폴백
    with open(os.path.join(DATA, "apt_data.js"), "w", encoding="utf-8") as f:
        f.write("window.APT_DATA = " + blob + ";\n")
    with open(os.path.join(DATA, "match_report.md"), "w", encoding="utf-8") as f:
        f.write("# 단지 매칭 리포트 (%s)\n\n" % meta["generated"])
        f.write("기간 %s~%s · 유형 %s\n\n" % (yms[0], yms[-1], ", ".join(dtypes)))
        if missing_cache:
            f.write("> 캐시 없는 (구, 월) 조합 %d건 — collect.py 를 다시 실행하세요.\n\n" % missing_cache)
        f.write("\n".join(report) + "\n")

    size = os.path.getsize(os.path.join(DATA, "apt_data.json")) / 1024.0
    zero = sum(1 for r in report if "매칭 0건" in r)
    log("집계 완료: data/apt_data.json (%.0f KB) · 매칭 0건 단지 %d개" % (size, zero))
    log("→ data/match_report.md 확인 후 complexes.py 의 aliases/dongs 를 보정하고 "
        "`py -3 collect.py --aggregate-only` 로 재집계하세요.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key")
    ap.add_argument("--start", default=None, help="YYYYMM (기본: 10년 전)")
    ap.add_argument("--end", default=None, help="YYYYMM (기본: 지난달)")
    ap.add_argument("--types", default="trade,rent")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rate", type=float, default=8.0,
                    help="초당 요청 수 상한 (429 가 잦으면 낮춘다)")
    ap.add_argument("--refresh", action="store_true", help="캐시 무시하고 다시 받기")
    ap.add_argument("--aggregate-only", action="store_true")
    a = ap.parse_args()

    today = date.today()
    if a.end:
        end = a.end
    elif today.month > 1:
        end = "%d%02d" % (today.year, today.month - 1)
    else:
        end = "%d12" % (today.year - 1)
    start = a.start or ("%d%s" % (int(end[:4]) - 9, end[4:6]))
    yms = months(start, end)
    dtypes = [t for t in a.types.split(",") if t in ENDPOINTS]

    if not a.aggregate_only:
        global _limiter
        _limiter = Limiter(a.rate)
        collect(load_key(a.key), dtypes, yms, a.workers, a.refresh)
    aggregate(dtypes, yms)


if __name__ == "__main__":
    main()
