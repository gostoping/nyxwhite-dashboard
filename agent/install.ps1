# NYX WHITE DIRECTOR 에이전트 설치 (Windows)
# 대시보드 → 에이전트 탭에서 복사한 한 줄 명령을 PowerShell에 붙여 넣으면 실행됩니다. 관리자 권한은 필요 없습니다.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$URL = $env:NYX_URL; $KEY = $env:NYX_KEY; $TOKEN = $env:NYX_TOKEN
if (-not $URL -or -not $KEY -or -not $TOKEN) { throw '설치 정보가 빠졌습니다. 대시보드에서 설치 명령을 다시 복사하세요.' }
$URL = $URL.TrimEnd('/')
$Dir = Join-Path $env:USERPROFILE 'NYX\agent'
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Write-Host "NYX 에이전트 설치를 시작합니다 ($Dir)"

# 0) 이미 돌고 있는 에이전트가 있으면 멈춤
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like '*NYX\agent*' -and $_.ProcessId -ne $PID } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# 1) 실행 도구(uv): 파이썬을 관리자 권한 없이 설치·실행
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
if (-not (Test-Path $uv)) {
  Write-Host '실행 도구(uv)를 설치합니다'
  $env:UV_NO_MODIFY_PATH = '1'
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}
Write-Host '파이썬 준비'
& $uv python install 3.12 | Out-Null

# 2) 에이전트 프로그램과 설정
Write-Host '에이전트 프로그램 내려받기'
try {
  Invoke-WebRequest 'https://raw.githubusercontent.com/gostoping/nyxwhite-dashboard/main/agent/agent.py' -OutFile (Join-Path $Dir 'agent.py') -UseBasicParsing
} catch {
  Invoke-WebRequest "$URL/storage/v1/object/public/agent/agent.py?t=$([DateTimeOffset]::Now.ToUnixTimeSeconds())" -OutFile (Join-Path $Dir 'agent.py') -UseBasicParsing
}
@{ url = $URL; key = $KEY; token = $TOKEN } | ConvertTo-Json | Set-Content -Path (Join-Path $Dir 'config.json') -Encoding UTF8

# 3) 꺼지면 다시 켜는 실행기 + 로그인하면 자동 시작(창 없이)
$runner = Join-Path $Dir 'runner.ps1'
@"
Set-Location '$Dir'
`$env:PYTHONIOENCODING = 'utf-8'
while (`$true) {
  & '$uv' run --python 3.12 --no-project '$Dir\agent.py' *>> '$Dir\agent.out.log'
  Start-Sleep -Seconds 10
}
"@ | Set-Content -Path $runner -Encoding UTF8
$vbs = Join-Path ([Environment]::GetFolderPath('Startup')) 'NYX-agent.vbs'
"CreateObject(""Wscript.Shell"").Run ""powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """"$runner"""""", 0, False" |
  Set-Content -Path $vbs -Encoding Unicode

# 4) 전원 연결 시 절전·최대 절전 끄기 (화면 끄기는 그대로)
try { powercfg /change standby-timeout-ac 0 | Out-Null; powercfg /change hibernate-timeout-ac 0 | Out-Null } catch {}

Start-Process wscript.exe -ArgumentList "`"$vbs`""
Write-Host ''
Write-Host '설치 완료. 1분 안에 대시보드 에이전트 탭에서 온라인으로 바뀝니다.'
Write-Host "기록 파일: $Dir\agent.log"
Write-Host '참고: Windows 업데이트의 사용 시간을 지정해 두면 마음대로 재부팅하지 않습니다.'
