"""설정 화면 '쓸 모델' 시험: 대본·목소리·이미지 모델과 목소리 엔진(Gemini | Supertonic)을 고르고 저장하고, 실제로 그 모델을 쓰는지.
네트워크·유료 API 는 부르지 않는다(모델 목록 조회도 가짜)."""
import json
import types

import pytest

from recap_shorts import modelfiles, settings
from recap_shorts.llm import gemini_models as G


def test_defaults_match_module_defaults():
    from recap_shorts.images import gemini_image
    from recap_shorts.llm import gemini_api
    from recap_shorts.voice import gemini as gtts
    assert settings.model("script") == gemini_api.TEXT_MODEL
    assert settings.model("tts") == gtts.DEFAULT_MODEL
    assert settings.model("image") == gemini_image.DEFAULT_MODEL
    for role, rec in G.BUILTIN.items():               # 추천 목록 맨 앞이 기본값
        assert rec[0][0] == settings.model(role)
    assert settings.voice_engine() == "supertonic" and settings.supertonic_sid() == 0    # 키가 없으면 로컬 목소리


def test_save_models_and_voice_keeps_other_fields(monkeypatch):
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"}, "local")
    settings.save(models={"script": "gemini-3.8-flash", "image": "gemini-3-pro-image"})
    settings.save(voice={"engine": "supertonic", "sid": 7})
    data = json.loads(settings.path().read_text(encoding="utf-8"))
    assert data == {"keys": {"gemini_api_key": "test-gemini-key-0000000000"}, "image_engine": "local",
                    "models": {"script": "gemini-3.8-flash", "image": "gemini-3-pro-image"},
                    "voice": {"engine": "supertonic", "sid": 7}}
    assert settings.model("script") == "gemini-3.8-flash" and settings.model_source("script") == "file"
    assert settings.model("tts") == "gemini-3.8-flash-tts" and settings.model_source("tts") is None
    assert settings.voice_engine() == "supertonic" and settings.supertonic_sid() == 7
    settings.save(models={"script": ""})                # 빈 값 = 기본으로
    assert settings.model("script") == "gemini-4-argon" and settings.model_source("script") is None
    v = settings.public_view()
    assert [r["role"] for r in v["model_roles"]] == ["script", "tts", "image"]
    assert v["voice"]["engine"] == "supertonic" and v["voice"]["sid"] == 7


@pytest.mark.parametrize("kw,status,msg", [
    ({"models": {"voice": "x-model"}}, 400, "모르는 모델 자리"),
    ({"models": {"script": "Gemini 4!"}}, 400, "쓸 수 없는 글자"),
    ({"voice": {"engine": "qwen"}}, 400, "모르는 목소리 엔진"),
    ({"voice": {"sid": 10}}, 400, "0~9"),
    ({"voice": {"sid": True}}, 400, "0~9"),
])
def test_save_rejects_bad_values(kw, status, msg):
    with pytest.raises(settings.SettingsError, match=msg) as e:
        settings.save(**kw)
    assert e.value.status == status


def test_supertonic_needs_model_file(monkeypatch):
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"})      # 기본 목소리가 Gemini 인 상태에서 시험
    monkeypatch.setattr(modelfiles, "ok", lambda mid: False)
    with pytest.raises(settings.SettingsError, match="로컬 목소리 모델이 없습니다"):
        settings.save(voice={"engine": "supertonic"})
    assert settings.voice_engine() == "gemini"


def test_env_wins_and_locks(monkeypatch):
    monkeypatch.setenv("RECAP_TTS_MODEL", "gemini-3.8-flash-lite-tts")
    monkeypatch.setenv("RECAP_TTS_SID", "3")
    assert settings.model("tts") == "gemini-3.8-flash-lite-tts" and settings.model_source("tts") == "env"
    assert settings.supertonic_sid() == 3
    with pytest.raises(settings.SettingsError) as e:
        settings.save(models={"tts": "gemini-3.8-flash-tts"})
    assert e.value.status == 409
    with pytest.raises(settings.SettingsError) as e:
        settings.save(voice={"sid": 5})
    assert e.value.status == 409


def test_modules_use_chosen_models(monkeypatch):
    from recap_shorts.llm import make_script_llm
    from recap_shorts.voice import gemini as gtts
    from recap_shorts.voice import tts
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)
    settings.save(models={"script": "gemini-3.8-flash", "tts": "gemini-3.8-flash-lite-tts"}, voice={"sid": 7})
    assert make_script_llm().model == "gemini-3.8-flash"
    assert make_script_llm(model="gemini-4-argon").model == "gemini-4-argon"      # 인자가 먼저
    assert gtts.chosen_model() == "gemini-3.8-flash-lite-tts"
    assert tts.default_sid() == 7


def test_gemini_image_uses_chosen_model(monkeypatch, tmp_path):
    import requests

    from recap_shorts.images import gemini_image
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"}, models={"image": "gemini-3-pro-image"})
    seen = {}

    def post(url, **kw):
        seen["url"] = url
        raise requests.ConnectionError("시험: 보내지 않음")
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("time.sleep", lambda s: None)
    r = gemini_image.generate("scene", tmp_path / "a.png")
    assert r["model"] == "gemini-3-pro-image" and "/gemini-3-pro-image:generateContent" in seen["url"]


# ---------------------------------------------------------------- 고를 거리(모델 목록)

def test_role_of():
    assert G.role_of("gemini-3.8-flash-tts") == "tts"
    assert G.role_of("gemini-3.1-flash-image") == "image" and G.role_of("gemini-nano-banana-2.1") == "image"
    assert G.role_of("gemini-4-argon") == "script"
    assert G.role_of("gemini-2.5-computer-use-preview-10-2025") is None
    assert G.role_of("gemini-3.5-transcribe") is None and G.role_of("imagen-4") is None


def test_catalog_without_key_is_builtin():
    c = G.catalog("")
    assert c["source"] == "builtin"
    assert [i["id"] for i in c["roles"]["tts"]] == [n for n, _ in G.BUILTIN["tts"]]
    assert all(i["recommended"] for r in c["roles"].values() for i in r)


def test_catalog_from_api_orders_recommended_first(monkeypatch):
    monkeypatch.setattr(G, "_cache", {"at": 0.0, "key_tail": "", "items": None})
    monkeypatch.setattr(G, "_fetch", lambda key, timeout=8.0: [
        ("gemini-4-argon", "Gemini 4 Argon"), ("gemini-3.8-flash", "Gemini 3.8 Flash"), ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
        ("gemini-3.8-flash-tts", "Gemini 3.8 Flash TTS"), ("gemini-2.5-flash-preview-tts", "Gemini 2.5 Flash Preview TTS"),
        ("gemini-3.1-flash-image", "Nano Banana 2"), ("gemini-3.5-transcribe", "Transcribe")])
    c = G.catalog("test-gemini-key-0000000000")
    assert c["source"] == "api"
    script = c["roles"]["script"]
    assert [i["id"] for i in script] == ["gemini-4-argon", "gemini-3.8-flash", "anthropic/claude-opus-5.5",
                                         "anthropic/claude-sonnet-5.5"]                # 대본은 써 본 모델만(Claude 는 OpenRouter 키로)
    assert all(i["recommended"] for i in script) and "$2" in script[0]["label"]
    tts = c["roles"]["tts"]
    assert [i["id"] for i in tts] == ["gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"]  # 키로 못 쓰는 추천은 뺌
    assert tts[0]["recommended"] and not tts[1]["recommended"]
    assert "$0.5" in tts[0]["label"] and "$" not in tts[1]["label"]               # 가격은 아는 모델만
    assert [i["id"] for i in c["roles"]["image"]] == ["gemini-3.1-flash-image"]


def test_catalog_falls_back_when_list_fails(monkeypatch):
    import requests
    monkeypatch.setattr(G, "_cache", {"at": 0.0, "key_tail": "", "items": None})

    def boom(key, timeout=8.0):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(G, "_fetch", boom)
    c = G.catalog("test-gemini-key-0000000000")
    assert c["source"] == "builtin" and "받지 못해" in c["note"]


# ---------------------------------------------------------------- 제작 단계(목소리)

def _voice_ctx(tmp_path, opt):
    from recap_shorts import pipeline
    used = {}

    class Deps:
        def make_voice(self, script, out_dir, engine, check_asr):
            used["engine"] = engine
            return {"duration": 1.0}
    c = types.SimpleNamespace(dir=tmp_path, deps=Deps(), opt={**pipeline.DEFAULT_OPTIONS, **opt},
                              job=types.SimpleNamespace(artifact=lambda *a: None),
                              read_json=lambda rel: {"sentences": []}, progress=lambda *a: None, sub=lambda *a: None)
    return c, used


def test_voice_step_uses_settings_engine(tmp_path, monkeypatch):
    from recap_shorts import pipeline
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"})      # 기본 목소리가 Gemini 인 상태에서 시험
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)
    c, used = _voice_ctx(tmp_path, {})
    pipeline.step_voice(c)
    assert used["engine"] == "gemini"
    settings.save(voice={"engine": "supertonic"})
    c, used = _voice_ctx(tmp_path, {})
    pipeline.step_voice(c)
    assert used["engine"] == "supertonic"
    c, used = _voice_ctx(tmp_path, {"tts_engine": "gemini"})       # 작업 옵션이 있으면 그게 먼저
    pipeline.step_voice(c)
    assert used["engine"] == "gemini"


def test_voice_step_explains_missing_supertonic(tmp_path, monkeypatch):
    from recap_shorts import pipeline
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)
    settings.save(voice={"engine": "supertonic"})
    monkeypatch.setattr(modelfiles, "ok", lambda mid: False)       # 고른 뒤 모델 폴더를 지운 경우
    c, _ = _voice_ctx(tmp_path, {})
    with pytest.raises(RuntimeError, match="설정 → 모델 파일"):
        pipeline.step_voice(c)


# ---------------------------------------------------------------- API

def test_settings_api_models_and_voice(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from recap_shorts.app import create_app
    from recap_shorts.images import provider
    from tests.pipeline.fakes import FakeDeps
    monkeypatch.setattr(provider, "local_server_up", lambda: False)
    monkeypatch.setattr(modelfiles, "ok", lambda mid: mid == "sensevoice")
    app = create_app(work_root=tmp_path, deps=FakeDeps(), start_runner=False)
    with TestClient(app, base_url="http://127.0.0.1:8020") as cl:
        d = cl.put("/api/settings", json={"models": {"tts": "gemini-3.8-flash-lite-tts"}}).json()
        assert next(r for r in d["model_roles"] if r["role"] == "tts")["value"] == "gemini-3.8-flash-lite-tts"
        r = cl.put("/api/settings", json={"voice": {"engine": "supertonic"}})
        assert r.status_code == 400 and "모델 파일" in r.text
        g = cl.get("/api/settings/gemini-models").json()                  # 키 없음 → 써 본 모델만(네트워크 없음)
        assert g["source"] == "builtin" and set(g["roles"]) == {"script", "tts", "image"}
        assert cl.get("/api/settings/gemini-models", headers={"host": "10.0.0.5:8020"}).status_code == 403


# ---------------------------------------------------------------- 목소리 이름·미리듣기

def test_gemini_voice_choice():
    from recap_shorts.voice import gemini as gtts
    assert settings.gemini_voice() == gtts.DEFAULT_VOICE == "Charon"
    assert len(settings.GEMINI_VOICES) == 30
    settings.save(voice={"gemini_voice": "Erinome"})
    assert settings.gemini_voice() == "Erinome" and gtts.chosen_voice() == "Erinome"
    assert settings.public_view()["voice"]["gemini_voice"] == "Erinome"
    with pytest.raises(settings.SettingsError, match="모르는 Gemini 목소리"):
        settings.save(voice={"gemini_voice": "Argon"})


def _fake_tts(monkeypatch, calls):
    from recap_shorts.voice import gemini as gtts

    def call(text, out_wav, voice=None, style=None, model=None, max_attempts=3):
        calls.append((text, voice, model))
        out_wav.write_bytes(b"RIFF" + b"\0" * 100)
        return {"seconds": 0.0, "attempt": 1}
    monkeypatch.setattr(gtts, "call", call)


def test_preview_gemini_caches_same_values(tmp_path, monkeypatch):
    from recap_shorts.voice import preview
    calls = []
    _fake_tts(monkeypatch, calls)
    with pytest.raises(preview.PreviewError) as e:                  # 키가 없으면 부르지 않음
        preview.make(tmp_path, "gemini")
    assert e.value.status == 409 and not calls
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"}, voice={"gemini_voice": "Kore"})
    wav, cached = preview.make(tmp_path, "gemini")
    assert not cached and wav.parent == tmp_path / "_preview" and calls[-1][1:] == ("Kore", "gemini-3.8-flash-tts")
    wav2, cached2 = preview.make(tmp_path, "gemini")
    assert cached2 and wav2 == wav and len(calls) == 1               # 같은 값이면 다시 요청하지 않음
    preview.make(tmp_path, "gemini", voice="Puck", text="  짧은   문장  ")
    assert len(calls) == 2 and calls[-1][1] == "Puck" and calls[-1][0] == "짧은 문장"
    with pytest.raises(preview.PreviewError, match="모르는 Gemini 목소리"):
        preview.make(tmp_path, "gemini", voice="Argon")
    assert not list((tmp_path / "_preview").glob("*.part.wav"))


def test_preview_supertonic(tmp_path, monkeypatch):
    import numpy as np

    from recap_shorts.voice import preview, tts
    monkeypatch.setattr(modelfiles, "ok", lambda mid: False)
    with pytest.raises(preview.PreviewError) as e:
        preview.make(tmp_path, "supertonic")
    assert e.value.status == 409
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)
    seen = []
    monkeypatch.setattr(tts, "synth_one", lambda text, sid=None, **k: (seen.append(sid), (np.zeros(2400, dtype="float32"), 24000))[1])
    wav, cached = preview.make(tmp_path, "supertonic", sid=7)
    assert wav.is_file() and not cached and seen == [7]
    with pytest.raises(preview.PreviewError, match="0~9"):
        preview.make(tmp_path, "supertonic", sid=12)


def test_preview_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from recap_shorts.app import create_app
    from recap_shorts.images import provider
    from tests.pipeline.fakes import FakeDeps
    monkeypatch.setattr(provider, "local_server_up", lambda: False)
    calls = []
    _fake_tts(monkeypatch, calls)
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"})
    app = create_app(work_root=tmp_path, deps=FakeDeps(), start_runner=False)
    with TestClient(app, base_url="http://127.0.0.1:8020") as cl:
        r = cl.post("/api/settings/voice/preview", json={"engine": "gemini", "voice": "Erinome"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.headers["x-preview-cached"] == "0"
        r = cl.post("/api/settings/voice/preview", json={"engine": "gemini", "voice": "Erinome"})
        assert r.headers["x-preview-cached"] == "1" and len(calls) == 1
        assert cl.post("/api/settings/voice/preview", json={"engine": "qwen"}).status_code == 400
        assert cl.post("/api/settings/voice/preview", json={}, headers={"host": "10.0.0.5:8020"}).status_code == 403
        assert cl.get("/api/jobs").json() == []                       # 미리듣기 폴더는 작업 목록에 안 나옴


def test_preview_gemini_error_is_korean_only(tmp_path, monkeypatch):
    """미리듣기가 실패해도 화면에는 한국어 한 줄 — 영문 API 원문·JSON 이 나가지 않는다."""
    from recap_shorts import errors
    from recap_shorts.voice import gemini as gtts
    from recap_shorts.voice import preview

    def bad_key(*a, **k):
        raise errors.mark(RuntimeError('Gemini TTS 요청 실패: HTTP 400: [{"error": {"message": "API key not valid."}}]'),
                          errors.BAD_KEY, 400)
    monkeypatch.setattr(gtts, "call", bad_key)
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"})
    with pytest.raises(preview.PreviewError) as e:
        preview.make(tmp_path, "gemini")
    assert e.value.status == 502 and str(e.value) == "Gemini API 키가 맞지 않습니다 — 위 Gemini 칸의 키를 확인하세요"

    def down(*a, **k):
        raise errors.mark(RuntimeError("Gemini TTS 재시도 실패: HTTP 503: overloaded"), errors.HTTP, 503)
    monkeypatch.setattr(gtts, "call", down)
    with pytest.raises(preview.PreviewError) as e:
        preview.make(tmp_path, "gemini", voice="Puck")
    assert "HTTP 503" in str(e.value) and "overloaded" not in str(e.value)
