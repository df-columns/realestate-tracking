# -*- coding: utf-8 -*-
"""상시 켜두는 PC 로 그대로 복사해 쓰는 수집기 폴더를 만든다.

  py -3 bundle.py

dist/수집기/ 가 나온다. 그 폴더를 상시 PC 로 복사하고 '설치.bat' 을 더블클릭하면 끝이다.
git 도, SSH 배포키도, 리포 clone 도 필요 없다 — 데이터 한 파일을 GitHub API 로
올리기 때문이다(push_github.py). 그 PC 에 필요한 건 파이썬 하나다.

캐시(67MB)를 같이 담으므로 상시 PC 에서 10년 전체 수집을 다시 하지 않는다.
"""

import argparse
import io
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "dist", "수집기")

# 수집에 실제로 필요한 것만 담는다. index.html·publish.py·firebase.json 등
# 화면·배포 쪽은 GitHub 이 처리하므로 상시 PC 에 있을 필요가 없다.
FILES = ["collect.py", "complexes.py", "update.py", "push_github.py", "apikey.txt"]

INSTALL_BAT = r"""@echo off
cd /d "%~dp0"
title 실거래가 수집기 설치

echo.
echo   ============================================
echo    실거래가 수집기 설치
echo   ============================================
echo.

rem ---- 1. 파이썬 ----------------------------------------------------
set PY=
py -3 -V >nul 2>&1 && set PY=py -3
if not defined PY ( python -V >nul 2>&1 && set PY=python )
if not defined PY goto nopython
echo   [1/4] 파이썬 확인 완료
echo.

rem ---- 2. 토큰 ------------------------------------------------------
rem findstr 은 BOM 이 붙은 파일에서 아무것도 못 찾는다(/b 여부와 무관).
rem 메모장이 BOM 을 붙이므로 인코딩을 제대로 다루는 파이썬에 맡긴다.
set TRIES=0

:tokencheck
%PY% push_github.py --check-token >nul 2>&1
if not errorlevel 1 goto tokenok
set /a TRIES+=1
if %TRIES% GEQ 4 goto tokengiveup
goto asktoken

:tokenok
echo   [2/4] GitHub 토큰 확인 완료
echo.

rem ---- 3. 의존성 ----------------------------------------------------
echo   [3/4] 필요한 라이브러리 설치 중...
%PY% -m pip install --quiet --disable-pip-version-check requests truststore
if errorlevel 1 (
  echo   [!] 라이브러리 설치 실패. 인터넷 연결을 확인하세요.
  pause
  exit /b 1
)
echo         완료
echo.

rem ---- 4. 시험 실행 + 스케줄 등록 -----------------------------------
echo   [4/4] 지금 한 번 돌려 봅니다 ^(1~2분^)
echo.
set PYTHONIOENCODING=cp949:replace
set PYTHONUTF8=1

rem 캐시가 없으면 10년 전체를 받아야 한다. --recent 3 만 돌리면 과거가 빈
rem 데이터가 되어 update.py 의 검증에서 막힌다.
set NCACHE=0
if exist cache for /f %%C in ('dir /s /b cache\*.json.gz 2^>nul ^| find /c /v ""') do set NCACHE=%%C
echo         캐시 파일 %NCACHE% 개
if %NCACHE% LSS 5000 (
  echo         캐시가 없어 10년 전체를 받습니다. 15~20분 걸립니다.
  %PY% -u update.py --full --push
) else (
  %PY% -u update.py --recent 3 --push
)
if errorlevel 1 (
  echo.
  echo   [!] 시험 실행이 실패했습니다. 위 메시지를 확인하세요.
  pause
  exit /b 1
)

rem 파이썬 절대경로를 적어 둔다. SYSTEM 계정의 PATH 에는 py 런처가 없다.
for /f "delims=" %%P in ('%PY% -c "import sys;print(sys.executable)"') do >pyexe.txt echo %%P

rem SYSTEM 계정으로 등록한다. 그러면 아무도 로그온하지 않아도, 다른 사람이
rem 로그온해도 실행된다. 비밀번호를 물어보지 않는다.
schtasks /create /tn "실거래가 갱신" /sc daily /st 04:37 /ru SYSTEM /f /tr "\"%~dp0update.bat\"" >nul 2>&1
if errorlevel 1 (
  echo         SYSTEM 등록 실패 - 현재 사용자로 등록합니다
  echo         ^(관리자 권한으로 실행하면 로그온 없이도 돌게 됩니다^)
  schtasks /create /tn "실거래가 갱신" /sc daily /st 04:37 /f /tr "\"%~dp0update.bat\"" >nul 2>&1
)
if errorlevel 1 (
  echo.
  echo   [!] 자동 실행 등록에 실패했습니다. 이 창을 관리자 권한으로 다시 실행해 보세요.
  echo       ^(수집 자체는 성공했습니다. '지금갱신.bat' 을 직접 눌러도 됩니다.^)
  pause
  exit /b 1
)

rem 불규칙하게 켜고 끄는 PC 를 위한 설정.
rem   StartWhenAvailable : 04:37 에 꺼져 있었으면 켜지는 즉시 그날 몫을 실행
rem   WakeToRun          : 절전이면 깨워서 실행
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=Get-ScheduledTask '실거래가 갱신'; $t.Settings.StartWhenAvailable=$true; $t.Settings.WakeToRun=$true; $t.Settings.ExecutionTimeLimit='PT2H'; Set-ScheduledTask -InputObject $t | Out-Null"
if errorlevel 1 echo         ^(놓친 실행 따라잡기 설정 실패 - 작업 스케줄러에서 직접 켜세요^)

echo.
echo   ============================================
echo    설치 완료
echo   ============================================
echo.
echo    매일 새벽 4시 37분에 자동으로 갱신합니다.
echo    그 시각에 PC 가 꺼져 있었으면, 다음에 켜질 때 그날 몫을 실행합니다.
echo    누가 컴퓨터를 켜든, 로그온을 하든 안 하든 상관없습니다.
echo.
echo    지금 바로 갱신    : 지금갱신.bat 더블클릭
echo    기록 보기         : daily.log
echo    자동 실행 끄기    : 자동실행끄기.bat
echo.
echo    결과 확인
echo      https://realestatetracking-89d37.web.app/
echo      https://df-columns.github.io/realestate-tracking/
echo.
pause
exit /b 0

:nopython
echo   [!] 파이썬이 설치돼 있지 않습니다.
echo.
echo       1. 방금 열린 페이지에서 'Download Python' 을 눌러 설치하세요.
echo       2. 설치 화면 맨 아래 "Add python.exe to PATH" 를 반드시 체크하세요.
echo       3. 설치가 끝나면 이 '설치.bat' 을 다시 더블클릭하세요.
echo.
start https://www.python.org/downloads/
pause
exit /b 1

:asktoken
echo   ---------------------------------------------
echo    GitHub 토큰이 필요합니다. 한 번만 만들면 됩니다.
echo   ---------------------------------------------
echo.
if %TRIES% GTR 1 goto skipopen
echo    방금 열린 페이지에서 이렇게 고르세요.
echo.
echo      Token name        : collector
echo      Repository access : Only select repositories
echo                          -^> realestate-tracking 선택
echo      Permissions       : Repository permissions 의 Contents 를
echo                          Read and write 로
echo.
echo    그리고 맨 아래 Generate token 을 누르세요.
echo.
start https://github.com/settings/personal-access-tokens/new
:skipopen
echo    나온 토큰 문자열을 복사해서 아래에 붙여넣고 Enter 를 누르세요.
echo    ^(붙여넣기는 이 창 안에서 마우스 오른쪽 클릭^)
echo.
set "TOKEN="
set /p TOKEN=   토큰 : 
rem 빈 입력일 때 asktoken 으로 되돌아가면 TRIES 를 건너뛰어 무한 반복이 된다.
rem 반드시 카운터가 있는 tokencheck 로 돌아가야 한다.
if not defined TOKEN goto tokencheck
>token.txt echo %TOKEN%
set "TOKEN="
echo.
echo    저장했습니다. 확인합니다...
echo.
goto tokencheck

:tokengiveup
echo   [!] 토큰을 확인할 수 없습니다.
echo.
echo       토큰은 github_pat_ 또는 ghp_ 로 시작하는 긴 문자열입니다.
echo       token.txt 를 메모장으로 직접 열어 붙여넣고 저장한 뒤
echo       이 '설치.bat' 을 다시 더블클릭해도 됩니다.
echo.
notepad token.txt
pause
exit /b 1
"""

DAILY_BAT = r"""@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=cp949:replace
set PYTHONUTF8=1

rem 이 작업은 SYSTEM 계정으로 돈다(아무도 로그온하지 않아도 실행되도록).
rem SYSTEM 의 PATH 에는 py 런처가 없으므로 설치 때 적어 둔 절대경로를 쓴다.
set PY=
if exist pyexe.txt set /p PY=<pyexe.txt
if defined PY set PY="%PY%"
if not defined PY ( py -3 -V >nul 2>&1 && set PY=py -3 )
if not defined PY ( python -V >nul 2>&1 && set PY=python )
if not defined PY (
  echo %DATE% %TIME% 파이썬을 찾을 수 없습니다>> daily.log
  exit /b 1
)

echo ==== %DATE% %TIME% 갱신 시작 ====>> daily.log
%PY% -u update.py --recent 3 --push >> daily.log 2>&1
if errorlevel 1 (
  echo ==== %DATE% %TIME% 실패 ====>> daily.log
  exit /b 1
)
echo ==== %DATE% %TIME% 완료 ====>> daily.log
exit /b 0
"""

RUNNOW_BAT = r"""@echo off
cd /d "%~dp0"
title 실거래가 지금 갱신
echo.
echo   지금 갱신합니다. 1~2분 걸립니다.
echo.
call "%~dp0update.bat"
if errorlevel 1 (
  echo.
  echo   [!] 실패했습니다. 아래는 기록의 마지막 부분입니다.
  echo.
  powershell -NoProfile -Command "Get-Content daily.log -Tail 25"
  pause
  exit /b 1
)
echo.
echo   완료. 웹사이트는 1~2분 뒤 반영됩니다.
echo.
powershell -NoProfile -Command "Get-Content daily.log -Tail 8"
echo.
pause
"""

STARTUP_BAT = r"""@echo off
rem 시작 폴더(shell:startup)에 이 파일의 '바로가기' 를 넣어 두면
rem PC 를 켤 때마다 한 번 갱신한다.
rem
rem  - 창을 띄우지 않는다. 결과는 daily.log 에 남는다.
rem  - 부팅 직후에는 네트워크가 아직 안 붙어 있을 수 있으므로 3분 기다린다.
rem  - 매일 04:37 작업을 대신하는 것이 아니라 보태는 것이다. PC 를 며칠 안 껐다
rem    켜면 로그온이 없으니 이건 안 돌고, 그때는 매일 작업이 받아 준다.

cd /d "%~dp0"

rem 최소화된 창으로 스스로를 다시 띄운 뒤 원래 창은 바로 닫는다
if not "%~1"=="min" (
  start "" /min cmd /c ""%~f0" min"
  exit /b 0
)

echo ==== %DATE% %TIME% 시작 시 갱신 - 네트워크 대기 ====>> daily.log
timeout /t 180 /nobreak >nul
call "%~dp0update.bat"
exit /b %errorlevel%
"""

STOP_BAT = r"""@echo off
schtasks /delete /tn "실거래가 갱신" /f
echo.
echo   자동 갱신을 껐습니다. 다시 켜려면 '설치.bat' 을 실행하세요.
pause
"""

TOKEN_TXT = """여기에 GitHub 토큰을 붙여넣고 이 줄을 지우세요.

만드는 곳
  https://github.com/settings/personal-access-tokens/new

설정
  Token name        : collector
  Repository access : Only select repositories -> realestate-tracking
  Permissions       : Contents = Read and write

이 토큰은 realestate-tracking 리포의 파일만 고칠 수 있습니다.
"""

README_TXT = """실거래가 수집기
================

이 폴더는 매일 국토부 실거래가를 받아 웹사이트를 갱신합니다.
국토부 API 가 해외 서버를 차단하기 때문에 한국에 있는 이 PC 에서 돌려야 합니다.


처음 한 번만
-------------
1. '설치.bat' 을 더블클릭합니다.
2. 안내에 따라 파이썬과 GitHub 토큰을 준비합니다.
   (필요할 때 알아서 페이지를 열어 줍니다)
3. 끝입니다. 매일 새벽 4시 37분에 저절로 돌아갑니다.


그 다음
-------
- 지금 바로 갱신하고 싶으면      '지금갱신.bat'
- 무슨 일이 있었는지 보려면      daily.log
- 자동 실행을 끄려면             '자동실행끄기.bat'
- PC 를 켤 때마다 갱신하려면     '시작할때갱신.bat' 의 바로가기를
                                 시작 폴더에 넣으세요.
                                 (Win+R -> shell:startup -> 바로가기 붙여넣기)


돌아가는 방식
-------------
이 PC 가 국토부에서 데이터를 받아 GitHub 에 올리면, GitHub 이 알아서
두 웹사이트를 갱신합니다. 이 PC 에는 웹사이트 열쇠가 없어도 됩니다.

  https://realestatetracking-89d37.web.app/
  https://df-columns.github.io/realestate-tracking/

며칠에서 몇 달 꺼져 있어도 켜지는 순간 빠진 기간을 알아서 메웁니다.
"""


FOR_CP949 = {"—": "-", "–": "-", "‘": "'", "’": "'",
             "“": '"', "”": '"', "…": "..."}


def wr(name, text, enc="utf-8-sig"):
    """CRLF 로 쓴다 — bat 은 LF 에서 goto 라벨이 깨진다.

    bat 은 cp949 로 쓴다. UTF-8 BOM 이 붙으면 cmd 가 첫 줄을 명령으로 읽어 에러를
    내고, BOM 없는 UTF-8 은 한글이 깨진다. 한국 윈도우 기본 코드페이지(949)로
    쓰면 둘 다 없다. txt 는 메모장이 잘 읽는 utf-8-sig.

    cp949 에 없는 활자(em dash 등)는 미리 바꾼다. errors='replace' 로 넘기면
    화면에 물음표가 찍히므로 여기서 정리하고, 남은 게 있으면 예외로 드러나게 둔다."""
    if enc == "cp949":
        for a, b in FOR_CP949.items():
            text = text.replace(a, b)
    with io.open(os.path.join(OUT, name), "w", encoding=enc, newline="\r\n") as f:
        f.write(text)


def zip_up(folder):
    """옮길 게 파일 하나가 되도록 묶는다. 캐시는 이미 gz 이라 더 줄지 않으니
    압축률보다 속도를 택한다."""
    zpath = folder + ".zip"
    if os.path.exists(zpath):
        os.remove(zpath)
    base = os.path.dirname(folder)
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for root, _d, files in os.walk(folder):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, base).replace(os.sep, "/"))
                n += 1
    return zpath, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true",
                    help="캐시를 빼서 작게 만든다 (상시 PC 첫 실행이 10년 전체를 받는다)")
    ap.add_argument("--no-zip", action="store_true", help="폴더만 만들고 zip 은 만들지 않는다")
    args = ap.parse_args()

    for f in FILES:
        if not os.path.exists(os.path.join(ROOT, f)):
            sys.exit("[!] %s 가 없습니다." % f)
    if not os.path.exists(os.path.join(ROOT, "data", "apt_data.json")):
        sys.exit("[!] data/apt_data.json 이 없습니다. update.py 를 먼저 실행하세요.")

    if os.path.isdir(OUT):
        shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(os.path.join(OUT, "data"), exist_ok=True)

    for f in FILES:
        shutil.copyfile(os.path.join(ROOT, f), os.path.join(OUT, f))
    shutil.copyfile(os.path.join(ROOT, "data", "apt_data.json"),
                    os.path.join(OUT, "data", "apt_data.json"))

    # 캐시를 같이 담아 상시 PC 에서 10년 전체 수집을 다시 하지 않게 한다
    src_cache = os.path.join(ROOT, "cache")
    n_cache = 0
    if not args.no_cache and os.path.isdir(src_cache):
        shutil.copytree(src_cache, os.path.join(OUT, "cache"))
        for _b, _d, fs in os.walk(os.path.join(OUT, "cache")):
            n_cache += len(fs)

    wr("설치.bat", INSTALL_BAT, "cp949")
    wr("update.bat", DAILY_BAT, "cp949")            # 스케줄러가 부른다 (ASCII 이름)
    wr("지금갱신.bat", RUNNOW_BAT, "cp949")
    wr("시작할때갱신.bat", STARTUP_BAT, "cp949")
    wr("자동실행끄기.bat", STOP_BAT, "cp949")
    wr("token.txt", TOKEN_TXT, "utf-8")
    wr("읽어보세요.txt", README_TXT)

    total = 0
    for base, _d, fs in os.walk(OUT):
        for f in fs:
            total += os.path.getsize(os.path.join(base, f))

    def mb(n):
        return n / 1048576.0

    print("dist/수집기/ — %.1f MB (캐시 파일 %s개)" % (mb(total), format(n_cache, ",")))

    target = OUT
    if not args.no_zip:
        zpath, n = zip_up(OUT)
        print("dist/수집기.zip — %.1f MB (파일 %s개 묶음)"
              % (mb(os.path.getsize(zpath)), format(n, ",")))
        target = zpath

    print()
    print("  1) 이걸 상시 켜두는 PC 로 옮기세요")
    print("     %s" % target)
    if not args.no_zip:
        print("     (옮긴 뒤 우클릭 → 압축 풀기)")
    print("  2) 그 PC 에서 '설치.bat' 더블클릭")
    if args.no_cache:
        print()
        print("  캐시를 뺐으므로 첫 실행이 10년 전체를 받습니다 (15~20분).")
    print()
    print("  apikey.txt 가 들어 있습니다. 외부에 공유하지 마세요.")


if __name__ == "__main__":
    main()
