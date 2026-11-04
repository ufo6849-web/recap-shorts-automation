"""작업 응답·다듬기 회귀 시험. TestClient 없이 실제 엔드포인트와 작업 큐를 부른다."""
import copy
import json

import pytest

from recap_shorts import factcheck as F, pipeline as P
from recap_shorts.app import create_app
from tests.pipeline.fakes import DRAFT, READ_TEXT, REFINED_TEXTS, FakeDeps
from tests.pipeline.test_pipeline import SRC


def endpoint(app, path, method="GET"):
    return next(r.endpoint for r in app.routes
                if getattr(r, "path", "") == path and method in r.methods)


@pytest.fixture
def ready(tmp_path):
    deps = FakeDeps()
    deps.fc_kind = "info"
    app = create_app(work_root=tmp_path, deps=deps, start_runner=False)
    job = app.state.store.create(dict(SRC), {"script_llm": "agy", "auto_steps": ["read", "script"]})
    F.atomic_json(job.dir / "topic.json", {"topic": "동네 축구 결승전", "source_text": READ_TEXT})
    P.run_factcheck(job, deps)
    for key in P.STEP_KEYS:
        job.update_step(key, status="done")
    job.set_status("waiting")
    return app, job, deps


@pytest.mark.parametrize("case,status,note", [
    ("override", "done", "직접 확인으로 넘김"),
    ("failed_override", "done", "직접 확인으로 넘김"),
    ("ack", "done", "주의 확인하고 넘김"),
    ("stale_override", "error", "글이 바뀌어 다시 확인해야 합니다"),
    ("stale_ack", "error", "글이 바뀌어 다시 확인해야 합니다"),
    ("missing", "error", "글이 바뀌어 다시 확인해야 합니다"),
    ("running", "running", "확인 중"),
    ("running_stale", "running", "확인 중"),
    ("initial_running", "running", "확인 중"),
    ("invalid_override", "error", "사실 아님 판정 — 고치거나 직접 확인해 주세요"),
    ("invalid_ack", "done", "보도로 확인되지 않은 주장이 있음 — 확인해 주세요"),
    ("stop", "error", "사실 아님 판정 — 고치거나 직접 확인해 주세요"),
    ("warn", "done", "보도로 확인되지 않은 주장이 있음 — 확인해 주세요"),
    ("failed", "error", "시험용 확인 실패"),
    ("pass", "done", "보도로 확인됨"),
    ("skipped", "done", "일반 글이라 건너뜀"),
    ("legacy", "error", "저장된 옛 문구"),
])
def test_read_factcheck_sub_follows_current_gate_without_saving(ready, case, status, note):
    app, job, _ = ready
    rec = F.load_record(job.dir)
    rec["status"] = ("warn" if "ack" in case or case == "warn" else
                     "failed" if "failed" in case else case if case in ("pass", "skipped") else "stop")
    rec["error"] = "시험용 확인 실패" if rec["status"] == "failed" else None
    for key in ("override", "ack"):
        if key in case:
            rec[key] = {"at": P.now(), "note": "직접 대조함",
                        "input_hash": "다른 글" if case.startswith("invalid") else rec["input_hash"]}
    F.save_record(job.dir, rec)
    job.set_sub("read", "사실 확인", "error", "저장된 옛 문구")
    job.set_sub("read", "다른 하위 줄", "done", "그대로")
    if "stale" in case:
        F.atomic_json(job.dir / "topic.json", {"topic": "수정한 글", "source_text": READ_TEXT})
    if case in ("missing", "initial_running"):
        (job.dir / F.RECORD_NAME).unlink()
    if case.startswith("running"):
        job.state["factcheck_running"] = True
    if case == "initial_running":
        job.update_step("read", status="running")
        job.set_sub("read", "사실 확인", "running")
    if case == "legacy":
        job.state.pop("factcheck_policy")
        job.state["factcheck_running"] = True
    job.save()
    before = copy.deepcopy(job.state)
    stored = (job.dir / "state.json").read_bytes()
    gate = P.factcheck_status(job)["gate"]
    detail = endpoint(app, "/api/jobs/{jid}")(job.id)
    listing = endpoint(app, "/api/jobs")()[0]
    for result in (detail, listing):
        read = next(s for s in result["steps"] if s["key"] == "read")
        sub = next(s for s in read["sub"] if s["label"] == "사실 확인")
        assert (sub["status"], sub["note"]) == (status, note)
        assert next(s for s in read["sub"] if s["label"] == "다른 하위 줄")["note"] == "그대로"
        assert read["status"] == before["steps"][1]["status"]
        if result["factcheck"] is not None:
            assert result["factcheck"]["gate"] == gate
    assert job.state == before
    assert (job.dir / "state.json").read_bytes() == stored


@pytest.mark.parametrize("verdict", ["false", "unverified", "true", "missing"])
def test_refine_runs_script_factcheck_and_waits_even_when_auto(ready, verdict):
    app, job, deps = ready
    deps.fc_verdicts = {"C1": {"verdict": verdict}}
    if verdict == "missing":
        (job.dir / F.RECORD_NAME).unlink()
    else:
        P.run_factcheck(job, deps)
    deps.fc_script_flags = [{"field": "sentence", "index": 0, "claim_id": "C1",
                             "verdict": verdict, "text": REFINED_TEXTS[0]}]
    d = job.dir / "script_final"
    d.mkdir()
    draft = copy.deepcopy(DRAFT)
    # 다른 원문 구절을 근거로 잘못 붙여 규칙 가드만으로는 걸러지지 않는 주장.
    draft["sentences"][0]["evidence"] = draft["sentences"][1]["evidence"]
    F.atomic_json(d / "script.json", draft)
    runner = app.state.runner
    runner.start()
    try:
        result = endpoint(app, "/api/jobs/{jid}/script/refine", "POST")(job.id)
        assert result == {"id": job.id, "status": "running"}
        assert runner.wait_idle(5)
    finally:
        runner.stop()
        runner.thread.join(timeout=5)
    script = json.loads((d / "script.json").read_text())
    check = json.loads((d / "script_check.json").read_text())
    assert check["refine"]["applied"] is True
    assert job.state["status"] == job.step("script")["status"] == "waiting"
    assert job.step("voice")["status"] == "todo"
    assert not deps.calls.get("make_voice")
    if verdict in ("false", "unverified"):
        assert deps.calls["factcheck_script_check"] == 1
        assert check["factcheck_flags"] == deps.fc_script_flags
        assert check["ok"] is False
        assert json.loads((job.dir / "script/script.json").read_text()) == script
        assert json.loads((job.dir / "script/script_check.json").read_text()) == check
        assert (REFINED_TEXTS[0] in [s["text"] for s in script["sentences"]]) == (verdict == "unverified")
        if verdict == "false":
            assert any(x["text"] == REFINED_TEXTS[0] for x in check["deleted"])
    else:
        assert not deps.calls.get("factcheck_script_check")
        assert not check.get("factcheck_flags")
