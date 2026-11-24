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
VERSION = "1.10.4"

import base64
import glob
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


CURRENT = {"task": None}


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
    for attempt in range(6):
        try:
            code, data = http_json("https://api.anthropic.com/v1/messages", headers, "POST", body, timeout=900)
        except Exception as e:
            code, data = 0, {"error": {"message": str(e)}}
        if code == 200:
            break
        msg = ((data or {}).get("error") or {}).get("message", "") if isinstance(data, dict) else ""
        if code in (0, 408, 429, 500, 502, 503, 504, 529) and attempt < 5:
            wait = min(120, 10 * (2 ** attempt))
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


def lessons_text(ctx, limit=30):
    rows = [f"- ({l.get('category')}) {l.get('rule')}" + (f"  · 실패 예: {l.get('bad_example')}" if l.get("bad_example") else "")
            for l in (ctx.get("lessons") or [])[:limit]]
    return "\n".join(rows) or "(아직 없음)"


def notes_text(ctx):
    rows = [f"- {('씬 ' + str(n['scene_no'])) if n.get('scene_no') else '화 전체'} [{'필수' if n.get('priority') == 'must' else '참고'}"
            f"{' · 중요 장면' if n.get('important') else ''}] {n.get('content')}" for n in (ctx.get("notes") or [])]
    return "\n".join(rows) or "(없음)"


def guide(ctx, *names):
    g = ctx.get("guidelines") or {}
    return "\n\n".join(f"[{n} 지침]\n{g[n]}" for n in names if g.get(n))


# ---------------- 작가실
@task("writers_room")
def t_writers_room(t):
    ep = t["episode_id"]
    p = t.get("payload") or {}
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=p.get("source_version_id"))
    src = (ctx.get("script") if p.get("source_version_id") else None) or ctx.get("latest_draft") or ctx.get("script")
    if not src or not src.get("content"):
        raise RuntimeError("작가실이 읽을 원고가 없습니다. 대본 탭에서 원고를 넣어 주세요")
    wr = rpc("agent_writers_room_context", p_token=TOKEN, p_episode=ep) or {}
    model = setting(ctx, "generation", "claude_writer", "claude-opus-5-5")
    e = ctx.get("episode") or {}
    system = (guide(ctx, "작가실") or "너는 드라마 작가실이다. 흐름은 그대로 두고 감정선과 감정 연결 장치만 다듬는다.") + f"""

[시리즈 바이블 — 인물·장소 이름은 반드시 이대로]
{bible_text(ctx)}

[답 형식 — 반드시 지킨다]
<draft>
(제작용 정리본 전체. 원고의 모든 씬을 순서대로. 씬 머리 형식: S#번호 | 시간 구간 | 장소 | 낮·밤 / 등장: 인물)
</draft>
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

[대표가 승인한 흐름 변경 — 이번 정리본에 반영]
{json.dumps([{k: a.get(k) for k in ('scene_no', 'kind', 'proposed_text', 'reason')} for a in approved], ensure_ascii=False) if approved else '(없음)'}

[원고]
{src['content']}"""
    progress(f"작가실이 {e.get('number')}화 원고를 읽고 감정선을 정리하는 중")
    text, _ = claude(model, system, user, max_tokens=20000, stage="script_revision", episode_id=ep, note="작가실 정리본")
    script = pick_tag(text, "draft")
    meta = pick_json(text)
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
    if wr.get("auto_confirm") is True and not props and not ppl:   # PPL을 넣고 축약한 수정본은 늘 대표가 읽고 확정
        confirmed = rpc("agent_confirm_script", p_token=TOKEN, p_id=vid, p_summary=summary)
        if p.get("auto_director", True):
            rpc("agent_add_task", p_token=TOKEN, p={"kind": "director", "episode_id": ep, "payload": {"script_version_id": vid}})
    else:
        notify(f"{e.get('number')}화 작가실 {'PPL 반영 수정본' if ppl else '정리본'} 도착", summary[:300] + "\n대본 탭에서 확인 후 확정해 주세요", "warning" if props or ppl else "info")
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
{lessons_text(ctx)}

[컷 설계 규칙]
- 씬 번호는 대본에 나온 순서대로 1부터. 컷은 4~15초. 대사는 한 글자도 바꾸지 않는다.
- 표정은 2~3초마다 바뀌도록 expression_beats를 컷 길이 전체에 빈틈없이 적는다.
- 프롬프트: 인물 고정 묘사(바이블), 의상, 장소, 조명, 카메라, 시간대별 동작·표정(0-3s, 3-5s …), 대사(화자와 한국어 원문, 톤).
- 소리: 대사와 현장음만. 배경음악은 넣지 않는다 → 프롬프트 끝에 "No background music, no score. Dialogue and natural ambient sound only." 를 반드시 붙인다(편집에서 음악을 따로 깐다).
- 화면 비율 {asp}. 자막·글자를 화면에 넣지 않는다(화면 글자가 대본에 있으면 summary에만 적는다).
- reference_names에는 이 컷 화면에 나오는 바이블 항목 이름을 바이블 표기 그대로(괄호·수식어 붙이지 말 것): 인물 최대 3, 장소 또는 배경 최대 1, PPL·중요 소품 최대 1. 사진이 없는 항목도 넣는다(제작 때 임시 기준 사진을 만들어 고정한다).
- voice_lines: 대사 한 줄마다 화자·대사·감정 지시. tags는 ElevenLabs v3 영어 감정 태그(예: [nervous] [whispers] [excited] [sighs] [crying] [laughs])로, direction은 한국어로 크기·높낮이·속도 변화를 적는다.
- frame_first: 모든 컷. 첫 0.5초의 정지 장면(얼굴·의상·배경 고정의 기준). use_end_frame은 컷 안에서 자세·위치·감정이 크게 바뀌어 끝 상태가 중요할 때만 true(일어서기·문 나가기·웃다가 울기 등). 대부분 false.
- continue_prev: 같은 씬에서 앞 컷과 동작이 바로 이어지면 true(앞 컷 마지막 장면에서 시작).

[답 형식]
<json>{{"cuts": [{CUT_SCHEMA}]}}</json>"""


def review_cuts(ctx, ep, cuts, model):
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
    text, _ = claude(model, system, "[대본]\n" + (ctx.get("script") or {}).get("content", "")[:60000]
                     + "\n\n[감독 컷 설계]\n" + json.dumps(cuts, ensure_ascii=False),
                     max_tokens=8000, stage="prompt_review", episode_id=ep, note="감정감수자 컷 감수")
    return pick_json(text)


def est_cut_usd(ctx, seconds, refs):
    res = setting(ctx, "generation", "resolution", "480p")
    model = f"seedance-2.5-i2v-{res}"   # 컷 설계에서 첫 장면 사진을 만들어 사진→영상으로 만든다
    u = price("higgsfield", model, "per_second") or price("higgsfield", f"seedance-2.5-{'ref' if refs else 't2v'}-{res}", "per_second")
    return round(u * seconds, 4) if u else None


def has_images(ctx, names):
    """감독이 고른 이름을 바이블 표기로 맞추고, 바이블에 없는 이름은 뺀다(사진 유무와 상관없이 남긴다)."""
    out = []
    for n in names or []:
        b = bible_entry(ctx, n)
        if b and b["name"] not in out:
            out.append(b["name"])
    return out


@task("director")
def t_director(t):
    ep = t["episode_id"]
    p = t.get("payload") or {}
    if p.get("cut_id"):
        return director_revise(t, p["cut_id"])
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=p.get("script_version_id"))
    script = ctx.get("script")
    if not script:
        raise RuntimeError("확정된 대본이 없습니다. 대본 탭에서 정리본을 확정해 주세요")
    e = ctx.get("episode") or {}
    dmodel = setting(ctx, "generation", "claude_director", "claude-opus-5-5")
    rmodel = setting(ctx, "generation", "claude_reviewer", "claude-sonnet-5-5")
    scenes = p.get("scenes")
    want = f"씬 {', '.join(map(str, scenes))}만" if scenes else "모든 씬을"
    progress(f"감독이 {e.get('number')}화 {want} 컷으로 나누는 중")
    user = f"""{e.get('number')}화 「{e.get('title') or ''}」 대본 v{script.get('version_no')}의 {want} 컷으로 설계해 주세요.
컷 code는 E{int(e.get('number') or 0):02d}-S씬번호2자리-C컷번호2자리.

[대표 연출 메모 — 필수는 반드시 반영]
{notes_text(ctx)}

[대본]
{script['content']}"""
    text, _ = claude(dmodel, director_system(ctx), user, max_tokens=24000, stage="cut_card", episode_id=ep, note="감독 컷 설계")
    cuts = pick_json(text).get("cuts") or []
    if scenes:
        cuts = [c for c in cuts if int(c.get("scene_no") or 0) in set(int(s) for s in scenes)]
    if not cuts:
        raise RuntimeError("감독이 컷을 만들지 못했습니다")
    progress(f"감정감수자가 컷 {len(cuts)}개를 감수하는 중")
    rv = review_cuts(ctx, ep, cuts, rmodel)
    verdicts = {(int(r.get("scene_no") or 0), int(r.get("cut_no") or 0)): r for r in rv.get("cuts") or []}
    fixes = [c for c in cuts if (verdicts.get((int(c["scene_no"]), int(c["cut_no"]))) or {}).get("verdict") == "fix"]
    for l in rv.get("lessons") or []:
        try:
            rpc("agent_add_lesson", p_token=TOKEN, p_lesson={**l, "series_id": (ctx.get("series") or {}).get("id"), "sources": ["감정감수자"]})
        except Exception as ex:
            log(f"교훈 기록 실패: {ex}")
    if fixes:
        progress(f"감독이 감수 의견 {len(fixes)}건을 반영해 고치는 중")
        fix_text = json.dumps([{"cut": c, "review": verdicts[(int(c['scene_no']), int(c['cut_no']))]} for c in fixes], ensure_ascii=False)
        text, _ = claude(dmodel, director_system(ctx), f"감정감수자 의견을 반영해 아래 컷들만 고쳐서 같은 형식으로 다시 주세요.\n{fix_text}\n\n[대본]\n{script['content']}",
                         max_tokens=12000, stage="cut_card", episode_id=ep, note="감독 감수 반영")
        fixed = {(int(c.get("scene_no") or 0), int(c.get("cut_no") or 0)): c for c in pick_json(text).get("cuts") or []}
        cuts = [fixed.get((int(c["scene_no"]), int(c["cut_no"])), c) for c in cuts]
    by_scene = {}
    for c in cuts:
        k = (int(c["scene_no"]), int(c["cut_no"]))
        r = verdicts.get(k) or {}
        c["review"] = {"verdict": "revised" if r.get("verdict") == "fix" else (r.get("verdict") or "pass"),
                       "issues": r.get("issues") or [], "fix": r.get("fix")}
        c["reference_names"] = has_images(ctx, c.get("reference_names"))
        c["seconds"] = max(4, min(15, int(c.get("seconds") or 10)))
        c["resolution"] = setting(ctx, "generation", "resolution", "480p")
        c["est_usd"] = est_cut_usd(ctx, c["seconds"], c["reference_names"])
        by_scene.setdefault(int(c["scene_no"]), []).append(c)
    n = 0
    for sc, cs in sorted(by_scene.items()):
        n += rpc("agent_save_cuts", p_token=TOKEN, p_episode=ep, p_version=script["id"], p_scene=sc, p_cuts=cs) or 0
    total = sum(c.get("est_usd") or 0 for c in cuts)
    if setting(ctx, "generation", "auto_prep", True):
        for sc in sorted(by_scene):
            rpc("agent_add_task", p_token=TOKEN, p={"kind": "prep_cut", "episode_id": ep, "scene_no": sc, "payload": {}})
    notify(f"{e.get('number')}화 컷 설계 완료", f"씬 {len(by_scene)}개 · 컷 {n}개 · 감수 수정 {len(fixes)}건\n"
           f"영상 생성 예상 ${total:.2f}. 제작 탭에서 확인하고 생성 승인을 눌러 주세요.")
    return f"씬 {len(by_scene)}개 · 컷 {n}개 설계 (감수 수정 {len(fixes)}건) · 생성 예상 ${total:.2f}"


def director_revise(t, cut_id):
    cut = rpc("agent_get_cut", p_token=TOKEN, p_id=cut_id)
    ep = cut["episode_id"]
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=cut.get("script_version_id"))
    last = (cut.get("takes") or [{}])[-1] if cut.get("takes") else {}
    dmodel = setting(ctx, "generation", "claude_director", "claude-opus-5-5")
    progress(f"감독이 {cut.get('code')} 프롬프트를 고치는 중")
    user = f"""아래 컷 하나를 고쳐서 같은 형식(cuts 배열에 1개)으로 주세요. 대사는 바꾸지 않습니다.
[지금 컷]
{json.dumps({k: cut.get(k) for k in ('scene_no', 'cut_no', 'code', 'scene_heading', 'seconds', 'summary', 'dialogue', 'emotion', 'expression_beats', 'camera', 'reference_names', 'prompt')}, ensure_ascii=False)}
[대표 수정 요청]
{cut.get('fix_request') or '(없음)'}
[지난 영상 감수 결과]
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
    rpc("agent_update_cut", p_token=TOKEN, p_id=cut_id, p={
        "status": "designed", "prompt": new["prompt"], "expression_beats": new.get("expression_beats") or cut.get("expression_beats"),
        "reference_names": refs, "director_note": new.get("director_note"), "est_usd": est_cut_usd(ctx, secs, refs), "clear_fix": True,
        "voice_lines": new.get("voice_lines") or cut.get("voice_lines") or [], "frame_first": new.get("frame_first") or {"prompt": (cut.get("frame_first") or {}).get("prompt")},
        "frame_last": new.get("frame_last") or {}, "use_end_frame": bool(new.get("use_end_frame")), "continue_prev": bool(new.get("continue_prev")), "prep_status": "none",
        "review": {"verdict": "revised", "issues": [], "fix": "대표 요청·영상 감수 반영"}})
    if setting(ctx, "generation", "auto_prep", True):
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "prep_cut", "episode_id": ep, "scene_no": cut.get("scene_no"), "cut_id": cut_id, "payload": {"force": True}})
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
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    os.replace(tmp, dest)
    return dest


# ---------------- ffmpeg (uv로 자동 준비, 관리자 권한 불필요)
FFMPEG = {"path": None}


def uv_path():
    cands = [shutil.which("uv"), os.path.expanduser("~/.local/bin/uv"), os.path.expanduser("~/.local/bin/uv.exe"),
             os.path.join(os.environ.get("USERPROFILE", ""), ".local", "bin", "uv.exe"), "/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
    return next((c for c in cands if c and os.path.exists(c)), None)


def ffmpeg():
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


def write_scene_info(d, take, files):
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)


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
def hf_auth_headers():
    cred = higgsfield_cred(secrets("higgsfield_key_id", "higgsfield_key_secret"))
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
    for b in ctx.get("bible") or []:
        if b.get("category") in ("character", "voice"):
            nm = b.get("name") or ""
            base = re.sub(r"\s*\(.*?\)\s*", "", nm)
            if not (speaker == nm or speaker == base or speaker.startswith(base) or base.startswith(speaker)):
                continue
            if b.get("voice_id"):
                return b["voice_id"], nm
            twin = bible_entry(ctx, b.get("same_as")) if b.get("same_as") else None   # 동일인의 목소리를 물려받음
            if twin and twin.get("voice_id"):
                return twin["voice_id"], nm
    return None, None


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
SOUL2 = "higgsfield-ai/soul/v2/standard"
# Nano Banana Pro의 Higgsfield API 경로는 공개 문서에 없어서, 내 키로 후보 경로를 직접 두드려 찾는다.
# 프롬프트 없이 보내면 실제 생성은 되지 않고(필수값 누락) 경로가 있으면 400/422, 없으면 404가 온다.
NANO_PRO_CANDIDATES = ["google/nano-banana-pro/edit", "google/nano-banana-pro", "nano-banana-pro/edit", "nano-banana-pro",
                       "google/nano-banana-pro/image-to-image", "google/gemini-3-pro-image/edit", "google/gemini-3-pro-image"]


def hf_probe(path):
    code, data = http_json(f"https://api.higgsfield.ai/{path}", {**hf_auth_headers(), "Content-Type": "application/json",
                           "Accept": "application/json"}, "POST", {}, timeout=30)
    return code, data


def resolve_image_model(gen):
    """장면 사진 모델: 설정에 경로가 있으면 그대로, 'auto'/비어 있으면 Nano Banana Pro를 찾아 쓰고 없으면 Grok Imagine."""
    want = (gen.get("image_model") or "auto").strip().strip("/")
    if want and want != "auto":
        return want
    cache = os.path.join(HOME, "hf_models.json")
    try:
        c = json.load(open(cache))
        if c.get("nano_pro") or time.time() - c.get("at", 0) < 86400:
            return c.get("nano_pro") or GROK_IMAGE
    except Exception:
        pass
    found = ""
    for p in NANO_PRO_CANDIDATES:
        try:
            code, data = hf_probe(p)
        except Exception:
            continue
        log(f"Nano Banana Pro 경로 확인: {p} → {code}")
        if code in (400, 422):
            found = p
            break
    with open(cache, "w") as f:
        json.dump({"nano_pro": found, "at": time.time()}, f)
    if not found:
        log("Nano Banana Pro 경로를 찾지 못해 Grok Imagine 2.0으로 장면 사진을 만듭니다(설정 → 제작에 경로를 넣으면 그걸 씁니다)")
    return found or GROK_IMAGE


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
    code, data = http_json(f"https://api.higgsfield.ai/{path.strip('/')}", hdr, "POST", body, timeout=120)
    if code not in (200, 201, 202) or not isinstance(data, dict):
        raise RuntimeError(f"{what} 요청 실패 {code}: {str(data)[:300]}")
    if find_image_urls(data.get("images") or []) or data.get("status") == "completed":
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
            raise RuntimeError(f"{what} 생성 {s}")
        if s == "completed":
            return st
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
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(15)
        try:
            _, st = http_json(f"https://api.higgsfield.ai/v1/custom-references/{sid}", {**hf_auth_headers(), "Accept": "application/json"}, timeout=60)
        except Exception:
            continue
        s = (st or {}).get("status")
        if s == "completed":
            return sid
        if s in ("failed", "canceled"):
            raise RuntimeError(f"얼굴 학습 실패: {str(st)[:200]}")
        progress(f"얼굴 학습 중… ({s or '대기'})")
    raise RuntimeError("얼굴 학습 시간 초과")


def hf_image(prompt, refs, aspect, model_path):
    """Higgsfield 장면 사진(참고 사진으로 얼굴·의상·장소 고정)."""
    model_path = model_path or GROK_IMAGE
    body = {"prompt": prompt, "aspect_ratio": aspect}
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
        raise RuntimeError(f"사진 요청 실패 {code}: {str(data)[:300]}")
    rid = data.get("request_id") or data.get("id")
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
            raise RuntimeError(f"사진 생성 {s}")
        if s == "completed":
            url = find_image_url(st)
            if not url:
                raise RuntimeError(f"사진 주소를 찾지 못함: {str(st)[:200]}")
    if not url:
        raise RuntimeError("사진 생성 시간 초과")
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


def ensure_temp_refs(ctx, names, gen, ep=None):
    """컷에 나오는 바이블 항목에 사진이 하나도 없으면, 작가실 묘사로 사진 1장을 만들어 '임시 기준 사진'으로 고정한다.
    그래야 임의로 정해진 모습이라도 모든 컷에서 같게 나온다. 대표가 사진을 올리면 그쪽이 우선."""
    for n in dict.fromkeys(names or []):
        b = bible_entry(ctx, n)
        if not b or b.get("category") not in PHOTO_CATS or b.get("images") or b.get("temp_image") or not b.get("entry_id"):
            continue
        twin = bible_entry(ctx, b.get("same_as")) if b.get("same_as") else None
        if twin and (twin.get("images") or twin.get("temp_image")):
            continue
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
            log_cost("keyframe", "higgsfield", model, {"images": 1}, episode_id=ep, note=f"임시 기준 사진: {n}", outcome="success")
            log(f"'{n}' 임시 기준 사진 고정")
        except Exception as ex:
            log(f"'{n}' 임시 기준 사진 실패(묘사만으로 진행): {ex}")


def bible_ref_urls(ctx, names, limit=4, per=1):
    bl = ctx.get("bible") or []
    imgs = {b["name"]: [("bible", p) for p in (b.get("images") or [])] for b in bl}
    for b in bl:   # 대표 사진이 없으면 임시 기준 사진(자동), 그것도 없으면 동일인의 사진
        if not imgs.get(b["name"]) and (b.get("temp_image") or {}).get("path"):
            imgs[b["name"]] = [("takes", b["temp_image"]["path"])]
    for b in bl:
        if not imgs.get(b["name"]) and b.get("same_as"):
            imgs[b["name"]] = imgs.get(b["same_as"]) or []
    out = []
    for n in names or []:
        if n not in imgs:
            b = bible_entry(ctx, n)
            n = b["name"] if b else n
        for bucket, p in imgs.get(n, [])[:per]:
            try:
                out.append(storage_signed_url(bucket, p, 7200))
            except Exception as ex:
                log(f"참고 사진 주소 실패({n}): {ex}")
    return out[:limit]


def bible_ref_blocks(ctx, names, limit=4):
    """감수용: 바이블 기준 사진을 Claude에게 보여 준다(작게)."""
    blocks = []
    for n, url in zip(names or [], bible_ref_urls(ctx, names, limit)):
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
    cuts = [c for c in cuts if c and c.get("status") in ("designed", "failed", "hold") and (p.get("force") or c.get("prep_status") in ("none", "failed", "fix"))]
    if not cuts:
        return "준비할 컷이 없습니다"
    ctx = rpc("agent_context", p_token=TOKEN, p_episode=ep, p_version=cuts[0].get("script_version_id"))
    gen = (ctx.get("settings") or {}).get("generation") or {}
    sid = ((ctx.get("series") or {}).get("id")) or "series"
    enum = int((ctx.get("episode") or {}).get("number") or 0)
    done = []
    for c in cuts:
        code = c.get("code") or f"S{c['scene_no']}C{c['cut_no']}"
        rpc("agent_update_cut", p_token=TOKEN, p_id=c["id"], p={"prep_status": "preparing"})
        base = f"{sid}/E{enum:02d}/S{int(c['scene_no']):02d}/prep/C{int(c['cut_no']):02d}_{uuid.uuid4().hex[:6]}"
        problems = []
        # ② 대사 미리 듣기
        lines = c.get("voice_lines") or parse_dialogue(c.get("dialogue"))
        for i, ln in enumerate(lines):
            vid, vname = voice_for(ctx, ln.get("speaker") or "")
            if not vid:
                ln["error"] = "바이블에 이 인물의 목소리가 없습니다(바이블 → 목소리 디자인)"
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
                    if imodel == GROK_IMAGE:
                        raise
                    log(f"{imodel} 실패, Grok Imagine으로 다시: {ex1}")
                    imodel = GROK_IMAGE
                    url = hf_image(fp, refs, aspect, imodel)
                local = os.path.join(HOME, "work", f"kf_{c['id']}_{key}.img")
                fetch_to(url, local)
                jpg = local + ".jpg"
                ff("-i", local, "-q:v", "2", jpg)
                fr.update({"prompt": fprompt, "path": storage_upload(f"{base}_{key}.jpg", jpg, "image/jpeg"), "status": "ready", "error": None})
                frames[key] = jpg
                os.remove(local)
                log_cost("keyframe", "higgsfield", "nano-banana-pro" if "nano-banana-pro" in imodel else imodel, {"images": 1}, episode_id=ep, scene_no=c["scene_no"],
                         cut_id=code, outcome="success", note=f"{'첫' if key == 'frame_first' else '끝'} 장면 사진")
            except Exception as ex:
                fr.update({"prompt": fprompt, "status": "failed", "error": str(ex)[:300]})
                problems.append(f"{'첫' if key == 'frame_first' else '끝'} 장면 사진 실패")
            c[key] = fr
        # 사전 감수: 영상감수자(사진 ↔ 바이블) + 감정감수자(목소리 ↔ 감정선)
        review = {}
        if frames or any(ln.get("analysis") for ln in lines):
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
                i = int(ft.get("line") or 0)
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
                                                   "payload": {"force": True, "second_pass": True}})
        done.append(f"{code}:{'다시 준비' if fixed else status}")
    ready = sum(1 for d in done if d.endswith(":ready"))
    if not t.get("cut_id") or p.get("second_pass"):
        notify(f"{enum}화 컷 준비 {ready}/{len(done)}", "제작 탭에서 장면 사진을 보고 대사를 들어 본 뒤 생성 승인을 눌러 주세요")
    return "컷 준비: " + ", ".join(done)


@task("generate")
def t_generate(t):
    cut = rpc("agent_get_cut", p_token=TOKEN, p_id=t["cut_id"])
    if not cut:
        raise RuntimeError("컷을 찾지 못했습니다")
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
            if waiting and (t.get("attempts") or 1) < 3:
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
    route = "image-to-video" if first_url else "reference-to-video" if refs else "text-to-video"
    body = {"prompt": cut["prompt"], "duration": int(cut["seconds"]), "resolution": res,
            "output_format": "mp4", "generate_audio": bool(gen.get("audio", True))}
    if route != "image-to-video":
        body["aspect_ratio"] = gen.get("aspect_ratio", "9:16")
    if first_url:
        body["image_url"] = first_url
        if last_url:
            body["end_image_url"] = last_url
    elif refs:
        body["image_urls"] = refs
    pmodel = f"seedance-2.5-{'i2v' if first_url else 'ref' if refs else 't2v'}-{res}"
    rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "generating"})
    progress(f"{cut['code']} Seedance 2.5 {res} {cut['seconds']}초 생성 요청 · " + ("첫 장면 사진" + (" + 끝 장면" if last_url else "") if first_url else f"참고 사진 {len(refs)}장" if refs else "글로만"))
    code, data = http_json(f"https://api.higgsfield.ai/bytedance/seedance-2.5/{route}",
                           {**hf_auth_headers(), "Content-Type": "application/json", "Accept": "application/json"}, "POST", body, timeout=120)
    if code not in (200, 201, 202) or not isinstance(data, dict):
        rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "failed"})
        raise RuntimeError(f"생성 요청 실패 {code}: {str(data)[:300]}")
    rid = data.get("request_id") or data.get("id")
    status_url = data.get("status_url") or f"https://api.higgsfield.ai/requests/{rid}/status"
    cost_id = log_cost("video", "higgsfield", pmodel, {"seconds": int(cut["seconds"])}, episode_id=ep, scene_no=cut["scene_no"],
                       cut_id=cut["code"], attempt_no=len(cut.get("takes") or []) + 1, outcome="pending", job_id=t["id"],
                       note=f"Seedance 2.5 {route} {res}")
    take_id = rpc("agent_save_take", p_token=TOKEN, p={"cut_id": cut["id"], "model": f"bytedance/seedance-2.5/{route}", "resolution": res,
                                                        "seconds": int(cut["seconds"]), "request_id": rid, "status": "submitted",
                                                        "cost_event_id": cost_id, "usd": estimate_usd("higgsfield", pmodel, {"seconds": int(cut["seconds"])})})
    deadline, last, st = time.time() + 40 * 60, None, {}
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
    url = find_video_url(st)
    if not url:
        raise RuntimeError(f"완료됐지만 영상 주소를 찾지 못했습니다: {str(st)[:300]}")
    rpc("agent_save_take", p_token=TOKEN, p={"id": take_id, "status": "completed", "remote_url": url})
    take = rpc("agent_get_take", p_token=TOKEN, p_id=take_id)
    progress(f"{cut['code']} 영상 받는 중")
    tmp = os.path.join(HOME, "work", f"{take_id}.mp4")
    fetch_to(url, tmp)
    files = save_local_package(take, tmp)
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
    rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "reviewing"})
    # 감수(같은 컴퓨터에서 바로) → 윈도우 보관
    try:
        review_take(take_id, tmp)
    except Exception as ex:
        log(f"영상 감수 실패(대기열로 다시): {ex}")
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "video_review", "episode_id": ep, "cut_id": cut["id"], "take_id": take_id})
    queue_store(take_id, ep, cut)
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
    system = (guide(ctx, "영상감수자", "감정감수자", "최종감수자") or "너는 영상감수자다.") + f"""

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
    rv = pick_json(text)
    if sound:
        rv["sound"] = {"heard_text": sound["heard_text"], "curve": sound["curve"]}
    sc = rv.get("scores") or {}
    got = {k: w for k, w in WEIGHTS.items() if sc.get(k) is not None}
    score = round(sum(float(sc[k]) * w for k, w in got.items()) / (sum(got.values()) or 1), 1)
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
    return verdict, score


@task("video_review")
def t_video_review(t):
    v, s = review_take(t["take_id"])
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
              "soft natural light, realistic hair strands, no makeup filter, no illustration, no 3D render, no text.")
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


@task("photo_design")
def t_photo_design(t):
    did = (t.get("payload") or {}).get("design_id")
    d = rpc("agent_get_photo_design", p_token=TOKEN, p_id=did)
    if not d:
        raise RuntimeError("사진 디자인 요청을 찾지 못했습니다")
    name = d["entry_name"]
    if re.search(r"[가-힣]", d.get("prompt") or ""):
        # 한글 묘사는 작가실이 영어 프롬프트로 옮긴 뒤에 만든다(사진 모델은 영어가 정확)
        if d.get("prompt_en"):
            d["prompt"] = d["prompt_en"]
        elif (t.get("attempts") or 1) < 6:
            raise Retry(90, "작가실이 한글 묘사를 영어 프롬프트로 옮기는 중이라 잠시 뒤에 만듭니다")
    try:
        if d["category"] == "character":
            progress(f"{name} 실사 인물 사진 후보를 만드는 중 (Soul 2)")
            urls = soul_images(d["prompt"] + " " + PHOTO_LOOK, n=4, aspect="3:4")
            model = SOUL2
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
        previews = [{"path": upload_from_url(u, f"photos/{did}/{i}_{uuid.uuid4().hex[:6]}.jpg")} for i, u in enumerate(urls, 1)]
    except Exception as e:
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": str(e)[:500]})
        raise
    if not previews:
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": "후보가 만들어지지 않았습니다"})
        raise RuntimeError("후보가 만들어지지 않았습니다")
    rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "ready", "previews": previews})
    log_cost("keyframe", "higgsfield", model, {"images": len(previews)}, note=f"사진 디자인: {name}", outcome="success")
    notify(f"{name} 사진 후보 {len(previews)}장 도착", "바이블 탭에서 마음에 드는 사진을 골라 주세요")
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
        progress(f"{name} 얼굴 학습 중 (Soul ID, 수 분 걸림)")
        soul_id = soul_train(f"{d.get('series_title') or 'NYX'} · {name}", src)
        log_cost("keyframe", "higgsfield", "soul-id-training", {"calls": 1}, note=f"얼굴 학습: {name}", outcome="success")
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "training", "soul_id": soul_id})
        look = (d.get("description") or d.get("prompt") or "")[:900]
        outfit = (d.get("details") or "")[:500]
        ref_set = []
        for label, shot in SET_SHOTS:
            progress(f"{name} 기준 사진 만드는 중: {label}")
            try:
                u = soul_images(f"{look} {outfit if label == '전신·의상' else ''} {shot} {PHOTO_LOOK}", n=1, aspect="3:4", soul_id=soul_id)[0]
                ref_set.append({"label": label, "path": upload_from_url(u, f"photos/{did}/set_{len(ref_set) + 1}_{uuid.uuid4().hex[:6]}.jpg")})
            except Exception as ex:
                log(f"기준 사진 실패({label}): {ex}")
        log_cost("keyframe", "higgsfield", SOUL2, {"images": len(ref_set)}, note=f"기준 사진 세트: {name}", outcome="success")
    except Exception as e:
        rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "failed", "error": str(e)[:500]})
        raise
    rpc("agent_update_photo_design", p_token=TOKEN, p_id=did, p={"status": "set_ready", "soul_id": soul_id, "ref_set": ref_set})
    notify(f"{name} 얼굴 고정 완료", f"같은 얼굴 기준 사진 {len(ref_set)}장을 바이블 탭에서 확인하고 적용해 주세요")
    return f"{name} Soul ID + 기준 사진 {len(ref_set)}장"


@task("voice_design")
def t_voice_design(t):
    did = (t.get("payload") or {}).get("design_id")
    d = rpc("agent_get_voice_design", p_token=TOKEN, p_id=did)
    if not d:
        raise RuntimeError("목소리 디자인 요청을 찾지 못했습니다")
    progress(f"{d['entry_name']} 목소리 후보를 만드는 중")
    body = {"voice_description": d["description"].split("\n[노래]")[0][:1000]}
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
    notify(f"{d['entry_name']} 목소리 후보 {len(previews)}개 도착", "바이블 탭에서 들어 보고 골라 주세요")
    return f"{d['entry_name']} 후보 {len(previews)}개"


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
답 형식: <json>{"items": [{"category": "", "name": "", "summary": "", "description": "", "details": "", "appears": [{"ep": 1, "scene": 1}]}]}</json>"""
    total = {"new_entries": 0, "proposals": 0}
    for e in eps:
        progress(f"작가실이 {e['number']}화에서 필요한 인물·장소·소품을 찾는 중")
        user = f"시리즈: {title}\n\n[이미 바이블에 있는 항목]\n{json.dumps(known, ensure_ascii=False)}\n\n[{e['number']}화 {e.get('title') or ''} 대본]\n{(e.get('text') or '')[:60000]}"
        text, _ = claude("claude-opus-5-5", system, user, max_tokens=8000, stage="other", note=f"바이블 필요 항목 추출: {title} {e['number']}화")
        items = pick_json(text).get("items") or []
        for it in items:
            it["appears"] = [{"ep": int(x.get("ep") or e["number"]), "scene": int(x.get("scene") or 0)} for x in (it.get("appears") or []) if isinstance(x, dict)]
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
    system = f"""너는 드라마 제작팀의 미술·캐스팅 담당이다. 대표가 올린 기준 사진을 보고 시리즈 바이블의 고정 묘사를 쓴다.
이 묘사는 영상·사진 생성 모델 프롬프트에 그대로 들어가서, 모든 화에서 같은 {'얼굴' if cat == 'character' else '모습'}이 나오게 하는 기준이 된다.
- description: 영어. 세세하고 꼼꼼하게, 사진에 실제로 보이는 것만. 4~8문장(약 80~160단어). 다룰 것 — {focus}.
  여러 장이면 공통으로 일관된 특징을 쓰고, 장마다 다른 것(의상·조명 등)은 details로 보낸다. 실존 인물·배우·브랜드 이름 금지. 추측은 쓰지 않는다.
- details: 한국어. 바뀌면 안 되는 특징(얼굴·머리·체형 등), 사진별로 다른 점(의상·조명), 만들 때 주의할 점·금지 사항을 줄 단위로.
답 형식: <json>{{"description": "", "details": ""}}</json>"""
    user = blocks + [{"type": "text", "text":
        f"시리즈: {info.get('series') or ''}\n항목: {info.get('name')} ({cat})\n대본 속 역할: {info.get('summary') or '-'}\n"
        f"지금 적혀 있는 묘사(참고만, 사진과 다르면 사진이 우선): {info.get('description') or '-'}"}]
    progress(f"{info.get('name')}: 작가실이 사진을 보고 묘사(프롬프트)를 쓰는 중")
    text, _ = claude("claude-opus-5-5", system, user, max_tokens=3000, stage="other", note=f"사진 보고 바이블 묘사: {info.get('name')}")
    d = pick_json(text)
    desc = (d.get("description") or "").strip()
    if not desc:
        raise RuntimeError("Claude가 묘사를 돌려주지 않았습니다")
    rpc("agent_bible_set_description", p_token=TOKEN, p_version=info["version_id"], p_description=desc, p_details=(d.get("details") or "").strip())
    notify(f"{info.get('name')} 묘사 작성 완료", "바이블 탭에서 사진을 보고 쓴 묘사를 확인해 주세요")
    return f"{info.get('name')} 묘사 {len(desc)}자"


@task("bible_prompt")
def t_bible_prompt(t):
    """작가실: 한국어로 적힌 바이블 묘사·세부 사항을 영상·사진 모델용 영어 고정 프롬프트로 한 번 옮겨 둔다."""
    p = t.get("payload") or {}
    info = rpc("agent_bible_photo_info", p_token=TOKEN, p_version=p.get("version_id")) or {}
    if not info:
        return "대상 버전이 없습니다(삭제됨)"
    if info.get("status") not in ("active", "proposed"):
        return "이미 다른 버전으로 바뀌었습니다"
    cat = info.get("category")
    system = """너는 드라마 작가실의 프롬프트 담당이다. 시리즈 바이블 항목의 묘사(한국어가 섞여 있음)를 영상·사진 생성 모델에 그대로 넣을 영어 고정 프롬프트로 옮긴다.
- 눈에 보이는 고정 특징만: 인물은 나이대·국적 인상·성별·얼굴형·눈·코·입·피부·머리(길이·모양·색)·체형·키 인상·기본 분위기·대표 의상,
  장소·배경은 공간 구조·재질·색·시간대·빛·분위기, 소품은 모양·크기·재질·색·낡음, 스타일·톤은 색감·조명·카메라·질감.
- 세부 사항에 '바뀌면 안 되는 특징'이 있으면 반드시 포함한다. 화마다 바뀌는 의상·상황·줄거리·성격·대사는 넣지 않는다.
- 3~7문장, 현재형, 쉼표로 이어진 묘사 위주. 이름·실존 인물·배우·브랜드는 쓰지 않는다. 원문에 없는 것을 지어내지 않는다.
답 형식: <json>{"prompt_en": ""}</json>"""
    user = (f"분류: {cat}\n이름(참고, 프롬프트에 쓰지 말 것): {info.get('name')}\n대본 속 역할: {info.get('summary') or '-'}\n"
            f"[묘사]\n{info.get('description') or '-'}\n[세부 사항]\n{info.get('details') or '-'}")
    progress(f"{info.get('name')}: 작가실이 영어 프롬프트로 옮기는 중")
    text, _ = claude("claude-opus-5-5", system, user, max_tokens=1500, stage="other", note=f"바이블 영어 프롬프트: {info.get('name')}")
    en = (pick_json(text).get("prompt_en") or "").strip()
    if not en or re.search(r"[가-힣]", en):
        raise RuntimeError("영어 프롬프트를 만들지 못했습니다")
    rpc("agent_bible_set_prompt", p_token=TOKEN, p_version=info["version_id"], p_prompt=en)
    return f"{info.get('name')} 영어 프롬프트 {len(en)}자"


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
            ep, sc = int(x.get("ep")), int(x.get("scene"))
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
    if not chars:
        return "목소리를 새로 만들 인물이 없습니다(이미 정했거나 후보가 기다리는 중)"
    title = (cast.get("series") or {}).get("title", "")
    progress(f"작가실이 「{title}」 인물 {len(chars)}명의 목소리를 설계하는 중")
    system = """너는 드라마 캐스팅 디렉터 겸 음성 감독이다. 대본과 인물 설정을 읽고 인물마다 ElevenLabs 목소리 디자인용 설명을 쓴다.
- voice_description: 영어 200~600자. 나이, 성별, 출신·억양(한국어를 말할 때의 억양), 음색(밝음·허스키·두께·높이), 말 속도, 감정 표현 습관, 녹음 품질("clean studio recording").
  실존 인물·배우·가수 이름은 쓰지 않는다. 인물끼리 확실히 구별되게 높이·질감·속도를 다르게 잡는다.
- sample_text: 그 인물이 대본에서 할 법한 한국어 대사 130~250자. 감정이 두세 번 바뀌게(설렘→긴장→다짐 등) 써서 목소리 폭을 들을 수 있게.
- singing_hint: 노래 장면이 있다면 어울리는 노래 음색 한 줄(한국어). 없으면 빈 문자열.
답 형식: <json>{"voices": [{"entry_id": "", "name": "", "voice_description": "", "sample_text": "", "singing_hint": ""}]}</json>"""
    user = f"시리즈: {title}\n\n[인물]\n" + json.dumps([{k: c.get(k) for k in ("entry_id", "name", "summary", "description", "details")} for c in chars], ensure_ascii=False) \
        + "\n\n[대본 앞부분]\n" + "\n\n".join(f"{s['number']}화 {s.get('title') or ''}\n{s.get('text') or ''}" for s in cast.get("scripts") or [])[:40000]
    text, _ = claude("claude-opus-5-5", system, user, max_tokens=6000, stage="other", note=f"시리즈 목소리 설계: {title}")
    voices = pick_json(text).get("voices") or []
    ids = {c["entry_id"] for c in chars}
    made = []
    for v in voices:
        if v.get("entry_id") not in ids or len(v.get("voice_description") or "") < 20:
            continue
        sample = (v.get("sample_text") or "").strip()
        if len(sample) < 100:
            sample = (sample + " " + "오늘은 정말 긴 하루였어요. 그래도 끝까지 해낼 거예요. 지켜봐 주세요.")[:300]
        desc = v["voice_description"] + (f"\n[노래] {v['singing_hint']}" if v.get("singing_hint") else "")
        did = rpc("agent_add_voice_design", p_token=TOKEN, p_entry=v["entry_id"], p_description=desc[:1000], p_sample=sample)
        rpc("agent_add_task", p_token=TOKEN, p={"kind": "voice_design", "payload": {"design_id": did}})
        made.append(v.get("name") or "")
    notify(f"「{title}」 목소리 후보 준비 시작", f"{', '.join(made)} — 몇 분 뒤 바이블 탭에서 인물별 후보 3개를 듣고 골라 주세요")
    return f"인물 {len(made)}명 목소리 설계 → 후보 만들기 시작: {', '.join(made)}"


@task("voice_save")
def t_voice_save(t):
    did = (t.get("payload") or {}).get("design_id")
    d = rpc("agent_get_voice_design", p_token=TOKEN, p_id=did)
    if not d or not d.get("chosen_generated_id"):
        raise RuntimeError("고른 목소리가 없습니다")
    progress(f"{d['entry_name']} 목소리를 ElevenLabs에 저장하는 중")
    desc = d["description"].split("\n[노래]")[0]
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
    return f"{d['entry_name']} 목소리 저장 ({data['voice_id']})"


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
    ed = rpc("agent_get_edit", p_token=TOKEN, p_project=r["project_id"])
    tl, kind = r["timeline"], r["kind"]
    takes = ed.get("takes") or {}
    upd = lambda **p: rpc("agent_update_render", p_token=TOKEN, p_id=rid, p=p)
    upd(status="running", progress="재료 모으는 중")
    try:
        clips, total, subs = tl_layout(tl)
        clips = [c for c in clips if c.get("take_id") in takes]
        if not clips:
            raise RuntimeError("타임라인에 쓸 수 있는 컷이 없습니다")
        work = os.path.join(HOME, "work", f"render_{rid}")
        os.makedirs(work, exist_ok=True)
        srcs = []
        for i, c in enumerate(clips, 1):
            progress(f"재료 {i}/{len(clips)}: {takes[c['take_id']].get('code')}")
            srcs.append(take_source(c["take_id"], takes[c["take_id"]]))
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
            fc.append(f"[{i}:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,fps=30,"
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
        files = {"seconds": total, "clips": len(clips)}
        srt, vtt = os.path.join(work, f"{tag}_ko.srt"), os.path.join(work, f"{tag}_ko.vtt")
        if subs:
            write_subs(subs, srt, vtt)
        sid = e.get("series_id") or "series"
        base = f"{sid}/E{int(e.get('number') or 0):02d}/edit/{kind}_{rid[:8]}"
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
        rpc("agent_task_update", p_token=TOKEN, p_id=tk["id"], p_status="queued", p_progress=r.why[:200], p_retry_in=int(r.seconds))
    except Exception as e:
        log(f"제작 작업 실패: {kind}: {e}\n{traceback.format_exc()}")
        retry = 120 if (tk.get("attempts") or 1) < 2 and kind in ("store", "video_review") else None
        rpc("agent_task_update", p_token=TOKEN, p_id=tk["id"], p_status="failed", p_error=str(e)[:1500], p_retry_in=retry)
        if kind in ("writers_room", "director") or retry is None:
            notify(f"작업 실패: {TASK_NAME.get(kind, kind)}", str(e)[:300], "warning")
    finally:
        CURRENT["task"] = None


TASK_NAME = {"bible_prompt": "바이블 영어 프롬프트", "bible_extract": "대본에서 필요 항목 찾기", "photo_describe": "사진 보고 묘사 쓰기", "bible_place": "넣을 화·씬 추천", "photo_design": "사진 디자인", "photo_set": "얼굴 학습·기준 사진", "prep_cut": "컷 준비(사진·목소리)", "assemble": "편집본 조립", "edit_command": "말로 편집", "voice_cast": "시리즈 목소리 설계", "voice_design": "목소리 후보 만들기", "voice_save": "목소리 저장", "writers_room": "작가실 정리", "director": "감독 컷 설계", "generate": "영상 생성", "store": "윈도우 보관", "video_review": "영상 감수"}


class Beat:
    """오래 걸리는 작업 중에도 15초마다 살아 있음 신호를 보내고, 간단한 명령은 바로 처리한다."""
    def __init__(self, info):
        self.info, self.stop, self.restart_after = info, threading.Event(), False
        self.th = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stop.wait(BEAT_SECONDS):
            try:
                t = CURRENT.get("task") or {}
                hb = rpc("agent_heartbeat", p_token=TOKEN, p_platform=PLATFORM, p_version=VERSION, p_state="busy",
                         p_current_job=t.get("kind"), p_last_error=None, p_info=self.info)
                for c in hb.get("commands") or []:
                    if c["command"] in ("restart", "update"):
                        self.restart_after = True
                        rpc("agent_command_done", p_token=TOKEN, p_id=c["id"], p_ok=True, p_result="지금 작업이 끝나면 바로 처리합니다")
                    else:
                        handle_command(c)
            except Exception as e:
                log(f"작업 중 신호 실패: {e}")

    def __enter__(self):
        self.th.start()
        return self

    def __exit__(self, *a):
        self.stop.set()


# ------------------------------------------------------------------ 본체
def main():
    global DEVICE_ID
    log(f"NYX 에이전트 v{VERSION} 시작 ({PLATFORM}, {socket.gethostname()})")
    keep_awake()
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
                tk = rpc("agent_claim_task", p_token=TOKEN, p_kinds=sorted(TASKS), p_platform=PLATFORM)
                if tk and tk.get("id"):
                    busy = True
                    with Beat(info) as beat:
                        run_task(tk)
                    if beat.restart_after:
                        check_update(force=True)
                        raise Restart()
                else:
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
