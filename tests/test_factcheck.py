"""사실 확인 핵심 시험(설계서 docs/PLAN_2026-11-04_factcheck.md 11.1·11.3, W1).

분류·판정 호출은 gemini_api._post 를 가짜로 바꿔 시험한다. 어댑터 변환(google_search↔plugins,
url_citation↔groundingChunks, usage.cost↔costUsd)은 requests.post 를 가짜로 바꿔 시험한다. 네트워크·유료 호출은 없다."""
import hashlib
import json

import pytest
import requests

from recap_shorts import factcheck as F
from recap_shorts import usage
from recap_shorts.llm import gemini_api as G

OR_KEY = "sk-or-test-0000000000000000"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
TITLE = "속보) 가공유 판매 금지"
SRC = "식약처가 가공유 판매를 금지한다고 발표했다. 서울우유 흰우유는 원유 함량이 40%라고 한다."
Q1 = "식약처가 가공유 판매를 금지한다고 발표했다"
Q2 = "원유 함량이 40%라고 한다"
URL_A = "https://biz.chosun.com/distribution/food/2026/11/02/2STS7W6TKJHR7C5WF7HFQICVGQ/"
URL_B = "https://www.yna.co.kr/view/MYH20260828008300038"
H = F.input_hash(TITLE, SRC)

DEFAULT_NOTE = "내용은 위 커뮤니티 게시글 기준이며 공식 기록과 대조하지 않았습니다."
PASS_NOTE = "아래 뉴스로 주요 주장을 확인했습니다."
CAUTION_NOTE = "일부 주장은 보도로 확인되지 않았습니다."
NO_RESULT = "사실 확인 결과가 없습니다. 다시 확인해 주세요."
STALE = "글이 바뀌어 사실 확인을 다시 해야 합니다."
NEED_ACK = "주의 판정을 확인하고 계속해 주세요."
STOP_MSG = "사실 아님 판정이 있어 계속할 수 없습니다. 고치거나 직접 확인해 주세요."

CLAIM1 = {"claim": "식약처가 판매 금지를 발표했다", "query": "식약처 가공유 판매 금지", "source_quote": Q1}
CLAIM2 = {"claim": "흰우유 원유 함량 40%", "query": "서울우유 원유 함량", "source_quote": Q2}
VCLAIM = {"id": "C1", "claim": "식약처가 판매 금지를 발표했다", "query": "식약처 가공유 판매 금지", "source_quote": Q1}

SRC_A = {"url": URL_A, "title": "식약처 검토 기사", "outlet": "조선일보", "date": "2026-11-02"}
SRC_B = {"url": URL_B, "title": "연합 기사", "outlet": "연합뉴스", "date": None}

SCRIPT = {"title_lines": ["가공유 판매", "금지 확정?"], "hook": "가공유가 금지된다",
          "sentences": [{"text": "식약처가 가공유 판매를 금지한다고 발표했다.", "evidence": Q1},
                        {"text": "흰우유도 함께 퇴출된다.", "evidence": None}],
          "closing": "정말일까요?"}


class Resp:
    def __init__(self, code=200, body=None):
        self.status_code, self._body = code, (body if body is not None else {})
        self.text = json.dumps(self._body, ensure_ascii=False)

    def json(self):
        return self._body


def gem(obj, chunks=None, cost=None):
    """G._post 가 돌려주는 Gemini 모양 답. obj 가 문자열이면 그대로 본문 글이 된다."""
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    cand = {"content": {"parts": [{"text": text}]}, "finishReason": "stop"}
    if chunks is not None:
        cand["groundingMetadata"] = {"groundingChunks": chunks}
    out = {"candidates": [cand]}
    if cost is not None:
        out["usageMetadata"] = {"costUsd": cost}
    return out


def web(uri, title):
    return {"web": {"uri": uri, "title": title}}


def fake_post(monkeypatch, answer):
    """G._post 를 가짜로 바꾸고 (모델, 본문, 시간 초과) 기록 목록을 돌려준다. answer 는 답 또는 callable(model, body)."""
    calls = []

    def post(model, body, timeout=300.0, sleep=None):
        calls.append({"model": model, "body": body, "timeout": timeout})
        return answer(model, body) if callable(answer) else answer
    monkeypatch.setattr(G, "_post", post)
    return calls


def use_or_key(monkeypatch, key=OR_KEY):
    monkeypatch.setattr(G, "_openrouter_key", lambda: key)


def mk_record(status, claims=(), **extra):
    rec = {"version": F.POLICY_VERSION, "input_hash": H, "status": status, "kind": "info", "forced": False,
           "reason": "", "claims": list(claims), "overall_why": "", "error": None, "override": None, "ack": None,
           "model": None, "search": None, "cost_usd": None, "checked_at": "2026-11-04T20:00:00+09:00"}
    rec.update(extra)
    return rec


def claim(cid, verdict, *, fact=None, sources=(), why="근거"):
    return {"id": cid, "claim": f"주장 {cid}", "source_quote": f"근거 {cid}", "verdict": verdict, "why": why,
            "fact": fact, "sources": list(sources)}


def res(cid, verdict, *, why="근거", fact=None, sources=()):
    return {"id": cid, "verdict": verdict, "why": why, "fact": fact, "sources": list(sources)}


def ver_for(results, *, overall="주의", cost=0.07):
    return {"results": list(results), "overall_why": overall, "model": F.VERIFY_MODEL, "search": "exa:5",
            "cost_usd": cost}


def verdict_answer(results, chunks, *, overall="주의", cost=0.07):
    return gem({"results": results, "overall_why": overall}, chunks=chunks, cost=cost)


# ---------------------------------------------------------------- 1. 주장 확인 키 (input_hash)
def test_input_hash_is_sha256_of_title_newline_text():
    assert F.input_hash(TITLE, SRC) == hashlib.sha256((TITLE + "\n" + SRC).encode("utf-8")).hexdigest()
    assert F.input_hash(TITLE, SRC) != F.input_hash(TITLE, SRC + " ")


# ---------------------------------------------------------------- 2. classify
def classified(kind, claims=(), reason="정책 발표"):
    return gem({"kind": kind, "reason": reason, "claims": list(claims)})


def test_classify_info_keeps_claims_with_ids_and_sends_json_mode(monkeypatch):
    calls = fake_post(monkeypatch, classified("info", [CLAIM1, CLAIM2]))
    out = F.classify(TITLE, SRC)
    assert out["kind"] == "info" and out["reason"] == "정책 발표"
    assert [c["id"] for c in out["claims"]] == ["C1", "C2"]
    assert out["claims"][0] == {"id": "C1", **CLAIM1}
    (call,) = calls
    assert call["model"] == F.CLASSIFY_MODEL == "gemini-3.8-flash"
    assert call["timeout"] == 30
    assert call["body"]["generationConfig"] == {"responseMimeType": "application/json"}
    assert "tools" not in call["body"]


def test_classify_general_drops_claims_when_not_forced(monkeypatch):
    fake_post(monkeypatch, classified("general", [CLAIM1]))
    out = F.classify(TITLE, SRC)
    assert out["kind"] == "general" and out["claims"] == []


def test_classify_general_with_force_still_extracts_claims(monkeypatch):
    calls = fake_post(monkeypatch, classified("general", [CLAIM1]))
    out = F.classify(TITLE, SRC, force=True)
    assert out["kind"] == "general" and [c["id"] for c in out["claims"]] == ["C1"]
    assert "사실 주장이 하나라도 있으면" in calls[0]["body"]["contents"][0]["parts"][0]["text"]


@pytest.mark.parametrize("kind", ["uncertain", "maybe", "", None])
def test_classify_unsure_or_unknown_kind_is_uncertain_and_keeps_claims(monkeypatch, kind):
    fake_post(monkeypatch, gem({"kind": kind, "reason": "", "claims": [CLAIM1]}))
    out = F.classify(TITLE, SRC)
    assert out["kind"] == "uncertain"
    assert [c["id"] for c in out["claims"]] == ["C1"]


def test_classify_drops_claim_whose_quote_is_not_in_source_and_renumbers(monkeypatch):
    fake = {"kind": "info", "reason": "", "claims": [
        {"claim": "지어낸 주장", "query": "q", "source_quote": "이 글에 없는 말입니다"}, CLAIM2]}
    fake_post(monkeypatch, gem(fake))
    out = F.classify(TITLE, SRC)
    assert out["claims"] == [{"id": "C1", **CLAIM2}]


def test_classify_quote_match_ignores_spacing_quotes_and_punctuation(monkeypatch):
    loose = {"claim": "c", "query": "q", "source_quote": "“식약처가   가공유 판매를 금지한다고 발표했다!”"}
    fake_post(monkeypatch, gem({"kind": "info", "reason": "", "claims": [loose]}))
    out = F.classify(TITLE, SRC)
    assert [c["id"] for c in out["claims"]] == ["C1"]


def test_classify_keeps_at_most_three_claims(monkeypatch):
    many = [{"claim": f"주장{i}", "query": "q", "source_quote": Q1} for i in range(5)]
    fake_post(monkeypatch, gem({"kind": "info", "reason": "", "claims": many}))
    out = F.classify(TITLE, SRC)
    assert [c["id"] for c in out["claims"]] == ["C1", "C2", "C3"]


def test_classify_empty_quote_is_dropped(monkeypatch):
    fake_post(monkeypatch, gem({"kind": "info", "reason": "", "claims": [{"claim": "x", "query": "q", "source_quote": "  "}]}))
    assert F.classify(TITLE, SRC)["claims"] == []


def test_classify_api_failure_becomes_factcheck_error_and_hides_key(monkeypatch):
    def boom(model, body):
        raise G.GeminiApiError(f"OpenRouter 응답 이상 {OR_KEY}", provider="openrouter")
    fake_post(monkeypatch, boom)
    use_or_key(monkeypatch)
    with pytest.raises(F.FactcheckError) as e:
        F.classify(TITLE, SRC)
    assert "사실 확인 분류에 실패했습니다" in str(e.value) and OR_KEY not in str(e.value)


def test_classify_unparseable_answer_becomes_factcheck_error(monkeypatch):
    fake_post(monkeypatch, gem("JSON 이 아닌 글"))
    with pytest.raises(F.FactcheckError):
        F.classify(TITLE, SRC)


# ---------------------------------------------------------------- 3. verify
def test_verify_without_openrouter_key_gives_the_exact_message(monkeypatch):
    monkeypatch.setattr(G, "_openrouter_key", lambda: "")
    calls = fake_post(monkeypatch, gem({}))
    with pytest.raises(F.FactcheckError) as e:
        F.verify([VCLAIM])
    assert str(e.value) == "사실 확인에는 OpenRouter 키가 필요합니다. 설정에서 넣어 주세요."
    assert calls == []


def test_verify_asks_with_google_search_tool_model_and_120s(monkeypatch):
    use_or_key(monkeypatch)
    calls = fake_post(monkeypatch, verdict_answer([], chunks=[]))
    F.verify([VCLAIM])
    (c,) = calls
    assert c["model"] == F.VERIFY_MODEL == "anthropic/claude-opus-5.5"
    assert c["timeout"] == 120
    assert c["body"]["tools"] == [{"google_search": {}}]
    assert c["body"]["generationConfig"] == {"responseMimeType": "application/json"}
    prompt = c["body"]["contents"][0]["parts"][0]["text"]
    assert '"public_event": true | false' in prompt
    assert "주장의 성격으로 정한다" in prompt


def test_verify_starts_user_message_with_all_queries_in_one_call(monkeypatch):
    use_or_key(monkeypatch)
    calls = fake_post(monkeypatch, verdict_answer([], chunks=[]))
    claims = [VCLAIM, {"id": "C2", **CLAIM2}]
    F.verify(claims)
    (call,) = calls
    (message,) = call["body"]["contents"]
    assert message["role"] == "user"
    prompt = message["parts"][0]["text"]
    assert prompt.splitlines()[0] == f"검색어: {VCLAIM['query']} / {CLAIM2['query']}"
    assert prompt.index("검색어: ") < prompt.index("verdict 는") < prompt.index("주장:\n")
    for c in claims:
        assert f"- {c['id']}: {c['claim']}" in prompt


@pytest.mark.parametrize("query_fields", [{"query": ""}, {"query": "   "}, {"query": None}, {}])
def test_verify_search_line_falls_back_to_claim_when_query_empty(monkeypatch, query_fields):
    use_or_key(monkeypatch)
    calls = fake_post(monkeypatch, verdict_answer([], chunks=[]))
    fallback = {k: v for k, v in VCLAIM.items() if k != "query"}
    fallback.update(query_fields)
    F.verify([fallback, {"id": "C2", **CLAIM2}])
    (call,) = calls
    prompt = call["body"]["contents"][0]["parts"][0]["text"]
    assert prompt.splitlines()[0] == f"검색어: {VCLAIM['claim']} / {CLAIM2['query']}"


def test_verify_keeps_only_cited_urls_and_takes_title_from_metadata(monkeypatch):
    use_or_key(monkeypatch)
    chunks = [web(URL_A, "식약처, 가공유 표시 개선 검토"), web("https://news.example.org/a", "다른 기사")]
    model_url = "HTTPS://Biz.Chosun.com" + URL_A[len("https://biz.chosun.com"):] + "#sec"
    results = [{"id": "C1", "verdict": "partly", "why": "검토 단계", "fact": "표시 개선을 검토 중이다",
                "sources": [model_url, "https://made-up.example.com/x"]}]
    fake_post(monkeypatch, verdict_answer(results, chunks))
    out = F.verify([VCLAIM])
    r = out["results"][0]
    assert r["verdict"] == "partly" and r["fact"] == "표시 개선을 검토 중이다" and r["why"] == "검토 단계"
    assert r["sources"] == [{"url": URL_A, "title": "식약처, 가공유 표시 개선 검토", "outlet": "조선일보",
                             "date": "2026-11-02"}]


def test_verify_takes_date_only_from_url_path(monkeypatch):
    use_or_key(monkeypatch)
    results = [{"id": "C1", "verdict": "partly", "why": "", "fact": "f", "sources": [URL_B, URL_A]}]
    chunks = [web(URL_B, "연합 기사"), web(URL_A, "조선 기사")]
    fake_post(monkeypatch, verdict_answer(results, chunks))
    srcs = F.verify([VCLAIM])["results"][0]["sources"]
    assert [(s["url"], s["date"]) for s in srcs] == [(URL_B, None), (URL_A, "2026-11-02")]
    assert srcs[0]["outlet"] == "연합뉴스"


@pytest.mark.parametrize("verdict", ["true", "partly"])
def test_verify_downgrades_true_or_partly_without_kept_source_to_unverified(monkeypatch, verdict):
    use_or_key(monkeypatch)
    results = [{"id": "C1", "verdict": verdict, "why": "맞다", "fact": "사실",
                "sources": ["https://made-up.example.com/x"]}]
    fake_post(monkeypatch, verdict_answer(results, chunks=[web(URL_A, "제목")]))
    r = F.verify([VCLAIM])["results"][0]
    assert r["verdict"] == "unverified" and r["sources"] == [] and r["fact"] is None
    assert r["why"] == "인용된 출처가 없어 확인 불가로 내렸습니다."


def test_verify_false_stays_false_without_sources(monkeypatch):
    use_or_key(monkeypatch)
    results = [{"id": "C1", "verdict": "false", "why": "보도와 다름", "fact": "그건 아님", "sources": []}]
    fake_post(monkeypatch, verdict_answer(results, chunks=[]))
    r = F.verify([VCLAIM])["results"][0]
    assert r["verdict"] == "false" and r["fact"] == "그건 아님"


def test_verify_false_fact_survives_record_but_is_not_a_topic_fact(monkeypatch, tmp_path):
    use_or_key(monkeypatch)
    fact = "식약처는 원유 함량 표시 제도 개선 검토에 착수했다."
    results = [res("C1", "false", why=fact, fact=f"  {fact}  ", sources=[URL_A])]
    fake_post(monkeypatch, verdict_answer(results, chunks=[web(URL_A, "표시 제도 개선 검토")]))
    ver = F.verify([VCLAIM])
    assert ver["results"][0]["verdict"] == "false"
    assert ver["results"][0]["fact"] == fact
    assert ver["results"][0]["why"] == fact
    assert ver["results"][0]["sources"][0]["url"] == URL_A
    rec = F.build_record(TITLE, SRC, {"kind": "info", "claims": [VCLAIM]}, ver)
    F.save_record(tmp_path, rec)
    saved = F.load_record(tmp_path)
    assert saved["claims"][0]["fact"] == fact
    assert saved["status"] == "stop"
    assert F.gate(saved, TITLE, SRC, F.POLICY_VERSION)[0] == "blocked"
    assert F.topic_additions(saved) == {
        "facts": [],
        "false_claims": [{"id": "C1", "claim": VCLAIM["claim"], "source_quote": VCLAIM["source_quote"]}],
        "unverified_claims": [],
    }


@pytest.mark.parametrize("fact", [None, "", "   "])
def test_verify_false_empty_fact_stays_none(monkeypatch, fact):
    use_or_key(monkeypatch)
    fake_post(monkeypatch, verdict_answer([res("C1", "false", fact=fact)], chunks=[]))
    r = F.verify([VCLAIM])["results"][0]
    assert r["verdict"] == "false" and r["fact"] is None


def test_verify_unverified_discards_model_fact(monkeypatch):
    use_or_key(monkeypatch)
    results = [res("C1", "unverified", fact="확인되지 않은 문장", sources=[URL_A])]
    fake_post(monkeypatch, verdict_answer(results, chunks=[web(URL_A, "기사")]))
    r = F.verify([VCLAIM])["results"][0]
    assert r["verdict"] == "unverified" and r["fact"] is None


@pytest.mark.parametrize("event_fields,expected", [
    ({"public_event": True}, True), ({"public_event": False}, False), ({}, False),
    ({"public_event": "false"}, False), ({"public_event": None}, False),
])
def test_verify_public_event_boolean_and_why_prefix(monkeypatch, event_fields, expected):
    use_or_key(monkeypatch)
    why = "해당 발표를 확인할 수 있는 보도가 없습니다."
    results = [{**res("C1", "unverified", why=why), **event_fields}]
    fake_post(monkeypatch, verdict_answer(results, chunks=[]))
    r = F.verify([VCLAIM])["results"][0]
    assert r["verdict"] == "unverified" and r["public_event"] is expected
    prefix = "반드시 보도될 일인데 관련 보도를 찾지 못했습니다. " if expected else ""
    assert r["why"] == prefix + why
    assert F.overall_status([r]) == ("stop" if expected else "warn")


@pytest.mark.parametrize("verdict", ["true", "partly"])
def test_verify_public_event_without_cited_source_downgrades_and_stops(monkeypatch, verdict):
    use_or_key(monkeypatch)
    result = {**res("C1", verdict, why="모델의 설명", fact="검증되지 않은 내용",
                    sources=["https://made-up.example.com/x"]), "public_event": True}
    fake_post(monkeypatch, verdict_answer([result], chunks=[]))
    r = F.verify([VCLAIM])["results"][0]
    assert r["verdict"] == "unverified" and r["public_event"] is True
    assert r["fact"] is None and r["sources"] == []
    assert r["why"] == "반드시 보도될 일인데 관련 보도를 찾지 못했습니다. 인용된 출처가 없어 확인 불가로 내렸습니다. 모델의 설명"
    assert F.overall_status([r]) == "stop"


@pytest.mark.parametrize("verdict,status", [("true", "pass"), ("partly", "warn"), ("false", "stop")])
def test_verify_public_event_keeps_supported_verdict_and_why(monkeypatch, verdict, status):
    use_or_key(monkeypatch)
    result = {**res("C1", verdict, why="보도로 확인한 내용", fact="사실", sources=[URL_A]), "public_event": True}
    fake_post(monkeypatch, verdict_answer([result], chunks=[web(URL_A, "기사")]))
    r = F.verify([VCLAIM])["results"][0]
    assert r["verdict"] == verdict and r["public_event"] is True
    assert r["why"] == "보도로 확인한 내용"
    assert F.overall_status([r]) == status


def test_verify_unknown_verdict_and_missing_result_become_unverified(monkeypatch):
    use_or_key(monkeypatch)
    results = [{"id": "C1", "verdict": "사실", "why": "x", "fact": None, "sources": []}]
    fake_post(monkeypatch, verdict_answer(results, chunks=[]))
    out = F.verify([VCLAIM, {**VCLAIM, "id": "C2"}])
    assert [r["id"] for r in out["results"]] == ["C1", "C2"]
    assert [r["verdict"] for r in out["results"]] == ["unverified", "unverified"]
    assert [r["public_event"] for r in out["results"]] == [False, False]
    assert out["results"][1]["why"] == "판정 결과가 없습니다."


def test_verify_result_metadata_and_cost(monkeypatch):
    use_or_key(monkeypatch)
    fake_post(monkeypatch, verdict_answer([], chunks=[], overall="두 주장 모두 확인 안 됨", cost=0.07))
    out = F.verify([VCLAIM])
    assert out["overall_why"] == "두 주장 모두 확인 안 됨"
    assert out["model"] == F.VERIFY_MODEL and out["search"] == "exa:5" and out["cost_usd"] == 0.07


def test_verify_api_failure_is_factcheck_error_and_never_shows_the_key(monkeypatch):
    use_or_key(monkeypatch)

    def boom(model, body):
        raise G.GeminiApiError(f"OpenRouter 응답 이상 {OR_KEY}", provider="openrouter")
    fake_post(monkeypatch, boom)
    with pytest.raises(F.FactcheckError) as e:
        F.verify([VCLAIM])
    assert "사실 확인 판정에 실패했습니다" in str(e.value) and OR_KEY not in str(e.value)


def test_verify_unparseable_answer_is_factcheck_error(monkeypatch):
    use_or_key(monkeypatch)
    fake_post(monkeypatch, gem("답이 JSON 이 아닙니다"))
    with pytest.raises(F.FactcheckError):
        F.verify([VCLAIM])


def test_verify_through_the_openrouter_adapter(monkeypatch):
    use_or_key(monkeypatch)
    logged = []
    monkeypatch.setattr(usage, "log", lambda kind, **f: logged.append(kind))
    sent = []
    content = json.dumps({"results": [{"id": "C1", "verdict": "partly", "why": "검토 단계", "fact": "검토 중이다",
                                       "sources": [URL_A]}], "overall_why": "주의"}, ensure_ascii=False)
    body = {"choices": [{"message": {"role": "assistant", "content": content, "annotations": [
        {"type": "url_citation", "url_citation": {"url": URL_A, "title": "식약처 검토 기사", "content": "…",
                                                  "start_index": 0, "end_index": 1}}]}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0612}}

    def post(url, **kw):
        sent.append((url, kw["json"]))
        return Resp(200, body)
    monkeypatch.setattr(requests, "post", post)
    out = F.verify([VCLAIM])
    (url, req), = sent
    assert url == OR_URL and req["model"] == "anthropic/claude-opus-5.5"
    assert req["plugins"] == [{"id": "web", "engine": "exa", "max_results": 5}]
    assert req["response_format"] == {"type": "json_object"}
    r = out["results"][0]
    assert r["verdict"] == "partly" and r["sources"][0]["title"] == "식약처 검토 기사"
    assert r["sources"][0]["url"] == URL_A and out["cost_usd"] == 0.0612
    assert logged == ["openrouter"]                       # 사용량은 어댑터가 한 번만 남긴다


# ---------------------------------------------------------------- 4. safe_url · date_from_url
@pytest.mark.parametrize("raw,expect", [
    ("HTTPS://News.Example.com/a/b?x=1&y=2#frag", "https://news.example.com/a/b?x=1&y=2"),
    ("http://example.com:8080/x", "http://example.com:8080/x"),
    ("https://example.com", "https://example.com"),
    ("  https://example.com/a  ", "https://example.com/a"),
])
def test_safe_url_accepts_public_http_urls(raw, expect):
    assert F.safe_url(raw) == expect


@pytest.mark.parametrize("raw", [
    None, "", "   ", "ftp://example.com/a", "javascript:alert(1)", "example.com/a",
    "https://user:pw@example.com/", "https://@example.com/", "http://localhost/x", "http://app.localhost/",
    "http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.0.1/", "http://169.254.169.254/latest",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://0x7f.0.0.1/",
    "http://example.com:99999/", "https://" + "a" * 500 + ".com/",
])
def test_safe_url_rejects_unsafe_urls(raw):
    assert F.safe_url(raw) is None


@pytest.mark.parametrize("url,expect", [
    (URL_A, "2026-11-02"),
    ("https://www.example.com/news/20261102/title", "2026-11-02"),
    ("https://www.example.com/2026/11/02/x.html", "2026-11-02"),
    (URL_B, None),
    ("https://www.example.com/news/2026/02/30/x", None),
    ("https://www.example.com/a?d=20261102", None),
    ("https://www.example.com/news/123456789/x", None),
])
def test_date_from_url_reads_only_the_path(url, expect):
    assert F.date_from_url(url) == expect


# ---------------------------------------------------------------- 5. 상태 묶음·기록 모양
@pytest.mark.parametrize("verdicts,expect", [
    ([], "warn"), (["true"], "pass"), (["true", "true"], "pass"),
    (["true", "partly"], "warn"), (["true", "unverified"], "warn"),
    (["false"], "stop"), (["true", "false", "unverified"], "stop"), (["partly", "false"], "stop"),
])
def test_overall_status_follows_the_verdicts(verdicts, expect):
    assert F.overall_status([{"verdict": v} for v in verdicts]) == expect


@pytest.mark.parametrize("event_fields,status", [
    ({"public_event": True}, "stop"), ({"public_event": False}, "warn"), ({}, "warn"),
])
def test_overall_status_unverified_public_event(event_fields, status):
    results = [{"verdict": "true"}, {"verdict": "unverified", **event_fields}, {"verdict": "partly"}]
    assert F.overall_status(results) == status


def test_build_record_general_is_skipped_and_has_no_claims():
    rec = F.build_record(TITLE, SRC, {"kind": "general", "reason": "잡담", "claims": []}, None)
    assert rec["status"] == "skipped" and rec["kind"] == "general" and rec["claims"] == []
    assert rec["forced"] is False and rec["error"] is None and rec["version"] == F.POLICY_VERSION
    assert rec["input_hash"] == H


def test_build_record_forced_general_runs_the_verdicts():
    cls = {"kind": "general", "reason": "잡담", "claims": [{"id": "C1", **CLAIM1}]}
    rec = F.build_record(TITLE, SRC, cls, ver_for([res("C1", "true", fact="사실", sources=[SRC_A])]), forced=True)
    assert rec["status"] == "pass" and rec["forced"] is True and rec["kind"] == "general"


def test_build_record_failed_with_error_message():
    rec = F.build_record(TITLE, SRC, None, None, error="사실 확인 분류에 실패했습니다.")
    assert rec["status"] == "failed" and rec["error"] == "사실 확인 분류에 실패했습니다."
    assert rec["kind"] is None and rec["claims"] == []


def test_build_record_merges_claims_and_copies_model_fields():
    cls = {"kind": "info", "reason": "정책 발표", "claims": [{"id": "C1", **CLAIM1}, {"id": "C2", **CLAIM2}]}
    ver = ver_for([res("C1", "partly", why="검토 단계", fact="검토 중", sources=[SRC_A]),
                   res("C2", "false", why="사실 아님")], overall="두 주장 확인")
    rec = F.build_record(TITLE, SRC, cls, ver)
    assert rec["status"] == "stop" and rec["kind"] == "info" and rec["reason"] == "정책 발표"
    assert rec["claims"][0] == {"id": "C1", "claim": CLAIM1["claim"], "source_quote": Q1, "verdict": "partly",
                                "why": "검토 단계", "fact": "검토 중", "sources": [SRC_A], "public_event": False}
    assert rec["claims"][1]["verdict"] == "false" and rec["claims"][1]["sources"] == []
    assert rec["overall_why"] == "두 주장 확인"
    assert rec["model"] == F.VERIFY_MODEL and rec["search"] == "exa:5" and rec["cost_usd"] == 0.07
    assert rec["override"] is None and rec["ack"] is None and rec["checked_at"]


def test_build_record_claim_without_verdict_is_unverified_and_warns():
    cls = {"kind": "info", "reason": "", "claims": [{"id": "C1", **CLAIM1}]}
    rec = F.build_record(TITLE, SRC, cls, None)
    assert rec["claims"][0]["verdict"] == "unverified" and rec["status"] == "warn"


def test_build_record_info_without_any_claim_warns_instead_of_passing():
    rec = F.build_record(TITLE, SRC, {"kind": "info", "reason": "", "claims": []}, None)
    assert rec["status"] == "warn" and rec["claims"] == []
    assert rec["overall_why"] == "검증할 주장을 찾지 못했습니다."


def test_topic_additions_facts_false_and_unverified():
    rec = mk_record("warn", claims=[
        claim("C1", "true", fact="첫 사실", sources=[SRC_A]),
        claim("C2", "partly", fact="바로잡은 문장", sources=[SRC_B]),
        claim("C3", "false", fact="보도로 확인된 실제 내용", sources=[SRC_A]),
        claim("C4", "unverified"),
        claim("C5", "true", fact=None, sources=[SRC_A]),
        claim("C6", "true", fact="출처 없음"),
    ])
    out = F.topic_additions(rec)
    assert out["facts"] == [
        {"id": "F1", "text": "첫 사실", "evidence": "첫 사실", "source": URL_A, "claim_id": "C1"},
        {"id": "F2", "text": "바로잡은 문장", "evidence": "바로잡은 문장", "source": URL_B, "claim_id": "C2"}]
    assert out["false_claims"] == [{"id": "C3", "claim": "주장 C3", "source_quote": "근거 C3"}]
    assert out["unverified_claims"] == [{"id": "C4", "claim": "주장 C4"}]


@pytest.mark.parametrize("record", [None, mk_record("skipped")])
def test_description_notice_default_for_none_and_skipped(record):
    assert F.description_notice(record) == (DEFAULT_NOTE, [])


def test_description_notice_pass_lists_each_source_once():
    rec = mk_record("pass", claims=[claim("C1", "true", fact="f", sources=[SRC_A]),
                                    claim("C2", "true", fact="g", sources=[SRC_A, SRC_B])])
    assert F.description_notice(rec) == (PASS_NOTE, ["조선일보 · 식약처 검토 기사 · " + URL_A,
                                                    "연합뉴스 · 연합 기사 · " + URL_B])


@pytest.mark.parametrize("status", ["warn", "stop", "failed"])
def test_description_notice_caution_for_warn_and_overridden_records(status):
    rec = mk_record(status, claims=[claim("C1", "partly", fact="f", sources=[SRC_A]), claim("C2", "false")])
    assert F.description_notice(rec) == (CAUTION_NOTE, ["조선일보 · 식약처 검토 기사 · " + URL_A])


def test_description_notice_skips_empty_outlet_or_title_parts():
    src = {"url": URL_B, "title": "", "outlet": "", "date": None}
    rec = mk_record("pass", claims=[claim("C1", "true", fact="f", sources=[src])])
    assert F.description_notice(rec) == (PASS_NOTE, [URL_B])


# ---------------------------------------------------------------- 6. gate: 모든 갈래
def test_gate_policy_none_is_always_clear():
    assert F.gate(None, TITLE, SRC, None) == ("clear", None)
    assert F.gate(mk_record("stop"), TITLE, "다른 글", None) == ("clear", None)


def test_gate_missing_or_old_policy_record_is_blocked():
    assert F.gate(None, TITLE, SRC, F.POLICY_VERSION) == ("blocked", NO_RESULT)
    assert F.gate(mk_record("pass", version=0), TITLE, SRC, F.POLICY_VERSION) == ("blocked", NO_RESULT)


@pytest.mark.parametrize("status", ["skipped", "pass", "warn", "stop", "failed"])
def test_gate_changed_text_is_stale_for_every_status(status):
    rec = mk_record(status, input_hash=F.input_hash(TITLE, SRC + " 고친 문장"),
                    override={"at": "t", "note": "n", "input_hash": F.input_hash(TITLE, SRC + " 고친 문장")},
                    ack={"at": "t", "input_hash": F.input_hash(TITLE, SRC + " 고친 문장")})
    assert F.gate(rec, TITLE, SRC, F.POLICY_VERSION) == ("blocked", STALE)


@pytest.mark.parametrize("status", ["skipped", "pass"])
def test_gate_skipped_and_pass_are_clear(status):
    assert F.gate(mk_record(status), TITLE, SRC, F.POLICY_VERSION) == ("clear", None)


def test_gate_warn_needs_a_matching_ack():
    rec = mk_record("warn")
    assert F.gate(rec, TITLE, SRC, 1) == ("need_ack", NEED_ACK)
    assert F.gate({**rec, "ack": {"at": "t", "input_hash": "other"}}, TITLE, SRC, 1) == ("need_ack", NEED_ACK)
    assert F.gate({**rec, "ack": {"at": "t", "input_hash": H}}, TITLE, SRC, 1) == ("clear", None)


def test_gate_stop_needs_a_matching_override():
    rec = mk_record("stop")
    assert F.gate(rec, TITLE, SRC, 1) == ("blocked", STOP_MSG)
    assert F.gate({**rec, "override": {"at": "t", "note": "n", "input_hash": "other"}}, TITLE, SRC, 1) == ("blocked", STOP_MSG)
    assert F.gate({**rec, "override": {"at": "t", "note": "n", "input_hash": H}}, TITLE, SRC, 1) == ("clear", None)


def test_gate_failed_shows_the_error_and_a_matching_override_clears_it():
    rec = mk_record("failed", error="OpenRouter 연결 실패 — 인터넷 연결을 확인하세요")
    assert F.gate(rec, TITLE, SRC, 1) == ("blocked", "OpenRouter 연결 실패 — 인터넷 연결을 확인하세요")
    assert F.gate(mk_record("failed"), TITLE, SRC, 1) == ("blocked", "사실 확인에 실패했습니다. 다시 확인해 주세요.")
    assert F.gate({**rec, "override": {"at": "t", "note": "n", "input_hash": H}}, TITLE, SRC, 1) == ("clear", None)
    assert F.gate({**rec, "override": {"at": "t", "note": "n", "input_hash": "other"}}, TITLE, SRC, 1)[0] == "blocked"


def test_gate_unknown_status_is_blocked():
    assert F.gate(mk_record("weird"), TITLE, SRC, 1) == ("blocked", NO_RESULT)


# ---------------------------------------------------------------- 7. script_check
def test_script_check_without_false_or_unverified_does_not_call(monkeypatch):
    monkeypatch.setattr(G, "_post", lambda *a, **k: pytest.fail("판정이 없으면 부르면 안 됨"))
    rec = mk_record("pass", claims=[claim("C1", "true", fact="f", sources=[SRC_A])])
    assert F.script_check(SCRIPT, rec) == []


def test_script_check_keeps_only_valid_flags_and_fills_text_and_verdict(monkeypatch):
    calls = fake_post(monkeypatch, gem({"flags": [
        {"claim_id": "C1", "field": "sentence", "index": 1},
        {"claim_id": "C1", "field": "sentence", "index": 9},
        {"claim_id": "C9", "field": "hook", "index": None},
        {"claim_id": "C2", "field": "closing", "index": None},
        {"claim_id": "C1", "field": "hook", "index": None},
        {"claim_id": "C1", "field": "hook", "index": None},
        {"claim_id": "C1", "field": "nonsense", "index": None},
        {"claim_id": "C3", "field": "sentence", "index": 0}]}))
    rec = mk_record("stop", claims=[claim("C1", "false"), claim("C2", "unverified"), claim("C3", "true", fact="f", sources=[SRC_A])])
    assert F.script_check(SCRIPT, rec) == [
        {"field": "sentence", "index": 1, "claim_id": "C1", "verdict": "false", "text": "흰우유도 함께 퇴출된다."},
        {"field": "closing", "index": None, "claim_id": "C2", "verdict": "unverified", "text": "정말일까요?"},
        {"field": "hook", "index": None, "claim_id": "C1", "verdict": "false", "text": "가공유가 금지된다"}]
    (c,) = calls
    assert c["model"] == F.CLASSIFY_MODEL and c["timeout"] == 30


def test_script_check_bad_answer_is_factcheck_error(monkeypatch):
    fake_post(monkeypatch, gem("JSON 아님"))
    with pytest.raises(F.FactcheckError):
        F.script_check(SCRIPT, mk_record("stop", claims=[claim("C1", "false")]))


# ---------------------------------------------------------------- 8. 저장·읽기
def test_save_load_round_trip_leaves_no_temp_file(tmp_path):
    rec = mk_record("pass", claims=[claim("C1", "true", fact="사실 한글", sources=[SRC_A])])
    F.save_record(tmp_path, rec)
    assert F.load_record(tmp_path) == rec
    assert sorted(p.name for p in tmp_path.iterdir()) == ["factcheck.json"]


@pytest.mark.parametrize("event_fields,expected,status", [
    ({"public_event": True}, True, "stop"), ({"public_event": False}, False, "warn"), ({}, False, "warn"),
])
def test_public_event_survives_verify_build_save_load_and_gate(monkeypatch, tmp_path, event_fields, expected, status):
    use_or_key(monkeypatch)
    result = {**res("C1", "unverified", why="관련 보도를 찾지 못했습니다."), **event_fields}
    fake_post(monkeypatch, verdict_answer([result], chunks=[]))
    ver = F.verify([VCLAIM])
    cls = {"kind": "info", "claims": [VCLAIM]}
    rec = F.build_record(TITLE, SRC, cls, ver)
    assert rec["claims"][0]["public_event"] is expected
    assert rec["status"] == status
    F.save_record(tmp_path, rec)
    saved = F.load_record(tmp_path)
    assert saved == rec
    assert saved["claims"][0]["public_event"] is expected
    assert saved["claims"][0]["why"] == ver["results"][0]["why"]
    assert F.gate(saved, TITLE, SRC, F.POLICY_VERSION) == (
        ("blocked", STOP_MSG) if expected else ("need_ack", NEED_ACK))
    saved["ack"] = {"at": "t", "input_hash": H}
    assert F.gate(saved, TITLE, SRC, F.POLICY_VERSION)[0] == ("blocked" if expected else "clear")
    saved["override"] = {"at": "t", "note": "직접 확인함", "input_hash": H}
    assert F.gate(saved, TITLE, SRC, F.POLICY_VERSION)[0] == "clear"


def test_load_missing_is_none(tmp_path):
    assert F.load_record(tmp_path) is None


def test_load_corrupt_file_is_none(tmp_path):
    (tmp_path / "factcheck.json").write_text("{깨진 json", encoding="utf-8")
    assert F.load_record(tmp_path) is None


def test_load_non_object_is_none(tmp_path):
    (tmp_path / "factcheck.json").write_text("[]", encoding="utf-8")
    assert F.load_record(tmp_path) is None


def test_load_other_policy_version_is_none(tmp_path):
    F.save_record(tmp_path, mk_record("pass", version=F.POLICY_VERSION + 1))
    assert F.load_record(tmp_path) is None


# ---------------------------------------------------------------- 9. 어댑터 변환(gemini_api)
def test_or_request_maps_google_search_tool_to_the_web_plugin():
    body = {"contents": [{"parts": [{"text": "p"}]}], "tools": [{"google_search": {}}]}
    req, _ = G._or_request("anthropic/claude-opus-5.5", body)
    assert req["plugins"] == [{"id": "web", "engine": "exa", "max_results": 5}]
    assert "tools" not in req


def test_or_request_without_search_tool_has_no_plugins():
    req, _ = G._or_request("google/gemini-3.8-flash", {"contents": [{"parts": [{"text": "p"}]}]})
    assert "plugins" not in req


def test_gemini_shape_maps_url_citations_and_cost():
    data = {"choices": [{"message": {"content": "{}", "annotations": [
        {"type": "url_citation", "url_citation": {"url": URL_A, "title": "제목", "content": "본문",
                                                  "start_index": 0, "end_index": 5}},
        {"type": "other"}]}, "finish_reason": "stop"}], "usage": {"cost": 0.07}}
    out = G._gemini_shape(data, "anthropic/claude-opus-5.5")
    cand = out["candidates"][0]
    assert cand["groundingMetadata"] == {"groundingChunks": [{"web": {"uri": URL_A, "title": "제목"}}]}
    assert out["usageMetadata"] == {"costUsd": 0.07}
    assert cand["content"]["parts"][0]["text"] == "{}" and cand["finishReason"] == "stop"


def test_gemini_shape_without_citations_or_cost_adds_nothing():
    data = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1}}
    assert G._gemini_shape(data, "google/gemini-3.8-flash") == {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "stop"}]}
