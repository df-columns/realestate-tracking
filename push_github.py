# -*- coding: utf-8 -*-
"""git 없이 data/apt_data.json 을 GitHub 에 올린다.

git 을 쓰면 그 PC 에 git 설치 + SSH 배포키 생성 + 리포 등록이 필요하다.
올릴 파일이 하나뿐이라 GitHub Contents API 로 PUT 한 번이면 끝난다.
그러면 그 PC 에는 파이썬만 있으면 된다.

푸시가 되면 GitHub 이 알아서 두 곳을 갱신한다.
  - GitHub Pages 재빌드      → github.io
  - deploy.yml 실행          → Firebase Hosting

토큰은 이 순서로 찾는다.
  1) --token 옵션
  2) GITHUB_TOKEN 환경변수
  3) 같은 폴더의 token.txt

토큰 만들기 (한 번)
  https://github.com/settings/personal-access-tokens/new
    Repository access  : Only select repositories → realestate-tracking
    Permissions        : Contents = Read and write
  만든 토큰을 token.txt 에 한 줄로 저장한다. 이 리포 하나의 파일만 고칠 수 있는 권한이다.
"""

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(ROOT, "data", "apt_data.json")

REPO = "df-columns/realestate-tracking"
PATH_IN_REPO = "data/apt_data.json"
BRANCH = "main"
API = "https://api.github.com"


def die(msg):
    print("[!] " + msg)
    sys.exit(1)


TOKEN_PREFIXES = ("github_pat_", "ghp_", "gho_", "ghs_", "ghu_")


def pick_token(text):
    """설명이 섞인 파일에서도 토큰 줄만 골라낸다. 붙여넣는 위치를 신경 쓸 필요가 없다."""
    for line in text.splitlines():
        t = line.strip()
        if t.startswith(TOKEN_PREFIXES):
            return t
    return None


def load_token(cli):
    if cli:
        return cli.strip()
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"].strip()
    p = os.path.join(ROOT, "token.txt")
    if os.path.exists(p):
        # utf-8-sig: 메모장이 붙이는 BOM 이 토큰에 섞여 들어가면 401 이 난다
        with io.open(p, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        t = pick_token(text)
        if t:
            return t
    die("token.txt 에 토큰이 없습니다.\n"
        "    만들기: https://github.com/settings/personal-access-tokens/new\n"
        "    Repository access = realestate-tracking / Permissions = Contents: Read and write\n"
        "    만든 토큰(github_pat_ 로 시작)을 token.txt 에 붙여넣으세요.")


def call(method, url, token, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "rtms-collector")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        # 사내망 SSL 검사 프록시 대응
        try:
            import truststore
            truststore.inject_into_ssl()
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            pass
        return e.code, {"message": detail or str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", help="GitHub 토큰 (기본: GITHUB_TOKEN 또는 token.txt)")
    ap.add_argument("--message", help="커밋 메시지 (기본: 데이터 기간으로 자동)")
    ap.add_argument("--check-token", action="store_true",
                    help="토큰이 있는지만 확인하고 끝낸다 (있으면 0, 없으면 1)")
    args = ap.parse_args()

    if args.check_token:
        # 설치 스크립트가 쓴다. findstr 은 BOM 이 붙은 파일에서 아무것도 못 찾으므로
        # 인코딩을 제대로 다루는 이쪽에서 판단한다.
        p = os.path.join(ROOT, "token.txt")
        if os.environ.get("GITHUB_TOKEN"):
            sys.exit(0)
        if os.path.exists(p):
            with io.open(p, encoding="utf-8-sig", errors="replace") as f:
                if pick_token(f.read()):
                    sys.exit(0)
        sys.exit(1)

    if not os.path.exists(DATA_JSON):
        die("data/apt_data.json 이 없습니다. update.py 를 먼저 실행하세요.")
    token = load_token(args.token)

    with io.open(DATA_JSON, "rb") as f:
        blob = f.read()
    with io.open(DATA_JSON, encoding="utf-8") as f:
        meta = json.load(f)["meta"]

    url = "%s/repos/%s/contents/%s" % (API, REPO, PATH_IN_REPO)

    # 기존 파일의 sha 가 있어야 덮어쓸 수 있다. 내용이 같으면 올리지 않는다.
    status, cur = call("GET", url + "?ref=" + BRANCH, token)
    if status == 401:
        die("토큰이 거부됐습니다 (401). 만료됐거나 잘못된 토큰입니다.")
    if status == 403:
        die("권한이 없습니다 (403). 토큰 권한에 Contents: Read and write 가 있는지 확인하세요.")
    sha = None
    if status == 200:
        sha = cur.get("sha")
        if cur.get("content"):
            remote = base64.b64decode(cur["content"])
            if remote == blob:
                print("원격과 내용이 같습니다 — 올리지 않습니다.")
                return
    elif status != 404:
        die("기존 파일 조회 실패 (%d): %s" % (status, cur.get("message")))

    body = {
        "message": args.message or ("데이터 갱신 %s~%s" % (meta["start"], meta["end"])),
        "content": base64.b64encode(blob).decode("ascii"),
        "branch": BRANCH,
        "committer": {"name": "rtms-collector", "email": "collector@users.noreply.github.com"},
    }
    if sha:
        body["sha"] = sha

    status, res = call("PUT", url, token, body)
    if status not in (200, 201):
        die("업로드 실패 (%d): %s" % (status, res.get("message")))

    commit = (res.get("commit") or {}).get("sha", "")[:7]
    print("업로드 완료 — %s (%.0f KB, %s~%s)"
          % (commit, len(blob) / 1024.0, meta["start"], meta["end"]))
    print("  Pages 와 Firebase 가 곧 갱신됩니다.")
    print("  https://df-columns.github.io/realestate-tracking/")
    print("  https://realestatetracking-89d37.web.app/")


if __name__ == "__main__":
    main()
