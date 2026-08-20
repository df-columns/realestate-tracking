@echo off
rem 정기 갱신 — 작업 스케줄러에 등록해서 쓴다.
rem   최근 N개월 재수집 -> 캐시 전체로 10년 재집계 -> 검증 -> 커밋·푸시
rem 푸시하면 GitHub Pages 는 자동 갱신되고 deploy.yml 이 Firebase 에 올린다.
rem 검증에 걸리면 푸시하지 않고 data/apt_data.json 을 갱신 전으로 되돌린다.
rem 공백(휴가·PC 꺼짐)은 update.py 가 요청 창을 넓혀 스스로 메운다.

setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

rem 무인 실행이라 인증이 막히면 프롬프트를 띄우지 말고 즉시 실패해야 한다.
rem 그러지 않으면 git 이 입력을 기다리며 영원히 매달린다.
set GIT_TERMINAL_PROMPT=0
set GCM_INTERACTIVE=never

echo ==== %DATE% %TIME% 갱신 시작 ====>> daily.log
py -3 -u update.py --recent 3 --push >> daily.log 2>&1
if errorlevel 1 (
  echo ==== %DATE% %TIME% 실패 ^(exit %errorlevel%^) ====>> daily.log
  exit /b 1
)
echo ==== %DATE% %TIME% 완료 ====>> daily.log
exit /b 0
