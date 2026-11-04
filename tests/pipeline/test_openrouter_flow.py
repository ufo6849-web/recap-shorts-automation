"""OpenRouter 흐름 시험: 실제 Deps 로 자료 읽기(step_read)와 대본(step_script)을 돌리고 requests.post 만 가짜로 바꾼다.
Gemini 키 없이 OpenRouter 키만 있을 때, 읽기는 google/gemini-3.8-flash, 대본은 anthropic/claude-opus-5.5 로 가야 한다."""
import io
import json

import requests
from PIL import Image

from recap_shorts import pipeline as P
from recap_shorts import settings, usage
from recap_shorts.collect.http import Blocked
from recap_shorts.images import sensitive
from tests.pipeline.fakes import READ_TEXT, FakeLLM, png, FC_CLAIM, FC_FACT, FC_SOURCE

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_KEY = "sk-or-test-0000000000000000"
SRC = {"gallery": "baseball_new13", "no": 2101, "minor": False,
       "url": "https://gall.dcinside.com/board/view/?id=baseball_new13&no=2101"}


class Resp:
    status_code = 200
    text = ""

    def __init__(self, content, annotations=None):
        self._d = {"choices": [{"message": {"role": "assistant", "content": content, "annotations": annotations or []}, "finish_reason": "stop"}],
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
            prompt = "".join(p.get("text", "") for p in parts if p["type"] == "text")
            if "너는 쇼츠 영상을 만들기 전에" in prompt:
                content = {"kind": "info", "claims": [FC_CLAIM]}
            elif "너는 한국어 뉴스 사실 확인 담당" in prompt:
                content = {"results": [{"id": "C1", "verdict": "true", "fact": FC_FACT,
                                        "sources": [FC_SOURCE["url"]]}]}
                return Resp(json.dumps(content, ensure_ascii=False), annotations=[
                    {"url_citation": {"url": FC_SOURCE["url"], "title": FC_SOURCE["title"]}}])
            else:
                content = self.llm.complete_json(prompt)
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
    assert models[1] == "google/gemini-3.8-flash"                       # 사실 확인 분류(정보성 판별)도 같은 Flash — 읽기 단계에서 한 번 더
    assert models[2:] and all(m == "anthropic/claude-opus-5.5" for m in models[2:])   # 초안·다듬기는 Claude Opus 5.5
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


# ---------------------------------------------------------------- 자료 화면 단계의 비전 호출(OpenRouter 키만)
PIC_URL = "https://upload.wikimedia.org/wikipedia/ko/a/a1/dallae.png"


def _png_bytes(size=(400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 90, 60)).save(buf, "PNG")
    return buf.getvalue()


class Page:
    """내려받은 주소의 내용(위키백과 요약 JSON 또는 그림 바이트)."""

    def __init__(self, content: bytes):
        self.content, self.url, self.final_url, self.status = content, "", "", 200

    @property
    def text(self):
        return self.content.decode("utf-8")


class WikiClient:
    """위키백과 요약과 그림 주소만 돌려주는 HTTP 가짜(네트워크 없음). 그 밖의 주소는 막힌 것으로 본다."""

    def fetch(self, url, *, check_robots=True, use_cache=True, timeout=None):
        if "/api/rest_v1/page/summary/" in url:
            doc = {"title": "달래", "originalimage": {"source": PIC_URL},
                   "content_urls": {"desktop": {"page": "https://ko.wikipedia.org/wiki/달래"}}}
            return Page(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        if url == PIC_URL:
            return Page(_png_bytes())
        return Blocked(url, "http_404")


def answer_for(text: str) -> dict:
    """보낸 글을 보고 그 호출에 맞는 답을 고른다(답의 내용은 시험 대상이 아니다 — 모델 이름만 본다)."""
    if "is_match" in text:                                        # 대상 사진 확인
        return {"is_match": "yes", "clear": True, "real_face": False, "mostly_text": False, "note": "ok"}
    if "대상 종류(kind)" in text:                                  # 대상 추출
        return {"items": [{"sentence": 1, "name_ko": "달래", "name_en": "Dallae", "name_ja": "", "kind": "character",
                           "real_person": False, "query_hints": [], "why": "마스코트"}]}
    if "level" in text:                                           # 민감한 그림 판정
        return {"items": [{"i": 1, "level": "none", "kind": "none", "reason": ""}]}
    if "parts" in text:                                           # 캡처 위치 찾기
        return {"parts": [], "private": []}
    return {"items": [], "worst": []}                             # 그림 품질


class VisionFake:
    """requests.post 가짜: 보낸 모델·주소·글을 적고 답을 돌려준다."""

    def __init__(self):
        self.sent = []

    def __call__(self, url, **kw):
        payload = kw["json"]
        text = "".join(p.get("text", "") for p in payload["messages"][0]["content"] if p["type"] == "text")
        self.sent.append({"url": url, "model": payload["model"], "auth": kw["headers"].get("Authorization"), "text": text})
        return Resp(json.dumps(answer_for(text), ensure_ascii=False))


def test_assets_vision_calls_all_use_gemini_3_8_flash(tmp_path, monkeypatch):
    """자료 화면 단계가 부르는 비전 호출 넷(캡처 위치, 민감한 그림 읽기, 대상 사진 추출·확인, 그림 품질)을
    OpenRouter 키만으로 부르면 모두 google/gemini-3.8-flash 로 간다. 일반 시험에서 이 호출을 건너뛰는 규칙
    (PYTEST_CURRENT_TEST)은 이 시험 안에서만 푼다."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    settings.save({"openrouter_api_key": OR_KEY})
    vision = VisionFake()
    monkeypatch.setattr(requests, "post", vision)
    monkeypatch.setattr(usage, "log", lambda kind, **f: None)
    monkeypatch.setattr(P.Deps, "http_client", lambda self, job_dir: WikiClient())
    job = make_job(tmp_path)
    d = job.dir
    deps = P.Deps()
    shot = d / "post" / "img_1.png"

    found = deps.locate_regions(shot, ["댓글 닉네임 — 문장: 구청 직원이 셋이나"])
    sens = sensitive.run(d, [{"file": "post/img_1.png", "kind": "cap", "status": "ok"}],
                         read_fn=deps.sensitive_read, cfg=sensitive.load_config())
    script = {"title_lines": ["동네 축구"], "hook": "구청 직원", "sentences": [{"text": "달래라는 이름이야."}]}
    timings = {"duration": 3.0, "sentences": [{"index": 1, "role": "body", "text": "달래라는 이름이야.", "start": 0.0, "end": 3.0}]}
    ents = deps.entity_images(d, script, timings, {"attachments": []})
    rv = deps.image_quality([shot], ["동네 운동장 전경"])

    assert "parts" in found and sens["checked"] == 1 and sens["errors"] == []
    assert ents["found"] == ["달래"] and "items" in rv
    for marker in ("가로", "level", "대상 종류(kind)", "is_match", "보조 화면"):   # 다섯 가지 호출이 모두 나갔는지
        assert any(marker in s["text"] for s in vision.sent), marker
    assert all(s["model"] == "google/gemini-3.8-flash" for s in vision.sent)
    assert all(s["url"] == OR_URL and s["auth"] == f"Bearer {OR_KEY}" for s in vision.sent)
