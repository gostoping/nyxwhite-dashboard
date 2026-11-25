#!/usr/bin/env python3
"""NYX WHITE DIRECTOR 에이전트

Supabase(중앙 기록 창고)만 바라보고 일합니다.
- 15초마다 "살아 있음" 신호를 보내고, 대시보드에서 누른 명령(연결 시험·재시작·업데이트)을 받아 처리합니다.
- 일시정지가 아니면 작업 대기열에서 이 에이전트가 할 수 있는 일을 가져가 처리합니다.
  제작: 작가실 정리 → 감독 컷 설계(대본감수 포함) → [대표 승인] Seedance 생성 → 영상 감수 → 집 윈도우 보관(씬 폴더)
- 컴퓨터가 잠들지 않게 막습니다(화면은 꺼져도 됨).
- 10분마다 GitHub에 새 버전이 있는지 확인하고, 있으면 하던 일을 마친 뒤 스스로 업데이트합니다.
  새 버전이 시작에 계속 실패하면 이전 버전으로 되돌립니다.
표준 라이브러리만 씁니다. 설정은 같은 폴더의 config.json(설치할 때 자동 생성)에 있습니다.
"""
VERSION = "1.34.1"

import base64
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
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


def hf_cost_start(model, qty, what):
    """Higgsfield가 요청을 받은 순간(=돈이 나갈 수 있는 순간) 한 줄 기록. 끝나면 hf_cost_end로 성공·실패 표시.
    예전엔 성공한 것만 적어서, 실패·거절·시간 초과로 버려진 시도의 비용이 장부에 안 남았다."""
    t = CURRENT.get("task") or {}
    return log_cost("keyframe", "higgsfield", model, qty, episode_id=t.get("episode_id"), scene_no=t.get("scene_no"),
                    job_id=t.get("id"), outcome="pending", note=what)


def hf_cost_end(cid, ok, reason=None):
    if cid:
        set_cost_outcome([cid], "success" if ok else "failed",
                         None if ok else f"Higgsfield {reason} (돌려받았는지는 Higgsfield 콘솔 사용 내역에서 확인)"[:300], None if ok else "tool")


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


def elevenlabs_check(el):
    """ElevenLabs 키 확인. 권한이 좁게 묶인 키도 있어 여러 곳을 물어보고, 거절 이유를 그대로 보여 준다."""
    tried = []
    for path, label in (("/v1/user/subscription", "구독"), ("/v1/models", "모델"), ("/v1/voices?page_size=1", "목소리")):
        try:
            code, data = http_json("https://api.elevenlabs.io" + path, {"xi-api-key": el, "Accept": "application/json"}, timeout=30)
        except Exception as e:
            code, data = 0, {"detail": str(e)}
        if code == 200:
            if label == "구독" and isinstance(data, dict):
                left = max(0, int(data.get("character_limit") or 0) - int(data.get("character_count") or 0))
                return f"ElevenLabs: 연결 정상 (요금제 {data.get('tier', '?')}, 남은 크레딧 {left:,})"
            return f"ElevenLabs: 연결 정상 ({label} 확인. 구독 정보는 키 권한에 없어 못 읽음)"
        det = (data or {}).get("detail") if isinstance(data, dict) else None
        reason = (det.get("status") or det.get("message")) if isinstance(det, dict) else (det or "")
        tried.append(f"{label} {code}{(' ' + str(reason)[:60]) if reason else ''}")
    return "ElevenLabs: 키가 맞지 않음 [" + "; ".join(tried) + f"] (키 {len(el)}자)"


def test_keys():
    """키가 맞는지 비용 없이 확인한다(생성 요청을 보내지 않음)."""
    k = secrets("anthropic_api_key", "higgsfield_key_id", "higgsfield_key_secret", "elevenlabs_api_key")
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
    el = (k.get("elevenlabs_api_key") or "").strip().strip('"\'')
    if el:
        out.append(elevenlabs_check(el))
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


# ------------------------------------------------------------------ 제작 파이프라인
# 작가실 → 감독(컷 설계) → 감정감수자 → [대표 생성 승인] → Seedance 생성 → 영상감수자·평가 → 윈도우 보관
# 돈이 드는 영상 생성은 대표가 대시보드에서 승인한 컷만 한다(서버에서도 막혀 있음).

TASKS = {}


def ts_of(v):
    """DB 시각 문자열 → 초. 소수점 자리 수가 달라도(파이썬 3.10 이하) 읽는다. 못 읽으면 None."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?", str(v or "").replace(" ", "T"))
    if not m:
        return None
    tz = (m.group(3) or "+00:00").replace("Z", "+00:00")
    if len(tz) == 5:
        tz = tz[:3] + ":" + tz[3:]
    try:
        return datetime.fromisoformat(m.group(1) + tz).timestamp() + float("0." + (m.group(2) or "0"))
    except Exception:
        return None


def waited(t):
    """이 일이 대기열에 들어온 뒤 지난 초(기다림 한도용 — '잠시 뒤 다시'는 시도 횟수에 세지 않으므로). 모르면 '오래됨'으로 본다."""
    c = ts_of(t.get("created_at"))
    return time.time() - c if c else 10 ** 9


class Retry(Exception):
    """지금은 할 수 없고 잠시 뒤 다시: 작업을 대기열로 돌려놓는다."""
    def __init__(self, seconds, why):
        super().__init__(why)
        self.seconds, self.why = seconds, why


def task(name):
    def wrap(fn):
        TASKS[name] = fn
        return fn
    return wrap


class _Current(threading.local):
    """작업 상태는 스레드마다 따로(긴 일 줄과 짧은 일 줄이 함께 돌아도 진행 문구·비용 기록이 섞이지 않게)."""
    def __init__(self):
        self.d = {"task": None}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def __getitem__(self, k):
        return self.d[k]

    def __setitem__(self, k, v):
        self.d[k] = v


CURRENT = _Current()
# 짧은 바이블 일은 긴 일(컷 설계·영상)과 따로 줄을 서서 바로 처리한다(컷 설계 중에도 고쳐쓰기 반영이 기다리지 않게)
QUICK_KINDS = ["bible_prompt", "coord_review", "photo_describe", "balance_check"]
# 바이블 사진(Higgsfield 후보·얼굴 학습)도 영상 생성·컷 설계 뒤에 줄 서지 않게 따로
PHOTO_KINDS = ["photo_design", "photo_set"]
QUICK = {"busy": None, "photo": None, **{f"busy{i}": None for i in range(8)}, **{f"photo{i}": None for i in range(4)},
         **{f"gen{i}": None for i in range(4)}, **{f"prep{i}": None for i in range(4)},
         **{f"work{i}": None for i in range(4)}, **{f"heavy{i}": None for i in range(2)},
         **{f"design{i}": None for i in range(3)}, **{f"voice{i}": None for i in range(3)}}   # 줄 이름을 미리 다 만들어 둔다(동시에 고치다 깨지지 않게)
PULSE = {"last": 0.0, "restart": False}


FLAGS = ("paused", "started", "draining")


def lane(kinds, slot, label):
    QUICK.setdefault(slot, None)
    while True:
        try:
            # 업데이트·재시작을 기다리는 중(draining)에는 새 일을 집지 않는다 — 집었다가 재시작에 끊기면 영상 생성이 두 번 돈다
            if not QUICK.get("paused") and not QUICK.get("draining"):
                tk = rpc("agent_claim_task", p_token=TOKEN, p_kinds=kinds, p_platform=PLATFORM)
                if tk and tk.get("id"):
                    QUICK[slot] = tk["kind"]
                    try:
                        run_task(tk)
                    finally:
                        QUICK[slot] = None
                    continue
        except Exception as e:
            log(f"{label} 오류: {e}")
        time.sleep(5)


def quick_lane():
    lane(QUICK_KINDS, "busy", "짧은 일 줄")


QUICK_WORKERS = 4   # 바이블 반영·코디네이터 감수는 4개를 동시에(병렬)
GEN_KINDS = ["generate"]   # 영상 생성은 Higgsfield를 기다리는 시간이 대부분 → 3개 동시에
PREP_KINDS = ["prep_cut"]  # 컷 장면 사진 준비 2개 동시에
HEAVY_KINDS = ["assemble", "upscale"]   # 컴퓨터를 많이 쓰는 ffmpeg 일은 1개씩
DESIGN_KINDS = ["director", "writers_room"]   # 컷 설계·작가실은 전용 줄 2개: 목소리·감수 일이 몰려도 기다리지 않게
VOICE_KINDS = ["voice_cast", "voice_design", "voice_save"]   # ElevenLabs를 기다리는 목소리 일은 따로 2개
WORK_WORKERS = 3   # 그 밖의 일(감독 컷 설계·작가실·감수·보관·목소리·배경음악 등)은 3개 동시에 — 예전엔 본체 혼자 하나씩 해서 밀렸음


def work_kinds():
    return sorted(k for k in TASKS if k not in QUICK_KINDS + PHOTO_KINDS + GEN_KINDS + PREP_KINDS + HEAVY_KINDS + DESIGN_KINDS + VOICE_KINDS)


def parallel(fns, workers=4):
    """함수 여러 개를 동시에 돌린다(지금 작업의 진행 문구·비용 기록 맥락은 그대로 넘김). 결과는 순서대로, 예외는 그 자리에 담아 돌려준다."""
    task = CURRENT.get("task")
    out = [None] * len(fns)

    def wrap(i, f):
        CURRENT["task"] = task
        try:
            out[i] = f()
        except Exception as e:
            out[i] = e
    ths = []
    for i, f in enumerate(fns):
        while sum(1 for th in ths if th.is_alive()) >= workers:
            time.sleep(0.2)
        th = threading.Thread(target=wrap, args=(i, f), daemon=True)
        th.start()
        ths.append(th)
    for th in ths:
        th.join()
    return out


def lanes_busy():
    return next((v for k, v in list(QUICK.items()) if k not in FLAGS and v), None)


def drain(max_minutes=50):
    """재시작 전에 새 일 집기를 멈추고, 줄마다 하던 일이 끝날 때까지 기다린다.
    영상 생성은 Higgsfield 대기(최대 40분)+받기·감수가 있어 넉넉히 기다린다. 그래도 남으면 다시 켜진 뒤 이어서 한다(t_generate 재개)."""
    QUICK["draining"] = True
    end = time.time() + max_minutes * 60
    while time.time() < end and lanes_busy():
        time.sleep(2)
    left = lanes_busy()
    if left:
        log(f"재시작 대기 시간이 지나 하던 일({left})을 두고 재시작합니다(다시 켜진 뒤 이어서 합니다)")


def pulse():
    """살아 있음 신호를 따로 보낸다: 본체가 업데이트 대기·긴 확인 등으로 잠시 신호를 못 보내도 '에이전트 없음'으로 보이지 않게.
    재시작·업데이트 명령은 표시만 해 두고 본체가 일 사이에 처리한다."""
    while True:
        time.sleep(10)
        if time.time() - PULSE["last"] < 25:
            continue
        try:
            hb = rpc("agent_heartbeat", p_token=TOKEN, p_platform=PLATFORM, p_version=VERSION,
                     p_state="busy" if lanes_busy() else "idle", p_current_job=lanes_busy(), p_last_error=None,
                     p_info={"hostname": socket.gethostname(), "tasks": sorted(TASKS), "started_at": STARTED})
            PULSE["last"] = time.time()
            for c in hb.get("commands") or []:
                if c["command"] in ("restart", "update"):
                    PULSE["restart"] = c["command"]
                    rpc("agent_command_done", p_token=TOKEN, p_id=c["id"], p_ok=True, p_result="하던 일이 끝나면 바로 처리합니다")
                else:
                    handle_command(c)
        except Exception as e:
            log(f"살아 있음 신호 실패(다시 시도): {e}")


def progress(text):
    t = CURRENT.get("task")
    log(f"  · {text}")
    if t:
        try:
            rpc("agent_task_update", p_token=TOKEN, p_id=t["id"], p_status="running", p_progress=str(text)[:300])
        except Exception:
            pass


def notify(title, body, level="info"):
    try:
        rpc("agent_notify", p_token=TOKEN, p_title=title, p_body=body, p_level=level)
    except Exception as e:
        log(f"알림 실패: {e}")


# ---------------- Claude
CACHE_READ_X, CACHE_WRITE_X = 0.05, 1.25


def claude(model, system, content, *, max_tokens=8000, stage="other", episode_id=None, scene_no=None, cut_code=None, note=None):
    """Claude 호출. 시스템(지침·바이블)은 캐시해서 반복 비용을 줄인다. (글, 비용기록id)를 돌려준다."""
    key = secrets("anthropic_api_key").get("anthropic_api_key")
    if not key:
        raise RuntimeError("Claude API 키가 없습니다. 대시보드 설정 → API 키에서 넣어 주세요")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    body = {"model": model, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    end = time.time() + 30 * 60   # 한 번의 Claude 호출은 다시 시도까지 합쳐 최대 30분(일이 45분 한도를 넘겨 두 번 돌지 않게)
    for attempt in range(6):
        try:
            code, data = http_json("https://api.anthropic.com/v1/messages", headers, "POST", body, timeout=max(60, min(900, end - time.time())))
        except Exception as e:
            code, data = 0, {"error": {"message": str(e)}}
        if code == 200:
            break
        msg = ((data or {}).get("error") or {}).get("message", "") if isinstance(data, dict) else ""
        if code in (0, 408, 429, 500, 502, 503, 504, 529) and attempt < 5 and time.time() + 70 < end:
            wait = min(120, 10 * (2 ** attempt), max(5, end - time.time() - 65))
            progress(f"Claude 응답 지연({code}) — {wait}초 뒤 다시 시도")
            time.sleep(wait)
            continue
        raise RuntimeError(f"Claude 호출 실패 {code}: {msg}")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    u = data.get("usage") or {}
    qty = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
           "cache_read_tokens": u.get("cache_read_input_tokens", 0), "cache_write_tokens": u.get("cache_creation_input_tokens", 0)}
    pin, pout = price("anthropic", model, "per_1m_input_tokens"), price("anthropic", model, "per_1m_output_tokens")
    usd = None
    if pin is not None and pout is not None:
        usd = round((qty["input_tokens"] + qty["cache_write_tokens"] * CACHE_WRITE_X + qty["cache_read_tokens"] * CACHE_READ_X) * pin / 1e6
                    + qty["output_tokens"] * pout / 1e6, 6)
    cid = log_cost(stage, "anthropic", model, qty, usd=usd, episode_id=episode_id, scene_no=scene_no, cut_id=cut_code,
                   outcome="success", note=note, job_id=(CURRENT.get("task") or {}).get("id"))
    CURRENT["last_stop"] = data.get("stop_reason")
    if data.get("stop_reason") == "max_tokens":
        log("주의: Claude 답이 길이 제한에 걸려 잘렸습니다")
    return text, cid


def pick_json(text, tag="json"):
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.S)
    raw = m.group(1) if m else text
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    a, b = raw.find("{"), raw.rfind("}")
    if a < 0 or b < 0:
        raise RuntimeError("AI 답에서 결과(JSON)를 찾지 못했습니다")
    return json.loads(raw[a:b + 1])


def pick_tag(text, tag):
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.S)
    return m.group(1).strip() if m else None


def setting(ctx, key, sub, default=None):
    return ((ctx.get("settings") or {}).get(key) or {}).get(sub, default)


def bible_text(ctx, limit=60):
    """감독·감수자가 읽는 바이블. 영어 프롬프트(prompt_en)가 있으면 그 문장이 '고정 묘사'이고, 한국어 원문은 참고로만 붙인다."""
    rows = []
    for b in (ctx.get("bible") or [])[:limit]:
        en = (b.get("prompt_en") or "").strip()
        main = en or (b.get("description") or "")
        rows.append(f"- [{b.get('category')}] {b.get('name')}: {main}"
                    + (f" / 한국어 원문(참고): {b.get('description')}" if en and b.get("description") and b.get("description") != en else "")
                    + (f" / 고정 특징: {b.get('details')}" if b.get("details") else "")
                    + (f" / 동일인: {b.get('same_as')}와 같은 사람(다른 시점·호칭). 이름·목소리·버릇·정체성은 이어지고, 외모는 이 항목 묘사를 따른다"
                       + ("" if b.get("images") else f", 이 항목 사진이 없으면 {b.get('same_as')}의 사진을 참고") if b.get("same_as") else "")
                    + (f" / ★이 화에서는 '{b.get('variant')}' 모습으로 나온다(같은 사람): {b.get('change_note') or ''} — 묘사·사진·얼굴은 이 모습을 따른다" if b.get("variant") else "")
                    + (f" / 변화: {b.get('change_note')}" if b.get("change_note") and not b.get("variant") else "")
                    + (f" / 나오는 화: {', '.join(map(str, b.get('apply_eps')))}화" if b.get("apply_eps") else "")
                    + (f" (필수 아님: 사진 없이 묘사로 · 앞서 만든 장면 사진 {len(b.get('refs') or [])}장을 참고)" if b.get("essential") is False else "")
                    + (" (사진 있음)" if b.get("images") else ""))
    return "\n".join(rows) or "(바이블이 비어 있음)"


def bible_entry(ctx, name):
    """이름으로 바이블 항목 찾기. 감독이 '린(과거)'처럼 괄호를 붙여도 '린'을 찾는다."""
    if not name:
        return None
    bl = ctx.get("bible") or []
    for b in bl:
        if b.get("name") == name:
            return b
    base = re.sub(r"\s*[\(\[].*?[\)\]]\s*", "", name).strip()
    for b in bl:
        if re.sub(r"\s*[\(\[].*?[\)\]]\s*", "", b.get("name") or "").strip() == base:
            return b
    return None


def lessons_text(ctx, limit=30, role=None):
    """교훈(지난 감수에서 배운 것). role이 있으면 그 사람에게 준 교훈만(받는 사람이 비어 있으면 감독용)."""
    ls = ctx.get("lessons") or []
    if role:
        ls = [l for l in ls if role in (l.get("targets") or []) or (role == "감독" and not l.get("targets"))]
    rows = [f"- ({l.get('category')}) {l.get('rule')}" + (f"  · 실패 예: {l.get('bad_example')}" if l.get("bad_example") else "")
            for l in ls[:limit]]
    return "\n".join(rows) or "(아직 없음)"


def role_lessons_block(ctx, role, title=None):
    """감수자들이 이 사람에게 준 피드백(교훈)을 프롬프트 앞부분에 넣을 블록. 없으면 빈칸."""
    t = lessons_text(ctx, 20, role)
    return "" if t == "(아직 없음)" else f"\n\n[{title or role + '에게 온 감수자 피드백'} — 지난 제작에서 배운 것, 먼저 적용]\n{t}\n"


def series_lessons(series_id):
    try:
        return {"lessons": rpc("agent_get_lessons", p_token=TOKEN, p_series=series_id, p_include_candidates=False) or []}
    except Exception:
        return {"lessons": []}


def continuity_text(ctx):
    rows = [f"- {x.get('name')}: {x.get('state')}" + (f" (씬 {x['scene_no']})" if x.get("scene_no") else "") for x in (ctx.get("prev_continuity") or [])]
    return "\n".join(rows) or "(첫 화이거나 기록 없음)"


def notes_text(ctx):
    rows = [f"- {('씬 ' + str(n['scene_no'])) if n.get('scene_no') else '화 전체'} [{'필수' if n.get('priority') == 'must' else '참고'}"
            f"{' · 중요 장면' if n.get('important') else ''}] {n.get('content')}" for n in (ctx.get("notes") or [])]
    return "\n".join(rows) or "(없음)"


def guide(ctx, *names):
    g = ctx.get("guidelines") or {}
    return "\n\n".join(f"[{n} 지침]\n{g[n]}" for n in names if g.get(n))


# ---------------- 작가실
DIALOG_LINE = re.compile(r"^\s*([^\s:()（）\[\]]{1,14})\s*[:：]\s*(.+)$")


def dialog_set(text):
    """대사 줄(이름: 대사)에서 괄호 지시를 뺀 대사만 모은다(나누기 전후 대사가 그대로인지 확인용)."""
    out = []
    for ln in (text or "").splitlines():
        m = DIALOG_LINE.match(ln)
        if m and not m.group(1).startswith("S#"):
            out.append(re.sub(r"\s+", "", re.sub(r"[（(][^)）]*[)）]", "", m.group(2))))
    return [x for x in out if x]


def writers_split(t, p):
    """감독이 컷으로 설계하지 못한 긴 씬을 작가실이 대사·사건·감정선 그대로 2~3개 부분(S#7-1, S#7-2)으로 나눈다."""
    ep = t["episode_id"]
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=p.get("source_version_id"))
    src = ctx.get("script")
    if not src or not src.get("content"):
        raise RuntimeError("작가실이 고칠 확정 대본이 없습니다")
    e = ctx.get("episode") or {}
    epno = int(e.get("number") or 0)
    model = setting(ctx, "generation", "claude_writer", "claude-opus-5-5")
    all_sc = split_scenes(src["content"])
    targets = [int(x) for x in p.get("scenes") or []]
    if 0 in targets:   # 예전 버전이 '화 전체(S#0)를 나눠 달라'고 남긴 요청: 이제 씬 구분을 알아보므로 나눌 필요 없이 감독이 씬별로 다시 설계
        real = [s for s, _, _ in all_sc if s > 0]
        if not real:
            raise RuntimeError(f"{epno}화 확정 대본에 씬 구분이 없어 화 전체가 씬 하나로 읽혔습니다. 이렇게 하세요: 대본 탭에서 씬마다 '00:12–00:48 | 장소' 줄을 넣고 확정하세요")
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "director", "episode_id": ep, "payload": {"script_version_id": src["id"]}})
        return f"{epno}화는 이제 씬 {len(real)}개로 읽힙니다(대본의 '시간 | 장소' 줄). 화 전체를 나눌 필요 없이 감독이 씬별로 다시 설계합니다"
    prep = bool(p.get("prep"))   # 컷 설계 전 제작용 나누기(작가실이 먼저 끝내고 감독은 나누기를 요청하지 않음)
    why_line = (f"컷 설계 전에 제작용으로 씬을 나눈다. 한 부분은 {SCENE_MAX_SEC}초 이하(영상 컷 15초 2개 이내)가 되게 한다."
                if prep else "감독이 아래 씬을 영상 컷으로 설계하지 못했다(씬이 너무 길어 한 번에 설계할 수 없음).")
    system = (guide(ctx, "작가실") or "너는 드라마 작가실이다.") + role_lessons_block(ctx, "작가실") + f"""

[이번 일 — 제작용 씬 나누기]
{why_line} 이 씬을 자연스러운 전환점(장소·시간이 바뀌는 곳 우선)에서 2~4개 부분으로 나눈다.""" + """
- 절대 바꾸지 않는 것: 대사(한 글자도), 사건 순서, 감정선, 등장인물, 복선. 새 대사·새 사건을 만들지 않는다.
- 나누는 곳: 장소·시간이 바뀌는 곳, 감정이 크게 꺾이는 곳, 인물이 들고 나는 곳, 긴 침묵·행동 전환.
- 반복되는 지문·늘어지는 동작 묘사는 짧게 다듬어도 된다(뜻은 그대로).
- 각 부분 머리: "S#{번호}-{1,2,3} | 시간 구간 | 장소 | 낮·밤 / 등장: 인물". 번호는 원래 씬 번호 그대로(예: S#7-1, S#7-2). 시간 구간은 원래 구간을 나눠서.
답 형식: <scene>나눈 씬 전체(부분 머리 포함)</scene> 다음에 <json>{"cut_points": ["어디서 왜 나눴는지(한국어)"], "trimmed": ["다듬은 지문(있으면)"]}</json>"""
    new_body, notes, orig_of = {}, [], {}

    def split_one(n):
        orig = next((b for sn, _, b in all_sc if sn == n), None)
        if not orig:
            notes.append(f"S#{n}: 대본에서 찾지 못함")
            return
        orig_of[n] = orig   # 이미 나뉜 씬(S#7-1·7-2)이면 그중 이 부분만 바꾼다(다른 부분을 덮어써 대사가 사라지던 문제)
        kept = 0
        for attempt in range(2):
            progress(f"작가실이 {'컷 설계 전에' if prep else '감독 요청으로'} {epno}화 S#{n}을(를) 나누는 중{' (다시)' if attempt else ''}")
            text, _ = claude(model, system, f"{epno}화 「{e.get('title') or ''}」\n[감독 피드백]\n{p.get('why') or ''}\n" + "\n".join(p.get("detail") or [])
                             + (f"\n[대표 요청]\n{p.get('note')}" if p.get("note") else "")
                             + f"\n\n[나눌 씬]\n{orig}" + ("\n\n[중요] 지난번 답에서 대사가 바뀌거나 빠졌다. 대사는 한 글자도 바꾸지 말 것." if attempt else ""),
                             max_tokens=12000, stage="script_revision", episode_id=ep, note=f"작가실: 긴 씬 나누기 S#{n}")
            body = pick_tag(text, "scene") or ""
            heads = re.findall(rf"S\s*#\s*{n}\s*-\s*\d+", body)
            want, got = dialog_set(orig), dialog_set(body)
            kept = sum(1 for x in want if x in got) / max(1, len(want))
            if len(heads) >= 2 and kept >= 0.95:
                new_body[n] = body.strip()
                cp = safe_json(text).get("cut_points") or []
                notes.append(f"S#{n} → {len(heads)}개 부분으로 나눔" + (f" ({'; '.join(map(str, cp[:3]))})" if cp else ""))
                break
        else:   # AI가 대사를 지키지 못하면 원문을 줄 단위로 기계적으로 나눈다(글자 하나 안 바뀜)
            body = mech_split(n, orig)
            if body:
                new_body[n] = body
                notes.append(f"S#{n} → {len(re.findall(rf'S#{n}-\d+', body))}개 부분으로 나눔(원문 그대로 줄 단위로 나눔 · AI 나누기는 대사 보존 {int(kept * 100)}%라 쓰지 않음)")
            else:
                notes.append(f"S#{n}: 너무 짧아 나누지 않음")

    for r in parallel([(lambda n=n: split_one(n)) for n in targets], workers=3):   # 여러 씬을 동시에
        if isinstance(r, Exception):
            notes.append(f"나누기 실패: {str(r)[:120]}")
    if not new_body:   # 나눌 게 없어도 제작은 멈추지 않는다: 감독이 그 씬을 그대로(컷 여러 개로) 설계
        pl = {"script_version_id": src["id"], "prepped": True}
        if not prep and targets:
            pl.update({"scenes": [x for x in targets if x > 0], "retry_scenes": True})
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "director", "episode_id": ep, "payload": pl})
        return f"나눌 씬이 없어 그대로 둡니다({' / '.join(notes)}) · 감독이 이어서 컷을 설계합니다"
    content = normalize_scene_heads(src["content"])   # 나눈 씬 본문과 같은 모양(S# 머리 붙인 대본)에서 바꿔 넣는다
    for sn, body in new_body.items():
        content = content.replace(orig_of[sn], body, 1)
    content = normalize_scene_heads(content)   # S#3-1·3-2 → S#3·S#4…: 씬 번호를 화 처음부터 순서대로
    note = f"작가실: 제작용 씬 나누기(대사 그대로) — " + " / ".join(notes)
    vid = rpc("agent_save_script_draft", p_token=TOKEN, p_episode=ep, p_content=content, p_note=note[:480])
    # 컷 설계 전 나누기면 화 전체, 예전 감독 요청이면 나눈 씬부터 끝까지(번호가 한 칸씩 밀림) 다시 설계
    follow = {"prepped": True} if prep else {"scenes": [s for s, _, _ in split_scenes(content) if s >= min(new_body)], "prepped": True, "retry_scenes": True}
    rpc("agent_set_script_followup", p_token=TOKEN, p_id=vid, p={"kind": "director", "payload": follow})
    # 대사·사건은 그대로(AI 나누기는 95% 이상 확인, 아니면 원문 줄 단위)라 대표 확정 없이 바로 확정
    try:
        rpc("agent_confirm_split", p_token=TOKEN, p_id=vid, p_summary=note[:480])
    except Exception as ex:   # DB 반영 전이면 예전 방식(정리본 자동 확정 설정)으로
        log(f"씬 나누기 확정 실패, 일반 확정 시도: {ex}")
        try:
            rpc("agent_confirm_script", p_token=TOKEN, p_id=vid, p_summary=note[:480])
        except Exception:
            notify(f"{epno}화 씬 나누기 정리본: 확정을 기다립니다", note[:300] + "\n대사는 그대로입니다. 대본 탭에서 확정하면 감독이 컷을 설계합니다.")
            return note + " · 확정 대기"
    return note + " · 자동 확정 → 감독이 컷 설계"


@task("writers_room")
def t_writers_room(t):
    ep = t["episode_id"]
    p = t.get("payload") or {}
    if p.get("mode") == "split_scenes":
        return writers_split(t, p)
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=p.get("source_version_id"))
    src = (ctx.get("script") if p.get("source_version_id") else None) or ctx.get("latest_draft") or ctx.get("script")
    if not src or not src.get("content"):
        raise RuntimeError("작가실이 읽을 원고가 없습니다. 대본 탭에서 원고를 넣어 주세요")
    wr = rpc("agent_writers_room_context", p_token=TOKEN, p_episode=ep) or {}
    if p.get("fix_issues") and not p.get("original"):   # 대본 감수 반영: 빠진 것을 되살리려면 대표 원고가 필요
        vers = rpc("agent_script_versions", p_token=TOKEN, p_episode=ep) or []
        p["original"] = (next((v for v in vers if (v.get("note") or "").startswith("원고") or v.get("version_no") is None), {}) or {}).get("content") or ""
        progress(f"작가실이 대본 감수에서 나온 {len(p['fix_issues'])}건을 그대로 반영하는 중")
    model = setting(ctx, "generation", "claude_writer", "claude-opus-5-5")
    e = ctx.get("episode") or {}
    system = role_lessons_block(ctx, "작가실").lstrip() + (guide(ctx, "작가실") or "너는 드라마 작가실이다. 흐름은 그대로 두고 감정선과 감정 연결 장치만 다듬는다.") + f"""

[시리즈 바이블 — 인물·장소 이름은 반드시 이대로]
{bible_text(ctx)}

[답 형식 — 반드시 지킨다]
<draft>
(제작용 정리본 전체. 원고의 모든 씬을 순서대로. 씬 머리 형식: S#번호 | 시간 구간 | 장소 | 낮·밤 / 등장: 인물)
</draft>

[제작용 씬 나누기 — 감독이 바로 컷 설계를 할 수 있게, 작가실이 여기서 끝낸다]
- 씬은 장소(배경)별로 구분한다: 장소나 시간이 바뀌면 새 씬이다. 한 씬 안에 장소가 둘이면 둘로 나눈다. 씬 머리의 장소 이름은 바이블 장소 이름을 그대로 쓴다.
- 한 씬은 {SCENE_MAX_SEC}초 이하. 넘으면 감정이 꺾이는 곳·인물이 들고 나는 곳에서 나눈다(대사 한 줄을 중간에 끊지 않는다). 시간 구간도 나눠 적는다.
- 씬 번호는 화 처음부터 S#1, S#2, S#3…으로 순서대로. 'S#2-1' 같은 가지 번호는 쓰지 않는다.
- 나누기만 하는 곳에서는 대사·사건·순서를 바꾸지 않는다.
<json>
{{"emotion_curve": "시작 감정 → 전환점 → 끝 감정",
  "changes": [{{"scene_no": 1, "before": "원문 일부", "after": "바꾼 것", "reason": "감정선 근거"}}],
  "proposals": [{{"scene_no": 1, "kind": "add_scene|remove_scene|reorder|event_timing|reveal|relationship|setting|ending|other",
                 "current_text": "", "proposed_text": "", "reason": "", "impact": ""}}],
  "summary": "대표용 변경 요약 3~5줄"}}
</json>"""
    approved = wr.get("approved") or []
    ppl = [n for n in (ctx.get("notes") or []) if "PPL" in (n.get("content") or "")]
    if ppl:
        system += """

[PPL·협찬 반영 — 가장 중요]
대표 연출 메모에 'PPL 강조 노출'이 있는 씬은 그 장소·소품이 화면에서 세게 보이도록 지문(행동·배치·카메라)으로 넣는다.
- 흐름이 절대 흐트러지면 안 된다: 사건 순서, 감정선, 대사의 뜻, 복선·반전, 씬 순서는 그대로. 새 대사·새 사건을 만들지 않는다(꼭 필요하면 대사 없는 행동으로).
- 늘어난 화면 시간만큼 같은 씬이나 앞뒤 씬의 군더더기(반복 지문, 늘어지는 동작, 같은 뜻의 대사 꼬리)를 축약해서 씬 시간 구간과 화 전체 길이를 원래대로 맞춘다.
- 광고처럼 보이는 설명 대사·상표 이름 언급은 하지 않는다. 인물이 자연스럽게 쓰는 순간에 카메라가 가까이 머문다.
- changes에 PPL을 넣은 곳과 축약한 곳을 각각 적는다(reason에 'PPL 반영' 또는 'PPL 시간 확보를 위한 축약')."""
    user = f"""{e.get('number')}화 「{e.get('title') or ''}」 원고를 정리해 주세요.

[대표 연출 메모]
{notes_text(ctx)}

[앞뒤 화 발췌]
{json.dumps(ctx.get('neighbors') or [], ensure_ascii=False)[:3000]}

{('[대본감수자 지적 — 대표가 그대로 적용하라고 함. 아래를 모두 고친다(빠진 씬·대사는 원고에서 그대로 되살리고, 순서·번호·장소 구분을 바로잡는다)]' + chr(10) + json.dumps(p.get('fix_issues'), ensure_ascii=False)[:8000] + chr(10) + '[대표 원고(빠진 것 되살리기용)]' + chr(10) + (p.get('original') or '')[:40000] + chr(10) + chr(10)) if p.get('fix_issues') else ''}[대표가 승인한 흐름 변경 — 이번 정리본에 반영]
{json.dumps([{k: a.get(k) for k in ('scene_no', 'kind', 'proposed_text', 'reason')} for a in approved], ensure_ascii=False) if approved else '(없음)'}

[원고]
{src['content']}"""
    progress(f"작가실이 {e.get('number')}화 원고를 읽고 감정선을 정리하는 중")
    text, _ = claude(model, system, user, max_tokens=32000, stage="script_revision", episode_id=ep, note="작가실 정리본")
    script = pick_tag(text, "draft")
    # 긴 화는 답이 길이 제한에 걸려 정리본이 중간에 잘린다 → 마지막 완성된 씬까지 살리고 그 뒤를 이어서 쓰게 한다(최대 3번)
    for k in range(3):
        if script or "<draft>" not in text:
            break
        part = text.split("<draft>", 1)[1]
        heads = list(SCENE_HEAD.finditer(part))
        if len(heads) < 2:
            break
        keep = part[:heads[-1].start()].rstrip()          # 잘린 마지막 씬은 버림
        last_no = int(heads[-2].group(1))
        progress(f"작가실 정리본이 길어 이어서 쓰는 중({k + 1}) — S#{last_no + 1}부터")
        cont, _ = claude(model, system, user + f"""

[이어서 쓰기] 정리본이 길어 S#{last_no}까지만 썼다. 아래는 지금까지 쓴 정리본의 끝부분이다. 원고에서 그 다음 내용부터 끝까지 같은 규칙으로 이어서,
씬 번호는 S#{last_no + 1}부터 이어 붙여 <draft>…</draft>에 넣는다(앞부분을 다시 쓰지 않는다). JSON은 마지막에 한 번만.
[지금까지 쓴 끝부분]
{keep[-2500:]}""", max_tokens=32000, stage="script_revision", episode_id=ep, note=f"작가실 정리본(이어서 {k + 1})")
        more = pick_tag(cont, "draft") or (cont.split("<draft>", 1)[1] if "<draft>" in cont else "")
        text = "<draft>\n" + keep + "\n\n" + more.strip() + ("\n</draft>" if pick_tag(cont, "draft") else "") + "\n" + cont.split("</draft>", 1)[-1]
        script = pick_tag(text, "draft")
    meta = safe_json(text)
    if not script or len(script) < len(src["content"]) * 0.5:
        raise RuntimeError("정리본이 원고보다 너무 짧게 나왔습니다(잘림 의심). 다시 시도해 주세요")
    vid = rpc("agent_save_script_draft", p_token=TOKEN, p_episode=ep, p_content=script,
              p_note=f"작가실 정리본 · 감정 곡선: {meta.get('emotion_curve', '')}"[:480])
    props = [x for x in (meta.get("proposals") or []) if x.get("proposed_text")]
    for x in props:
        rpc("agent_add_story_proposal", p_token=TOKEN, p={**x, "episode_id": ep, "script_version_id": vid})
    if approved:
        rpc("agent_mark_proposal_applied", p_token=TOKEN, p_ids=[a["id"] for a in approved])
    changes = meta.get("changes") or []
    summary = (meta.get("summary") or "") + f"\n변경 {len(changes)}곳" + (f" · 흐름 변경 제안 {len(props)}건(대표 승인 필요)" if props else "")
    confirmed = None
    if p.get("fix_issues"):   # 대본 감수 결과를 대표가 '그대로 적용'으로 맡긴 것 → 바로 확정(→ 감독이 컷 설계를 다시). 흐름 변경 제안은 정리본에 안 들어가고 따로 대표 결정을 기다린다
        confirmed = rpc("agent_confirm_script", p_token=TOKEN, p_id=vid, p_summary="대본 감수 반영: " + summary[:300])
    elif wr.get("auto_confirm") is True and not props and not ppl:   # PPL을 넣고 축약한 수정본은 늘 대표가 읽고 확정
        confirmed = rpc("agent_confirm_script", p_token=TOKEN, p_id=vid, p_summary=summary)   # 확정되면 DB가 감독 컷 설계를 바로 맡긴다
    else:
        notify(f"{e.get('number')}화 작가실 {'PPL 반영 수정본' if ppl else '정리본'} 도착", summary[:300] + "\n대본 탭(또는 지시 탭)에서 확정하면 감독이 바로 컷 설계를 시작합니다", "warning" if props or ppl else "info")
    return f"정리본 저장" + (f", v{confirmed}로 확정 → 감독 컷 설계 시작" if confirmed else ", 확정 대기") + f" · 변경 {len(changes)}곳 · 제안 {len(props)}건"


# ---------------- 감독 + 감정감수자
CUT_SCHEMA = """{"scene_no": 1, "cut_no": 1, "code": "E01-S01-C01", "scene_heading": "S#1 | 00:00–00:12 | 장소 | 낮",
 "seconds": 10, "summary": "이 컷에서 일어나는 일", "dialogue": "린: (떨리며, 점점 높아지며) 대사 그대로",
 "emotion": "감정선", "expression_beats": [{"t": "0-3", "who": "린", "face": "놀람 → 입술을 깨묾"}, {"t": "3-5", "who": "린", "face": "…"}],
 "camera": "샷·앵글·움직임", "reference_names": ["린", "연습실"],
 "prompt": "Seedance에 보낼 영어 프롬프트(대사는 한국어 원문 그대로 따옴표)", "director_note": "반영한 메모·교훈",
 "voice_lines": [{"speaker": "린", "text": "대사 그대로(괄호 지문 빼고)", "tags": "[nervous] [whispers]", "direction": "작게 떨리며 시작 → 끝에서 목소리가 갈라지며 높아짐"}],
 "frame_first": {"prompt": "첫 장면 사진용 영어 프롬프트: 인물 고정 묘사·의상·자세·첫 표정·장소·조명·구도"},
 "use_end_frame": false, "frame_last": {"prompt": "끝 장면이 중요할 때만: 마지막 순간의 자세·표정·위치"},
 "continue_prev": false}"""


def director_system(ctx):
    asp = setting(ctx, "generation", "aspect_ratio", "9:16")
    return (guide(ctx, "감독") or "너는 실사 AI 드라마 감독이다.") + f"""

[시리즈 바이블 — 고정 묘사는 프롬프트에 그대로 옮긴다]
{bible_text(ctx)}

[감독 교훈 — 지난 감수에서 배운 것, 먼저 적용]
{lessons_text(ctx, 30, "감독")}

[컷 설계 규칙]
- 씬 번호는 대본에 나온 순서대로 1부터. 컷은 4~15초. 대사는 한 글자도 바꾸지 않는다.
- 표정은 2~3초마다 바뀌도록 expression_beats를 컷 길이 전체에 빈틈없이 적는다.
- 프롬프트: 인물 고정 묘사(바이블), 의상, 장소, 조명, 카메라, 시간대별 동작·표정(0-3s, 3-5s …), 대사(화자와 한국어 원문, 톤).
- 목소리는 Seedance가 영상과 함께 만든다(대표 결정): 프롬프트의 대사마다 화자의 목소리를 영어로 적는다 — 기본은 "young adult in their early-to-mid 20s, soft gentle natural human voice, natural Korean delivery, sentence endings softly trailing off" + 이 대사의 감정 변화(작게 떨리며 시작 → 끝에서 커짐 등). 같은 인물은 모든 컷에서 같은 목소리 문장을 쓴다.
- 소리: 대사와 현장음만. 배경음악은 넣지 않는다 → 프롬프트 끝에 "No background music, no score. Dialogue and natural ambient sound only." 를 반드시 붙인다(편집에서 음악을 따로 깐다).
- 화면 비율 {asp}. 자막·글자를 화면에 넣지 않는다(화면 글자가 대본에 있으면 summary에만 적는다).
- reference_names에는 이 컷 화면에 나오는 바이블 항목 이름을 바이블 표기 그대로(괄호·수식어 붙이지 말 것): 인물 최대 3, 장소 또는 배경 최대 1, PPL·중요 소품 최대 1. 사진이 없는 항목도 넣는다(제작 때 임시 기준 사진을 만들어 고정한다).
- voice_lines: 대사 한 줄마다 화자·대사·감정 지시. tags는 ElevenLabs v3 영어 감정 태그(예: [nervous] [whispers] [excited] [sighs] [crying] [laughs])로, direction은 한국어로 크기·높낮이·속도 변화를 적는다.
  direction에는 끝음 처리도 적는다(예: "끝음이 살짝 내려가며 흐려짐", "끝에 짧은 숨"). 모든 목소리는 젊고 나긋한 사람 목소리가 기본이고, 끝음이 뚝 끊기거나 기계적이면 안 된다.
- frame_first: 모든 컷. 첫 0.5초의 정지 장면(얼굴·의상·배경 고정의 기준). use_end_frame은 컷 안에서 자세·위치·감정이 크게 바뀌어 끝 상태가 중요할 때만 true(일어서기·문 나가기·웃다가 울기 등). 대부분 false.
- continue_prev: 같은 씬에서 앞 컷과 동작이 바로 이어지면 true(앞 컷 마지막 장면에서 시작).

[답 형식]
<json>{{"cuts": [{CUT_SCHEMA}]}}</json>"""


def review_cuts(ctx, ep, cuts, model, script_text=None):
    system = (guide(ctx, "감정감수자") or guide(ctx, "감정감수자") or "너는 감정감수자다.") + f"""

[시리즈 바이블]
{bible_text(ctx)}

[감독 교훈(위반 여부도 확인)]
{lessons_text(ctx)}

[확인할 것 — 감정감수자는 '소리와 감정'의 책임자다]
1) 대사가 대본과 한 글자도 다르지 않은가.
2) 모든 소리가 대사의 감정선을 따라가는가: 화자별 목소리 톤·크기(속삭임~외침)·높낮이(피치)·말 속도·숨·떨림이 프롬프트에 감정 순서대로 적혔는가
   (예: "작게 떨리며 시작 → 마지막 단어에서 목소리가 갈라지며 높아짐"). 앞뒤 컷과 감정 온도가 끊기지 않는가.
3) 현장음·배경 소리(공항 안내 방송, 연습실 음악 소리 등)가 감정을 돕는가, 대사를 덮지 않는가. 배경음악 금지 문구가 있는가.
4) 같은 인물의 목소리 묘사가 바이블 목소리 항목·지난 컷과 같은가(나이·음색·억양이 바뀌면 안 됨).
5) 최초 기획 일관성: 인물 얼굴·의상·장소 묘사가 바이블 고정 문장 그대로인가, 이름 표기가 맞는가.
6) 표정이 2~3초마다 바뀌게 설계됐는가(세부 판단은 영상감수자), 4~15초인가, 프롬프트가 한 장면만 묘사하는가.

[답 형식]
<json>{{"cuts": [{{"scene_no": 1, "cut_no": 1, "verdict": "pass|fix", "issues": ["…"], "fix": "감독이 고칠 것"}}],
 "lessons": [{{"fingerprint": "짧은-영문-열쇠", "category": "face|costume|location|prop|continuity|expression|emotion_tone|dialogue|camera|motion|prompt|other",
               "problem": "", "rule": "앞으로 이렇게", "bad_example": "", "severity": "보통|치명"}}]}}</json>"""
    text, _ = claude(model, system, "[대본]\n" + (script_text if script_text is not None else (ctx.get("script") or {}).get("content", ""))[:60000]
                     + "\n\n[감독 컷 설계]\n" + json.dumps(cuts, ensure_ascii=False),
                     max_tokens=8000, stage="prompt_review", episode_id=ep, note="감정감수자 컷 감수")
    return pick_json(text)


def est_cut_usd(ctx, seconds, refs):
    res = setting(ctx, "generation", "resolution", "480p")
    vm = VIDEO_MODELS.get(setting(ctx, "generation", "video_model", "seedance-2.5") or "seedance-2.5", VIDEO_MODELS["seedance-2.5"])
    if res not in vm["res"]:
        res = vm["res"][0]
    model = f"{vm['key']}-i2v-{res}"   # 컷 설계에서 첫 장면 사진을 만들어 사진→영상으로 만든다
    u = price("higgsfield", model, "per_second") or price("higgsfield", f"{vm['key']}-{'ref' if refs else 't2v'}-{res}", "per_second")
    return round(u * seconds, 4) if u else None


def has_images(ctx, names):
    """감독이 고른 이름을 바이블 표기로 맞추고, 바이블에 없는 이름은 뺀다(사진 유무와 상관없이 남긴다)."""
    out = []
    for n in names or []:
        b = bible_entry(ctx, n)
        if b and b["name"] not in out:
            out.append(b["name"])
    return out


SCENE_HEAD = re.compile(r"^[ \t]*[*#>\[]*[ \t]*S[ \t]*#[ \t]*(\d+)(?:[ \t]*-[ \t]*(\d+))?", re.M)


# 대표 원고의 씬 구분: "00:12–00:48 | 태국 오디션장" (시간 구간 | 장소). S#가 없어도 이 줄을 씬 머리로 본다.
SUB_HEAD = re.compile(r"^([ \t]*[*#>\[]*[ \t]*)S[ \t]*#[ \t]*(\d+)((?:[ \t]*-[ \t]*\d+)*)", re.M)
TIME_HEAD = re.compile(r"^([ \t]*[*#>\[]*[ \t]*)(\d{1,2}:\d{2}(?::\d{2})?[ \t]*[–—\-~][ \t]*\d{1,2}:\d{2}(?::\d{2})?[ \t]*\|)", re.M)


def normalize_scene_heads(content):
    """S# 씬 머리가 하나도 없고 '시간 구간 | 장소' 줄이 있으면 그 줄마다 순서대로 'S#1 | ' 을 붙인다(내용은 그대로).
    예전엔 이 형식을 못 알아봐 화 전체가 씬 하나(S#0)가 되고, 작가실에 화 전체를 나눠 달라고 했다가 실패했다."""
    content = content or ""
    if SCENE_HEAD.search(content):
        # 작가실이 나눈 씬(S#2-1, S#2-2, S#2-1-2…)이 있거나 번호가 겹치면 씬 순서대로 S#1, S#2, S#3…으로 다시 매긴다.
        # 예전엔 나뉜 부분이 모두 '2번 씬'이 돼 감독이 어느 컷이 어느 부분인지 헷갈려 컷을 비우고, 작가실·감독이 계속 다시 돌았다.
        heads = [(int(m.group(2)), m.group(3) or "") for m in SUB_HEAD.finditer(content)]
        nums = [h[0] for h in heads]
        if any(h[1] for h in heads) or len(set(nums)) != len(nums):
            k = [0]

            def renum(m):
                k[0] += 1
                return f"{m.group(1)}S#{k[0]}"
            return SUB_HEAD.sub(renum, content)
        return content
    if not TIME_HEAD.search(content):
        return content
    n = [0]

    def add(m):
        n[0] += 1
        return f"{m.group(1)}S#{n[0]} | {m.group(2)}"
    return TIME_HEAD.sub(add, content)


def split_scenes(content):
    """정리본을 씬(S#번호) 단위로 나눈다. [(번호, 머리줄, 본문)] — 씬 표시가 없으면 통째로 하나."""
    content = normalize_scene_heads(content)
    marks = list(SCENE_HEAD.finditer(content or ""))
    if not marks:
        return [(0, "", content or "")]
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(content)
        body = content[m.start():end].strip()
        out.append((int(m.group(1)), body.splitlines()[0][:120], body))   # 나뉜 씬(S#7-1·7-2)은 normalize_scene_heads가 순서대로 다시 번호를 매긴 뒤라 번호가 겹치지 않는다
    return out


SCENE_MAX_SEC = 30   # 작가실이 나눠 두는 씬 길이 상한(컷 15초 × 2)


def _secs(t):
    p = [int(x) for x in t.split(":")]
    return p[0] * 60 + p[1] if len(p) == 2 else p[0] * 3600 + p[1] * 60 + p[2]


def scene_seconds(head, body):
    """씬 머리의 시간 구간(00:12–00:48)으로 길이(초). 없으면 글자 수로 어림(한국어 대사·지문 약 12자/초)."""
    m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*[–—\-~]\s*(\d{1,2}:\d{2}(?::\d{2})?)", head or "")
    if m:
        d = _secs(m.group(2)) - _secs(m.group(1))
        if d > 0:
            return d
    return len(body or "") // 12


def _mmss(s):
    return f"{s // 60:02d}:{s % 60:02d}"


def mech_split(n, orig, limit=SCENE_MAX_SEC):
    """원문 씬을 줄 단위로 2~6개 부분(S#n-1, S#n-2…)으로 나눈다. 대사·지문은 한 글자도 바꾸지 않고 머리만 붙인다."""
    lines = orig.strip("\n").split("\n")
    head, rest = lines[0], lines[1:]
    if not re.search(r"S\s*#\s*\d+", head):
        head, rest = f"S#{n}", lines
    rest_idx = [i for i, x in enumerate(rest) if x.strip()]
    secs = scene_seconds(head, orig)
    k = min(6, max(2, -(-secs // limit)), len(rest_idx))
    if k < 2:
        return None
    total = sum(len(x) + 1 for x in rest)
    cuts, acc, want = [], 0, 1
    for i, x in enumerate(rest):   # 글 양이 1/k, 2/k…가 되는 줄 앞에서 나눔(빈 줄 앞이면 더 좋음)
        if want < k and acc >= total * want / k and x.strip() and i > (cuts[-1] if cuts else 0):
            cuts.append(i); want += 1
        acc += len(x) + 1
    if not cuts:
        return None
    m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*[–—\-~]\s*(\d{1,2}:\d{2}(?::\d{2})?)", head)
    t0, t1 = (_secs(m.group(1)), _secs(m.group(2))) if m else (None, None)
    bounds = [0] + cuts + [len(rest)]
    out = []
    for j in range(len(bounds) - 1):
        h = re.sub(r"S\s*#\s*\d+(?:\s*-\s*\d+)*", f"S#{n}-{j + 1}", head, count=1)
        if m and t1 > t0:
            a = t0 + (t1 - t0) * sum(len(x) + 1 for x in rest[:bounds[j]]) // max(1, total)
            b = t0 + (t1 - t0) * sum(len(x) + 1 for x in rest[:bounds[j + 1]]) // max(1, total)
            h = h.replace(m.group(0), f"{_mmss(a)}–{_mmss(b)}", 1)
        out.append(h + "\n" + "\n".join(rest[bounds[j]:bounds[j + 1]]))
    return "\n".join(out).strip()


def long_scenes(all_sc, limit=SCENE_MAX_SEC):
    """컷 설계 전에 작가실이 먼저 나눠야 할 씬 번호(씬 구분이 없으면 [0])."""
    if all_sc and all_sc[0][0] == 0 and len(all_sc) == 1:
        return [0]
    return [n for n, h, b in all_sc if n > 0 and scene_seconds(h, b) > limit + 3]   # 2~3초 넘는 정도는 그대로


def scene_groups(scenes, max_n=3, max_chars=5000):
    groups, cur, size = [], [], 0
    for s in scenes:
        if cur and (len(cur) >= max_n or size + len(s[2]) > max_chars):
            groups.append(cur); cur, size = [], 0
        cur.append(s); size += len(s[2])
    if cur:
        groups.append(cur)
    return groups


def first_line_of(body):
    for ln in body.splitlines()[1:]:
        if ln.strip():
            return ln.strip()[:60]
    return ""


@task("director")
def t_director(t):
    """감독 컷 설계. 대본을 씬 몇 개씩 나눠 설계한다(한 번에 다 하면 답이 길이 제한에 걸려 잘림).
    한 묶음이 실패하면 씬 하나씩 다시, 그래도 실패한 씬은 어느 화·어느 씬·왜 실패했는지 남긴다."""
    ep = t["episode_id"]
    p = t.get("payload") or {}
    if p.get("cut_id"):
        return director_revise(t, p["cut_id"])
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=p.get("script_version_id"))
    script = ctx.get("script")
    if not script:
        raise RuntimeError("확정된 대본이 없습니다. 대본 탭에서 정리본을 확정해 주세요")
    e = ctx.get("episode") or {}
    epno = int(e.get("number") or 0)
    dmodel = setting(ctx, "generation", "claude_director", "claude-opus-5-5")
    rmodel = setting(ctx, "generation", "claude_reviewer", "claude-sonnet-5-5")
    all_sc = split_scenes(script["content"])
    want_set = set(int(x) for x in (p.get("scenes") or []))
    # 씬 나누기는 작가실 일: 감독이 설계하다 실패한 뒤가 아니라, 설계 전에 긴 씬(30초 넘음)·씬 구분 없는 대본을 작가실이 먼저 나눈다
    longs = long_scenes(all_sc)
    if longs == [0]:
        raise RuntimeError(f"{epno}화 확정 대본에 씬 구분이 없어 화 전체가 씬 하나로 읽혔습니다. "
                           "이렇게 하세요: 대본 탭에서 씬마다 '00:12–00:48 | 장소' 줄을 넣고 확정하거나, '작가실 정리 실행'으로 씬을 나눈 정리본을 받아 확정하세요.")
    if longs and not p.get("prepped") and not want_set:
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "writers_room", "episode_id": ep, "payload": {
            "mode": "split_scenes", "prep": True, "scenes": longs, "source_version_id": script["id"],
            "why": f"컷 설계 전 제작용 씬 나누기: {SCENE_MAX_SEC}초가 넘는 씬은 컷 2개 이내가 되게 나눔"}})
        return (f"{epno}화 씬 {len(all_sc)}개 중 {len(longs)}개(S#{', S#'.join(map(str, longs))})가 {SCENE_MAX_SEC}초를 넘어 "
                "작가실이 먼저 나눕니다(대사 그대로). 끝나면 감독이 이어서 컷을 설계합니다")
    todo = [s for s in all_sc if not want_set or s[0] in want_set or s[0] == 0]
    if not todo:
        raise RuntimeError(f"{epno}화 확정 대본에 요청한 씬 {sorted(want_set)}이(가) 없습니다. 대본 탭에서 씬 번호(S#)를 확인해 주세요")
    heads = "\n".join(h for _, h, _ in all_sc if h)
    base = f"""[대표 연출 메모 — 필수는 반드시 반영]
{notes_text(ctx)}

[직전 화가 끝났을 때 상태 — 이 화 첫 등장은 여기서 이어진다(대본에 바뀐다는 말이 없으면 헤어·의상·상처·소지품 유지)]
{continuity_text(ctx)}

[이 화 전체 씬 목록(흐름 참고)]
{heads or '-'}"""

    def ask(group, tight=False):
        nos = [n for n, _, _ in group]
        i0 = next((i for i, s in enumerate(all_sc) if s is group[0]), 0)
        prev = all_sc[i0 - 1][2] if i0 > 0 else ""
        parts = [re.match(r"\S+", h).group(0).lstrip("*#>[ ") if h else "" for _, h, _ in group]
        label = "모든 씬" if nos == [0] else "씬 " + ", ".join(dict.fromkeys(x.replace("S#", "").strip() or str(n) for x, (n, _, _) in zip(parts, group)))
        user = f"""{epno}화 「{e.get('title') or ''}」 대본 v{script.get('version_no')}의 {label}만 컷으로 설계해 주세요.
컷 code는 E{epno:02d}-S씬번호2자리-C컷번호2자리.

{base}

[연속성 기록]
JSON 최상위에 "end_state"도 함께 준다: 이 씬들이 끝났을 때 주요 인물·소품의 상태(헤어·의상·상처·화장·들고 있는 물건·장소).
형식: "end_state": [{{"name": "바이블 이름 그대로", "scene_no": 마지막 등장 씬, "state": "한국어 한 줄"}}].

{('[바로 앞 씬(이어지는 흐름 참고, 설계하지 말 것)]' + chr(10) + prev[:3000] + chr(10) + chr(10)) if prev else ''}[설계할 대본]
{chr(10).join(b for _, _, b in group)}"""
        if tight:
            user += "\n\n[중요] 지난번 답이 너무 길어 잘렸거나 형식이 깨졌다. 설명 없이 <json>{\"cuts\": [...], \"end_state\": [...]}</json> 하나만. 프롬프트·메모는 짧고 핵심만."
        progress(f"감독이 {epno}화 {label}을(를) 컷으로 나누는 중")
        text, _ = claude(dmodel, director_system(ctx), user, max_tokens=16000, stage="cut_card", episode_id=ep,
                         note=f"감독 컷 설계 {label}")
        d = safe_json(text)
        cuts = [c for c in (d.get("cuts") or []) if isinstance(c, dict)]
        if nos != [0]:
            cuts = [c for c in cuts if inum(c.get("scene_no") or 0) in nos]
        if cuts:
            return cuts, d.get("end_state") or [], None
        if CURRENT.get("last_stop") == "max_tokens":
            why = "감독 답이 길이 제한에 걸려 중간에 잘렸습니다(이 씬의 컷 설계가 너무 길어짐)"
        elif not d:
            why = f"감독이 정해진 형식(JSON) 대신 다른 답을 했습니다. 감독 답 앞부분(대본 아님): {text.strip()[:120]!r}"
        else:
            why = "감독 답에 이 씬의 컷이 비어 있었습니다(씬 번호를 다르게 적었을 수 있음)"
        return [], [], why

    def one_scene(one):   # 씬 하나로 다시, 그래도 안 되면 더 짧게 한 번 더
        cs1, es1, why1 = ask([one])
        if not cs1:
            cs1, es1, why1 = ask([one], tight=True)
        if cs1:
            return cs1, es1, None
        n, h, b = one
        return [], [], (n, "길이 제한" in (why1 or ""), f"{epno}화 {h.split('|')[0].strip() or f'S#{n}'} 「{h.split('|')[2].strip() if h.count('|') >= 2 else h}」"
                        f"(첫 줄: {first_line_of(b) or '-'}) — 원인: {why1}. 씬 하나로 줄여 두 번 더 시도했지만 같았습니다.")

    def design_group(g):   # 묶음 설계 → 실패하면 그 묶음의 씬들을 동시에(예전엔 하나씩 차례로) 다시
        try:
            cs, es, why = ask(g)
        except Exception as ex:
            cs, es, why = [], [], f"감독 요청 오류: {ex}"
        if cs:
            return cs, es, []
        out_c, out_e, out_f = [], [], []
        for r, one in zip(parallel([(lambda one=one: one_scene(one)) for one in g], workers=len(g)), g):
            if isinstance(r, Exception):
                n, h, _b = one
                out_f.append((n, False, f"{epno}화 S#{n} — 원인: 감독 요청 오류: {r}"))
                continue
            cs1, es1, f1 = r
            out_c += cs1; out_e += es1
            if f1:
                out_f.append(f1)
        return out_c, out_e, out_f

    cuts, end_state, failed = [], [], []
    groups = scene_groups(todo)
    progress(f"감독이 {epno}화 씬 묶음 {len(groups)}개를 동시에 설계하는 중")
    for g, r in zip(groups, parallel([(lambda g=g: design_group(g)) for g in groups], workers=min(8, max(1, len(groups))))):
        if isinstance(r, Exception):
            for n, h, _b in g:
                failed.append((n, False, f"{epno}화 S#{n} — 원인: 감독 요청 오류: {r}"))
            continue
        cuts += r[0]; end_state += r[1]; failed += r[2]
    # 실패 처리: 씬 나누기는 설계 전에 작가실이 끝냈다(긴 씬은 먼저 나눔) → 감독은 작가실에 나누기를 요청하지 않고, 실패한 씬만 한 번 더(짧게) 시도한다
    other_nos = sorted({n for n, _, _ in failed})
    lines = [x for _, _, x in failed]
    asked = []
    if 0 in other_nos:   # 씬 구분이 아예 없는 대본(설계 전 점검을 지나친 경우)
        raise RuntimeError(f"{epno}화 확정 대본에 씬 구분이 없어 화 전체가 씬 하나로 읽혔습니다(감독이 한 번에 설계할 수 없는 길이). "
                           "이렇게 하세요: 대본 탭에서 씬마다 '00:12–00:48 | 장소' 줄을 넣고 확정하거나, '작가실 정리 실행'으로 씬을 나눈 정리본을 받아 확정하세요.")
    if other_nos and not p.get("retry_scenes"):
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "director", "episode_id": ep,
            "payload": {"script_version_id": script["id"], "scenes": other_nos, "retry_scenes": True, "prepped": True}})
        asked.append(f"감독이 S#{', S#'.join(map(str, other_nos))}을(를) 한 번 더 시도합니다")
    left = [x for _, _, x in failed] if p.get("retry_scenes") else []
    if not cuts and not asked:
        raise RuntimeError("씬별 결과 — 감독이 한 씬도 설계하지 못했습니다.\n" + "\n".join(lines[:8])
                           + "\n이렇게 하세요: 다시 시도하세요. 대본 자체를 고쳐야 하면 '작가실에 요청'에 무엇을 바꿀지 적어 보내세요.")
    if not cuts:
        notify(f"{epno}화 컷 설계: 실패한 씬을 감독이 한 번 더 시도합니다", "\n".join(asked + lines[:5]), "warning")
        return "설계된 씬 없음 · " + " / ".join(asked)
    failed = lines
    seq = {}
    for c in cuts:   # 같은 씬이 여러 부분(S#7-1·7-2)으로 설계됐으면 컷 번호를 이어서 다시 매긴다(감수 전에)
        sn = inum(c.get("scene_no") or 0)
        c["scene_no"] = sn   # 'S#7-1' 같은 글자 번호도 숫자로 맞춰 저장(DB는 정수)
        seq[sn] = seq.get(sn, 0) + 1
        c["cut_no"] = seq[sn]
        c["code"] = f"E{epno:02d}-S{sn:02d}-C{seq[sn]:02d}"
    def fix_part(part, verdicts):
        fix_text = json.dumps([{"cut": c, "review": verdicts[(inum(c['scene_no']), inum(c['cut_no']))]} for c in part], ensure_ascii=False)
        scs = {inum(c["scene_no"]) for c in part}
        src = "\n\n".join(b for n, _, b in all_sc if n in scs or n == 0)
        text, _ = claude(dmodel, director_system(ctx), f"감정감수자 의견을 반영해 아래 컷들만 고쳐서 같은 형식으로 다시 주세요.\n{fix_text}\n\n[대본]\n{src}",
                         max_tokens=12000, stage="cut_card", episode_id=ep, note="감독 감수 반영")
        return {(inum(c.get("scene_no") or 0), inum(c.get("cut_no") or 0)): c for c in safe_json(text).get("cuts") or [] if isinstance(c, dict)}

    def review_fix(chunk):   # 이 묶음만 감수하고, 고칠 컷이 있으면 곧바로 고친다
        scs = {inum(c["scene_no"]) for c in chunk}
        src = "\n\n".join(b for n, _, b in all_sc if n in scs or n == 0)
        try:
            rv = review_cuts(ctx, ep, chunk, rmodel, script_text=src)
        except Exception as ex:   # 감수가 깨져도 설계는 살린다(감수는 생략하고 기록)
            log(f"감정감수자 감수 실패(설계는 저장): {ex}")
            return {}, {}, []
        vd = {(inum(r.get("scene_no") or 0), inum(r.get("cut_no") or 0)): r for r in rv.get("cuts") or [] if isinstance(r, dict)}
        fx = [c for c in chunk if (vd.get((inum(c.get("scene_no") or 0), inum(c.get("cut_no") or 0))) or {}).get("verdict") == "fix"]
        fixed_c = {}
        for r in parallel([(lambda part=fx[i:i + 5]: fix_part(part, vd)) for i in range(0, len(fx), 5)], workers=4):
            if isinstance(r, Exception):
                log(f"감독 감수 반영 일부 실패(원래 설계 유지): {r}")
            else:
                fixed_c.update(r)
        return vd, fixed_c, rv.get("lessons") or []

    chunks, cur_c, cur_s = [], [], set()   # 씬 단위로 묶되 한 묶음은 씬 3개·컷 12개 정도까지
    for sn in sorted({inum(c["scene_no"]) for c in cuts}):
        sc_cuts = [c for c in cuts if inum(c["scene_no"]) == sn]
        if cur_c and (len(cur_s) >= 3 or len(cur_c) + len(sc_cuts) > 12):
            chunks.append(cur_c); cur_c, cur_s = [], set()
        cur_c += sc_cuts; cur_s.add(sn)
    if cur_c:
        chunks.append(cur_c)
    verdicts, fixed, lessons = {}, {}, []
    auto_rv = bool(setting(ctx, "generation", "auto_review", False))   # 대표 결정: 감수는 수동(대본 감수·생성 후 감수 버튼). 켜면 예전처럼 설계 중 자동 감수
    if auto_rv:
        progress(f"감정감수자가 컷 {len(cuts)}개를 {len(chunks)}묶음으로 나눠 동시에 감수하고, 고칠 컷은 감독이 바로 고치는 중")
    for r in parallel([(lambda ch=ch: review_fix(ch)) for ch in chunks] if auto_rv else [], workers=min(8, max(1, len(chunks)))):
        if isinstance(r, Exception):
            log(f"감수 묶음 실패(설계는 저장): {r}")
            continue
        verdicts.update(r[0]); fixed.update(r[1]); lessons += r[2]
    fixes = [c for c in cuts if (verdicts.get((inum(c.get("scene_no") or 0), inum(c.get("cut_no") or 0))) or {}).get("verdict") == "fix"]
    for l in lessons:
        try:
            rpc("agent_add_lesson", p_token=TOKEN, p_lesson={**l, "series_id": (ctx.get("series") or {}).get("id"), "sources": ["감정감수자"]})
        except Exception as ex:
            log(f"교훈 기록 실패: {ex}")
    cuts = [{**fixed[(inum(c["scene_no"]), inum(c["cut_no"]))], "scene_no": c["scene_no"], "cut_no": c["cut_no"], "code": c.get("code")}
            if (inum(c["scene_no"]), inum(c["cut_no"])) in fixed else c for c in cuts]   # 고친 컷도 번호·코드는 그대로
    by_scene = {}
    for c in cuts:
        k = (inum(c.get("scene_no") or 0), inum(c.get("cut_no") or 0))
        r = verdicts.get(k) or {}
        c["review"] = {"verdict": "revised" if r.get("verdict") == "fix" else (r.get("verdict") or ("pass" if verdicts else "skipped")),
                       "issues": r.get("issues") or [], "fix": r.get("fix")}
        c["reference_names"] = has_images(ctx, c.get("reference_names"))
        c["seconds"] = max(4, min(15, inum(c.get("seconds"), 10) or 10))
        c["resolution"] = setting(ctx, "generation", "resolution", "480p")
        c["est_usd"] = est_cut_usd(ctx, c["seconds"], c["reference_names"])
        by_scene.setdefault(k[0], []).append(c)
    n = 0
    for sc, cs in sorted(by_scene.items()):
        n += rpc("agent_save_cuts", p_token=TOKEN, p_episode=ep, p_version=script["id"], p_scene=sc, p_cuts=cs) or 0
    total = sum(c.get("est_usd") or 0 for c in cuts)
    if not want_set and end_state:
        try:
            k = rpc("agent_add_continuity", p_token=TOKEN, p_episode=ep, p_items=end_state, p_source="감독(컷 설계)")
            log(f"연속성 기록 {k}건(이 화가 끝났을 때 상태)")
        except Exception as ex:
            log(f"연속성 기록 실패: {ex}")
    if setting(ctx, "generation", "auto_prep", True):
        for sc in sorted(by_scene):
            rpc("agent_add_task", p_token=TOKEN, p={"kind": "prep_cut", "episode_id": ep, "scene_no": sc, "payload": {}})
    if failed:
        if left:
            raise RuntimeError(f"씬별 결과 — 씬 {len(by_scene)}개는 설계·저장했고, 아래 씬은 감독이 한 번 더 시도한 뒤에도 실패했습니다.\n" + "\n".join(left[:8])
                               + ("\n" + "\n".join(asked) if asked else "")
                               + "\n이렇게 하세요: '작가실에 요청'에 무엇을 바꿀지 적어 보내거나, 다시 시도(실패한 씬만 다시 합니다).")
        notify(f"{epno}화 컷 설계: 씬 {len(by_scene)}개 완료, {len(failed)}개는 감독이 다시 시도", "\n".join(asked + failed[:5]), "warning")
    if not failed:
        notify(f"{epno}화 컷 설계 완료", f"씬 {len(by_scene)}개 · 컷 {n}개 · 감수 수정 {len(fixes)}건\n"
               f"영상 생성 예상 ${total:.2f}. 제작 탭에서 확인하고 생성 승인을 눌러 주세요.")
    return f"씬 {len(by_scene)}개 · 컷 {n}개 설계 (감수 수정 {len(fixes)}건) · 생성 예상 ${total:.2f}" + (f" · 실패 씬 {len(failed)}개는 감독이 한 번 더 시도" if failed else "")


def owner_to_bible(ctx, ep, cut, text):
    """제작이 최우선, 대표 지시가 최우선: 보완 요청 중 '계속 유지할 변경'(인물 생김새·의상 기본값·장소·소품·목소리)은 바이블에도 저장한다.
    이 컷만의 연출(표정·카메라·동작·타이밍)은 바이블에 넣지 않는다. 바이블 변경은 대표 본인의 지시라 바로 적용한다."""
    names = [{"name": b.get("name"), "category": b.get("category")} for b in ctx.get("bible") or []]
    if not names:
        return []
    out, _ = claude("claude-sonnet-5-5", """너는 코디네이터다. 대표가 제작 중 컷에 남긴 수정 요청을 읽고, 바이블(시리즈 고정 설정)에 저장해야 할 '계속 유지할 변경'만 뽑는다.
- 저장: 인물의 생김새·머리·체형·기본 의상·소품·장소의 모습처럼 다른 장면에서도 계속 그래야 하는 것, 그리고 목소리(톤·밝기·나이감·말투).
- 저장 안 함: 이 컷의 표정·동작·카메라·타이밍·대사 연기처럼 이 장면에만 해당하는 연출. 애매하면 저장 안 함.
- name은 아래 바이블 이름 그대로. change는 바이블 묘사에 반영할 한국어 한두 문장(무엇을 어떻게). 목소리면 kind=voice, voice_hint에 원하는 목소리.
답: <json>{"changes": [{"name": "", "kind": "look|voice", "change": "", "voice_hint": ""}]}</json>""",
        json.dumps({"수정 요청": text, "컷": {k: cut.get(k) for k in ("code", "summary", "dialogue", "reference_names")}, "바이블 항목": names}, ensure_ascii=False),
        max_tokens=1500, stage="other", episode_id=ep, note="대표 보완 요청 → 바이블 저장 판단")
    done = []
    for c in (safe_json(out) or {}).get("changes") or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        try:
            if c.get("kind") == "voice":
                eid = rpc("agent_bible_owner_change", p_token=TOKEN, p_episode=ep, p_name=c["name"], p_reason="")
                if eid:
                    rpc("agent_add_task", p_token=TOKEN, p={"kind": "voice_cast", "series_id": (ctx.get("series") or {}).get("id"),
                        "payload": {"series_id": (ctx.get("series") or {}).get("id"), "entry_ids": [eid], "redo": True, "auto": True, "force": True,
                                    "hint": (c.get("voice_hint") or c.get("change") or text)[:300]}})
                    done.append(f"{c['name']} 목소리 → {(c.get('voice_hint') or c.get('change') or '')[:60]}")
            elif (c.get("change") or "").strip():
                why = re.sub(r"목소리", "말소리", c["change"].strip())[:300]
                if rpc("agent_bible_owner_change", p_token=TOKEN, p_episode=ep, p_name=c["name"], p_reason=why):
                    done.append(f"{c['name']}: {why[:60]}")
        except Exception as ex:
            log(f"바이블 저장 실패({c.get('name')}): {ex}")
    if done:
        notify("제작 중 대표 지시를 바이블에도 저장", "\n".join(done)[:600])
    return done


def director_revise(t, cut_id):
    cut = rpc("agent_get_cut", p_token=TOKEN, p_id=cut_id)
    ep = cut["episode_id"]
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=cut.get("script_version_id"))
    fr = (cut.get("fix_request") or "").strip()
    if fr and not fr.startswith("[자동 재시도") and not (t.get("payload") or {}).get("auto_retry") and not (t.get("payload") or {}).get("bible_synced"):
        try:
            saved = owner_to_bible(ctx, ep, cut, fr)
            if saved:
                log("대표 지시 → 바이블 저장: " + " / ".join(saved))
        except Exception as ex:
            log(f"대표 지시 바이블 저장 판단 실패(컷 수정은 계속): {ex}")
    last = (cut.get("takes") or [{}])[-1] if cut.get("takes") else {}
    dmodel = setting(ctx, "generation", "claude_director", "claude-opus-5-5")
    progress(f"감독이 {cut.get('code')} 프롬프트를 고치는 중")
    user = f"""아래 컷 하나를 고쳐서 같은 형식(cuts 배열에 1개)으로 주세요. 대사는 바꾸지 않습니다.
[지금 컷]
{json.dumps({k: cut.get(k) for k in ('scene_no', 'cut_no', 'code', 'scene_heading', 'seconds', 'summary', 'dialogue', 'emotion', 'expression_beats', 'camera', 'reference_names', 'prompt')}, ensure_ascii=False)}
[대표 지시 — 최우선. 아래를 빠짐없이 프롬프트에 반영한다. 바이블·감수 의견과 부딪히면 대표 지시를 따른다]
{cut.get('fix_request') or '(없음)'}
[지난 영상 감수 결과(참고, 대표 지시보다 뒤)]
{json.dumps(last.get('review') or {}, ensure_ascii=False)}
[같은 씬의 다른 컷]
{json.dumps(cut.get('scene_cuts') or [], ensure_ascii=False)}"""
    text, _ = claude(dmodel, director_system(ctx), user, max_tokens=6000, stage="cut_card", episode_id=ep,
                     scene_no=cut.get("scene_no"), cut_code=cut.get("code"), note="감독 컷 수정")
    new = (pick_json(text).get("cuts") or [None])[0]
    if not new or not new.get("prompt"):
        raise RuntimeError("감독이 고친 프롬프트를 만들지 못했습니다")
    refs = has_images(ctx, new.get("reference_names") or cut.get("reference_names"))
    secs = max(4, min(15, int(new.get("seconds") or cut.get("seconds") or 10)))
    then_gen = bool((t.get("payload") or {}).get("then_generate"))   # 대표가 '보완해서 다시 만들기'로 생성까지 맡긴 것
    rpc("agent_update_cut", p_token=TOKEN, p_id=cut_id, p={
        "status": "approved" if then_gen else "designed", "prompt": new["prompt"], "expression_beats": new.get("expression_beats") or cut.get("expression_beats"),
        "reference_names": refs, "director_note": new.get("director_note"), "est_usd": est_cut_usd(ctx, secs, refs), "clear_fix": True,
        "voice_lines": new.get("voice_lines") or cut.get("voice_lines") or [], "frame_first": new.get("frame_first") or {"prompt": (cut.get("frame_first") or {}).get("prompt")},
        "frame_last": new.get("frame_last") or {}, "use_end_frame": bool(new.get("use_end_frame")), "continue_prev": bool(new.get("continue_prev")), "prep_status": "none",
        "review": {"verdict": "revised", "issues": [], "fix": "대표 요청·영상 감수 반영"}})
    if setting(ctx, "generation", "auto_prep", True):
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "prep_cut", "episode_id": ep, "scene_no": cut.get("scene_no"), "cut_id": cut_id,
                                               "payload": {"force": True, "auto_retry": bool((t.get("payload") or {}).get("auto_retry"))}})
    if then_gen:
        notify(f"{cut.get('code')} 보완 완료 → 다시 만드는 중", "대표 보완 내용을 반영해 프롬프트를 고쳤고, 바로 영상을 다시 만듭니다", "info")
    elif (t.get("payload") or {}).get("auto_retry"):
        notify(f"{cut.get('code')} 자동 재시도", "감수 불합격이라 감독이 고쳤고, 사진·목소리를 다시 준비한 뒤 자동으로 다시 생성합니다", "info")
    else:
        notify(f"{cut.get('code')} 수정 완료", "사진·목소리를 다시 준비한 뒤 제작 탭에서 생성 승인을 눌러 주세요")
    return f"{cut.get('code')} 프롬프트 수정"


# ---------------- 저장소(Supabase) 올리기·받기: 출입증을 받아 정해진 파일만
def sb_headers(extra=None):
    h = {"apikey": KEY}
    if not KEY.startswith("sb_"):
        h["Authorization"] = "Bearer " + KEY
    h.update(extra or {})
    return h


def storage_upload(path, local, ctype):
    rpc("agent_storage_grant", p_token=TOKEN, p_bucket="takes", p_path=path, p_mode="write")
    with open(local, "rb") as f:
        data = f.read()
    req = urllib.request.Request(f"{URL}/storage/v1/object/takes/{path}", data=data, method="POST",
                                 headers=sb_headers({"Content-Type": ctype, "x-upsert": "false", "cache-control": "3600"}))
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"저장소 올리기 실패 {e.code}: {e.read()[:200]!r}") from None
    return path


def storage_signed_url(bucket, path, seconds=3600):
    rpc("agent_storage_grant", p_token=TOKEN, p_bucket=bucket, p_path=path, p_mode="read")
    code, data = http_json(f"{URL}/storage/v1/object/sign/{bucket}/{path}", sb_headers({"Content-Type": "application/json"}),
                           "POST", {"expiresIn": seconds}, timeout=30)
    if code != 200 or not (data or {}).get("signedURL"):
        raise RuntimeError(f"사진 주소 만들기 실패 {code}: {data}")
    return f"{URL}/storage/v1{data['signedURL']}"


def fetch_to(url, dest, headers=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "nyx-agent"})
    tmp = f"{dest}.{uuid.uuid4().hex[:6]}.part"   # 여러 줄이 같은 파일을 동시에 받아도 서로 덮어쓰지 않게
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    os.replace(tmp, dest)
    return dest


# ---------------- ffmpeg (uv로 자동 준비, 관리자 권한 불필요)
FFMPEG = {"path": None}
FFMPEG_LOCK = threading.Lock()


def uv_path():
    cands = [shutil.which("uv"), os.path.expanduser("~/.local/bin/uv"), os.path.expanduser("~/.local/bin/uv.exe"),
             os.path.join(os.environ.get("USERPROFILE", ""), ".local", "bin", "uv.exe"), "/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
    return next((c for c in cands if c and os.path.exists(c)), None)


def ffmpeg():
    if FFMPEG["path"] and os.path.exists(FFMPEG["path"]):
        return FFMPEG["path"]
    with FFMPEG_LOCK:   # 처음 준비는 한 줄만(11개 줄이 동시에 내려받던 문제)
        return _ffmpeg_setup()


def _ffmpeg_setup():
    if FFMPEG["path"] and os.path.exists(FFMPEG["path"]):
        return FFMPEG["path"]
    p = shutil.which("ffmpeg")
    if not p:
        cache = os.path.join(HOME, "ffmpeg.json")
        try:
            with open(cache, encoding="utf-8") as f:
                p = json.load(f).get("path")
        except Exception:
            p = None
        if not p or not os.path.exists(p):
            uv = uv_path()
            if not uv:
                raise RuntimeError("영상 도구(ffmpeg)를 준비할 uv를 찾지 못했습니다")
            progress("영상 도구(ffmpeg)를 처음 한 번 준비하는 중")
            out = subprocess.run([uv, "run", "--no-project", "--python", "3.12", "--with", "imageio-ffmpeg", "python", "-c",
                                  "import imageio_ffmpeg as m; print(m.get_ffmpeg_exe())"],
                                 capture_output=True, text=True, timeout=900)
            p = (out.stdout or "").strip().splitlines()[-1] if out.stdout.strip() else None
            if not p or not os.path.exists(p):
                raise RuntimeError(f"ffmpeg 준비 실패: {(out.stderr or '')[-300:]}")
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"path": p}, f)
    FFMPEG["path"] = p
    return p


def ff(*args):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {(r.stderr or '')[-300:]}")


def ff_long(*args, timeout=7200):
    """오래 걸리는 ffmpeg(업스케일 등): 2시간까지 기다린다."""
    r = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {(r.stderr or '')[-300:]}")


def has_audio(src):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", src], capture_output=True, text=True, timeout=60)
    return "Audio:" in (r.stderr or "")


def split_streams(src, video_out, audio_out):
    """원본을 영상(무음)과 대사·현장음(wav)으로 나눈다."""
    os.makedirs(os.path.dirname(video_out), exist_ok=True)
    ff("-i", src, "-an", "-c:v", "copy", video_out)
    if has_audio(src):
        os.makedirs(os.path.dirname(audio_out), exist_ok=True)
        ff("-i", src, "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", audio_out)
        return True
    return False


# ---------------- 씬 폴더: 집 윈도우(OneDrive) 또는 이 컴퓨터
def safe(s, n=40):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(s or "")).strip()
    return re.sub(r"\s+", " ", s)[:n].strip() or "제목없음"


def local_root(settings_value):
    st = settings_value or {}
    if PLATFORM == "windows":
        raw = st.get("windows_folder") or r"%OneDrive%\NYX"
        if "%OneDrive%" in raw and not os.environ.get("OneDrive"):
            raw = raw.replace("%OneDrive%", os.environ.get("USERPROFILE", os.path.expanduser("~")))
        root = os.path.expandvars(raw)
    else:
        root = os.path.expanduser(st.get("mac_folder") or "~/Movies/NYX")
    os.makedirs(root, exist_ok=True)
    return root


SUB = {"video": "01_영상(무음)", "voice": "02_대사·현장음", "bgm": "03_배경음악", "original": "04_원본(소리포함)"}


def scene_dir(root, take):
    e, c = take.get("episode") or {}, take.get("cut") or {}
    heading = (take.get("package") or {}).get("heading") or c.get("scene_heading") or ""
    place = heading.split("|")[2] if heading.count("|") >= 2 else heading
    d = os.path.join(root, safe(take.get("series_title") or "시리즈"), f"EP{int(e.get('number') or 0):02d}_{safe(e.get('title'), 30)}",
                     f"S{int(c.get('scene_no') or 0):02d}_{safe(place, 30)}")
    for s in SUB.values():
        os.makedirs(os.path.join(d, s), exist_ok=True)
    return d


SCENE_INFO_LOCK = threading.Lock()


def write_scene_info(d, take, files):
    """씬정보.json 갱신. 여러 영상 줄이 같은 씬을 동시에 끝내도 서로 지우지 않게 잠그고, 임시 파일로 쓴 뒤 바꿔 넣는다."""
    try:
        with SCENE_INFO_LOCK:
            _write_scene_info(d, take, files)
    except OSError as ex:   # OneDrive가 잠깐 잡고 있는 등: 기록만 남기고 작업은 계속
        log(f"씬정보.json 쓰기 실패(영상은 저장됨): {ex}")


def _write_scene_info(d, take, files):
    path = os.path.join(d, "씬정보.json")
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    except Exception:
        info = {"cuts": {}}
    c = take.get("cut") or {}
    info["scene"] = c.get("scene_heading")
    info["updated_at"] = datetime.now().isoformat(timespec="seconds")
    info["folders"] = {"영상(무음)": SUB["video"], "대사·현장음": SUB["voice"], "배경음악": SUB["bgm"], "원본": SUB["original"]}
    entry = info["cuts"].setdefault(c.get("code") or str(c.get("cut_no")), {})
    entry.update({"cut_no": c.get("cut_no"), "seconds": c.get("seconds"), "summary": c.get("summary"), "dialogue": c.get("dialogue")})
    tk = entry.setdefault(f"take_{take.get('take_no')}", {})
    tk.update({**files, "verdict": take.get("verdict"), "score": take.get("score")})
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_local_package(take, src_mp4):
    """원본을 씬 폴더에 넣고 영상(무음)·대사·현장음으로 나눠 저장한다."""
    settings = (rpc("agent_context", p_token=TOKEN, p_episode=(take.get("cut") or {}).get("episode_id")) or {}).get("settings") or {}
    d = scene_dir(local_root(settings.get("storage")), take)
    c = take.get("cut") or {}
    name = f"C{int(c.get('cut_no') or 0):02d}_t{take.get('take_no')}"
    orig = os.path.join(d, SUB["original"], name + ".mp4")
    if os.path.abspath(src_mp4) != os.path.abspath(orig):
        shutil.copyfile(src_mp4, orig)
    vid, wav = os.path.join(d, SUB["video"], name + ".mp4"), os.path.join(d, SUB["voice"], name + ".wav")
    got_audio = split_streams(orig, vid, wav)
    files = {"folder": d, "original": orig, "video": vid, "voice": wav if got_audio else None}
    write_scene_info(d, take, {k: os.path.basename(v) for k, v in files.items() if v and k != "folder"})
    rpc("agent_save_package", p_token=TOKEN, p_episode=c.get("episode_id"), p_scene=c.get("scene_no"), p={"folder": {PLATFORM: d}})
    return files


# ---------------- Higgsfield (Seedance)
HF_CRED = {"at": 0.0, "cred": None}


def hf_auth_headers():
    if time.time() - HF_CRED["at"] > 300 or not HF_CRED["cred"]:   # 5분마다만 키를 다시 읽는다(상태 확인마다 DB를 부르던 것)
        HF_CRED.update(cred=higgsfield_cred(secrets("higgsfield_key_id", "higgsfield_key_secret")), at=time.time())
    cred = HF_CRED["cred"]
    if not cred:
        raise RuntimeError("Higgsfield 키가 없습니다. 대시보드 설정 → API 키에서 넣어 주세요")
    scheme = "key"
    try:
        with open(os.path.join(HOME, "hf_auth.json"), encoding="utf-8") as f:
            scheme = json.load(f).get("scheme", "key")
    except Exception:
        pass
    if scheme == "legacy":
        return {"hf-api-key": cred[0], "hf-secret": cred[1]}
    return {"Authorization": f"Key {cred[0]}:{cred[1]}"}


def find_video_url(d):
    """완료 응답에서 영상 주소를 찾는다(구조가 조금 달라도)."""
    if isinstance(d, dict):
        v = d.get("video")
        if isinstance(v, dict) and isinstance(v.get("url"), str):
            return v["url"]
        if isinstance(v, str) and v.startswith("http"):
            return v
        for k in ("videos", "outputs", "output", "result", "results", "data"):
            if k in d:
                u = find_video_url(d[k])
                if u:
                    return u
        for val in d.values():
            u = find_video_url(val)
            if u:
                return u
    elif isinstance(d, list):
        for x in d:
            u = find_video_url(x)
            if u:
                return u
    elif isinstance(d, str) and d.startswith("http") and re.search(r"\.(mp4|mov)(\?|$)", d):
        return d
    return None


# ---------------- 컷 설계 ②③: 대사 미리 듣기(ElevenLabs) · 장면 사진(첫·끝) · 감수
def audio_curve(path, step=0.25):
    """소리를 초 단위 숫자로: 구간마다 크기(dBFS)와 높낮이(Hz). Claude는 소리를 못 들으니 이것으로 판단한다."""
    import array
    import math
    r = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                       capture_output=True, timeout=300)
    pcm = array.array("h", r.stdout[: len(r.stdout) // 2 * 2])
    sr, n = 8000, int(8000 * step)
    out = []
    for i in range(0, max(0, len(pcm) - n), n):
        seg = pcm[i:i + n]
        rms = math.sqrt(sum(x * x for x in seg) / n) or 1e-9
        db = round(20 * math.log10(rms / 32768), 1)
        hz = None
        if db > -40:   # 소리가 있을 때만 높낮이(자기상관)
            w = seg[:320]
            best, lag_best = 0.0, 0
            e0 = sum(x * x for x in w) or 1
            for lag in range(20, 160):      # 50~400Hz
                c = sum(w[j] * w[j + lag] for j in range(0, len(w) - lag))
                if c > best:
                    best, lag_best = c, lag
            if lag_best and best / e0 > 0.3:
                hz = round(sr / lag_best)
        out.append({"t": round(i / sr, 2), "db": db, "hz": hz})
    return out


def curve_summary(curve):
    """감수용 요약: 1초 단위 평균 크기·높낮이와 전체 범위."""
    if not curve:
        return {}
    secs = {}
    for p in curve:
        secs.setdefault(int(p["t"]), []).append(p)
    rows = []
    for s, ps in sorted(secs.items()):
        dbs = [p["db"] for p in ps]
        hzs = [p["hz"] for p in ps if p["hz"]]
        rows.append({"s": s, "db": round(sum(dbs) / len(dbs), 1), "hz": round(sum(hzs) / len(hzs)) if hzs else None})
    voiced = [p for p in curve if p["db"] > -40]
    hz_all = [p["hz"] for p in voiced if p["hz"]]
    return {"per_second": rows, "loud_max": max(p["db"] for p in curve), "voiced_ratio": round(len(voiced) / len(curve), 2),
            "pitch_median": sorted(hz_all)[len(hz_all) // 2] if hz_all else None, "pitch_range": [min(hz_all), max(hz_all)] if hz_all else None}


def parse_dialogue(dialogue):
    out = []
    for line in str(dialogue or "").splitlines():
        m = re.match(r"\s*([^:：]{1,15})[:：]\s*(.+)", line)
        if not m:
            continue
        spk, rest = m.group(1).strip(), m.group(2).strip()
        direction = " ".join(re.findall(r"\(([^)]*)\)", rest))
        text = re.sub(r"\([^)]*\)", "", rest).strip()
        if text:
            out.append({"speaker": spk, "text": text, "direction": direction, "tags": ""})
    return out


def voice_for(ctx, speaker):
    """대사의 말한 사람 → 바이블 목소리. 이름이 정확히 같은 인물을 먼저 찾고, 없을 때만 앞부분이 같은 이름을 쓴다
    ('민지' 대사에 '민' 목소리가, 이름 없는 대사에 첫 인물 목소리가 붙던 문제)."""
    speaker = (speaker or "").strip()
    if not speaker:
        return None, None
    chars = [b for b in ctx.get("bible") or [] if b.get("category") in ("character", "voice")]
    base_of = lambda b: re.sub(r"\s*\(.*?\)\s*", "", b.get("name") or "").strip()
    exact = [b for b in chars if speaker in (b.get("name"), base_of(b))]
    loose = [b for b in chars if base_of(b) and (speaker.startswith(base_of(b)) or base_of(b).startswith(speaker))]
    for b in exact or (loose if len(loose) == 1 else []):
        if b.get("voice_id"):
            return b["voice_id"], b.get("name")
        twin = bible_entry(ctx, b.get("same_as")) if b.get("same_as") else None   # 동일인의 목소리를 물려받음
        if twin and twin.get("voice_id"):
            return twin["voice_id"], b.get("name")
    return None, None


_EL_VOICES = {"list": None, "at": 0}


def recommend_voice(ctx, speaker):
    """바이블에 목소리가 아직 없는 인물 → ElevenLabs 기본 목소리 중 성별·나이가 맞는 것을 추천해 임시로 쓴다.
    같은 인물은 언제나 같은 목소리(이름으로 고정 선택) → 컷마다 목소리가 바뀌지 않는다. 바이블 확정은 대표가 한다."""
    if time.time() - _EL_VOICES["at"] > 3600 or not _EL_VOICES["list"]:
        data = el_json("/v1/voices", method="GET", timeout=30) or {}
        _EL_VOICES["list"] = [v for v in (data.get("voices") or []) if v.get("voice_id") and v.get("category") in ("premade", "professional", "generated", "cloned", None)]
        _EL_VOICES["at"] = time.time()
    voices = sorted(_EL_VOICES["list"] or [], key=lambda v: v["voice_id"])
    if not voices:
        return None, None
    b = bible_entry(ctx, speaker) or {}
    txt = " ".join(str(b.get(k) or "") for k in ("name", "description", "details", "prompt_en")) + " " + speaker
    fem = len(re.findall(r"여자|여성|소녀|그녀|언니|누나|엄마|어머니|할머니|아가씨|female|woman|girl|\bshe\b|\bher\b", txt, re.I))
    mal = len(re.findall(r"남자|남성|소년|그는|오빠|형|아빠|아버지|할아버지|아저씨|\bmale\b|\bman\b|\bboy\b|\bhe\b|\bhis\b", txt, re.I))
    gender = "female" if fem > mal else "male" if mal > fem else None
    age = "young"   # 대표 기본값: 모든 인물은 젊은 목소리
    lab = lambda v: {k: str(v2).lower().replace(" ", "_") for k, v2 in (v.get("labels") or {}).items()}
    pool = [v for v in voices if not gender or lab(v).get("gender") == gender] or voices
    pool = [v for v in pool if not age or lab(v).get("age") == age] or pool
    import hashlib
    v = pool[int(hashlib.md5((b.get("name") or speaker).encode()).hexdigest(), 16) % len(pool)]
    return v["voice_id"], v.get("name")


def tts_line(voice_id, text, tags, model):
    key = el_key()
    body = {"text": (tags + " " if tags else "") + text, "model_id": model or "eleven_v3"}
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"목소리 만들기 실패 {e.code}: {e.read()[:200]!r}") from None


def el_music(prompt, seconds, model="music_v1"):
    """ElevenLabs Music: 가사 없는 배경음악(mp3)을 만든다. 길이 3~600초."""
    body = {"prompt": prompt[:2000], "music_length_ms": int(max(3, min(600, seconds)) * 1000), "model_id": model or "music_v1",
            "force_instrumental": True}
    req = urllib.request.Request("https://api.elevenlabs.io/v1/music?output_format=mp3_44100_128", data=json.dumps(body).encode(), method="POST",
                                 headers={"xi-api-key": el_key(), "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"배경음악 만들기 실패 {e.code}: {e.read()[:300]!r}") from None


def el_sts(path, voice_id, model="eleven_multilingual_sts_v2"):
    """ElevenLabs 목소리 바꾸기(speech-to-speech): 타이밍·감정은 그대로 두고 음색만 바이블 인물 목소리로."""
    boundary = "----nyx" + uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="model_id"\r\n\r\n{model}\r\n').encode() + \
        (f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}?output_format=mp3_44100_128", data=body, method="POST",
                                 headers={"xi-api-key": el_key(), "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"목소리 바꾸기 실패 {e.code}: {e.read()[:200]!r}") from None


def diarized_swap(voice_wav, lines, speakers, secs, work, out_wav, cut, ep):
    """여러 명이 말하는 컷: 받아쓰기로 화자별 말한 구간을 찾고, 구간마다 그 인물 목소리로 바꿔 같은 시간에 다시 놓는다.
    말하지 않는 구간의 현장음은 원래 소리 그대로. 화자 연결은 대본의 첫 등장 순서 = 받아쓰기의 첫 등장 순서."""
    try:
        progress(f"{cut['code']} 여러 명 대사: 화자별 구간을 찾는 중")
        segs = stt_speakers(voice_wav)
    except Exception as ex:
        log(f"화자 구분 실패(이어 붙이기로): {ex}")
        return 0
    order_script = list(dict.fromkeys(ln.get("speaker") for ln in lines if ln.get("speaker")))
    order_heard = list(dict.fromkeys(s["speaker"] for s in segs))
    if not segs or len(order_heard) > len(order_script) + 1:
        return 0
    m = {h: order_script[i] for i, h in enumerate(order_heard) if i < len(order_script)}
    pieces = []
    for k, sg in enumerate(segs):
        who = m.get(sg["speaker"])
        vid = speakers.get(who)
        if not vid:
            continue
        a, b = max(0.0, sg["s"] - 0.05), min(secs, sg["e"] + 0.12)
        src = os.path.join(work, f"seg{k}.wav")
        ff("-i", voice_wav, "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-ac", "1", "-ar", "44100", src)
        conv = os.path.join(work, f"seg{k}.mp3")
        with open(conv, "wb") as f:
            f.write(el_sts(src, vid))
        pieces.append((conv, a, b))
    if not pieces:
        return 0
    mute = ",".join(f"volume=enable='between(t,{a:.2f},{b:.2f})':volume=0" for _, a, b in pieces)
    inputs, filt = ["-i", voice_wav], [f"[0:a]{mute},aformat=channel_layouts=stereo[bg]"]
    for k, (p, a, b) in enumerate(pieces, 1):
        inputs += ["-i", p]
        filt.append(f"[{k}:a]atrim=0:{b - a + 0.05:.2f},adelay={int(a * 1000)}|{int(a * 1000)},aformat=channel_layouts=stereo[v{k}]")
    mix = "[bg]" + "".join(f"[v{k}]" for k in range(1, len(pieces) + 1)) + f"amix=inputs={len(pieces) + 1}:normalize=0,apad,atrim=0:{secs}[out]"
    ff(*inputs, "-filter_complex", ";".join(filt) + ";" + mix, "-map", "[out]", "-ar", "48000", "-c:a", "pcm_s16le", out_wav)
    log_cost("audio", "elevenlabs", "speech-to-speech", {"seconds": round(sum(b - a for _, a, b in pieces), 1)}, episode_id=ep,
             scene_no=cut.get("scene_no"), cut_id=cut.get("code"), outcome="success", note="화자별 목소리 바꾸기")
    return len(pieces)


def apply_dialogue_tool(ctx, gen, cut, files, tmp, ep):
    """설정 '대사 도구'가 ElevenLabs면 영상의 대사를 바이블 인물 목소리로 바꿔 입힌다.
    - 영상에 소리가 있고 화자가 한 명: 목소리 바꾸기(타이밍·감정 유지, 음색만 교체)
    - 영상에 소리가 없거나(예: Kling) 화자가 여럿: 대사 미리 듣기(TTS)를 순서대로 이어 붙인다(입 모양과 어긋날 수 있음)
    원래 영상 소리는 씬 폴더에 '_원음.wav'로 남기고, 대사 줄과 미리보기·감수용 영상은 바꾼 소리로 만든다."""
    if (gen.get("dialogue_tool") or "higgsfield") != "elevenlabs":
        return tmp
    lines = [ln for ln in (cut.get("voice_lines") or parse_dialogue(cut.get("dialogue"))) if (ln.get("text") or "").strip()]
    if not lines:
        return tmp
    speakers = {}
    for ln in lines:
        vid, _ = voice_for(ctx, ln.get("speaker") or "")
        if vid:
            speakers[ln.get("speaker")] = vid
    if not speakers:
        log(f"{cut['code']} 바이블에 목소리가 정해진 인물이 없어 영상 소리 그대로 둡니다")
        return tmp
    secs = float(cut.get("seconds") or 6)
    work = os.path.join(HOME, "work", f"dlg_{uuid.uuid4().hex[:6]}")
    os.makedirs(work, exist_ok=True)
    out_wav = os.path.join(work, "voice.wav")
    try:
        if files.get("voice") and len(speakers) == 1 and len({ln.get("speaker") for ln in lines}) == 1:
            progress(f"{cut['code']} 대사를 바이블 목소리로 바꾸는 중(ElevenLabs 목소리 바꾸기)")
            mp3 = os.path.join(work, "sts.mp3")
            with open(mp3, "wb") as f:
                f.write(el_sts(files["voice"], list(speakers.values())[0]))
            ff("-i", mp3, "-ac", "2", "-ar", "48000", "-af", f"apad,atrim=0:{secs}", "-c:a", "pcm_s16le", out_wav)
            how = "목소리 바꾸기"
            log_cost("audio", "elevenlabs", "speech-to-speech", {"seconds": secs}, episode_id=ep, scene_no=cut.get("scene_no"), cut_id=cut.get("code"),
                     outcome="success", note="대사 목소리 바꾸기")
        elif files.get("voice") and (segs := diarized_swap(files["voice"], lines, speakers, secs, work, out_wav, cut, ep)):
            how = f"화자별 목소리 바꾸기 {segs}구간"
        else:
            progress(f"{cut['code']} 대사를 바이블 목소리로 읽혀 이어 붙이는 중(ElevenLabs)")
            parts, t0 = [], 0.3
            for i, ln in enumerate(lines):
                vid = speakers.get(ln.get("speaker"))
                if not vid:
                    continue
                p = os.path.join(work, f"l{i}.mp3")
                got = False
                if ln.get("path"):
                    try:
                        fetch_to(storage_signed_url("takes", ln["path"], 3600), p)
                        got = True
                    except Exception:
                        got = False
                if not got:
                    with open(p, "wb") as f:
                        f.write(tts_line(vid, ln["text"], ln.get("tags") or "", gen.get("tts_model") or "eleven_v3"))
                parts.append((p, t0))
                t0 += max(0.6, len(audio_curve(p)) * 0.25) + 0.25
            if not parts:
                return tmp
            inputs, filt = [], []
            for k, (p, st) in enumerate(parts):
                inputs += ["-i", p]
                filt.append(f"[{k}:a]adelay={int(st * 1000)}|{int(st * 1000)},aformat=channel_layouts=stereo[a{k}]")
            mix = "".join(f"[a{k}]" for k in range(len(parts))) + f"amix=inputs={len(parts)}:normalize=0,apad,atrim=0:{secs}[out]"
            ff(*inputs, "-filter_complex", ";".join(filt) + ";" + mix, "-map", "[out]", "-ar", "48000", "-c:a", "pcm_s16le", out_wav)
            how = "대사 읽기 이어 붙이기"
        if files.get("voice"):
            keep = files["voice"].replace(".wav", "_원음.wav")
            shutil.copyfile(files["voice"], keep)
            shutil.copyfile(out_wav, files["voice"])
        else:
            dst = os.path.join(files["folder"], SUB["voice"], os.path.basename(files["video"]).replace(".mp4", ".wav"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(out_wav, dst)
            files["voice"] = dst
        mixed = tmp.replace(".mp4", "_el.mp4")
        ff("-i", files["video"], "-i", out_wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", mixed)
        files["dialogue_tool"] = f"ElevenLabs({how})"
        log(f"{cut['code']} 대사 ElevenLabs로 교체: {how}")
        return mixed
    except Exception as ex:
        log(f"{cut['code']} 대사 바꾸기 실패(영상 소리 그대로): {ex}")
        return tmp
    finally:
        shutil.rmtree(work, ignore_errors=True)


def stt_speakers(path, model="scribe_v1"):
    """받아쓰기 + 화자 구분: [{speaker, s, e, text}] 말한 구간 목록(같은 화자 연속 단어는 한 구간)."""
    boundary = "----nyx" + uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    def part(name, value):
        return f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body = part("model_id", model) + part("language_code", "kor") + part("diarize", "true") + part("timestamps_granularity", "word") + \
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.elevenlabs.io/v1/speech-to-text", data=body, method="POST",
                                 headers={"xi-api-key": el_key(), "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"화자 구분 받아쓰기 실패 {e.code}: {e.read()[:160]!r}") from None
    segs = []
    for w in d.get("words") or []:
        if w.get("type", "word") != "word":
            continue
        spk, s, e = w.get("speaker_id") or "speaker_0", float(w.get("start") or 0), float(w.get("end") or 0)
        if segs and segs[-1]["speaker"] == spk and s - segs[-1]["e"] < 0.8:
            segs[-1]["e"] = e
            segs[-1]["text"] += " " + (w.get("text") or "")
        else:
            segs.append({"speaker": spk, "s": s, "e": e, "text": w.get("text") or ""})
    return segs


def stt_words(path, model="scribe_v1"):
    """ElevenLabs 받아쓰기: 단어와 시간. 영상 소리 감수에 쓴다."""
    key = el_key()
    boundary = "----nyx" + uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    def part(name, value):
        return f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    last = None
    for mdl in (model, "scribe_v2", "scribe_v1"):
        body = part("model_id", mdl) + part("language_code", "kor") + part("timestamps_granularity", "word") + \
            (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request("https://api.elevenlabs.io/v1/speech-to-text", data=body, method="POST",
                                     headers={"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read())
            words = [{"w": w.get("text"), "s": round(w.get("start") or 0, 2), "e": round(w.get("end") or 0, 2)}
                     for w in d.get("words") or [] if w.get("type", "word") == "word"]
            return d.get("text") or "", words
        except urllib.error.HTTPError as e:
            last = f"{e.code}: {e.read()[:160]!r}"
            if e.code not in (400, 404, 422):
                break
    raise RuntimeError(f"받아쓰기 실패 {last}")


GROK_IMAGE = "xai/grok-imagine-image-2.0"
# 영상 모델(Higgsfield 안에서 고름). 경로·입력값은 Higgsfield 공개 문서 기준(2026-11)
VIDEO_MODELS = {
    "seedance-2.5": {"key": "seedance-2.5", "name": "Seedance 2.5", "base": "bytedance/seedance-2.5", "audio": True,
                     "res": ["480p", "720p", "1080p"], "routes": {"i2v": "image-to-video", "ref": "reference-to-video", "t2v": "text-to-video"}},
    "kling-4.0": {"key": "kling-4.0", "name": "Kling 4.0", "base": "kling-video/v4.0", "audio": False,
                  "res": ["720p", "1080p"], "routes": {"i2v": "image-to-video", "t2v": "text-to-video"}},
}
SOUL2 = "higgsfield-ai/soul/v2/standard"
# 사진 모델 (Higgsfield 공개 API 기준, 2026-11 내 키로 확인)
# - Seedream 5.0 Pro: 기본. 참고 사진이 있으면 /edit(image_urls 1~10장), 없으면 /text-to-image
# - Nano Banana Pro: 주소(nano-banana-pro, nano-banana-pro/edit)는 있으나 2026-11 현재 "model_disabled"(꺼져 있음).
#   'auto'·'nano'는 켜져 있으면 나노바나나 프로, 꺼져 있으면 Seedream 5.0 Pro로 만든다(예전처럼 몰래 Grok으로 바꾸지 않음).
SEEDREAM5 = "bytedance/seedream/v5.0/pro"
KLING_IMG4 = "kling-image/v4.0"
NANO_PRO = "nano-banana-pro"
IMAGE_ROUTES = {SEEDREAM5: ("/edit", "/text-to-image"), KLING_IMG4: ("/edit", "/text-to-image"), NANO_PRO: ("/edit", "")}
IMAGE_EXTRA = {SEEDREAM5: {"resolution": "2k"}, KLING_IMG4: {"resolution": "2k"}}
IMAGE_ALIAS = {"seedream": SEEDREAM5, "kling": KLING_IMG4, "grok": GROK_IMAGE}
MODEL_LABEL = {SEEDREAM5: "Seedream 5.0 Pro", KLING_IMG4: "Kling Image 4.0", NANO_PRO: "나노바나나 프로", GROK_IMAGE: "Grok Imagine 2.0", SOUL2: "Soul 2"}


def model_label(m):
    base = image_base(m)
    return MODEL_LABEL.get(base, m)


def image_base(m):
    m = (m or "").strip().strip("/")
    for base in IMAGE_ROUTES:
        if m == base or m.startswith(base + "/"):
            return base
    return m


def image_route(m, has_refs):
    """모델 이름 → 실제 요청 주소(참고 사진이 있으면 편집·참고용 주소)."""
    m = (m or "").strip().strip("/")
    if m in IMAGE_ROUTES:
        return m + IMAGE_ROUTES[m][0 if has_refs else 1]
    return m


def hf_probe(path):
    code, data = http_json(f"https://api.higgsfield.ai/{path}", {**hf_auth_headers(), "Content-Type": "application/json",
                           "Accept": "application/json"}, "POST", {}, timeout=30)
    return code, data


NANO_STATE = {"ok": None, "why": "", "at": 0.0}


def nano_available():
    """나노바나나 프로가 지금 켜져 있나(빈 요청으로 확인, 생성·비용 없음). 켜짐은 6시간, 꺼짐은 30분 기억."""
    if NANO_STATE["ok"] is not None and time.time() - NANO_STATE["at"] < (21600 if NANO_STATE["ok"] else 1800):
        return NANO_STATE["ok"]
    try:
        code, data = hf_probe(NANO_PRO + "/edit")
    except Exception as ex:
        code, data = 0, {"detail": str(ex)[:120]}
    ok = code in (400, 422)
    why = "" if ok else ((data or {}).get("detail") if isinstance(data, dict) else "") or f"응답 {code}"
    NANO_STATE.update(ok=ok, why=str(why)[:120], at=time.time())
    log(f"나노바나나 프로 확인: {code} {'' if ok else why}")
    return ok


def resolve_image_model(gen):
    """사진 모델 이름. 설정에 경로가 있으면 그대로, 'seedream'·'kling'·'grok'은 그 모델,
    'auto'·'nano'·비어 있으면 나노바나나 프로(켜져 있을 때) → 아니면 Seedream 5.0 Pro."""
    want = (gen.get("image_model") or "auto").strip().strip("/")
    if want in IMAGE_ALIAS:
        return IMAGE_ALIAS[want]
    if want not in ("auto", "nano", "") and want != NANO_PRO:
        return want
    if nano_available():
        return NANO_PRO
    log(f"나노바나나 프로를 지금 Higgsfield API에서 쓸 수 없어({NANO_STATE['why']}) Seedream 5.0 Pro로 만듭니다")
    return SEEDREAM5


def char_photo_model(cm):
    """인물 사진(후보·기준 사진) 설정값 → 사진 모델 이름. soul2는 따로 처리."""
    return resolve_image_model({"image_model": cm or "seedream"})


def find_image_urls(d, out=None):
    out = [] if out is None else out
    if isinstance(d, dict):
        if isinstance(d.get("url"), str) and d["url"].startswith("http"):
            out.append(d["url"])
        for v in d.values():
            find_image_urls(v, out)
    elif isinstance(d, list):
        for x in d:
            find_image_urls(x, out)
    elif isinstance(d, str) and d.startswith("http") and re.search(r"\.(png|jpe?g|webp)(\?|$)", d, re.I):
        out.append(d)
    return list(dict.fromkeys(out))


def hf_submit_wait(path, body, what="사진", timeout=900):
    """Higgsfield에 요청을 넣고 끝날 때까지 기다려 완료 응답을 돌려준다."""
    hdr = {**hf_auth_headers(), "Content-Type": "application/json", "Accept": "application/json"}
    body = {**IMAGE_EXTRA.get(image_base(path), {}), **body}
    path = image_route(path, bool(body.get("image_urls")))
    code, data = http_json(f"https://api.higgsfield.ai/{path.strip('/')}", hdr, "POST", body, timeout=120)
    if code not in (200, 201, 202) or not isinstance(data, dict):
        raise RuntimeError(f"{what} 요청 실패 {code} ({model_label(path)}): {str(data)[:300]}")
    cid = hf_cost_start(image_base(path), {"images": int(body.get("batch_size") or 1)}, f"{what} ({model_label(path)})")
    if find_image_urls(data.get("images") or []) or data.get("status") == "completed":
        hf_cost_end(cid, True)
        return data
    rid = data.get("request_id") or data.get("id")
    status_url = data.get("status_url") or f"https://api.higgsfield.ai/requests/{rid}/status"
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(4)
        try:
            _, st = http_json(status_url, {**hf_auth_headers(), "Accept": "application/json"}, timeout=60)
        except Exception:
            continue
        s = (st or {}).get("status") if isinstance(st, dict) else None
        if s in ("failed", "nsfw", "canceled"):
            hf_cost_end(cid, False, f"{what} 생성 {s}")
            raise RuntimeError(f"{what} 생성 {s}")
        if s == "completed":
            hf_cost_end(cid, True)
            return st
        el = int(time.time() - (deadline - timeout))
        progress(f"Higgsfield {what}: {'차례 기다리는 중(Higgsfield 대기열)' if s in ('queued', 'pending', None) else '만드는 중'} · {el // 60}분 {el % 60}초 지남")
    hf_cost_end(cid, False, f"{what} 생성 시간 초과")
    raise RuntimeError(f"{what} 생성 시간 초과")


def soul_images(prompt, n=4, aspect="3:4", soul_id=None, strength=0.9):
    """Soul 2: 실사 인물 사진. soul_id가 있으면 그 얼굴로."""
    body = {"prompt": prompt[:2000], "batch_size": 4 if n > 1 else 1, "resolution": "1080p", "aspect_ratio": aspect, "enhance_prompt": False}
    if soul_id:
        body.update({"custom_reference_id": soul_id, "custom_reference_strength": strength})
    st = hf_submit_wait(SOUL2, body, "인물 사진")
    urls = find_image_urls(st.get("images") or st)
    if not urls:
        raise RuntimeError(f"인물 사진 주소를 찾지 못함: {str(st)[:200]}")
    return urls[:n]


def soul_train(name, image_urls, timeout=3600):
    """Soul ID(이 인물 전용 얼굴 모델)를 학습시키고 id를 돌려준다."""
    hdr = {**hf_auth_headers(), "Content-Type": "application/json", "Accept": "application/json"}
    code, data = http_json("https://api.higgsfield.ai/v1/custom-references", hdr, "POST", {
        "name": name[:80], "model_version": "v2",
        "input_images": [{"type": "image_url", "image_url": u} for u in image_urls[:100]]}, timeout=120)
    if code not in (200, 201, 202) or not isinstance(data, dict) or not data.get("id"):
        raise RuntimeError(f"얼굴 학습 요청 실패 {code}: {str(data)[:300]}")
    sid = data["id"]
    cid = hf_cost_start("soul-id-training", {"calls": 1}, f"얼굴 학습: {name}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(15)
        try:
            _, st = http_json(f"https://api.higgsfield.ai/v1/custom-references/{sid}", {**hf_auth_headers(), "Accept": "application/json"}, timeout=60)
        except Exception:
            continue
        s = (st or {}).get("status")
        if s == "completed":
            hf_cost_end(cid, True)
            return sid
        if s in ("failed", "canceled"):
            hf_cost_end(cid, False, f"얼굴 학습 {s}")
            raise RuntimeError(f"얼굴 학습 실패: {str(st)[:200]}")
        progress(f"얼굴 학습 중… ({s or '대기'})")
    hf_cost_end(cid, False, "얼굴 학습 시간 초과")
    raise RuntimeError("얼굴 학습 시간 초과")


def hf_image(prompt, refs, aspect, model_path):
    """Higgsfield 장면 사진(참고 사진으로 얼굴·의상·장소 고정)."""
    base = image_base(model_path or SEEDREAM5)
    model_path = image_route(base, bool(refs))
    body = {"prompt": prompt, "aspect_ratio": aspect, **IMAGE_EXTRA.get(base, {})}
    if refs:
        body["image_urls"] = refs[:8]
    code, data = http_json(f"https://api.higgsfield.ai/{model_path.strip('/')}",
                           {**hf_auth_headers(), "Content-Type": "application/json", "Accept": "application/json"}, "POST", body, timeout=120)
    if code in (400, 422) and refs:
        log(f"사진 모델이 image_urls를 받지 않음, input_images로 다시: {str(data)[:160]}")
        body.pop("image_urls")
        body["input_images"] = [{"type": "image_url", "image_url": u} for u in refs[:8]]
        code, data = http_json(f"https://api.higgsfield.ai/{model_path.strip('/')}",
                               {**hf_auth_headers(), "Content-Type": "application/json", "Accept": "application/json"}, "POST", body, timeout=120)
    if code not in (200, 201, 202) or not isinstance(data, dict):
        raise RuntimeError(f"사진 요청 실패 {code} ({model_label(model_path)}): {str(data)[:300]}")
    rid = data.get("request_id") or data.get("id")
    cid = hf_cost_start(base, {"images": 1}, f"장면 사진 ({model_label(base)})")
    url = find_image_url(data)
    status_url = data.get("status_url") or f"https://api.higgsfield.ai/requests/{rid}/status"
    deadline = time.time() + 600
    while not url and time.time() < deadline:
        time.sleep(4)
        try:
            _, st = http_json(status_url, {**hf_auth_headers(), "Accept": "application/json"}, timeout=60)
        except Exception:
            continue
        s = (st or {}).get("status") if isinstance(st, dict) else None
        if s in ("failed", "nsfw", "canceled"):
            hf_cost_end(cid, False, f"사진 생성 {s}")
            raise RuntimeError(f"사진 생성 {s}")
        if s == "completed":
            url = find_image_url(st)
            if not url:
                hf_cost_end(cid, False, "사진 주소를 받지 못함")
                raise RuntimeError(f"사진 주소를 찾지 못함: {str(st)[:200]}")
    if not url:
        hf_cost_end(cid, False, "사진 생성 시간 초과")
        raise RuntimeError("사진 생성 시간 초과")
    hf_cost_end(cid, True)
    return url


def find_image_url(d):
    if isinstance(d, dict):
        for k in ("images", "image", "output", "outputs", "result", "results", "data"):
            if k in d:
                u = find_image_url(d[k])
                if u:
                    return u
        if isinstance(d.get("url"), str) and d["url"].startswith("http"):
            return d["url"]
        for v in d.values():
            u = find_image_url(v)
            if u:
                return u
    elif isinstance(d, list):
        for x in d:
            u = find_image_url(x)
            if u:
                return u
    elif isinstance(d, str) and d.startswith("http") and re.search(r"\.(png|jpe?g|webp)(\?|$)", d, re.I):
        return d
    return None


PHOTO_CATS = ("character", "location", "background", "prop")


TEMP_REF_LOCKS, TEMP_REF_DONE = {}, {}
_TEMP_GUARD = threading.Lock()


def ensure_temp_refs(ctx, names, gen, ep=None):
    """컷에 나오는 바이블 항목에 사진이 하나도 없으면, 작가실 묘사로 사진 1장을 만들어 '임시 기준 사진'으로 고정한다.
    그래야 임의로 정해진 모습이라도 모든 컷에서 같게 나온다. 대표가 사진을 올리면 그쪽이 우선."""
    for n in dict.fromkeys(names or []):
        b = bible_entry(ctx, n)
        if not b or b.get("category") not in PHOTO_CATS or b.get("images") or b.get("temp_image") or not b.get("entry_id"):
            continue
        if b.get("essential") is False:   # 필수가 아닌 항목: 미리 만들지 않고, 처음 나온 장면 사진을 참고로 쓴다
            continue
        twin = bible_entry(ctx, b.get("same_as")) if b.get("same_as") else None
        if twin and (twin.get("images") or twin.get("temp_image")):
            continue
        with _TEMP_GUARD:
            lk = TEMP_REF_LOCKS.setdefault(b["entry_id"], threading.Lock())
        with lk:   # 같은 인물 임시 사진을 두 줄이 따로 사서 컷마다 얼굴이 달라지던 문제
            if TEMP_REF_DONE.get(b["entry_id"]):
                b["temp_image"] = TEMP_REF_DONE[b["entry_id"]]
                continue
            _make_temp_ref(b, n, gen, ep)


def _make_temp_ref(b, n, gen, ep):
    """임시 기준 사진 1장 만들기(잠금 안에서 부름)."""
    prompt = (b.get("prompt_en") or b.get("description") or n).strip()
    try:
        progress(f"'{n}' 사진이 비어 있어 작가실 묘사로 임시 기준 사진을 만드는 중")
        if b["category"] == "character":
            url, model = soul_images(prompt + " Neutral expression, plain light grey background. " + PHOTO_LOOK, n=1, aspect="3:4")[0], SOUL2
        else:
            model = resolve_image_model(gen)
            look = {"prop": " Photorealistic product photograph of the object alone, plain neutral background, no people, no text.",
                    "background": " Photorealistic wide background plate photograph, no people, no text."}.get(
                b["category"], " Photorealistic location photograph, natural light, no people, no text.")
            st = hf_submit_wait(model, {"prompt": prompt + look, "aspect_ratio": "9:16"}, "임시 사진")
            url = (find_image_urls(st.get("images") or st) or [None])[0]
            if not url:
                raise RuntimeError("사진 주소가 없습니다")
        path = upload_from_url(url, f"bible_temp/{b['entry_id']}/{uuid.uuid4().hex[:8]}.jpg")
        b["temp_image"] = rpc("agent_bible_set_temp_photo", p_token=TOKEN, p_entry=b["entry_id"], p_path=path, p_model=model) or {"path": path}
        TEMP_REF_DONE[b["entry_id"]] = b["temp_image"]
        log(f"'{n}' 임시 기준 사진 고정")
    except Exception as ex:
        log(f"'{n}' 임시 기준 사진 실패(묘사만으로 진행): {ex}")


def bible_ref_urls(ctx, names, limit=4, per=1):
    return [u for _, u in bible_ref_pairs(ctx, names, limit, per)]


def bible_ref_pairs(ctx, names, limit=4, per=1):
    """(이름, 서명된 주소) 목록. 사진 없는 이름은 건너뛰되 이름표가 밀리지 않게 짝으로 돌려준다. 주소 만들기는 동시에."""
    bl = ctx.get("bible") or []
    imgs = {b["name"]: [("bible", p) for p in (b.get("images") or [])] for b in bl}
    for b in bl:   # 대표 사진이 없으면 쌓인 참고 사진(장면 사진) → 임시 기준 사진(자동) → 동일인의 사진
        if not imgs.get(b["name"]) and b.get("refs"):
            imgs[b["name"]] = [("takes", r["path"]) for r in b["refs"] if r.get("path")]
        if not imgs.get(b["name"]) and (b.get("temp_image") or {}).get("path"):
            imgs[b["name"]] = [("takes", b["temp_image"]["path"])]
    for b in bl:
        if not imgs.get(b["name"]) and b.get("same_as"):
            imgs[b["name"]] = imgs.get(b["same_as"]) or []
    want = []
    for n in names or []:
        if n not in imgs:
            b = bible_entry(ctx, n)
            n = b["name"] if b else n
        want += [(n, bucket, p) for bucket, p in imgs.get(n, [])[:per]]
    want = want[:limit + 2]   # 몇 장 실패해도 limit을 채울 만큼만 서명
    signed = parallel([(lambda b=b, p=p: storage_signed_url(b, p, 7200)) for _, b, p in want], workers=6)
    out = []
    for (n, _, _), u in zip(want, signed):
        if isinstance(u, Exception) or not u:
            log(f"참고 사진 주소 실패({n}): {u}")
        else:
            out.append((n, u))
    return out[:limit]


def bible_ref_blocks(ctx, names, limit=4):
    """감수용: 바이블 기준 사진을 Claude에게 보여 준다(작게)."""
    blocks = []
    for n, url in bible_ref_pairs(ctx, names, limit):
        try:
            tmp = os.path.join(HOME, "work", f"ref_{uuid.uuid4().hex[:6]}.img")
            fetch_to(url, tmp)
            small = tmp + ".jpg"
            ff("-i", tmp, "-vf", "scale=448:-2", "-q:v", "4", small)
            with open(small, "rb") as f:
                blocks += [{"type": "text", "text": f"[바이블 기준 사진: {n}]"},
                           {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}}]
            for p in (tmp, small):
                os.remove(p)
        except Exception as ex:
            log(f"기준 사진 준비 실패({n}): {ex}")
    return blocks


def img_block(path, label):
    small = path + ".s.jpg"
    ff("-i", path, "-vf", "scale=448:-2", "-q:v", "4", small)
    with open(small, "rb") as f:
        b = [{"type": "text", "text": label}, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}}]
    os.remove(small)
    return b


@task("prep_cut")
def t_prep_cut(t):
    """컷 설계 ②③: 승인 전 컷마다 대사 미리 듣기 + 장면 사진(첫·끝) + 영상감수자·감정감수자 사전 감수."""
    p = t.get("payload") or {}
    ep = t["episode_id"]
    cuts = [rpc("agent_get_cut", p_token=TOKEN, p_id=t["cut_id"])] if t.get("cut_id") else \
        rpc("agent_list_cuts", p_token=TOKEN, p_episode=ep, p_scene=t.get("scene_no"))
    retried = (t.get("attempts") or 1) > 1   # 끊긴 뒤 다시 집힌 일: 그때 '준비 중'에 멈춘 컷도 다시 한다
    cuts = [c for c in cuts if c and c.get("status") in ("designed", "failed", "hold")
            and (p.get("force") or c.get("prep_status") in ("none", "failed", "fix") or (retried and c.get("prep_status") == "preparing"))]
    if not cuts:
        return "준비할 컷이 없습니다"
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=cuts[0].get("script_version_id"))
    gen = (ctx.get("settings") or {}).get("generation") or {}
    sid = ((ctx.get("series") or {}).get("id")) or "series"
    enum = int((ctx.get("episode") or {}).get("number") or 0)

    def prep_one(c):
        code = c.get("code") or f"S{c['scene_no']}C{c['cut_no']}"
        rpc("agent_update_cut", p_token=TOKEN, p_id=c["id"], p={"prep_status": "preparing"})
        base = f"{sid}/E{enum:02d}/S{int(c['scene_no']):02d}/prep/C{int(c['cut_no']):02d}_{uuid.uuid4().hex[:6]}"
        problems = []
        # ② 대사 미리 듣기 — 대표 결정(11/25): 목소리는 Seedance가 영상과 함께 만든다 → 미리 듣기(ElevenLabs)는 만들지 않는다
        lines = c.get("voice_lines") or parse_dialogue(c.get("dialogue"))
        if (gen.get("voice_source") or "seedance") == "seedance":
            lines = [{k: ln.get(k) for k in ("speaker", "text", "direction", "tags") if ln.get(k)} for ln in lines]
        for i, ln in (enumerate(lines) if (gen.get("voice_source") or "seedance") != "seedance" else []):
            if (gen.get("dialogue_tool") or "elevenlabs") == "none":   # 설정: 대사 도구 '쓰지 않음'
                ln["error"] = "설정에서 대사 도구를 '쓰지 않음'으로 해 두어 미리 듣기를 만들지 않았습니다"
                continue
            vid, vname = voice_for(ctx, ln.get("speaker") or "")
            ln.pop("voice_note", None)
            if not vid:   # 바이블에 목소리가 아직 없으면 → 추천 목소리로 먼저 들어 볼 수 있게(임시, 바이블 확정은 대표)
                try:
                    vid, vname = recommend_voice(ctx, ln.get("speaker") or "")
                except Exception as ex:
                    vid, vname = None, None
                    log(f"추천 목소리 찾기 실패: {ex}")
                if vid:
                    ln["voice_note"] = f"추천 목소리(임시): {vname} — 바이블 → 목소리에서 이 인물 목소리를 정하면 그 목소리로 바뀝니다"
            if not vid:
                ln["error"] = "바이블에 이 인물의 목소리가 없고, 추천 목소리도 찾지 못했습니다(ElevenLabs 키·연결 확인)"
                problems.append(f"{ln.get('speaker')} 목소리 없음")
                continue
            progress(f"{code} 대사 {i + 1}/{len(lines)} 미리 듣기 만드는 중 ({vname})")
            try:
                audio = tts_line(vid, ln["text"], ln.get("tags") or "", gen.get("tts_model") or "eleven_v3")
                local = os.path.join(HOME, "work", f"vl_{c['id']}_{i}.mp3")
                with open(local, "wb") as f:
                    f.write(audio)
                curve = audio_curve(local)
                ln["analysis"] = curve_summary(curve)
                ln["seconds"] = round(len(curve) * 0.25, 2)
                ln["path"] = storage_upload(f"{base}_v{i}.mp3", local, "audio/mpeg")
                ln.pop("error", None)
                os.remove(local)
                log_cost("audio", "elevenlabs", gen.get("tts_model") or "eleven_v3", {"characters": len(ln["text"])},
                         episode_id=ep, scene_no=c["scene_no"], cut_id=code, outcome="success", note="대사 미리 듣기")
            except Exception as ex:
                ln["error"] = str(ex)[:200]
                problems.append(f"대사 {i + 1} 실패")
        # ③ 장면 사진 (첫 장면, 필요하면 끝 장면)
        frames = {}
        aspect = gen.get("aspect_ratio", "9:16")
        ensure_temp_refs(ctx, c.get("reference_names"), gen, ep)
        refs = bible_ref_urls(ctx, c.get("reference_names"), limit=8, per=3)
        imodel = resolve_image_model(gen)
        for key, want in (("frame_first", True), ("frame_last", bool(c.get("use_end_frame")))):
            fr = dict(c.get(key) or {})
            if not want:
                continue
            fprompt = fr.get("prompt") or (c.get("prompt") or "")[:900] + (" — opening frame, still image." if key == "frame_first" else " — closing frame, still image.")
            progress(f"{code} {'첫' if key == 'frame_first' else '끝'} 장면 사진 만드는 중")
            try:
                fp = (fprompt + " Photorealistic live-action film still shot on a cinema camera, natural skin texture, realistic lighting."
                      " Keep each person's face, hair and outfit exactly as in the reference photos. No text, no subtitles.")
                try:
                    url = hf_image(fp, refs, aspect, imodel)
                except Exception as ex1:
                    if image_base(imodel) == SEEDREAM5:
                        raise
                    log(f"{model_label(imodel)} 실패, Seedream 5.0 Pro로 다시: {ex1}")
                    imodel = SEEDREAM5
                    url = hf_image(fp, refs, aspect, imodel)
                local = os.path.join(HOME, "work", f"kf_{c['id']}_{key}.img")
                fetch_to(url, local)
                jpg = local + ".jpg"
                ff("-i", local, "-q:v", "2", jpg)
                fr.update({"prompt": fprompt, "path": storage_upload(f"{base}_{key}.jpg", jpg, "image/jpeg"), "status": "ready", "error": None})
                frames[key] = jpg
                os.remove(local)
                if key == "frame_first":   # 필수가 아닌 장소·소품·배경: 이 장면 사진을 그 항목의 참고 사진으로 바이블에 쌓는다(최근 6장)
                    for n in dict.fromkeys(c.get("reference_names") or []):
                        b = bible_entry(ctx, n)
                        if b and b.get("essential") is False and b.get("category") in PHOTO_CATS and not b.get("images") and b.get("entry_id"):
                            try:
                                rpc("agent_bible_add_ref", p_token=TOKEN, p_entry=b["entry_id"], p_path=fr["path"], p_cut=code,
                                    p_ep=int((ctx.get("episode") or {}).get("number") or 0) or None)
                                b["refs"] = [{"path": fr["path"], "cut": code}] + [r for r in (b.get("refs") or []) if r.get("path") != fr["path"]][:5]
                                b["temp_image"] = b.get("temp_image") or {"path": fr["path"]}
                                log(f"'{n}' 참고 사진 쌓음: {code} 장면 사진")
                            except Exception as ex:
                                log(f"'{n}' 참고 사진 저장 실패: {ex}")
            except Exception as ex:
                fr.update({"prompt": fprompt, "status": "failed", "error": str(ex)[:300]})
                problems.append(f"{'첫' if key == 'frame_first' else '끝'} 장면 사진 실패")
            c[key] = fr
        # 사전 감수: 영상감수자(사진 ↔ 바이블) + 감정감수자(목소리 ↔ 감정선)
        review = {}
        auto_rv = bool(gen.get("auto_review"))   # 대표 결정(11/25): 감수는 대표가 버튼으로 할 때만. 설정에서 켜면 예전처럼 자동
        if auto_rv and (frames or any(ln.get("analysis") for ln in lines)):
            progress(f"{code} 영상감수자·감정감수자 사전 감수")
            content = bible_ref_blocks(ctx, c.get("reference_names"))
            for key, jpg in frames.items():
                content += img_block(jpg, f"[{'첫' if key == 'frame_first' else '끝'} 장면 사진]")
            content.append({"type": "text", "text": "[컷 설계]\n" + json.dumps({k: c.get(k) for k in (
                "code", "seconds", "summary", "dialogue", "emotion", "expression_beats", "camera")}, ensure_ascii=False)
                + "\n\n[대사 미리 듣기 분석: 1초마다 크기(dBFS, 0에 가까울수록 큼)·높낮이(Hz)]\n"
                + json.dumps([{k: ln.get(k) for k in ("speaker", "text", "direction", "tags", "seconds", "analysis", "error")} for ln in lines], ensure_ascii=False)})
            system = (guide(ctx, "영상감수자") or "") + "\n\n" + (guide(ctx, "감정감수자") or "") + f"""

[시리즈 바이블]
{bible_text(ctx)}

[지금 할 일: 영상을 만들기 전 사전 감수]
- 영상감수자: 장면 사진의 얼굴이 바이블 기준 사진과 같은 사람인가, 의상·소품·배경이 바이블·설계와 같은가, 첫 표정·자세가 감정 시작점에 맞는가, 화면 글자·왜곡이 없는가.
- 감정감수자: 대사 미리 듣기의 크기·높낮이 흐름이 지시(direction)와 감정선대로 움직이는가(예: '점점 커짐'이면 뒤로 갈수록 dB가 올라야 함), 같은 인물 목소리 높이가 바이블·다른 대사와 비슷한가, 대사 길이가 컷 안에 들어가는가.
- 고칠 점은 감독이 프롬프트·사진 지시·목소리 태그에 바로 옮길 수 있는 문장으로.
[답 형식]
<json>{{"video": {{"verdict": "pass|fix", "issues": [""], "fix_prompt": "사진 프롬프트를 이렇게"}},
 "emotion": {{"verdict": "pass|fix", "issues": [""], "fix_tags": [{{"line": 0, "tags": "[whispers] 같은 영어 감정 태그", "direction": "한국어 지시"}}]}},
 "summary": "대표용 한두 줄"}}</json>"""
            try:
                text, _ = claude(gen.get("claude_reviewer") or "claude-sonnet-5-5", system, content, max_tokens=2500, stage="keyframe_review",
                                 episode_id=ep, scene_no=c["scene_no"], cut_code=code, note="사진·목소리 사전 감수")
                review = pick_json(text)
            except Exception as ex:
                review = {"summary": f"사전 감수 실패: {ex}"}
        # 한 번은 스스로 고친다: 감정 태그 → 목소리 다시, 사진 프롬프트 → 사진 다시 (그래도 안 되면 대표 확인)
        fixed = []
        if (review.get("emotion") or {}).get("verdict") == "fix" and not p.get("second_pass"):
            for ft in review["emotion"].get("fix_tags") or []:
                i = inum(ft.get("line") or 0)
                if 0 <= i < len(lines):
                    lines[i]["tags"] = ft.get("tags") or lines[i].get("tags")
                    lines[i]["direction"] = ft.get("direction") or lines[i].get("direction")
                    fixed.append("목소리")
        if (review.get("video") or {}).get("verdict") == "fix" and not p.get("second_pass") and review["video"].get("fix_prompt"):
            for key in frames:
                c[key]["prompt"] = review["video"]["fix_prompt"]
            fixed.append("사진")
        status = "ready"
        if fixed:
            status = "fix"
        elif problems or any((review.get(k) or {}).get("verdict") == "fix" for k in ("video", "emotion")):
            status = "fix" if p.get("second_pass") else "fix"
        rpc("agent_update_cut", p_token=TOKEN, p_id=c["id"], p={
            "voice_lines": lines, "frame_first": c.get("frame_first") or {}, "frame_last": c.get("frame_last") or {},
            "prep_review": {**review, "problems": problems, "auto_fixed": fixed}, "prep_status": status})
        for jpg in frames.values():
            try:
                os.remove(jpg)
            except OSError:
                pass
        if fixed and not p.get("second_pass"):
            rpc("agent_add_task", p_token=TOKEN, p={"kind": "prep_cut", "episode_id": ep, "scene_no": c["scene_no"], "cut_id": c["id"],
                                                   "payload": {"force": True, "second_pass": True, "auto_retry": bool(p.get("auto_retry"))}})
        elif p.get("auto_retry"):
            # 자동 재시도: 대표가 설정에서 미리 승인한 횟수 안이면 바로 다시 생성(한도는 DB가 확인)
            try:
                rpc("agent_update_cut", p_token=TOKEN, p_id=c["id"], p={"status": "approved"})
                rpc("agent_add_task", p_token=TOKEN, p={"kind": "generate", "episode_id": ep, "scene_no": c["scene_no"], "cut_id": c["id"],
                                                       "payload": {"auto_retry": True}})
                log(f"{code} 자동 재시도: 다시 생성합니다")
            except Exception as ex:
                rpc("agent_update_cut", p_token=TOKEN, p_id=c["id"], p={"status": "designed"})
                log(f"{code} 자동 재시도 못 함(대표 승인 필요): {ex}")
        return f"{code}:{'다시 준비' if fixed else status}"

    def safe_one(c):
        try:
            return prep_one(c)
        except Exception as ex:   # 컷 하나가 실패해도 '준비 중'에 멈춰 두지 않고 원인과 함께 '실패'로
            log(f"컷 준비 실패 {c.get('code')}: {ex}\n{traceback.format_exc()}")
            try:
                rpc("agent_update_cut", p_token=TOKEN, p_id=c["id"], p={"prep_status": "failed", "prep_review": {"summary": f"준비 실패: {str(ex)[:300]}", "problems": [str(ex)[:200]]}})
            except Exception:
                pass
            if is_transient(str(ex)) and len(cuts) == 1:
                raise   # 컷 하나짜리 일은 일시적 문제면 대기열이 알아서 다시
            return f"{c.get('code')}:failed"

    # 컷마다 대사·사진·감수를 3개씩 동시에(예전엔 씬 전체를 한 컷씩 차례로 해서 45분을 넘기기도 함)
    done = parallel([(lambda c=c: safe_one(c)) for c in cuts], workers=3)
    err = next((d for d in done if isinstance(d, Exception)), None)
    if err:
        raise err
    ready = sum(1 for d in done if d.endswith(":ready"))
    if not t.get("cut_id") or p.get("second_pass"):
        notify(f"{enum}화 컷 준비 {ready}/{len(done)}", "제작 탭에서 장면 사진을 보고 대사를 들어 본 뒤 생성 승인을 눌러 주세요")
    return "컷 준비: " + ", ".join(done)


def inflight_take(cut, max_hours=3):
    """이 컷에서 Higgsfield에 이미 맡겼는데 결과를 아직 받지 못한 테이크(재시작·끊김으로 놓친 것)."""
    if cut.get("status") != "generating":
        return None
    for k in reversed(cut.get("takes") or []):
        if not k.get("request_id"):
            continue
        c = ts_of(k.get("created_at"))
        age = time.time() - c if c else 0
        if age > max_hours * 3600:
            return None
        if k.get("status") in ("submitted", "in_progress"):
            return k
        if k.get("status") == "completed" and k.get("remote_url") and not k.get("files") and not k.get("verdict"):
            return k
        return None   # 가장 최근 테이크가 이미 끝났으면(실패·보관 등) 새로 생성
    return None


@task("generate")
def t_generate(t):
    """영상 생성. 끝까지 못 가면 컷을 '생성 중'에 묶어 두지 않는다: 일시적 문제는 다시(이미 맡긴 요청을 이어서), 그 밖엔 실패로."""
    try:
        return _generate(t)
    except Retry:
        raise
    except Exception as e:
        if is_transient(str(e)) and (t.get("attempts") or 1) < 4:
            raise   # run_task가 잠시 뒤 다시 맡김 → inflight_take로 이어서
        try:
            cut = rpc("agent_get_cut", p_token=TOKEN, p_id=t["cut_id"]) or {}
            if cut.get("status") == "generating":
                k = inflight_take(cut)
                if k:
                    rpc("agent_save_take", p_token=TOKEN, p={"id": k["id"], "status": "failed", "error": str(e)[:300]})
                    set_cost_outcome([k.get("cost_event_id")], "failed", str(e)[:200], "tool")
                rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "failed"})
        except Exception as ex:
            log(f"생성 실패 정리 실패: {ex}")
        raise


def _generate(t):
    cut = rpc("agent_get_cut", p_token=TOKEN, p_id=t["cut_id"])
    if not cut:
        raise RuntimeError("컷을 찾지 못했습니다")
    if (t.get("payload") or {}).get("after_director") and cut.get("fix_request"):   # '보완해서 다시 만들기': 감독이 보완을 끝낼 때까지 기다렸다가 만든다
        try:
            waited = time.time() - datetime.fromisoformat(str(t.get("created_at")).replace("Z", "+00:00")).timestamp()
        except Exception:
            waited = 0
        if waited > 25 * 60:
            rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "designed"})
            raise RuntimeError("감독 보완이 25분 넘게 끝나지 않아 생성을 멈췄습니다(감독 일이 실패했을 수 있음). 제작 탭에서 확인하고 다시 눌러 주세요")
        raise Retry(30, "감독이 보완하는 중 — 끝나면 바로 만듭니다")
    if cut["status"] not in ("approved", "generating"):
        return f"생성 승인 상태가 아니어서 건너뜀 ({cut['status']})"
    ep = cut["episode_id"]
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=cut.get("script_version_id"))
    gen = (ctx.get("settings") or {}).get("generation") or {}
    res = cut.get("resolution") or gen.get("resolution", "480p")
    refs, first_url, last_url = [], None, None
    # 앞 컷에서 이어 시작: 통과한 앞 컷의 실제 마지막 장면을 첫 장면으로
    if cut.get("continue_prev"):
        try:
            prev = next((x for x in rpc("agent_list_cuts", p_token=TOKEN, p_episode=ep, p_scene=cut["scene_no"]) or []
                         if x["cut_no"] == cut["cut_no"] - 1 and x.get("best_take_id")), None)
            waiting = next((x for x in rpc("agent_list_cuts", p_token=TOKEN, p_episode=ep, p_scene=cut["scene_no"]) or []
                            if x["cut_no"] == cut["cut_no"] - 1 and not x.get("best_take_id") and x.get("status") in ("approved", "generating", "reviewing")), None)
            if waiting and waited(t) < 30 * 60:
                raise Retry(600, f"앞 컷 {waiting.get('code')}이 끝나면 그 마지막 장면에서 이어 시작합니다")
            if prev:
                ptk = rpc("agent_get_take", p_token=TOKEN, p_id=prev["best_take_id"])
                src = take_source(prev["best_take_id"], ptk)
                lastjpg = os.path.join(HOME, "work", f"last_{prev['id']}.jpg")
                ff("-sseof", "-0.1", "-i", src, "-frames:v", "1", "-q:v", "2", lastjpg)
                sid = ((ctx.get("series") or {}).get("id")) or "series"
                pth = storage_upload(f"{sid}/chain/{prev['id']}_{uuid.uuid4().hex[:6]}.jpg", lastjpg, "image/jpeg")
                first_url = storage_signed_url("takes", pth, 7200)
                progress(f"{cut['code']} 앞 컷({prev.get('code')}) 마지막 장면에서 이어 시작")
        except Retry:
            raise
        except Exception as ex:
            log(f"앞 컷 이어 붙이기 실패, 첫 장면 사진 사용: {ex}")
    if not first_url and (cut.get("frame_first") or {}).get("path"):
        first_url = storage_signed_url("takes", cut["frame_first"]["path"], 7200)
    if cut.get("use_end_frame") and (cut.get("frame_last") or {}).get("path"):
        last_url = storage_signed_url("takes", cut["frame_last"]["path"], 7200)
    if not first_url:
        imgs = {b["name"]: b.get("images") or [] for b in (ctx.get("bible") or [])}
        for n in cut.get("reference_names") or []:
            for pth in imgs.get(n, [])[:1]:
                try:
                    refs.append(storage_signed_url("bible", pth, 7200))
                except Exception as ex:
                    log(f"참고 사진 주소 실패({n}): {ex}")
        refs = refs[:4]
    vm = VIDEO_MODELS.get(gen.get("video_model") or "seedance-2.5", VIDEO_MODELS["seedance-2.5"])
    if res not in vm["res"]:
        res = vm["res"][0]
    if not first_url and refs and "ref" not in vm["routes"]:
        refs = []   # 참고 사진 방식이 없는 모델(예: Kling)은 글로만
    kind = "i2v" if first_url else "ref" if refs else "t2v"
    route = vm["routes"][kind]
    if vm["key"] == "kling-4.0":
        body = {"prompt": cut["prompt"], "duration": int(cut["seconds"]), "resolution": res}
        if kind == "t2v":
            body["aspect_ratio"] = gen.get("aspect_ratio", "9:16")
    else:
        body = {"prompt": cut["prompt"], "duration": int(cut["seconds"]), "resolution": res,
                "generate_audio": bool(gen.get("audio", True))}
        if route == "text-to-video":   # output_format은 글→영상만 받는다(사진→영상·참고→영상은 모르는 칸이 있으면 422로 거절)
            body["output_format"] = "mp4"
        if route != "image-to-video":
            body["aspect_ratio"] = gen.get("aspect_ratio", "9:16")
    if first_url:
        body["image_url"] = first_url
        if last_url:
            body["end_image_url"] = last_url
    elif refs:
        body["image_urls"] = refs
    pmodel = f"{vm['key']}-{kind}-{res}"
    inflight = inflight_take(cut)
    if inflight:
        # 재시작·끊김 뒤 다시 집힌 일: 이미 Higgsfield에 맡긴 요청을 이어서 기다린다(같은 컷을 두 번 결제하지 않게)
        take_id, rid, cost_id = inflight["id"], inflight["request_id"], inflight.get("cost_event_id")
        status_url = f"https://api.higgsfield.ai/requests/{rid}/status"
        progress(f"{cut['code']} 이미 맡긴 생성 요청을 이어서 기다립니다(새로 결제하지 않음)")
    else:
        rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "generating"})
        progress(f"{cut['code']} {vm['name']} {res} {cut['seconds']}초 생성 요청 · " + ("첫 장면 사진" + (" + 끝 장면" if last_url else "") if first_url else f"참고 사진 {len(refs)}장" if refs else "글로만"))
        # 같은 컷·같은 시도·같은 프롬프트면 같은 열쇠: 응답을 못 받고 다시 보내도 Higgsfield가 같은 요청으로 처리(두 번 결제 방지)
        idem = f"nyx-{cut['id']}-{len(cut.get('takes') or []) + 1}-{uuid.uuid5(uuid.NAMESPACE_URL, json.dumps({k: v for k, v in body.items() if 'url' not in k}, sort_keys=True) + route).hex[:12]}"   # 서명 주소는 매번 달라 빼고
        try:
            code, data = http_json(f"https://api.higgsfield.ai/{vm['base']}/{route}",
                                   {**hf_auth_headers(), "Content-Type": "application/json", "Accept": "application/json", "Idempotency-Key": idem},
                                   "POST", body, timeout=120)
        except Exception as ex:   # 응답이 끊김: 같은 열쇠로 잠시 뒤 다시 보낸다
            raise Retry(60, f"생성 요청 응답이 끊겨 1분 뒤 같은 요청으로 다시 확인합니다({type(ex).__name__})")
        if code in (429, 500, 502, 503, 504) and waited(t) < 2 * 3600:   # 대기열이 꽉 참·서버 오류: 실패로 끝내지 않고 기다렸다 다시
            raise Retry(90 if code == 429 else 60, f"Higgsfield가 붐벼요({code}). 잠시 뒤 자동으로 다시 요청합니다")
        if code not in (200, 201, 202) or not isinstance(data, dict):
            rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "failed"})
            raise RuntimeError(f"생성 요청 실패 {code}: {str(data)[:300]}")
        rid = data.get("request_id") or data.get("id")
        if not rid:
            rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "failed"})
            raise RuntimeError(f"생성 요청 번호를 받지 못했습니다: {str(data)[:300]}")
        status_url = data.get("status_url") or f"https://api.higgsfield.ai/requests/{rid}/status"
        cost_id = log_cost("video", "higgsfield", pmodel, {"seconds": int(cut["seconds"])}, episode_id=ep, scene_no=cut["scene_no"],
                           cut_id=cut["code"], attempt_no=len(cut.get("takes") or []) + 1, outcome="pending", job_id=t["id"],
                           note=f"{vm['name']} {route} {res}")
        take_id = rpc("agent_save_take", p_token=TOKEN, p={"cut_id": cut["id"], "model": f"{vm['base']}/{route}", "resolution": res,
                                                            "seconds": int(cut["seconds"]), "request_id": rid, "status": "submitted",
                                                            "cost_event_id": cost_id, "usd": estimate_usd("higgsfield", pmodel, {"seconds": int(cut["seconds"])})})
    deadline, last, st = time.time() + 40 * 60, None, {}
    if inflight and inflight.get("status") == "completed" and inflight.get("remote_url"):
        st, deadline = {"status": "completed", "_url": inflight["remote_url"]}, 0   # 이미 끝났고 받기만 남음
    while time.time() < deadline:
        time.sleep(10)
        try:
            c2, st = http_json(status_url, {**hf_auth_headers(), "Accept": "application/json"}, timeout=60)
        except Exception as ex:
            log(f"상태 확인 실패(다시 시도): {ex}")
            continue
        s = (st or {}).get("status") if isinstance(st, dict) else None
        if s != last:
            progress(f"{cut['code']} 생성 상태: {s}")
            last = s
            if s in ("queued", "in_progress"):
                rpc("agent_save_take", p_token=TOKEN, p={"id": take_id, "status": "in_progress" if s == "in_progress" else "submitted"})
        if s in ("completed", "failed", "nsfw", "canceled"):
            break
    s = (st or {}).get("status") if isinstance(st, dict) else None
    if s != "completed":
        rpc("agent_save_take", p_token=TOKEN, p={"id": take_id, "status": s if s in ("failed", "nsfw", "canceled") else "failed",
                                                 "error": f"생성 {s or '시간 초과'}: {str(st)[:300]}"})
        rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "failed"})
        set_cost_outcome([cost_id], "failed", f"생성 {s or '시간 초과'}", "tool")
        notify(f"{cut['code']} 생성 실패", f"상태: {s or '40분 시간 초과'}. 제작 탭에서 감독에게 수정 요청하거나 다시 승인해 주세요", "warning")
        raise RuntimeError(f"생성 {s or '시간 초과'}")
    url = st.get("_url") or find_video_url(st)
    if not url:
        raise RuntimeError(f"완료됐지만 영상 주소를 찾지 못했습니다: {str(st)[:300]}")
    rpc("agent_save_take", p_token=TOKEN, p={"id": take_id, "status": "completed", "remote_url": url})
    take = rpc("agent_get_take", p_token=TOKEN, p_id=take_id)
    progress(f"{cut['code']} 영상 받는 중")
    tmp = os.path.join(HOME, "work", f"{take_id}.mp4")
    fetch_to(url, tmp)
    files = save_local_package(take, tmp)
    tmp_raw = tmp
    tmp = apply_dialogue_tool(ctx, gen, cut, files, tmp, ep)
    # 대시보드에서 보는 미리보기: 원본(소리 포함)·포스터
    sid = ((take.get("episode") or {}).get("series_id")) or "series"
    base = f"{sid}/E{int(take['episode']['number']):02d}/S{int(cut['scene_no']):02d}/C{int(cut['cut_no']):02d}_t{take['take_no']}_{uuid.uuid4().hex[:6]}"
    pub = {}
    try:
        poster = os.path.join(HOME, "work", f"{take_id}.jpg")
        ff("-ss", "1", "-i", tmp, "-frames:v", "1", "-vf", "scale=360:-2", poster)
        pub["poster"] = storage_upload(base + ".jpg", poster, "image/jpeg")
        os.remove(poster)
    except Exception as ex:
        log(f"포스터 실패: {ex}")
    try:
        if files.get("voice"):
            va = os.path.join(HOME, "work", f"{take_id}.m4a")
            ff("-i", files["voice"], "-c:a", "aac", "-b:a", "96k", va)
            pub["voice_preview"] = storage_upload(base + "_voice.m4a", va, "audio/mp4")
            os.remove(va)
    except Exception as ex:
        log(f"대사 미리듣기 실패: {ex}")
    if os.path.getsize(tmp) < 48 * 1024 * 1024:
        pub["preview"] = storage_upload(base + ".mp4", tmp, "video/mp4")
    else:
        log("영상이 커서 미리보기 올리기를 건너뜀(로컬·윈도우에는 저장됨)")
    rpc("agent_save_take", p_token=TOKEN, p={"id": take_id, "files": {**pub, PLATFORM: files}})
    # 제작에서 만든 목소리를 바이블에 저장(대표 결정): 한 사람만 말하는 컷의 대사 소리를 그 인물 카드에(처음 생긴 것만, 대표가 바꾼 것은 덮지 않음)
    if pub.get("voice_preview"):
        spk = {re.sub(r"\s*\(.*?\)\s*", "", (ln.get("speaker") or "")).strip() for ln in (cut.get("voice_lines") or parse_dialogue(cut.get("dialogue")))}
        spk.discard("")
        if len(spk) == 1:
            try:
                if rpc("agent_bible_voice_sample", p_token=TOKEN, p_episode=ep, p_name=next(iter(spk)), p_path="takes:" + pub["voice_preview"]):
                    notify(f"{next(iter(spk))} 목소리를 바이블에 저장", f"{cut['code']} 영상의 목소리를 인물 카드에 넣었습니다(다른 컷 목소리로 바꾸려면 제작 탭 컷의 '이 목소리를 바이블에')")
            except Exception as ex:
                log(f"바이블 목소리 저장 실패: {ex}")
    if gen.get("auto_review"):   # 설정에서 자동 감수를 켰을 때만(예전 방식)
        rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "reviewing"})
        try:
            review_take(take_id, tmp)
        except Exception as ex:
            log(f"영상 감수 실패(대기열로 다시): {ex}")
            rpc("agent_add_task", p_token=TOKEN, p={"kind": "video_review", "episode_id": ep, "cut_id": cut["id"], "take_id": take_id})
    else:   # 대표 결정(11/25): 생성되면 바로 '생성됨'(이 테이크를 쓰는 컷). 영상·음성·감정 감수는 대표가 버튼으로 할 때만
        rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "passed", "best_take_id": take_id})
        set_cost_outcome([take.get("cost_event_id")], "success", None, None)
        notify(f"{cut['code']} 영상 생성됨", "제작 탭에서 ▶ 영상 보기로 확인하고, 필요하면 영상·음성·감정 감수나 보완 요청을 눌러 주세요")
        try:
            auto_rough_cut(ep)
        except Exception as ex:
            log(f"가편집 자동 만들기 실패: {ex}")
    queue_store(take_id, ep, cut)
    if tmp != tmp_raw:
        try:
            os.remove(tmp_raw)
        except OSError:
            pass
    try:
        os.remove(tmp)
    except OSError:
        pass
    return f"{cut['code']} 생성 완료 · {files['folder']}"


def queue_store(take_id, ep, cut):
    if PLATFORM == "windows":
        return
    try:
        if rpc("agent_has_platform", p_token=TOKEN, p_platform="windows"):
            rpc("agent_add_task", p_token=TOKEN, p={"kind": "store", "platform": "windows", "episode_id": ep, "cut_id": cut["id"], "take_id": take_id})
    except Exception as ex:
        log(f"윈도우 보관 작업 등록 실패: {ex}")


# ---------------- 영상감수자(최종감수 겸) + 평가(점수는 코드로)
WEIGHTS = {"face_consistency": 0.15, "costume_background": 0.1, "expression_changes": 0.12, "gesture_action": 0.12, "pace": 0.08, "emotion": 0.08,
           "motion_quality": 0.08, "prompt_match": 0.07, "voice_emotion": 0.12, "voice_consistency": 0.08}


def inum(v, default=0):
    """'7', 7, 'S#7-1', '7-2', None → 정수(첫 숫자). AI가 번호를 글자로 써도 작업이 멈추지 않게."""
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    m = re.search(r"\d+", str(v or ""))
    return int(m.group(0)) if m else default


def num(v):
    """'85점', '85/100', None 같은 값을 숫자로(못 읽으면 None)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group(0)) if m else None


def review_take(take_id, local_mp4=None):
    take = rpc("agent_get_take", p_token=TOKEN, p_id=take_id)
    cut = take["cut"]
    ep = cut["episode_id"]
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=cut.get("script_version_id"))
    model = setting(ctx, "generation", "claude_reviewer", "claude-sonnet-5-5")
    src = local_mp4
    if not src or not os.path.exists(src):
        src = os.path.join(HOME, "work", f"{take_id}.mp4")
        if not os.path.exists(src):
            prev = (take.get("files") or {}).get("preview")
            if prev:
                fetch_to(storage_signed_url("takes", prev, 900), src)
            elif take.get("remote_url"):
                fetch_to(take["remote_url"], src)
            else:
                raise RuntimeError("감수할 영상 파일이 없습니다")
    progress(f"{cut['code']} 영상감수자가 장면을 1초 간격으로 보는 중")
    fdir = os.path.join(HOME, "work", f"frames_{take_id}")
    os.makedirs(fdir, exist_ok=True)
    ff("-i", src, "-vf", "fps=1,scale=448:-2", "-frames:v", "16", "-q:v", "4", os.path.join(fdir, "f_%02d.jpg"))
    frames = sorted(glob.glob(os.path.join(fdir, "f_*.jpg")))
    content = []
    for i, fp in enumerate(frames):
        with open(fp, "rb") as f:
            content.append({"type": "text", "text": f"{i}초"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}})
    shutil.rmtree(fdir, ignore_errors=True)
    sound = None
    if has_audio(src):
        try:
            progress(f"{cut['code']} 감정감수자가 소리를 분석하는 중(받아쓰기·크기·높낮이)")
            wav = os.path.join(HOME, "work", f"aud_{take_id}.wav")
            ff("-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav)
            text_heard, words = "", []
            try:
                text_heard, words = stt_words(wav, setting(ctx, "generation", "stt_model", "scribe_v1"))
                log_cost("audio", "elevenlabs", "speech-to-text", {"seconds": cut.get("seconds")}, episode_id=ep, scene_no=cut.get("scene_no"),
                         cut_id=cut.get("code"), outcome="success", note="영상 소리 받아쓰기")
            except Exception as ex:
                log(f"받아쓰기 실패(크기·높낮이로만 감수): {ex}")
            sound = {"heard_text": text_heard, "words": words[:120], "curve": curve_summary(audio_curve(wav)),
                     "planned": [{k: ln.get(k) for k in ("speaker", "text", "direction", "tags", "analysis")} for ln in (cut.get("voice_lines") or [])]}
            os.remove(wav)
        except Exception as ex:
            log(f"소리 분석 실패: {ex}")
    if sound:
        content.append({"type": "text", "text": "[영상 소리 분석 — 감정감수자용]\n들린 대사(받아쓰기): " + (sound["heard_text"] or "(실패)")
                        + "\n단어별 시간: " + json.dumps(sound["words"], ensure_ascii=False)
                        + "\n1초마다 크기(dBFS)·높낮이(Hz): " + json.dumps(sound["curve"], ensure_ascii=False)
                        + "\n컷 설계 때 승인된 대사 미리 듣기(목표): " + json.dumps(sound["planned"], ensure_ascii=False)})
    content.append({"type": "text", "text": "[컷 설계]\n" + json.dumps({k: cut.get(k) for k in (
        'code', 'seconds', 'summary', 'dialogue', 'emotion', 'expression_beats', 'camera', 'reference_names', 'prompt')}, ensure_ascii=False)})
    system = (guide(ctx, "영상감수자", "감정감수자") or "너는 영상감수자다.") + f"""

[시리즈 바이블]
{bible_text(ctx)}

[감독 교훈]
{lessons_text(ctx)}

[보는 법] 1초 간격 장면 사진과 컷 설계를 비교한다. 소리는 들을 수 없으니 판단하지 않는다(대표 확인).
영상감수자는 '화면 속 연기와 일관성'의 책임자다. 각 항목 0~100점:
face_consistency(바이블 얼굴과 같은 사람인가), costume_background(의상·소품·배경이 바이블·앞뒤 컷과 같은가),
expression_changes(2~3초마다 감정에 맞게 표정이 바뀌는가), gesture_action(손짓·몸짓·동작이 대사와 감정에 맞고 자연스러운가),
pace(움직임·카메라 속도가 감정 템포에 맞는가: 너무 빠르거나 느리지 않은가), emotion(화면 전체가 이 컷의 감정선을 전달하는가),
motion_quality(손·몸 왜곡, 깜빡임, AI 티), prompt_match(설계대로인가).
치명 문제(얼굴이 다른 사람, 의상이 바뀜, 심한 왜곡, 화면 글자)가 있으면 verdict는 fail.
감정감수자는 '소리 분석'이 있으면 함께 판정한다(소리 분석이 없으면 이 두 항목은 빼고 답한다):
voice_emotion(들린 대사의 크기·높낮이 흐름이 감정선·목표 미리 듣기와 맞는가, 대사가 대본과 같은가, 배경음악이 섞이지 않았는가),
voice_consistency(같은 인물 목소리 높이·음색이 목표 미리 듣기·다른 컷과 비슷한가 — 높낮이 중앙값이 크게 다르면 감점).
대사가 대본과 다르게 들리거나 다른 사람 목소리면 치명.

[답 형식]
<json>{{"verdict": "pass|fail", "scores": {{"face_consistency": 0, "costume_background": 0, "expression_changes": 0, "gesture_action": 0, "pace": 0, "emotion": 0, "motion_quality": 0, "prompt_match": 0, "voice_emotion": 0, "voice_consistency": 0}},
 "issues": [{{"t": "3초", "problem": "", "fix": ""}}], "fix_for_director": "다음 생성에서 고칠 프롬프트 방향",
 "summary": "대표용 한두 줄",
 "lessons": [{{"fingerprint": "", "category": "face|costume|location|prop|continuity|expression|emotion_tone|dialogue|camera|motion|prompt|other", "problem": "", "rule": "", "bad_example": "", "severity": "보통|치명"}}]}}</json>"""
    text, rcost = claude(model, system, content, max_tokens=4000, stage="video_review", episode_id=ep,
                         scene_no=cut.get("scene_no"), cut_code=cut.get("code"), note="영상·소리 감수")
    try:
        rv = pick_json(text)
    except Exception:   # 답이 잘리거나 형식이 깨졌을 때 한 번 더(결과만 짧게)
        text, rcost2 = claude(model, system, content + [{"type": "text", "text": "답을 <json>…</json> 하나로만, issues·lessons는 3개 이하로 짧게 다시 써라."}],
                              max_tokens=6000, stage="video_review", episode_id=ep, scene_no=cut.get("scene_no"), cut_code=cut.get("code"), note="영상 감수(형식 재요청)")
        rv = pick_json(text)
    if sound:
        rv["sound"] = {"heard_text": sound["heard_text"], "curve": sound["curve"]}
    sc = {k: num(v) for k, v in (rv.get("scores") or {}).items()}
    if not sound:   # 소리 분석이 없으면 소리 두 항목은 점수에서 뺀다(0점이 섞여 멀쩡한 컷이 불합격·유료 재생성되던 문제)
        sc.pop("voice_emotion", None); sc.pop("voice_consistency", None)
    got = {k: w for k, w in WEIGHTS.items() if sc.get(k) is not None}
    score = round(sum(sc[k] * w for k, w in got.items()) / (sum(got.values()) or 1), 1)
    passed = rv.get("verdict") == "pass" and score >= 70
    verdict = "pass" if passed else "fail"
    rv["score"] = score
    rpc("agent_save_take", p_token=TOKEN, p={"id": take_id, "review": rv, "score": score, "verdict": verdict})
    rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "passed" if passed else "failed", **({"best_take_id": take_id} if passed else {})})
    set_cost_outcome([take.get("cost_event_id"), rcost], "success" if passed else "failed",
                     None if passed else (rv.get("summary") or "영상 감수 불합격")[:300], None if passed else "director")
    if not passed:
        for l in rv.get("lessons") or []:
            try:
                rpc("agent_add_lesson", p_token=TOKEN, p_lesson={**l, "series_id": (take.get("episode") or {}).get("series_id"),
                                                                 "sources": ["영상감수자"], "cut_refs": [cut.get("code")]})
            except Exception as ex:
                log(f"교훈 기록 실패: {ex}")
    folder = ((take.get("files") or {}).get(PLATFORM) or {}).get("folder")
    if folder and os.path.isdir(folder):
        try:
            write_scene_info(folder, {**take, "verdict": verdict, "score": score}, {})
        except Exception:
            pass
    notify(f"{cut['code']} {'통과' if passed else '불합격'} ({score}점)", (rv.get("summary") or "")[:300], "info" if passed else "warning")
    try:
        if passed:
            auto_rough_cut(ep)
        else:
            maybe_auto_retry(ctx, ep, cut, take, rv)
    except Exception as ex:
        log(f"감수 뒤 이어 하기 실패: {ex}")
    return verdict, score


def maybe_auto_retry(ctx, ep, cut, take, rv):
    """설정 '자동 재시도 횟수' 안이면 감독이 감수 의견으로 고치고 → 사진·목소리 준비 → 다시 생성까지 자동으로."""
    mx = int(setting(ctx, "generation", "max_auto_retries", 0) or 0)
    n = int(take.get("take_no") or 1)
    if mx <= 0 or n > mx:
        return
    rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"fix_request": f"[자동 재시도 {n}/{mx}] " + (rv.get("fix_for_director") or rv.get("summary") or "")[:500]})
    rpc("agent_add_task", p_token=TOKEN, p={"kind": "director", "episode_id": ep, "scene_no": cut.get("scene_no"), "cut_id": cut["id"],
                                           "payload": {"cut_id": cut["id"], "auto_retry": True}})
    log(f"{cut['code']} 불합격 → 자동 재시도 {n}/{mx}")


def auto_rough_cut(ep):
    """한 화의 모든 컷이 통과하면 기본 타임라인(통과 영상 순서대로 + 대사 자막)으로 가편집 미리보기를 자동으로 만든다.
    대표가 이미 편집을 시작한 화는 건드리지 않는다(DB가 확인)."""
    cuts = rpc("agent_list_cuts", p_token=TOKEN, p_episode=ep, p_scene=None) or []
    live = [c for c in cuts if c.get("status") != "hold"]
    if not live or any(not c.get("best_take_id") for c in live):
        return
    clips, subs = [], []
    for i, c in enumerate(sorted(live, key=lambda x: (inum(x.get("scene_no") or 0), inum(x.get("cut_no") or 0)))):
        tk = rpc("agent_get_take", p_token=TOKEN, p_id=c["best_take_id"]) or {}
        ln = float(tk.get("seconds") or c.get("seconds") or 5)
        cid = f"c{uuid.uuid4().hex[:6]}"
        clips.append({"id": cid, "cut_id": c["id"], "take_id": c["best_take_id"], "in": 0, "out": ln, "src_len": ln, "gap": 0, "voice_offset": 0, "voice_gain": 1})
        lines = [re.sub(r"\([^)]*\)|\[[^\]]*\]", "", re.sub(r"^\s*[^:：]{1,12}[:：]\s*", "", l)).strip() for l in str(c.get("dialogue") or "").split("\n")]
        lines = [l for l in lines if l]
        span = max(0.6, (ln - 0.3) / max(1, len(lines)))
        for k, text in enumerate(lines):
            subs.append({"id": f"s{uuid.uuid4().hex[:6]}", "clip_id": cid, "rs": round(0.15 + k * span, 3), "re": round(min(ln, 0.15 + (k + 1) * span - 0.05), 3), "text": text})
    rid = rpc("agent_rough_cut", p_token=TOKEN, p_episode=ep, p_timeline={"clips": clips, "music": [], "subs": subs, "settings": {}})
    if rid:
        notify("가편집본 자동 생성", f"모든 컷({len(clips)}개)이 감수를 통과해 가편집 미리보기를 만들고 있습니다. 편집 탭에서 다듬고 완성본을 내보내 주세요")


@task("video_review")
def t_video_review(t):
    mode = (t.get("payload") or {}).get("mode")
    if mode in FOCUS:   # 대표가 컷에서 누른 영상·음성·감정 감수(컷 상태는 바꾸지 않음)
        return review_take_focus(t["take_id"], mode)
    try:
        v, s = review_take(t["take_id"])
    except Exception as e:
        if (t.get("attempts") or 1) >= 2 and not is_transient(str(e)):   # 마지막 시도: 컷을 '감수 중'에 묶어 두지 않는다
            try:
                tk = rpc("agent_get_take", p_token=TOKEN, p_id=t["take_id"]) or {}
                if (tk.get("cut") or {}).get("status") == "reviewing":
                    rpc("agent_update_cut", p_token=TOKEN, p_id=tk["cut"]["id"], p={"status": "failed"})
            except Exception:
                pass
        raise
    return f"감수 {v} ({s}점)"


@task("store")
def t_store(t):
    """집 윈도우: 원본을 받아 씬 폴더(OneDrive)에 원본·영상(무음)·대사·현장음으로 나눠 보관."""
    take = rpc("agent_get_take", p_token=TOKEN, p_id=t["take_id"])
    if not take:
        raise RuntimeError("테이크를 찾지 못했습니다")
    tmp = os.path.join(HOME, "work", f"{take['id']}.mp4")
    ok = False
    if take.get("remote_url"):
        try:
            fetch_to(take["remote_url"], tmp)
            ok = True
        except Exception as ex:
            log(f"Higgsfield 원본 받기 실패, 미리보기로 대신: {ex}")
    if not ok and (take.get("files") or {}).get("preview"):
        fetch_to(storage_signed_url("takes", take["files"]["preview"], 900), tmp)
        ok = True
    if not ok:
        raise RuntimeError("받을 영상이 없습니다(원본 주소 만료, 미리보기 없음)")
    files = save_local_package(take, tmp)
    rpc("agent_save_take", p_token=TOKEN, p={"id": take["id"], "files": {PLATFORM: files}})
    try:
        os.remove(tmp)
    except OSError:
        pass
    return f"보관 완료: {files['folder']}"


# ---------------- ElevenLabs: 목소리 디자인
def el_key():
    k = (secrets("elevenlabs_api_key").get("elevenlabs_api_key") or "").strip().strip('"\'')
    if not k:
        raise RuntimeError("ElevenLabs 키가 없습니다. 대시보드 설정 → API 키에서 넣어 주세요")
    return k


def el_json(path, body=None, method="POST", timeout=180):
    code, data = http_json("https://api.elevenlabs.io" + path, {"xi-api-key": el_key(), "Content-Type": "application/json",
                                                                 "Accept": "application/json"}, method, body, timeout=timeout)
    if code not in (200, 201):
        det = (data or {}).get("detail") if isinstance(data, dict) else data
        msg = (det.get("message") or det.get("status")) if isinstance(det, dict) else det
        raise RuntimeError(f"ElevenLabs {code}: {str(msg)[:300]}")
    return data


# ---------------- 바이블 사진 디자인: Soul 2(실사 인물) + Soul ID(얼굴 고정)
PHOTO_LOOK = ("Photorealistic photograph of a real person, shot on a full-frame camera with an 85mm lens, natural skin texture with pores, "
              "soft natural light, realistic hair strands, no makeup filter, no illustration, no 3D render, "
              "no text, no letters, no captions, no name tags, no subtitles, no watermark, no logo.")
SET_SHOTS = [("정면 얼굴", "Head-and-shoulders portrait facing the camera, neutral expression, plain light grey studio background."),
             ("3/4 얼굴", "Three-quarter view portrait turned slightly to the left, gentle smile, plain light grey studio background."),
             ("전신·의상", "Full-body shot standing naturally, wearing the signature outfit, plain light grey studio background."),
             ("감정 표정", "Close-up portrait with a strong emotional expression matching the character, plain light grey studio background.")]


def upload_from_url(url, path):
    local = os.path.join(HOME, "work", f"ph_{uuid.uuid4().hex[:8]}.img")
    fetch_to(url, local)
    jpg = local + ".jpg"
    ff("-i", local, "-q:v", "2", jpg)
    p = storage_upload(path, jpg, "image/jpeg")
    for x in (local, jpg):
        try:
            os.remove(x)
        except OSError:
            pass
    return p


def _photo_generate(d, name):
    """사진 디자인 한 번 만들기 → (사진 주소들, 모델)."""
    cm = (d.get("model") or (d.get("generation") or {}).get("char_photo_model") or "seedream")
    if d["category"] == "character" and cm == "soul2":
        progress(f"{name} 실사 인물 사진 후보를 만드는 중 (Soul 2)")
        urls = soul_images(d["prompt"] + " " + PHOTO_LOOK, n=4, aspect="3:4")
        model = SOUL2
    elif d["category"] == "character":   # 다른 모델(나노바나나 프로·Grok)로 후보 4장을 동시에 — 고른 사진으로 얼굴 학습(Soul ID)은 그대로
        model = char_photo_model(cm)
        progress(f"{name} 실사 인물 사진 후보 4장을 동시에 만드는 중 ({model_label(model)})")
        res = parallel([(lambda: hf_image(d["prompt"] + " Head-and-shoulders portrait, plain light grey background. " + PHOTO_LOOK, [], "3:4", model)) for _ in range(4)], workers=4)
        urls = [u for u in res if isinstance(u, str) and u]
        if not urls:
            raise RuntimeError(f"인물 사진 후보 실패({model}): {res[0]}")
    else:
        model = resolve_image_model(d.get("generation") or {})
        progress(f"{name} 사진 후보를 만드는 중")
        urls = []
        for _ in range(2):
            look = {"prop": " Photorealistic product photograph of the object alone, true-to-life materials and scale, soft studio light, plain neutral background, no people, no text.",
                    "background": " Photorealistic wide background plate photograph, real camera, natural light and atmosphere, no people, no text."}.get(
                d["category"], " Photorealistic location photograph, natural light, no people, no text.")
            st = hf_submit_wait(model, {"prompt": d["prompt"] + look, "aspect_ratio": "9:16"}, "사진")
            urls += find_image_urls(st.get("images") or st)[:1]
    return urls, model


@task("photo_design")
def t_photo_design(t):
    did = (t.get("payload") or {}).get("design_id")
    d = rpc("agent_get_photo_design", p_token=TOKEN, p_id=did)
    if not d:
        raise RuntimeError("사진 디자인 요청을 찾지 못했습니다")
    name = d["entry_name"]
    # 순서: 그 항목의 영어 프롬프트 반영·코디네이터 감수가 진행 중이면 그게 끝난 뒤에 만든다
    if d.get("prompt_pending") and waited(t) < 180:   # 최대 약 3분만 기다리고, 그 뒤엔 지금 프롬프트로 만든다
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "queued"})
        raise Retry(20, "바이블 영어 프롬프트 반영이 끝나길 기다리는 중(최대 3분, 끝나면 바로 만듭니다)")
    if d.get("use_bible") and d.get("prompt_en"):   # 대표가 묘사 칸을 손대지 않았으면 만들 때의 최신 영어 프롬프트
        d["prompt"] = d["prompt_en"]
    lr = d.get("last_review") or {}   # 지난 후보에서 코디네이터가 찾은 문제를 이번 프롬프트에 반영
    if d.get("use_bible") and lr.get("prompt_improved") and not lr.get("bible_fixed"):
        d["prompt"] = lr["prompt_improved"]
    if lr.get("avoid"):
        d["prompt"] = (d.get("prompt") or "").rstrip(". ") + ". Avoid: " + ", ".join(lr["avoid"]) + "."
    if re.search(r"[가-힣]", d.get("prompt") or ""):
        # 한글 묘사는 작가실이 영어 프롬프트로 옮긴 뒤에 만든다(사진 모델은 영어가 정확)
        if d.get("prompt_en"):
            d["prompt"] = d["prompt_en"]
        else:   # 기다리지 않고 바로 영어로 옮긴다(몇 초)
            progress(f"{name}: 한글 묘사를 영어 프롬프트로 바로 옮기는 중")
            txt, _ = claude("claude-sonnet-5-5", "드라마 인물·장소 사진 생성용 영어 프롬프트로 옮긴다. 눈에 보이는 특징만, 4~6문장, 이름·브랜드·한글 금지. 답은 <json>{\"prompt_en\": \"\"}</json>",
                            d.get("prompt") or "", max_tokens=1200, stage="other", note=f"사진용 영어 프롬프트: {name}")
            en = clean_en(txt)
            if en:
                d["prompt"] = en
    REJECT = re.compile(r"nsfw|safety|moderation|content.?filter|inappropriate|prohibited|sensitive", re.I)   # 403(키·권한 문제)은 내용 거절이 아니라 다시 써도 소용없음
    def gen_once():
        return _photo_generate(d, name)
    try:
        try:
            urls, model = gen_once()
        except Exception as ex1:
            if not REJECT.search(str(ex1)) or d.get("_safe_done"):
                raise
            progress(f"{name}: 생성 서비스가 내용을 거절해 코디네이터가 표현을 완곡하게 고쳐 다시 만드는 중")
            fixed, changed = safe_prompt(d["prompt"], name)
            if not fixed:
                raise
            d["prompt"], d["_safe_done"] = fixed, True
            rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"review": {"items": [], "summary": f"내용 거절 → 코디네이터가 표현을 완곡하게 고쳐 다시 만듦: {changed}",
                                                                                 "prompt_improved": fixed, "improve_note": changed}})
            urls, model = gen_once()
        # 동시에 올리고, 하나가 실패해도 나머지 후보는 살린다(이미 결제된 사진을 통째로 버리고 다시 사던 문제)
        ups = parallel([(lambda i=i, u=u: upload_from_url(u, f"photos/{did}/{i}_{uuid.uuid4().hex[:6]}.jpg")) for i, u in enumerate(urls, 1)], workers=4)
        for r in ups:
            if isinstance(r, Exception):
                log(f"{name}: 후보 1장 올리기 실패: {r}")
        previews = [{"path": r} for r in ups if r and not isinstance(r, Exception)]
    except Exception as e:
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": str(e)[:500]})
        raise
    if not previews:
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": "후보가 만들어지지 않았습니다"})
        raise RuntimeError("후보가 만들어지지 않았습니다")
    rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "ready", "previews": previews})
    rpc("agent_add_task", p_token=TOKEN, p={"kind": "coord_review", "payload": {"design_id": did, "entry_id": d.get("entry_id"), "series_id": d.get("series_id")}})
    notify(f"{name} 사진 후보 {len(previews)}장 도착", "코디네이터가 확인해 추천 사진으로 바이블까지 자동 반영합니다" if d.get("auto_apply") else "바이블 탭에서 마음에 드는 사진을 골라 주세요")
    return f"{name} 사진 후보 {len(previews)}장"


@task("photo_set")
def t_photo_set(t):
    """고른 사진으로 Soul ID 학습 → 같은 얼굴로 기준 사진 세트."""
    did = (t.get("payload") or {}).get("design_id")
    d = rpc("agent_get_photo_design", p_token=TOKEN, p_id=did)
    if not d:
        raise RuntimeError("사진 디자인 요청을 찾지 못했습니다")
    name = d["entry_name"]
    try:
        chosen = d.get("chosen") or []
        src = [storage_signed_url(x.get("bucket") or "takes", x["path"], 7200) for x in chosen if x.get("path")]
        if not src:
            raise RuntimeError("고른 사진이 없습니다")
        cm = d.get("model") or (d.get("generation") or {}).get("char_photo_model") or "seedream"
        use_nano = cm != "soul2"   # soul2가 아니면 얼굴 학습 없이 고른 사진을 참고 사진으로(Seedream 5.0 Pro·나노바나나 프로 등)
        if use_nano:   # 나노바나나 프로: 얼굴 학습 없이, 고른 사진을 참고 사진으로 같은 얼굴의 기준 사진을 바로 만든다(빠름)
            soul_id = "nano-ref"
            nmodel = char_photo_model(cm)
        elif d.get("soul_id") and d.get("soul_id") != "nano-ref":   # 다시 시도: 이미 끝난 얼굴 학습은 다시 사지 않는다
            soul_id = d["soul_id"]
            progress(f"{name} 이미 학습한 얼굴(Soul ID)로 기준 사진을 이어서 만듭니다")
        else:
            progress(f"{name} 얼굴 학습 중 (Soul ID, 수 분 걸림)")
            soul_id = soul_train(f"{d.get('series_title') or 'NYX'} · {name}", src)
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "training", "soul_id": soul_id})
        look = (d.get("prompt_en") or d.get("description") or d.get("prompt") or "")[:900]   # 사진 모델에는 영어 프롬프트
        outfit = (d.get("details") or "")[:500]
        progress(f"{name} 기준 사진 {len(SET_SHOTS)}장을 동시에 만드는 중")

        def shot_fn(i, label, shot):
            text = f"{look} {outfit if label == '전신·의상' else ''} {shot} {PHOTO_LOOK}"
            if use_nano:
                gen = lambda: hf_image(text + " Keep exactly the same person (face, age, body proportions) as in the reference photos.", src[:4], "3:4", nmodel)
            else:
                gen = lambda: soul_images(text, n=1, aspect="3:4", soul_id=soul_id)[0]
            return lambda: {"label": label, "path": upload_from_url(gen(), f"photos/{did}/set_{i + 1}_{uuid.uuid4().hex[:6]}.jpg")}
        res = parallel([shot_fn(i, lb, sh) for i, (lb, sh) in enumerate(SET_SHOTS)], workers=len(SET_SHOTS))
        ref_set = []
        for (label, _), r in zip(SET_SHOTS, res):
            if isinstance(r, Exception):
                log(f"기준 사진 실패({label}): {r}")
            else:
                ref_set.append(r)
        if not ref_set:
            raise RuntimeError("기준 사진이 하나도 만들어지지 않았습니다: " + str(next((r for r in res if isinstance(r, Exception)), ""))[:200])
    except Exception as e:
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": str(e)[:500]})
        raise
    rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "set_ready", "soul_id": soul_id, "ref_set": ref_set})
    rpc("agent_add_task", p_token=TOKEN, p={"kind": "coord_review", "payload": {"design_id": did, "entry_id": d.get("entry_id"), "series_id": d.get("series_id")}})
    notify(f"{name} 얼굴 고정 완료", f"기준 사진 {len(ref_set)}장 — " + ("코디네이터 확인 뒤 바이블에 자동 반영합니다" if d.get("auto_apply") else "바이블 탭에서 확인하고 적용해 주세요"))
    return f"{name} {'나노바나나 프로 참고 사진 고정' if use_nano else 'Soul ID'} + 기준 사진 {len(ref_set)}장"


@task("voice_design")
def t_voice_design(t):
    """목소리 후보 만들기. 어디서 실패해도 디자인이 '만드는 중'에 남지 않게(그러면 시리즈 목소리 설계가 그 인물을 영영 건너뜀)."""
    try:
        return _voice_design(t)
    except Exception as e:
        if not (is_transient(str(e)) and (t.get("attempts") or 1) < 4):
            try:
                rpc("agent_update_voice_design", p_token=TOKEN, p_id=(t.get("payload") or {}).get("design_id"), p={"status": "failed", "error": str(e)[:500]})
            except Exception:
                pass
        raise


def _voice_design(t):
    did = (t.get("payload") or {}).get("design_id")
    d = rpc("agent_get_voice_design", p_token=TOKEN, p_id=did)
    if not d:
        raise RuntimeError("목소리 디자인 요청을 찾지 못했습니다")
    if d.get("sample_path"):
        return _voice_clone(d)
    progress(f"{d['entry_name']} 목소리 후보를 만드는 중")
    if len((d.get("sample_text") or "").strip()) < 100:   # 들어 볼 대사는 코디네이터가 대본에서 뽑아 쓴다
        d["sample_text"] = voice_sample_text(d)
    body = {"voice_description": voice_desc(d["description"])}
    sample = (d.get("sample_text") or "").strip()
    if len(sample) >= 100:
        body["text"] = sample[:1000]
    else:
        body["auto_generate_text"] = True
    try:
        data = el_json("/v1/text-to-voice/design", body, timeout=300)
    except Exception as e:
        rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": str(e)[:500]})
        raise
    previews = []
    for i, p in enumerate(data.get("previews") or [], 1):
        ext = "mp3" if "mpeg" in (p.get("media_type") or "audio/mpeg") else "wav"
        local = os.path.join(HOME, "work", f"vd_{did}_{i}.{ext}")
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as f:
            f.write(base64.b64decode(p.get("audio_base_64") or ""))
        path = storage_upload(f"voices/{did}/{i}_{uuid.uuid4().hex[:6]}.{ext}", local, p.get("media_type") or "audio/mpeg")
        os.remove(local)
        previews.append({"generated_voice_id": p.get("generated_voice_id"), "path": path, "duration": p.get("duration_secs")})
    if not previews:
        rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": "후보가 만들어지지 않았습니다"})
        raise RuntimeError("후보가 만들어지지 않았습니다")
    rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "ready", "previews": previews})
    log_cost("audio", "elevenlabs", "voice-design", {"calls": 1}, note=f"목소리 디자인: {d['entry_name']}", outcome="success")
    if d.get("auto"):   # 사진처럼: 대표가 고르기 전에 추천 후보(1번)를 먼저 바이블에 넣어 둔다 → 대사 미리 듣기가 바로 이 목소리로 나온다
        d = dict(d, previews=previews, chosen_generated_id=previews[0]["generated_voice_id"])
        rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "saving", "chosen_generated_id": d["chosen_generated_id"]})
        vid = _voice_save_design(d)
        r = rpc("agent_voice_link", p_token=TOKEN, p_design=did, p_force=bool(d.get("force_apply")))
        notify(f"{d['entry_name']} 추천 목소리 적용", f"후보 {len(previews)}개 중 1번을 먼저 넣었습니다({'바이블 적용' if r == 'linked' else '대표가 정한 목소리는 그대로 둠'}). 바이블 → 목소리에서 다른 후보로 바꿀 수 있습니다")
        return f"{d['entry_name']} 추천 목소리 적용 ({vid})"
    notify(f"{d['entry_name']} 목소리 후보 {len(previews)}개 도착", "바이블 탭에서 들어 보고 골라 주세요")
    return f"{d['entry_name']} 후보 {len(previews)}개"


# 대표 지시(기본 목소리): 모든 인물은 젊고, 사람 같은(AI 티 없는), 나긋하고 부드러운 목소리가 기본. 그 위에 인물마다 감정선만 얹는다.
VOICE_BASE = ("Young adult voice in their early-to-mid 20s, light and youthful timbre, soft and gentle, warm and calm, "
              "natural conversational Korean delivery like a real person talking, relaxed breathing, subtle natural pauses, "
              "human sentence endings: the last syllable softly trails off with a natural Korean falling intonation (gentle rise only for questions), "
              "a little breath or softness at the end, never clipped, abrupt, flat or sing-song robotic endings, "
              "not deep, not husky, not raspy, not mature, not middle-aged, not an announcer or narrator, not robotic or synthetic, "
              "clean close-mic studio recording.")
_OLD_WORDS = re.compile(r"\b(deep(er|ly)?|husky|raspy|gravell?y|gruff|mature|middle[- ]aged|elderly|old(er)?|aged|baritone|bass|booming|"
                        r"authoritative|commanding|heavy|weathered|30s|40s|50s|60s|thirties|forties|fifties|sixties|announcer|narrator)\b[,]?", re.I)


def voice_desc(desc):
    """목소리 설명 → 기본 목소리(젊고 나긋·사람 같음)를 앞에 두고, 나이 들게 만드는 낱말은 뺀 뒤 인물별 감정선을 붙인다."""
    body = re.sub(r"\b(in\s+)?(his|her|their)\s+(early|mid|late)?[- ]?(\d0s|thirties|forties|fifties|sixties)\b|\b(aged|age)\s*\d+\b|\d{2}\s*(대|세)", "",
                  (desc or "").split("\n[노래]")[0], flags=re.I)
    body = _OLD_WORDS.sub("", body)
    body = re.sub(r"\s{2,}", " ", re.sub(r"\s+,", ",", body)).strip(" ,.")
    return (VOICE_BASE + (" Character and emotional color: " + body if body else ""))[:1000]


def voice_sample_text(d):
    """코디네이터: 목소리 후보를 들어 볼 한국어 대사 120~250자. 대본에 이 인물 대사가 있으면 그 대사를 이어 붙여 쓰고,
    적으면 인물 설정·감정선에 맞춘 자기소개로 쓴다(감정이 두세 번 바뀌게 → 목소리 폭을 들을 수 있게)."""
    name = re.sub(r"\s*\(.*?\)\s*", "", d.get("entry_name") or "").strip()
    lines = []
    try:
        cast = rpc("agent_series_cast", p_token=TOKEN, p_series=d.get("series_id")) or {}
        for s in cast.get("scripts") or []:
            for ln in (s.get("text") or "").splitlines():
                m = re.match(r"\s*([^:：]{1,15})[:：]\s*(.+)", ln)
                if m and re.sub(r"\s*\(.*?\)\s*", "", m.group(1)).strip() == name:
                    lines.append(m.group(2).strip())
    except Exception as ex:
        log(f"대본에서 대사 찾기 실패: {ex}")
    try:
        text, _ = claude("claude-sonnet-5-5", """너는 코디네이터다. ElevenLabs 목소리 후보를 들어 볼 한국어 대사를 쓴다.
- 대본 대사가 있으면 그 대사들을 인용해 자연스럽게 이어 붙인다(괄호 지문은 빼고). 모자라면 인물 말투로 한두 문장 보탠다.
- 대사가 거의 없으면 인물 설정과 감정선에 맞춘 자기소개를 쓴다.
- 120~250자, 감정이 두세 번 바뀌게(예: 설렘 → 긴장 → 다짐). 대사 문장만, 따옴표·설명·이름표 없이.
답: <json>{"text": ""}</json>""", json.dumps({"인물": d.get("entry_name"), "요약": d.get("entry_summary"), "설정": (d.get("entry_description") or "")[:800],
                         "목소리 설명": (d.get("description") or "")[:500], "대본 대사": lines[:40]}, ensure_ascii=False),
            max_tokens=800, stage="other", note=f"목소리 시험 대사: {d.get('entry_name')}")
        t = (safe_json(text).get("text") or "").strip()
        if len(t) >= 80:
            return t[:400]
    except Exception as ex:
        log(f"시험 대사 쓰기 실패: {ex}")
    base = " ".join(lines)[:250]
    return base if len(base) >= 100 else (base + f" 안녕하세요, 저는 {name}이에요. 오늘은 정말 떨리지만, 여기까지 온 이유를 잊지 않을 거예요. 끝까지 해낼게요.")[:300]


def _voice_clone(d):
    """대표가 올린 목소리 파일 → ElevenLabs 즉시 복제(IVC) → 시험 대사로 미리 듣기 → 바이블에 적용."""
    did = d["id"]
    progress(f"{d['entry_name']} 올린 목소리 파일로 ElevenLabs 목소리를 만드는 중")
    ext = (d["sample_path"].rsplit(".", 1)[-1] or "mp3").lower()[:5]
    local = os.path.join(HOME, "work", f"vc_{did}.{ext}")
    fetch_to(storage_signed_url("takes", d["sample_path"], 600), local)
    with open(local, "rb") as f:
        data = f.read()
    os.remove(local)
    boundary = "----nyx" + uuid.uuid4().hex
    field = lambda k, v: f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    ctype = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg", "webm": "audio/webm"}.get(ext, "application/octet-stream")
    body = field("name", f"{d['entry_name']} · {d.get('series_title') or 'NYX'}"[:100]) + field("remove_background_noise", "true") \
        + field("description", (d.get("description") or "")[:500]) \
        + f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="sample.{ext}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.elevenlabs.io/v1/voices/add", data=body, method="POST",
                                 headers={"xi-api-key": el_key(), "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            vid = json.loads(r.read()).get("voice_id")
    except urllib.error.HTTPError as e:
        msg = e.read()[:300].decode("utf-8", "replace")
        hint = " (ElevenLabs 요금제에서 목소리 복제가 막혀 있거나, 파일이 너무 짧거나(30초 이상 권장) 형식이 맞지 않을 수 있습니다)"
        rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": f"ElevenLabs {e.code}: {msg}{hint}"[:500]})
        raise RuntimeError(f"ElevenLabs 목소리 복제 실패 {e.code}: {msg}{hint}") from None
    if not vid:
        rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": "ElevenLabs가 목소리 ID를 주지 않았습니다"})
        raise RuntimeError("ElevenLabs가 목소리 ID를 주지 않았습니다")
    upd = {"status": "saved", "voice_id": vid}
    try:   # 저장한 목소리로 시험 대사를 읽혀 바로 들어 볼 수 있게
        audio = tts_line(vid, (d.get("sample_text") or "안녕하세요. 오늘부터 이 목소리로 연기합니다.")[:400], "", "eleven_v3")
        lp = os.path.join(HOME, "work", f"vcp_{did}.mp3")
        with open(lp, "wb") as f:
            f.write(audio)
        upd["preview_path"] = storage_upload(f"voices/{did}/clone_{uuid.uuid4().hex[:6]}.mp3", lp, "audio/mpeg")
        os.remove(lp)
    except Exception as ex:
        log(f"복제 목소리 미리 듣기 실패: {ex}")
    rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p=upd)
    rpc("agent_voice_link", p_token=TOKEN, p_design=did)
    log_cost("audio", "elevenlabs", "voice-clone", {"calls": 1}, note=f"목소리 복제: {d['entry_name']}", outcome="success")
    notify(f"{d['entry_name']} 목소리 반영", "올린 목소리 파일로 만든 목소리를 바이블에 넣었습니다. 대사 미리 듣기가 이 목소리로 바뀝니다")
    return f"{d['entry_name']} 올린 목소리 반영 ({vid})"


@task("bible_extract")
def t_bible_extract(t):
    """작가실: 대본을 화별로 읽어 씬마다 필요한 인물·장소·소품을 뽑아 바이블에 올린다(새 항목은 대표 적용 대기 수정안)."""
    p = t.get("payload") or {}
    sid = t.get("series_id") or p.get("series_id")
    ov = rpc("agent_bible_overview", p_token=TOKEN, p_series=sid, p_episode=t.get("episode_id") or p.get("episode_id")) or {}
    title = (ov.get("series") or {}).get("title", "")
    known = [{k: e.get(k) for k in ("category", "name", "summary")} for e in ov.get("entries") or []]
    eps = [e for e in ov.get("episodes") or [] if (e.get("text") or "").strip()]
    if not eps:
        return "읽을 대본이 없습니다"
    system = """너는 드라마 작가실의 미술·캐스팅 담당이다. 대본을 읽고 씬마다 화면에 나와야 하는 것을 빠짐없이 뽑아 시리즈 바이블 항목으로 만든다.
- category: character(대사가 있거나 얼굴이 보이는 인물. 단역도 얼굴이 나오면 포함, '군중'처럼 얼굴이 중요하지 않으면 제외),
  location(씬이 벌어지는 장소. 같은 장소는 하나로), background(장소와 별도로 화면 뒤에 보이는 배경: 창밖 풍경, 도시 야경, 하늘, 무대 뒤 스크린 등. 반복되거나 분위기에 중요한 것만),
  prop(줄거리·연속성에 중요한 물건: 반복 등장, 클로즈업, 인물의 상징).
- 이미 바이블에 있는 항목은 이름을 그대로 쓰고 appears만 채운다(description은 비워도 됨).
- 새 항목의 description: 영상·사진 모델에 그대로 넣을 영어 고정 묘사 1~3문장. 인물은 나이·국적·성별·얼굴형·머리 모양과 색·체형·분위기,
  장소는 공간 구조·재질·색·시간대·빛, 배경은 풍경·거리감·날씨·시간대·빛, 소품은 모양·재질·색·크기. 실존 인물·배우 이름 금지.
- details: 한국어. 화별 의상·바뀌면 안 되는 특징·금지 사항.
- summary: 한국어 한 줄(대본 속 역할).
- appears: 이 항목이 나오는 씬 [{"ep": 화 번호, "scene": 씬 번호}]. 씬 번호는 대본의 씬 순서(1부터).
- 장소·소품·배경은 사진을 따로 만들지 않고 이 묘사로 바로 바이블에 적용된다. 그러니 description을 모습이 늘 같게 나올 만큼 구체적으로(구조·재질·색·크기·빛).
답 형식: <json>{"items": [{"category": "", "name": "", "summary": "", "description": "", "details": "", "appears": [{"ep": 1, "scene": 1}]}]}</json>"""
    total = {"new_entries": 0, "proposals": 0}
    for e in eps:
        progress(f"작가실이 {e['number']}화에서 필요한 인물·장소·소품을 찾는 중")
        user = f"시리즈: {title}\n\n[이미 바이블에 있는 항목]\n{json.dumps(known, ensure_ascii=False)}\n\n[{e['number']}화 {e.get('title') or ''} 대본]\n{(e.get('text') or '')[:60000]}"
        text, _ = claude("claude-opus-5-5", system, user, max_tokens=8000, stage="other", note=f"바이블 필요 항목 추출: {title} {e['number']}화")
        items = pick_json(text).get("items") or []
        for it in items:
            it["appears"] = [{"ep": inum(x.get("ep") or e["number"]), "scene": inum(x.get("scene") or 0)} for x in (it.get("appears") or []) if isinstance(x, dict)]
        r = rpc("agent_bible_upsert", p_token=TOKEN, p_series=sid, p_items=items) or {}
        for k in total:
            total[k] += int(r.get(k) or 0)
        for it in items:
            if not any(k["name"].lower() == (it.get("name") or "").lower() and k["category"] == it.get("category") for k in known):
                known.append({k: it.get(k) for k in ("category", "name", "summary")})
    if total["proposals"]:
        notify(f"바이블 필요 항목 {total['proposals']}개 제안", "바이블 탭에서 확인하고 '모두 적용 + 사진 만들기'를 눌러 주세요")
    return f"새 항목 {total['new_entries']}개 · 적용 대기 {total['proposals']}개"


def photo_block_large(path, label):
    """묘사용: 사진을 크게(긴 변 1280) 보여 준다. ffmpeg가 없으면 원본을 그대로(5MB 이하) 보낸다."""
    small = path + ".d.jpg"
    try:
        ff("-i", path, "-vf", "scale=1280:1280:force_original_aspect_ratio=decrease", "-frames:v", "1", "-q:v", "3", small)
        with open(small, "rb") as f:
            data, mt = f.read(), "image/jpeg"
        os.remove(small)
    except Exception as ex:
        log(f"사진 줄이기 실패, 원본 사용: {ex}")
        with open(path, "rb") as f:
            data = f.read()
        head = data[:12]
        mt = ("image/png" if head.startswith(b"\x89PNG") else "image/webp" if head[8:12] == b"WEBP"
              else "image/gif" if head.startswith(b"GIF") else "image/jpeg")
        if len(data) > 5_000_000:
            raise RuntimeError(f"{label}: 사진이 너무 커서 보낼 수 없습니다(5MB 이하로 줄여 주세요)")
    return [{"type": "text", "text": label},
            {"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(data).decode()}}]


@task("photo_describe")
def t_photo_describe(t):
    """대표가 올린 외부 사진을 보고, 영상·사진 모델에 그대로 넣을 고정 묘사(영어)와 세부 사항(한국어)을 쓴다."""
    p = t.get("payload") or {}
    info = rpc("agent_bible_photo_info", p_token=TOKEN, p_version=p.get("version_id")) or {}
    if not info:
        return "대상 버전이 없습니다(삭제됨)"
    paths = (info.get("image_paths") or [])[:6]
    if not paths:
        return "사진이 없습니다"
    cat = info.get("category")
    progress(f"{info.get('name')}: 사진 {len(paths)}장 받는 중")
    blocks = []
    for i, path in enumerate(paths, 1):
        tmp = os.path.join(HOME, "work", f"desc_{uuid.uuid4().hex[:6]}.img")
        try:
            fetch_to(storage_signed_url("bible", path, 3600), tmp)
            blocks += photo_block_large(tmp, f"[사진 {i}]")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    focus = {
        "character": "인물: 추정 나이대, 국적·인종 인상, 성별, 얼굴형, 이마·눈썹·눈(모양·쌍꺼풀·눈꼬리)·코·입술·턱선, 피부 톤과 질감, 점·흉터 같은 특징, "
                     "머리 길이·모양·가르마·앞머리·색·질감, 키 인상·체형·어깨·자세, 표정 습관과 분위기, 사진 속 의상(종류·색·재질·핏)·액세서리·메이크업",
        "location": "장소: 실내/실외, 공간의 크기와 구조, 바닥·벽·천장 재질과 색, 가구·소품 배치, 창·문 위치, 빛의 방향·색온도·시간대, 분위기, 눈에 띄는 고유한 물건",
        "background": "배경: 무엇이 보이는 풍경인가(도시·자연·하늘·무대 등), 거리감과 원근, 주요 형태와 색, 날씨·시간대·빛의 방향과 색온도, 분위기, 눈에 띄는 고유한 요소",
        "prop": "소품: 종류, 모양과 비율, 크기(손·몸 대비), 재질과 표면 질감, 색(주색·보조색), 로고·글자·무늬(실제 상표 이름은 쓰지 말 것), 낡음·흠집, 쓰는 방식",
    }.get(cat, "사진 속 대상의 생김새를 빠짐없이")
    system = f"""너는 드라마 제작팀의 코디네이터다. 대표가 올린 기준 사진을 보고 시리즈 바이블의 고정 묘사와 영어 프롬프트를 쓴다.
이 프롬프트는 영상·사진 생성 모델에 그대로 들어가서, 모든 화에서 같은 {'얼굴' if cat == 'character' else '모습'}이 나오게 하는 기준이 된다.
다룰 것 — {focus}. 사진에 실제로 보이는 것만, 세세하고 꼼꼼하게. 여러 장이면 공통으로 일관된 특징을 쓰고 장마다 다른 것(의상·조명 등)은 details로.
실존 인물·배우·브랜드 이름, 사진 속 글자(이름표·자막·간판 글자)는 쓰지 않는다. 추측은 쓰지 않는다.
- description: 한국어. 대표가 읽고 확인할 묘사, 4~8문장. 위 항목을 빠짐없이.
- prompt_en: 영어. description과 같은 내용을 생성 모델용으로 더 구체적으로(약 80~160단어, 쉼표로 이어진 묘사 위주, 현재형). 한글 금지.
- details: 한국어. 바뀌면 안 되는 특징(얼굴·머리·체형 등), 사진별로 다른 점(의상·조명), 만들 때 주의할 점·금지 사항을 줄 단위로.
답 형식: <json>{{"description": "", "prompt_en": "", "details": ""}}</json>"""
    user = blocks + [{"type": "text", "text":
        f"시리즈: {info.get('series') or ''}\n항목: {info.get('name')} ({cat})\n대본 속 역할: {info.get('summary') or '-'}\n"
        f"지금 적혀 있는 묘사(참고만, 사진과 다르면 사진이 우선): {info.get('description') or '-'}"}]
    progress(f"{info.get('name')}: 코디네이터가 사진 {len(paths)}장을 보고 묘사·영어 프롬프트를 쓰는 중")
    text, _ = claude("claude-opus-5-5", system, user, max_tokens=4000, stage="other", note=f"코디네이터: 사진 보고 묘사·프롬프트: {info.get('name')}")
    d = safe_json(text)
    desc = (d.get("description") or "").strip()
    en = clean_en(json.dumps({"prompt_en": d.get("prompt_en") or ""}))
    if not desc:
        raise RuntimeError(f"코디네이터가 사진을 보고 묘사를 쓰지 못했습니다(답 형식 오류). 다시 시도하면 대부분 해결됩니다. 답 앞부분: {text.strip()[:120]!r}")
    rpc("agent_bible_set_description", p_token=TOKEN, p_version=info["version_id"], p_description=desc, p_details=(d.get("details") or "").strip(),
        p_reason="코디네이터가 사진을 보고 묘사·영어 프롬프트 작성")
    if en:   # 사진에서 바로 쓴 영어 프롬프트(작가실 번역보다 자세함)
        rpc("agent_bible_set_prompt", p_token=TOKEN, p_version=info["version_id"], p_prompt=en)
    rpc("agent_add_task", p_token=TOKEN, p={"kind": "coord_review", "payload": {"entry_id": info["entry_id"], "version_id": info["version_id"],
                                                                                "series_id": info.get("series_id")}})
    notify(f"{info.get('name')} 사진 보고 묘사·프롬프트 작성 완료", "코디네이터가 이어서 감수합니다. 바이블 카드의 '감수·반영 기록'에서 확인하세요")
    return f"{info.get('name')} 묘사 {len(desc)}자 · 영어 프롬프트 {len(en)}자"


@task("upscale")
def t_upscale(t):
    """편집 OK 때 '1080p로 업그레이드' → 완성본을 1080p 배포용으로 키운다(집 PC, 추가 비용 없음).
    Real-ESRGAN(realesrgan-ncnn-vulkan)이 이 컴퓨터에 있으면 AI 업스케일, 없으면 ffmpeg 고품질 확대(lanczos + 약한 선명도)."""
    p = t.get("payload") or {}
    rel = rpc("agent_get_release", p_token=TOKEN, p_id=p.get("release_id")) or {}
    rd = rel.get("render") or {}
    files = rd.get("files") or {}
    folder = files.get("folder")
    if not folder or not os.path.isdir(folder):
        raise RuntimeError(f"완성본 폴더가 이 컴퓨터에 없습니다: {folder or '(없음)'} — 완성본을 만든 컴퓨터에서 해야 합니다")
    src = next((os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.endswith("_완성.mp4")), None)
    if not src:
        raise RuntimeError("완성 영상(_완성.mp4)을 찾지 못했습니다")
    rpc("agent_update_release", p_token=TOKEN, p_id=rel["id"], p={"upscale_status": "running"})
    try:
        info = subprocess.run([ffmpeg(), "-hide_banner", "-i", src], capture_output=True, text=True, timeout=60).stderr
        m = re.search(r"Video:.*?, (\d{3,5})x(\d{3,5})", info)
        w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        if not w:
            raise RuntimeError("영상 크기를 읽지 못했습니다")
        k = 1080 / min(w, h)
        W, H = int(round(w * k / 2) * 2), int(round(h * k / 2) * 2)
        if abs(w / h - 9 / 16) < 0.02:
            W, H = 1080, 1920
        elif abs(w / h - 16 / 9) < 0.02:
            W, H = 1920, 1080
        out = src.replace("_완성.mp4", "_완성_1080p.mp4")
        esr = shutil.which("realesrgan-ncnn-vulkan") or next((x for x in glob.glob(os.path.join(HOME, "tools", "realesrgan*", "realesrgan-ncnn-vulkan*")) if os.access(x, os.X_OK)), None)
        method = "ffmpeg 고품질 확대"
        if esr:
            try:
                work = os.path.join(HOME, "work", f"esr_{uuid.uuid4().hex[:6]}")
                fin, fout = os.path.join(work, "in"), os.path.join(work, "out")
                os.makedirs(fin), os.makedirs(fout)
                progress(f"{rel['episode']['number']}화 AI 업스케일: 장면 나누는 중")
                ff_long("-i", src, "-qscale:v", "1", os.path.join(fin, "f%06d.jpg"))
                progress(f"{rel['episode']['number']}화 AI 업스케일 중(Real-ESRGAN, 수십 분 걸릴 수 있음)")
                # 실사 드라마라 실사용 모델(realesrgan-x4plus). 애니 모델(animevideov3)은 피부·머리카락을 그림처럼 뭉갰다. 멈춤 방지 90분 제한
                subprocess.run([esr, "-i", fin, "-o", fout, "-n", "realesrgan-x4plus", "-s", "4", "-f", "jpg"], check=True, capture_output=True, timeout=90 * 60)
                fps = re.search(r"(\d+(?:\.\d+)?) fps", info)
                ff_long("-framerate", fps.group(1) if fps else "24", "-i", os.path.join(fout, "f%06d.jpg"), "-i", src, "-map", "0:v", "-map", "1:a?",
                   "-vf", f"scale={W}:{H}:flags=lanczos", "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                   "-c:a", "copy", "-movflags", "+faststart", out)
                method = "AI 업스케일(Real-ESRGAN)"
            except Exception as ex:
                log(f"AI 업스케일 실패, ffmpeg 확대로: {ex}")
                esr = None
            finally:   # 실패해도 수 GB 장면 사진을 남기지 않는다
                shutil.rmtree(work, ignore_errors=True)
        if not esr:
            progress(f"{rel['episode']['number']}화 1080p로 키우는 중(ffmpeg)")
            ff_long("-i", src, "-vf", f"scale={W}:{H}:flags=lanczos,unsharp=5:5:0.5:5:5:0.0", "-c:v", "libx264", "-preset", "slow", "-crf", "16",
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out)
        ff("-i", out, "-an", "-c:v", "copy", out.replace("_완성_1080p.mp4", "_영상만(무음)_1080p.mp4"))
    except Exception as ex:
        rpc("agent_update_release", p_token=TOKEN, p_id=rel["id"], p={"upscale_status": "failed", "upscale_files": {"error": str(ex)[:300]}})
        raise
    rpc("agent_update_release", p_token=TOKEN, p_id=rel["id"], p={"upscale_status": "done", "resolution": "1080p",
                                                                  "upscale_files": {"path": out, "folder": folder, "method": method, "size": f"{W}x{H}", "platform": PLATFORM}})
    notify(f"{rel['episode']['number']}화 1080p 배포본 완성", f"{method} · {W}x{H}\n{out}")
    return f"{W}x{H} · {method}"


@task("bgm")
def t_bgm(t):
    """편집실 '이 화 음악 톤' → ElevenLabs Music으로 가사 없는 배경음악을 만들어 저장소에 올린다(편집실에서 타임라인에 넣기)."""
    p = t.get("payload") or {}
    ep = t.get("episode_id")
    prompt = (p.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("음악 톤(프롬프트)이 비어 있습니다")
    secs = int(p.get("seconds") or 60)
    tool = p.get("tool") or "elevenlabs"
    if tool != "elevenlabs":
        raise RuntimeError(f"배경음악 도구 '{tool}'는 아직 자동 생성을 지원하지 않습니다(설정 → 제작 → 소리 도구)")
    progress(f"ElevenLabs가 배경음악 {secs}초를 만드는 중")
    data = el_music(prompt + " Instrumental only, no vocals, cinematic drama underscore that sits under dialogue.", secs, p.get("model") or "music_v1")
    local = os.path.join(HOME, "work", f"bgm_{uuid.uuid4().hex[:8]}.mp3")
    with open(local, "wb") as f:
        f.write(data)
    path = storage_upload(f"music/{p.get('series_id') or 'series'}/el_{uuid.uuid4().hex[:8]}.mp3", local, "audio/mpeg")
    os.remove(local)
    log_cost("audio", "elevenlabs", "music", {"seconds": secs}, episode_id=ep, note=f"배경음악: {p.get('name') or ''}", outcome="success")
    notify("배경음악 도착", f"{p.get('name') or '이 화'} · {secs}초. 편집실 '이 화 음악 톤'에서 들어 보고 타임라인에 넣으세요")
    return json.dumps({"path": path, "seconds": secs, "name": p.get("name") or "ElevenLabs 음악"}, ensure_ascii=False)


@task("bible_suggest")
def t_bible_suggest(t):
    """작가실: 대본을 읽고 스타일·톤(전 회차 공통 화면 규칙) 또는 화별 배경음악 톤 추천안을 바이블 수정안으로 올린다."""
    p = t.get("payload") or {}
    sid = t.get("series_id") or p.get("series_id")
    what = p.get("what") or "style"
    ov = rpc("agent_bible_overview", p_token=TOKEN, p_series=sid) or {}
    title = (ov.get("series") or {}).get("title", "")
    eps = [e for e in ov.get("episodes") or [] if (e.get("text") or "").strip()]
    if not eps:
        return "읽을 대본이 없습니다"
    budget = max(6000, 200000 // max(1, len(eps)))
    scripts = "\n\n".join(f"[{x['number']}화 {x.get('title') or ''}]\n{(x.get('text') or '')[:budget]}" for x in eps)
    if what == "music":
        system = """너는 드라마 작가실의 음악 감독이다. 대본을 읽고 화별로 어울리는 배경음악 톤을 추천한다(가사 없는 연주곡, 대사 밑에 까는 음악).
- 시리즈 공통 테마 1개(name: "메인 테마", eps: []) + 화마다 1개(name: "N화 — 한 줄 요약", eps: [N]). 감정이 크게 다른 구간이 있으면 화당 2개까지.
- description: 한국어. 장르·악기·템포·분위기·세기 변화를 구체적으로(예: "저음 피아노 단음으로 시작, 느린 템포, 후반 현악이 서서히 올라오며 긴장").
- details: 한국어. 어느 씬에서 어떻게 쓰면 좋은지, 피할 것(가사, 과한 드럼 등).
답 형식: <json>{"items": [{"name": "", "description": "", "details": "", "eps": [1]}]}</json>"""
    else:
        system = """너는 드라마 작가실의 미술·촬영 감독이다. 대본을 읽고 모든 화에 공통으로 적용할 화면 스타일·톤을 추천한다.
- 1~2개 항목. 첫 항목 name: "전 회차 공통 화면 스타일". 회상·미래처럼 톤이 확 다른 시점이 대본에 있으면 두 번째 항목(예: "회상 장면 톤").
- description: 한국어. 색감·대비·조명·렌즈·심도·카메라 움직임·질감·화면 비율 느낌을 구체적으로(실사 드라마, 세로 숏폼 기준).
- details: 한국어. 금지 사항(화면 글자·워터마크·과한 필터 등)과 주의점.
답 형식: <json>{"items": [{"name": "", "description": "", "details": ""}]}</json>"""
    hint = (p.get("hint") or "").strip()
    if hint:
        system += """
[대표 제안 — 가장 먼저 반영]
대표가 원하는 방향을 적었다. 대본과 맞지 않는 부분이 있어도 대표 제안을 우선하고, 제안이 비어 있는 부분만 대본을 읽고 채운다.
제안에 나온 표현(악기·색·분위기·금지 사항)은 빠뜨리지 말고 description·details에 구체적으로 풀어 쓴다."""
    known = [e.get("name") for e in ov.get("entries") or [] if e.get("category") == ("music" if what == "music" else "style")]
    if p.get("redo") and known:
        system += f"\n이미 있는 항목 이름: {', '.join(known)}. 같은 이름을 쓰면 새 수정안으로 올라간다(대표가 비교해 고른다)."
    progress(f"작가실이 「{title}」 대본을 읽고 {'화별 배경음악 톤' if what == 'music' else '스타일·톤'}을 추천하는 중" + (" (대표 제안 반영)" if hint else ""))
    text, _ = claude("claude-opus-5-5", system, f"시리즈: {title}\n\n[대표 제안]\n{hint or '(없음 — 대본을 읽고 추천)'}\n\n[대본]\n{scripts}", max_tokens=6000, stage="other",
                     note=f"작가실 추천: {'배경음악' if what == 'music' else '스타일·톤'}")
    items = []
    for it in pick_json(text).get("items") or []:
        if not (it.get("name") and it.get("description")):
            continue
        items.append({"category": "music" if what == "music" else "style", "name": it["name"][:80], "summary": it["description"][:200],
                      "description": it["description"], "details": it.get("details") or "",
                      "appears": [{"ep": int(n), "scene": None} for n in (it.get("eps") or []) if str(n).isdigit()],
                      "reason": "작가실 추천(대표 제안 반영)" if hint else "작가실 추천(대본을 읽고)", "force_proposal": bool(p.get("redo"))})
    if not items:
        raise RuntimeError("추천안을 만들지 못했습니다")
    r = rpc("agent_bible_upsert", p_token=TOKEN, p_series=sid, p_items=items) or {}
    notify(f"작가실 {'배경음악' if what == 'music' else '스타일·톤'} 추천 {len(items)}건", "바이블 탭(또는 지시 탭 적용 대기함)에서 고쳐서 적용해 주세요")
    return f"추천 {len(items)}건 · 새 수정안 {r.get('proposals', 0)}건"


def _num_in(d, keys):
    """응답 JSON 어디에 있든 잔액처럼 보이는 숫자를 찾는다."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k.lower() in keys and isinstance(v, (int, float, str)) and not isinstance(v, bool):
                try:
                    return k, float(v)
                except ValueError:
                    pass
        for v in d.values():
            r = _num_in(v, keys)
            if r:
                return r
    return None


@task("balance_check")
def t_balance_check(t):
    """비용 탭: 서비스별 남은 잔액을 API로 조회(비용 없음). 결과는 JSON으로 남겨 화면이 읽는다."""
    out = {"at": datetime.now().isoformat(timespec="seconds")}
    try:   # ElevenLabs: 구독의 남은 글자 수
        s = el_json("/v1/user/subscription", method="GET", timeout=20) or {}
        used, lim = int(s.get("character_count") or 0), int(s.get("character_limit") or 0)
        out["elevenlabs"] = {"ok": True, "tier": s.get("tier"), "used": used, "limit": lim, "left": max(0, lim - used),
                             "reset": s.get("next_character_count_reset_unix"), "status": s.get("status")}
    except Exception as ex:
        out["elevenlabs"] = {"ok": False, "error": str(ex)[:200]}
    try:   # Higgsfield: 공개 문서에 잔액 조회가 없어, 있을 법한 주소를 조회만 해 본다(없으면 '사이트에서 확인')
        h = {**hf_auth_headers(), "Accept": "application/json"}
        found = None
        for base in HF_BASES:
            for path in ("/v1/balance", "/balance", "/v1/account/balance", "/account/balance", "/v1/me", "/me", "/v1/account", "/account"):
                try:
                    code, data = http_json(base + path, h, timeout=15)
                except Exception:
                    continue
                if code == 200 and isinstance(data, dict):
                    r = _num_in(data, {"balance", "usd", "balance_usd", "credits", "credit", "remaining", "amount"})
                    if r:
                        found = {"ok": True, "field": r[0], "value": r[1], "unit": "크레딧" if "credit" in r[0].lower() else "USD", "from": base + path}
                        break
            if found:
                break
        out["higgsfield"] = found or {"ok": False, "error": "Higgsfield API가 잔액 조회를 제공하지 않습니다(사이트에서 확인)"}
    except Exception as ex:
        out["higgsfield"] = {"ok": False, "error": str(ex)[:200]}
    out["anthropic"] = {"ok": False, "error": "Anthropic은 잔액 조회 API를 제공하지 않습니다(console.anthropic.com → Billing)"}
    return json.dumps(out, ensure_ascii=False)


@task("api_check")
def t_api_check(t):
    """설정 → 실제 연결 점검: 진짜 호출을 아주 작게 한 번씩(합계 약 100~300원) 해서 경로·권한·응답 형식을 확인한다."""
    p = t.get("payload") or {}
    out = []
    def step(name, fn):
        progress(f"점검 중: {name}")
        try:
            out.append(f"OK  {name}: {fn()}")
        except Exception as ex:
            out.append(f"실패 {name}: {str(ex)[:200]}")
    def claude_ping():
        text, _ = claude("claude-opus-5-5", "한 단어로만 답한다.", "연결 확인. '확인'이라고만 답해.", max_tokens=20, stage="other", note="실제 연결 점검")
        return text.strip()[:20]
    step("Claude", claude_ping)
    def nano():
        try:
            os.remove(os.path.join(HOME, "hf_models.json"))
        except OSError:
            pass
        NANO_STATE.update(ok=None, at=0.0)
        m = resolve_image_model({})
        return f"{model_label(m)} ({m})" + ("" if m == NANO_PRO else f" — 나노바나나 프로는 지금 쓸 수 없음: {NANO_STATE['why']}")
    step("장면 사진 모델 경로", nano)
    def soul():
        u = soul_images("A Korean woman in her 20s, plain grey background, natural light. " + PHOTO_LOOK, n=1, aspect="3:4")[0]
        path = upload_from_url(u, f"api_check/{uuid.uuid4().hex[:8]}.jpg")
        return f"사진 1장 받음 → {path}"
    step("Soul 2 실사 인물 사진", soul)
    def scene():
        m = resolve_image_model({})
        st = hf_submit_wait(m, {"prompt": "A small cozy cafe interior, natural light, photorealistic, no people, no text.", "aspect_ratio": "9:16"}, "점검 사진")
        urls = find_image_urls(st.get("images") or st)
        if not urls:
            raise RuntimeError(f"사진 주소 없음: {str(st)[:150]}")
        return f"{m} 사진 1장 받음"
    step("장면 사진 생성", scene)
    vid = p.get("voice_id")
    if vid:
        mp3 = os.path.join(HOME, "work", "check_tts.mp3")
        def tts():
            data = tts_line(vid, "연결 확인입니다. 잘 들리나요?", "[excited]", "eleven_v3")
            with open(mp3, "wb") as f:
                f.write(data)
            return f"{len(data) // 1024}KB 음성 받음"
        step("ElevenLabs 대사 읽기(TTS)", tts)
        def stt():
            text, words = stt_words(mp3, "scribe_v1")
            return f"받아쓰기: {text[:40]}"
        if os.path.exists(mp3):
            step("ElevenLabs 받아쓰기(STT)", stt)
    else:
        out.append("건너뜀 ElevenLabs: 바이블에 목소리가 정해진 인물이 없어 대사 읽기·받아쓰기는 확인하지 못했습니다")
    ok = sum(1 for x in out if x.startswith("OK"))
    report = "\n".join(out)
    notify(f"실제 연결 점검 {ok}/{len(out)} 통과", report[:900], "info" if ok == len(out) else "warning")
    return report


PROMPT_NEEDS = {
    "character": "나이대, 성별, 국적 인상, 얼굴형, 눈·코·입, 피부톤, 머리(길이·모양·색), 체형·키 인상, 대표 의상",
    "location": "공간 종류와 크기, 구조(창·문·가구 배치), 바닥·벽 재질과 색, 시간대, 빛(자연광·조명 색), 분위기",
    "background": "풍경 종류, 거리감, 날씨, 시간대, 빛과 색감",
    "prop": "물건 종류, 모양, 크기, 재질, 색, 무늬, 낡은 정도",
    "style": "전체 색감, 조명, 카메라 느낌(렌즈·움직임), 화면 질감",
    "music": "장르, 악기, 템포, 분위기, 세기 변화",
}
PROMPT_EXAMPLE = {
    "character": "예: 20대 초반 한국 여성, 갸름한 얼굴, 쌍꺼풀 없는 큰 눈, 맑은 피부, 어깨까지 오는 검은 생머리, 마른 체형에 키 160cm 정도, 평소 흰 셔츠에 청바지",
    "location": "예: 10평 남짓한 연습실, 한쪽 벽 전체가 거울, 회색 나무 바닥, 밤, 천장 형광등 하나만 켜져 약간 푸른빛, 조용하고 쓸쓸한 분위기",
    "background": "예: 창밖으로 보이는 서울 야경, 멀리 남산타워, 맑은 밤, 주황빛 가로등",
    "prop": "예: 손바닥만 한 낡은 은색 목걸이, 둥근 펜던트 안에 작은 사진, 체인이 군데군데 검게 변색",
    "style": "예: 차갑고 푸른 색감, 낮은 대비, 손에 든 카메라처럼 살짝 흔들림, 필름 같은 거친 질감",
    "music": "예: 저음 피아노 단음으로 시작, 느린 템포, 후반에 현악이 서서히 올라오며 긴장",
}


def safe_json(text):
    try:
        d = pick_json(text)
        return d if isinstance(d, dict) else {}
    except Exception:   # 답에 JSON이 없거나 깨짐: 빈 것으로 보고 다음 단계로
        return {}


def clean_en(text):
    en = (safe_json(text).get("prompt_en") or "").strip()
    if not en:   # JSON이 깨졌으면 "prompt_en": "…" 부분이나 본문을 그대로 쓴다
        m = re.search(r'"prompt_en"\s*:\s*"(.+?)"\s*[,}]', text, re.S)
        en = (m.group(1) if m else re.sub(r"</?json>|```\w*", "", text)).strip()
    # 이름 등 한글이 섞여 있으면 괄호째 지우고, 남은 한글 단어도 뺀다(영어 문장만 남김)
    en = re.sub(r"\s*[\(\[][^\)\]]*[가-힣][^\)\]]*[\)\]]", "", en)
    en = re.sub(r"[가-힣]+", "", en)
    en = re.sub(r"\s{2,}", " ", en).strip(" ,.;")
    return en + "." if len(en) >= 30 and len(re.findall(r"[A-Za-z]{2,}", en)) >= 6 else ""


def script_mentions(sid, name, summary, limit=12000):
    """대본 전체에서 그 항목 이름(괄호 앞 부분)이 나오는 줄과 앞뒤 줄을 모은다."""
    ov = rpc("agent_bible_overview", p_token=TOKEN, p_series=sid) or {}
    keys = [k for k in {re.sub(r"\s*\(.*?\)", "", name or "").strip(), (name or "").strip()} if len(k) >= 1]
    out = []
    for e in ov.get("episodes") or []:
        lines = (e.get("text") or "").splitlines()
        hit = set()
        for i, ln in enumerate(lines):
            if any(k in ln for k in keys):
                hit.update(range(max(0, i - 2), min(len(lines), i + 3)))
        if hit:
            out.append(f"[{e.get('number')}화]\n" + "\n".join(lines[i] for i in sorted(hit)))
    return "\n\n".join(out)[:limit]


@task("bible_prompt")
def t_bible_prompt(t):
    """작가실: 한국어로 적힌 바이블 묘사·세부 사항을 영상·사진 모델용 영어 고정 프롬프트로 옮겨 둔다.
    1) 묘사로 옮기기 → 2) 실패하면 한 번 더(더 엄격하게) →
    3) 그래도 안 되면: AI가 쓴 묘사면 대본에서 그 항목이 나오는 부분을 읽고 채움 / 대표가 쓴 묘사면 무엇이 빠졌는지 한글로 알려 줌."""
    p = t.get("payload") or {}
    info = rpc("agent_bible_photo_info", p_token=TOKEN, p_version=p.get("version_id")) or {}
    if not info:
        return "대상 버전이 없습니다(삭제됨)"
    if info.get("status") not in ("active", "proposed"):
        return "이미 다른 버전으로 바뀌었습니다"
    cat, name = info.get("category"), info.get("name")
    if (info.get("description") or "").startswith("(작가실이 사진을 보고 쓰는 중)"):
        return f"{name}: 코디네이터가 사진을 보고 쓰는 중이라 건너뜀"
    if info.get("prompt_en") and not p.get("apply_reason") and not p.get("rerun"):
        return f"{name}: 이미 영어 프롬프트가 있어 건너뜀(코디네이터가 사진을 보고 작성)"
    reason = (info.get("change_reason") or "").strip()
    by_owner = info.get("proposed_by") == "대표" and reason and "작가실" not in reason and "사진을 보고" not in reason
    reason = re.sub(r"\s*→ (작가실이|코디네이터가) 묘사·프롬프트에 반영:.*$", "", reason)   # 다시 반영할 때 이전 반영 기록은 빼고 요청만
    if p.get("apply_reason") and reason:   # 대표가 '바꾸는 이유'에 적은 요청을 한국어 묘사에 먼저 반영
        progress(f"{name}: 대표 요청('{reason[:40]}')을 묘사에 반영하는 중")
        sysm = """너는 드라마 제작팀의 코디네이터다. 대표가 바이블 항목을 고치며 남긴 요청을 지금 묘사·세부 사항에 반드시 반영해 고쳐 쓴다.
- 대표 요청이 최우선이다. 요청과 다른 기존 표현(국적 인상·나이 인상·얼굴·머리·체형·의상 등)은 요청대로 바꾼다.
  예: 요청 "어려 보이는 한국 사람 얼굴" + 기존 "Thai-Korean woman in her early twenties" → "앳되어 보이는 한국인 얼굴의 20대 초반 여성"으로 바꾸고 태국계 인상 표현은 뺀다.
- 요청과 관계없는 나머지 내용은 그대로 둔다(빠뜨리지 않는다).
- 한국어로 쓴다(기존이 영어여도 한국어로 옮겨 쓴다). 이름·실존 인물·브랜드는 넣지 않는다.
답 형식: <json>{"description": "고친 묘사 전체", "details": "고친 세부 사항 전체(없으면 빈 문자열)", "changed": "무엇을 어떻게 바꿨는지 한 줄"}</json>"""
        ask = f"분류: {cat}\n이름: {name}\n[대표 요청]\n{reason}\n\n[지금 묘사]\n{info.get('description') or '-'}\n[지금 세부 사항]\n{info.get('details') or '-'}"
        text, _ = claude("claude-opus-5-5", sysm, ask, max_tokens=3000, stage="other", note=f"바이블 묘사에 대표 요청 반영: {name}")
        d = safe_json(text)
        nd = (d.get("description") or "").strip()
        if nd and nd.strip() == (info.get("description") or "").strip():   # 그대로면 한 번 더, 더 분명하게
            text, _ = claude("claude-opus-5-5", sysm, ask + "\n\n[중요] 지난번 답이 묘사를 바꾸지 않았다. 대표 요청을 묘사에 분명히 넣어라.",
                             max_tokens=3000, stage="other", note=f"바이블 묘사에 대표 요청 반영(다시): {name}")
            d = safe_json(text)
            nd = (d.get("description") or "").strip()
        if len(nd) < 10:
            raise RuntimeError(f"대표님이 '바꾸는 이유'에 적은 요청('{reason[:60]}')을 '{name}' 묘사에 반영하지 못했습니다. "
                               f"바이블 카드 → 내용 고치기에서 묘사 칸에 바뀐 내용을 직접 적어 주세요(예: '눈매가 더 날카롭게' → 묘사에 '눈꼬리가 올라간 날카로운 눈매').")
        info["description"], info["details"] = nd, (d.get("details") or "").strip() or info.get("details")
        rpc("agent_bible_set_description", p_token=TOKEN, p_version=info["version_id"], p_description=nd, p_details=info.get("details") or "",
            p_reason=f"{reason} → 코디네이터가 묘사·프롬프트에 반영: {str(d.get('changed') or '')[:200]}")
        applied = str(d.get("changed") or "요청 반영")[:120]
    rules = f"""- 눈에 보이는 고정 특징만. 이 분류({cat})에서 담을 것: {PROMPT_NEEDS.get(cat, '눈에 보이는 특징')}.
- 세부 사항에 '바뀌면 안 되는 특징'이 있으면 반드시 포함한다. 화마다 바뀌는 상황·줄거리·성격·대사는 넣지 않는다.
- 4~8문장, 현재형, 쉼표로 이어진 묘사 위주. 이름·실존 인물·배우·브랜드는 쓰지 않는다. 한글은 한 글자도 쓰지 않는다.
- 실존 인물(배우·가수 등)을 닮았다는 표현은 이름을 빼고 그 인상을 이목구비 특징으로 바꿔 쓴다. 신체 부위를 성적으로 강조하는 표현(가슴골·노출 등)은 생성 서비스가 거절하므로 '균형 잡힌 글래머러스한 체형'처럼 전체 체형 인상으로 완곡하게 쓴다."""
    system = f"""너는 드라마 작가실의 프롬프트 담당이다. 시리즈 바이블 항목의 묘사(한국어가 섞여 있음)를 영상·사진 생성 모델에 그대로 넣을 영어 고정 프롬프트로 옮긴다.
{rules}
- 한국어 원문의 내용을 하나도 빠뜨리지 말고 꼼꼼하게 옮긴다(색·재질·수치·금지 사항까지). 원문에 없는 것을 지어내지 않는다.
- 원문이 너무 짧거나 모호해 위 특징을 거의 알 수 없으면 prompt_en을 비우고, missing에 빠진 것을 한국어로 구체적으로 적는다.
답 형식: <json>{{"prompt_en": "", "missing": ["빠진 것(한국어)"]}}</json>"""
    user = (f"분류: {cat}\n이름(참고, 프롬프트에 쓰지 말 것): {name}\n대본 속 역할: {info.get('summary') or '-'}\n"
            f"[묘사]\n{info.get('description') or '-'}\n[세부 사항]\n{info.get('details') or '-'}"
            + (f"\n[대표가 이번에 바꾸며 남긴 요청 — 묘사와 함께 반드시 반영]\n{reason}" if by_owner or p.get("apply_reason") else ""))
    progress(f"{name}: 코디네이터가 영어 프롬프트로 옮기는 중")
    text, _ = claude("claude-opus-5-5", system, user, max_tokens=3000, stage="other", note=f"바이블 영어 프롬프트: {name}")
    en, missing = clean_en(text), safe_json(text).get("missing") or []
    if not en:   # 2) 한 번 더: 형식을 더 단순하게
        progress(f"{name}: 한 번 더 시도하는 중")
        text, _ = claude("claude-opus-5-5", system, user + "\n\n[중요] 지난번 답이 형식에 맞지 않았다. 설명 없이 <json> 하나만, 영어 문장으로.",
                         max_tokens=3000, stage="other", note=f"바이블 영어 프롬프트(다시): {name}")
        en = clean_en(text)
        missing = safe_json(text).get("missing") or missing
    how = "묘사로 옮김"
    owner_wrote = (info.get("proposed_by") == "대표") and "자동" not in reason and "묘사·프롬프트에 반영" not in (info.get("change_reason") or "")
    if not en and not owner_wrote:   # 3-a) AI가 쓴 묘사 → 대본에서 채운다
        sid = info.get("series_id") or t.get("series_id")
        found = script_mentions(sid, name, info.get("summary")) if sid else ""
        progress(f"{name}: 묘사가 부족해 대본에서 '{name}'이(가) 나오는 부분을 읽고 채우는 중")
        text, _ = claude("claude-opus-5-5", system.replace("원문에 없는 것을 지어내지 않는다.",
                         "묘사가 부족하므로 아래 대본 발췌에서 드러나는 특징으로 채우고, 대본에도 없으면 이 드라마의 역할·분위기에 맞게 자연스럽게 정해 채운다(비우지 않는다)."),
                         user + f"\n\n[대본에서 '{name}'이(가) 나오는 부분]\n{found or '(대본에서 이름을 찾지 못함)'}",
                         max_tokens=3000, stage="other", note=f"바이블 영어 프롬프트(대본에서 채움): {name}")
        en = clean_en(text)
        how = "묘사가 부족해 대본에서 채움" if en else how
    if not en:
        need = PROMPT_NEEDS.get(cat, "눈에 보이는 특징")
        miss = ", ".join(str(m) for m in missing[:6]) if missing else need
        if owner_wrote:
            raise RuntimeError(f"대표님이 쓴 묘사로는 영어 프롬프트를 만들지 못했습니다(두 번 시도). 묘사가 짧거나 모호합니다. "
                               f"빠진 것: {miss}. 바이블 '{name}' 카드 → 내용 고치기에서 한글로 자세히 적어 주세요(고치면 자동으로 다시 돕니다). "
                               f"{PROMPT_EXAMPLE.get(cat, '')}")
        raise RuntimeError(f"묘사와 대본 모두에서 '{name}'의 영어 프롬프트를 만들지 못했습니다(세 번 시도). "
                           f"바이블 '{name}' 카드에서 한글 묘사를 자세히 적어 주세요(담을 것: {need}). {PROMPT_EXAMPLE.get(cat, '')}")
    rpc("agent_bible_set_prompt", p_token=TOKEN, p_version=info["version_id"], p_prompt=en)
    return f"{name} 영어 프롬프트 {len(en)}자 ({how})" + (f" · 요청 반영: {applied}" if p.get("apply_reason") and reason else "")


def safe_prompt(prompt, name=""):
    """코디네이터: 생성 서비스가 거절할 표현(실존 인물 닮음·성적인 신체 강조)을 뜻은 살리고 완곡하게 고친 영어 프롬프트."""
    txt, _ = claude("claude-sonnet-5-5", "너는 드라마 제작팀의 코디네이터다. 사진 생성 서비스가 안전 규정으로 거절한 영어 프롬프트를 고친다. "
                    + "실존 인물(배우·가수 등)을 닮았다는 표현은 이름을 빼고 그 인상을 이목구비 특징으로 바꿔 쓴다. 신체 부위를 성적으로 강조하는 표현(가슴골·노출 등)은 생성 서비스가 거절하므로 '균형 잡힌 글래머러스한 체형'처럼 전체 체형 인상으로 완곡하게 쓴다." + " 그 밖의 외모 특징(나이 인상·얼굴·체형·머리)은 최대한 그대로 살린다. 답은 <json>{\"prompt_en\": \"\", \"changed\": \"무엇을 바꿨는지 한국어 한 줄\"}</json>",
                    prompt, max_tokens=1500, stage="other", note=f"코디네이터: 안전 규정용 프롬프트 다듬기 {name}")
    return clean_en(txt), str(safe_json(txt).get("changed") or "")[:200]


def design_check(did):
    """코디네이터: 만들어진 사진 후보(또는 기준 사진 세트)를 프롬프트와 한 장씩 비교한다.
    글자·이름표·사원증 같은 글씨, 다른 사람, 묘사와 다른 나이·성별·머리·옷, 실사가 아닌 것 → 장마다 표시."""
    d = rpc("agent_get_photo_design", p_token=TOKEN, p_id=did) or {}
    if not d:
        return "사진 디자인이 없습니다(삭제됨)"
    name, cat = d.get("entry_name"), d.get("category")
    items = (d.get("previews") or []) if d.get("status") == "ready" else (d.get("ref_set") or []) if d.get("status") == "set_ready" else []
    items = [x for x in items if x.get("path")][:6]
    if not items:
        return f"{name}: 확인할 사진이 없습니다(상태 {d.get('status')})"
    prompt = d.get("prompt_en") or d.get("prompt") or d.get("description") or ""
    blocks = []
    for i, x in enumerate(items, 1):
        tmp = os.path.join(HOME, "work", f"dc_{uuid.uuid4().hex[:6]}.img")
        try:
            fetch_to(storage_signed_url(x.get("bucket") or "takes", x["path"], 3600), tmp)
            blocks += photo_block_large(tmp, f"[사진 {i}]{' ' + x['label'] if x.get('label') else ''}")
        except Exception as ex:
            log(f"확인용 사진 받기 실패: {ex}")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    if not blocks:
        raise RuntimeError("확인할 사진을 받지 못했습니다(저장소 연결 문제). 다시 시도해 주세요")
    system = f"""너는 드라마 제작팀의 코디네이터다. 생성된 {'인물' if cat == 'character' else '바이블'} 사진 후보를 프롬프트와 한 장씩 비교한다.
장마다 확인: 1) {'인물은 얼굴(나이 인상·성별·국적 인상·이목구비)과 몸(체형·몸 비율·몸매)만 프롬프트와 맞는지 본다. 의상·액세서리·헤어스타일·배경·조명·포즈는 장면마다 바뀌므로 보지 않고 문제로 삼지 않는다' if cat == 'character' else '프롬프트의 구조·재질·색·크기와 맞는가'}
2) 사진 안에 글자가 찍혀 있지 않은가 — 이름표·사원증·명찰·자막·간판·워터마크·로고(예: '김도연' 같은 이름). 하나라도 있으면 문제
3) {'후보끼리 같은 사람으로 쓸 수 있는가(세트면 모두 같은 사람인가), ' if cat == 'character' else ''}실사 사진인가(그림·3D 아님)
match: "ok"(그대로 써도 됨) | "issue"(문제 있음). issues는 한국어로 짧게, 글자가 보이면 그 글자를 text에.
그리고 다음 생성이 더 나아지게: 문제의 원인이 프롬프트에 있으면(모호함·빠진 특징·글자를 부르는 표현: 사원증·명찰·유니폼 로고 등) prompt_improved에 고친 영어 프롬프트 전체를{'(인물은 얼굴·몸 묘사만 다듬고 의상·배경 지시는 새로 넣지 않는다)' if cat == 'character' else ''},
avoid에 생성 모델에 넣을 금지 사항(영어 짧은 구, 예: "no ID badge", "no lanyard", "not older than 25")을 준다.
문제가 바이블 묘사 자체와 다르게 나온 탓이 아니라 프롬프트가 부족한 탓이면 fix_bible_prompt를 true로.
답 형식: <json>{{"images": [{{"no": 1, "match": "ok", "issues": [], "text": ""}}], "best": [1], "summary": "한국어 한 줄(몇 장이 쓸 만한지)",
"prompt_improved": "", "avoid": [], "fix_bible_prompt": false, "improve_note": "무엇을 고쳤는지 한국어 한 줄"}}</json>"""
    progress(f"코디네이터가 {name} 사진 {len(items)}장을 프롬프트와 비교하는 중")
    text, _ = claude("claude-sonnet-5-5", system, [{"type": "text", "text": f"항목: {name} ({cat})\n[프롬프트]\n{prompt}"}] + blocks,
                     max_tokens=2500, stage="other", note=f"코디네이터 사진 확인: {name}")
    r = safe_json(text)
    imgs = {inum(x.get("no") or 0): x for x in (r.get("images") or []) if isinstance(x, dict)}
    if not imgs:
        raise RuntimeError(f"코디네이터 사진 확인 답이 형식에 맞지 않았습니다. 다시 시도하면 대부분 해결됩니다. 답 앞부분: {text.strip()[:120]!r}")
    out = []
    for i, x in enumerate(items, 1):
        v = imgs.get(i) or {}
        out.append({"path": x["path"], "ok": v.get("match") == "ok", "issues": [str(s)[:120] for s in (v.get("issues") or [])][:4],
                    "text": str(v.get("text") or "")[:60]})
    bad = sum(1 for x in out if not x["ok"])
    improved = clean_en(json.dumps({"prompt_en": r.get("prompt_improved") or ""})) if bad else ""
    avoid = [str(x)[:60] for x in (r.get("avoid") or []) if re.match(r"^[ -~]+$", str(x))][:8] if bad else []
    bible_fixed = False
    if improved and r.get("fix_bible_prompt") and d.get("cur_version_id"):   # 프롬프트가 부족했던 것이면 바이블 영어 프롬프트도 고친다(기록 남김)
        rpc("agent_bible_set_prompt", p_token=TOKEN, p_version=d["cur_version_id"], p_prompt=improved)
        rpc("agent_bible_review_save", p_token=TOKEN, p={"series_id": d.get("series_id"), "entry_id": d.get("entry_id"), "version_id": d["cur_version_id"],
            "verdict": "fixed", "checks": [{"item": "사진 후보 확인에서 프롬프트 부족", "status": "fixed", "where": "prompt", "note": str(r.get("improve_note") or "")[:200]}],
            "prompt_before": prompt[:2000], "prompt_after": improved, "summary": f"사진 후보 {bad}/{len(out)}장 문제 → 영어 프롬프트 보정"})
        bible_fixed = True
    rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"review": {"items": out, "best": r.get("best") or [], "summary": str(r.get("summary") or "")[:300],
                                                                         "prompt": prompt[:600], "checked_at": datetime.now().isoformat(timespec="seconds"),
                                                                         "stage": d.get("status"), "prompt_improved": improved or None, "avoid": avoid,
                                                                         "improve_note": str(r.get("improve_note") or "")[:200], "bible_fixed": bible_fixed}})
    # 쓸 만한 후보가 하나도 없으면: 대표에게 넘기지 않고 코디네이터가 고친 프롬프트로 한 번 다시 만든다(같은 사슬에서 1번만)
    redo = None
    if d.get("status") == "ready" and bad == len(out) and (improved or avoid):
        np = (improved or prompt).rstrip(". ") + (". Avoid: " + ", ".join(avoid) + "." if avoid else "")
        try:
            redo = rpc("agent_photo_redesign", p_token=TOKEN, p_from=did, p_prompt=np)
        except Exception as ex:
            log(f"자동 다시 만들기 실패: {ex}")
        if redo:
            notify(f"코디네이터: {name} 후보를 고친 프롬프트로 다시 만드는 중", str(r.get("improve_note") or r.get("summary") or "")[:200])
    auto_msg = ""
    if d.get("auto_apply") and not redo:   # 대표가 '추천하고 자동 반영'을 고른 사진: 추천 사진으로 다음 단계까지 이어서
        try:
            auto_msg = " · " + photo_auto_continue(d, out, r.get("best") or [])
        except Exception as ex:
            log(f"자동 반영 이어 하기 실패: {ex}")
            notify(f"{name} 사진 자동 반영을 끝내지 못했습니다", f"{str(ex)[:200]} — 바이블 탭에서 직접 골라 적용해 주세요", "warning")
    return f"{name} 사진 {len(out)}장 확인: 문제 {bad}장" + (" · 고친 프롬프트로 자동 다시 만드는 중" if redo else " · 다음 후보용 프롬프트 개선" if improved or avoid else "") + auto_msg


def bible_photo_upload(did, series_id, entry_id, item, i):
    """takes 저장소의 사진 → 바이블 저장소(그 항목 폴더)로 복사. 자동 반영으로 맡긴 디자인만 허가된다."""
    tmp = os.path.join(HOME, "work", f"bp_{uuid.uuid4().hex[:6]}.jpg")
    try:
        fetch_to(storage_signed_url(item.get("bucket") or "takes", item["path"], 900), tmp)
        path = f"{series_id}/{entry_id}/{int(time.time() * 1000)}-auto{i}.jpg"
        rpc("agent_bible_photo_grant", p_token=TOKEN, p_design=did, p_path=path)
        with open(tmp, "rb") as f:
            data = f.read()
        req = urllib.request.Request(f"{URL}/storage/v1/object/bible/{path}", data=data, method="POST",
                                     headers=sb_headers({"Content-Type": "image/jpeg", "x-upsert": "false", "cache-control": "3600"}))
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"바이블 저장소 올리기 실패 {e.code}: {e.read()[:200]!r}") from None
        return path
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def photo_auto_continue(d, items, best):
    """자동 반영: 후보 확인 뒤 → 추천 사진 고르기 → (인물) 얼굴 고정·기준 사진 → 기준 사진 확인 뒤 → 바이블 적용."""
    did, name = d["id"], d.get("entry_name")
    ok = [x for x in items if x.get("ok")]
    if d.get("status") == "ready":
        okp = {x["path"] for x in ok}
        prev = d.get("previews") or []
        pick = [prev[n - 1] for n in best if isinstance(n, int) and 0 < n <= len(prev) and prev[n - 1].get("path") in okp]
        pick += [x for x in prev if x.get("path") in okp and x not in pick]   # 추천 순서 먼저, 나머지 문제없는 사진으로 채움
        pick = [{"path": x["path"], "bucket": x.get("bucket") or "takes"} for x in pick][:4]
        if not pick:
            notify(f"{name}: 추천할 만한 사진이 없습니다", "코디네이터 확인에서 모든 후보에 문제가 있었습니다. 바이블 탭에서 '새로 만들기'를 눌러 주세요", "warning")
            return "추천할 사진 없음"
        if d.get("category") == "character":
            rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"chosen": pick, "status": "training"})
            rpc("agent_add_task", p_token=TOKEN, p={"kind": "photo_set", "payload": {"design_id": did}})
            return f"추천 사진 {len(pick)}장으로 얼굴 고정 시작(자동 반영)"
        paths = [bible_photo_upload(did, d["series_id"], d["entry_id"], x, i) for i, x in enumerate(pick, 1)]
        res = rpc("agent_photo_auto_apply", p_token=TOKEN, p_design=did, p_paths=paths, p_soul="")
        notify(f"{name} 사진을 바이블에 넣었습니다(자동 반영)", f"코디네이터 추천 {len(paths)}장")
        return f"추천 사진 {len(paths)}장 바이블 적용({res})"
    if d.get("status") == "set_ready":
        okp = {x["path"] for x in ok}
        lst = [x for x in (d.get("chosen") or []) + (d.get("ref_set") or []) if x.get("path") and (x["path"] in okp or x in (d.get("chosen") or []))]
        if not [x for x in (d.get("ref_set") or []) if x.get("path") in okp]:
            notify(f"{name}: 기준 사진에 모두 문제가 있어 자동 반영을 멈췄습니다", "바이블 탭에서 확인해 주세요", "warning")
            return "기준 사진 모두 문제 → 자동 반영 멈춤"
        paths = [bible_photo_upload(did, d["series_id"], d["entry_id"], x, i) for i, x in enumerate(lst, 1)]
        res = rpc("agent_photo_auto_apply", p_token=TOKEN, p_design=did, p_paths=paths, p_soul=d.get("soul_id") or "")
        notify(f"{name} 얼굴 고정·기준 사진을 바이블에 넣었습니다(자동 반영)", f"사진 {len(paths)}장")
        return f"기준 사진 {len(paths)}장 바이블 적용({res})"
    return ""


VIDEO_REVIEW_RULES = """[영상감수자 — 영상을 만들기 전에 컷 설계(프롬프트)를 본다. 영상 생성 모델에서 의도대로 나오게 하는 것이 책임이다]
1) 한 컷은 한 장면·한 장소·하나의 연속 동작인가(장면 전환·시간 점프가 섞이면 안 됨). 길이 4~15초에 맞는 동작량인가.
2) 인물·장소 묘사가 바이블 고정 문장(영어 프롬프트)과 같은가, reference_names가 실제로 나오는 인물·장소와 맞는가.
3) 표정 변화가 2~3초마다 구체적으로(눈·입·눈썹) 적혔는가, 카메라는 한 가지 움직임(고정·천천히 밀기 등)인가.
4) 앞 컷과 이어지는 컷(continue_prev)이면 자세·위치·의상·소품이 이어지는가.
5) 글자·자막·로고·간판 글자를 요구하지 않는가, 생성 서비스가 거절할 표현(노출·폭력 과장·실존 인물 이름)이 없는가.
6) 프롬프트가 영어로 명확한가(대사는 한국어 원문 그대로 따옴표)."""


def script_review(t):
    """대본감수자: 원고 → 작가실 정리본(→ 컷 설계)이 씬 순서대로 빠짐없이 옮겨졌는지. 결과는 JSON으로 남겨 제작 탭이 보여 준다."""
    ep = t["episode_id"]
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep)
    vers = rpc("agent_script_versions", p_token=TOKEN, p_episode=ep) or []
    orig = next((v for v in vers if (v.get("note") or "").startswith("원고") or v.get("version_no") is None), vers[0] if vers else None)
    conf = next((v for v in reversed(vers) if v.get("status") == "confirmed"), None)
    draft = conf or next((v for v in reversed(vers) if v is not orig and v.get("status") in ("draft", "proposed", "review")), None)
    if not orig or not draft or orig is draft:
        raise RuntimeError("대본 감수할 정리본이 없습니다. 먼저 '작가실 정리 실행'으로 정리본을 만들어 주세요")
    # ① 기계 점검: 씬 번호가 1부터 빠짐·겹침 없이 이어지는가
    nos = [n for n, _, _ in split_scenes(draft["content"]) if n]
    mech = []
    if not nos:
        mech.append("정리본에 씬 머리(S#번호)가 없습니다")
    else:
        dup = sorted({n for n in nos if nos.count(n) > 1})
        gap = [n for n in range(1, max(nos) + 1) if n not in nos]
        if dup:
            mech.append("겹치는 씬 번호: " + ", ".join(f"S#{n}" for n in dup))
        if gap:
            mech.append("빠진 씬 번호: " + ", ".join(f"S#{n}" for n in gap))
        if nos != sorted(nos):
            mech.append("씬 번호가 순서대로가 아닙니다: " + " → ".join(f"S#{n}" for n in nos[:30]))
    cuts = rpc("agent_list_cuts", p_token=TOKEN, p_episode=ep) or []
    cut_view = [{k: c.get(k) for k in ("scene_no", "cut_no", "dialogue", "summary")} for c in cuts if c.get("status") != "cancelled"]
    if cut_view:
        no_cut = [n for n in nos if not any(int(c["scene_no"] or 0) == n for c in cut_view)]
        if no_cut:
            mech.append("컷이 없는 씬: " + ", ".join(f"S#{n}" for n in no_cut))
    model = setting(ctx, "generation", "claude_reviewer", "claude-sonnet-5-5")
    system = role_lessons_block(ctx, "대본감수자").lstrip() + (guide(ctx, "대본감수자") or "너는 대본감수자다. 원고 → 작가실 정리본 → 컷 설계가 씬 순서대로 빠짐없이 옮겨졌는지 감수한다.") + """

[답 형식] 문제만 적는다. 감정선 보강으로 늘어난 지문·숨은 문제가 아니다. 대사는 글자 그대로여야 한다.
<json>{"verdict": "pass|fix", "summary": "대표용 한두 줄",
 "issues": [{"scene": "S#3", "type": "순서|누락|대사 변경|장소 구분|컷 누락|기타", "original": "원고(또는 정리본) 원문 짧게", "now": "지금 상태", "fix": "고칠 곳과 방법", "who": "작가실|감독|대표"}]}</json>"""
    user = ("[대표 원고]\n" + orig["content"][:40000] + "\n\n[작가실 정리본" + (" — 확정" if draft is conf else " — 확정 전") + "]\n" + draft["content"][:50000]
            + ("\n\n[컷 설계(씬·컷·대사)]\n" + json.dumps(cut_view, ensure_ascii=False)[:30000] if cut_view else "")
            + ("\n\n[기계 점검에서 나온 것]\n" + "\n".join(mech) if mech else ""))
    progress(f"대본감수자가 원고·정리본{'·컷 설계' if cut_view else ''}를 씬 순서대로 대조하는 중")
    text, _ = claude(model, system, user, max_tokens=8000, stage="prompt_review", episode_id=ep, note="대본감수자: 원고·정리본 대조")
    r = safe_json(text) or {}
    issues = [i for i in (r.get("issues") or []) if isinstance(i, dict)][:40]
    for m in mech:
        issues.insert(0, {"scene": "", "type": "순서" if "번호" in m else "컷 누락", "original": "", "now": m, "fix": "", "who": "작가실" if "번호" in m or "머리" in m else "감독"})
    verdict = "pass" if not issues and r.get("verdict") != "fix" else "fix"
    out = {"verdict": verdict, "summary": r.get("summary") or ("문제 없음" if verdict == "pass" else f"확인할 것 {len(issues)}건"),
           "issues": issues, "scenes": len(nos), "cuts": len(cut_view), "draft_version": draft.get("version_no"), "confirmed": draft is conf,
           "at": datetime.now().isoformat(timespec="seconds")}
    notify(f"{(ctx.get('episode') or {}).get('number')}화 대본 감수 {'통과' if verdict == 'pass' else f'확인 {len(issues)}건'}", out["summary"][:300],
           "info" if verdict == "pass" else "warning")
    return json.dumps(out, ensure_ascii=False)


FOCUS = {
    "video": ("영상감수자", "화면", "얼굴·의상·소품·배경이 바이블·앞뒤 컷과 같은가, 2~3초마다 표정 변화, 손짓·동작의 자연스러움(손가락·관절 왜곡), 움직임·카메라 속도, 깜빡임·AI 티, 화면 글자, 설계(프롬프트)대로인가"),
    "voice": ("영상감수자", "음성", "들린 대사가 대본과 글자 그대로인가(빠진 말·바뀐 말), 화자가 맞는가, 같은 인물 목소리(높이·음색·나이)가 목표 미리 듣기·다른 컷과 같은가, 젊고 나긋한 사람 목소리인가(AI 티·중후함), 문장 끝음이 사람답게 자연스럽게 내려가며 흐려지는가(뚝 끊김·기계적 끝음), 잡음·끊김·배경음악 섞임, 입모양과 대사 타이밍"),
    "emotion": ("감정감수자", "감정", "표정·목소리 크기와 높낮이·동작이 대사의 감정선을 순서대로 따라가는가, 감정 정점에서 표정과 목소리가 함께 가장 큰가, 앞뒤 컷과 감정 온도가 이어지는가, 연기가 과하거나 밋밋하지 않은가"),
}


def review_take_focus(take_id, mode):
    """생성된 영상을 한 가지 관점(영상·음성·감정)으로만 다시 감수한다. 컷 상태는 바꾸지 않고 결과만 남긴다(대표가 보고 '보완 요청')."""
    who, label, what = FOCUS[mode]
    take = rpc("agent_get_take", p_token=TOKEN, p_id=take_id)
    cut = take["cut"]
    ep = cut["episode_id"]
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=cut.get("script_version_id"))
    model = setting(ctx, "generation", "claude_reviewer", "claude-sonnet-5-5")
    src = os.path.join(HOME, "work", f"{take_id}_{mode}.mp4")
    prev = (take.get("files") or {}).get("preview")
    if prev:
        fetch_to(storage_signed_url("takes", prev, 900), src)
    elif take.get("remote_url"):
        fetch_to(take["remote_url"], src)
    else:
        raise RuntimeError("감수할 영상 파일이 없습니다")
    content = []
    try:
        if mode in ("video", "emotion"):
            progress(f"{cut['code']} {who}가 {label} 감수: 장면을 1초 간격으로 보는 중")
            fdir = os.path.join(HOME, "work", f"ff_{take_id}_{mode}")
            os.makedirs(fdir, exist_ok=True)
            ff("-i", src, "-vf", "fps=1,scale=448:-2", "-frames:v", "16", "-q:v", "4", os.path.join(fdir, "f_%02d.jpg"))
            for i, fp in enumerate(sorted(glob.glob(os.path.join(fdir, "f_*.jpg")))):
                with open(fp, "rb") as f:
                    content += [{"type": "text", "text": f"{i}초"}, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}}]
            shutil.rmtree(fdir, ignore_errors=True)
        if mode in ("voice", "emotion"):
            if not has_audio(src):
                if mode == "voice":
                    raise RuntimeError("이 영상에는 소리가 없습니다(음성 감수 불가)")
            else:
                progress(f"{cut['code']} {who}가 {label} 감수: 소리 받아쓰기·크기·높낮이 분석 중")
                wav = os.path.join(HOME, "work", f"fa_{take_id}_{mode}.wav")
                ff("-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav)
                heard, words = "", []
                try:
                    heard, words = stt_words(wav, setting(ctx, "generation", "stt_model", "scribe_v1"))
                except Exception as ex:
                    log(f"받아쓰기 실패: {ex}")
                content.append({"type": "text", "text": "[영상 소리 분석]\n들린 대사(받아쓰기): " + (heard or "(실패)") + "\n단어별 시간: " + json.dumps(words[:120], ensure_ascii=False)
                                + "\n1초마다 크기(dBFS)·높낮이(Hz): " + json.dumps(curve_summary(audio_curve(wav)), ensure_ascii=False)
                                + "\n목표 대사 미리 듣기: " + json.dumps([{k: ln.get(k) for k in ("speaker", "text", "direction", "tags", "analysis")} for ln in (cut.get("voice_lines") or [])], ensure_ascii=False)})
                os.remove(wav)
    finally:
        try:
            os.remove(src)
        except OSError:
            pass
    content.append({"type": "text", "text": "[컷 설계]\n" + json.dumps({k: cut.get(k) for k in (
        "code", "seconds", "summary", "dialogue", "emotion", "expression_beats", "camera", "reference_names", "prompt")}, ensure_ascii=False)})
    system = (guide(ctx, who) or f"너는 {who}다.") + f"""

[시리즈 바이블]
{bible_text(ctx)}

[이번 감수: {label}만 본다] {what}
다른 관점은 판단하지 않는다. 0~100점, 문제는 몇 초·무엇이·어떻게 달라야 하는지 감독이 프롬프트에 바로 옮길 수 있게.
<json>{{"score": 0, "verdict": "pass|fix", "summary": "대표용 한두 줄", "issues": [{{"t": "3초", "problem": "", "fix": ""}}], "fix_for_director": "보완 요청에 넣을 한 문단"}}</json>"""
    text, _ = claude(model, system, content, max_tokens=3000, stage="video_review", episode_id=ep, scene_no=cut.get("scene_no"),
                     cut_code=cut.get("code"), note=f"{label} 감수(대표 요청)")
    rv = safe_json(text) or {}
    res = {"verdict": "pass" if rv.get("verdict") == "pass" else "fix", "score": num(rv.get("score")), "summary": str(rv.get("summary") or "")[:300],
           "issues": [f"{i.get('t') or ''} {i.get('problem') or ''}{' → ' + i['fix'] if i.get('fix') else ''}".strip() for i in (rv.get("issues") or []) if isinstance(i, dict)][:8],
           "fix": str(rv.get("fix_for_director") or "")[:600] or None, "take_id": take_id, "at": datetime.now().isoformat(timespec="seconds")}
    rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"reviews": {"after_" + mode: res}})
    notify(f"{cut['code']} {label} 감수 {'통과' if res['verdict'] == 'pass' else '확인 필요'}" + (f" ({res['score']}점)" if res["score"] is not None else ""), res["summary"][:300])
    return f"{label} 감수 {res['verdict']} ({res['score']}점)"


@task("cut_review")
def flow_review(t):
    """편집실 흐름 감수: 타임라인에 놓인 컷들을 순서대로 이어 보며 영상감수자(화면 연결)·감정감수자(감정·대사 흐름)가
    대본과 맞춰 보고, 특히 컷과 컷 사이가 어색한 곳을 찾아 '감독에게 보낼 보완 요청'으로 정리한다. 컷은 바꾸지 않는다(대표가 고르고 지시를 덧붙여 보냄)."""
    p = t.get("payload") or {}
    ed = rpc("agent_get_edit", p_token=TOKEN, p_project=p["project_id"]) or {}
    tl = (ed.get("project") or {}).get("timeline") or {}
    clips, total, _ = tl_layout(tl)
    if not clips:
        raise RuntimeError("타임라인에 컷이 없습니다. 먼저 '영상 가져오기'로 컷을 넣어 주세요")
    clips = clips[:60]
    progress(f"흐름 감수 시작: 타임라인 {len(clips)}컷 · {total:.1f}초를 순서대로 봅니다")
    ep0 = (ed.get("episode") or {}).get("id") or t.get("episode_id")
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep0)
    model = setting(ctx, "generation", "claude_reviewer", "claude-sonnet-5-5")
    # 화별 확정 대본(씬 본문)
    scripts = {}
    def scene_body(ep, no):
        if ep not in scripts:
            vers = rpc("agent_script_versions", p_token=TOKEN, p_episode=ep) or []
            conf = next((v for v in reversed(vers) if v.get("status") == "confirmed"), None) or (vers[-1] if vers else None)
            scripts[ep] = {n: (h + "\n" + b) for n, h, b in split_scenes((conf or {}).get("content") or "")} if conf else {}
        return (scripts[ep].get(inum(no)) or "")[:2500]
    work = os.path.join(HOME, "work", f"flow_{t['id']}")
    os.makedirs(work, exist_ok=True)
    rows, files = [], {}
    try:
        for i, c in enumerate(clips):
            progress(f"① 준비 {i + 1}/{len(clips)}: {((ed.get('takes') or {}).get(c.get('take_id')) or {}).get('code') or '컷'} 영상 받아 장면 4장(시작·0.6초·가운데·끝)·소리 크기 뽑는 중")
            take = rpc("agent_get_take", p_token=TOKEN, p_id=c["take_id"]) or {}
            cut = take.get("cut") or {}
            src = files.get(c["take_id"])
            if not src:
                src = os.path.join(work, f"{len(files)}.mp4")
                prev = (take.get("files") or {}).get("preview")
                try:
                    if prev:
                        fetch_to(storage_signed_url("takes", prev, 900), src)
                    elif take.get("remote_url"):
                        fetch_to(take["remote_url"], src)
                    else:
                        src = None
                except Exception as ex:
                    log(f"흐름 감수: 영상 받기 실패 {cut.get('code')}: {ex}")
                    src = None
                files[c["take_id"]] = src
            a, b = float(c["in"]), float(c["out"])
            row = {"i": i, "clip": c["id"], "cut_id": cut.get("id"), "code": cut.get("code") or "", "episode_id": cut.get("episode_id"),
                   "timeline": f"{c['start']:.1f}~{c['start'] + c['dur']:.1f}초", "used": f"원본 {a:.1f}~{b:.1f}초", "gap_before": c.get("gap") or 0,
                   "summary": cut.get("summary"), "dialogue": cut.get("dialogue"), "emotion": cut.get("emotion"), "camera": cut.get("camera"),
                   "prompt": cut.get("prompt"), "scene_no": cut.get("scene_no"), "frames": []}
            if src:
                for tag, at in (("시작", a + 0.05), ("0.6초", min(b - 0.1, a + 0.6)), ("가운데", (a + b) / 2), ("끝", max(a, b - 0.12))):
                    fp = os.path.join(work, f"f{i}_{len(row['frames'])}.jpg")
                    try:
                        ff("-ss", f"{at:.2f}", "-i", src, "-frames:v", "1", "-vf", "scale=384:-2", "-q:v", "5", fp)
                        row["frames"].append((tag, fp))
                    except Exception:
                        pass
                if has_audio(src):
                    wav = os.path.join(work, f"a{i}.wav")
                    try:
                        ff("-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", src, "-vn", "-ac", "1", "-ar", "8000", wav)
                        cv = audio_curve(wav, 0.1)
                        loud = [x for x in cv if x["db"] > -42]
                        row["sound"] = {"첫소리까지초": round(loud[0]["t"], 1) if loud else None,
                                        "끝소리뒤_조용한초": round(cv[-1]["t"] - loud[-1]["t"], 1) if loud and cv else None,
                                        "시작0.3초_dB": round(sum(x["db"] for x in cv[:3]) / max(1, len(cv[:3])), 1) if cv else None,
                                        "끝0.3초_dB": round(sum(x["db"] for x in cv[-3:]) / max(1, len(cv[-3:])), 1) if cv else None}
                    except Exception as ex:
                        log(f"흐름 감수: 소리 분석 실패 {cut.get('code')}: {ex}")
                else:
                    row["sound"] = {"소리": "없음"}
            snd = row.get("sound") or {}
            progress(f"① 준비 {i + 1}/{len(clips)}: {row['code']} 끝 — 장면 {len(row['frames'])}장"
                     + (f", 첫소리 {snd.get('첫소리까지초')}초·끝 정적 {snd.get('끝소리뒤_조용한초')}초" if snd.get("첫소리까지초") is not None else (", 소리 없음" if snd.get("소리") else ""))
                     + ("" if src else " (영상 파일 없음 — 대본만으로 봄)"))
            rows.append(row)
        # 씬별 대본
        scene_txt = {}
        for r in rows:
            k = (r["episode_id"], r["scene_no"])
            if k not in scene_txt and r["episode_id"]:
                scene_txt[k] = scene_body(r["episode_id"], r["scene_no"])
        progress(f"② 대본: 확정본에서 씬 {len(scene_txt)}개 불러옴(" + ", ".join(f"S#{k[1]}" for k in list(scene_txt)[:12]) + ")"
                 + ("" if all(scene_txt.values()) else " — 확정본에 없는 씬이 있어 그 부분은 컷 설계로만 봄"))
        system = (guide(ctx, "영상감수자") or "너는 영상감수자다.") + "\n\n" + (guide(ctx, "감정감수자") or "너는 감정감수자다.") + f"""

[시리즈 바이블]
{bible_text(ctx, 40)}

[이번 일: 편집실 흐름 감수 — 영상감수자와 감정감수자가 함께]
타임라인에 놓인 컷을 순서대로 이어 본다. 컷 하나하나의 완성도보다 '이어 볼 때 자연스러운가'를 본다. 특히 컷과 컷 사이.
- 영상감수자(화면·구도): 앞 컷 '끝' 장면과 다음 컷 '시작' 장면의 인물 위치·시선·자세·의상·빛·배경이 이어지는가,
  구도가 정리돼 있는가 — 샷 크기(풀·미디엄·바스트·클로즈업)가 이야기에 맞게 바뀌는가(같은 크기 반복·이유 없는 급변),
  카메라 높이·각도, 인물 배치(화면 왼쪽/오른쪽, 머리 위 여백, 잘린 곳), 180도 규칙(두 인물의 좌우가 컷마다 뒤바뀌지 않는가),
  시선 방향이 상대와 맞는가, 같은 장소의 공간 배치(문·창·소품 위치)가 컷마다 같은가,
  컷 시작이 굳어 있거나(시작과 0.6초가 거의 같음) 끝에서 동작이 멈추는가.
- 감정감수자: 대본 씬과 비교해 감정이 앞 컷에서 다음 컷으로 자연스럽게 이어지는가(갑자기 다른 감정에서 시작 등),
  대사 순서·누락이 대본과 맞는가, 대사 사이 쉼(첫소리까지초·끝소리뒤_조용한초)이 너무 길거나 짧아 리듬이 깨지는가, 컷 경계에서 소리 크기가 튀는가.
- 편집으로 고칠 수 있는 것(앞뒤 몇 초 잘라내기, 정적 줄이기)은 edit_fix에 적고 remake=false.
  다시 만들어야 하는 것만 remake=true와 fix_for_director(감독이 프롬프트에 바로 옮길 수 있게, 앞뒤 컷과 어떻게 이어져야 하는지 구체적으로).
  다시 만들 컷은 prompt_rewrite에 그 컷의 프롬프트를 처음부터 다시 쓴다(지금 프롬프트와 같은 형식·언어, 대사는 그대로,
  구도·샷 크기·인물 배치·시선·앞 컷에서 이어지는 시작 자세를 분명히).
- 대사는 바꾸라고 하지 않는다. 문제 없는 곳은 적지 않는다. 다시 만들 컷은 꼭 필요한 것만.
- 상호 피드백: 같은 문제가 다음 화에서 처음부터 안 생기게, 원인이 있는 사람에게 교훈(lessons)을 남긴다.
  감독(컷 설계·구도·카메라·컷 나누는 자리), 작가실(씬 구성·감정선·지문), 대본감수자(대본↔컷 대조에서 놓친 것),
  코디네이터(바이블 묘사·장소 공간 배치·의상 등 고정 요소의 프롬프트). rule은 그 사람이 다음에 그대로 따를 한 문장. 3개 이하.
<json>{{"summary": "대표용 두세 줄: 전체 흐름이 어색한 가장 큰 이유",
 "issues": [{{"where": "S01 C02 → S01 C03 (경계) 또는 S01 C02", "cut": "다시 만들거나 고칠 컷 code(E01-S01-C03 형식)", "who": "영상감수자|감정감수자",
   "type": "연결|굳은 시작|끝 멈춤|감정 튐|대본 불일치|리듬|소리", "problem": "무엇이 어떻게 어색한지", "edit_fix": "편집으로 고칠 방법(없으면 빈칸)",
   "remake": true, "fix_for_director": "감독에게 보낼 보완 요청 한 문단", "prompt_rewrite": "다시 쓴 프롬프트(remake일 때만)"}}],
 "lessons": [{{"fingerprint": "짧은-영문-열쇠", "targets": ["감독|작가실|대본감수자|코디네이터"], "category": "camera|continuity|expression|emotion_tone|dialogue|motion|location|prompt|other",
   "problem": "무엇이 반복해서 어색했나", "rule": "다음부터 이렇게", "severity": "보통|치명"}}]}}</json>"""
        issues, summaries, lessons = [], [], []
        step = 14
        for s0 in range(0, len(rows), step - 1):
            part = rows[s0:s0 + step]
            if s0 and len(part) < 2:
                break
            content = []
            for r in part:
                content.append({"type": "text", "text": "[컷] " + json.dumps({k: r.get(k) for k in ("code", "timeline", "used", "gap_before", "summary", "dialogue", "emotion", "camera", "sound", "prompt")}, ensure_ascii=False)})
                for tag, fp in r["frames"]:
                    with open(fp, "rb") as f:
                        content += [{"type": "text", "text": f"{r['code']} {tag}"}, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}}]
            keys = []
            for r in part:
                k = (r["episode_id"], r["scene_no"])
                if k not in keys:
                    keys.append(k)
            content.append({"type": "text", "text": "[대본(확정본) — 이 컷들이 속한 씬]\n" + "\n\n".join(scene_txt.get(k) or "" for k in keys)[:12000]})
            progress(f"③ 감수 {s0 + 1}~{s0 + len(part)}컷: 영상감수자(구도·연결)·감정감수자(감정·대사·리듬)가 "
                     f"{part[0]['code']} ~ {part[-1]['code']}를 대본 씬과 맞춰 보는 중 (장면 {sum(len(r['frames']) for r in part)}장)")
            text, _ = claude(model, system, content, max_tokens=5000, stage="video_review", episode_id=ep0, note="편집실 흐름 감수")
            r = safe_json(text) or {}
            if r.get("summary"):
                summaries.append(r["summary"])
            got = [x for x in (r.get("issues") or []) if isinstance(x, dict)]
            issues += got
            lessons += [x for x in (r.get("lessons") or []) if isinstance(x, dict) and x.get("rule")]
            vw = sum(1 for x in got if "영상" in str(x.get("who"))); em = len(got) - vw
            progress(f"③ 감수 {s0 + 1}~{s0 + len(part)}컷 끝: 확인 {len(got)}건(영상감수자 {vw} · 감정감수자 {em}), 다시 만들 컷 "
                     f"{len({x.get('cut') for x in got if x.get('remake')})}개" + (" — " + "; ".join(f"{x.get('where') or x.get('cut')}: {x.get('problem')}" for x in got[:4])[:250] if got else ""))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    by_code = {r["code"]: r for r in rows if r["code"]}
    out_issues, seen = [], set()
    for x in issues[:60]:
        code = (x.get("cut") or "").strip()
        r = by_code.get(code) or next((v for k, v in by_code.items() if code and (k.endswith(code) or code.replace(" ", "-") in k)), None)
        key = (code, x.get("problem"))
        if key in seen:
            continue
        seen.add(key)
        out_issues.append({**{k: x.get(k) for k in ("where", "who", "type", "problem", "edit_fix", "fix_for_director", "prompt_rewrite")},
                           "cut": r["code"] if r else code, "cut_id": r["cut_id"] if r else None, "episode_id": r["episode_id"] if r else None,
                           "scene_no": r["scene_no"] if r else None, "clip": r["clip"] if r else None, "remake": bool(x.get("remake")) and bool(r)})
    remake = len({i["cut_id"] for i in out_issues if i["remake"]})
    # 상호 피드백: 원인이 있는 사람(감독·작가실·대본감수자·코디네이터)에게 교훈으로 남긴다(같은 문제가 두 번 나오거나 대표가 켜면 다음 제작부터 적용)
    sid = ((ctx.get("series") or {}).get("id")) or (ed.get("episode") or {}).get("series_id")
    out_lessons, seenf = [], set()
    roles = ("감독", "작가실", "대본감수자", "코디네이터")
    for l in lessons[:12]:
        fp = re.sub(r"[^a-z0-9-]", "", str(l.get("fingerprint") or "").lower())[:60] or ("flow-" + hashlib.md5(l["rule"].encode()).hexdigest()[:8])
        if fp in seenf:
            continue
        seenf.add(fp)
        tg = [x for x in (l.get("targets") or []) if x in roles] or ["감독"]
        row = {"fingerprint": "flow:" + fp, "targets": tg, "category": l.get("category") or "other", "problem": l.get("problem") or "",
               "rule": l["rule"], "severity": l.get("severity") or "보통"}
        try:
            got = rpc("agent_add_lesson", p_token=TOKEN, p_lesson={**row, "series_id": sid, "sources": ["영상감수자", "감정감수자"],
                                                                  "cut_refs": sorted({i["cut"] for i in out_issues if i.get("cut")})[:20]}) or {}
            row.update({"id": got.get("id"), "status": got.get("status"), "hit_count": got.get("hit_count")})
        except Exception as ex:
            log(f"흐름 감수 교훈 기록 실패: {ex}")
        out_lessons.append(row)
        progress(f"④ 피드백 → {'·'.join(tg)}: {row['rule'][:120]} ({'지금부터 적용' if row.get('status') == 'active' else '후보 — 한 번 더 나오거나 대표가 켜면 적용'})")
    out = {"summary": " ".join(summaries)[:900] or ("이어 보기에 큰 문제 없음" if not out_issues else ""), "issues": out_issues,
           "lessons": out_lessons, "clips": len(rows), "remake_cuts": remake, "project_id": p["project_id"], "at": datetime.now().isoformat(timespec="seconds")}
    progress(f"⑤ 정리 끝: 확인 {len(out_issues)}건 · 다시 만들 컷 {remake}개 · 피드백 {len(out_lessons)}건 — 편집 탭에서 고르고 지시를 덧붙여 보내 주세요")
    notify(f"편집실 흐름 감수: 확인 {len(out_issues)}건 · 다시 만들 컷 {remake}개", out["summary"][:300], "info" if not out_issues else "warning")
    return json.dumps(out, ensure_ascii=False)


def t_cut_review(t):
    """작가실이 가져온 컷 설계를 제작 전에 감수: mode emotion(감정감수자: 대사·소리·감정선) | video(영상감수자: 화면·동작·일관성).
    고칠 수 있는 것은 감수자가 프롬프트를 직접 고치고(대표 승인 없이 설계 단계만), 결과를 컷마다 남긴다."""
    ep = t["episode_id"]
    p = t.get("payload") or {}
    if p.get("mode") == "script":
        return script_review(t)
    if p.get("mode") == "flow":
        return flow_review(t)
    mode = p.get("mode") if p.get("mode") in ("emotion", "video") else "emotion"
    who = "감정감수자" if mode == "emotion" else "영상감수자"
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep)
    cuts = [c for c in (rpc("agent_list_cuts", p_token=TOKEN, p_episode=ep) or []) if c.get("status") in ("designed", "hold", "failed")]
    if not cuts:
        return "감수할 컷이 없습니다(설계 전이거나 모두 생성됨)"
    rmodel = setting(ctx, "generation", "claude_reviewer", "claude-sonnet-5-5")
    rules = VIDEO_REVIEW_RULES if mode == "video" else ""
    def slim(c):
        return {k: c.get(k) for k in ("scene_no", "cut_no", "code", "seconds", "summary", "dialogue", "emotion", "expression_beats", "camera",
                                      "reference_names", "prompt", "voice_lines", "continue_prev")}
    def review_chunk(chunk):
        if mode == "emotion":
            r = review_cuts(ctx, ep, [slim(c) for c in chunk], rmodel)
            return {(int(x.get("scene_no") or 0), int(x.get("cut_no") or 0)): x for x in (r.get("cuts") or []) if isinstance(x, dict)}
        system = (guide(ctx, "영상감수자") or "너는 영상감수자다.") + f"""

[시리즈 바이블]
{bible_text(ctx)}

{rules}

[답 형식] 고칠 컷만 fixed_prompt(고친 영어 프롬프트 전체)를 준다. 대사는 바꾸지 않는다.
<json>{{"cuts": [{{"scene_no": 1, "cut_no": 1, "verdict": "pass|fix", "issues": ["…"], "fixed_prompt": ""}}]}}</json>"""
        text, _ = claude(rmodel, system, "[컷 설계]\n" + json.dumps([slim(c) for c in chunk], ensure_ascii=False), max_tokens=12000,
                         stage="prompt_review", episode_id=ep, note=f"영상감수자 컷 감수({len(chunk)}컷)")
        return {(int(x.get("scene_no") or 0), int(x.get("cut_no") or 0)): x for x in (safe_json(text).get("cuts") or []) if isinstance(x, dict)}
    chunks = [cuts[i:i + 8] for i in range(0, len(cuts), 8)]
    progress(f"{who}가 컷 {len(cuts)}개를 {len(chunks)}묶음으로 동시에 감수하는 중")
    res = parallel([(lambda ch=ch: review_chunk(ch)) for ch in chunks], workers=4)
    verdicts = {}
    for r in res:
        if isinstance(r, dict):
            verdicts.update(r)
    n_pass = n_fix = n_err = 0
    for c in cuts:
        v = verdicts.get((int(c["scene_no"]), int(c["cut_no"])))
        if not v:
            n_err += 1
            continue
        upd = {}
        fixed = False
        if v.get("verdict") == "fix":
            newp = (v.get("fixed_prompt") or "").strip()
            if mode == "emotion" and not newp and v.get("fix"):   # 감정감수자는 고칠 점만 주면 → 프롬프트에 반영해 다시 쓰게
                txt, _ = claude(rmodel, "너는 감정감수자다. 아래 지적을 반영해 이 컷의 영어 영상 프롬프트를 고쳐 쓴다. 대사는 한국어 원문 그대로. 답은 <json>{\"prompt\": \"\"}</json>",
                                json.dumps({"cut": slim(c), "fix": v.get("fix"), "issues": v.get("issues")}, ensure_ascii=False),
                                max_tokens=3000, stage="prompt_review", episode_id=ep, note="감정감수자 프롬프트 반영")
                newp = (safe_json(txt).get("prompt") or "").strip()
            if len(newp) > 40:
                upd["prompt"] = newp
                fixed = True
        upd["reviews"] = {mode: {"verdict": "pass" if v.get("verdict") != "fix" else ("fixed" if fixed else "fix"),
                                 "issues": [str(x)[:200] for x in (v.get("issues") or [])][:6], "fix": str(v.get("fix") or "")[:300] or None,
                                 "at": datetime.now().isoformat(timespec="seconds")}}
        rpc("agent_update_cut", p_token=TOKEN, p_id=c["id"], p=upd)
        if v.get("verdict") == "fix":
            n_fix += 1
        else:
            n_pass += 1
    msg = f"{who} 감수: {len(cuts)}컷 중 통과 {n_pass} · 고침 {n_fix}" + (f" · 답 없음 {n_err}" if n_err else "")
    notify(f"{(ctx.get('episode') or {}).get('number')}화 {who} 감수 완료", msg + "\n제작 탭에서 확인하고 생성 승인을 눌러 주세요")
    if n_err and not (n_pass + n_fix):
        raise RuntimeError(f"{who}가 컷을 감수하지 못했습니다(답 형식 오류). 다시 시도하면 대부분 해결됩니다")
    return msg


@task("coord_review")
def t_coord_review(t):
    """코디네이터: 적용된 바이블 항목을 감수한다 — 대표 요청 반영, 묘사↔영어 프롬프트 일치, 대본과 어긋남.
    영어 프롬프트만 틀렸으면 직접 보정하고, 묘사 자체를 바꿔야 하면 대표 확인 필요로 넘긴다. 결과는 바이블 카드에 기록."""
    p = t.get("payload") or {}
    if p.get("design_id"):
        return design_check(p["design_id"])
    info = rpc("agent_coord_info", p_token=TOKEN, p_version=p.get("version_id")) or {}
    if not info:
        return "대상 버전이 없습니다(삭제됨)"
    if info.get("status") != "active":
        return "이미 다른 버전으로 바뀌어 감수하지 않음"
    name, cat = info.get("name"), info.get("category")
    hangul = re.search(r"[가-힣]", (info.get("description") or "") + (info.get("details") or ""))
    if (info.get("prompt_pending") or (hangul and not info.get("prompt_en"))) and waited(t) < 300:
        raise Retry(15, f"{name}: 영어 프롬프트 반영이 끝난 뒤 바로 감수합니다")
    if (info.get("description") or "").startswith("(작가실이 사진을 보고 쓰는 중)") and waited(t) < 300:
        raise Retry(15, f"{name}: 코디네이터가 사진을 보고 묘사를 쓰는 중이라 끝난 뒤 감수합니다")
    prev = info.get("prev") or {}
    reason = (info.get("change_reason") or "").strip()
    owner_req = reason if info.get("proposed_by") == "대표" and not re.search(r"(사진을 보고|가져옴|얼굴 고정|Soul)", reason) else ""
    owner_req = re.sub(r"\s*→ (작가실이|코디네이터가) 묘사·프롬프트에 반영:.*$", "", owner_req)
    prompt = (info.get("prompt_en") or "").strip()
    found = script_mentions(info.get("series_id"), name, info.get("summary"), limit=6000) if cat not in ("style", "music") else ""
    system = role_lessons_block(series_lessons(info.get("series_id")), "코디네이터").lstrip() + f"""너는 드라마 제작팀의 코디네이터다. 시리즈 바이블 항목의 묘사·영어 프롬프트를 감수한다.
확인할 것:
1) 대표 요청(있으면)이 한글 묘사와 영어 프롬프트에 정확히 들어갔는가 — 요청을 항목별로 쪼개 하나씩.
2) 영어 프롬프트가 한글 묘사·세부 사항의 눈에 보이는 고정 특징({PROMPT_NEEDS.get(cat, '눈에 보이는 특징')})을 빠짐없이, 어긋나지 않게 담았는가. 한글·이름·브랜드가 섞이지 않았는가.
   생성 서비스 안전 규정에 걸릴 표현이 없는가: 실존 인물(배우·가수 등)을 닮았다는 표현은 이름을 빼고 그 인상을 이목구비 특징으로 바꿔 쓴다. 신체 부위를 성적으로 강조하는 표현(가슴골·노출 등)은 생성 서비스가 거절하므로 '균형 잡힌 글래머러스한 체형'처럼 전체 체형 인상으로 완곡하게 쓴다. 걸릴 표현이 있으면 verdict "fix"로 영어 프롬프트를 완곡하게 고친다(한글 묘사는 그대로 두고 summary에 무엇을 바꿨는지 적는다).
3) 대본 발췌(있으면)와 어긋나는 것(나이·성별·머리·옷·장소 구조 등)이 없는가.
처리:
- 영어 프롬프트만 틀리거나 빠졌으면 verdict "fix"와 fixed_prompt_en(고친 영어 프롬프트 전체, 4~8문장)을 준다. 묘사는 고치지 않는다.
- 대표 요청이 한글 묘사에 빠졌거나 다르게 들어갔으면 verdict "apply"와 fixed_description(요청을 반영해 고친 한국어 묘사 전체), fixed_prompt_en(그에 맞춘 영어 프롬프트 전체)을 준다. 대표 요청은 대표가 직접 시킨 일이므로 그대로 반영한다.
- 요청과 관계없이 묘사가 대본과 부딪히면 고치지 않고 verdict "owner", owner_note에 무엇을 어떻게 고치면 되는지 한국어 한 줄.
- 모두 맞으면 verdict "pass".
checks는 항목마다: item(무엇을 봤는지, 한국어 짧게), status(ok|missing|conflict), where(request|description|prompt|script), note(근거 문구, 짧게).
답 형식: <json>{{"verdict": "pass|fix|apply|owner", "checks": [{{"item": "", "status": "ok", "where": "prompt", "note": ""}}], "fixed_description": "", "fixed_prompt_en": "", "owner_note": "", "summary": "한국어 한 줄"}}</json>"""
    user = (f"항목: {name} ({cat})\n대본 속 역할: {info.get('summary') or '-'}\n"
            f"[대표 요청(고쳐쓰기 '바꾸는 이유·바꿀 내용')]\n{owner_req or '(없음)'}\n\n"
            f"[바로 앞 버전 v{prev.get('version_no', '-')} 묘사]\n{prev.get('description') or '-'}\n[바로 앞 버전 영어 프롬프트]\n{prev.get('prompt_en') or '-'}\n\n"
            f"[지금 v{info.get('version_no')} 묘사]\n{info.get('description') or '-'}\n[지금 세부 사항]\n{info.get('details') or '-'}\n"
            f"[지금 영어 프롬프트]\n{prompt or '(없음 — 묘사를 그대로 씀)'}\n\n[대본에서 이 항목이 나오는 부분]\n{found or '(없음)'}")
    # 기준 사진도 본다: 묘사와 맞는지, 글자·이름표·자막이 찍혀 있지 않은지, (인물) 같은 사람인지
    blocks = []
    for i, path in enumerate((info.get("image_paths") or [])[:4], 1):
        tmp = os.path.join(HOME, "work", f"cv_{uuid.uuid4().hex[:6]}.img")
        try:
            fetch_to(storage_signed_url("bible", path, 3600), tmp)
            blocks += photo_block_large(tmp, f"[기준 사진 {i}]")
        except Exception as ex:
            log(f"감수용 사진 받기 실패: {ex}")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    if blocks:
        system += f"""
4) 기준 사진({len(blocks) // 2}장)도 본다: {'인물은 얼굴(나이 인상·성별·이목구비)과 몸(체형·비율·몸매)만 묘사와 맞는지(의상·액세서리·헤어스타일·배경은 장면마다 바뀌니 보지 않음)' if cat == 'character' else '묘사·영어 프롬프트와 맞는가(구조·재질·색)'},
   사진 안에 글자·이름표·자막·워터마크·로고가 찍혀 있지 않은가(예: 다른 사람 이름 같은 한글·영문 글자), {'모든 사진이 같은 사람인가, ' if cat == 'character' else ''}실사인가.
   문제가 있으면 photo_verdict "redo"와 photo_issues(한국어, 사진 번호와 무엇이 문제인지)를 준다. 문제없으면 photo_verdict "ok".
답 형식에 "photo_verdict": "ok|redo", "photo_issues": ["…"]를 함께 넣는다."""
    progress(f"코디네이터가 {name} v{info.get('version_no')}을(를) 감수하는 중" + (f"(기준 사진 {len(blocks) // 2}장 포함)" if blocks else ""))
    text, _ = claude("claude-sonnet-5-5", system, ([{"type": "text", "text": user}] + blocks) if blocks else user,
                     max_tokens=4000, stage="other", note=f"코디네이터 감수: {name}")
    d = safe_json(text)
    verdict = d.get("verdict") if d.get("verdict") in ("pass", "fix", "apply", "owner") else None
    if not verdict:
        raise RuntimeError(f"코디네이터 답이 형식에 맞지 않았습니다(감수 기록 없음). 다시 시도하면 대부분 해결됩니다. 답 앞부분: {text.strip()[:120]!r}")
    checks = [c for c in (d.get("checks") or []) if isinstance(c, dict)][:20]
    after = None
    applied_desc = False
    if verdict == "apply":   # 대표 요청이 묘사에 빠짐 → 코디네이터가 요청대로 묘사·프롬프트를 고친다
        nd = (d.get("fixed_description") or "").strip()
        if len(nd) >= 10 and owner_req:
            rpc("agent_bible_set_description", p_token=TOKEN, p_version=info["version_id"], p_description=nd, p_details=info.get("details") or "",
                p_reason=f"{owner_req} → 코디네이터가 묘사·프롬프트에 반영: 감수에서 빠진 요청을 넣음")
            for c in checks:
                if c.get("status") in ("missing", "conflict") and c.get("where") in ("request", "description"):
                    c["status"] = "fixed"
            verdict, applied_desc = "fix", True
        else:
            verdict = "owner"
            d["owner_note"] = d.get("owner_note") or "대표 요청이 묘사에 빠졌는데 고친 묘사가 비었습니다. 고쳐쓰기에서 묘사 칸에 직접 적어 주세요"
    if verdict == "fix":
        after = clean_en(json.dumps({"prompt_en": d.get("fixed_prompt_en") or ""}))
        if after:
            rpc("agent_bible_set_prompt", p_token=TOKEN, p_version=info["version_id"], p_prompt=after)
            for c in checks:
                if c.get("status") in ("missing", "conflict") and c.get("where") == "prompt":
                    c["status"] = "fixed"
        elif applied_desc:   # 묘사는 고쳤고 영어 프롬프트는 곧 새로 씀(묘사가 바뀌면 자동으로 다시 돎)
            d["summary"] = (d.get("summary") or "") + " · 영어 프롬프트는 고친 묘사로 곧 새로 씁니다"
        else:
            verdict = "owner"
            d["owner_note"] = d.get("owner_note") or "영어 프롬프트를 고쳐야 하는데 보정안이 비었습니다. 다시 감수를 누르거나 묘사를 더 자세히 적어 주세요"
    final = {"pass": "pass", "fix": "fixed", "owner": "owner"}[verdict]
    photo_redo = bool(blocks) and d.get("photo_verdict") == "redo"
    rpc("agent_bible_review_save", p_token=TOKEN, p={
        "series_id": info.get("series_id"), "entry_id": info["entry_id"], "version_id": info["version_id"], "version_no": info.get("version_no"),
        "verdict": final, "request": owner_req or None, "checks": checks, "prompt_before": prompt or None, "prompt_after": after,
        "owner_note": (d.get("owner_note") or "")[:500] or None, "summary": (d.get("summary") or "")[:300],
        "photo_redo": photo_redo, "photo_issues": [str(x)[:200] for x in (d.get("photo_issues") or [])][:8] if photo_redo else []})
    if photo_redo:
        notify(f"코디네이터: {name} 사진 다시 만들기 추천", "; ".join(map(str, (d.get("photo_issues") or [])[:3]))[:300]
               + "\n바이블 카드의 '감수·반영 기록'에서 [다시 적용해서 사진 만들기]를 누르세요", "warning")
    elif final == "owner":
        notify(f"코디네이터: {name} 확인이 필요합니다", (d.get("owner_note") or d.get("summary") or "")[:300] + "\n바이블 카드의 '감수·반영 기록'에서 볼 수 있습니다", "warning")
    return f"{name} v{info.get('version_no')} 감수: " + {"pass": "반영 확인", "fixed": "영어 프롬프트 보정", "owner": "대표 확인 필요"}[final] + (" · 사진 다시 만들기 추천" if photo_redo else "")



@task("bible_place")
def t_bible_place(t):
    """대표가 추가한 소품(등)을 대본 어느 화·씬에 넣으면 좋을지 제작자가 판단해 추천한다. 필요하면 영어 고정 묘사도 쓴다."""
    p = t.get("payload") or {}
    info = rpc("agent_bible_place_info", p_token=TOKEN, p_entry=p.get("entry_id")) or {}
    e = info.get("entry") or {}
    if not e:
        return "항목이 없습니다(삭제됨)"
    eps = [x for x in info.get("episodes") or [] if (x.get("text") or "").strip()]
    if not eps and p.get("place") is not False:
        raise RuntimeError("대본이 들어간 화가 없어 추천할 수 없습니다. 대본 탭에서 대본을 먼저 넣어 주세요")
    want_desc = bool(p.get("write_description"))
    progress(f"작가실이 대본 {len(eps)}개 화를 읽고 '{e.get('name')}'이(가) 잘 보일 화·씬을 고르는 중")
    budget = max(8000, 240000 // max(1, len(eps)))
    scripts = "\n\n".join(f"[{x['number']}화 {x.get('title') or ''}]\n{(x.get('text') or '')[:budget]}" for x in eps)
    system = """너는 드라마 작가실이다. 대표가 바이블에 직접 넣은 장소·배경·소품은 광고(PPL·협찬)일 가능성이 높다.
대본의 어느 화·씬에 넣으면 시청자에게 가장 '세게' 보일지 판단해 추천하고, 그 씬에서 어떻게 강하게 보여 줄지 조정안을 낸다.
- 노출이 강한 곳을 우선: 인물이 직접 쓰거나 만지는 순간, 감정이 터지는 장면, 화의 첫 장면·마지막 장면, 반복 등장으로 기억에 남는 곳.
- 화면에서 크게·오래: 클로즈업이나 화면 중앙, 3초 이상, 조명이 닿게. 장소·배경은 인물이 들어서는 와이드 숏으로 공간이 한눈에 보이게.
- 그래도 이야기 흐름은 해치지 않는다: 대사를 새로 쓰거나 줄거리를 바꾸지 않고, 화면 안 배치·행동·카메라로 강조한다.
- 씬 번호는 그 화 대본의 씬 순서(1부터). 대본에 실제로 있는 씬만 고른다.
- 추천은 2~5곳, 좋은 순서대로. 각 추천: reason(왜 여기인지, 한국어 1~2문장), how(화면에서 어떻게 세게 보이게 할지: 누가 들고/어디 놓이고/카메라가 얼마나 크게·몇 초 잡는지, 한국어 1~2문장).
- description: 요청이 있을 때만. 영상·사진 모델에 그대로 넣을 영어 고정 묘사 3~6문장(모양·비율·크기·재질·질감·색·무늬·낡은 정도). 실존 브랜드 이름 금지. 요청이 없으면 빈 문자열.
답 형식: <json>{"placements": [{"ep": 1, "scene": 1, "reason": "", "how": ""}], "description": ""}</json>"""
    user = (f"시리즈: {(info.get('series') or {}).get('title', '')}\n"
            f"새 항목: {e.get('name')} ({e.get('category')})\n대표 설명: {e.get('summary') or '-'}\n"
            f"지금 묘사: {e.get('description') or '-'}\n세부 사항·대표 희망: {e.get('details') or '-'}\n"
            f"대표가 원하는 쓰임(있으면 우선 반영): {p.get('hint') or '-'}\n"
            f"영어 고정 묘사 필요: {'예' if want_desc else '아니오'}\n\n"
            f"[바이블의 다른 항목]\n{json.dumps(info.get('others') or [], ensure_ascii=False)[:6000]}\n\n[대본]\n{scripts}")
    text, _ = claude("claude-opus-5-5", system, user, max_tokens=4000, stage="other", note=f"바이블 항목 배치 추천: {e.get('name')}")
    d = pick_json(text)
    desc = (d.get("description") or "").strip()
    if want_desc and desc and e.get("version_id"):
        rpc("agent_bible_set_description", p_token=TOKEN, p_version=e["version_id"], p_description=desc, p_details=e.get("details") or "",
            p_reason="대표가 소품 추가 · 설명을 보고 묘사 자동 작성")
    if p.get("place") is False:  # 대표가 추천을 원하지 않음: 묘사만
        return f"{e.get('name')} 묘사 {len(desc)}자"
    nums = {int(x["number"]) for x in eps}
    items = []
    for x in d.get("placements") or []:
        try:
            ep, sc = inum(x.get("ep")), inum(x.get("scene"))
        except (TypeError, ValueError):
            continue
        if ep in nums and sc > 0:
            items.append({"ep": ep, "scene": sc, "reason": str(x.get("reason") or "")[:400], "how": str(x.get("how") or "")[:400],
                          "accepted": False, "note_id": None})
    if not items:
        raise RuntimeError("추천할 화·씬을 찾지 못했습니다")
    rpc("agent_bible_save_placement", p_token=TOKEN, p_entry=e["id"], p_items=items[:5])
    notify(f"{e.get('name')} 넣을 곳 추천 {len(items[:5])}곳", "바이블 탭 카드에서 '넣기'를 누르면 그 씬 감독 메모로 들어갑니다")
    return f"{e.get('name')} 추천 {len(items[:5])}곳"


@task("voice_cast")
def t_voice_cast(t):
    """시리즈 인물 전체의 목소리 설명·시험 대사를 Claude가 쓰고, 인물마다 후보 3개를 만들도록 이어서 맡긴다."""
    p = t.get("payload") or {}
    sid = t.get("series_id") or p.get("series_id")
    cast = rpc("agent_series_cast", p_token=TOKEN, p_series=sid) or {}
    chars = [c for c in cast.get("characters") or [] if (p.get("redo") or not c.get("voice_id")) and not c.get("open_design")]
    if p.get("entry_ids"):
        chars = [c for c in chars if c["entry_id"] in p["entry_ids"]]
    else:   # 자동 추천은 원래 인물만(동일인의 다른 모습은 그 목소리를 같이 씀)
        chars = [c for c in chars if not c.get("same_as")]
    if not chars:
        return "목소리를 새로 만들 인물이 없습니다(이미 정했거나 후보가 기다리는 중)"
    title = (cast.get("series") or {}).get("title", "")
    progress(f"작가실이 「{title}」 인물 {len(chars)}명의 목소리를 설계하는 중")
    system = """너는 드라마 캐스팅 디렉터 겸 음성 감독이다. 대본과 인물 설정을 읽고 인물마다 ElevenLabs 목소리 디자인용 설명을 쓴다.
- 대표 기본값(반드시): 모든 인물은 20대 초중반의 젊은 목소리, 사람처럼 자연스럽고(AI 티 없음) 나긋하고 부드러운 목소리다.
  특히 문장 끝음 처리를 사람답게: 마지막 음절이 자연스럽게 내려가며 살짝 흐려지거나 숨이 섞인다(질문만 살짝 올림). 뚝 끊기거나 평평하거나 기계적인 끝음 금지.
  설정상 나이가 많아 보여도 중후·굵은·허스키·걸걸한 목소리로 쓰지 않는다(deep, husky, raspy, mature, middle-aged, narrator 같은 말 금지).
- voice_description: 영어 200~500자. 기본값 위에 이 인물만의 것: 성별, 높낮이(조금 높게/조금 낮게), 밝기, 말 속도, 그리고 이 인물의 감정선
  (대본에서 이 인물이 주로 지나가는 감정, 예: 떨리지만 다짐하는, 장난스럽다가 진지해지는)과 감정 표현 습관. 녹음 품질 "clean close-mic studio recording".
  실존 인물·배우·가수 이름은 쓰지 않는다. 인물끼리는 높이·밝기·속도·감정 습관으로 구별한다(나이·굵기로 구별하지 않는다).
- sample_text: 그 인물이 대본에서 할 법한 한국어 대사 130~250자. 감정이 두세 번 바뀌게(설렘→긴장→다짐 등) 써서 목소리 폭을 들을 수 있게.
- singing_hint: 노래 장면이 있다면 어울리는 노래 음색 한 줄(한국어). 없으면 빈 문자열.
답 형식: <json>{"voices": [{"entry_id": "", "name": "", "voice_description": "", "sample_text": "", "singing_hint": ""}]}</json>"""
    user = (f"[대표 지시 — 최우선으로 반영]\n{p['hint']}\n\n" if p.get("hint") else "") + f"시리즈: {title}\n\n[인물]\n" + json.dumps([{k: c.get(k) for k in ("entry_id", "name", "summary", "description", "details")} for c in chars], ensure_ascii=False) \
        + "\n\n[대본 앞부분]\n" + "\n\n".join(f"{s['number']}화 {s.get('title') or ''}\n{s.get('text') or ''}" for s in cast.get("scripts") or [])[:40000]
    text, _ = claude("claude-opus-5-5", system, user, max_tokens=8000, stage="other", note=f"시리즈 목소리 설계: {title}")
    voices = (safe_json(text) or {}).get("voices") or []
    if not voices:   # 답이 잘리거나 형식이 깨졌으면 한 번 더(짧게)
        text, _ = claude("claude-opus-5-5", system + "\n답은 <json>…</json> 하나로만, 인물마다 voice_description 300자 이내로 짧게.", user,
                         max_tokens=8000, stage="other", note=f"시리즈 목소리 설계(형식 재요청): {title}")
        voices = (safe_json(text) or {}).get("voices") or []
    if not voices:
        raise RuntimeError(f"작가실(Claude)이 목소리 설계 답을 형식대로 주지 않았습니다(두 번 시도). 다시 시도하면 대부분 해결됩니다. 답 앞부분: {text[:150]}")
    ids = {c["entry_id"] for c in chars}
    made = []
    for v in voices:
        if v.get("entry_id") not in ids:   # 번호를 잘못 적었으면 이름으로 찾는다
            hit = [c for c in chars if c.get("name") == v.get("name")]
            if len(hit) == 1:
                v["entry_id"] = hit[0]["entry_id"]
        if v.get("entry_id") not in ids or len(v.get("voice_description") or "") < 20:
            continue
        sample = (v.get("sample_text") or "").strip()
        if len(sample) < 100:
            sample = (sample + " " + "오늘은 정말 긴 하루였어요. 그래도 끝까지 해낼 거예요. 지켜봐 주세요.")[:300]
        desc = v["voice_description"] + (f"\n[노래] {v['singing_hint']}" if v.get("singing_hint") else "")
        did = rpc("agent_add_voice_design3", p_token=TOKEN, p_entry=v["entry_id"], p_description=desc[:1000], p_sample=sample,
                  p_auto=bool(p.get("auto")), p_force=bool(p.get("force")))
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "voice_design", "payload": {"design_id": did}})
        made.append(v.get("name") or "")
    if not made:
        raise RuntimeError("작가실 답에서 이 인물들의 목소리 설계를 찾지 못했습니다(인물 이름·번호 불일치). 다시 시도해 주세요")
    notify(f"「{title}」 목소리 후보 준비 시작", f"{', '.join(made)} — " + ("추천 목소리(후보 1번)를 바로 바이블에 넣습니다. 바이블 → 목소리에서 다른 후보로 바꿀 수 있습니다" if p.get("auto") else "몇 분 뒤 바이블 탭에서 인물별 후보 3개를 듣고 골라 주세요"))
    return f"인물 {len(made)}명 목소리 설계 → 후보 만들기 시작: {', '.join(made)}"


@task("voice_save")
def t_voice_save(t):
    did = (t.get("payload") or {}).get("design_id")
    d = rpc("agent_get_voice_design", p_token=TOKEN, p_id=did)
    if not d or not d.get("chosen_generated_id"):
        if d:
            rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": "고른 목소리가 없습니다"})
        raise RuntimeError("고른 목소리가 없습니다")
    vid = _voice_save_design(d)
    if d.get("auto"):   # 추천 목소리를 대표가 다른 후보로 바꾼 것 → 바로 바이블에 반영하고 생성 전 컷의 대사 미리 듣기도 새 목소리로
        rpc("agent_voice_link", p_token=TOKEN, p_design=did, p_force=True)
        return f"{d['entry_name']} 목소리를 다른 후보로 바꿔 바이블에 반영 ({vid})"
    return f"{d['entry_name']} 목소리 저장 ({vid})"


def _voice_save_design(d):
    did = d["id"]
    progress(f"{d['entry_name']} 목소리를 ElevenLabs에 저장하는 중")
    desc = voice_desc(d["description"])
    if len(desc) < 20:
        desc = (desc + " — NYX WHITE 드라마 캐릭터 목소리")[:1000]
    try:
        data = el_json("/v1/text-to-voice", {
            "voice_name": f"{d['entry_name']} · {d.get('series_title') or 'NYX'}"[:100], "voice_description": desc[:1000],
            "generated_voice_id": d["chosen_generated_id"],
            "labels": {"project": "NYX WHITE", "character": d["entry_name"][:30]},
            "played_not_selected_voice_ids": [p["generated_voice_id"] for p in d.get("previews") or []
                                              if p.get("generated_voice_id") and p["generated_voice_id"] != d["chosen_generated_id"]]})
    except Exception as e:
        rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": str(e)[:500]})
        raise
    rpc("agent_update_voice_design", p_token=TOKEN, p_id=did, p={"status": "saved", "voice_id": data["voice_id"]})
    return data["voice_id"]


# ---------------- 편집실: 타임라인 조립(ffmpeg) · 말로 편집(Claude)
def tl_layout(tl):
    """타임라인 계산: 클립마다 시작 위치, 전체 길이, 자막의 절대 시간."""
    t, out = 0.0, []
    for c in tl.get("clips") or []:
        gap = max(0.0, float(c.get("gap") or 0))
        a, b = float(c.get("in") or 0), float(c.get("out") or 0)
        if b - a < 0.2:
            continue
        t += gap
        out.append({**c, "start": round(t, 3), "dur": round(b - a, 3)})
        t += b - a
    pos = {c["id"]: c for c in out}
    subs = []
    for s in tl.get("subs") or []:
        if not (s.get("text") or "").strip():
            continue
        if s.get("clip_id"):
            c = pos.get(s["clip_id"])
            if not c:
                continue
            st, en = c["start"] + max(0.0, float(s.get("rs") or 0)), c["start"] + min(c["dur"], float(s.get("re") or c["dur"]))
        else:
            st, en = float(s.get("start") or 0), float(s.get("end") or 0)
        if en - st >= 0.3:
            subs.append({"start": round(st, 3), "end": round(min(en, t), 3), "text": s["text"].strip()})
    return out, round(t, 3), sorted(subs, key=lambda x: x["start"])


def ts(sec, vtt=False):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{'.' if vtt else ','}{ms:03d}"


def write_subs(subs, srt, vtt):
    with open(srt, "w", encoding="utf-8") as f:
        for i, s in enumerate(subs, 1):
            f.write(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text']}\n\n")
    with open(vtt, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for s in subs:
            f.write(f"{ts(s['start'], True)} --> {ts(s['end'], True)}\n{s['text']}\n\n")


def video_size(src):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", src], capture_output=True, text=True, timeout=60)
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r.stderr or "")
    return (int(m.group(1)), int(m.group(2))) if m else (720, 1280)


def take_source(take_id, tk):
    """이 컴퓨터에 있는 원본을 쓰고, 없으면 미리보기(저장소)나 원래 주소에서 받아 둔다."""
    f = tk.get("files") or {}
    for key in (PLATFORM, "mac", "windows"):
        p = (f.get(key) or {}).get("original")
        if p and os.path.exists(p):
            return p
    cache = os.path.join(HOME, "work", "src", f"{take_id}.mp4")
    if os.path.exists(cache):
        return cache
    if f.get("preview"):
        return fetch_to(storage_signed_url("takes", f["preview"], 900), cache)
    if tk.get("remote_url"):
        return fetch_to(tk["remote_url"], cache)
    raise RuntimeError(f"{tk.get('code')} 영상 파일을 찾지 못했습니다")


def episode_dir(ed):
    settings = ed.get("settings") or {}
    e = ed.get("episode") or {}
    d = os.path.join(local_root(settings.get("storage")), safe(ed.get("series_title") or "시리즈"),
                     f"EP{int(e.get('number') or 0):02d}_{safe(e.get('title'), 30)}", "00_편집본")
    os.makedirs(d, exist_ok=True)
    return d


def even(n):
    n = int(n)
    return n - (n % 2)


def premiere_xml(path, name, clips, srcs, musics, W, H, total, src_dir):
    """프리미어 프로·다빈치에서 여는 타임라인(FCP7 XML). 영상 V1, 대사 A1·A2, 음악 A3·A4. 소스는 같은 폴더의 '소스'에 복사."""
    import urllib.parse
    from xml.sax.saxutils import escape as x
    fps = 30
    fr = lambda s: int(round(float(s) * fps))
    def url(p):
        p = os.path.abspath(p).replace("\\", "/")
        if not p.startswith("/"):
            p = "/" + p
        return "file://localhost" + urllib.parse.quote(p, safe="/:")
    os.makedirs(src_dir, exist_ok=True)
    files, fdefs = {}, {}
    def file_el(fid, p, dur_f, video=True):
        if fid in fdefs:
            return f'<file id="{fid}"/>'
        fdefs[fid] = True
        vid = f"<video><samplecharacteristics><width>{W}</width><height>{H}</height></samplecharacteristics></video>" if video else ""
        return (f'<file id="{fid}"><name>{x(os.path.basename(p))}</name><pathurl>{url(p)}</pathurl>'
                f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate><duration>{dur_f}</duration>"
                f"<media>{vid}<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics>"
                f"<channelcount>2</channelcount></audio></media></file>")
    vitems, a1, a2 = [], [], []
    for i, (c, src) in enumerate(zip(clips, srcs), 1):
        key = os.path.abspath(src)
        if key not in files:
            dst = os.path.join(src_dir, f"{c.get('code') or 'clip'}_{len(files) + 1}.mp4".replace("/", "_"))
            if not os.path.exists(dst):
                shutil.copyfile(src, dst)
            files[key] = (f"file-{len(files) + 1}", dst, fr(c.get("src_len") or c["out"]))
        fid, dst, durf = files[key]
        st, en = fr(c["start"]), fr(c["start"] + c["dur"])
        cin, cout = fr(c["in"]), fr(c["out"])
        nm = x(c.get("code") or f"clip{i}")
        vitems.append(f'<clipitem id="v{i}"><name>{nm}</name><duration>{durf}</duration><rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>'
                      f"<start>{st}</start><end>{en}</end><in>{cin}</in><out>{cout}</out>{file_el(fid, dst, durf)}</clipitem>")
        off = fr(float(c.get("voice_offset") or 0))
        ast, aen = max(0, st + off), max(0, st + off) + (en - st)
        g = float(c.get("voice_gain") if c.get("voice_gain") is not None else 1)
        lvl = (f'<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effecttype>audiolevels</effecttype><mediatype>audio</mediatype>'
               f'<parameter><parameterid>level</parameterid><name>Level</name><value>{g:.3f}</value></parameter></effect></filter>') if abs(g - 1) > 0.01 else ""
        for trk, lst in ((1, a1), (2, a2)):
            lst.append(f'<clipitem id="a{trk}_{i}"><name>{nm} 대사</name><duration>{durf}</duration><rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>'
                       f"<start>{ast}</start><end>{aen}</end><in>{cin}</in><out>{cout}</out><file id=\"{fid}\"/>"
                       f"<sourcetrack><mediatype>audio</mediatype><trackindex>{trk}</trackindex></sourcetrack>{lvl}</clipitem>")
    m1, m2 = [], []
    for j, (m, local) in enumerate(musics, 1):
        dst = os.path.join(src_dir, f"music_{j}{os.path.splitext(local)[1] or '.mp3'}")
        if not os.path.exists(dst):
            shutil.copyfile(local, dst)
        srclen = fr(m.get("src_len") or m.get("out") or 60)
        mi, mo = fr(m.get("in") or 0), fr(m.get("out") or (m.get("src_len") or 60))
        st = fr(m.get("start") or 0)
        g = float(m.get("gain") if m.get("gain") is not None else 0.6)
        fid = f"music-{j}"
        for trk, lst in ((1, m1), (2, m2)):
            lst.append(f'<clipitem id="m{trk}_{j}"><name>{x(m.get("name") or "음악")}</name><duration>{srclen}</duration><rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>'
                       f"<start>{st}</start><end>{st + (mo - mi)}</end><in>{mi}</in><out>{mo}</out>{file_el(fid, dst, srclen, video=False)}"
                       f"<sourcetrack><mediatype>audio</mediatype><trackindex>{trk}</trackindex></sourcetrack>"
                       f'<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effecttype>audiolevels</effecttype><mediatype>audio</mediatype>'
                       f'<parameter><parameterid>level</parameterid><name>Level</name><value>{g:.3f}</value></parameter></effect></filter></clipitem>')
    trk = lambda items: "<track>" + "".join(items) + "</track>"
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq1"><name>{x(name)}</name>'
           f"<duration>{fr(total)}</duration><rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"
           f"<media><video><format><samplecharacteristics><width>{W}</width><height>{H}</height><pixelaspectratio>square</pixelaspectratio>"
           f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate></samplecharacteristics></format>{trk(vitems)}</video>"
           f"<audio>{trk(a1)}{trk(a2)}{trk(m1) if m1 else ''}{trk(m2) if m2 else ''}</audio></media></sequence></xmeml>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


@task("assemble")
def t_assemble(t):
    rid = (t.get("payload") or {}).get("render_id")
    r = rpc("agent_get_render", p_token=TOKEN, p_id=rid)
    if not r:
        raise RuntimeError("조립 요청을 찾지 못했습니다")
    upd = lambda **p: rpc("agent_update_render", p_token=TOKEN, p_id=rid, p=p)
    ed = rpc("agent_get_edit", p_token=TOKEN, p_project=r["project_id"]) or {}
    tl, kind = r["timeline"], r["kind"]
    takes = ed.get("takes") or {}
    upd(status="running", progress="재료 모으는 중")
    try:
        # 쓸 수 없는 컷을 먼저 빼고 위치를 계산한다(나중에 빼면 그 뒤 소리·자막이 빠진 길이만큼 어긋났음)
        tl = {**tl, "clips": [c for c in tl.get("clips") or [] if c.get("take_id") in takes]}
        clips, total, subs = tl_layout(tl)
        if not clips:
            raise RuntimeError("타임라인에 쓸 수 있는 컷이 없습니다")
        work = os.path.join(HOME, "work", f"render_{rid}")
        os.makedirs(work, exist_ok=True)
        progress(f"재료 {len(clips)}개를 동시에 받는 중")
        srcs = parallel([(lambda c=c: take_source(c["take_id"], takes[c["take_id"]])) for c in clips], workers=4)
        bad = next((x for x in srcs if isinstance(x, Exception)), None)
        if bad:
            raise bad
        W, H = video_size(srcs[0])
        if kind == "preview":
            s = 640 / max(W, H)
            W, H = even(W * s), even(H * s)
        W, H = even(W), even(H)
        musics = []
        for m in tl.get("music") or []:
            if not m.get("path"):
                continue
            local = os.path.join(HOME, "work", "music", os.path.basename(m["path"]))
            if not os.path.exists(local):
                fetch_to(storage_signed_url("takes", m["path"], 900), local)
            musics.append((m, local))
        args, fc, alab = [], [], []
        for i, (c, src) in enumerate(zip(clips, srcs)):
            args += ["-i", src]
            a, b, gap = float(c["in"]), float(c["out"]), float(c.get("gap") or 0)
            pad = f",tpad=start_duration={gap:.3f}:start_mode=add:color=black" if gap > 0 else ""
            # 실제 파일이 설계 길이보다 짧아도 마지막 장면을 늘려 정확히 (b-a)초로 맞춘다(아니면 컷마다 소리와 영상이 조금씩 어긋남)
            fc.append(f"[{i}:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop_duration=2,trim=duration={max(0.04, b - a):.3f},"
                      f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p{pad}[v{i}]")
            if has_audio(src):
                at = max(0.0, c["start"] + float(c.get("voice_offset") or 0))
                g = max(0.0, min(3.0, float(c.get("voice_gain") if c.get("voice_gain") is not None else 1)))
                ms = int(at * 1000)
                fc.append(f"[{i}:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                          f"volume={g:.2f},adelay={ms}|{ms}[a{i}]")
                alab.append(f"[a{i}]")
        n = len(clips)
        fc.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vcat]")
        sub_ok = bool(subs) and bool((tl.get("settings") or {}).get("burn_subs"))
        if sub_ok:
            write_subs(subs, os.path.join(work, "subs.srt"), os.path.join(work, "subs.vtt"))
            fc.append(f"[vcat]subtitles=subs.srt:force_style='FontSize={14 if kind == 'preview' else 18},Outline=2,MarginV=40'[vout]")
        else:
            fc.append("[vcat]null[vout]")
        def mixdown(labels, name):
            if not labels:
                fc.append(f"anullsrc=r=48000:cl=stereo,atrim=end={total:.3f}[{name}]")
            else:
                fc.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,apad,atrim=end={total:.3f}[{name}]")
        mixdown(alab, "dlg")
        mlab = []
        for j, (m, local) in enumerate(musics):
            k = n + j
            args += ["-i", local]
            mi, mo = float(m.get("in") or 0), float(m.get("out") or 0)
            dur = max(0.5, mo - mi) if mo > mi else None
            trim = f"atrim=start={mi:.3f}" + (f":end={mo:.3f}" if dur else "")
            fi, fo = float(m.get("fade_in") or 0), float(m.get("fade_out") or 0)
            fades = (f",afade=t=in:d={fi:.2f}" if fi > 0 else "") + (f",afade=t=out:st={max(0, (dur or 0) - fo):.2f}:d={fo:.2f}" if fo > 0 and dur else "")
            g = max(0.0, min(3.0, float(m.get("gain") if m.get("gain") is not None else 0.6)))
            ms = int(max(0.0, float(m.get("start") or 0)) * 1000)
            fc.append(f"[{k}:a]{trim},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo{fades},volume={g:.2f},adelay={ms}|{ms}[m{j}]")
            mlab.append(f"[m{j}]")
        mixdown(mlab, "mus")
        fc.append("[dlg]asplit=2[dlg1][dlg2]")
        fc.append("[mus]asplit=2[mus1][mus2]")
        fc.append("[dlg1][mus1]amix=inputs=2:normalize=0,atrim=end=%.3f[mix]" % total)
        e = ed.get("episode") or {}
        tag = f"EP{int(e.get('number') or 0):02d}"
        out_mp4 = os.path.join(work, f"{tag}.mp4")
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args, "-filter_complex", ";".join(fc),
               "-map", "[vout]", "-map", "[mix]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28" if kind == "preview" else "17",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k" if kind == "preview" else "256k", "-movflags", "+faststart", out_mp4]
        if kind == "final":
            cmd += ["-map", "[dlg2]", "-c:a", "pcm_s16le", os.path.join(work, f"{tag}_대사·현장음.wav"),
                    "-map", "[mus2]", "-c:a", "pcm_s16le", os.path.join(work, f"{tag}_배경음악.wav")]
        else:
            cmd += ["-map", "[dlg2]", "-c:a", "aac", "-b:a", "96k", os.path.join(work, "stem_voice.m4a"),
                    "-map", "[mus2]", "-c:a", "aac", "-b:a", "96k", os.path.join(work, "stem_music.m4a")]
        upd(progress=f"{'미리보기' if kind == 'preview' else '완성본'} 조립 중 ({len(clips)}컷 · {total:.1f}초)")
        progress(f"ffmpeg 조립 {len(clips)}컷 {total:.1f}초")
        rr = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=work)
        if rr.returncode != 0 and sub_ok:
            log(f"자막 굽기 실패, 자막 없이 다시: {(rr.stderr or '')[-200:]}")
            cmd[cmd.index("-filter_complex") + 1] = cmd[cmd.index("-filter_complex") + 1].replace(
                [x for x in fc if x.startswith("[vcat]subtitles")][0], "[vcat]null[vout]")
            rr = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=work)
        if rr.returncode != 0:
            raise RuntimeError(f"조립 실패: {(rr.stderr or '')[-400:]}")
        files = {"seconds": total, "clips": len(clips), "audio_clips": len(alab)}   # 소리가 있는 컷 수(0이면 결과물에 대사 소리 없음)
        srt, vtt = os.path.join(work, f"{tag}_ko.srt"), os.path.join(work, f"{tag}_ko.vtt")
        if subs:
            write_subs(subs, srt, vtt)
        sid = e.get("series_id") or "series"
        base = f"{sid}/E{int(e.get('number') or 0):02d}/edit/{kind}_{rid[:8]}_{uuid.uuid4().hex[:4]}"   # 다시 시도해도 '이미 있음'으로 실패하지 않게
        if kind == "preview":
            files["preview"] = storage_upload(base + ".mp4", out_mp4, "video/mp4")
            files["voice"] = storage_upload(base + "_voice.m4a", os.path.join(work, "stem_voice.m4a"), "audio/mp4")
            files["music"] = storage_upload(base + "_music.m4a", os.path.join(work, "stem_music.m4a"), "audio/mp4")
        else:
            d = os.path.join(episode_dir(ed), f"v{r.get('rev') or 1}_{datetime.now():%m%d_%H%M}")
            os.makedirs(d, exist_ok=True)
            final = os.path.join(d, f"{tag}_완성.mp4")
            shutil.move(out_mp4, final)
            ff("-i", final, "-an", "-c:v", "copy", os.path.join(d, f"{tag}_영상만(무음).mp4"))
            for nm in (f"{tag}_대사·현장음.wav", f"{tag}_배경음악.wav"):
                shutil.move(os.path.join(work, nm), os.path.join(d, nm))
            if subs:
                shutil.copyfile(srt, os.path.join(d, f"{tag}_ko.srt"))
            for c in clips:
                c["code"] = c.get("code") or (takes.get(c["take_id"]) or {}).get("code")
            try:
                premiere_xml(os.path.join(d, f"{tag}_프리미어·다빈치.xml"), f"{tag} {e.get('title') or ''}".strip(),
                             clips, srcs, musics, even(W), even(H), total, os.path.join(d, "소스"))
                files["xml"] = True
            except Exception as ex:
                log(f"프리미어 XML 실패(완성본은 정상): {ex}")
            with open(os.path.join(d, "편집정보.json"), "w", encoding="utf-8") as f:
                json.dump({"episode": e, "rev": r.get("rev"), "seconds": total, "timeline": tl}, f, ensure_ascii=False, indent=2)
            files["folder"] = d
            files["platform"] = PLATFORM
            small = os.path.join(work, "small.mp4")
            s = 640 / max(W, H)
            ff("-i", final, "-vf", f"scale={even(W * s)}:{even(H * s)}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", small)
            files["preview"] = storage_upload(base + ".mp4", small, "video/mp4")
            for key, nm in (("voice", f"{tag}_대사·현장음.wav"), ("music", f"{tag}_배경음악.wav")):
                m4a = os.path.join(work, f"stem_{key}.m4a")
                ff("-i", os.path.join(d, nm), "-c:a", "aac", "-b:a", "96k", m4a)
                files[key] = storage_upload(f"{base}_{key}.m4a", m4a, "audio/mp4")
        if subs:
            files["vtt"] = storage_upload(base + ".vtt", vtt, "text/vtt")
        upd(status="done", progress="완료", files=files)
        if kind == "final":
            notify(f"{tag} 완성본 저장", f"{files['folder']}\n완성 영상·영상만·대사·배경음악·자막이 함께 있습니다")
        shutil.rmtree(work, ignore_errors=True)
        return f"{kind} {len(clips)}컷 {total:.1f}초" + (f" → {files.get('folder')}" if files.get("folder") else "")
    except Exception as ex:
        upd(status="failed", error=str(ex)[:1500])
        raise


EDIT_OPS = """사용할 수 있는 명령(ops):
- {"op":"remove","clip":"c3"}  클립 빼기
- {"op":"move","clip":"c3","to":0}  순서 바꾸기(to = 0부터 시작하는 새 위치)
- {"op":"cut_head","clip":"c3","sec":0.5}  앞부분 잘라내기 / {"op":"cut_tail","clip":"c3","sec":1}  뒷부분 잘라내기
- {"op":"extend_tail","clip":"c3","sec":0.5}  잘라 낸 뒷부분 되살리기(원본 길이 안에서) / {"op":"extend_head",...}
- {"op":"gap","clip":"c3","sec":2}  이 클립 앞 검은 화면(정적) 길이를 sec로
- {"op":"voice_offset","clip":"c3","sec":-0.5}  대사를 화면보다 먼저(음수)/나중(양수)
- {"op":"voice_gain","clip":"c3","gain":1.2}  대사 크기(1 = 그대로)
- {"op":"music_gain","music":"m1","gain":0.4} / {"op":"music_fade","music":"m1","fade_in":2,"fade_out":3} / {"op":"music_start","music":"m1","sec":12.5}
- {"op":"sub_text","sub":"s4","text":"바꾼 자막"} / {"op":"sub_remove","sub":"s4"}"""


def apply_ops(tl, ops):
    clips = tl.setdefault("clips", [])
    find = lambda cid: next((c for c in clips if c.get("id") == cid), None)
    done = []
    for o in ops or []:
        op, c = o.get("op"), find(o.get("clip"))
        sec = float(o.get("sec") or 0)
        try:
            if op == "remove" and c:
                clips.remove(c)
                tl["subs"] = [s for s in tl.get("subs") or [] if s.get("clip_id") != c["id"]]
            elif op == "move" and c:
                clips.remove(c)
                clips.insert(max(0, min(len(clips), int(o.get("to") or 0))), c)
            elif op == "cut_head" and c:
                c["in"] = round(min(float(c["out"]) - 0.3, float(c["in"]) + sec), 3)
            elif op == "cut_tail" and c:
                c["out"] = round(max(float(c["in"]) + 0.3, float(c["out"]) - sec), 3)
            elif op == "extend_head" and c:
                c["in"] = round(max(0.0, float(c["in"]) - sec), 3)
            elif op == "extend_tail" and c:
                c["out"] = round(min(float(c.get("src_len") or c["out"]), float(c["out"]) + sec), 3)
            elif op == "gap" and c:
                c["gap"] = round(max(0.0, min(10.0, sec)), 2)
            elif op == "voice_offset" and c:
                c["voice_offset"] = round(max(-3.0, min(3.0, sec)), 2)
            elif op == "voice_gain" and c:
                c["voice_gain"] = round(max(0.0, min(3.0, float(o.get("gain") or 1))), 2)
            elif op.startswith("music_"):
                m = next((x for x in tl.get("music") or [] if x.get("id") == o.get("music")), None)
                if not m:
                    continue
                if op == "music_gain":
                    m["gain"] = round(max(0.0, min(3.0, float(o.get("gain") or 0.6))), 2)
                elif op == "music_fade":
                    m["fade_in"], m["fade_out"] = float(o.get("fade_in") or m.get("fade_in") or 0), float(o.get("fade_out") or m.get("fade_out") or 0)
                elif op == "music_start":
                    m["start"] = round(max(0.0, sec), 2)
            elif op in ("sub_text", "sub_remove"):
                s = next((x for x in tl.get("subs") or [] if x.get("id") == o.get("sub")), None)
                if not s:
                    continue
                if op == "sub_text":
                    s["text"] = str(o.get("text") or s["text"])
                else:
                    tl["subs"].remove(s)
            else:
                continue
            done.append(o)
        except Exception as ex:
            log(f"편집 명령 건너뜀 {o}: {ex}")
    return done


@task("edit_command")
def t_edit_command(t):
    p = t.get("payload") or {}
    ed = rpc("agent_get_edit", p_token=TOKEN, p_project=p["project_id"])
    tl = (ed.get("project") or {}).get("timeline") or {}
    takes = ed.get("takes") or {}
    clips, total, _ = tl_layout(tl)
    table = [{"clip": c["id"], "code": c.get("code") or (takes.get(c.get("take_id")) or {}).get("code"), "position_sec": c["start"],
              "length": c["dur"], "in": c["in"], "out": c["out"], "src_len": c.get("src_len"), "gap_before": c.get("gap") or 0,
              "voice_offset": c.get("voice_offset") or 0, "voice_gain": c.get("voice_gain", 1),
              "dialogue": ((takes.get(c.get("take_id")) or {}).get("dialogue") or "")[:120]} for c in clips]
    music = [{k: m.get(k) for k in ("id", "name", "start", "in", "out", "gain", "fade_in", "fade_out")} for m in tl.get("music") or []]
    subs = [{k: s.get(k) for k in ("id", "clip_id", "text")} for s in (tl.get("subs") or [])][:200]
    system = f"""너는 드라마 편집 보조다. 대표의 말을 타임라인 명령으로 바꾼다. 컷 이름(S03 C02, E01-S03-C02, 세 번째 컷 등)은 code와 position으로 찾는다.
애매하면 가장 그럴듯한 해석 하나만 하고 summary에 그렇게 해석했다고 적는다. 할 수 없는 요청(색보정, 화면 효과 등)은 ops 없이 summary로 설명한다.
{EDIT_OPS}
답 형식: <json>{{"ops": [...], "summary": "무엇을 바꿨는지 한두 줄(한국어)"}}</json>"""
    user = f"[대표 지시]\n{p.get('text')}\n\n[클립 {len(table)}개, 전체 {total:.1f}초]\n{json.dumps(table, ensure_ascii=False)}\n\n[음악]\n{json.dumps(music, ensure_ascii=False)}\n\n[자막]\n{json.dumps(subs, ensure_ascii=False)}"
    text, _ = claude("claude-sonnet-5-5", system, user, max_tokens=3000, stage="edit", episode_id=(ed.get("episode") or {}).get("id"), note="말로 편집")
    res = pick_json(text)
    done = apply_ops(tl, res.get("ops"))
    if done:
        rpc("agent_save_edit", p_token=TOKEN, p_project=p["project_id"], p_timeline=tl)
    return (res.get("summary") or "") + f" (명령 {len(done)}개 적용)"


def run_task(tk):
    kind = tk["kind"]
    CURRENT["task"] = tk
    log(f"제작 작업 시작: {kind} ({tk['id']})")
    try:
        result = TASKS[kind](tk)
        rpc("agent_task_update", p_token=TOKEN, p_id=tk["id"], p_status="done", p_progress="완료", p_result=str(result or "완료"))
        log(f"제작 작업 완료: {kind} · {result}")
    except Retry as r:
        log(f"잠시 뒤 다시: {kind}: {r.why}")
        try:   # 기다림은 시도 횟수에 세지 않는다(DB 650 이후). 옛 DB면 예전 방식으로
            rpc("agent_task_update", p_token=TOKEN, p_id=tk["id"], p_status="queued", p_progress=r.why[:200], p_retry_in=int(r.seconds), p_wait=True)
        except Exception:
            rpc("agent_task_update", p_token=TOKEN, p_id=tk["id"], p_status="queued", p_progress=r.why[:200], p_retry_in=int(r.seconds))
    except Exception as e:
        log(f"제작 작업 실패: {kind}: {e}\n{traceback.format_exc()}")
        # 일시적인 문제(인터넷 끊김·서비스 붐빔·5xx·시간 초과·재시작)는 실패로 띄우지 않고 조용히 다시 한다(최대 3번)
        if is_transient(str(e)) and (tk.get("attempts") or 1) < 4:   # 영상 생성도: 다시 집으면 이미 맡긴 요청을 이어서 기다림(새 결제 없음)
            wait = 30 * (tk.get("attempts") or 1)
            if kind in ("photo_design", "photo_set"):
                did = (tk.get("payload") or {}).get("design_id")
                try:
                    rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "training" if kind == "photo_set" else "queued"})
                except Exception:
                    pass
            rpc("agent_task_update", p_token=TOKEN, p_id=tk["id"], p_status="queued", p_progress=f"일시적인 문제로 {wait}초 뒤 자동으로 다시 합니다({str(e)[:80]})", p_retry_in=wait)
            return
        retry = 120 if (tk.get("attempts") or 1) < 2 and kind in ("store", "video_review") else None
        rpc("agent_task_update", p_token=TOKEN, p_id=tk["id"], p_status="failed", p_error=str(e)[:1500], p_retry_in=retry)
        # 바이블 보조 작업은 하나하나 알림을 울리지 않는다: 지시 탭 적용 대기함 '실패'에 원인과 함께 모이고 거기서 다시 시도
        if kind not in QUIET_FAIL and (kind in ("writers_room", "director") or retry is None):
            notify(f"작업 실패: {TASK_NAME.get(kind, kind)}", f"원인: {str(e)[:260]}\n이렇게 하세요: {fail_hint(str(e))}\n(제작 탭 빨간 상자·지시 탭 '실패'에서 바로 다시 시도할 수 있습니다)", "warning")
    finally:
        CURRENT["task"] = None


def is_transient(e):
    """다시 하면 대부분 되는 오류인가(인터넷·서비스 붐빔·서버 오류·시간 초과).
    단, Higgsfield 생성·얼굴 학습이 '시간 초과'된 것은 다시 하면 새로 결제되므로 자동 반복하지 않는다(대표가 확인 후 다시 시도)."""
    if re.search(r"(생성|학습) 시간 초과", e):
        return False
    return bool(re.search(r"(URLError|Connection|connection|reset|refused|Temporary failure|Name or service|timed out|timeout|시간 초과|"
                          r"(?:실패|failed|error|Error|오류|HTTP|status|code|응답)\D{0,10}(429|500|502|503|504|520|522|524|529)\b|overloaded|rate.?limit|SSL|EOF occurred|RemoteDisconnected|IncompleteRead|"
                          r"Remote end closed|BrokenPipe|주소를 찾지 못함|후보가 만들어지지 않았습니다)", e))


def fail_hint(e):
    """실패 알림에 붙일 '대표가 할 일' 한 줄(대시보드 explainFail과 같은 기준)."""
    rules = [(r"확정된 대본이 없", "대본 탭에서 정리본(또는 원고)을 확정한 뒤 다시 시도"),
             (r"키가 없|401|403|Unauthorized|invalid.*key", "설정 → API 키를 다시 넣고 '연결 시험' 후 다시 시도"),
             (r"402|잔액|credit|insufficient|quota|balance", "원인에 적힌 서비스(Higgsfield·ElevenLabs·Claude) 충전 후 다시 시도"),
             (r"429|rate.?limit|overloaded|529", "몇 분 뒤 다시 시도(서비스가 잠시 붐빔)"),
             (r"시간 초과|timeout|timed out", "다시 시도, 계속되면 에이전트 탭에서 그 컴퓨터 재시작"),
             (r"nsfw|safety|moderation", "컷 카드 '감독에게 수정 요청'으로 표현을 순화한 뒤 다시 생성"),
             (r"ffmpeg", "에이전트 탭에서 그 컴퓨터 재시작 후 다시 시도"),
             (r"묘사", "바이블에서 그 항목 묘사를 한글로 자세히 고치기(고치면 자동으로 다시 돎)"),
             (r"JSON|형식|만들지 못했", "다시 시도(대부분 해결). 두 번 이상 같으면 원인 문구를 개발에 전달")]
    for pat, how in rules:
        if re.search(pat, e, re.I):
            return how
    return "다시 시도. 같은 원인으로 또 실패하면 원인 문구를 개발(Claude)에 전달"


QUIET_FAIL = {"coord_review", "bible_prompt", "photo_describe", "bible_place", "photo_design", "prep_cut"}
TASK_NAME = {"balance_check": "잔액 조회", "cut_review": "컷 감수(감정·영상)", "coord_review": "코디네이터 감수", "upscale": "1080p 업그레이드", "bgm": "배경음악 만들기", "bible_suggest": "작가실 추천(스타일·음악)", "api_check": "실제 연결 점검", "bible_prompt": "코디네이터: 영어 프롬프트", "bible_extract": "대본에서 필요 항목 찾기", "photo_describe": "사진 보고 묘사 쓰기", "bible_place": "넣을 화·씬 추천", "photo_design": "사진 디자인", "photo_set": "얼굴 학습·기준 사진", "prep_cut": "컷 준비(사진·목소리)", "assemble": "편집본 조립", "edit_command": "말로 편집", "voice_cast": "시리즈 목소리 설계", "voice_design": "목소리 후보 만들기", "voice_save": "목소리 저장", "writers_room": "작가실 정리", "director": "감독 컷 설계", "generate": "영상 생성", "store": "윈도우 보관", "video_review": "영상 감수"}


# ------------------------------------------------------------------ 본체
def main():
    global DEVICE_ID
    log(f"NYX 에이전트 v{VERSION} 시작 ({PLATFORM}, {socket.gethostname()})")
    keep_awake()
    os.makedirs(os.path.join(HOME, "work"), exist_ok=True)   # 새로 설치한 컴퓨터에서 첫 대사 파일 쓰기가 실패하던 문제
    last_error, paused = None, False
    info = {"hostname": socket.gethostname(), "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(), "started_at": STARTED, "jobs": sorted(HANDLERS), "tasks": sorted(TASKS),
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
                     p_state="busy" if lanes_busy() else ("error" if last_error else ("paused" if paused else "idle")),
                     p_current_job=lanes_busy(), p_last_error=last_error, p_info=info)
            DEVICE_ID, paused = hb.get("device_id"), bool(hb.get("paused"))
            PULSE["last"] = time.time()
            QUICK["paused"] = paused
            if PULSE.get("restart"):   # 신호 줄이 받아 둔 재시작·업데이트 명령
                what = PULSE["restart"]; PULSE["restart"] = False
                if what == "update":
                    check_update(force=True)
                raise Restart()
            if not QUICK.get("started"):   # 첫 연결이 된 뒤에 일 줄을 연다. 모든 제작 일은 줄에서 동시에 하고, 본체는 신호·명령·업데이트만 맡는다
                QUICK["started"] = True
                threading.Thread(target=pulse, daemon=True).start()
                lanes = [(QUICK_KINDS, "busy", "짧은 일 줄", QUICK_WORKERS),
                         (PHOTO_KINDS, "photo", "사진 줄", 2),        # 서로 다른 인물 후보·얼굴 학습
                         (GEN_KINDS, "gen", "영상 줄", 3),
                         (PREP_KINDS, "prep", "장면 사진 줄", 2),
                         (DESIGN_KINDS, "design", "컷 설계·작가실 줄", 2),
                         (VOICE_KINDS, "voice", "목소리 줄", 2),
                         (work_kinds(), "work", "일반 줄", WORK_WORKERS),   # 감수·보관·배경음악 등
                         (HEAVY_KINDS, "heavy", "편집·업스케일 줄", 1)]
                for kinds, slot, label, n in lanes:
                    for i in range(n):
                        threading.Thread(target=lane, args=(kinds, f"{slot}{i}", f"{label} {i + 1}"), daemon=True,
                                         name=f"{slot}{i}").start()
                        time.sleep(0.3)   # 줄마다 대기열 확인 시점을 흩어 둔다
                log("일 줄 시작: " + ", ".join(f"{label} {n}개" for _, _, label, n in lanes))
            last_error = None
            if os.path.exists(PENDING):  # 새 버전이 정상적으로 연결됨 → 업데이트 확정
                os.remove(PENDING)
                log(f"v{VERSION} 업데이트 확정")
            for c in hb.get("commands") or []:
                handle_command(c)
            if not paused and not QUICK.get("draining"):
                j = rpc("agent_claim_job", p_token=TOKEN, p_types=sorted(HANDLERS))
                if j and j.get("id"):
                    busy = True
                    run_job(j)
        except Restart:
            drain()
            log("재시작")
            sys.exit(0)  # 자동 시작 장치(맥 launchd / Windows 실행기)가 다시 켭니다
        except Exception as e:
            last_error = str(e)[:500]
            log(f"오류: {last_error}")
            if "invalid agent token" in last_error:
                log("토큰이 맞지 않습니다. 대시보드에서 토큰을 다시 발급하고 설치 명령을 다시 실행하세요.")
                time.sleep(300)
                continue
        # 업데이트 확인은 일이 계속 있어도 일 사이마다(10분 간격) 한다 — 바쁘면 영영 옛 버전에 머물던 문제
        try:
            if check_update():
                drain()
                log("새 버전으로 재시작")
                sys.exit(0)
        except SystemExit:
            raise
        except Exception as e:
            log(f"업데이트 확인 실패(다음에 다시 시도): {e}")
        if not busy:
            time.sleep(BEAT_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("사용자가 중지함")
