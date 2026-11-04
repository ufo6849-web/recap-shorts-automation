"""GET/PUT/POST /api/settings 시험: 로컬 전용 지킴이·응답 꼴·키 가리기 — 유료 API 는 전부 가짜로."""
import json

import pytest
from fastapi.testclient import TestClient

from recap_shorts import settings
from recap_shorts.app import create_app
from tests.pipeline.fakes import FakeDeps

GEMINI = "test-gemini-key-0000000000"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from recap_shorts.images import provider
    monkeypatch.setattr(provider, "local_available", lambda *a, **k: False)
    monkeypatch.setattr(provider, "local_server_up", lambda: False)
    app = create_app(work_root=tmp_path, deps=FakeDeps(), start_runner=False)
    # 앱은 127.0.0.1 에서 뜬다. TestClient 기본 host(testserver)는 로컬 전용 지킴이에 막히므로 로컬 주소로.
    with TestClient(app, base_url="http://127.0.0.1:8020") as c:
        yield c


def body(resp) -> dict:
    return resp.json()


def test_get_settings_shape(client, monkeypatch):
    from recap_shorts import modelfiles
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)
    r = client.get("/api/settings")
    assert r.status_code == 200
    d = body(r)
    assert d["ready"] is False
    assert d["image_engine"] == "off"             # 고른 적 없고 Gemini 키도 없으면 AI 그림 끔(키가 있으면 Gemini)
    assert [e["id"] for e in d["image_engines"]] == ["local", "gemini", "off"]
    assert [k["name"] for k in d["keys"]] == ["gemini_api_key", "openrouter_api_key", "cf_account_id", "cf_api_token"]
    assert all(k["set"] is False and k["source"] is None and k["masked"] is None for k in d["keys"])
    assert d["image_server"] == {"url": "http://127.0.0.1:8791", "up": False}
    assert d["setup_cmd"] == ".venv\\Scripts\\python scripts\\setup_models.py"


def test_auto_engine_is_gone(client):
    r = client.put("/api/settings", json={"image_engine": "auto"})
    assert r.status_code == 400


def test_models_api(client, monkeypatch):
    from recap_shorts import modelfiles
    started = []
    monkeypatch.setattr(modelfiles, "start", lambda mid: started.append(mid))
    d = body(client.get("/api/settings/models"))
    assert [m["id"] for m in d["models"]] == ["sensevoice", "supertonic"]
    assert {"env", "job"} <= set(d["models"][0]) and d["setup_cmd"].startswith(".venv")
    assert client.post("/api/settings/models/supertonic/install").status_code == 200 and started == ["supertonic"]
    assert client.post("/api/settings/models/nope/install").status_code == 404

    def refuse(mid):
        raise modelfiles.ModelError("환경변수 RECAP_SUPERTONIC_DIR 로 폴더를 정해 두어 여기서 받을 수 없습니다")
    monkeypatch.setattr(modelfiles, "start", refuse)
    r = client.post("/api/settings/models/supertonic/install")
    assert r.status_code == 409 and "RECAP_SUPERTONIC_DIR" in r.text
    assert client.post("/api/settings/models/supertonic/install", headers={"host": "10.0.0.5:8020"}).status_code == 403


def test_put_then_get(client):
    r = client.put("/api/settings", json={"keys": {"gemini_api_key": GEMINI}, "image_engine": "gemini"})
    assert r.status_code == 200
    d = body(r)
    assert d["ready"] is True and d["image_engine"] == "gemini"
    k = next(k for k in d["keys"] if k["name"] == "gemini_api_key")
    assert k["set"] and k["source"] == "file" and k["masked"] == "••••0000"
    assert GEMINI not in r.text
    assert body(client.get("/api/settings"))["image_engine"] == "gemini"


def test_put_delete_and_partial_update(client):
    client.put("/api/settings", json={"keys": {"gemini_api_key": GEMINI, "cf_account_id": "acct-1"}})
    d = body(client.put("/api/settings", json={"keys": {"cf_account_id": None}}))
    by = {k["name"]: k for k in d["keys"]}
    assert by["gemini_api_key"]["set"] is True and by["cf_account_id"]["set"] is False
    d = body(client.put("/api/settings", json={"keys": {"gemini_api_key": ""}}))
    assert d["ready"] is False


@pytest.mark.parametrize("payload,code,msg", [
    ({"keys": {"nope": "x"}}, 400, "모르는 키 이름입니다: nope"),
    ({"image_engine": "photoshop"}, 400, "모르는 image_engine 입니다"),
    ({"keys": {"gemini_api_key": "has space"}}, 400, "키에 쓸 수 없는 글자가 있습니다"),
    ({"keys": {"gemini_api_key": "키"}}, 400, "키에 쓸 수 없는 글자가 있습니다"),
])
def test_put_validation(client, payload, code, msg):
    r = client.put("/api/settings", json=payload)
    assert r.status_code == code
    assert msg in r.text


def test_put_env_owned_is_409_and_does_not_leak(client, monkeypatch):
    monkeypatch.setenv("RECAP_CF_API_TOKEN", "env-token-000000")
    r = client.put("/api/settings", json={"keys": {"cf_api_token": "other-token-000000"}})
    assert r.status_code == 409
    assert "환경변수 RECAP_CF_API_TOKEN 로 정해져 있어 여기서 바꿀 수 없습니다" in r.text
    assert "env-token" not in r.text and "other-token" not in r.text


def test_guard_blocks_foreign_host_and_origin(client):
    r = client.get("/api/settings", headers={"host": "evil.example.com"})
    assert r.status_code == 403
    r = client.get("/api/settings", headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert client.get("/api/settings", headers={"origin": "http://localhost:5173"}).status_code == 200
    assert client.put("/api/settings", json={}, headers={"host": "10.0.0.5:8020"}).status_code == 403
    assert client.post("/api/settings/test", json={"service": "gemini"},
                       headers={"origin": "http://8.8.8.8"}).status_code == 403


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data or {}

    def json(self):
        return self._data


def fake_requests(monkeypatch, resp=None, boom=None, seen=None):
    import requests

    def get(url, params=None, headers=None, timeout=None):
        if seen is not None:
            seen.update(url=url, params=params, headers=headers, timeout=timeout)
        if boom:
            raise boom
        return resp or Resp(200)
    monkeypatch.setattr(requests, "get", get)


def test_test_gemini_status_messages(client, monkeypatch):
    r = client.post("/api/settings/test", json={"service": "gemini"})
    assert body(r) == {"service": "gemini", "ok": False, "message": "키가 없습니다"}

    client.put("/api/settings", json={"keys": {"gemini_api_key": GEMINI}})
    seen = {}
    fake_requests(monkeypatch, Resp(200, {"models": []}), seen=seen)
    out = body(client.post("/api/settings/test", json={"service": "gemini"}))
    assert out == {"service": "gemini", "ok": True, "message": "연결됨"}
    assert seen["headers"] == {"x-goog-api-key": GEMINI} and seen["timeout"] == 10
    assert "generativelanguage.googleapis.com" in seen["url"]

    for code, msg in [(400, "키가 맞지 않습니다 (HTTP 400)"), (401, "키가 맞지 않습니다 (HTTP 401)"),
                      (403, "키가 맞지 않습니다 (HTTP 403)"), (429, "키는 맞지만 사용 한도에 걸렸습니다"),
                      (500, "확인 실패 (HTTP 500)")]:
        fake_requests(monkeypatch, Resp(code))
        out = body(client.post("/api/settings/test", json={"service": "gemini"}))
        assert out["ok"] is False and out["message"] == msg

    import requests as rq_lib
    fake_requests(monkeypatch, boom=rq_lib.ConnectionError())
    out = body(client.post("/api/settings/test", json={"service": "gemini"}))
    assert out["message"] == "연결 실패 — 인터넷 연결을 확인하세요" and GEMINI not in json.dumps(out)


def test_test_cloudflare(client, monkeypatch):
    out = body(client.post("/api/settings/test", json={"service": "cloudflare"}))
    assert out["message"] == "키가 없습니다"
    client.put("/api/settings", json={"keys": {"cf_account_id": "acct-1234567890"}})
    out = body(client.post("/api/settings/test", json={"service": "cloudflare"}))
    assert out["ok"] is False and out["message"] == "계정 ID 와 토큰이 둘 다 필요합니다"
    client.put("/api/settings", json={"keys": {"cf_api_token": "tok-1234567890"}})

    seen = {}
    fake_requests(monkeypatch, Resp(200, {"success": True}), seen=seen)
    out = body(client.post("/api/settings/test", json={"service": "cloudflare"}))
    assert out == {"service": "cloudflare", "ok": True, "message": "연결됨 (Workers AI 사용 가능)"}
    assert "acct-1234567890/ai/models/search" in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer tok-1234567890" and seen["timeout"] == 10

    for code, msg in [(401, "토큰이 맞지 않거나 Workers AI 권한이 없습니다"),
                      (403, "토큰이 맞지 않거나 Workers AI 권한이 없습니다"), (404, "계정 ID 가 맞지 않습니다")]:
        fake_requests(monkeypatch, Resp(code))
        out = body(client.post("/api/settings/test", json={"service": "cloudflare"}))
        assert out["message"] == msg
    fake_requests(monkeypatch, Resp(200, {"success": False}))
    assert body(client.post("/api/settings/test", json={"service": "cloudflare"}))["message"] == "확인 실패 (HTTP 200)"
    fake_requests(monkeypatch, Resp(500))
    assert body(client.post("/api/settings/test", json={"service": "cloudflare"}))["message"] == "확인 실패 (HTTP 500)"


def test_test_unknown_service(client):
    r = client.post("/api/settings/test", json={"service": "openai"})
    assert r.status_code == 400 and "모르는 service" in r.text
    r = client.post("/api/settings/test", json={"service": "naver"})   # 뉴스 검색 기능을 뺐으므로 모르는 서비스
    assert r.status_code == 400 and "모르는 service" in r.text


def test_health_reports_settings_files(client, monkeypatch):
    h = body(client.get("/api/health"))
    assert h["gemini_key"] == {"ok": False, "detail": "Gemini 또는 OpenRouter API 키가 필요합니다"}
    assert set(h["image_server"]) == {"ok", "detail"}
    assert h["image_server"] == {"ok": False, "detail": "AI 그림 꺼짐 — 캡처 카드만"}   # 키가 없으면 AI 그림 끔
    assert h["asr_model"]["ok"] is True or "받아쓰기 모델" in h["asr_model"]["detail"]
    client.put("/api/settings", json={"keys": {"gemini_api_key": GEMINI}})
    h = body(client.get("/api/health"))
    assert h["gemini_key"] == {"ok": True, "detail": "Gemini 키로 부름"}
    assert GEMINI not in json.dumps(h)


def test_health_image_engine_local_and_off(client, monkeypatch):
    import recap_shorts.images.provider as provider
    monkeypatch.setattr(provider, "local_server_up", lambda: True)
    client.put("/api/settings", json={"image_engine": "local"})
    h = body(client.get("/api/health"))
    assert h["image_server"] == {"ok": True, "detail": "로컬 이미지 서버로 만듦"}
    client.put("/api/settings", json={"image_engine": "off"})
    h = body(client.get("/api/health"))
    assert h["image_server"] == {"ok": False, "detail": "AI 그림 꺼짐 — 캡처 카드만"}


def test_settings_file_is_the_same_one_the_app_reads(client, tmp_path):
    client.put("/api/settings", json={"keys": {"gemini_api_key": GEMINI}})
    assert settings.get_key("gemini_api_key") == GEMINI
    assert settings.path().name == "settings.local.json"


def test_health_turns_red_after_the_key_fails_the_check(client, monkeypatch):
    """키가 '있기만' 하면 초록이던 것 — 연결 확인에서 틀렸다고 나온 키는 첫 화면 만들기가 꺼지게 ok=false(bad)."""
    client.put("/api/settings", json={"keys": {"gemini_api_key": GEMINI}})
    fake_requests(monkeypatch, Resp(400))
    client.post("/api/settings/test", json={"service": "gemini"})
    h = body(client.get("/api/health"))
    assert h["gemini_key"]["ok"] is False and h["gemini_key"]["bad"] is True and GEMINI not in json.dumps(h)
    assert h["image_server"]["ok"] is False          # 기본 AI 그림(Gemini 이미지)도 같은 키라 꺼짐으로
    fake_requests(monkeypatch, Resp(200))
    client.post("/api/settings/test", json={"service": "gemini"})
    assert body(client.get("/api/health"))["gemini_key"] == {"ok": True, "detail": "Gemini 키로 부름"}
    fake_requests(monkeypatch, Resp(401))
    client.post("/api/settings/test", json={"service": "gemini"})
    client.put("/api/settings", json={"keys": {"gemini_api_key": GEMINI + "1"}})     # 다른 키로 바꾸면 확인 전까지는 '키 있음'
    assert body(client.get("/api/health"))["gemini_key"]["ok"] is True
    fake_requests(monkeypatch, Resp(429))                                             # 한도는 키가 맞다는 뜻
    client.post("/api/settings/test", json={"service": "gemini"})
    assert body(client.get("/api/health"))["gemini_key"]["ok"] is True
