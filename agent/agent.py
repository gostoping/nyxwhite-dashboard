#!/usr/bin/env python3
"""NYX WHITE DIRECTOR 에이전트

Supabase(중앙 기록 창고)만 바라보고 일합니다.
- 15초마다 "살아 있음" 신호를 보내고, 대시보드에서 누른 명령(연결 시험·재시작·업데이트)을 받아 처리합니다.
- 일시정지가 아니면 작업 대기열에서 이 에이전트가 할 수 있는 일을 가져가 처리합니다.
- 컴퓨터가 잠들지 않게 막습니다(화면은 꺼져도 됨).
표준 라이브러리만 씁니다. 설정은 같은 폴더의 config.json(설치할 때 자동 생성)에 있습니다.
"""
VERSION = "1.0.0"

import json
import os
import platform
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime

try:  # 콘솔 문자 코드가 달라도 기록 때문에 멈추지 않게
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HOME = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HOME, "config.json"), encoding="utf-8-sig") as f:
    CFG = json.load(f)
URL = CFG["url"].rstrip("/")
KEY = CFG["key"]
TOKEN = CFG["token"]
PLATFORM = "mac" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"
BEAT_SECONDS = int(CFG.get("beat_seconds", 15))
LOG_PATH = os.path.join(HOME, "agent.log")
STARTED = datetime.now().isoformat(timespec="seconds")

# 이 에이전트가 처리할 수 있는 작업 종류. 기능이 늘면 여기에 추가됩니다.
HANDLERS = {}


def job(name):
    def wrap(fn):
        HANDLERS[name] = fn
        return fn
    return wrap


# ------------------------------------------------------------------ 기록
def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 2_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ 창고 연결
DEVICE_ID = None


def rpc(fn, **args):
    headers = {"apikey": KEY, "Content-Type": "application/json", "Accept": "application/json"}
    if not KEY.startswith("sb_"):  # 예전 형식(anon JWT) 키는 Authorization에도 넣는다
        headers["Authorization"] = "Bearer " + KEY
    if DEVICE_ID:
        headers["x-device-id"] = DEVICE_ID
    req = urllib.request.Request(f"{URL}/rest/v1/rpc/{fn}", data=json.dumps(args).encode("utf-8"),
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except Exception:
            pass
        raise RuntimeError(f"{fn}: {detail}") from None
    return json.loads(body) if body else None


def download(path):
    with urllib.request.urlopen(f"{URL}/storage/v1/object/public/agent/{path}?t={int(time.time())}", timeout=60) as r:
        return r.read()


# ------------------------------------------------------------------ 잠자기 방지
def keep_awake():
    try:
        if PLATFORM == "mac":
            # -i 유휴 잠자기, -m 디스크, -s 전원 연결 시 시스템 잠자기를 막는다. 화면(-d)은 꺼져도 됨.
            subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif PLATFORM == "windows":
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        log("잠자기 방지 켜짐 (화면은 꺼질 수 있음)")
    except Exception as e:
        log(f"잠자기 방지 실패: {e}")


# ------------------------------------------------------------------ 대시보드 명령
class Restart(Exception):
    pass


def handle_command(cmd):
    cid, name = cmd["id"], cmd["command"]
    log(f"명령 받음: {name}")
    try:
        if name == "ping":
            rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=True,
                p_result=f"응답 정상 · {socket.gethostname()} · {platform.system()} {platform.release()} · v{VERSION}")
        elif name == "restart":
            rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=True, p_result="재시작합니다")
            raise Restart()
        elif name == "update":
            code = download("agent.py")
            text = code.decode("utf-8")
            compile(text, "agent.py", "exec")  # 문법이 깨진 파일이면 여기서 멈추고 기존 버전 유지
            new_ver = next((l.split("=")[1].strip().strip('"') for l in text.splitlines() if l.startswith("VERSION =")), "?")
            if new_ver == VERSION:
                rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=True, p_result=f"이미 최신 버전입니다 (v{VERSION})")
                return
            tmp = os.path.join(HOME, "agent.py.new")
            with open(tmp, "wb") as f:
                f.write(code)
            os.replace(tmp, os.path.join(HOME, "agent.py"))
            rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=True, p_result=f"v{VERSION} → v{new_ver} 업데이트, 재시작합니다")
            raise Restart()
        else:
            rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=False, p_result=f"모르는 명령: {name}")
    except Restart:
        raise
    except Exception as e:
        log(f"명령 실패: {name}: {e}")
        rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=False, p_result=str(e)[:500])


# ------------------------------------------------------------------ 작업
@job("ping")
def do_ping(j):
    return f"연결 시험 완료 ({socket.gethostname()})"


def run_job(j):
    jid, jtype = j["id"], j["type"]
    log(f"작업 시작: {jtype} ({jid})")
    try:
        HANDLERS[jtype](j)
        rpc("agent_finish_job", p_token=TOKEN, p_job=jid, p_ok=True, p_error=None)
        log(f"작업 완료: {jtype}")
    except Exception as e:
        log(f"작업 실패: {jtype}: {e}\n{traceback.format_exc()}")
        rpc("agent_finish_job", p_token=TOKEN, p_job=jid, p_ok=False, p_error=str(e)[:1000])


# ------------------------------------------------------------------ 본체
def main():
    global DEVICE_ID
    log(f"NYX 에이전트 v{VERSION} 시작 ({PLATFORM}, {socket.gethostname()})")
    keep_awake()
    last_error, paused = None, False
    info = {"hostname": socket.gethostname(), "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(), "started_at": STARTED, "jobs": sorted(HANDLERS)}
    while True:
        busy = False
        try:
            hb = rpc("agent_heartbeat", p_token=TOKEN, p_platform=PLATFORM, p_version=VERSION,
                     p_state="error" if last_error else ("paused" if paused else "idle"),
                     p_current_job=None, p_last_error=last_error, p_info=info)
            DEVICE_ID, paused = hb.get("device_id"), bool(hb.get("paused"))
            last_error = None
            for c in hb.get("commands") or []:
                handle_command(c)
            if not paused:
                j = rpc("agent_claim_job", p_token=TOKEN, p_types=sorted(HANDLERS))
                if j and j.get("id"):
                    busy = True
                    run_job(j)
        except Restart:
            log("재시작")
            sys.exit(0)  # 자동 시작 장치(맥 launchd / Windows 실행기)가 다시 켭니다
        except Exception as e:
            last_error = str(e)[:500]
            log(f"오류: {last_error}")
            if "invalid agent token" in last_error:
                log("토큰이 맞지 않습니다. 대시보드에서 토큰을 다시 발급하고 설치 명령을 다시 실행하세요.")
                time.sleep(300)
                continue
        if not busy:
            time.sleep(BEAT_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("사용자가 중지함")
