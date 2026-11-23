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
VERSION = "1.6.0"

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
# 작가실 → 감독(컷 설계) → 대본감수자 → [대표 생성 승인] → Seedance 생성 → 영상감수자·평가 → 윈도우 보관
# 돈이 드는 영상 생성은 대표가 대시보드에서 승인한 컷만 한다(서버에서도 막혀 있음).

TASKS = {}


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
    rows = []
    for b in (ctx.get("bible") or [])[:limit]:
        rows.append(f"- [{b.get('category')}] {b.get('name')}: {b.get('description') or ''}"
                    + (f" / 고정 특징: {b.get('details')}" if b.get("details") else "")
                    + (" (사진 있음)" if b.get("images") else ""))
    return "\n".join(rows) or "(바이블이 비어 있음)"


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
    if wr.get("auto_confirm") is True and not props:
        confirmed = rpc("agent_confirm_script", p_token=TOKEN, p_id=vid, p_summary=summary)
        if p.get("auto_director", True):
            rpc("agent_add_task", p_token=TOKEN, p={"kind": "director", "episode_id": ep, "payload": {"script_version_id": vid}})
    else:
        notify(f"{e.get('number')}화 작가실 정리본 도착", summary[:300] + "\n대본 탭에서 확인 후 확정해 주세요", "warning" if props else "info")
    return f"정리본 저장" + (f", v{confirmed}로 확정 → 감독 컷 설계 시작" if confirmed else ", 확정 대기") + f" · 변경 {len(changes)}곳 · 제안 {len(props)}건"


# ---------------- 감독 + 대본감수자
CUT_SCHEMA = """{"scene_no": 1, "cut_no": 1, "code": "E01-S01-C01", "scene_heading": "S#1 | 00:00–00:12 | 장소 | 낮",
 "seconds": 10, "summary": "이 컷에서 일어나는 일", "dialogue": "린: (떨리며, 점점 높아지며) 대사 그대로",
 "emotion": "감정선", "expression_beats": [{"t": "0-3", "who": "린", "face": "놀람 → 입술을 깨묾"}, {"t": "3-5", "who": "린", "face": "…"}],
 "camera": "샷·앵글·움직임", "reference_names": ["린(과거)", "연습실"],
 "prompt": "Seedance에 보낼 영어 프롬프트(대사는 한국어 원문 그대로 따옴표)", "director_note": "반영한 메모·교훈"}"""


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
- reference_names에는 사진이 있는 바이블 항목 중 이 컷에 나오는 인물(최대 3)과 장소(최대 1)만.

[답 형식]
<json>{{"cuts": [{CUT_SCHEMA}]}}</json>"""


def review_cuts(ctx, ep, cuts, model):
    system = (guide(ctx, "대본감수자") or "너는 대본감수자다.") + f"""

[시리즈 바이블]
{bible_text(ctx)}

[감독 교훈(위반 여부도 확인)]
{lessons_text(ctx)}

[확인할 것] 대사가 대본과 같은가 / 감정선·발화 톤(피치)이 흐름에 맞는가 / 표정이 2~3초마다 바뀌게 설계됐는가 /
바이블 고정 묘사와 이름이 맞는가 / 4~15초인가 / 배경음악 금지 문구가 있는가 / 프롬프트가 한 장면만 묘사하는가

[답 형식]
<json>{{"cuts": [{{"scene_no": 1, "cut_no": 1, "verdict": "pass|fix", "issues": ["…"], "fix": "감독이 고칠 것"}}],
 "lessons": [{{"fingerprint": "짧은-영문-열쇠", "category": "face|costume|location|prop|continuity|expression|emotion_tone|dialogue|camera|motion|prompt|other",
               "problem": "", "rule": "앞으로 이렇게", "bad_example": "", "severity": "보통|치명"}}]}}</json>"""
    text, _ = claude(model, system, "[대본]\n" + (ctx.get("script") or {}).get("content", "")[:60000]
                     + "\n\n[감독 컷 설계]\n" + json.dumps(cuts, ensure_ascii=False),
                     max_tokens=8000, stage="prompt_review", episode_id=ep, note="대본감수자 컷 감수")
    return pick_json(text)


def est_cut_usd(ctx, seconds, refs):
    res = setting(ctx, "generation", "resolution", "480p")
    model = f"seedance-2.5-{'ref' if refs else 't2v'}-{res}"
    u = price("higgsfield", model, "per_second")
    return round(u * seconds, 4) if u else None


def has_images(ctx, names):
    imgs = {b["name"]: b.get("images") for b in (ctx.get("bible") or [])}
    return [n for n in (names or []) if imgs.get(n)]


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
    progress(f"대본감수자가 컷 {len(cuts)}개를 감수하는 중")
    rv = review_cuts(ctx, ep, cuts, rmodel)
    verdicts = {(int(r.get("scene_no") or 0), int(r.get("cut_no") or 0)): r for r in rv.get("cuts") or []}
    fixes = [c for c in cuts if (verdicts.get((int(c["scene_no"]), int(c["cut_no"]))) or {}).get("verdict") == "fix"]
    for l in rv.get("lessons") or []:
        try:
            rpc("agent_add_lesson", p_token=TOKEN, p_lesson={**l, "series_id": (ctx.get("series") or {}).get("id"), "sources": ["대본감수자"]})
        except Exception as ex:
            log(f"교훈 기록 실패: {ex}")
    if fixes:
        progress(f"감독이 감수 의견 {len(fixes)}건을 반영해 고치는 중")
        fix_text = json.dumps([{"cut": c, "review": verdicts[(int(c['scene_no']), int(c['cut_no']))]} for c in fixes], ensure_ascii=False)
        text, _ = claude(dmodel, director_system(ctx), f"대본감수자 의견을 반영해 아래 컷들만 고쳐서 같은 형식으로 다시 주세요.\n{fix_text}\n\n[대본]\n{script['content']}",
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
        "review": {"verdict": "revised", "issues": [], "fix": "대표 요청·영상 감수 반영"}})
    notify(f"{cut.get('code')} 수정 완료", "제작 탭에서 다시 생성 승인을 눌러 주세요")
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
    refs = []
    imgs = {b["name"]: b.get("images") or [] for b in (ctx.get("bible") or [])}
    for n in cut.get("reference_names") or []:
        for pth in imgs.get(n, [])[:1]:
            try:
                refs.append(storage_signed_url("bible", pth, 7200))
            except Exception as ex:
                log(f"참고 사진 주소 실패({n}): {ex}")
    refs = refs[:4]
    route = "reference-to-video" if refs else "text-to-video"
    body = {"prompt": cut["prompt"], "duration": int(cut["seconds"]), "resolution": res,
            "aspect_ratio": gen.get("aspect_ratio", "9:16"), "output_format": "mp4", "generate_audio": bool(gen.get("audio", True))}
    if refs:
        body["image_urls"] = refs
    pmodel = f"seedance-2.5-{'ref' if refs else 't2v'}-{res}"
    rpc("agent_update_cut", p_token=TOKEN, p_id=cut["id"], p={"status": "generating"})
    progress(f"{cut['code']} Seedance 2.5 {res} {cut['seconds']}초 생성 요청" + (f" (참고 사진 {len(refs)}장)" if refs else ""))
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
WEIGHTS = {"face_consistency": 0.25, "expression_changes": 0.2, "emotion": 0.2, "motion_quality": 0.15, "continuity": 0.1, "prompt_match": 0.1}


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
    content.append({"type": "text", "text": "[컷 설계]\n" + json.dumps({k: cut.get(k) for k in (
        'code', 'seconds', 'summary', 'dialogue', 'emotion', 'expression_beats', 'camera', 'reference_names', 'prompt')}, ensure_ascii=False)})
    system = (guide(ctx, "영상감수자", "최종감수자") or "너는 영상감수자다.") + f"""

[시리즈 바이블]
{bible_text(ctx)}

[감독 교훈]
{lessons_text(ctx)}

[보는 법] 1초 간격 장면 사진과 컷 설계를 비교한다. 소리는 들을 수 없으니 판단하지 않는다(대표 확인).
각 항목 0~100점: face_consistency(바이블 얼굴·의상 일치), expression_changes(2~3초마다 표정 변화), emotion(감정선),
motion_quality(손·몸 왜곡, 깜빡임, AI 티), continuity(앞뒤 컷과 이어짐), prompt_match(설계대로인가).
치명 문제(얼굴이 다른 사람, 심한 왜곡, 화면 글자)가 있으면 verdict는 fail.

[답 형식]
<json>{{"verdict": "pass|fail", "scores": {{"face_consistency": 0, "expression_changes": 0, "emotion": 0, "motion_quality": 0, "continuity": 0, "prompt_match": 0}},
 "issues": [{{"t": "3초", "problem": "", "fix": ""}}], "fix_for_director": "다음 생성에서 고칠 프롬프트 방향",
 "summary": "대표용 한두 줄",
 "lessons": [{{"fingerprint": "", "category": "face|costume|location|prop|continuity|expression|emotion_tone|dialogue|camera|motion|prompt|other", "problem": "", "rule": "", "bad_example": "", "severity": "보통|치명"}}]}}</json>"""
    text, rcost = claude(model, system, content, max_tokens=4000, stage="video_review", episode_id=ep,
                         scene_no=cut.get("scene_no"), cut_code=cut.get("code"), note="영상 감수(장면 사진)")
    rv = pick_json(text)
    sc = rv.get("scores") or {}
    score = round(sum(float(sc.get(k) or 0) * w for k, w in WEIGHTS.items()), 1)
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
        if kind == "final":
            fc.append("[dlg]asplit=2[dlg1][dlg2]")
            fc.append("[mus]asplit=2[mus1][mus2]")
            fc.append("[dlg1][mus1]amix=inputs=2:normalize=0,atrim=end=%.3f[mix]" % total)
        else:
            fc.append("[dlg][mus]amix=inputs=2:normalize=0,atrim=end=%.3f[mix]" % total)
        e = ed.get("episode") or {}
        tag = f"EP{int(e.get('number') or 0):02d}"
        out_mp4 = os.path.join(work, f"{tag}.mp4")
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args, "-filter_complex", ";".join(fc),
               "-map", "[vout]", "-map", "[mix]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28" if kind == "preview" else "17",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k" if kind == "preview" else "256k", "-movflags", "+faststart", out_mp4]
        if kind == "final":
            cmd += ["-map", "[dlg2]", "-c:a", "pcm_s16le", os.path.join(work, f"{tag}_대사·현장음.wav"),
                    "-map", "[mus2]", "-c:a", "pcm_s16le", os.path.join(work, f"{tag}_배경음악.wav")]
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
            with open(os.path.join(d, "편집정보.json"), "w", encoding="utf-8") as f:
                json.dump({"episode": e, "rev": r.get("rev"), "seconds": total, "timeline": tl}, f, ensure_ascii=False, indent=2)
            files["folder"] = d
            files["platform"] = PLATFORM
            small = os.path.join(work, "small.mp4")
            s = 640 / max(W, H)
            ff("-i", final, "-vf", f"scale={even(W * s)}:{even(H * s)}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", small)
            files["preview"] = storage_upload(base + ".mp4", small, "video/mp4")
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
    except Exception as e:
        log(f"제작 작업 실패: {kind}: {e}\n{traceback.format_exc()}")
        retry = 120 if (tk.get("attempts") or 1) < 2 and kind in ("store", "video_review") else None
        rpc("agent_task_update", p_token=TOKEN, p_id=tk["id"], p_status="failed", p_error=str(e)[:1500], p_retry_in=retry)
        if kind in ("writers_room", "director") or retry is None:
            notify(f"작업 실패: {TASK_NAME.get(kind, kind)}", str(e)[:300], "warning")
    finally:
        CURRENT["task"] = None


TASK_NAME = {"assemble": "편집본 조립", "edit_command": "말로 편집", "voice_cast": "시리즈 목소리 설계", "voice_design": "목소리 후보 만들기", "voice_save": "목소리 저장", "writers_room": "작가실 정리", "director": "감독 컷 설계", "generate": "영상 생성", "store": "윈도우 보관", "video_review": "영상 감수"}


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
