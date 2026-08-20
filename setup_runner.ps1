# 상시 켜두는 PC 에 수집기를 설치한다. 그 PC 에서 한 번만 실행하면 된다.
#
#   powershell -ExecutionPolicy Bypass -File setup_runner.ps1
#
# 하는 일
#   1) python · git 확인
#   2) 배포키(SSH) 생성 — 이 PC 만 이 리포에 푸시할 수 있게 하는 열쇠
#   3) 리포 clone (또는 기존 것 SSH 로 전환)
#   4) 의존성 설치
#   5) apikey.txt 확인
#   6) 국토부 API 접속 시험 1건
#   7) 첫 수집 (캐시가 없으면 10년 전체)
#   8) 작업 스케줄러 등록
#
# 왜 배포키인가
#   무인 실행에서는 Windows 자격 증명 관리자를 못 읽을 수 있다. 배포키는 파일이라
#   로그오프 상태에서도 동작하고, 이 리포 하나에만 권한이 있어 계정 전체가 걸리지 않는다.

$ErrorActionPreference = "Stop"

$REPO_SSH  = "git@github.com:df-columns/realestate-tracking.git"
$REPO_WEB  = "https://github.com/df-columns/realestate-tracking"
$DIR       = Join-Path $HOME "realestate-tracking"
$KEY       = Join-Path $HOME ".ssh\rtms_deploy"
$TASK      = "실거래가 갱신"
$TASK_TIME = "04:37"          # 상시 켜둔 PC 라 새벽이 가장 한가하다

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "[!] $msg" -ForegroundColor Red; exit 1 }

# ── 1. 도구 확인 ────────────────────────────────────────────────
Step 1 "python · git 확인"
$py = $null
foreach ($c in @("py", "python")) {
  $found = Get-Command $c -ErrorAction SilentlyContinue
  if ($found) { $py = $found.Source; break }
}
if (-not $py) { Fail "python 이 없습니다. https://www.python.org/downloads/ 에서 설치하고 (Add to PATH 체크) 다시 실행하세요." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Fail "git 이 없습니다. https://git-scm.com/download/win 에서 설치하고 다시 실행하세요."
}
if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {
  Fail "ssh-keygen 이 없습니다. Windows 설정 → 앱 → 선택적 기능에서 'OpenSSH 클라이언트' 를 설치하세요."
}
Write-Host "    python: $py"
Write-Host "    git   : $((Get-Command git).Source)"

# ── 2. 배포키 ───────────────────────────────────────────────────
Step 2 "배포키 준비"
$sshDir = Split-Path $KEY
if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Force $sshDir | Out-Null }
if (Test-Path $KEY) {
  Write-Host "    이미 있음: $KEY"
} else {
  ssh-keygen -t ed25519 -f $KEY -N '""' -C "rtms-collector" | Out-Null
  Write-Host "    생성: $KEY"
}
$pub = (Get-Content "$KEY.pub" -Raw).Trim()

Write-Host ""
Write-Host "  아래 공개키를 리포의 배포키로 등록하세요 (쓰기 권한 체크 필수)" -ForegroundColor Yellow
Write-Host "  $REPO_WEB/settings/keys/new" -ForegroundColor Yellow
Write-Host ""
Write-Host $pub
Write-Host ""
Write-Host "  Title 은 아무거나(예: collector-pc), 'Allow write access' 를 반드시 켜세요."
Read-Host "  등록을 마쳤으면 Enter"

# ── 3. clone ────────────────────────────────────────────────────
Step 3 "리포 준비"
$sshCmd = "ssh -i `"$KEY`" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
if (Test-Path (Join-Path $DIR ".git")) {
  Write-Host "    이미 있음: $DIR"
  Push-Location $DIR
  git remote set-url origin $REPO_SSH
  git config core.sshCommand $sshCmd
  git fetch origin
  git checkout main
  git reset --hard origin/main
  Pop-Location
} else {
  $env:GIT_SSH_COMMAND = $sshCmd
  git clone $REPO_SSH $DIR
  if ($LASTEXITCODE -ne 0) { Fail "clone 실패 — 배포키 등록을 확인하세요." }
  Push-Location $DIR
  git config core.sshCommand $sshCmd
  Pop-Location
}
Push-Location $DIR

# 푸시가 되는지 지금 확인한다. 나중에 무인 실행에서 실패하면 알아채기 어렵다.
git push --dry-run origin main
if ($LASTEXITCODE -ne 0) { Fail "푸시 권한이 없습니다. 배포키에 'Allow write access' 를 켰는지 확인하세요." }
Write-Host "    푸시 권한 확인됨"

# ── 4. 의존성 ───────────────────────────────────────────────────
Step 4 "의존성 설치"
& $py -3 -m pip install --quiet --upgrade pip
& $py -3 -m pip install --quiet -r requirements.txt
Write-Host "    requests · truststore 설치됨"

# ── 5. 서비스 키 ────────────────────────────────────────────────
Step 5 "공공데이터포털 서비스 키"
if (Test-Path "apikey.txt") {
  Write-Host "    이미 있음: $DIR\apikey.txt"
} else {
  Write-Host "    apikey.txt 가 없습니다 (리포에 올라가지 않는 파일입니다)."
  $k = Read-Host "    일반 인증키(Decoding) 를 붙여넣고 Enter"
  if (-not $k.Trim()) { Fail "키가 비었습니다." }
  [IO.File]::WriteAllText((Join-Path $DIR "apikey.txt"), $k.Trim(), (New-Object Text.UTF8Encoding $false))
  Write-Host "    저장됨"
}

# ── 6. API 접속 시험 ────────────────────────────────────────────
Step 6 "국토부 API 접속 시험"
$probe = @'
import io, sys, requests
try:
    import truststore; truststore.inject_into_ssl()
except Exception:
    pass
key = io.open("apikey.txt", encoding="utf-8").read().strip()
r = requests.get(
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    params={"serviceKey": key, "LAWD_CD": "11680", "DEAL_YMD": "202607", "numOfRows": 3},
    timeout=30)
print("  HTTP", r.status_code, "/", len(r.text), "bytes")
if "SERVICE_KEY_IS_NOT_REGISTERED" in r.text:
    sys.exit("  키가 아직 활성화되지 않았습니다 (신청 후 1~2시간).")
if "<item>" not in r.text:
    sys.exit("  응답에 거래 항목이 없습니다:\n" + r.text[:300])
print("  정상 — 이 PC 에서 수집 가능합니다.")
'@
$probe | & $py -3 -
if ($LASTEXITCODE -ne 0) { Fail "API 접속 시험 실패. 위 메시지를 확인하세요." }

# ── 7. 첫 수집 ──────────────────────────────────────────────────
Step 7 "첫 수집"
$cacheCount = 0
if (Test-Path "cache") {
  $cacheCount = (Get-ChildItem cache -Recurse -Filter *.json.gz -ErrorAction SilentlyContinue | Measure-Object).Count
}
Write-Host "    캐시 파일 $cacheCount 개"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
if ($cacheCount -lt 5000) {
  Write-Host "    캐시가 없어 10년 전체를 받습니다 (약 7,200요청 · 15~20분). 일일 한도 안입니다."
  & $py -3 -u update.py --full --push
} else {
  & $py -3 -u update.py --recent 3 --push
}
if ($LASTEXITCODE -ne 0) { Fail "첫 수집 실패. 위 로그를 확인하세요." }

# ── 8. 스케줄러 ─────────────────────────────────────────────────
Step 8 "작업 스케줄러 등록"
$bat = Join-Path $DIR "daily.bat"
schtasks /create /tn $TASK /sc daily /st $TASK_TIME /f /tr "`"$bat`"" | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "스케줄러 등록 실패 (관리자 권한이 필요할 수 있습니다)." }
Write-Host "    매일 $TASK_TIME 에 실행"

Pop-Location
Write-Host ""
Write-Host "설치 완료." -ForegroundColor Green
Write-Host "  폴더      $DIR"
Write-Host "  로그      $DIR\daily.log"
Write-Host "  즉시 실행  schtasks /run /tn `"$TASK`""
Write-Host "  해제      schtasks /delete /tn `"$TASK`" /f"
Write-Host ""
Write-Host "  결과 확인  https://realestatetracking-89d37.web.app/"
Write-Host "            https://df-columns.github.io/realestate-tracking/"
