"""설정 저장소(recap_shorts/settings.py) 시험: 실제 파일은 시험마다 새 임시 파일(tests/conftest.py)."""
import json

import pytest

from recap_shorts import settings


def test_missing_file_means_no_keys():
    assert settings.get_key("gemini_api_key") == ""
    assert settings.key_source("gemini_api_key") is None
    assert settings.image_engine() == "off"              # 키가 없으면 AI 그림 끔(키가 있으면 gemini)


def test_unknown_name_raises():
    with pytest.raises(KeyError):
        settings.get_key("nope")


def test_env_comes_before_file(monkeypatch):
    settings.save({"gemini_api_key": "file-key-0000000000"})
    assert settings.get_key("gemini_api_key") == "file-key-0000000000"
    assert settings.key_source("gemini_api_key") == "file"
    monkeypatch.setenv("RECAP_GEMINI_API_KEY", "  env-key-00000000000  ")
    assert settings.get_key("gemini_api_key") == "env-key-00000000000"     # 앞뒤 공백은 뺀다
    assert settings.key_source("gemini_api_key") == "env"


def test_blank_env_is_ignored(monkeypatch):
    settings.save({"gemini_api_key": "file-key-0000000000"})
    monkeypatch.setenv("RECAP_GEMINI_API_KEY", "   ")
    assert settings.get_key("gemini_api_key") == "file-key-0000000000"
    assert settings.key_source("gemini_api_key") == "file"


def test_save_writes_expected_file_shape(tmp_path, monkeypatch):
    p = tmp_path / "s" / "settings.local.json"
    monkeypatch.setenv("RECAP_SETTINGS_FILE", str(p))
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"}, "gemini")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"keys": {"gemini_api_key": "test-gemini-key-0000000000"}, "image_engine": "gemini"}
    assert settings.image_engine() == "gemini"
    assert not list(p.parent.glob("*.tmp")), "임시 파일이 남지 않아야 함"


def test_save_keeps_others_and_deletes_with_empty(monkeypatch):
    settings.save({"gemini_api_key": "a" * 20, "cf_account_id": "acct-1"})
    settings.save({"cf_account_id": None}, None)
    assert settings.get_key("gemini_api_key") == "a" * 20
    assert settings.get_key("cf_account_id") == ""
    settings.save({"gemini_api_key": ""})
    assert settings.get_key("gemini_api_key") == ""


@pytest.mark.parametrize("old", ["bogus", "auto"])      # "auto"(자동)는 더 이상 쓰지 않는 값
def test_image_engine_falls_back_to_default_on_unknown_value(tmp_path, monkeypatch, old):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"keys": {}, "image_engine": old}), encoding="utf-8")
    monkeypatch.setenv("RECAP_SETTINGS_FILE", str(p))
    assert settings.image_engine() == "off"              # 모르는 값은 기본으로(키가 없으니 끔)


@pytest.mark.parametrize("bad", ["키", "has space", "tab\there", "\x01control", "x" * 513])
def test_save_rejects_bad_values(bad):
    with pytest.raises(settings.SettingsError) as e:
        settings.save({"gemini_api_key": bad})
    assert e.value.status == 400
    assert str(e.value) == "키에 쓸 수 없는 글자가 있습니다"


def test_save_rejects_unknown_name_and_engine():
    with pytest.raises(settings.SettingsError) as e:
        settings.save({"nope": "x" * 10})
    assert e.value.status == 400
    with pytest.raises(settings.SettingsError) as e:
        settings.save({}, "photoshop")
    assert e.value.status == 400


def test_save_refuses_env_owned_key(monkeypatch):
    monkeypatch.setenv("RECAP_CF_API_TOKEN", "env-token-000000")
    with pytest.raises(settings.SettingsError) as e:
        settings.save({"cf_api_token": "other-token-0000"})
    assert e.value.status == 409
    assert str(e.value) == "환경변수 RECAP_CF_API_TOKEN 로 정해져 있어 여기서 바꿀 수 없습니다"
    assert "env-token" not in str(e.value) and "other-token" not in str(e.value)


def test_public_view_masks_and_never_leaks(monkeypatch):
    settings.save({"gemini_api_key": "test-gemini-key-0000000000", "cf_account_id": "tiny-id7", "cf_api_token": ""})
    v = settings.public_view()
    assert v["ready"] is True
    by = {k["name"]: k for k in v["keys"]}
    assert by["gemini_api_key"]["masked"] == "••••0000"
    assert by["gemini_api_key"]["set"] is True and by["gemini_api_key"]["source"] == "file"
    assert by["cf_account_id"]["masked"] == "••••"
    assert by["cf_api_token"]["masked"] is None and by["cf_api_token"]["set"] is False
    dumped = json.dumps(v, ensure_ascii=False)
    for secret in ("test-gemini-key-0000000000", "tiny-id7"):
        assert secret not in dumped
    assert [k["name"] for k in v["keys"]] == [k["name"] for k in settings.KEYS]
    assert [g["id"] for g in v["groups"]] == ["gemini", "openrouter", "cloudflare"]
    assert [e["id"] for e in v["image_engines"]] == ["local", "gemini", "off"]
    assert {"file", "ready", "ready_reason", "keys", "groups", "image_engine", "image_engines", "image_server", "model_roles",
            "voice", "sfx", "memes", "models", "setup_cmd"} == set(v)


def test_public_view_env_source(monkeypatch):
    monkeypatch.setenv("RECAP_GEMINI_API_KEY", "env-gemini-key-000000")
    v = settings.public_view()
    k = next(k for k in v["keys"] if k["name"] == "gemini_api_key")
    assert k["source"] == "env" and k["masked"] == "••••0000"


def test_public_view_path_and_models(tmp_path, monkeypatch):
    monkeypatch.setenv("RECAP_SETTINGS_FILE", str(tmp_path / "mine.json"))
    v = settings.public_view()
    assert v["file"] == "mine.json"          # 저장소 밖이면 파일 이름만
    ids = [m["id"] for m in v["models"]]
    assert ids == ["sensevoice", "supertonic"]


def test_corrupt_file_is_treated_as_empty(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("RECAP_SETTINGS_FILE", str(p))
    assert settings.get_key("gemini_api_key") == ""
    assert settings.image_engine() == "off"
    settings.save({"gemini_api_key": "a" * 20})              # 깨진 파일도 덮어써서 다시 쓸 수 있다
    assert settings.get_key("gemini_api_key") == "a" * 20
