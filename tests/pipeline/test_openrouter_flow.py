"""OpenRouter 흐름 시험: 실제 Deps 로 자료 읽기(step_read)와 대본(step_script)을 돌리고 requests.post 만 가짜로 바꾼다.
Gemini 키 없이 OpenRouter 키만 있을 때, 읽기는 google/gemini-3.8-flash, 대본은 anthropic/claude-opus-5.5 로 가야 한다."""
import json

import requests

from recap_shorts import pipeline as P
from recap_shorts import settings, usage
from tests.pipeline.fakes import READ_TEXT, FakeLLM, png

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_KEY = "sk-or-test-0000000000000000"
SRC = {"gallery": "baseball_new13", "no": 2101, "minor": False,
       "url": "https://gall.dcinside.com/board/view/?id=baseball_new13&no=2101"}


class Resp:
    status_code = 200
    text = ""

    def __init__(self, content):
        self._d = {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.01}}

    def json(self):
        return self._d


class OpenRouter:
    """requests.post 대신 쓴다. 그림이 있으면 읽기 답, 없으면 대본 답(초안·다듬기 — 시험용 FakeLLM 과 같은 답)을 돌려준다."""

    def __init__(self):
        self.sent = []
        self.llm = FakeLLM()

    def __call__(self, url, **kw):
        payload = kw["json"]
        self.sent.append({"url": url, "model": payload["model"], "auth": kw["headers"].get("Authorization")})
        parts = payload["messages"][0]["content"]
        if any(p["type"] == "image_url" for p in parts):
            content = {"kind": "비교표", "fan_made": False, "text": READ_TEXT, "summary": "표", "has_face": False,
                       "has_id_text": False}
        else:
            content = self.llm.complete_json("".join(p.get("text", "") for p in parts if p["type"] == "text"))
        return Resp(json.dumps(content, ensure_ascii=False))


def make_job(tmp_path):
    job = P.JobStore(tmp_path / "work").create(dict(SRC), {"auto_steps": []})
    d = job.dir
    (d / "post").mkdir(parents=True, exist_ok=True)
    (d / "post" / "post_2101.json").write_text(json.dumps(
        {"title": "[가상] 동네 축구 결승전에 구청 직원이 셋이나", "text": "동네 축구대회 결승인데 구청 직원이 셋이나 나왔네...",
         "written_at": "2026-09-26 01:55:37"}, ensure_ascii=False), encoding="utf-8")
    png(d / "post" / "img_1.png", (400, 300))
    (d / "post" / "attachments.json").write_text(json.dumps({"files": ["post/img_1.png"]}), encoding="utf-8")
    return job


def test_openrouter_only_read_and_script_use_the_right_models(tmp_path, monkeypatch):
    settings.save({"openrouter_api_key": OR_KEY})
    settings.save(models={"script": "anthropic/claude-opus-5.5"})
    or_post = OpenRouter()
    monkeypatch.setattr(requests, "post", or_post)
    rows = []
    monkeypatch.setattr(usage, "log", lambda kind, **f: rows.append((kind, f)))
    job = make_job(tmp_path)
    deps = P.Deps()

    P.step_read(P.Ctx(job, "read", deps))
    assert (job.dir / "topic.json").is_file()
    P.step_script(P.Ctx(job, "script", deps))

    assert (job.dir / "script_final" / "script.json").is_file()
    models = [s["model"] for s in or_post.sent]
    assert models[0] == "google/gemini-3.8-flash"                       # 그림 읽기는 Gemini 3.8 Flash
    assert models[1:] and all(m == "anthropic/claude-opus-5.5" for m in models[1:])   # 초안·다듬기는 Claude Opus 5.5
    assert all(s["url"] == OR_URL and s["auth"] == f"Bearer {OR_KEY}" for s in or_post.sent)
    assert [kind for kind, _ in rows] == ["openrouter"] * len(models)
    assert [f["model"] for _, f in rows] == models                       # 기록에도 같은 모델 이름이 남는다
    assert rows[0][1]["images"] == 1 and rows[0][1]["cost"] == 0.01


def test_description_names_the_real_voice_engine_without_gemini_key(tmp_path, monkeypatch):
    """Gemini 키가 없으면 목소리는 로컬(Supertonic)이고, 설명 문구도 Google Gemini TTS 라고 적지 않는다."""
    from recap_shorts import modelfiles
    from tests.pipeline.fakes import FakeDeps
    from tests.pipeline.test_pipeline import go, make
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)
    store, runner, job = make(tmp_path, FakeDeps())
    go(runner, job)
    for _ in range(4):                       # 대본·장면·검수 확인, 그다음 승인
        P.continue_job(runner, job)
        assert runner.wait_idle(60)
    assert job.state["status"] == "done"
    desc = (job.dir / "out" / "description.txt").read_text(encoding="utf-8")
    assert "Supertonic" in desc and "Google Gemini TTS" not in desc
