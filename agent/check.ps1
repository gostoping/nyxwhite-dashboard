# NYX 서버 점검 (읽기만 함, 아무것도 바꾸지 않음)
$ok = '[정상]'; $warn = '[확인]'; $bad = '[문제]'
function Say($tag, $msg) { Write-Host ("{0} {1}" -f $tag, $msg) }
Write-Host "`n===== NYX 집 윈도우 서버 점검 =====`n"

# 1. 에이전트 설치
$dir = Join-Path $env:USERPROFILE 'NYX\agent'
if (Test-Path (Join-Path $dir 'agent.py')) {
  $v = (Select-String -Path (Join-Path $dir 'agent.py') -Pattern '^VERSION = "(.+)"' | Select-Object -First 1).Matches.Groups[1].Value
  Say $ok "에이전트 설치됨: $dir (v$v)"
  if (Test-Path (Join-Path $dir 'config.json')) { Say $ok '연결 설정(config.json) 있음' } else { Say $bad '연결 설정(config.json) 없음 → 대시보드에서 설치 명령을 다시 받아 실행' }
} else { Say $bad "에이전트가 설치되지 않음 → 대시보드 에이전트 탭 → '집 윈도우 (서버)' → 설치 명령 실행" }

# 2. 실행 중인지
$p = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*NYX\agent*' }
if ($p) { Say $ok ("에이전트 실행 중 (프로세스 {0}개)" -f @($p).Count) } else { Say $bad '에이전트가 실행 중이 아님 → 다시 로그인하거나 설치 명령을 다시 실행' }

# 3. 자동 시작
$vbs = Join-Path ([Environment]::GetFolderPath('Startup')) 'NYX-agent.vbs'
if (Test-Path $vbs) { Say $ok '로그인하면 자동 시작됨' } else { Say $bad '자동 시작 항목 없음 → 설치 명령을 다시 실행' }

# 4. 전원 (전원 연결 상태 기준)
function AcVal($sub, $set) { $o = powercfg /query SCHEME_CURRENT $sub $set 2>$null; foreach ($l in $o) { if ($l -match 'AC.*0x([0-9a-fA-F]+)') { return [Convert]::ToInt32($Matches[1], 16) } }; return $null }
$sleep = AcVal SUB_SLEEP STANDBYIDLE; $hib = AcVal SUB_SLEEP HIBERNATEIDLE; $lid = AcVal SUB_BUTTONS LIDACTION
if ($sleep -eq 0) { Say $ok '절전(잠자기): 안 함' } else { Say $bad ("절전이 {0}분 뒤 켜짐 → 설정 > 시스템 > 전원에서 '안 함'" -f [math]::Round($sleep / 60)) }
if ($hib -eq 0 -or $hib -eq $null) { Say $ok '최대 절전: 안 함' } else { Say $bad ("최대 절전이 {0}분 뒤 켜짐" -f [math]::Round($hib / 60)) }
if ($lid -ne $null) { if ($lid -eq 0) { Say $ok '덮개를 닫아도: 아무것도 안 함' } else { Say $warn '노트북 덮개를 닫으면 잠듦 → 제어판 > 전원 옵션 > 덮개를 닫으면(전원 연결 시) "아무 것도 안 함"' } }

# 5. 자동 로그인 (재부팅 후 에이전트·OneDrive가 알아서 켜지려면 필요)
$wl = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction SilentlyContinue
if ($wl.AutoAdminLogon -eq '1') { Say $ok '자동 로그인 켜짐' } else { Say $warn '자동 로그인 꺼짐 → 정전·업데이트로 재부팅되면 로그인할 때까지 일하지 않음 (Win+R → netplwiz 또는 Sysinternals Autologon)' }

# 6. OneDrive 보관 폴더
if ($env:OneDrive) {
  Say $ok "OneDrive 폴더: $env:OneDrive"
  $nyx = Join-Path $env:OneDrive 'NYX'
  if (Test-Path $nyx) { Say $ok "보관 폴더 있음: $nyx" } else { Say $warn "보관 폴더는 첫 영상이 들어올 때 자동으로 생김: $nyx" }
  $od = Get-Process OneDrive -ErrorAction SilentlyContinue
  if ($od) { Say $ok 'OneDrive 실행 중(자동 백업)' } else { Say $bad 'OneDrive가 실행 중이 아님 → 시작 메뉴에서 OneDrive 실행·로그인' }
  $drv = (Get-Item $env:OneDrive).PSDrive
  $free = [math]::Round($drv.Free / 1GB, 1)
  if ($free -gt 100) { Say $ok "디스크 남은 공간: ${free}GB" } else { Say $warn "디스크 남은 공간 ${free}GB → 영상이 쌓이면 부족할 수 있음" }
} else { Say $bad 'OneDrive가 설정되지 않음 → OneDrive 로그인 후 다시 점검 (안 하면 사용자 폴더\NYX에 저장되고 백업 안 됨)' }

# 7. 인터넷 연결
foreach ($u in @('https://raw.githubusercontent.com', 'https://api.higgsfield.ai', 'https://api.anthropic.com')) {
  try { Invoke-WebRequest -Uri $u -Method Head -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop | Out-Null; Say $ok "연결됨: $u" }
  catch { if ($_.Exception.Response) { Say $ok "연결됨: $u" } else { Say $bad "연결 안 됨: $u" } }
}

# 8. 영상 도구
if (Test-Path (Join-Path $dir 'ffmpeg.json')) { Say $ok '영상 도구(ffmpeg) 준비됨' } else { Say $warn '영상 도구(ffmpeg)는 첫 영상 작업 때 자동으로 준비됨(몇 분)' }

Write-Host "`n[문제]·[확인] 줄만 고치면 됩니다. 이 화면을 그대로 Claude에게 보내 주셔도 됩니다.`n"
