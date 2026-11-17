<#
  비디오유즈 파이프라인 Windows 셋업 (scripts/setup.sh 의 Windows 판)

  무엇을 하나:
    0. 필수 도구 설치 (winget): Git, uv, Node.js LTS, ffmpeg, Python 3.12
    1. video-use 를 ~\Developer\video-use 에 clone (없으면)
    2. video-use venv 동기화 (Python 3.12) + edge-tts 추가
    3. .env 템플릿 (ElevenLabs 선택사항)
    4. .claude\skills\video-use 연결 (junction — 관리자 권한 불필요)
    5. footage\ 폴더, .edit-mode (기본 standalone)
    6. hyperframes npm install + 보조 스킬
    7. 동작 검증

  멱등(idempotent) — 여러 번 실행해도 안전.

  실행 (repo 루트에서):
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -EditMode nle:premiere

  참고: uv 가 내려받는 Python 은 일부 PC에서 설치 오류가 나서
        ("Missing expected target directory for Python minor version link"),
        winget 으로 받은 Python 3.12 를 쓴다.
#>
param(
  [string]$EditMode = "standalone",
  [string]$VideoUseDir = (Join-Path $HOME "Developer\video-use")
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Helpers = Join-Path $RepoRoot ".claude\skills\motion-pipeline\helpers"

function Step($msg) { Write-Host ""; Write-Host "── $msg ──" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "✓ $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "! $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "✗ $msg" -ForegroundColor Red; exit 1 }

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
}

function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Invoke-Native {
  param([string]$What, [scriptblock]$Block)
  & $Block
  if ($LASTEXITCODE -ne 0) { Fail "$What 실패 (exit $LASTEXITCODE)" }
}

function Find-Python312 {
  $cands = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Join-Path $env:ProgramFiles "Python312\python.exe")
  )
  foreach ($c in $cands) { if (Test-Path $c) { return $c } }
  if (Has "py") {
    $p = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
  }
  return $null
}

# ─────────────────────────────────────────────────────────────
Step "0. 필수 도구 (winget)"
# ─────────────────────────────────────────────────────────────
if (-not (Has "winget")) { Fail "winget 없음 — Microsoft Store 에서 '앱 설치 관리자'를 설치/업데이트하세요" }
Refresh-Path

$tools = @(
  @{ Cmd = "git";    Id = "Git.Git" },
  @{ Cmd = "uv";     Id = "astral-sh.uv" },
  @{ Cmd = "node";   Id = "OpenJS.NodeJS.LTS" },
  @{ Cmd = "ffmpeg"; Id = "Gyan.FFmpeg" }
)
foreach ($t in $tools) {
  if (Has $t.Cmd) { Ok "$($t.Cmd) 있음"; continue }
  Write-Host "  $($t.Id) 설치 중... (관리자 권한 창이 뜨면 '예')"
  winget install --id $t.Id -e --silent --accept-source-agreements --accept-package-agreements
  Refresh-Path
  if (-not (Has $t.Cmd)) { Fail "$($t.Cmd) 설치 후에도 찾을 수 없음 — 새 PowerShell 창에서 다시 실행하세요" }
  Ok "$($t.Cmd) 설치됨"
}

$py = Find-Python312
if (-not $py) {
  Write-Host "  Python 3.12 설치 중..."
  winget install --id Python.Python.3.12 -e --silent --scope user --accept-source-agreements --accept-package-agreements
  $py = Find-Python312
  if (-not $py) { Fail "Python 3.12 를 찾을 수 없음" }
}
Ok "Python 3.12 → $py"

$nodeMajor = [int]((& node -p "process.versions.node.split('.')[0]").Trim())
if ($nodeMajor -lt 22) { Fail "Node 22+ 필요 (현재 $(node -v)). winget upgrade OpenJS.NodeJS.LTS" }
Ok "node $(node -v), $(& git --version), $(& uv --version)"

# ─────────────────────────────────────────────────────────────
Step "1. video-use clone"
# ─────────────────────────────────────────────────────────────
if (Test-Path (Join-Path $VideoUseDir ".git")) {
  Ok "이미 존재 → $VideoUseDir"
} else {
  New-Item -ItemType Directory -Force (Split-Path $VideoUseDir) | Out-Null
  Invoke-Native "video-use clone" { git clone https://github.com/browser-use/video-use $VideoUseDir }
  Ok "clone 완료 → $VideoUseDir"
}

# ─────────────────────────────────────────────────────────────
Step "2. video-use 의존성 (uv sync) + edge-tts"
# ─────────────────────────────────────────────────────────────
$env:UV_PYTHON_DOWNLOADS = "never"
$venvDir = Join-Path $VideoUseDir ".venv"
if (Test-Path $venvDir) {
  # 일부 패키지 폴더가 읽기 전용으로 남아 uv 가 갱신/삭제 못 하는 경우가 있음 (os error 5)
  attrib -R "$venvDir\*" /S /D | Out-Null
}
Push-Location $VideoUseDir
try {
  # --inexact: keep edge-tts etc. (not in video-use's lockfile) instead of uninstalling them every run
  Invoke-Native "uv sync" { uv sync --inexact --python $py }
  Invoke-Native "edge-tts 설치" { uv pip install -r (Join-Path $Helpers "requirements.txt") }
} finally { Pop-Location }
$venvPy = Join-Path $VideoUseDir ".venv\Scripts\python.exe"
Ok "venv → $venvPy"

# ─────────────────────────────────────────────────────────────
Step "3. .env 템플릿 (ElevenLabs Scribe 선택사항)"
# ─────────────────────────────────────────────────────────────
$envFile = Join-Path $VideoUseDir ".env"
if (Test-Path $envFile) {
  Ok ".env 이미 존재"
} elseif (Test-Path (Join-Path $VideoUseDir ".env.example")) {
  Copy-Item (Join-Path $VideoUseDir ".env.example") $envFile
  Warn "기본 워크플로우는 키 0개. ElevenLabs 가 필요할 때만 $envFile 에 ELEVENLABS_API_KEY 입력"
}

# ─────────────────────────────────────────────────────────────
Step "4. .claude\skills\video-use 연결"
# ─────────────────────────────────────────────────────────────
$link = Join-Path $RepoRoot ".claude\skills\video-use"
$item = Get-Item $link -Force -ErrorAction SilentlyContinue
if ($item -and $item.LinkType) {
  $target = @($item.Target)[0]
  if ($target -and ((Resolve-Path $target -ErrorAction SilentlyContinue).Path -eq (Resolve-Path $VideoUseDir).Path)) {
    Ok "이미 연결됨 → $VideoUseDir"
  } else {
    $item.Delete()
    $item = $null
  }
} elseif ($item) {
  Fail "$link 가 링크가 아닌 실제 폴더로 존재 — 확인 후 지우고 다시 실행하세요"
}
if (-not $item) {
  New-Item -ItemType Directory -Force (Split-Path $link) | Out-Null
  New-Item -ItemType Junction -Path $link -Target $VideoUseDir | Out-Null
  Ok "$link → $VideoUseDir"
}

# ─────────────────────────────────────────────────────────────
Step "5. footage\ 와 .edit-mode"
# ─────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force (Join-Path $RepoRoot "footage") | Out-Null
Ok "footage\"
$modeFile = Join-Path $RepoRoot ".edit-mode"
if (Test-Path $modeFile) {
  Ok ".edit-mode 이미 존재 → $((Get-Content $modeFile -TotalCount 1).Trim())"
} else {
  [IO.File]::WriteAllText($modeFile, "$EditMode`n")
  Ok ".edit-mode → $EditMode"
}

# ─────────────────────────────────────────────────────────────
Step "6. hyperframes"
# ─────────────────────────────────────────────────────────────
$hf = Join-Path $RepoRoot "hyperframes"
if (Test-Path $hf) {
  Push-Location $hf
  try {
    npm install --no-fund --no-audit
    if ($LASTEXITCODE -eq 0) { Ok "hyperframes npm install" } else { Warn "npm install 실패 — 인터넷 연결 확인" }
    if (Test-Path (Join-Path $hf ".agents\skills\hyperframes")) {
      Ok "hyperframes 보조 스킬 이미 설치됨"
    } else {
      npx --yes skills@latest add heygen-com/hyperframes --yes
      if ($LASTEXITCODE -eq 0) { Ok "hyperframes 보조 스킬 설치 (heygen-com/hyperframes, Apache 2.0)" }
      else { Warn "hyperframes 스킬 설치 실패 — 인터넷 연결 후 재실행" }
    }
  } finally { Pop-Location }
} else {
  Warn "hyperframes\ 폴더 없음"
}

# ─────────────────────────────────────────────────────────────
Step "7. 동작 검증"
# ─────────────────────────────────────────────────────────────
$checks = @(
  @{ Name = "edge-tts import";   Args = @("-c", "import edge_tts") },
  @{ Name = "batch_tts.py";      Args = @((Join-Path $Helpers "batch_tts.py"), "--help") },
  @{ Name = "edl_to_fcpxml.py";  Args = @((Join-Path $Helpers "edl_to_fcpxml.py"), "--help") },
  @{ Name = "edl_to_vrew.py";    Args = @((Join-Path $Helpers "edl_to_vrew.py"), "--help") }
)
foreach ($c in $checks) {
  & $venvPy @($c.Args) | Out-Null
  if ($LASTEXITCODE -eq 0) { Ok "$($c.Name) OK" } else { Fail "$($c.Name) 실패" }
}
$ff = (& ffmpeg -version | Select-Object -First 1)
Ok "$ff"
$hfv = & npx --yes hyperframes@latest --version
if ($LASTEXITCODE -eq 0) { Ok "hyperframes CLI $hfv" } else { Warn "hyperframes CLI 확인 실패 (첫 호출은 인터넷 필요)" }

# ─────────────────────────────────────────────────────────────
Step "셋업 완료"
# ─────────────────────────────────────────────────────────────
Write-Host @"

다음 단계 (Claude Code 에서 이 폴더를 열고):

  Mode B (스크립트 → 영상)
    "이 스크립트로 30초 한국어 릴스 만들어줘. SunHi 보이스로."

  Mode A (풋티지 편집)
    1) 원본 영상을 footage\ 에 넣기
    2) "footage 영상으로 인터뷰 컷 편집해줘. 자막 + 로어써드."

  결과: footage\edit\ 에 final.mp4, timeline.vrew (Vrew), timeline.fcpxml, timeline.xml

  * 새로 설치된 도구는 새 터미널 / Claude 재시작 후 인식됩니다.
  * 첫 transcribe 는 Whisper 모델(~3GB) 다운로드로 5–15분 걸립니다.
"@
