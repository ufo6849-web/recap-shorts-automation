"""정보성 글 사실 확인(설계서 docs/PLAN_2026-11-04_factcheck.md 11.1·11.3).

분류(gemini-3.8-flash)와 판정(claude-opus-5.5 + 웹 검색)을 부르고, 결과를 상태·기록·고지 문구로 바꾼다.
모든 호출은 gemini_api._post 를 거친다(사용량 기록은 어댑터만 한다). 파일 흐름·대기열·화면은 pipeline·server 가 맡는다.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from recap_shorts import errors
from recap_shorts.collect.news_api import outlet_for
from recap_shorts.llm import gemini_api as G
from recap_shorts.script.llm import parse_json_loose
from recap_shorts.script.text import normalize_loose

POLICY_VERSION = 1
CLASSIFY_MODEL = "gemini-3.8-flash"          # route()가 Gemini 키 또는 OpenRouter로 보낸다
VERIFY_MODEL = "anthropic/claude-opus-5.5"   # OpenRouter 전용
RECORD_NAME = "factcheck.json"
MAX_CLAIMS = 3
URL_MAX = 500
SEARCH = "exa:5"
KINDS = ("info", "general", "uncertain")
VERDICTS = ("true", "partly", "false", "unverified")
FIELDS = ("title", "hook", "sentence", "closing")
NO_OR_KEY = "사실 확인에는 OpenRouter 키가 필요합니다. 설정에서 넣어 주세요."
DEFAULT_NOTE = "내용은 위 커뮤니티 게시글 기준이며 공식 기록과 대조하지 않았습니다."
PASS_NOTE = "아래 뉴스로 주요 주장을 확인했습니다."
CAUTION_NOTE = "일부 주장은 보도로 확인되지 않았습니다."


class FactcheckError(RuntimeError):
    """str(e) 는 화면에 그대로 보이는 한국어 문장이다. 키·예외 이름·경로를 넣지 않는다."""


# ---------------------------------------------------------------- 프롬프트
_CLASSIFY_HEAD = """너는 쇼츠 영상을 만들기 전에 글에 사실 확인이 필요한지 가리는 담당이다. 아래 커뮤니티 글을 읽고 JSON 만 답하라.

kind 는 다음 셋 중 하나다.
- "info": 실제 사건·사고·정책·법·기업·가격·수치·통계·건강·의학·과학 주장처럼 사실 여부를 따질 수 있는 내용이 조금이라도 있다. 시사성 글은 거의 모두 info 다.
- "general": 개인 경험담·유머·감상·잡담뿐이고 사실로 따질 내용이 전혀 없다.
- "uncertain": 판단하기 어렵다.
애매하면 general 로 두지 않는다. uncertain 이나 info 를 고른다."""

_CLASSIFY_SCHEMA = """JSON 형식: {"kind": "info" | "general" | "uncertain", "reason": "한 문장", "claims": [{"claim": "...", "query": "...", "source_quote": "..."}]}
- claims 는 영상에서 사실로 말하게 될 핵심 주장 최대 3개다.
- claim: 주장을 한 문장으로(한국어).
- query: 한국 뉴스를 찾을 검색어 2~5낱말.
- source_quote: 아래 본문에 있는 근거 구절을 글자 그대로 옮긴다. 바꿔 쓰지 않는다."""

_RULE_NORMAL = "kind 가 general 이면 claims 는 빈 배열([])로 둔다."
_RULE_FORCE = "kind 가 general 이어도 사실 주장이 하나라도 있으면 claims 를 뽑는다."

_VERIFY_HEAD = """너는 한국어 뉴스 사실 확인 담당이다. 아래 주장을 웹 검색으로 찾은 보도와 대조하라. 오늘은 <<오늘>>이다.

verdict 는 다음 영문 값 중 하나다.
- "true": 보도로 주장이 확인된다.
- "partly": 주장의 일부만 보도로 확인되거나 맞는 부분과 틀린 부분이 섞였다. fact 에 보도로 확인된 내용을 바로잡아 적는다.
- "false": 보도가 주장과 반대되는 사실을 분명히 보도한다. 근거가 애매하면 false 가 아니라 unverified 다.
- "unverified": 보도가 주장을 직접 다루지 않거나, 검토·가능성·발표 예정 같은 단계만 보도되어 주장 내용이 확인되지 않는다.

규칙
- 보도에 없는 내용을 추측으로 채우지 않는다.
- fact 는 verdict 가 true 또는 partly 일 때만 한 문장으로 쓴다. 그 밖에는 null 이다.
- sources 에는 근거로 든 기사 URL 만 넣는다. 검색으로 실제 찾은 기사 URL 이어야 한다.
- why 는 한두 문장이다.

JSON 형식: {"results": [{"id": "C1", "verdict": "true" | "partly" | "false" | "unverified", "why": "...", "fact": "... 또는 null", "sources": ["https://..."]}], "overall_why": "한 문장"}"""

_SCRIPT_HEAD = """너는 쇼츠 대본 검수 담당이다. 아래 주장은 사실 확인에서 확인되지 않았거나 사실 아님으로 판정되었다. 대본의 각 부분(번호 포함)이 그 주장을 사실처럼 말하거나 뒷받침하는지 찾아라.
- 주장을 사실처럼 말하는 부분만 고른다. 주장을 부정하거나 "보도되지 않았다", "확인되지 않았다"처럼 의심하는 부분은 고르지 않는다.
- 해당 부분이 없으면 빈 배열을 낸다."""

_SCRIPT_SCHEMA = """JSON 형식: {"flags": [{"claim_id": "C1", "field": "title" | "hook" | "sentence" | "closing", "index": 문장 번호 또는 null}]}"""


# ---------------------------------------------------------------- 공통 호출
def _body(prompt: str, tools: list | None = None) -> dict:
    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json"}}
    if tools:
        body["tools"] = tools
    return body


def _api_error(stage: str, e: Exception) -> FactcheckError:
    """API 실패를 화면 문장 한 줄로. 키가 들어간 문장은 버린다."""
    detail = errors.screen_text(e, fallback="") if isinstance(e, G.GeminiApiError) else ""
    keys = [k for k in (G._openrouter_key(), G._key_or_empty()) if k]
    if any(k in detail for k in keys):
        detail = ""
    return FactcheckError(f"사실 확인 {stage}에 실패했습니다" + (f" — {detail}" if detail else "."))


def _ask(stage: str, model: str, body: dict, timeout: float) -> tuple[dict, dict]:
    """_post 한 번 → (JSON 답, 원래 답). 호출 실패와 깨진 답은 FactcheckError 로 바꾼다."""
    try:
        resp = G._post(model, body, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 화면 문장 하나로 바꾼다
        raise _api_error(stage, e) from None
    try:
        return parse_json_loose(G._text_of(resp)), resp
    except Exception:  # noqa: BLE001
        raise FactcheckError(f"사실 확인 {stage} 답을 읽지 못했습니다. 다시 확인해 주세요.") from None


# ---------------------------------------------------------------- 분류
def classify(title: str, source_text: str, *, force: bool = False) -> dict:
    """글을 info / general / uncertain 으로 나누고 주장을 뽑는다(최대 3개, 근거 구절이 원문에 있어야 함).
    force=True 면 일반 글이어도 주장을 뽑는다. 실패하면 FactcheckError."""
    rule = _RULE_FORCE if force else _RULE_NORMAL
    prompt = "\n\n".join([_CLASSIFY_HEAD, rule, _CLASSIFY_SCHEMA, f"제목: {title}\n본문:\n{source_text}"])
    out, _ = _ask("분류", CLASSIFY_MODEL, _body(prompt), timeout=30)
    norm_src = normalize_loose(source_text)
    kind = str(out.get("kind") or "").strip().lower()
    if kind not in KINDS:
        kind = "uncertain"
    claims = []
    for c in out.get("claims") or []:
        if not isinstance(c, dict):
            continue
        text = str(c.get("claim") or "").strip()
        quote = str(c.get("source_quote") or "").strip()
        nq = normalize_loose(quote)
        if not text or not nq or nq not in norm_src:
            continue
        claims.append({"claim": text, "query": str(c.get("query") or "").strip(), "source_quote": quote})
        if len(claims) == MAX_CLAIMS:
            break
    if kind == "general" and not force:
        claims = []
    return {"kind": kind, "reason": str(out.get("reason") or "").strip(),
            "claims": [{"id": f"C{i}", **c} for i, c in enumerate(claims, 1)]}


# ---------------------------------------------------------------- 출처 주소·날짜
_DATE_SLASH = re.compile(r"/(20\d{2})/(\d{2})/(\d{2})(?!\d)")
_DATE_PLAIN = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")


def _public_host(host: str) -> bool:
    """IP 주소 꼴이면 공인 주소인지 본다(127.1·2130706433 같은 옛 꼴도 IP 로 읽는다). 이름이면 True.
    ponytail: 이름은 DNS 를 찾아 확인하지 않는다. 사설 주소로 풀리는 이름은 못 막는다. 주소를 직접 열게 되면 DNS 확인을 더한다."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
        except (OSError, ValueError):
            return True
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


def safe_url(url) -> str | None:
    """공개 http(s) 주소만 남긴다: 사용자 정보·localhost·사설/루프백/링크로컬 IP 없음, 500자 이하.
    scheme·host 는 소문자로 맞추고 fragment 는 뺀다(query 는 남긴다). 조건에 안 맞으면 None."""
    if not isinstance(url, str):
        return None
    s = url.strip()
    if not s or len(s) > URL_MAX:
        return None
    try:
        p = urlsplit(s)
        _ = p.port                                   # 포트가 숫자가 아니거나 범위 밖이면 ValueError
    except ValueError:
        return None
    host = p.hostname or ""
    if p.scheme.lower() not in ("http", "https") or not host or "@" in p.netloc:
        return None
    if host == "localhost" or host.endswith(".localhost") or not _public_host(host):
        return None
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, p.query, ""))


def date_from_url(url) -> str | None:
    """경로에 적힌 날짜만 쓴다: /2026/11/02/ 또는 20261102. 없거나 없는 날짜면 None."""
    if not isinstance(url, str):
        return None
    try:
        path = urlsplit(url.strip()).path
    except ValueError:
        return None
    m = _DATE_SLASH.search(path) or _DATE_PLAIN.search(path)
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------- 판정
def _citations(resp: dict) -> dict:
    """검색 인용 메타데이터: 정규화한 URL → 제목. 모델이 든 URL 은 여기 있는 것만 출처가 된다."""
    cands = resp.get("candidates") or [{}]
    chunks = ((cands[0] or {}).get("groundingMetadata") or {}).get("groundingChunks") or []
    cited: dict[str, str] = {}
    for ch in chunks:
        web = (ch or {}).get("web") or {}
        u = safe_url(web.get("uri"))
        if u and u not in cited:
            cited[u] = str(web.get("title") or "").strip()
    return cited


def _sources(raw, cited: dict) -> list[dict]:
    out, seen = [], set()
    for s in raw or []:
        u = safe_url(s.get("url") if isinstance(s, dict) else s)
        if u and u in cited and u not in seen:
            seen.add(u)
            out.append({"url": u, "title": cited[u], "outlet": outlet_for(u), "date": date_from_url(u)})
    return out


def _verify_prompt(claims: list[dict]) -> str:
    listing = "\n".join(f"- {c['id']}: {c['claim']}" + (f" (검색 힌트: {c['query']})" if c.get("query") else "")
                        for c in claims)
    head = _VERIFY_HEAD.replace("<<오늘>>", date.today().isoformat())
    return "\n\n".join([head, "주장:\n" + listing])


def verify(claims: list[dict]) -> dict:
    """주장을 웹 검색 보도와 대조한다(OpenRouter 전용). 출처는 검색 인용에 있는 것만 남기고,
    true·partly 인데 출처가 없으면 unverified 로 내린다. 키가 없거나 호출이 실패하면 FactcheckError."""
    if not G._openrouter_key():
        raise FactcheckError(NO_OR_KEY)
    if not claims:
        return {"results": [], "overall_why": "", "model": VERIFY_MODEL, "search": SEARCH, "cost_usd": None}
    out, resp = _ask("판정", VERIFY_MODEL, _body(_verify_prompt(claims), tools=[{"google_search": {}}]), timeout=120)
    cited = _citations(resp)
    got = {r.get("id"): r for r in out.get("results") or [] if isinstance(r, dict)}
    results = []
    for c in claims:
        r = got.get(c["id"]) or {}
        verdict = str(r.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            verdict = "unverified"
        why = str(r.get("why") or "").strip() if r else "판정 결과가 없습니다."
        fact = (str(r.get("fact") or "").strip() or None) if verdict in ("true", "partly") else None
        sources = _sources(r.get("sources"), cited)
        if verdict in ("true", "partly") and not sources:
            verdict, why, fact = "unverified", "인용된 출처가 없어 확인 불가로 내렸습니다.", None
        results.append({"id": c["id"], "verdict": verdict, "why": why, "fact": fact, "sources": sources})
    cost = (resp.get("usageMetadata") or {}).get("costUsd")
    return {"results": results, "overall_why": str(out.get("overall_why") or "").strip(),
            "model": VERIFY_MODEL, "search": SEARCH,
            "cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None}


def overall_status(results: list[dict]) -> str:
    """false 가 하나라도 있으면 stop, 모두 true 면 pass, 그 밖(확인 불가·일부·결과 없음)은 warn."""
    vs = [r.get("verdict") for r in results]
    if "false" in vs:
        return "stop"
    if vs and all(v == "true" for v in vs):
        return "pass"
    return "warn"


def input_hash(title: str, source_text: str) -> str:
    return hashlib.sha256((title + "\n" + source_text).encode("utf-8")).hexdigest()


def build_record(title, source_text, cls, ver, *, error=None, forced=False) -> dict:
    """factcheck.json 내용(11.3). 일반 글(강제 아님)은 skipped, error 가 있으면 failed, 그 밖은 판정으로 정한다."""
    kind = (cls or {}).get("kind")
    if error:
        status = "failed"
    elif kind == "general" and not forced:
        status = "skipped"
    else:
        status = None
    rec = {"version": POLICY_VERSION, "input_hash": input_hash(title, source_text), "status": status,
           "kind": kind, "forced": bool(forced), "reason": (cls or {}).get("reason") or "", "claims": [],
           "overall_why": "", "error": error or None, "override": None, "ack": None,
           "model": None, "search": None, "cost_usd": None,
           "checked_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if status == "skipped":
        return rec
    got = {r.get("id"): r for r in (ver or {}).get("results") or []}
    for c in (cls or {}).get("claims") or []:
        r = got.get(c["id"]) or {}
        rec["claims"].append({"id": c["id"], "claim": c["claim"], "source_quote": c["source_quote"],
                              "verdict": r.get("verdict") or "unverified", "why": r.get("why") or "",
                              "fact": r.get("fact"), "sources": r.get("sources") or []})
    rec["status"] = status or overall_status(rec["claims"])
    if ver:
        rec.update(model=ver.get("model"), search=ver.get("search"), cost_usd=ver.get("cost_usd"),
                   overall_why=ver.get("overall_why") or "")
    if status is None and not rec["claims"]:
        rec["overall_why"] = "검증할 주장을 찾지 못했습니다."
    return rec


def topic_additions(record: dict) -> dict:
    """topic.json 에 더할 세 키(11.2). true·partly 는 출처가 있을 때만 사실이 된다."""
    facts, false_claims, unverified = [], [], []
    for c in record.get("claims") or []:
        v = c.get("verdict")
        if v in ("true", "partly") and c.get("fact") and c.get("sources"):
            facts.append({"id": f"F{len(facts) + 1}", "text": c["fact"], "evidence": c["fact"],
                          "source": c["sources"][0]["url"], "claim_id": c["id"]})
        elif v == "false":
            false_claims.append({"id": c["id"], "claim": c["claim"], "source_quote": c["source_quote"]})
        elif v == "unverified":
            unverified.append({"id": c["id"], "claim": c["claim"]})
    return {"facts": facts, "false_claims": false_claims, "unverified_claims": unverified}


_BLOCK_NO_RESULT = "사실 확인 결과가 없습니다. 다시 확인해 주세요."
_BLOCK_STALE = "글이 바뀌어 사실 확인을 다시 해야 합니다."


def gate(record: dict | None, title: str, source_text: str, policy: int | None) -> tuple[str, str | None]:
    """계속 가도 되는지 정한다 → ("clear" | "need_ack" | "blocked", 사유). policy None 은 기능 이전 작업이라 막지 않는다."""
    if policy is None:
        return ("clear", None)
    if record is None or record.get("version") != POLICY_VERSION:
        return ("blocked", _BLOCK_NO_RESULT)
    h = input_hash(title, source_text)
    if record.get("input_hash") != h:
        return ("blocked", _BLOCK_STALE)
    st = record.get("status")
    if st in ("skipped", "pass"):
        return ("clear", None)
    if st == "warn":
        if (record.get("ack") or {}).get("input_hash") == h:
            return ("clear", None)
        return ("need_ack", "주의 판정을 확인하고 계속해 주세요.")
    if st == "stop":
        if (record.get("override") or {}).get("input_hash") == h:
            return ("clear", None)
        return ("blocked", "사실 아님 판정이 있어 계속할 수 없습니다. 고치거나 직접 확인해 주세요.")
    if st == "failed":
        if (record.get("override") or {}).get("input_hash") == h:
            return ("clear", None)
        return ("blocked", record.get("error") or "사실 확인에 실패했습니다. 다시 확인해 주세요.")
    return ("blocked", _BLOCK_NO_RESULT)


_TAG = {"title": "[제목]", "hook": "[훅]", "closing": "[끝]"}
_LABEL = {"false": "사실 아님", "unverified": "확인 불가"}


def script_check(script: dict, record: dict) -> list[dict]:
    """false·unverified 주장을 사실처럼 말한 대본 부분을 찾는다(모델 한 번, 분류 모델). 그런 주장이 없으면 부르지 않고 []."""
    targets = {c["id"]: c for c in record.get("claims") or [] if c.get("verdict") in ("false", "unverified")}
    if not targets:
        return []
    sents = script.get("sentences") or []
    parts = [("title", None, " ".join(script.get("title_lines") or [])), ("hook", None, script.get("hook") or ""),
             *[("sentence", i, s.get("text") or "") for i, s in enumerate(sents)],
             ("closing", None, script.get("closing") or "")]
    texts = {(f, i): t for f, i, t in parts if t}
    script_txt = "\n".join(f"{_TAG[f] if f != 'sentence' else f'[문장 {i}]'} {t}" for (f, i), t in texts.items())
    claims_txt = "\n".join(f"- {cid} ({_LABEL[c['verdict']]}): {c['claim']}" for cid, c in targets.items())
    prompt = "\n\n".join([_SCRIPT_HEAD, "판정된 주장:\n" + claims_txt, "대본:\n" + script_txt, _SCRIPT_SCHEMA])
    out, _ = _ask("대본 검수", CLASSIFY_MODEL, _body(prompt), timeout=30)
    flags, seen = [], set()
    for x in out.get("flags") or []:
        if not isinstance(x, dict) or not isinstance(x.get("claim_id"), str) or x["claim_id"] not in targets:
            continue
        field = x.get("field")
        if field not in FIELDS:
            continue
        idx = None
        if field == "sentence":
            idx = x.get("index")
            if not (isinstance(idx, int) and not isinstance(idx, bool)):
                continue
        key = (x["claim_id"], field, idx)
        if key in seen or (field, idx) not in texts:
            continue
        seen.add(key)
        flags.append({"field": field, "index": idx, "claim_id": x["claim_id"],
                      "verdict": targets[x["claim_id"]]["verdict"], "text": texts[(field, idx)]})
    return flags


# ---------------------------------------------------------------- 고지 문구·저장
def description_notice(record: dict | None) -> tuple[str, list[str]]:
    """설명 문구의 고지 한 문장과 [뉴스 출처] 줄들. 통과·주의·직접 확인에 따라 문구가 다르다(10절 10)."""
    if not record or record.get("status") in (None, "skipped"):
        return DEFAULT_NOTE, []
    note = PASS_NOTE if record.get("status") == "pass" else CAUTION_NOTE
    lines, seen = [], set()
    for c in record.get("claims") or []:
        if c.get("verdict") not in ("true", "partly"):
            continue
        for s in c.get("sources") or []:
            if s["url"] in seen:
                continue
            seen.add(s["url"])
            lines.append(" · ".join(x for x in (s.get("outlet"), s.get("title"), s["url"]) if x))
    return note, lines


def save_record(job_dir, record: dict) -> None:
    """임시 파일에 다 쓴 뒤 os.replace 로 바꿔 끼운다. 쓰다 죽어도 이전 파일은 그대로 남는다."""
    fd, tmp = tempfile.mkstemp(dir=str(job_dir), prefix=RECORD_NAME + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, Path(job_dir) / RECORD_NAME)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_record(job_dir) -> dict | None:
    """없음·깨짐·정책 버전 다름이면 None."""
    try:
        rec = json.loads((Path(job_dir) / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("version") != POLICY_VERSION:
        return None
    return rec
