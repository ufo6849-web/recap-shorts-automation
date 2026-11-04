"""OpenRouter 엔진 시험(설계서 docs/PLAN_2026-11-04_openrouter-engine.md, W1): 경로 표, 요청·답 변환, 다시 보내기,
오류 분류, Gemini 경로가 그대로인지, 준비 상태(readiness)·기본 엔진, 서버 문구. 네트워크는 requests 를 가짜로 바꿔 쓴다."""
import json
import sys
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from recap_shorts import errors, modelfiles, settings, usage
from recap_shorts.images import provider
from recap_shorts.llm import gemini_api as G
from recap_shorts.llm import gemini_models as GM
from recap_shorts.llm.gemini_api import GeminiApiError

GEM_KEY = "test-gemini-key-0000000000"
OR_KEY = "sk-or-test-0000000000000000"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GEM_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-argon:generateContent"
ROOT = Path(__file__).resolve().parents[1]


class Resp:
    def __init__(self, code=200, body=None, text=None):
        self.status_code, self._body = code, (body if body is not None else {})
        self.text = text if text is not None else json.dumps(self._body, ensure_ascii=False)

    def json(self):
        return self._body


def or_ok(content, finish="stop", refusal=None, usage_=None):
    msg = {"role": "assistant", "content": content}
    if refusal is not None:
        msg["refusal"] = refusal
    u = ({"prompt_tokens": 11, "completion_tokens": 7, "completion_tokens_details": {"reasoning_tokens": 3},
          "cost": 0.00042} if usage_ is None else usage_)
    return Resp(200, {"choices": [{"message": msg, "finish_reason": finish}], "usage": u})


def use_keys(gemini=False, openrouter=False):
    keys = {}
    if gemini:
        keys["gemini_api_key"] = GEM_KEY
    if openrouter:
        keys["openrouter_api_key"] = OR_KEY
    if keys:
        settings.save(keys)


@pytest.fixture
def logs(monkeypatch):
    rows = []
    monkeypatch.setattr(usage, "log", lambda kind, **f: rows.append((kind, f)))
    return rows


@pytest.fixture
def models_ok(monkeypatch):
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)


# ---------------------------------------------------------------- 1. 경로 표
@pytest.mark.parametrize("model,g,o,expect", [
    ("anthropic/claude-opus-5.5", False, True, ("openrouter", "anthropic/claude-opus-5.5")),
    ("anthropic/claude-opus-5.5", True, True, ("openrouter", "anthropic/claude-opus-5.5")),
    ("gemini-3.8-flash", True, False, ("gemini", "gemini-3.8-flash")),
    ("gemini-3.8-flash", True, True, ("gemini", "gemini-3.8-flash")),
    ("gemini-3.8-flash", False, True, ("openrouter", "google/gemini-3.8-flash")),
])
def test_route_table_rows_that_go_somewhere(model, g, o, expect):
    assert G.route(model, g, o) == expect


def test_route_vendor_model_without_openrouter_key_is_no_key():
    with pytest.raises(GeminiApiError, match="이 모델은 OpenRouter 키가 필요합니다") as e:
        G.route("anthropic/claude-opus-5.5", True, False)
    assert e.value.kind == errors.NO_KEY and e.value.provider == "openrouter"


def test_route_plain_model_without_any_key_is_gemini_no_key():
    with pytest.raises(GeminiApiError) as e:
        G.route("gemini-3.8-flash", False, False)
    assert e.value.kind == errors.NO_KEY and e.value.provider == "gemini"
    assert errors.screen_text(e.value) == "Gemini API 키가 없습니다 — 화면 오른쪽 위 '설정'에서 넣으세요"


# ---------------------------------------------------------------- 2. Gemini 경로는 바이트 단위로 그대로
def test_gemini_path_request_is_unchanged(monkeypatch, logs):
    use_keys(gemini=True, openrouter=True)               # OpenRouter 키가 있어도 슬래시 없는 모델은 Gemini 로 간다
    seen = []

    def post(url, **kw):
        seen.append((url, kw))
        return Resp(200, {"candidates": [{"content": {"parts": [{"text": "답"}]}}],
                          "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2,
                                            "thoughtsTokenCount": 1, "totalTokenCount": 8}})
    monkeypatch.setattr(requests, "post", post)
    body = {"contents": [{"role": "user", "parts": [{"text": "p"}]}], "generationConfig": {"temperature": 0.1}}
    out = G._post("gemini-4-argon", body)
    assert out["candidates"][0]["content"]["parts"][0]["text"] == "답"
    (url, kw), = seen
    assert url == GEM_URL
    assert set(kw) == {"headers", "json", "timeout"}
    assert kw["headers"] == {"x-goog-api-key": GEM_KEY, "Content-Type": "application/json"}
    assert kw["json"] is body and kw["timeout"] == 300.0
    assert logs == [("gemini_text", {"model": "gemini-4-argon", "prompt_tokens": 5, "output_tokens": 2,
                                     "thought_tokens": 1, "total_tokens": 8, "images": 0})]


def test_gemini_path_errors_carry_provider_gemini(monkeypatch):
    use_keys(gemini=True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(400, {"error": {"message": "API key not valid."}}))
    with pytest.raises(GeminiApiError) as e:
        G._post("gemini-4-argon", {"contents": []}, sleep=lambda s: None)
    assert e.value.kind == errors.BAD_KEY and e.value.provider == "gemini"
    assert errors.screen_text(e.value) == "Gemini API 키가 맞지 않습니다 — 오른쪽 위 '설정'에서 키를 확인하세요"


# ---------------------------------------------------------------- 3. 요청 변환
def test_openrouter_request_converts_parts_and_settings(monkeypatch, logs):
    use_keys(openrouter=True)
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: sent.append((url, kw)) or or_ok('{"a": 1}'))
    body = {"contents": [{"role": "user", "parts": [
        {"text": "글"},
        {"inline_data": {"mime_type": "image/png", "data": "QUJD"}},
        {"inline_data": {"mime_type": "audio/wav", "data": "UklGRg=="}}]}],
        "generationConfig": {"responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"},
                             "temperature": 0.2}}
    assert G._post("gemini-3.8-flash", body, sleep=lambda s: None)
    url, kw = sent[0]
    assert url == OR_URL
    assert kw["headers"] == {"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json"}
    j = kw["json"]
    assert j["model"] == "google/gemini-3.8-flash"
    assert j["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "글"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        {"type": "input_audio", "input_audio": {"data": "UklGRg==", "format": "wav"}}]}]
    assert j["response_format"] == {"type": "json_object"}
    assert j["reasoning"] == {"effort": "low"} and j["temperature"] == 0.2
    assert j["max_tokens"] == 32000 and j["usage"] == {"include": True}


def test_anthropic_request_leaves_out_temperature(monkeypatch, logs):
    use_keys(openrouter=True)
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: sent.append(kw["json"]) or or_ok("글"))
    G._post("anthropic/claude-opus-5.5", {"contents": [{"parts": [{"text": "p"}]}],
                                          "generationConfig": {"temperature": 0.2}}, sleep=lambda s: None)
    assert sent[0]["model"] == "anthropic/claude-opus-5.5"          # 슬래시가 있으면 그대로(google/ 를 붙이지 않음)
    assert "temperature" not in sent[0] and "response_format" not in sent[0] and "reasoning" not in sent[0]


def test_unknown_mime_is_value_error(monkeypatch):
    use_keys(openrouter=True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("보내면 안 됨"))
    body = {"contents": [{"parts": [{"inline_data": {"mime_type": "video/mp4", "data": "AAAA"}}]}]}
    with pytest.raises(ValueError):
        G._post("gemini-3.8-flash", body)


# ---------------------------------------------------------------- 4. 답 정규화
def test_string_content_is_returned(monkeypatch, logs):
    use_keys(openrouter=True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: or_ok("  그냥 글  "))
    assert G.generate_text("p", "anthropic/claude-opus-5.5", json=False) == "그냥 글"


def test_list_content_joins_text_parts_only(monkeypatch, logs):
    use_keys(openrouter=True)
    content = [{"type": "text", "text": "앞"}, {"type": "reasoning", "text": "숨은 생각"}, {"type": "text", "text": "뒤"}]
    monkeypatch.setattr(requests, "post", lambda *a, **k: or_ok(content))
    assert G.generate_text("p", "anthropic/claude-opus-5.5", json=False) == "앞뒤"


def test_empty_content_keeps_finish_reason(monkeypatch, logs):
    use_keys(openrouter=True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: or_ok("", finish="length"))
    with pytest.raises(GeminiApiError, match=r"비어 있음\(finishReason=length\)"):
        G.generate_text("p", "anthropic/claude-opus-5.5", json=False)


@pytest.mark.parametrize("kw", [{"finish": "content_filter"}, {"refusal": "I can't help with that."}])
def test_content_filter_or_refusal_is_safety_block(monkeypatch, logs, kw):
    from recap_shorts.images import sensitive
    use_keys(openrouter=True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: or_ok("", **kw))
    with pytest.raises(GeminiApiError) as e:
        G.generate_text("p", "google/gemini-3.8-flash", json=False)
    assert "blockReason" in str(e.value) and sensitive._is_block(e.value)


# ---------------------------------------------------------------- 5. 사용량은 성공 한 번에 한 번
def test_usage_logged_once_with_fields(monkeypatch, logs):
    use_keys(openrouter=True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: or_ok("ok"))
    G.generate_text("p", "anthropic/claude-opus-5.5", json=False)
    assert logs == [("openrouter", {"model": "anthropic/claude-opus-5.5", "prompt_tokens": 11, "output_tokens": 7,
                                    "reasoning_tokens": 3, "cost": 0.00042, "images": 0, "audio": 0})]


def test_usage_non_numbers_become_none(monkeypatch, logs):
    use_keys(openrouter=True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: or_ok("ok", usage_={"prompt_tokens": "x", "cost": True}))
    G.generate_text("p", "google/gemini-3.8-flash", json=False)
    f = logs[0][1]
    assert f["prompt_tokens"] is None and f["cost"] is None and f["output_tokens"] is None and f["reasoning_tokens"] is None


def test_format_retry_logs_usage_only_on_success(monkeypatch, logs):
    use_keys(openrouter=True)
    seq, sent = [Resp(400, {"error": {"message": "response_format json_object is not supported"}}), or_ok('{"a": 1}')], []

    def post(url, **kw):
        sent.append(kw["json"])
        return seq.pop(0)
    monkeypatch.setattr(requests, "post", post)
    assert G.generate_text("p", "google/gemini-3.8-flash") == {"a": 1}
    assert "response_format" in sent[0] and "response_format" not in sent[1]     # 두 번째는 JSON 모드를 뺀다
    assert len(logs) == 1 and logs[0][0] == "openrouter"


# ---------------------------------------------------------------- 6. 다시 보내기
def test_other_400_is_not_retried(monkeypatch, logs):
    use_keys(openrouter=True)
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or Resp(400, {"error": {"message": "bad"}}))
    with pytest.raises(GeminiApiError) as e:
        G._post("google/gemini-3.8-flash", {"contents": [{"parts": [{"text": "p"}]}],
                                            "generationConfig": {"responseMimeType": "application/json"}},
                sleep=lambda s: pytest.fail("다시 보내면 안 됨"))
    assert len(calls) == 1 and e.value.kind == errors.HTTP and e.value.http == 400


def test_json_400_without_json_mode_is_not_retried(monkeypatch, logs):
    use_keys(openrouter=True)
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or Resp(400, {"error": {"message": "json broke"}}))
    with pytest.raises(GeminiApiError):
        G._post("google/gemini-3.8-flash", {"contents": [{"parts": [{"text": "p"}]}]}, sleep=lambda s: None)
    assert len(calls) == 1


def test_connection_failure_retries_twice_then_net(monkeypatch, logs):
    use_keys(openrouter=True)
    calls, sleeps = [], []

    def boom(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(GeminiApiError) as e:
        G._post("anthropic/claude-opus-5.5", {"contents": []}, sleep=sleeps.append)
    assert len(calls) == 3 and sleeps == [5, 10]
    assert e.value.kind == errors.NET and e.value.provider == "openrouter"
    assert errors.screen_text(e.value) == "OpenRouter 연결 실패 — 인터넷 연결을 확인하세요"


def test_429_three_times_is_quota(monkeypatch, logs):
    use_keys(openrouter=True)
    calls, sleeps = [], []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or Resp(429, {"error": {"message": "slow"}}))
    with pytest.raises(GeminiApiError) as e:
        G._post("anthropic/claude-opus-5.5", {"contents": []}, sleep=sleeps.append)
    assert len(calls) == 3 and sleeps == [5, 10]
    assert e.value.kind == errors.QUOTA and e.value.http == 429 and e.value.provider == "openrouter"
    assert errors.screen_text(e.value) == "OpenRouter 잔액·한도가 모자랍니다 — openrouter.ai 에서 확인하세요"


def test_500_then_ok_retries_once(monkeypatch, logs):
    use_keys(openrouter=True)
    seq, sleeps = [Resp(500, {"error": {"message": "upstream"}}), or_ok("ok")], []
    monkeypatch.setattr(requests, "post", lambda *a, **k: seq.pop(0))
    out = G._post("anthropic/claude-opus-5.5", {"contents": [{"parts": [{"text": "p"}]}]}, sleep=sleeps.append)
    assert out["candidates"][0]["content"]["parts"][0]["text"] == "ok"
    assert sleeps == [5]


def test_200_without_choices_retries_then_http(monkeypatch, logs):
    use_keys(openrouter=True)
    calls, sleeps = [], []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or Resp(200, {"id": "x"}))
    with pytest.raises(GeminiApiError) as e:
        G._post("anthropic/claude-opus-5.5", {"contents": []}, sleep=sleeps.append)
    assert len(calls) == 3 and sleeps == [5, 10] and e.value.kind == errors.HTTP
    assert logs == []                                                  # 성공이 아니면 사용량을 남기지 않는다


# ---------------------------------------------------------------- 7. 오류 분류·문구·키 값
def test_401_is_bad_key_once_and_hides_key(monkeypatch, logs):
    use_keys(openrouter=True)
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or Resp(401, {"error": {"message": f"no {OR_KEY}"}}))
    with pytest.raises(GeminiApiError) as e:
        G._post("anthropic/claude-opus-5.5", {"contents": []}, sleep=lambda s: pytest.fail("키 오류는 다시 보내지 않음"))
    assert len(calls) == 1 and e.value.kind == errors.BAD_KEY and e.value.http == 401
    assert OR_KEY not in str(e.value) and OR_KEY not in errors.screen_text(e.value)
    assert errors.screen_text(e.value) == "OpenRouter API 키가 맞지 않습니다 — 오른쪽 위 '설정'에서 키를 확인하세요"


def test_402_is_quota_without_retry(monkeypatch, logs):
    use_keys(openrouter=True)
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or Resp(402, {"error": {"message": "credits"}}))
    with pytest.raises(GeminiApiError) as e:
        G._post("anthropic/claude-opus-5.5", {"contents": []}, sleep=lambda s: pytest.fail("잔액 부족은 다시 보내지 않음"))
    assert len(calls) == 1 and e.value.kind == errors.QUOTA and e.value.http == 402
    assert str(e.value) == "OpenRouter 잔액이나 키 한도가 모자랍니다"
    assert errors.screen_text(e.value) == "OpenRouter 잔액·한도가 모자랍니다 — openrouter.ai 에서 확인하세요"


def test_kind_for_http_402_is_quota():
    assert errors.kind_for_http(402) == errors.QUOTA
    assert errors.kind_for_http(429) == errors.QUOTA and errors.kind_for_http(500) == errors.HTTP


def test_screen_text_openrouter_kinds_and_inferred_provider():
    e = GeminiApiError("x", kind=errors.NO_KEY, provider="openrouter")
    assert errors.screen_text(e) == "OpenRouter API 키가 없습니다 — 화면 오른쪽 위 '설정'에서 넣으세요"
    # 붙은 provider 가 없어도 문구에 OpenRouter 가 있으면 OpenRouter 문구
    assert errors.screen_text(RuntimeError("OpenRouter 실패(x): HTTP 401: nope")) \
        == "OpenRouter API 키가 맞지 않습니다 — 오른쪽 위 '설정'에서 키를 확인하세요"
    assert errors.screen_text(RuntimeError("OpenRouter 연결 실패(x): ConnectionError")) \
        == "OpenRouter 연결 실패 — 인터넷 연결을 확인하세요"


# ---------------------------------------------------------------- 8. 준비 상태·기본 엔진·모델 이름
@pytest.mark.parametrize("gemini,openrouter,script,voice,ok,reason,via", [
    (False, False, None, None, False, "Gemini 또는 OpenRouter API 키가 필요합니다", None),
    (True, False, None, None, True, None, "gemini"),
    (True, False, "anthropic/claude-opus-5.5", None, False,
     "대본 모델(anthropic/claude-opus-5.5)은 OpenRouter 키가 필요합니다", "gemini"),
    (False, True, None, None, True, None, "openrouter"),
    (False, True, "anthropic/claude-opus-5.5", None, True, None, "openrouter"),
    (False, True, None, "gemini", False,
     "목소리가 Gemini 인데 Gemini 키가 없습니다 — 설정에서 로컬 목소리를 고르세요", "openrouter"),
    (True, True, "anthropic/claude-opus-5.5", "gemini", True, None, "mixed"),
    (True, True, None, None, True, None, "gemini"),
])
def test_readiness_by_key_combo(models_ok, gemini, openrouter, script, voice, ok, reason, via):
    use_keys(gemini=gemini, openrouter=openrouter)
    if script:
        settings.save(models={"script": script})
    if voice:
        settings.save(voice={"engine": voice})
    assert settings.readiness() == {"ok": ok, "reason": reason, "via": via}


def test_readiness_supertonic_needs_model_files(monkeypatch):
    use_keys(openrouter=True)
    monkeypatch.setattr(modelfiles, "ok", lambda mid: False)
    r = settings.readiness()
    assert r["ok"] is False and r["reason"] == "로컬 목소리(Supertonic) 모델이 없습니다 — 설정 → 모델 파일에서 먼저 받으세요"


def test_public_view_ready_follows_readiness(models_ok):
    v = settings.public_view()
    assert v["ready"] is False and v["ready_reason"] == "Gemini 또는 OpenRouter API 키가 필요합니다"
    use_keys(openrouter=True)
    v = settings.public_view()
    assert v["ready"] is True and v["ready_reason"] is None


def test_llm_ready_needs_either_key():
    assert settings.llm_ready() is False
    use_keys(openrouter=True)
    assert settings.llm_ready() is True
    use_keys(gemini=True)
    assert settings.llm_ready() is True


def test_default_engines_follow_keys():
    assert settings.voice_engine() == "supertonic" and settings.image_engine() == "off"
    use_keys(openrouter=True)
    assert settings.voice_engine() == "supertonic" and settings.image_engine() == "off"
    use_keys(gemini=True)
    assert settings.voice_engine() == "gemini" and settings.image_engine() == "gemini"


def test_saved_engine_beats_default():
    use_keys(openrouter=True)
    settings.save(voice={"engine": "gemini"}, image_engine="gemini")
    assert settings.voice_engine() == "gemini" and settings.image_engine() == "gemini"


def test_vendor_model_is_saved_and_read_back():
    use_keys(openrouter=True)
    settings.save(models={"script": "anthropic/claude-opus-5.5"})
    assert settings.model("script") == "anthropic/claude-opus-5.5" and settings.model_source("script") == "file"
    assert json.loads(settings.path().read_text(encoding="utf-8"))["models"] == {"script": "anthropic/claude-opus-5.5"}


@pytest.mark.parametrize("role", ["tts", "image"])
def test_vendor_model_is_refused_for_other_roles(role):
    with pytest.raises(settings.SettingsError) as e:
        settings.save(models={role: "anthropic/claude-opus-5.5"})
    assert e.value.status == 400 and "쓸 수 없는 글자" in str(e.value)


def test_valid_model_id_rules():
    assert settings.valid_model_id("script", "anthropic/claude-opus-5.5")
    assert settings.valid_model_id("script", "gemini-4-argon")
    assert not settings.valid_model_id("script", "anthropic/" + "a" * 91)     # 전체 100자를 넘으면 안 됨
    assert not settings.valid_model_id("script", "Anthropic/Claude")           # 소문자만
    assert not settings.valid_model_id("tts", "anthropic/claude-opus-5.5")
    assert not settings.valid_model_id("script", None)


def test_key_list_has_openrouter_and_optional_gemini():
    by = {k["name"]: k for k in settings.KEYS}
    assert by["openrouter_api_key"] == {"name": "openrouter_api_key", "env": "RECAP_OPENROUTER_API_KEY",
                                        "label": "OpenRouter API 키", "group": "openrouter", "required": False}
    assert by["gemini_api_key"]["required"] is False
    groups = {g["id"]: g for g in settings.GROUPS}
    assert [g["id"] for g in settings.GROUPS] == ["gemini", "openrouter", "cloudflare"]
    assert groups["openrouter"]["url"] == "https://openrouter.ai/settings/keys"
    assert groups["gemini"]["need"] == groups["openrouter"]["need"] == "둘 중 하나 필수"
    assert groups["openrouter"]["desc"] == ("대본(Claude 등)·그림 읽기. Gemini 키가 없으면 Gemini 모델도 이 키로 부릅니다. "
                                            "목소리는 로컬 엔진을 쓰세요")


def test_env_openrouter_key_is_read_and_counts_as_key(monkeypatch, models_ok):
    monkeypatch.setenv("RECAP_OPENROUTER_API_KEY", "  env-or-key-00000000  ")
    assert settings.get_key("openrouter_api_key") == "env-or-key-00000000"
    assert settings.readiness()["ok"] is True and settings.readiness()["via"] == "openrouter"


def test_catalog_keeps_claude_with_or_without_gemini_key(monkeypatch):
    monkeypatch.setattr(GM, "_cache", {"at": 0.0, "key_tail": "", "items": None})
    monkeypatch.setattr(GM, "_fetch", lambda key, timeout=8.0: [("gemini-4-argon", "Gemini 4 Argon"),
                                                                 ("gemini-3.8-flash", "Gemini 3.8 Flash")])
    for key in ("", GEM_KEY):
        script = GM.catalog(key)["roles"]["script"]
        ids = [i["id"] for i in script]
        assert ids[:2] == ["gemini-4-argon", "gemini-3.8-flash"] and "anthropic/claude-opus-5.5" in ids
        assert "anthropic/claude-sonnet-5.5" in ids
    label = next(i["label"] for i in GM.catalog("")["roles"]["script"] if i["id"] == "anthropic/claude-opus-5.5")
    assert label.startswith("Claude Opus 5.5 · OpenRouter")


# ---------------------------------------------------------------- 9. 사용처: 청취·비용 합산
def test_fx_listen_is_on_with_openrouter_only(monkeypatch):
    from recap_shorts.fx import library
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert library._gemini_ready() is False
    use_keys(openrouter=True)
    assert library._gemini_ready() is True
    monkeypatch.setenv("RECAP_FX_NO_LLM", "1")
    assert library._gemini_ready() is False


def test_cost_estimate_adds_openrouter_rows():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import cost_estimate as CE
    finally:
        sys.path.pop(0)
    prices = json.loads((ROOT / "config" / "prices.json").read_text(encoding="utf-8"))
    rows = [{"kind": "openrouter", "model": "anthropic/claude-opus-5.5", "cost": 0.25},
            {"kind": "openrouter", "model": "google/gemini-3.8-flash", "cost": 0.001},
            {"kind": "openrouter", "model": "google/gemini-3.8-flash"}]
    items = CE.per_video(rows, prices)["items"]
    assert items["OpenRouter (anthropic/claude-opus-5.5)"] == {"calls": 1, "usd": 0.25}
    assert items["OpenRouter (google/gemini-3.8-flash)"] == {"calls": 2, "usd": 0.001}


# ---------------------------------------------------------------- 10. 서버 문구·게이트·설정 시험
def fake_requests(monkeypatch, resp=None, boom=None, seen=None):
    def get(url, params=None, headers=None, timeout=None):
        if seen is not None:
            seen.update(url=url, params=params, headers=headers, timeout=timeout)
        if boom:
            raise boom
        return resp or Resp(200)
    monkeypatch.setattr(requests, "get", get)


@pytest.fixture
def fake_client(tmp_path, monkeypatch):
    from recap_shorts.app import create_app
    from tests.pipeline.fakes import FakeDeps
    monkeypatch.setattr(provider, "local_available", lambda *a, **k: False)
    monkeypatch.setattr(provider, "local_server_up", lambda: False)
    app = create_app(work_root=tmp_path, deps=FakeDeps(), start_runner=False)
    with TestClient(app, base_url="http://127.0.0.1:8020") as c:
        yield c


@pytest.fixture
def real_client(tmp_path, monkeypatch):
    from recap_shorts import pipeline as P
    from recap_shorts.app import create_app
    monkeypatch.setattr(provider, "local_available", lambda *a, **k: False)
    monkeypatch.setattr(provider, "local_server_up", lambda: False)
    app = create_app(work_root=tmp_path, deps=P.Deps(), start_runner=False)
    with TestClient(app, base_url="http://127.0.0.1:8020") as c:
        yield c, app


SRC = {"site": "dcinside", "gallery": "baseball_new13", "no": 2101, "minor": False,
       "url": "https://gall.dcinside.com/board/view/?id=baseball_new13&no=2101"}


def test_health_reason_when_no_keys(fake_client, models_ok):
    h = fake_client.get("/api/health").json()
    assert h["gemini_key"] == {"ok": False, "detail": "Gemini 또는 OpenRouter API 키가 필요합니다"}


def test_health_detail_by_provider(fake_client, models_ok):
    use_keys(openrouter=True)
    assert fake_client.get("/api/health").json()["gemini_key"] == {"ok": True,
                                                                    "detail": "OpenRouter 키로 부름(Gemini 키 없음)"}
    use_keys(gemini=True)
    assert fake_client.get("/api/health").json()["gemini_key"] == {"ok": True, "detail": "Gemini 키로 부름"}
    settings.save(models={"script": "anthropic/claude-opus-5.5"})
    assert fake_client.get("/api/health").json()["gemini_key"] == {"ok": True, "detail": "Gemini·OpenRouter 키 있음"}


def test_settings_test_openrouter_connected_shows_limit(fake_client, monkeypatch):
    fake_client.put("/api/settings", json={"keys": {"openrouter_api_key": OR_KEY}})
    seen = {}
    fake_requests(monkeypatch, Resp(200, {"data": {"limit_remaining": 12.5}}), seen=seen)
    out = fake_client.post("/api/settings/test", json={"service": "openrouter"}).json()
    assert out == {"service": "openrouter", "ok": True, "message": "연결됨 (남은 한도 $12.50)"}
    assert seen["url"] == "https://openrouter.ai/api/v1/key" and seen["headers"] == {"Authorization": f"Bearer {OR_KEY}"}
    assert OR_KEY not in json.dumps(out)


def test_settings_test_openrouter_status_messages(fake_client, monkeypatch):
    fake_client.put("/api/settings", json={"keys": {"openrouter_api_key": OR_KEY}})
    fake_requests(monkeypatch, Resp(200, {"data": {}}))
    assert fake_client.post("/api/settings/test", json={"service": "openrouter"}).json()["message"] == "연결됨"
    fake_requests(monkeypatch, Resp(402))
    out = fake_client.post("/api/settings/test", json={"service": "openrouter"}).json()
    assert out["ok"] is False and "잔액" in out["message"] and "HTTP 402" in out["message"]
    fake_requests(monkeypatch, Resp(429))
    assert fake_client.post("/api/settings/test", json={"service": "openrouter"}).json()["message"] \
        == "키는 맞지만 사용 한도에 걸렸습니다"
    fake_requests(monkeypatch, Resp(500))
    assert fake_client.post("/api/settings/test", json={"service": "openrouter"}).json()["message"] \
        == "확인 실패 (HTTP 500)"
    fake_requests(monkeypatch, boom=requests.ConnectionError(f"down {OR_KEY}"))
    out = fake_client.post("/api/settings/test", json={"service": "openrouter"}).json()
    assert out["message"] == "연결 실패 — 인터넷 연결을 확인하세요" and OR_KEY not in json.dumps(out)


def test_settings_test_openrouter_bad_key_turns_health_red(fake_client, monkeypatch, models_ok):
    fake_client.put("/api/settings", json={"keys": {"openrouter_api_key": OR_KEY}})
    fake_requests(monkeypatch, Resp(401))
    out = fake_client.post("/api/settings/test", json={"service": "openrouter"}).json()
    assert out["ok"] is False and out["message"] == "키가 맞지 않습니다 (HTTP 401)"
    h = fake_client.get("/api/health").json()
    assert h["gemini_key"] == {"ok": False, "detail": "OpenRouter API 키가 맞지 않습니다 — 설정에서 키를 확인하세요",
                               "bad": True}
    assert OR_KEY not in json.dumps(h)
    fake_requests(monkeypatch, Resp(200, {"data": {}}))
    fake_client.post("/api/settings/test", json={"service": "openrouter"})
    assert fake_client.get("/api/health").json()["gemini_key"]["ok"] is True


def test_settings_test_openrouter_without_key(fake_client):
    assert fake_client.post("/api/settings/test", json={"service": "openrouter"}).json() \
        == {"service": "openrouter", "ok": False, "message": "키가 없습니다"}


def test_create_job_needs_some_ready_pipeline(real_client, models_ok):
    client, _ = real_client
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "no_key", "message": "Gemini 또는 OpenRouter API 키가 필요합니다"}


def test_create_job_blocks_vendor_script_without_openrouter(real_client, models_ok):
    client, _ = real_client
    use_keys(gemini=True)
    settings.save(models={"script": "anthropic/claude-opus-5.5"})
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101})
    assert r.status_code == 409
    assert r.json()["detail"]["message"] == "대본 모델(anthropic/claude-opus-5.5)은 OpenRouter 키가 필요합니다"


def test_create_job_allowed_with_openrouter_only(real_client, models_ok):
    client, _ = real_client
    use_keys(openrouter=True)
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101})
    assert r.status_code == 200 and r.json()["id"]


def test_voice_redo_with_gemini_voice_needs_gemini_key(real_client, models_ok):
    client, app = real_client
    use_keys(openrouter=True)
    job = app.state.store.create(dict(SRC), {"auto_steps": []})
    job.set_status("waiting")                                          # 새 작업은 running 으로 시작한다(작업 중이면 409)
    (job.dir / "voice").mkdir(parents=True, exist_ok=True)
    (job.dir / "voice" / "timings.json").write_text(
        json.dumps({"engine": "gemini", "sentences": [{"text": "안녕", "spoken": "안녕"}]}, ensure_ascii=False),
        encoding="utf-8")
    r = client.post(f"/api/jobs/{job.id}/voice/redo", json={"index": 0})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_key"
    assert r.json()["detail"]["message"].startswith("Gemini API 키가 없어 목소리 다시 녹음")


def test_regen_stays_blocked_without_gemini_key(real_client, models_ok):
    client, app = real_client
    use_keys(openrouter=True)
    settings.save(image_engine="gemini")
    job = app.state.store.create(dict(SRC), {"auto_steps": []})
    job.set_status("waiting")
    (job.dir / "assets").mkdir(parents=True, exist_ok=True)
    (job.dir / "assets" / "assets.json").write_text(json.dumps([{"file": "ai_01.png", "kind": "ai"}]), encoding="utf-8")
    r = client.post(f"/api/jobs/{job.id}/assets/regenerate", json={"file": "ai_01.png"})
    # 그림 엔진(Gemini)은 Gemini 키 없이는 못 만든다 — 엔진 가용성(503)보다 키 안내(409)가 먼저 보여야 한다
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_key"
    assert r.json()["detail"]["message"].startswith("Gemini API 키가 없어 AI 그림 다시 만들기")


# ---------------------------------------------------------------- 11. 키 값 가리기: 300자로 자르기 전에 가린다
LONG_KEY = "sk-or-v1-" + "Qx7" * 18        # 63자: 300자 자르기 경계에 걸리도록 긴 오류 문구와 함께 쓴다


def no_key_piece(key: str, text: str, n: int = 20) -> bool:
    return not any(key[i:i + n] in text for i in range(len(key) - n + 1))


def test_key_straddling_the_300_cut_is_masked_whole(monkeypatch, logs):
    settings.save({"openrouter_api_key": LONG_KEY})
    msg = "e" * 280 + LONG_KEY + " 뒤의 긴 설명 " * 3
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(401, {"error": {"message": msg}}))
    with pytest.raises(GeminiApiError) as e:
        G._post("anthropic/claude-opus-5.5", {"contents": []}, sleep=lambda s: None)
    assert no_key_piece(LONG_KEY, str(e.value)) and no_key_piece(LONG_KEY, errors.screen_text(e.value))
    assert "<key>" in str(e.value)


def test_key_in_error_body_is_masked(monkeypatch, logs):
    settings.save({"openrouter_api_key": LONG_KEY})
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(401, {"error": {"message": f"no key {LONG_KEY} here"}}))
    with pytest.raises(GeminiApiError) as e:
        G._post("anthropic/claude-opus-5.5", {"contents": []}, sleep=lambda s: None)
    assert LONG_KEY not in str(e.value) and "no key <key> here" in str(e.value)


def test_empty_key_inserts_no_placeholder(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(400, {"error": {"message": "bad"}}))
    with pytest.raises(GeminiApiError) as e:
        G._post_openrouter("anthropic/claude-opus-5.5", {"contents": []}, key="", sleep=lambda s: None)
    assert str(e.value) == "OpenRouter 실패(anthropic/claude-opus-5.5): HTTP 400 — bad"


def test_openrouter_key_is_read_once_per_call(monkeypatch, logs):
    reads = []

    def key_once():
        reads.append(1)
        return OR_KEY if len(reads) == 1 else ""             # 두 번째 조회는 사용자가 키를 지운 것처럼 빈 값
    monkeypatch.setattr(G, "_openrouter_key", key_once)
    auth = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: auth.append(kw["headers"]["Authorization"]) or or_ok("ok"))
    assert G.generate_text("p", "anthropic/claude-opus-5.5", json=False) == "ok"
    assert len(reads) == 1 and auth == [f"Bearer {OR_KEY}"]


# ---------------------------------------------------------------- 12. 거절·빈 답은 OpenRouter 문구로
@pytest.mark.parametrize("kw,block,screen", [
    ({"finish": "content_filter"}, True, "OpenRouter 모델이 이 내용의 답을 거절했습니다 — 자료나 문장을 바꿔 보세요"),
    ({"refusal": "I can't help with that."}, True, "OpenRouter 모델이 이 내용의 답을 거절했습니다 — 자료나 문장을 바꿔 보세요"),
    ({"finish": "length"}, False, "OpenRouter 응답이 비어 있음(finishReason=length)"),
])
def test_openrouter_block_or_empty_answer_names_openrouter(monkeypatch, logs, kw, block, screen):
    from recap_shorts.images import sensitive
    use_keys(openrouter=True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: or_ok("", **kw))
    with pytest.raises(GeminiApiError) as e:
        G.generate_text("p", "google/gemini-3.8-flash", json=False)
    assert e.value.provider == "openrouter"
    assert errors.screen_text(e.value) == screen
    assert sensitive._is_block(e.value) is block


# ---------------------------------------------------------------- 13. 준비 상태: 작업 옵션·남은 단계
def test_readiness_uses_job_script_option(models_ok):
    use_keys(gemini=True)
    assert settings.readiness()["ok"] is True
    assert settings.readiness(script_model="anthropic/claude-opus-5.5") == {
        "ok": False, "reason": "대본 모델(anthropic/claude-opus-5.5)은 OpenRouter 키가 필요합니다", "via": "gemini"}


def test_readiness_uses_job_tts_option(models_ok):
    use_keys(openrouter=True)
    assert settings.readiness()["ok"] is True
    assert settings.readiness(tts_engine="gemini") == {
        "ok": False, "reason": "목소리가 Gemini 인데 Gemini 키가 없습니다 — 설정에서 로컬 목소리를 고르세요", "via": "openrouter"}


def test_readiness_need_checks_only_the_steps_left(models_ok):
    use_keys(openrouter=True)
    settings.save(voice={"engine": "gemini"})
    assert settings.readiness()["ok"] is False
    assert settings.readiness(need={"read"})["ok"] is True          # 목소리는 이미 끝난 단계라 보지 않는다
    assert settings.readiness(need={"voice"})["ok"] is False
    assert settings.readiness(need=set()) == {"ok": True, "reason": None, "via": None}


# ---------------------------------------------------------------- 14. 서버: 틀린 키·작업 옵션·남은 단계
def waiting_job(app, done=(), waiting=None):
    job = app.state.store.create(dict(SRC), {"auto_steps": []})
    for s in job.state["steps"]:
        if s["key"] in done:
            s["status"] = "done"
        elif s["key"] == waiting:
            s["status"] = "waiting"
    job.set_status("waiting")
    return job


BAD_OR = "OpenRouter API 키가 맞지 않습니다 — 설정에서 키를 확인하세요"


def test_bad_openrouter_key_blocks_create(real_client, monkeypatch, models_ok):
    client, _ = real_client
    use_keys(openrouter=True)
    fake_requests(monkeypatch, Resp(401))
    client.post("/api/settings/test", json={"service": "openrouter"})
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101})
    assert r.status_code == 409 and r.json()["detail"] == {"code": "no_key", "message": BAD_OR}


def test_bad_openrouter_key_blocks_continue_and_assets_rerun(real_client, monkeypatch, models_ok):
    client, app = real_client
    use_keys(openrouter=True)
    fake_requests(monkeypatch, Resp(401))
    client.post("/api/settings/test", json={"service": "openrouter"})
    job = waiting_job(app, done=("source",), waiting="read")
    r = client.post(f"/api/jobs/{job.id}/continue")
    assert r.status_code == 409 and r.json()["detail"]["message"] == BAD_OR
    job = waiting_job(app, done=("source", "read", "script", "voice"))
    r = client.post(f"/api/jobs/{job.id}/rerun", json={"from": "assets"})
    assert r.status_code == 409 and r.json()["detail"]["message"] == BAD_OR


def test_openrouter_402_and_429_clear_the_bad_mark(fake_client, monkeypatch, models_ok):
    fake_client.put("/api/settings", json={"keys": {"openrouter_api_key": OR_KEY}})
    ok = {"ok": True, "detail": "OpenRouter 키로 부름(Gemini 키 없음)"}
    for code in (401, 402, 401, 429):
        fake_requests(monkeypatch, Resp(code))
        fake_client.post("/api/settings/test", json={"service": "openrouter"})
        bad = fake_client.get("/api/health").json()["gemini_key"].get("bad") is True
        assert bad is (code == 401)                              # 인증이 됐으면(402·429) 틀린 표시를 지운다
    assert fake_client.get("/api/health").json()["gemini_key"] == ok


def test_create_job_checks_script_option(real_client, models_ok):
    client, _ = real_client
    use_keys(gemini=True)
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101,
                                       "options": {"script_model": "anthropic/claude-opus-5.5"}})
    assert r.status_code == 409
    assert r.json()["detail"]["message"] == "대본 모델(anthropic/claude-opus-5.5)은 OpenRouter 키가 필요합니다"


def test_create_job_checks_tts_option(real_client, models_ok):
    client, _ = real_client
    use_keys(openrouter=True)
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101, "options": {"tts_engine": "gemini"}})
    assert r.status_code == 409
    assert r.json()["detail"]["message"] == "목소리가 Gemini 인데 Gemini 키가 없습니다 — 설정에서 로컬 목소리를 고르세요"


def test_create_job_rejects_unknown_option_values(real_client, models_ok):
    client, _ = real_client
    use_keys(openrouter=True)
    for opts in ({"script_model": "Not A Model!"}, {"tts_engine": "bogus"}):
        r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101, "options": opts})
        assert r.status_code == 400, opts


def test_create_job_keeps_stripped_script_option(real_client, models_ok):
    client, app = real_client
    use_keys(openrouter=True)
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101,
                                       "options": {"script_model": " anthropic/claude-opus-5.5 "}})
    assert r.status_code == 200
    assert app.state.store.get(r.json()["id"]).options["script_model"] == "anthropic/claude-opus-5.5"


def test_assets_rerun_does_not_check_the_voice_step(real_client, models_ok):
    client, app = real_client
    use_keys(openrouter=True)
    settings.save(voice={"engine": "gemini"})                  # 목소리는 Gemini 인데 Gemini 키는 없다
    job = waiting_job(app, done=("source", "read", "script", "voice"))
    r = client.post(f"/api/jobs/{job.id}/rerun", json={"from": "assets"})
    assert r.status_code == 200


def test_continue_after_script_ignores_the_script_route(real_client, models_ok):
    client, app = real_client
    use_keys(gemini=True)
    settings.save(models={"script": "anthropic/claude-opus-5.5"})     # 대본은 이미 끝났다 — 지금 OpenRouter 키가 없어도 된다
    job = waiting_job(app, done=("source", "read"), waiting="script")
    r = client.post(f"/api/jobs/{job.id}/continue")
    assert r.status_code == 200
