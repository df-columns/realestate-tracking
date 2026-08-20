# -*- coding: utf-8 -*-
"""
index.html 을 "데이터 없이도 도는 단일 파일" 로 만들어 주는 스크립트.

세 가지 모드가 있다.

[A] Hosting 모드 (기본) — firebase CLI 로그인이 되어 있을 때
    py -3 publish.py
      1) public/ 을 비우고 index.html + data/apt_data.json 둘만 넣는다.
         apikey.txt, cache/, *.py 는 구조적으로 올라갈 수 없다.
      2) public/index.html 의 REMOTE_DATA_URL 을 Hosting 주소로 채운다.
      3) firebase deploy --only hosting
    준비: firebase login  →  firebase use --add

[B] Cloud Shell 모드 — 로컬 CLI 로그인이 안 될 때 (권장 우회로)
    py -3 publish.py --zip --project <프로젝트ID>
      dist/deploy-<프로젝트ID>.zip 하나를 만든다. 안에 firebase.json + public/ 이 들어 있다.
      Firebase 콘솔 우측 상단의 Cloud Shell (>_) 을 열고 이 zip 을 업로드한 뒤
      출력되는 명령 세 줄을 붙여넣으면 배포가 끝난다.
      Cloud Shell 의 firebase CLI 는 콘솔에 로그인한 계정으로 이미 인증돼 있다.

[C] 외부 URL 모드 — CLI 로그인 없이, 콘솔에서 데이터만 직접 올렸을 때
    py -3 publish.py --data-url "https://.../apt_data.json"
      dist/index.html 하나만 만든다. 그 파일을 더블클릭하면
      지정한 URL 에서 데이터를 받아 화면이 뜬다. 배포 안 함.
    Firebase Storage 다운로드 URL, Realtime Database 의 .json URL,
    그 밖에 CORS 가 열린 아무 주소나 다 된다.

기타 옵션
    --build-only            public/ 만 만들고 배포는 생략
    --project <ID>          .firebaserc 대신 프로젝트 지정
    --site <이름>           Hosting 사이트가 여러 개일 때
    --no-check              데이터 URL 접속 확인 생략
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(ROOT, "data", "apt_data.json")
SRC_HTML = os.path.join(ROOT, "index.html")
PUBLIC = os.path.join(ROOT, "public")
DIST = os.path.join(ROOT, "dist")

# 소스의 REMOTE_DATA_URL 은 이미 Firebase 주소가 들어 있다(웹에서 열렸을 때의 폴백).
# 배포·단일파일 빌드 때는 그 값을 그때의 주소로 다시 써 넣는다.
URL_LINE = re.compile(r'var REMOTE_DATA_URL = "[^"]*";')


def die(msg):
    print("[!] " + msg)
    sys.exit(1)


def read_project(explicit):
    if explicit:
        return explicit
    rc = os.path.join(ROOT, ".firebaserc")
    if not os.path.exists(rc):
        die(".firebaserc 가 없습니다. `firebase login` 후 `firebase use --add` 를 실행하거나,\n"
            "    `--project <프로젝트ID>` 로 지정하세요.\n"
            "    CLI 로그인이 안 되면 `--data-url <데이터주소>` 모드를 쓰세요.")
    with open(rc, encoding="utf-8") as f:
        rcj = json.load(f)
    projects = rcj.get("projects", {})
    pid = projects.get("default") or (list(projects.values())[0] if projects else None)
    if not pid:
        die(".firebaserc 에서 프로젝트 ID를 찾지 못했습니다. `firebase use --add` 를 실행하세요.")
    return pid


def render_html(data_url):
    """index.html 을 읽어 REMOTE_DATA_URL 을 채운 문자열을 돌려준다."""
    with open(SRC_HTML, encoding="utf-8") as f:
        html = f.read()
    html, n = URL_LINE.subn('var REMOTE_DATA_URL = "%s";' % data_url, html, count=1)
    if not n:
        die("index.html 에서 REMOTE_DATA_URL 줄을 찾지 못했습니다. (index.html 이 수정된 듯합니다)")
    return html


def clear_dir(d):
    """폴더 자체를 지우면 탐색기/서버가 잡고 있을 때 WinError 32 가 난다. 내용만 비운다."""
    if not os.path.isdir(d):
        return
    for name in os.listdir(d):
        child = os.path.join(d, name)
        if os.path.isdir(child):
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                os.remove(child)
            except OSError:
                pass


def check_url(url):
    """데이터 URL 이 정말 이 화면이 읽을 수 있는 JSON 을 주는지 본다."""
    import urllib.request
    try:
        try:                      # 사내망 SSL 검사 프록시 대응
            import truststore
            truststore.inject_into_ssl()
        except ImportError:
            pass
        with urllib.request.urlopen(url, timeout=20) as r:
            head = r.read(400000)
        obj = json.loads(head.decode("utf-8"))
    except Exception as e:
        print("[?] 데이터 URL 확인 실패: %s" % e)
        print("    주소가 맞는지, 공개 읽기가 허용됐는지 확인하세요. (건너뛰려면 --no-check)")
        return False
    n = len(obj.get("complexes") or [])
    if not n:
        print("[?] 응답은 왔지만 complexes 가 비어 있습니다. 올린 파일이 apt_data.json 인지 확인하세요.")
        return False
    print("[o] 데이터 URL 정상 — 단지 %d개, 생성 %s" % (n, (obj.get("meta") or {}).get("generated", "?")))
    return True


def build_hosting(host):
    if not os.path.exists(DATA_JSON):
        die("data/apt_data.json 이 없습니다. `py -3 collect.py` 또는 `py -3 make_sample.py` 를 먼저 실행하세요.")
    data_url = "https://%s/data/apt_data.json" % host
    html = render_html(data_url)

    clear_dir(PUBLIC)
    os.makedirs(os.path.join(PUBLIC, "data"), exist_ok=True)
    out_html = os.path.join(PUBLIC, "index.html")
    with open(out_html, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    shutil.copyfile(DATA_JSON, os.path.join(PUBLIC, "data", "apt_data.json"))

    print("public/ 생성 완료")
    print("  index.html            (데이터 주소: %s)" % data_url)
    print("  data/apt_data.json    %.0f KB" % (os.path.getsize(DATA_JSON) / 1024.0))
    return out_html


def build_standalone(data_url):
    html = render_html(data_url)
    clear_dir(DIST)
    os.makedirs(DIST, exist_ok=True)
    out_html = os.path.join(DIST, "index.html")
    with open(out_html, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("dist/index.html 생성 완료 (%.0f KB)" % (os.path.getsize(out_html) / 1024.0))
    print("  데이터 주소: %s" % data_url)
    return out_html


def build_zip(project, host):
    """Cloud Shell 에 올릴 배포 꾸러미 하나를 만든다."""
    build_hosting(host)
    os.makedirs(DIST, exist_ok=True)
    zpath = os.path.join(DIST, "deploy-%s.zip" % project)
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(os.path.join(ROOT, "firebase.json"), "firebase.json")
        for base, _dirs, files in os.walk(PUBLIC):
            for name in files:
                full = os.path.join(base, name)
                z.write(full, os.path.relpath(full, ROOT).replace(os.sep, "/"))
    kb = os.path.getsize(zpath) / 1024.0
    print("\n%s (%.0f KB)" % (zpath, kb))
    print("""
Cloud Shell 로 배포하기
  1) https://console.firebase.google.com/project/{pid}/hosting  접속
  2) 우측 상단 Cloud Shell 아이콘 ( >_ ) 클릭 — 아래에 터미널이 열린다
  3) 터미널 우측 위 점 세 개 ( : ) → 업로드 → 파일 → 위 zip 선택
  4) 터미널에 아래 세 줄 붙여넣기

     unzip -o deploy-{pid}.zip -d apt
     cd apt
     firebase deploy --only hosting --project {pid}

  끝나면 https://{host}/ 로 열린다.
  (CLI 버전이 낮다고 하면 마지막 줄을 npx firebase-tools@latest deploy --only hosting --project {pid} 로)
""".format(pid=project, host=host))
    return zpath


def deploy(project):
    cmd = ["firebase", "deploy", "--only", "hosting", "--project", project]
    print("\n$ " + " ".join(cmd))
    # Windows 에서 firebase 는 .cmd 셔임이라 shell=True 가 필요하다
    rc = subprocess.call(cmd, cwd=ROOT, shell=(os.name == "nt"))
    if rc != 0:
        die("firebase deploy 실패 (exit %d). `firebase login` 상태를 확인하세요." % rc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true",
                    help="Cloud Shell 에 올릴 배포 zip 만들기 (로컬 CLI 로그인 불필요)")
    ap.add_argument("--data-url", help="콘솔에서 직접 올린 apt_data.json 의 주소 (배포 안 함)")
    ap.add_argument("--project", help="Firebase 프로젝트 ID (기본: .firebaserc)")
    ap.add_argument("--site", help="Hosting 사이트 이름 (기본: 프로젝트 ID)")
    ap.add_argument("--build-only", action="store_true", help="public/ 만 만들고 배포는 생략")
    ap.add_argument("--no-check", action="store_true", help="데이터 URL 접속 확인 생략")
    args = ap.parse_args()

    if args.data_url:
        if not args.no_check:
            check_url(args.data_url)
        out_html = build_standalone(args.data_url)
        print("\n공유용 단일 파일  %s" % out_html)
        print("                  (이 파일만 보내면 받는 사람은 그냥 열어서 봅니다)")
        return

    project = read_project(args.project)
    site = args.site or project
    host = "%s.web.app" % site

    if args.zip:
        build_zip(project, host)
        print("  공유용 단일 파일  %s" % os.path.join(PUBLIC, "index.html"))
        return

    out_html = build_hosting(host)

    if args.build_only:
        print("\n--build-only: 배포는 생략했습니다.")
    else:
        deploy(project)
        print("\n배포 완료")
        print("  웹에서 보기      https://%s/" % host)

    print("  공유용 단일 파일  %s" % out_html)
    print("                    (이 파일만 보내면 받는 사람은 그냥 열어서 봅니다)")


if __name__ == "__main__":
    main()
