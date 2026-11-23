#!/usr/bin/env python3
"""NYX WHITE DIRECTOR 에이전트

Supabase(중앙 기록 창고)만 바라보고 일합니다.
- 15초마다 "살아 있음" 신호를 보내고, 대시보드에서 누른 명령(연결 시험·재시작·업데이트)을 받아 처리합니다.
- 일시정지가 아니면 작업 대기열에서 이 에이전트가 할 수 있는 일을 가져가 처리합니다.
- 컴퓨터가 잠들지 않게 막습니다(화면은 꺼져도 됨).
- 10분마다 GitHub에 새 버전이 있는지 확인하고, 있으면 하던 일을 마친 뒤 스스로 업데이트합니다.
  새 버전이 시작에 계속 실패하면 이전 버전으로 되돌립니다.
표준 라이브러리만 씁니다. 설정은 같은 폴더의 config.json(설치할 때 자동 생성)에 있습니다.
"""
VERSION = "1.3.2"

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
PENDING = os.path.join(HOME, "update_pending.json")
PREV = os.path.join(HOME, "agent.py.prev")


def _rollback_if_broken():
    """업데이트 직후 시작이 3번 넘게 실패하면 이전 버전으로 되돌린다."""
    try:
        if not os.path.exists(PENDING):
            return
        with open(PENDING, encoding="utf-8") as f:
            st = json.load(f)
        st["tries"] = int(st.get("tries", 0)) + 1
        if st["tries"] > 3 and os.path.exists(PREV):
            os.replace(PREV, os.path.join(HOME, "agent.py"))
            os.remove(PENDING)
            with open(os.path.join(HOME, "rollback.json"), "w", encoding="utf-8") as f:
                json.dump({"from": st.get("version"), "at": time.time()}, f)
            sys.exit(0)  # 자동 시작 장치가 이전 버전으로 다시 켠다
        with open(PENDING, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except SystemExit:
        raise
    except Exception:
        pass


_rollback_if_broken()
with open(os.path.join(HOME, "config.json"), encoding="utf-8-sig") as f:
    CFG = json.load(f)
URL = CFG["url"].rstrip("/")
KEY = CFG["key"]
TOKEN = CFG["token"]
PLATFORM = "mac" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"
BEAT_SECONDS = int(CFG.get("beat_seconds", 15))
UPDATE_URL = CFG.get("update_url", "https://raw.githubusercontent.com/gostoping/nyxwhite-dashboard/main/agent/agent.py")
UPDATE_EVERY = int(CFG.get("update_every_seconds", 600))
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


def ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def fetch_latest():
    """최신 agent.py 내용과 버전을 가져온다. GitHub 우선, 실패하면 Supabase 저장소."""
    errors = []
    for src in ("github", "storage"):
        try:
            if src == "github":
                req = urllib.request.Request(UPDATE_URL, headers={"Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    code = r.read()
            else:
                code = download("agent.py")
            text = code.decode("utf-8")
            compile(text, "agent.py", "exec")  # 문법이 깨진 파일이면 쓰지 않는다
            ver = next((l.split("=", 1)[1].strip().strip('"') for l in text.splitlines() if l.startswith("VERSION =")), None)
            if ver:
                return code, ver, src
        except Exception as e:
            errors.append(f"{src}: {e}")
    raise RuntimeError("최신 버전 확인 실패 (" + "; ".join(errors) + ")")


def install_update(code, new_ver):
    """새 버전으로 바꾸고, 이전 버전은 되돌리기용으로 보관한다."""
    me = os.path.join(HOME, "agent.py")
    with open(me, "rb") as f:
        old = f.read()
    with open(PREV, "wb") as f:
        f.write(old)
    tmp = me + ".new"
    with open(tmp, "wb") as f:
        f.write(code)
    os.replace(tmp, me)
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump({"version": new_ver, "from": VERSION, "tries": 0}, f)


LAST_CHECK = 0.0
LATEST_SEEN = None


def check_update(force=False):
    """새 버전이 있으면 설치하고 True를 돌려준다(호출한 쪽이 재시작)."""
    global LAST_CHECK, LATEST_SEEN
    if not force and time.time() - LAST_CHECK < UPDATE_EVERY:
        return None
    LAST_CHECK = time.time()
    code, ver, src = fetch_latest()
    LATEST_SEEN = ver
    if ver_tuple(ver) <= ver_tuple(VERSION):
        return None
    try:  # 시작에 실패해 되돌렸던 버전은 다시 받지 않는다(더 새 버전이 나오면 받음)
        with open(os.path.join(HOME, "rollback.json"), encoding="utf-8") as f:
            if json.load(f).get("from") == ver and not force:
                return None
    except Exception:
        pass
    install_update(code, ver)
    log(f"자동 업데이트: v{VERSION} → v{ver} ({src})")
    return ver


# ------------------------------------------------------------------ 비용 기록
# 모든 유료 호출은 반드시 log_cost로 남긴다. 결과가 정해지면 set_cost_outcome으로
# success(최종 결과에 쓰임) / failed(버려진 시도) / hold(보류) 를 표시한다.
PRICES = {"at": 0, "krw_per_usd": None, "prices": []}


def price(provider, model, unit):
    """단가표에서 달러 단가를 찾는다(10분마다 새로 읽음). 없으면 None."""
    if time.time() - PRICES["at"] > 600:
        try:
            pb = rpc("agent_price_book", p_token=TOKEN) or {}
            PRICES.update(at=time.time(), krw_per_usd=pb.get("krw_per_usd"), prices=pb.get("prices") or [])
        except Exception as e:
            log(f"단가표 읽기 실패: {e}")
    for p in PRICES["prices"]:
        if p.get("provider") == provider and p.get("model") == model and p.get("unit") == unit:
            return float(p["usd"]) if p.get("usd") is not None else None
    return None


def estimate_usd(provider, model, qty):
    """사용량(qty)과 단가표로 달러 비용을 계산한다. 단가가 없으면 None."""
    total, known = 0.0, False
    pairs = [("seconds", "per_second", 1), ("images", "per_image", 1), ("calls", "per_call", 1),
             ("input_tokens", "per_1m_input_tokens", 1e-6), ("output_tokens", "per_1m_output_tokens", 1e-6)]
    for key, unit, scale in pairs:
        if qty.get(key):
            u = price(provider, model, unit)
            if u is not None:
                total += u * float(qty[key]) * scale
                known = True
    return round(total, 6) if known else None


def log_cost(stage, provider, model, qty, *, episode_id=None, scene_no=None, cut_id=None, attempt_no=None,
             usd=None, outcome="pending", job_id=None, note=None):
    """비용 한 줄을 기록하고 그 id를 돌려준다. 기록 실패가 작업을 멈추지는 않는다."""
    try:
        if usd is None:
            usd = estimate_usd(provider, model, qty or {})
        return rpc("agent_log_cost", p_token=TOKEN, p_cost={
            "stage": stage, "provider": provider, "model": model, "qty": qty, "usd": usd,
            "episode_id": episode_id, "scene_no": scene_no, "cut_id": cut_id, "attempt_no": attempt_no,
            "outcome": outcome, "job_id": job_id, "note": note})
    except Exception as e:
        log(f"비용 기록 실패: {e}")
        return None


def set_cost_outcome(ids, outcome, reason=None, cause=None):
    ids = [i for i in (ids or []) if i]
    if not ids:
        return 0
    try:
        return rpc("agent_set_cost_outcome", p_token=TOKEN, p_ids=ids, p_outcome=outcome, p_reason=reason, p_cause=cause)
    except Exception as e:
        log(f"비용 결과 기록 실패: {e}")
        return 0


# ------------------------------------------------------------------ API 키
def secrets(*names):
    """대표가 대시보드에 등록한 API 키를 꺼낸다. 키는 기록이나 화면에 절대 남기지 않는다."""
    return rpc("agent_get_secrets", p_token=TOKEN, p_names=list(names)) or {}


def http_json(url, headers, method="GET", body=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300].decode("utf-8", "replace")}


def higgsfield_cred(k):
    """Higgsfield 인증 값 (ID, Secret). 한 칸에 'ID:Secret'을 통째로 넣은 경우, 앞에 'Key '가 붙은 경우도 받아 준다."""
    def clean(v):
        v = (v or "").strip().strip('"\'').strip()
        if v.lower().startswith("key "):
            v = v[4:].strip()
        return v
    hid, hsec = clean(k.get("higgsfield_key_id")), clean(k.get("higgsfield_key_secret"))
    for v in (hid, hsec):
        if ":" in v and len(v.split(":", 1)[1]) > 8:
            i, s2 = v.split(":", 1)
            return i.strip(), s2.strip()
    return (hid, hsec) if hid and hsec else None


HF_BASES = ["https://platform.higgsfield.ai", "https://api.higgsfield.ai"]


def higgsfield_probe(cred):
    """주소 2곳 x 인증 방식 2가지로 인증만 확인한다(없는 요청 번호의 상태 조회, 비용 없음).
    통과한 조합을 hf_auth.json에 기억해 이후 영상 생성에 쓴다."""
    hid, hsec = cred
    schemes = [("key", {"Authorization": f"Key {hid}:{hsec}"}),
               ("legacy", {"hf-api-key": hid, "hf-secret": hsec})]
    tried = []
    for base in HF_BASES:
        for name, h in schemes:
            try:
                code, data = http_json(f"{base}/requests/00000000-0000-0000-0000-000000000000/status",
                                       {**h, "Accept": "application/json"}, timeout=20)
            except Exception as e:
                code, data = 0, {"detail": type(e).__name__}
            if code not in (401, 403) and code:
                try:
                    open(os.path.join(HOME, "hf_auth.json"), "w").write(json.dumps({"base": base, "scheme": name}))
                except Exception:
                    pass
                return True, f"{base.split('//')[1]} · {name} · 응답 {code}"
            hint = ""
            if isinstance(data, dict):
                hint = str(data.get("detail") or data.get("message") or data.get("error") or data.get("raw") or "")[:80]
            tried.append(f"{base.split('//')[1]}/{name}={code}{(' ' + hint) if hint else ''}")
    return False, "; ".join(tried)


def test_keys():
    """키가 맞는지 비용 없이 확인한다(생성 요청을 보내지 않음)."""
    k = secrets("anthropic_api_key", "higgsfield_key_id", "higgsfield_key_secret")
    out = []
    a = k.get("anthropic_api_key")
    if not a:
        out.append("Claude: 키 미등록")
    else:
        code, data = http_json("https://api.anthropic.com/v1/models?limit=20",
                               {"x-api-key": a, "anthropic-version": "2023-06-01"})
        if code == 200:
            ids = [m.get("id") for m in (data or {}).get("data", [])]
            out.append(f"Claude: 연결 정상 (모델 {len(ids)}개 확인)")
        else:
            msg = ((data or {}).get("error") or {}).get("message") if isinstance(data, dict) else ""
            out.append(f"Claude: 실패 {code} {msg or ''}".strip())
    cred = higgsfield_cred(k)
    if not cred:
        out.append("Higgsfield: 키 미등록")
    else:
        ok, detail = higgsfield_probe(cred)
        out.append(f"Higgsfield: 인증 통과 ({detail})" if ok
                   else f"Higgsfield: 키가 맞지 않음 (401) [ID {len(cred[0])}자 · Secret {len(cred[1])}자] {detail}")
    return " / ".join(out), all("실패" not in o and "맞지 않음" not in o and "미등록" not in o for o in out)


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
        elif name == "test_keys":
            msg, ok = test_keys()
            rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=ok, p_result=msg)
        elif name == "restart":
            rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=True, p_result="재시작합니다")
            raise Restart()
        elif name == "update":
            new_ver = check_update(force=True)
            if not new_ver:
                rpc("agent_command_done", p_token=TOKEN, p_id=cid, p_ok=True,
                    p_result=f"이미 최신 버전입니다 (v{VERSION}, 확인한 최신 v{LATEST_SEEN})")
                return
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
            "python": platform.python_version(), "started_at": STARTED, "jobs": sorted(HANDLERS),
            "auto_update_minutes": UPDATE_EVERY // 60}
    try:
        with open(os.path.join(HOME, "rollback.json"), encoding="utf-8") as f:
            rb = json.load(f)
        info["rolled_back_from"] = rb.get("from")
        log(f"주의: v{rb.get('from')} 업데이트가 시작에 실패해 이전 버전으로 되돌렸습니다")
    except Exception:
        pass
    while True:
        busy = False
        try:
            info["latest_seen"] = LATEST_SEEN
            hb = rpc("agent_heartbeat", p_token=TOKEN, p_platform=PLATFORM, p_version=VERSION,
                     p_state="error" if last_error else ("paused" if paused else "idle"),
                     p_current_job=None, p_last_error=last_error, p_info=info)
            DEVICE_ID, paused = hb.get("device_id"), bool(hb.get("paused"))
            last_error = None
            if os.path.exists(PENDING):  # 새 버전이 정상적으로 연결됨 → 업데이트 확정
                os.remove(PENDING)
                log(f"v{VERSION} 업데이트 확정")
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
            try:
                if check_update():
                    log("새 버전으로 재시작")
                    sys.exit(0)
            except SystemExit:
                raise
            except Exception as e:
                log(f"업데이트 확인 실패(다음에 다시 시도): {e}")
            time.sleep(BEAT_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("사용자가 중지함")
