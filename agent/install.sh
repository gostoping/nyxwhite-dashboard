#!/bin/bash
# NYX WHITE DIRECTOR 에이전트 설치 (맥)
# 대시보드 → 에이전트 탭에서 복사한 한 줄 명령으로 실행됩니다. 관리자 비밀번호는 필요 없습니다.
#   curl -fsSL <저장소>/install.sh | bash -s -- <URL> <KEY> <TOKEN>
set -euo pipefail

URL="${1:?Project URL이 빠졌습니다}"
KEY="${2:?공개용 키가 빠졌습니다}"
TOKEN="${3:?에이전트 토큰이 빠졌습니다}"
URL="${URL%/}"
DIR="$HOME/NYX/agent"
LABEL="com.nyxwhite.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "▶ NYX 에이전트 설치를 시작합니다 ($DIR)"
mkdir -p "$DIR" "$HOME/Library/LaunchAgents"

# 1) 실행 도구(uv): 파이썬을 관리자 권한 없이 설치·실행
UV="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
if [ -z "$UV" ]; then
  echo "▶ 실행 도구(uv)를 설치합니다"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  UV="$HOME/.local/bin/uv"
fi
echo "▶ 파이썬 준비"
"$UV" python install 3.12 >/dev/null

# 2) 에이전트 프로그램과 설정
echo "▶ 에이전트 프로그램 내려받기"
GH="https://raw.githubusercontent.com/gostoping/nyxwhite-dashboard/main/agent/agent.py"
curl -fsSL "$GH" -o "$DIR/agent.py" 2>/dev/null || curl -fsSL "$URL/storage/v1/object/public/agent/agent.py?t=$(date +%s)" -o "$DIR/agent.py"
cat > "$DIR/config.json" <<EOF
{"url": "$URL", "key": "$KEY", "token": "$TOKEN"}
EOF
chmod 600 "$DIR/config.json"

# 3) 로그인하면 자동 시작, 꺼지면 자동 재시작
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string><string>run</string><string>--python</string><string>3.12</string>
    <string>--no-project</string><string>$DIR/agent.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DIR/agent.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/agent.out.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo ""
echo "✅ 설치 완료. 1분 안에 대시보드 에이전트 탭에서 '온라인'으로 바뀝니다."
echo "   맥북은 전원 어댑터를 연결하고 덮개를 열어 두세요(덮으면 잠듭니다)."
echo "   기록 파일: $DIR/agent.log"
