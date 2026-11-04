"""사실 확인 흐름 시험(설계 11.4·11.5): 읽기 하위 단계, 멈춤 규칙, 입구별 막기, 다시 확인·직접 확인, 대본 검수, 뉴스 카드.
가짜 부품(FakeDeps)만 쓴다 — 네트워크는 없다. 다시 확인·직접 확인·넘기기는 이 PC 에서만 되므로 기준 주소는 127.0.0.1 이다."""
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recap_shorts import factcheck as F
from recap_shorts import pipeline as P
from recap_shorts.app import create_app
from recap_shorts.capcut import desktop
from tests.pipeline.fakes import DRAFT, FC_CLAIM, FC_FACT, FC_SOURCE, FC_URL, FakeDeps
from tests.pipeline.test_pipeline import SRC, go, make, statuses

BASE = "http://127.0.0.1:8020"
STATUSES = ("skipped", "pass", "warn", "stop", "failed", "stale", "missing")
CLEAR = ("skipped", "pass")
EXPECT = {"skipped": "clear", "pass": "clear", "warn": "need_ack", "stop": "blocked", "failed": "blocked",
          "stale": "blocked", "missing": "blocked"}
REASON = {
    "warn": "주의 판정을 확인하고 계속해 주세요.",
    "stop": "사실 아님 판정이 있어 계속할 수 없습니다. 고치거나 직접 확인해 주세요.",
    "failed": "사실 확인 분류에 실패했습니다 — 시험",
    "stale": "글이 바뀌어 사실 확인을 다시 해야 합니다.",
    "missing": "사실 확인 결과가 없습니다. 다시 확인해 주세요.",
}
OTHER_SOURCE = {"url": "https://www.hankyung.example.kr/news/article/2026/11/03/abc", "title": "", "outlet": "한국일보",
                "date": None}


# ---------------------------------------------------------------- 가짜 상태 만들기
def deps_for(status: str) -> FakeDeps:
    """읽기 단계가 이 상태의 기록을 남기도록 가짜를 맞춘다. stale·missing 은 통과 상태로 읽은 뒤 깨뜨린다."""
    d = FakeDeps()
    d.fc_kind = "general" if status == "skipped" else "info"
    if status == "warn":
        d.fc_verdicts = {"C1": {"verdict": "unverified", "why": "보도에 없음", "fact": None, "sources": []}}
    elif status == "stop":
        d.fc_verdicts = {"C1": {"verdict": "false", "why": "보도는 반대로 씀", "fact": None,
                                "sources": [dict(FC_SOURCE)]}}
    elif status == "failed":
        d.fc_classify_error = REASON["failed"]
    return d


def mixed_deps() -> FakeDeps:
    """하나는 보도로 확인되고 하나는 안 된 글 → 주의 판정이면서 뉴스 출처가 남는다."""
    d = FakeDeps()
    d.fc_kind = "info"
    d.fc_claims = [{**FC_CLAIM, "id": "C1"}, {**FC_CLAIM, "id": "C2", "claim": "다른 주장"}]
    d.fc_verdicts = {"C2": {"verdict": "unverified", "why": "보도에 없음", "fact": None, "sources": []}}
    return d


def three_sources_deps() -> FakeDeps:
    """주장 셋: 첫째·셋째는 같은 기사, 둘째는 제목 없는 다른 기사. 뉴스 카드는 기사 둘에 한 장씩."""
    d = FakeDeps()
    d.fc_kind = "info"
    d.fc_claims = [{**FC_CLAIM, "id": f"C{i}"} for i in (1, 2, 3)]
    d.fc_verdicts = {"C2": {"sources": [dict(OTHER_SOURCE)], "fact": "상대 팀은 열두 개 동 대표 선수였다"}}
    return d


@contextmanager
def app_client(root: Path, deps: FakeDeps):
    app = create_app(work_root=root, deps=deps)
    with TestClient(app, base_url=BASE) as c:
        yield app, c


def create(c, app, options=None):
    body = {"gallery": "baseball_new13", "no": 2101}
    if options is not None:
        body["options"] = options
    r = c.post("/api/jobs", json=body)
    assert r.status_code == 200, r.text
    assert app.state.runner.wait_idle(60)
    jid = r.json()["id"]
    return jid, app.state.store.get(jid)


def finish(c, app, job):
    """확인 대기를 모두 넘겨 끝까지 간다(자동 단계는 그대로 지나간다)."""
    for _ in range(5):
        if job.state["status"] == "done":
            return
        assert c.post(f"/api/jobs/{job.id}/continue").status_code == 200
        assert app.state.runner.wait_idle(60)
    assert job.state["status"] == "done"


def read_topic(job_dir: Path) -> dict:
    return json.loads((job_dir / "topic.json").read_text(encoding="utf-8"))


def fc_hash(job) -> str:
    t = read_topic(job.dir)
    return F.input_hash(*P.fc_input(t), topic=t.get("topic") or "")


def edit_topic(job_dir: Path) -> None:
    """글을 고친다(입력 해시가 바뀐다). topic.json 만 바꾼다."""
    t = read_topic(job_dir)
    t["source_text"] = "글쓴이가 한 줄 더 적었다.\n" + t["source_text"]
    (job_dir / "topic.json").write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")


def tamper(status: str, job_dir: Path) -> None:
    """읽기가 끝난 뒤 기록을 상태에 맞게 깨뜨린다: 글 고치기, 기록 지우기, 상태만 바꾸기."""
    if status == "stale":
        edit_topic(job_dir)
    elif status == "missing":
        (job_dir / F.RECORD_NAME).unlink()
    elif status in ("warn", "stop", "failed"):
        rec = F.load_record(job_dir)
        rec.update(status=status, ack=None, override=None, error=REASON["failed"] if status == "failed" else None)
        F.save_record(job_dir, rec)


def break_during_read(status: str, job, deps: FakeDeps, monkeypatch) -> None:
    """읽기가 도는 중에 깨뜨린다(자동 실행): 분류 중에 글을 고치거나, 기록 저장을 빠뜨린다."""
    if status == "stale":
        deps.fc_hook = lambda: edit_topic(job.dir)
    elif status == "missing":
        monkeypatch.setattr(F, "save_record", lambda job_dir, rec: None)


def strip_policy(job) -> None:
    """기능 이전 작업처럼 만든다: 정책 표시와 기록을 지운다."""
    job.state.pop("factcheck_policy", None)
    job.save()
    (job.dir / F.RECORD_NAME).unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def capcut_env(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts" / "com.lveditor.draft"
    drafts.mkdir(parents=True)
    (drafts / "root_meta_info.json").write_text(
        json.dumps({"all_draft_store": [], "draft_ids": 0, "root_path": str(drafts)}), encoding="utf-8")
    monkeypatch.setenv("RECAP_CAPCUT_DRAFTS_DIR", str(drafts))
    monkeypatch.setattr(desktop, "capcut_running", lambda: False)


# ---------------------------------------------------------------- 읽기 하위 단계
@pytest.mark.parametrize("status", ["skipped", "pass", "warn", "stop", "failed"])
def test_read_records_status_topic_additions_and_policy(tmp_path, status):
    deps = deps_for(status)
    store, runner, job = make(tmp_path, deps)
    go(runner, job)
    assert statuses(job)["read"] == "waiting" and job.state["status"] == "waiting"
    assert job.state["factcheck_policy"] == F.POLICY_VERSION
    rec = F.load_record(job.dir)
    assert rec["status"] == status and rec["input_hash"] == fc_hash(job)
    topic = read_topic(job.dir)
    assert topic["topic_auto"] == topic["topic"]
    assert topic["topic"] not in P.fc_input(topic)[1]
    sub = {s["label"]: s for s in job.step("read")["sub"]}["사실 확인"]
    assert sub["status"] == ("error" if status in ("stop", "failed") else "done")
    if status == "skipped":
        assert deps.calls.get("factcheck_verify") is None          # 일반 글은 판정을 부르지 않는다
        assert topic["facts"] == [] and topic["false_claims"] == [] and topic["unverified_claims"] == []
    if status == "pass":
        assert topic["facts"] == [{"id": "F1", "text": FC_FACT, "evidence": FC_FACT, "source": FC_URL, "claim_id": "C1"}]
        assert topic["false_claims"] == [] and topic["unverified_claims"] == []
    if status == "warn":
        assert topic["unverified_claims"] == [{"id": "C1", "claim": FC_CLAIM["claim"]}]
        assert topic["facts"] == [] and topic["false_claims"] == []
    if status == "stop":
        assert topic["false_claims"] == [{"id": "C1", "claim": FC_CLAIM["claim"], "source_quote": FC_CLAIM["source_quote"]}]
    if status == "failed":
        assert rec["error"] == REASON["failed"] and job.step("read")["error"] is None
        assert topic["facts"] == [] and topic["false_claims"] == [] and topic["unverified_claims"] == []


@pytest.mark.parametrize("site", ["dcinside", "blind"])
def test_read_records_exact_header_comments_and_keeps_fake_ocr_marker(tmp_path, site):
    deps = deps_for("stop")
    quote = "식약처가 모든 가공유 판매를 금지했다"
    ocr = "[게시글 1 조작 댓글 캡처]\n" + quote
    read_image = deps.read_image
    deps.read_image = lambda images, instruction: (
        read_image(images, instruction) if "댓글 목록" in instruction else {"kind": "기사", "text": ocr}
    )
    deps.fc_claims = [{**FC_CLAIM, "claim": quote, "source_quote": quote}]
    classify, verify = deps.factcheck_classify, deps.factcheck_verify
    inputs = []

    def classify_input(title, text, force):
        inputs.append((title, text))
        return classify(title, text, force)

    def verify_input(claims, *, title, source_text):
        inputs.append((title, source_text))
        return verify(claims, title=title, source_text=source_text)

    deps.factcheck_classify, deps.factcheck_verify = classify_input, verify_input
    store, runner, job = make(tmp_path, deps)
    job.state["source"]["site"] = site
    go(runner, job)
    topic = read_topic(job.dir)
    header, comments = topic["fc_exclude"]
    assert header == topic["source_text"].splitlines(keepends=True)[0]
    assert header.startswith("[게시글 1]") and " 게시글, " in header and " 작성\n" in header
    assert "제목:" not in header and "본문:" not in header
    assert comments in topic["source_text"] and "이건 좀 웃기네" in comments
    assert ocr not in comments
    title, text = P.fc_input(topic)
    assert header not in text
    assert title == topic["source_text"].split("제목: ", 1)[1].splitlines()[0]
    assert ocr in text and "이건 좀 웃기네" not in text
    assert topic["source_text"].replace(header, "", 1).replace(comments, "", 1) == text
    assert inputs == [(title, text), (title, text)]
    assert F.load_record(job.dir)["status"] == "stop"
    assert P.factcheck_status(job)["gate"] == "blocked"


def test_unexpected_error_in_check_is_recorded_and_read_completes(tmp_path):
    deps = FakeDeps()

    def boom():
        raise RuntimeError("시험용 예상 밖 오류")
    deps.fc_hook = boom
    store, runner, job = make(tmp_path, deps)
    go(runner, job)
    assert statuses(job)["read"] == "waiting" and job.step("read")["status"] != "error"
    rec = F.load_record(job.dir)
    assert rec["status"] == "failed" and "사실 확인 중 오류" in rec["error"]
    assert (job.dir / P.ERRORS_LOG).is_file()


# ---------------------------------------------------------------- 멈춤 규칙: 자동 실행·continue
@pytest.mark.parametrize("status", STATUSES)
def test_auto_run_stops_at_read_unless_clear(tmp_path, monkeypatch, status):
    deps = deps_for(status)
    store, runner, job = make(tmp_path, deps, {"auto_steps": ["read", "script"]})
    break_during_read(status, job, deps, monkeypatch)
    go(runner, job)
    if EXPECT[status] == "clear":
        st = statuses(job)
        assert st["read"] == "done" and st["script"] == "done" and st["scenes"] == "waiting"
    else:
        assert statuses(job)["read"] == "waiting" and statuses(job)["script"] == "todo"
        assert job.state["status"] == "waiting"


@pytest.mark.parametrize("status", STATUSES)
def test_continue_refused_when_blocked_and_acks_warn(tmp_path, status):
    with app_client(tmp_path / "w", deps_for(status)) as (app, c):
        jid, job = create(c, app)                                   # 읽기에서 확인을 기다린다
        assert statuses(job)["read"] == "waiting"
        tamper(status, job.dir)
        r = c.post(f"/api/jobs/{jid}/continue")
        if EXPECT[status] == "blocked":
            assert r.status_code == 409 and r.json()["detail"] == REASON[status]
            assert statuses(job)["read"] == "waiting" and job.state["status"] == "waiting"
            return
        assert r.status_code == 200, r.text
        assert app.state.runner.wait_idle(60)
        assert statuses(job)["script"] == "waiting"
        if EXPECT[status] == "need_ack":
            assert F.load_record(job.dir)["ack"]["input_hash"] == fc_hash(job)


# ---------------------------------------------------------------- 멈춤 규칙: rerun·render·capcut
@pytest.mark.parametrize("entry", ["rerun", "render", "capcut"])
@pytest.mark.parametrize("status", STATUSES)
def test_rerun_render_capcut_pass_only_when_clear(tmp_path, status, entry):
    with app_client(tmp_path / "w", deps_for("pass")) as (app, c):
        jid, job = create(c, app, {"auto_steps": ["read", "script"]})   # 장면 배치까지 통과 상태로 간다
        assert statuses(job)["scenes"] == "waiting"
        tamper(status, job.dir)
        if entry == "rerun":
            r = c.post(f"/api/jobs/{jid}/rerun", json={"from": "assets"})
        elif entry == "render":
            r = c.post(f"/api/jobs/{jid}/render")
        else:
            r = c.post(f"/api/jobs/{jid}/capcut")
        if status in CLEAR:
            assert r.status_code == 200, r.text
            assert app.state.runner.wait_idle(60)
            if entry == "render":
                assert statuses(job)["review"] == "waiting"
            if entry == "capcut":
                assert r.json()["draft_name"]
        else:
            assert r.status_code == 409 and r.json()["detail"] == REASON[status]
            assert statuses(job)["scenes"] == "waiting" and job.state["status"] == "waiting"


def test_legacy_job_passes_every_gate(tmp_path):
    """기능 이전에 만든 작업(정책 표시 없음)은 막지 않는다. 화면에도 사실 확인이 없다."""
    with app_client(tmp_path / "w", FakeDeps()) as (app, c):
        jid, job = create(c, app, {"auto_steps": ["read", "script"]})
        strip_policy(job)
        view = c.get(f"/api/jobs/{jid}").json()
        assert view["factcheck_policy"] is None and view["factcheck"] is None
        assert c.post(f"/api/jobs/{jid}/rerun", json={"from": "assets"}).status_code == 200
        assert app.state.runner.wait_idle(60)
        assert c.post(f"/api/jobs/{jid}/render").status_code == 200
        assert app.state.runner.wait_idle(60)
        assert c.post(f"/api/jobs/{jid}/capcut").status_code == 200


def test_legacy_job_continues_without_record(tmp_path):
    with app_client(tmp_path / "w", FakeDeps()) as (app, c):
        jid, job = create(c, app)                                   # 읽기에서 확인을 기다린다
        strip_policy(job)
        assert c.post(f"/api/jobs/{jid}/continue").status_code == 200
        assert app.state.runner.wait_idle(60)
        assert statuses(job)["script"] == "waiting"


# ---------------------------------------------------------------- 보기 (GET job)
def test_view_reports_policy_record_and_missing_form(tmp_path):
    with app_client(tmp_path / "w", deps_for("warn")) as (app, c):
        jid, job = create(c, app)
        v = c.get(f"/api/jobs/{jid}").json()
        assert v["factcheck_policy"] == F.POLICY_VERSION
        fcv = v["factcheck"]
        assert fcv["status"] == "warn" and fcv["gate"] == "need_ack" and fcv["gate_reason"] == REASON["warn"]
        assert fcv["stale"] is False and fcv["running"] is False and "input_hash" not in fcv
        (job.dir / F.RECORD_NAME).unlink()
        v = c.get(f"/api/jobs/{jid}").json()
        assert v["factcheck"] == {"status": "missing", "claims": [], "gate": "blocked",
                                  "gate_reason": REASON["missing"], "stale": False, "running": False}


def test_restart_clears_leftover_recheck_flag(tmp_path):
    store = P.JobStore(tmp_path)
    job = store.create(dict(SRC))
    job.state["factcheck_running"] = True
    job.save()
    again = P.JobStore(tmp_path).get(job.id)
    assert not again.state.get("factcheck_running")


# ---------------------------------------------------------------- 직접 확인(override)
def test_override_needs_a_note_and_a_stop_record_that_is_not_stale(tmp_path):
    with app_client(tmp_path / "w", deps_for("stop")) as (app, c):
        jid, job = create(c, app)
        url = f"/api/jobs/{jid}/factcheck/override"
        for body in ({}, {"note": ""}, {"note": "   "}, {"note": "가" * 501}):
            assert c.post(url, json=body).status_code == 400, body
        assert c.post(url, json={"note": "가" * 500}).status_code == 200
        edit_topic(job.dir)                                          # 글이 바뀌면 직접 확인으로 못 넘어간다
        assert c.post(url, json={"note": "확인함"}).status_code == 400


def test_override_refused_for_warn(tmp_path):
    with app_client(tmp_path / "w", deps_for("warn")) as (app, c):
        jid, job = create(c, app)
        assert c.post(f"/api/jobs/{jid}/factcheck/override", json={"note": "확인함"}).status_code == 400


@pytest.mark.parametrize("status", ["stop", "failed"])
def test_override_then_continue_passes(tmp_path, status):
    with app_client(tmp_path / "w", deps_for(status)) as (app, c):
        jid, job = create(c, app)
        r = c.post(f"/api/jobs/{jid}/factcheck/override", json={"note": "직접 확인함"})
        assert r.status_code == 200 and r.json()["factcheck"]["gate"] == "clear"
        rec = F.load_record(job.dir)
        assert rec["override"]["note"] == "직접 확인함" and rec["override"]["input_hash"] == fc_hash(job)
        assert c.post(f"/api/jobs/{jid}/continue").status_code == 200
        assert app.state.runner.wait_idle(60)
        assert statuses(job)["script"] == "waiting"


# ---------------------------------------------------------------- 다시 확인(recheck)
def test_recheck_refused_while_busy_or_before_read_finishes(tmp_path):
    deps = deps_for("pass")
    with app_client(tmp_path / "w", deps) as (app, c):
        jid, job = create(c, app)
        gate = threading.Event()
        deps.fc_hook = lambda: gate.wait(10)                         # 다시 확인이 분류에서 멈춰 있는 동안
        assert c.post(f"/api/jobs/{jid}/factcheck", json={}).status_code == 200
        assert c.post(f"/api/jobs/{jid}/factcheck", json={}).status_code == 409
        assert c.post(f"/api/jobs/{jid}/factcheck/override", json={"note": "확인함"}).status_code == 409
        assert c.post(f"/api/jobs/{jid}/continue").status_code == 409
        gate.set()
        assert app.state.runner.wait_idle(60)
        assert job.state["status"] == "waiting" and not job.state.get("factcheck_running")
    deps2 = FakeDeps()
    deps2.fail_once.add("get_post")                                  # 원본을 못 가져와 읽기가 끝나지 않은 작업
    with app_client(tmp_path / "w2", deps2) as (app, c):
        jid, job = create(c, app)
        assert job.step("read")["status"] == "todo"
        assert c.post(f"/api/jobs/{jid}/factcheck", json={}).status_code == 409


def test_recheck_rewrites_record_and_resets_later_steps(tmp_path):
    deps = deps_for("pass")
    with app_client(tmp_path / "w", deps) as (app, c):
        jid, job = create(c, app, {"auto_steps": ["read", "script"]})
        assert statuses(job)["scenes"] == "waiting" and (job.dir / "assets/news/card_01.png").is_file()
        deps.fc_verdicts = {"C1": {"verdict": "false", "why": "보도는 반대로 씀", "fact": None,
                                   "sources": [dict(FC_SOURCE)]}}
        assert c.post(f"/api/jobs/{jid}/factcheck", json={"force": False}).status_code == 200
        assert app.state.runner.wait_idle(60)
        st = statuses(job)
        assert st["read"] == "waiting" and st["script"] == "todo" and st["scenes"] == "todo"
        assert job.state["status"] == "waiting" and not job.state.get("factcheck_running")
        assert F.load_record(job.dir)["status"] == "stop"
        topic = read_topic(job.dir)
        assert topic["facts"] == [] and [x["id"] for x in topic["false_claims"]] == ["C1"]
        assert not (job.dir / "assets/news").exists()
        ents = json.loads((job.dir / "assets/assets.json").read_text(encoding="utf-8"))
        assert not any(e.get("role") == "news" for e in ents)


def test_recheck_with_force_checks_a_general_post(tmp_path):
    with app_client(tmp_path / "w", deps_for("skipped")) as (app, c):
        jid, job = create(c, app)
        assert F.load_record(job.dir)["status"] == "skipped"
        assert c.post(f"/api/jobs/{jid}/factcheck", json={"force": True}).status_code == 200
        assert app.state.runner.wait_idle(60)
        rec = F.load_record(job.dir)
        assert rec["status"] == "pass" and rec["forced"] is True and rec["claims"]


@pytest.mark.parametrize("edit", ["none", "body", "comments"])
def test_recheck_view_stale_for_body_or_changed_recorded_comments(tmp_path, edit):
    with app_client(tmp_path / "w", deps_for("pass")) as (app, c):
        jid, job = create(c, app)
        topic = read_topic(job.dir)
        comments = next(block for block in topic["fc_exclude"] if "댓글 캡처" in block)
        assert comments in topic["source_text"] and comments not in P.fc_input(topic)[1]
        assert c.post(f"/api/jobs/{jid}/factcheck", json={}).status_code == 200
        assert app.state.runner.wait_idle(60)
        topic = read_topic(job.dir)
        if edit == "comments":
            changed = comments + "\n- 새 반응 100만원"
            # 전체 구간의 원래 문자열이 더는 남지 않게 기존 댓글도 바꾼다.
            changed = changed.replace("이건 좀 웃기네", "사람이 고친 댓글")
            topic["source_text"] = topic["source_text"].replace(comments, changed)
            assert changed in P.fc_input(topic)[1]
        F.atomic_json(job.dir / "topic.json", topic)
        if edit == "body":
            edit_topic(job.dir)
        view = c.get(f"/api/jobs/{jid}").json()["factcheck"]
        assert view["stale"] is (edit != "none")
        assert view["gate"] == ("clear" if edit == "none" else "blocked")
        if edit != "none":
            assert view["gate_reason"] == REASON["stale"]


# ---------------------------------------------------------------- 대본 뒤 검수
def test_script_check_deletes_false_sentence_and_waits_even_in_auto(tmp_path):
    deps = deps_for("stop")
    deps.fc_script_flags = [{"field": "sentence", "index": 1, "claim_id": "C1", "verdict": "false",
                             "text": "반면 상대 팀은 열두 개 동 대표 선수들이었잖아."}]
    with app_client(tmp_path / "w", deps) as (app, c):
        jid, job = create(c, app, {"auto_steps": ["read", "script"]})
        assert statuses(job)["read"] == "waiting"                    # 사실 아님 → 자동이어도 읽기에서 멈춤
        assert c.post(f"/api/jobs/{jid}/factcheck/override", json={"note": "직접 확인함"}).status_code == 200
        assert c.post(f"/api/jobs/{jid}/continue").status_code == 200
        assert app.state.runner.wait_idle(60)
        assert statuses(job)["script"] == "waiting" and job.state["status"] == "waiting"
        chk = json.loads((job.dir / "script_final/script_check.json").read_text(encoding="utf-8"))
        assert [f["verdict"] for f in chk["factcheck_flags"]] == ["false"]
        assert chk["deleted"][-1]["index"] == 1 and "사실 확인" in chk["deleted"][-1]["reason"]
        assert any(x.startswith("사실 확인: ") for x in chk["flags"])
        # 가드가 C1 의 source_quote 와 같은 근거를 쓴 문장 0 을 먼저 지우고, 대본 뒤 검사가 문장 1 을 지운다(두 방어 모두 동작).
        assert any("사실 아님 주장(C1)" in x for x in chk["flags"])
        script = json.loads((job.dir / "script_final/script.json").read_text(encoding="utf-8"))
        assert len(script["sentences"]) == len(DRAFT["sentences"]) - 2
        assert chk["deleted"][-1]["text"] not in [s["text"] for s in script["sentences"]]
        assert DRAFT["sentences"][0]["text"] not in [s["text"] for s in script["sentences"]]


def test_script_check_flags_unverified_without_deleting(tmp_path):
    deps = deps_for("warn")
    deps.fc_script_flags = [{"field": "hook", "index": None, "claim_id": "C1", "verdict": "unverified",
                             "text": DRAFT["hook"]}]
    with app_client(tmp_path / "w", deps) as (app, c):
        jid, job = create(c, app, {"auto_steps": ["read", "script"]})
        assert c.post(f"/api/jobs/{jid}/continue").status_code == 200   # 주의 확인(ack) 뒤 대본으로
        assert app.state.runner.wait_idle(60)
        assert statuses(job)["script"] == "waiting" and job.state["status"] == "waiting"
        chk = json.loads((job.dir / "script_final/script_check.json").read_text(encoding="utf-8"))
        assert [f["verdict"] for f in chk["factcheck_flags"]] == ["unverified"]
        assert not any("사실 확인" in d.get("reason", "") for d in chk["deleted"])
        script = json.loads((job.dir / "script_final/script.json").read_text(encoding="utf-8"))
        assert len(script["sentences"]) == len(DRAFT["sentences"])


def test_script_check_error_flags_a_human_without_failing_the_step(tmp_path):
    deps = deps_for("warn")
    deps.fc_script_error = "사실 확인 대본 검수에 실패했습니다 — 시험"
    with app_client(tmp_path / "w", deps) as (app, c):
        jid, job = create(c, app, {"auto_steps": ["read", "script"]})
        assert c.post(f"/api/jobs/{jid}/continue").status_code == 200
        assert app.state.runner.wait_idle(60)
        assert statuses(job)["script"] == "waiting" and job.step("script")["error"] is None
        chk = json.loads((job.dir / "script_final/script_check.json").read_text(encoding="utf-8"))
        assert chk["factcheck_flags"] == [{"field": None, "index": None, "claim_id": None, "verdict": "error",
                                           "text": "사실 확인 대본 검수에 실패했습니다 — 시험"}]


def test_script_check_not_called_without_false_or_unverified(tmp_path):
    deps = deps_for("pass")
    with app_client(tmp_path / "w", deps) as (app, c):
        create(c, app, {"auto_steps": ["read", "script"]})
        assert deps.calls.get("factcheck_script_check") is None


# ---------------------------------------------------------------- 뉴스 카드·문장 근거
@pytest.mark.parametrize("url,suffix,outlet", [
    ("https://www.mt.co.kr/a", " - 머니투데이", "머니투데이"),
    ("https://starnewskorea.com/a", " | 스타뉴스", "스타뉴스"),
    ("https://unknown.example.com/a", " :: 공감언론 뉴시스 ::", "공감언론 뉴시스"),
])
def test_news_cards_use_normalized_source_title_and_outlet(tmp_path, url, suffix, outlet):
    deps = deps_for("pass")
    title = "표시 제도 개선 검토"
    sources = F._sources([url], {url: title + suffix})
    deps.fc_verdicts = {"C1": {"sources": sources}}
    store, runner, job = make(tmp_path, deps, {"auto_steps": ["read", "script"]})
    go(runner, job)
    assert [(card["title"], card["outlet"]) for card in deps.news_cards] == [(title, outlet)]
    assert F.description_notice(F.load_record(job.dir)) == (F.PASS_NOTE, [f"{outlet} · {title} · {url}"])


def test_news_cards_one_per_source_up_to_two(tmp_path):
    deps = three_sources_deps()
    other_fact = "분석된 176개 제품 가운데 27개가 원유 0%였다"
    deps.fc_verdicts["C3"] = {"fact": other_fact}
    store, runner, job = make(tmp_path, deps, {"auto_steps": ["read", "script"]})
    go(runner, job)
    ents = [e for e in json.loads((job.dir / "assets/assets.json").read_text(encoding="utf-8")) if e.get("role") == "news"]
    assert [e["file"] for e in ents] == ["assets/news/card_01.png", "assets/news/card_02.png"]
    assert all((job.dir / e["file"]).is_file() for e in ents)
    assert ents[0] == {"file": "assets/news/card_01.png", "kind": "cap", "role": "news", "status": "ok",
                       "fact_id": "F1", "fact_text": FC_FACT, "source_url": FC_URL, "outlet": "조선일보",
                       "fact_ids": ["F1", "F3"], "fact_texts": [FC_FACT, other_fact],
                       "title": FC_SOURCE["title"], "date": "2026-11-02"}
    assert ents[1]["source_url"] == OTHER_SOURCE["url"] and ents[1]["fact_id"] == "F2"
    assert ents[1]["fact_ids"] == ["F2"] and ents[1]["fact_texts"] == ["상대 팀은 열두 개 동 대표 선수였다"]
    assert ents[1]["title"] == "한국일보" and ents[1]["date"] is None          # 제목이 없으면 언론사 이름
    assert [c["title"] for c in deps.news_cards] == [FC_SOURCE["title"], "한국일보"]
    assert {x["label"]: x for x in job.step("assets")["sub"]}["뉴스 카드"]["status"] == "done"


def test_news_card_draw_failure_skips_only_that_card(tmp_path):
    deps = three_sources_deps()
    deps.fc_draw_fail = {FC_SOURCE["title"]}
    store, runner, job = make(tmp_path, deps, {"auto_steps": ["read", "script"]})
    go(runner, job)
    ents = [e for e in json.loads((job.dir / "assets/assets.json").read_text(encoding="utf-8")) if e.get("role") == "news"]
    assert [e["source_url"] for e in ents] == [OTHER_SOURCE["url"]]
    assert ents[0]["file"] == "assets/news/card_01.png"
    assert any("뉴스 카드 그리기 실패" in e["msg"] for e in job.state["log"])
    assert statuses(job)["scenes"] == "waiting"                    # 단계는 실패하지 않는다


def test_sentence_infos_carry_body_evidence():
    script = {"sentences": [{"text": "첫 문장", "evidence": "원문 구절", "image_hint": "사무실"},
                            {"text": "둘째 문장", "evidence": None, "image_hint": "운동장"}]}
    timings = {"sentences": [{"index": 0, "role": "hook", "text": "훅"}, {"index": 1, "role": "body", "text": "첫 문장"},
                             {"index": 2, "role": "body", "text": "둘째 문장"}, {"index": 3, "role": "closing", "text": "끝"}]}
    assert [i["evidence"] for i in P.sentence_infos(script, timings)] == ["", "원문 구절", "", ""]


# ---------------------------------------------------------------- 설명 문구
def finish_and_read_description(tmp_path, deps):
    orig = deps.read_image                                       # 첨부를 팬 제작물이 아닌 것으로 읽게 한다(고지 문구가 팬 고지로 바뀌지 않게)

    def plain(images, instruction):
        r = orig(images, instruction)
        return {**r, "fan_made": False} if isinstance(r, dict) and "kind" in r else r
    deps.read_image = plain
    with app_client(tmp_path / "w", deps) as (app, c):
        jid, job = create(c, app, {"auto_steps": ["read", "script", "scenes", "review"]})
        finish(c, app, job)
        assert job.state["status"] == "done"
        return (job.dir / "out/description.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("label, deps, note, news", [
    ("skipped", deps_for("skipped"), F.DEFAULT_NOTE, False),
    ("pass", deps_for("pass"), F.PASS_NOTE, True),
    ("warn", mixed_deps(), F.CAUTION_NOTE, True),
])
def test_description_notice_follows_status(tmp_path, label, deps, note, news):
    desc = finish_and_read_description(tmp_path / label, deps)
    assert note in desc
    if news:
        assert "[뉴스 출처]\n" + f"- 조선일보 · {FC_SOURCE['title']} · {FC_URL}" in desc
    else:
        assert "[뉴스 출처]" not in desc
    if label == "pass":
        assert F.CAUTION_NOTE not in desc


# ---------------------------------------------------------------- 다시 확인 뒤 뉴스 카드 흔적
def test_drop_news_cards_only_removes_news_entries(tmp_path):
    job = P.JobStore(tmp_path).create(dict(SRC))
    (job.dir / "assets").mkdir(parents=True)
    (job.dir / "assets/assets.json").write_text(json.dumps(
        [{"file": "assets/cap/a.png", "kind": "cap"}, {"file": "assets/news/card_01.png", "kind": "cap", "role": "news"}],
        ensure_ascii=False), encoding="utf-8")
    (job.dir / "assets/news").mkdir()
    (job.dir / "assets/news/card_01.png").write_bytes(b"x")
    P._drop_news_cards(job)
    assert json.loads((job.dir / "assets/assets.json").read_text(encoding="utf-8")) == [{"file": "assets/cap/a.png", "kind": "cap"}]
    assert not (job.dir / "assets/news").exists()
