# -*- coding: utf-8 -*-
"""정기 갱신 오케스트레이터.

국토부 실거래가는 계약일 기준으로 등록되고 신고 기한이 30일이라, 이미 받아 둔
과거 달은 거의 변하지 않고 **최근 몇 달만 계속 늘어난다.** 그래서 매번 10년치를
다시 받을 필요가 없다.

  1) 최근 N개월만 --refresh 로 다시 받는다      (약 100회/월 · 사실상 공짜)
  2) 캐시 전체로 10년치를 재집계한다             (요청 0회)
  3) 결과가 온전한지 확인한다                    (단지 수 · 기간 · 최근 거래)
  4) (옵션) 배포용 public/ 을 빌드한다

1번은 요청 범위만 집계하므로 data/apt_data.json 이 잠깐 2~3개월짜리로 덮인다.
2번이 곧바로 이를 10년치로 되돌리지만, 중간에 죽으면 반쪽 데이터가 남는다.
그래서 시작 전에 백업해 두고 실패하면 되돌린다. 3번을 통과하지 못하면
배포 단계로 넘어가지 않는다.

사용
  py -3 update.py                  # 최근 3개월 갱신 + 재집계 + 검증
  py -3 update.py --recent 6       # 최근 6개월
  py -3 update.py --full           # 10년 전체 재수집 (약 7,200회, 일일 한도 주의)
  py -3 update.py --build          # 검증 후 public/ 까지 빌드
  py -3 update.py --aggregate-only # 수집 없이 재집계만
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(ROOT, "data", "apt_data.json")
BACKUP = DATA_JSON + ".bak"

MIN_COMPLEXES = 90        # complexes.py 기준 99개 — 이보다 적으면 뭔가 잘못됐다
MIN_YEARS = 9             # 10년치를 기대하지만 경계 달을 감안해 9년으로 본다


def run(args, label):
    print("\n=== %s ===" % label)
    print("$ %s" % " ".join(args))
    rc = subprocess.call([sys.executable] + args, cwd=ROOT)
    if rc != 0:
        raise RuntimeError("%s 실패 (exit %d)" % (label, rc))


def last_month():
    t = date.today()
    return "%d12" % (t.year - 1) if t.month == 1 else "%d%02d" % (t.year, t.month - 1)


def shift_month(ym, back):
    y, m = int(ym[:4]), int(ym[4:6])
    total = y * 12 + (m - 1) - back
    return "%d%02d" % (total // 12, total % 12 + 1)


def month_span(a, b):
    return (int(b[:4]) * 12 + int(b[4:6])) - (int(a[:4]) * 12 + int(a[4:6]))


def verify():
    """집계 결과가 배포해도 되는 상태인지 본다."""
    if not os.path.exists(DATA_JSON):
        raise RuntimeError("data/apt_data.json 이 없습니다")
    with open(DATA_JSON, encoding="utf-8") as f:
        j = json.load(f)

    meta = j.get("meta") or {}
    cx = j.get("complexes") or []
    start, end = meta.get("start"), meta.get("end")

    if len(cx) < MIN_COMPLEXES:
        raise RuntimeError("단지가 %d개뿐입니다 (%d개 이상이어야 함)" % (len(cx), MIN_COMPLEXES))
    if not start or not end:
        raise RuntimeError("meta.start / meta.end 가 비어 있습니다")
    if month_span(start, end) < MIN_YEARS * 12:
        raise RuntimeError(
            "기간이 %s~%s 로 너무 짧습니다 — 재집계가 안 끝난 상태로 보입니다" % (start, end))

    # 최근 달에 거래가 실제로 들어와 있는지 (전부 빈 껍데기면 수집이 실패한 것)
    recent = 0
    for c in cx:
        for a in (c.get("areas") or {}).values():
            for rows in (a.get("trade") or [], a.get("rent") or []):
                for r in rows:
                    if r and str(r[0]) >= shift_month(end, 2):
                        recent += 1
    if recent == 0:
        raise RuntimeError("최근 3개월 거래가 0건입니다 — 수집이 실패한 것으로 보입니다")

    kb = os.path.getsize(DATA_JSON) / 1024.0
    print("\n[o] 검증 통과 — 단지 %d개 · 기간 %s~%s · 최근 3개월 거래 %d건 · %.0f KB"
          % (len(cx), start, end, recent, kb))
    return j


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=3, help="다시 받을 최근 개월 수 (기본 3)")
    ap.add_argument("--full", action="store_true", help="10년 전체 재수집 (요청 약 7,200회)")
    ap.add_argument("--aggregate-only", action="store_true", help="수집 없이 재집계만")
    ap.add_argument("--build", action="store_true", help="검증 후 public/ 빌드")
    ap.add_argument("--rate", type=float, default=None, help="collect.py 의 초당 요청 상한")
    args = ap.parse_args()

    had_backup = os.path.exists(DATA_JSON)
    if had_backup:
        shutil.copyfile(DATA_JSON, BACKUP)

    try:
        if args.full:
            cmd = ["collect.py", "--refresh"]
            if args.rate:
                cmd += ["--rate", str(args.rate)]
            run(cmd, "전체 재수집 (10년)")
        elif not args.aggregate_only:
            end = last_month()
            start = shift_month(end, args.recent - 1)
            cmd = ["collect.py", "--start", start, "--end", end, "--refresh"]
            if args.rate:
                cmd += ["--rate", str(args.rate)]
            run(cmd, "최근 %d개월 재수집 (%s~%s)" % (args.recent, start, end))
            # 위 단계는 그 범위만 집계하므로 여기서 10년치로 되돌린다
            run(["collect.py", "--aggregate-only"], "전체 재집계 (10년)")
        else:
            run(["collect.py", "--aggregate-only"], "전체 재집계 (10년)")

        verify()

    except Exception as e:
        print("\n[!] %s" % e)
        if had_backup:
            shutil.copyfile(BACKUP, DATA_JSON)
            print("[!] data/apt_data.json 을 갱신 전 상태로 되돌렸습니다.")
        sys.exit(1)
    finally:
        if os.path.exists(BACKUP):
            os.remove(BACKUP)

    if args.build:
        run(["publish.py", "--build-only"], "배포용 빌드")

    print("\n갱신 완료.")


if __name__ == "__main__":
    main()
