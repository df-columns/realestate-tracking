# -*- coding: utf-8 -*-
"""단지 제원(총 세대수·준공연월·동수) 수집기 — 공동주택 단지정보 API.

  py -3 enrich.py            # data/complex_info.json 생성
  py -3 enrich.py --list     # 매칭 결과만 출력(저장 안 함)

필요한 활용신청 (data.go.kr, 둘 다 즉시 승인):
  · 국토교통부_공동주택 단지 목록제공 서비스   AptListService3
  · 국토교통부_공동주택 기본 정보제공 서비스   AptBasisInfoServiceV4

동작
  1) 대상 단지의 법정동(10자리 코드)으로 단지 목록을 받아 kaptCode 를 찾는다.
  2) kaptCode 로 기본정보를 받아 세대수(kaptdaCnt)·준공일(kaptUsedate)·동수(kaptDongCnt)를 뽑는다.
  3) data/complex_info.json 에 저장한다. 이후 collect.py --aggregate-only 로 화면에 반영.

법정동 10자리 코드는 시군구 5자리 + 읍면동 3자리 + 리 2자리다. 이 스크립트는
시군구 단위 목록(getSigunguAptList3)을 받아 단지명·법정동명으로 맞추므로
동 코드를 따로 관리하지 않는다.
"""
import argparse
import json
import os
import re
import sys
import time
from xml.etree import ElementTree as ET

import requests
import urllib3

try:
    import truststore
    truststore.inject_into_ssl()
    VERIFY = True
except Exception:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    VERIFY = False

from collect import load_key, norm, log, DATA, build_matchers, match
from complexes import COMPLEXES, LAWD_LIST

LIST_URL = "https://apis.data.go.kr/1613000/AptListService3/getSigunguAptList3"
INFO_URL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusBassInfoV4"


def _text(node, *names):
    for n in names:
        el = node.find(n)
        if el is not None and el.text is not None:
            return el.text.strip()
    return ""


def api(session, key, url, params, what):
    p = {"serviceKey": key}
    p.update(params)
    for attempt in range(5):
        try:
            r = session.get(url, params=p, timeout=30, verify=VERIFY)
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            root = ET.fromstring(r.text)
            code = _text(root, ".//resultCode") or _text(root, ".//returnReasonCode")
            if code and code not in ("00", "000"):
                msg = _text(root, ".//resultMsg", ".//returnAuthMsg", ".//errMsg")
                if "NOT_REGISTERED" in msg or code == "30":
                    sys.exit(
                        "\n[중단] %s 서비스에 이 키가 등록되지 않았습니다 (%s).\n"
                        "data.go.kr 에서 아래 두 API 활용신청 후 다시 실행하세요.\n"
                        "  · 국토교통부_공동주택 단지 목록제공 서비스\n"
                        "  · 국토교통부_공동주택 기본 정보제공 서비스\n" % (what, msg)
                    )
                raise RuntimeError("%s: %s %s" % (what, code, msg))
            return root
        except SystemExit:
            raise
        except Exception as e:
            if attempt == 4:
                log("!! %s 실패: %s" % (what, str(e)[:120]))
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def fetch_sigungu_list(session, key, lawd):
    """시군구의 단지 목록 → [{kaptCode, kaptName, bjdName}]"""
    out, page = [], 1
    while True:
        root = api(session, key, LIST_URL,
                   {"sigunguCode": lawd, "numOfRows": 1000, "pageNo": page},
                   "단지목록 %s" % lawd)
        if root is None:
            break
        items = list(root.iter("item"))
        for it in items:
            out.append({
                "code": _text(it, "kaptCode"),
                "name": _text(it, "kaptName"),
                "bjd": _text(it, "bjdongName", "bjdName", "as3"),
                "as1": _text(it, "as1"), "as2": _text(it, "as2"),
                "as3": _text(it, "as3"), "as4": _text(it, "as4"),
            })
        if len(items) < 1000:
            break
        page += 1
        if page > 20:
            break
    return out


def fetch_info(session, key, code):
    root = api(session, key, INFO_URL, {"kaptCode": code}, "기본정보 %s" % code)
    if root is None:
        return None
    it = next(root.iter("item"), None)
    if it is None:
        return None
    used = re.sub(r"[^0-9]", "", _text(it, "kaptUsedate"))     # YYYYMMDD
    built = None
    if len(used) >= 6:
        built = "%s-%s" % (used[:4], used[4:6])
    def num(name):
        v = re.sub(r"[^0-9]", "", _text(it, name))
        return int(v) if v else None
    return {
        "kapt_name": _text(it, "kaptName"),
        "built": built,
        "units": num("kaptdaCnt"),
        "dong_cnt": num("kaptDongCnt"),
        "addr": _text(it, "kaptAddr"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key")
    ap.add_argument("--list", action="store_true", help="매칭 결과만 출력")
    a = ap.parse_args()
    key = load_key(a.key)
    session = requests.Session()

    matchers = build_matchers()
    by_lawd = {}
    for mt in matchers:
        by_lawd.setdefault(mt["c"]["lawd"], []).append(mt)

    result, unmatched = {}, []
    for lawd in LAWD_LIST:
        lst = fetch_sigungu_list(session, key, lawd)
        log("%s 단지 %d개" % (lawd, len(lst)))
        for mt in by_lawd[lawd]:
            c = mt["c"]
            hit = None
            for row in lst:
                dong = row.get("as3") or row.get("bjd") or ""
                if match(mt, norm(row["name"]), dong):
                    hit = row
                    break
            if not hit:                       # 동 조건을 풀고 이름만으로 한 번 더
                for row in lst:
                    an = norm(row["name"])
                    if any(al and al in an for al in mt["aliases"]):
                        hit = row
                        break
            if not hit:
                unmatched.append(c)
                continue
            info = fetch_info(session, key, hit["code"])
            if not info:
                unmatched.append(c)
                continue
            info["kapt_code"] = hit["code"]
            result[c["id"]] = info
            log("  %s → %s / %s세대 / 준공 %s"
                % (c["name"], info["kapt_name"],
                   format(info["units"], ",") if info["units"] else "?",
                   info["built"] or "?"))

    log("\n제원 확보 %d개 / 미확보 %d개" % (len(result), len(unmatched)))
    for c in unmatched:
        log("  미확보: %s %s %s" % (c["sgg"], c["dong"], c["name"]))

    if not a.list:
        os.makedirs(DATA, exist_ok=True)
        p = os.path.join(DATA, "complex_info.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        log("저장: %s" % p)
        log("→ py -3 collect.py --aggregate-only 로 화면에 반영하세요.")


if __name__ == "__main__":
    main()
