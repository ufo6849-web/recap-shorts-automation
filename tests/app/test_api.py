import json
import time

import pytest
from fastapi.testclient import TestClient

from recap_shorts import pipeline as P
from recap_shorts.app import create_app
from recap_shorts.collect import dcinside
from tests.pipeline.fakes import FakeDeps


@pytest.fixture
def env(tmp_path):
    deps = FakeDeps()
    app = create_app(work_root=tmp_path, deps=deps)
    with TestClient(app) as client:
        yield client, app, deps


def idle(app, t=60):
    assert app.state.runner.wait_idle(t)


def new_job(client, app, options=None, **body):
    body = body or {"gallery": "baseball_new13", "no": 2101}
    if options is not None:
        body["options"] = options
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 200, r.text
    idle(app)
    return r.json()["id"]


def step(state, key):
    return next(s for s in state["steps"] if s["key"] == key)


def read_json(job_dir, rel):
    return json.loads((job_dir / rel).read_text(encoding="utf-8"))


def test_health_shape(env):
    from recap_shorts import settings
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"})    # 기본 AI 그림(Gemini)이 켜진 상태에서 본다
    client, app, deps = env
    h = client.get("/api/health").json()
    assert set(h) == {"gemini_key", "cf_keys", "gpu", "image_server", "asr_model", "ffmpeg"}
    assert all(set(v) == {"ok", "detail"} for v in h.values())
    assert h["image_server"]["ok"] is True
    assert "TESTKEY" not in json.dumps(h)


def test_index_without_dist(env):
    from recap_shorts.app.server import ROOT
    if (ROOT / "web" / "dist").is_dir():
        pytest.skip("web/dist 가 있으면 프론트가 제공됨")
    client, app, _ = env
    r = client.get("/")
    assert r.status_code == 200 and r.json()["app"] == "recap-shorts"


def test_cors_header(env):
    client, _, _ = env
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_create_job_validation(env):
    client, _, _ = env
    assert client.post("/api/jobs", json={}).status_code == 400
    assert client.post("/api/jobs", json={"url": "https://example.com/x"}).status_code == 400
    assert client.post("/api/jobs", json={"gallery": "bad id!", "no": 1}).status_code == 400
    assert client.get("/api/jobs/nope").status_code == 404


def test_job_flow_over_rest(env):
    client, app, deps = env
    jid = new_job(client, app, url="https://gall.dcinside.com/board/view/?id=baseball_new13&no=2101")
    st = client.get(f"/api/jobs/{jid}").json()
    assert st["status"] == "waiting" and step(st, "read")["status"] == "waiting"
    assert st["source"]["no"] == 2101 and st["thumbnail"].startswith("post/img_")
    # topic 확인/수정
    topic = client.get(f"/api/jobs/{jid}/topic").json()
    assert "source_text" in topic
    # 이어 가기 → 대본
    st = client.post(f"/api/jobs/{jid}/continue").json()
    idle(app)
    st = client.get(f"/api/jobs/{jid}").json()
    assert step(st, "script")["status"] == "waiting"
    sc = client.get(f"/api/jobs/{jid}/script").json()
    assert sc["draft"]["sentences"][0]["text"] != sc["script"]["sentences"][0]["text"]
    assert sc["check"]["refine"]["applied"] is True
    # 대본 고치기 → 가드 다시, 다음 단계로 안 넘어감
    new = sc["script"]
    new["sentences"][2]["text"] = "근데 이게 전부 구청 공무원들끼리 하는 리그라니 진짜 놀랍지 않아?"
    put = client.put(f"/api/jobs/{jid}/script", json={"script": new}).json()
    assert put["script"]["sentences"][2]["text"].startswith("근데 이게 전부 구청")
    assert "per_sentence_syllables" in put["check"]
    client.post(f"/api/jobs/{jid}/continue")       # 고친 대본 확인 → 목소리·자료 화면·장면 배치까지
    idle(app)
    st = client.get(f"/api/jobs/{jid}").json()
    assert step(st, "scenes")["status"] == "waiting", [s["status"] for s in st["steps"]]
    # 장면 읽기/고치기
    scenes = client.get(f"/api/jobs/{jid}/scenes").json()
    assert scenes["sentences"][0]["images"][0]["file"] and "score" in scenes["sentences"][0]["images"][0]
    assert scenes["candidates"] and scenes["scored_by"] == "heuristic"
    plan = {str(s["index"]): [i["file"] for i in s["images"]] for s in scenes["sentences"]}
    other = scenes["candidates"][-1]["file"]
    plan["2"] = [other]
    after = client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan}).json()
    assert [i["file"] for i in after["sentences"][2]["images"]] == [other]
    bad = client.put(f"/api/jobs/{jid}/scenes", json={"plan": {"2": ["assets/none.png"]}})
    assert bad.status_code == 400
    assert client.put(f"/api/jobs/{jid}/scenes", json={"plan": {"2": []}}).status_code == 400
    # 가리기: 원본 좌표 사각형 → 같은 원본에서 나온 카드를 모두 다시 만들고 assets.json 에 기록
    from PIL import Image
    ents = client.get(f"/api/jobs/{jid}/assets").json()
    capent = next(e for e in ents if e["kind"] == "cap" and e.get("src"))
    before = Image.open(app.state.store.get(jid).dir / capent["file"]).tobytes()
    r = client.post(f"/api/jobs/{jid}/assets/blur", json={"file": capent["file"], "boxes": [[0, 0, 200, 120]]})
    assert r.status_code == 200 and capent["file"] in r.json()["cards"]
    ents = client.get(f"/api/jobs/{jid}/assets").json()
    done = next(e for e in ents if e["file"] == capent["file"])
    assert done["blurred"] is True and done["needs_review"] is False and done["blur_boxes"] == [[0, 0, 200, 120]]
    assert Image.open(app.state.store.get(jid).dir / capent["file"]).tobytes() != before
    assert client.post(f"/api/jobs/{jid}/assets/blur", json={"file": "assets/ai/none.png", "boxes": []}).status_code == 404
    assert client.post(f"/api/jobs/{jid}/assets/blur", json={"file": capent["file"], "boxes": [["a"]]}).status_code == 400
    # 문장 다시 녹음
    r = client.post(f"/api/jobs/{jid}/voice/redo", json={"index": 3})
    assert r.status_code == 200
    idle(app)
    assert deps.redo_args[-1][0] == 3 and deps.redo_args[-1][1][0][1] == ""
    assert client.post(f"/api/jobs/{jid}/voice/redo", json={"index": 99}).status_code == 400
    # 이미지 다시 만들기
    ai = next(c for c in scenes["candidates"] if c["kind"] == "ai")
    n0 = deps.calls["generate_image"]
    assert client.post(f"/api/jobs/{jid}/assets/regenerate", json={"file": ai["file"], "prompt": "A quiet office"}).status_code == 200
    idle(app)
    assert deps.calls["generate_image"] == n0 + 1 and "A quiet office" in deps.generated[-1]
    cap = next(c for c in scenes["candidates"] if c["kind"] == "cap")
    assert client.post(f"/api/jobs/{jid}/assets/regenerate", json={"file": cap["file"]}).status_code == 404
    # 렌더 → 검수 대기
    r = client.post(f"/api/jobs/{jid}/render")
    assert r.status_code == 200
    idle(app)
    st = client.get(f"/api/jobs/{jid}").json()
    assert step(st, "review")["status"] == "waiting" and st["artifacts"]["video"] == "out/final.mp4"
    media = client.get(f"/media/{jid}/out/final.mp4")
    assert media.status_code == 200 and media.content.startswith(b"\x00\x00\x00\x18ftyp")
    assert "AI 음성" in client.get(f"/media/{jid}/out/description.txt").text
    client.post(f"/api/jobs/{jid}/continue")
    idle(app)
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "done"
    lst = client.get("/api/jobs").json()
    assert [j["id"] for j in lst] == [jid] and "steps" in lst[0]


def test_media_blocks_traversal(env):
    client, app, _ = env
    jid = new_job(client, app)
    assert client.get(f"/media/{jid}/../../state.json").status_code in (404, 400)
    assert client.get(f"/media/{jid}/%2e%2e/%2e%2e/pytest.ini").status_code == 404
    assert client.get(f"/media/{jid}/nothing.png").status_code == 404
    assert client.get(f"/media/zzz/state.json").status_code == 404
    assert client.get(f"/media/{jid}/state.json").status_code == 200


def test_busy_job_returns_409(env):
    client, app, deps = env
    import threading
    gate = threading.Event()
    orig = deps.read_image

    def slow(images, instruction):
        gate.wait(10)
        return orig(images, instruction)
    deps.read_image = slow
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101})
    jid = r.json()["id"]
    for _ in range(100):                                   # source 가 끝나고 read 에서 막힐 때까지
        if step(client.get(f"/api/jobs/{jid}").json(), "read")["status"] == "running":
            break
        time.sleep(0.02)
    assert client.post(f"/api/jobs/{jid}/continue").status_code == 409
    assert client.put(f"/api/jobs/{jid}/script", json={"script": {"sentences": []}}).status_code in (409,)
    gate.set()
    idle(app)
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "waiting"


def test_error_and_continue_retries(env):
    client, app, deps = env
    deps.fail_once.add("get_post")
    jid = new_job(client, app)
    st = client.get(f"/api/jobs/{jid}").json()
    assert st["status"] == "error" and step(st, "source")["status"] == "error" and step(st, "source")["error"]
    client.post(f"/api/jobs/{jid}/continue")
    idle(app)
    st = client.get(f"/api/jobs/{jid}").json()
    assert st["status"] == "waiting" and step(st, "source")["status"] == "done"


def test_topics_endpoint_with_cache_and_triage(env, monkeypatch):
    client, app, deps = env
    calls = []

    def fake_list(gallery, minor=False, page=1, keyword=None, client=None, recommend=False):
        calls.append(page)
        return [dcinside.PostSummary(no=100 * page + i, title=f"제목 {page}-{i}", date="10.17", views=10, recommends=5,
                                     comments=1, url="u") for i in range(2)]
    monkeypatch.setattr(dcinside, "list_posts", fake_list)
    deps.clef_triage = lambda posts: [{"no": p["no"], "category": {"스포츠·야구": 0.9}, "hidden": p["no"] == 200}
                                      for p in posts]
    r = client.get("/api/topics?gallery=baseball_new13&pages=2").json()
    assert r["triage"] == "ok" and len(r["items"]) == 4 and calls == [1, 2]
    assert r["items"][0]["triage"]["category"] and {"no", "title", "recommends", "views", "url"} <= set(r["items"][0])
    client.get("/api/topics?gallery=baseball_new13&pages=2")
    assert calls == [1, 2]                                  # 10분 캐시
    client.get("/api/topics?gallery=baseball_new13&pages=2&refresh=true")
    assert calls == [1, 2, 1, 2]


def test_topics_without_clef(env, monkeypatch):
    client, app, deps = env
    monkeypatch.setattr(dcinside, "list_posts", lambda *a, **k: [dcinside.PostSummary(1, "t", "d", 1, 1, 0, "u")])
    r = client.get("/api/topics?pages=1").json()
    assert r["triage"] == "unavailable" and r["items"][0]["triage"] is None


def test_script_refine_endpoint(env):
    client, app, deps = env
    jid = new_job(client, app, options={"auto_topic": True})
    st = client.get(f"/api/jobs/{jid}").json()
    assert step(st, "script")["status"] == "waiting"
    n = len(deps.llm.calls)
    assert client.post(f"/api/jobs/{jid}/script/refine").status_code == 200
    idle(app)
    assert len(deps.llm.calls) == n + 1
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "waiting"


def test_topic_edit_resets_script(env):
    client, app, deps = env
    jid = new_job(client, app, options={"auto_topic": True})
    client.put(f"/api/jobs/{jid}/topic", json={"topic": "새 주제"})
    st = client.get(f"/api/jobs/{jid}").json()
    assert step(st, "script")["status"] == "todo" and step(st, "read")["status"] == "waiting"
    client.post(f"/api/jobs/{jid}/continue")
    idle(app)
    assert step(client.get(f"/api/jobs/{jid}").json(), "script")["status"] == "waiting"


def test_hint_only_edit_keeps_voice_and_rerun(env):
    client, app, deps = env
    jid = new_job(client, app, options={"auto_steps": ["read", "script"]})
    st = client.get(f"/api/jobs/{jid}").json()
    assert step(st, "scenes")["status"] == "waiting"
    n_voice = deps.calls["make_voice"]
    sc = client.get(f"/api/jobs/{jid}/script").json()["script"]
    sc["sentences"][0]["image_hint"] = "AI 장면: 구청 사무실 / 캡처: 비교표의 상대 팀 줄"
    client.put(f"/api/jobs/{jid}/script", json={"script": sc})
    st = client.get(f"/api/jobs/{jid}").json()
    assert step(st, "voice")["status"] == "done" and step(st, "assets")["status"] == "todo"
    client.post(f"/api/jobs/{jid}/continue")
    idle(app)
    st = client.get(f"/api/jobs/{jid}").json()
    assert deps.calls["make_voice"] == n_voice and step(st, "scenes")["status"] == "waiting"
    ents = client.get(f"/api/jobs/{jid}/assets").json()
    assert any(e.get("role") == "focus" and e["sentence"] == 1 for e in ents)     # 새 '캡처:' 힌트로 잘라 낸 카드
    # 단계부터 다시
    assert client.post(f"/api/jobs/{jid}/rerun", json={"from": "voice"}).status_code == 400
    r = client.post(f"/api/jobs/{jid}/rerun", json={"from": "assets"})
    assert r.status_code == 200
    idle(app)
    st = client.get(f"/api/jobs/{jid}").json()
    assert deps.calls["make_voice"] == n_voice and step(st, "scenes")["status"] == "waiting"
    # 대사가 바뀌면 목소리부터
    sc = client.get(f"/api/jobs/{jid}/script").json()["script"]
    sc["sentences"][0]["text"] = sc["sentences"][0]["text"].replace("직원이었어", "직원이었대")
    client.put(f"/api/jobs/{jid}/script", json={"script": sc})
    assert step(client.get(f"/api/jobs/{jid}").json(), "voice")["status"] == "todo"


def test_blur_on_old_panel_card_is_not_black(env):
    """칸 카드에 '가리기 적용'을 누르니 검은 화면이 되던 문제 — 예전 칸 카드는 crop 에 원본 좌표가 들어 있었다."""
    import numpy as np
    from PIL import Image
    client, app, deps = env
    jid = new_job(client, app, options={"auto_steps": ["read", "script"]})
    d = app.state.store.get(jid).dir
    (d / "post" / "panels").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 170), (240, 200, 120)).save(d / "post" / "panels" / "a01_05.png")
    ap = d / "assets" / "assets.json"
    ents = json.loads(ap.read_text(encoding="utf-8"))
    ents.append({"kind": "cap", "status": "ok", "file": "assets/cap/c01_s01.png", "src": "post/panels/a01_05.png",
                 "attachment": 1, "sentence": 1, "role": "focus", "crop": [0, 890, 300, 1060], "panel": 5, "blur_boxes": []})
    ap.write_text(json.dumps(ents, ensure_ascii=False), encoding="utf-8")
    r = client.post(f"/api/jobs/{jid}/assets/blur", json={"file": "assets/cap/c01_s01.png", "boxes": [[250, 0, 300, 40]]})
    assert r.status_code == 200
    card = np.asarray(Image.open(d / "assets" / "cap" / "c01_s01.png").convert("L"), dtype=float)
    assert card.mean() > 60          # 칸 그림이 보인다(검은 카드면 0 근처)


def test_intro_preview_matches_renderer(env):
    """장면 편집기 미리보기용 인트로 카드 = 렌더러 IntroCard 가 그린 글자 묶음 — 앱 미리보기에 인트로가 안 보이던 문제."""
    import base64
    import io
    from pathlib import Path

    from PIL import Image

    from recap_shorts.render import intro as RI
    client, app, _ = env
    jid = new_job(client, app)
    d = app.state.store.get(jid).dir
    gold = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "intro_golden" / "timeline.json").read_text(encoding="utf-8"))
    (d / "timeline.json").write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    got = client.get(f"/api/jobs/{jid}/intro_preview").json()["intro"]
    card = RI.IntroCard(gold["intro"])
    assert got["t_cut"] == card.t_cut and got["t_pop"] == card.t_pop and got["group_cy"] == card.cy_g
    assert got["label"] == gold["intro"]["label"] and got["label_icon"] == gold["intro"]["label_icon"]
    png = Image.open(io.BytesIO(base64.b64decode(got["group"].split(",", 1)[1])))
    assert png.size == card.group.size and png.tobytes() == card.group.tobytes()
    gold.pop("intro")
    (d / "timeline.json").write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    assert client.get(f"/api/jobs/{jid}/intro_preview").json() == {"intro": None}


def test_web_index_is_not_cached(tmp_path):
    """다시 빌드한 화면이 보이게 index.html 은 no-cache, js 같은 이름에 해시가 붙은 파일은 그대로."""
    from fastapi import FastAPI

    from recap_shorts.app.server import WebFiles
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "a.js").write_text("1", encoding="utf-8")
    app = FastAPI()
    app.mount("/", WebFiles(directory=tmp_path, html=True), name="web")
    with TestClient(app) as c:
        assert c.get("/").headers.get("cache-control") == "no-cache"
        assert c.get("/index.html").headers.get("cache-control") == "no-cache"
        assert "cache-control" not in c.get("/a.js").headers


# ---------------------------------------------------------------- 사용자 컷(문장 안 그림 경계)
def scenes_job(client, app):
    """장면 배치까지 끝난 작업 하나 — auto_steps 로 대본 확인·목소리·장면까지 한 번에."""
    jid = new_job(client, app, options={"auto_steps": ["read", "script"]})
    assert step(client.get(f"/api/jobs/{jid}").json(), "scenes")["status"] == "waiting"
    return jid


def plan_of(client, jid):
    sc = client.get(f"/api/jobs/{jid}/scenes").json()
    return {str(s["index"]): [i["file"] for i in s["images"]] for s in sc["sentences"]}, sc


def test_scenes_view_has_span_gaps_and_saved_cuts(env):
    client, app, _ = env
    jid = scenes_job(client, app)
    _, sc = plan_of(client, jid)
    s0, s1 = sc["sentences"][0], sc["sentences"][1]
    assert s0["span"] == [0.0, s1["start"]] and s1["span"] == [s1["start"], sc["sentences"][2]["start"]]
    assert sc["sentences"][-1]["span"][1] > sc["sentences"][-1]["end"]     # 마지막은 영상 끝(tail 포함)
    assert all(isinstance(s["gaps"], list) for s in sc["sentences"])       # 받아쓰기 꺼짐(시험 기본) → 빈 목록
    assert all(s["cuts"] is None for s in sc["sentences"])
    # 손으로 shot_plan 에 사용자 컷을 넣으면 그대로 보인다
    d = app.state.store.get(jid).dir
    doc = json.loads((d / "shot_plan.json").read_text(encoding="utf-8"))
    doc["cuts"] = {"1": {"t": [4.5], "span": [3.2, 6.3]}}
    (d / "shot_plan.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    sc2 = client.get(f"/api/jobs/{jid}/scenes").json()
    assert [s["cuts"] for s in sc2["sentences"]] == [None, [4.5], None, None, None, None][:len(sc2["sentences"])]


def test_put_scenes_saves_cuts_and_rebuilds_timeline(env):
    client, app, deps = env
    jid = scenes_job(client, app)
    plan, sc = plan_of(client, jid)
    d = app.state.store.get(jid).dir
    span = next(s for s in sc["sentences"] if s["index"] == 2)["span"]
    n = len(plan["2"])
    assert n >= 2
    t = round(span[0] + (span[1] - span[0]) * 0.6, 3)       # 경계를 옮긴다
    body = {"plan": plan, "cuts": {"2": [t] if n == 2 else [t] * (n - 1)}}
    if n > 2:      # 여러 장이면 고르게 나눈 자리에서 한 곳만 옮긴 목록
        step_ = (span[1] - span[0]) / n
        body["cuts"]["2"] = [round(span[0] + step_ * (k + 1), 3) for k in range(n - 1)]
        body["cuts"]["2"][0] = t
    r = client.put(f"/api/jobs/{jid}/scenes", json=body)
    assert r.status_code == 200, r.text
    # shot_plan.json 에 남는다(span 은 지금 timings 값)
    doc = json.loads((d / "shot_plan.json").read_text(encoding="utf-8"))
    assert doc["cuts"]["2"]["t"] == body["cuts"]["2"]
    assert doc["cuts"]["2"]["span"] == [round(x, 3) for x in span]
    # timeline.json 샷 시각이 바뀌고 그 문장 샷에 cut="user"
    tl = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
    assert tl["shots"][[i for i, s in enumerate(tl["shots"]) if s["file"] == plan["2"][0]][0]]["end"] == t
    assert [s.get("cut") for s in tl["shots"] if s["file"] in plan["2"]] == ["user"] * n
    # scenes_view 응답에도 실린다
    got = client.get(f"/api/jobs/{jid}/scenes").json()
    assert next(s for s in got["sentences"] if s["index"] == 2)["cuts"] == body["cuts"]["2"]


def test_put_scenes_cut_validation_400(env):
    client, app, _ = env
    jid = scenes_job(client, app)
    plan, sc = plan_of(client, jid)
    extra = next(c["file"] for c in sc["candidates"] if c["file"] not in plan["2"])
    plan["2"] = plan["2"] + [extra]                       # 세 장으로 만들어 '순서' 검사도 하게
    span = next(s for s in sc["sentences"] if s["index"] == 2)["span"]
    a, b = span
    assert len(plan["2"]) == 3
    cases = [
        ({"2": [a - 1.0, a + 1.0]}, "밖입니다"),
        ({"2": [a + 1.0, b + 1.0]}, "밖입니다"),
        ({"2": [a + 0.1, a + 2.0]}, "짧습니다"),
        ({"2": [a + 1.0, b - 0.2]}, "짧습니다"),
        ({"2": ["x", "y"]}, "숫자여야"),
        ({"2": [a + 2.0, a + 1.0]}, "오름차순"),
        ({"2": [a + 1.0]}, "2개여야"),                     # 개수 다름(그림 3장 → 2개)
        ({"2": "x"}, "목록이어야"),
        ({"9": [1.0]}, "대본에 없습니다"),
    ]
    for cuts, why in cases:
        r = client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan, "cuts": cuts})
        assert r.status_code == 400, (cuts, r.text)
        assert why in r.json()["detail"], (cuts, r.json()["detail"])
    assert client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan, "cuts": "x"}).status_code == 400
    # 잘못 보낸 뒤에도 저장은 안 됐다
    d = app.state.store.get(jid).dir
    assert "cuts" not in json.loads((d / "shot_plan.json").read_text(encoding="utf-8"))


def test_put_scenes_null_and_picture_count_delete_cuts(env):
    client, app, _ = env
    jid = scenes_job(client, app)
    plan, sc = plan_of(client, jid)
    d = app.state.store.get(jid).dir
    span = next(s for s in sc["sentences"] if s["index"] == 2)["span"]
    n = len(plan["2"])
    first = [round(span[0] + (span[1] - span[0]) * (k + 1) / n, 3) for k in range(n - 1)]
    assert client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan, "cuts": {"2": first}}).status_code == 200
    assert "cuts" in json.loads((d / "shot_plan.json").read_text(encoding="utf-8"))
    # body 에 없는 문장은 그림 수가 그대로면 유지
    r = client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan})
    assert r.status_code == 200 and json.loads((d / "shot_plan.json").read_text(encoding="utf-8"))["cuts"]["2"]["t"] == first
    # null → 지운다(자동으로)
    r = client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan, "cuts": {"2": None}})
    assert r.status_code == 200
    assert "cuts" not in json.loads((d / "shot_plan.json").read_text(encoding="utf-8"))
    # 다시 저장한 뒤 그림 수를 바꾸면 그 문장 컷은 지운다
    assert client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan, "cuts": {"2": first}}).status_code == 200
    plan2 = json.loads(json.dumps(plan))
    plan2["2"] = plan2["2"][:1]                          # 1장으로
    r = client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan2})
    assert r.status_code == 200
    assert "cuts" not in json.loads((d / "shot_plan.json").read_text(encoding="utf-8"))


def test_put_scenes_rollback_restores_cuts(env, monkeypatch):
    """타임라인을 만들다 실패하면 배치표와 사용자 컷을 모두 예전으로 되돌린다."""
    client, app, _ = env
    jid = scenes_job(client, app)
    plan, sc = plan_of(client, jid)
    d = app.state.store.get(jid).dir
    span = next(s for s in sc["sentences"] if s["index"] == 2)["span"]
    n = len(plan["2"])
    first = [round(span[0] + (span[1] - span[0]) * (k + 1) / n, 3) for k in range(n - 1)]
    assert client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan, "cuts": {"2": first}}).status_code == 200
    before = (d / "shot_plan.json").read_text(encoding="utf-8")

    from recap_shorts import pipeline as PL

    def boom(job):
        raise RuntimeError("일부러 실패")
    monkeypatch.setattr(PL, "write_timeline", boom)
    r = client.put(f"/api/jobs/{jid}/scenes", json={"plan": plan, "cuts": {"2": None}})
    assert r.status_code == 400 and "타임라인" in r.json()["detail"]
    after = json.loads((d / "shot_plan.json").read_text(encoding="utf-8"))
    assert after["cuts"]["2"]["t"] == json.loads(before)["cuts"]["2"]["t"]
    assert after["plan"] == json.loads(before)["plan"]


# ---------------------------------------------------------------- 블라인드(두 번째 글 출처)
def test_create_blind_job_by_url_reaches_scenes(env):
    """블라인드 글 주소로 만든 job 이 장면 배치까지 가고, 대본 머리에 사이트 이름이 한 번만 들어간다."""
    client, app, deps = env
    r = client.post("/api/jobs", json={"url": "https://www.teamblind.com/kr/post/aaaa0001",
                                       "options": {"auto_steps": ["read", "script"]}})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    assert app.state.runner.wait_idle(60)
    st = client.get(f"/api/jobs/{jid}").json()
    assert st["source"]["site"] == "blind" and st["source"]["gallery"] == "blind"
    assert st["source"]["no"] == "aaaa0001" and isinstance(st["source"]["no"], str)
    assert st["source"]["url"] == "https://www.teamblind.com/kr/post/aaaa0001"
    assert step(st, "scenes")["status"] == "waiting", [s["status"] for s in st["steps"]]
    assert deps.calls["get_post"] == 1 and deps.calls["screenshot_post"] == 1
    d = app.state.store.get(jid).dir
    assert d.name.endswith("_blind_aaaa0001")                 # 폴더 이름에 쓸 수 없는 글자는 바꾼다
    post = read_json(d, "post/post_aaaa0001.json")
    assert post["topic"] == "블라블라" and post["title"] == "점심값이 올라서 슬픈 사람의 이야기"
    assert "company" not in post and "nick" not in json.dumps(post, ensure_ascii=False).lower()
    topic = read_json(d, "topic.json")
    head = topic["source_text"].splitlines()[0]
    assert head.startswith("[게시글 1] 블라인드 블라블라 토픽 게시글"), head
    assert "디시" not in head and "블라인드 블라인드" not in head
    assert "블라인드" in topic["topic"]


def test_create_blind_job_with_slug_url(env):
    """제목-slug 가 붙은 주소도 별칭만 남겨 같은 글로 읽는다."""
    client, app, _ = env
    jid = new_job(client, app, url="https://www.teamblind.com/kr/post/%EA%B0%80%EC%83%81-%EC%A0%9C%EB%AA%A9-aaaa0001")
    src = client.get(f"/api/jobs/{jid}").json()["source"]
    assert src["no"] == "aaaa0001" and src["url"] == "https://www.teamblind.com/kr/post/aaaa0001"


def test_duplicate_check_is_per_site(env):
    """같은 글 번호라도 디시 글과 블라인드 글은 다른 글 — 번호가 겹친다고 막지 않는다."""
    client, app, deps = env
    new_job(client, app)                                      # 디시 baseball_new13 / 2101
    r = client.post("/api/jobs", json={"url": "https://www.teamblind.com/kr/post/2101"})
    assert r.status_code == 200, r.text
    blind_id = r.json()["id"]
    idle(app)
    r = client.post("/api/jobs", json={"url": "https://www.teamblind.com/kr/post/2101"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate"
    assert r.json()["detail"]["count"] == 1
    assert r.json()["detail"]["jobs"][0]["id"] == blind_id


def test_create_blind_job_with_gallery_no(env):
    """인기글 목록에서 고를 때처럼 gallery="blind" + 별칭으로도 만든다(글 번호는 문자열)."""
    client, app, _ = env
    jid = new_job(client, app, gallery="blind", no="bbbb0002")
    src = client.get(f"/api/jobs/{jid}").json()["source"]
    assert src["site"] == "blind" and src["no"] == "bbbb0002"
    assert src["url"].endswith("/kr/post/bbbb0002")


def test_create_job_rejects_unknown_host(env):
    client, _, _ = env
    r = client.post("/api/jobs", json={"url": "https://example.com/kr/post/aaaa0001"})
    assert r.status_code == 400 and "디시인사이드·블라인드" in r.json()["detail"]


def test_old_job_without_site_opens_as_dcinside(tmp_path):
    """site 가 없던 때 저장한 state.json 도 그대로 열린다(디시 작업으로 읽는다) — 기존 work/ 폴더 호환."""
    st = {"id": "20261020-120000_baseball_new13_2101", "title": "예전 작업",
          "source": {"gallery": "baseball_new13", "no": 2101,
                     "url": "https://gall.dcinside.com/board/view/?id=baseball_new13&no=2101", "title": "예전 작업"},
          "created_at": "2026-10-20T12:00:00+09:00", "status": "waiting", "progress": 0.2, "options": {},
          "steps": [{"key": k, "label": k, "status": "done" if k == "source" else "todo", "progress": 0.0,
                     "detail": "", "sub": [], "started_at": None, "ended_at": None, "error": None}
                    for k in ("source", "read", "script", "voice", "assets", "scenes", "render", "review")],
          "artifacts": {}, "log": []}
    d = tmp_path / st["id"]
    d.mkdir()
    (d / "state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    app = create_app(work_root=tmp_path, deps=FakeDeps())
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{st['id']}")
        assert r.status_code == 200
        assert r.json()["source"]["site"] == "dcinside"       # 화면(JobHeader.sourceLabel)이 디시로 읽는 것과 같은 규칙
        assert [j["id"] for j in client.get("/api/jobs").json()] == [st["id"]]


# ---------------------------------------------------------------- AI 키 없이 만들기 막기(공급자별 경계)
def test_no_key_blocks_create_with_readiness_reason(tmp_path, monkeypatch):
    """키가 하나도 없으면 만들기를 409 no_key 로 막는다. 문구는 설정 준비 상태의 이유(Gemini 또는 OpenRouter)다."""
    from recap_shorts import settings
    monkeypatch.setattr(settings, "get_key", lambda name: "")
    real = create_app(work_root=tmp_path / "real", deps=P.Deps(), start_runner=False)
    with TestClient(real) as client:
        r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101})
        assert r.status_code == 409
        assert r.json()["detail"] == {"code": "no_key", "message": "Gemini 또는 OpenRouter API 키가 필요합니다"}
        assert not list((tmp_path / "real").glob("*/state.json"))     # job 폴더도 만들지 않는다


def test_gemini_key_alone_allows_create(tmp_path):
    from recap_shorts import settings
    settings.save({"gemini_api_key": "test-gemini-key-0000000000"})
    real = create_app(work_root=tmp_path / "real", deps=P.Deps(), start_runner=False)
    with TestClient(real) as client:
        assert client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101}).status_code == 200


def test_openrouter_key_alone_allows_create(tmp_path, monkeypatch):
    from recap_shorts import modelfiles, settings
    monkeypatch.setattr(modelfiles, "ok", lambda mid: True)            # 기본 목소리(로컬)의 모델 파일
    settings.save({"openrouter_api_key": "sk-or-test-0000000000000000"})
    real = create_app(work_root=tmp_path / "real", deps=P.Deps(), start_runner=False)
    with TestClient(real) as client:
        assert client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101}).status_code == 200


def test_fake_apps_are_never_blocked(tmp_path):
    """가짜 부품(FakeDeps)으로 띄운 앱은 키를 보지 않고 막지 않는다."""
    fake = create_app(work_root=tmp_path / "fake", deps=FakeDeps())
    with TestClient(fake) as client:
        r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101})
        assert r.status_code == 200


def test_no_gemini_key_blocks_gemini_steps_only(tmp_path, monkeypatch):
    """continue·rerun 이 Gemini 단계로 넘어갈 때만 막는다(다른 단계는 키 검사에 걸리지 않는다)."""
    from recap_shorts import settings
    work = tmp_path / "work"
    app = create_app(work_root=work, deps=FakeDeps())
    with TestClient(app) as c:
        jid = new_job(c, app)
        assert step(c.get(f"/api/jobs/{jid}").json(), "read")["status"] == "waiting"
    monkeypatch.setattr(settings, "get_key", lambda name: "")
    real = create_app(work_root=work, deps=P.Deps(), start_runner=False)
    with TestClient(real) as c:
        r = c.post(f"/api/jobs/{jid}/continue")            # 다음은 자료 읽기(Gemini 또는 OpenRouter)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "no_key"
        assert r.json()["detail"]["message"] == "Gemini 또는 OpenRouter API 키가 필요합니다"
        r = c.post(f"/api/jobs/{jid}/rerun", json={"from": "assets"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "no_key"
        r = c.post(f"/api/jobs/{jid}/rerun", json={"from": "render"})     # Gemini 단계가 아니라 키 때문에 막히지는 않는다
        assert r.status_code == 409 and "앞 단계" in r.json()["detail"]


def test_no_gemini_key_topics_show_list_without_scoring(tmp_path, monkeypatch):
    """키가 없으면 주제 목록은 그대로 보여 주고 맞춤 점수만 건너뛴다 — 화면에 RuntimeError 가 나가지 않게.
    진짜 Deps 앱이라야 키 검사가 돈다(FakeDeps 앱은 일부러 통과시킨다)."""
    from recap_shorts import settings
    from recap_shorts.collect import dcbest, topicfit
    monkeypatch.setattr(settings, "get_key", lambda name: "")
    monkeypatch.setattr(dcbest, "fetch_best", lambda *a, **k: [
        dcbest.BestItem(1008, "[가상] 점심값", "싱갤", 100, 5, 2, "2026-11-02T07:00:00+09:00", 5.0, "u")])
    monkeypatch.setattr(topicfit, "resolve", lambda cfg: topicfit.Topic("daily_drama", "일상 논란·썰", "설명", "k"))
    app = create_app(work_root=tmp_path, deps=P.Deps(), start_runner=False)
    app.state.deps.clef_triage = lambda posts: None          # Clef 도 밖으로 나가지 않게
    with TestClient(app) as client:
        r = client.get("/api/topics?source=dcbest&sort=views&hours=48&pages=1").json()
    assert r["fit"] == "unavailable" and r["fit_note"] == "키가 없어 맞춤 점수는 건너뜀"
    assert r["channel"] == {"main_topic": "daily_drama", "label": "일상 논란·썰"}
    assert len(r["items"]) == 1 and "fit" not in r["items"][0]
    assert "RuntimeError" not in json.dumps(r, ensure_ascii=False)
