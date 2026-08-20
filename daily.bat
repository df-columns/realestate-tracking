@echo off
rem 정기 갱신 — 작업 스케줄러에 등록해서 쓴다.
rem   최근 3개월 재수집 -> 캐시 전체로 10년 재집계 -> 검증 -> 커밋·푸시
rem 푸시하면 GitHub Pages 는 자동 갱신되고 deploy.yml 이 Firebase 에 올린다.
rem 검증에 걸리면 푸시하지 않고 data/apt_data.json 을 갱신 전으로 되돌린다.

setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ==== %DATE% %TIME% 갱신 시작 ====>> daily.log
py -3 -u update.py --recent 3 --push >> daily.log 2>&1
if errorlevel 1 (
  echo ==== %DATE% %TIME% 실패 ====>> daily.log
  exit /b 1
)
echo ==== %DATE% %TIME% 완료 ====>> daily.log
exit /b 0
