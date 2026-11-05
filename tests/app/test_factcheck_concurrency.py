"""API 작업 예약 경합 회귀 시험. 샌드박스 밖에서 TestClient로 실행한다."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from recap_shorts import factcheck as F, pipeline as P
from recap_shorts.app import create_app
from recap_shorts.capcut import export as ce
from tests.pipeline.fakes import FakeDeps
from tests.pipeline.test_pipeline import SRC


@pytest.fixture
def ready(tmp_path):
    deps = FakeDeps()
    deps.fc_kind = "info"
    app = create_app(work_root=tmp_path, deps=deps, start_runner=False)
    job = app.state.store.create(dict(SRC))
    F.atomic_json(job.dir / "topic.json", {"topic": "제목", "source_text": "본문"})
    P.run_factcheck(job, deps)
    for key in P.STEP_KEYS:
        job.update_step(key, status="done")
    job.set_status("done")
    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8020") as client:
        yield app, client, job


@pytest.mark.parametrize("endpoint,body", [("capcut", None), ("render", None), ("rerun", {"from": "assets"})])
def test_rechecking_rejects_exports_and_reruns(ready, monkeypatch, endpoint, body):
    app, client, job = ready
    monkeypatch.setattr(ce, "export_job", lambda *a, **kw: pytest.fail("내보내면 안 됨"))
    # status가 갱신되기 전이어도 명시적인 재확인 예약 표시로 막는다.
    job.state["factcheck_running"] = True
    r = client.post(f"/api/jobs/{job.id}/{endpoint}", json=body)
    assert r.status_code == 409 and "사실 확인 중" in r.json()["detail"]
    assert app.state.runner.q.empty()


def test_queued_recheck_rejects_capcut(ready, monkeypatch):
    app, client, job = ready
    monkeypatch.setattr(ce, "export_job", lambda *a, **kw: pytest.fail("내보내면 안 됨"))
    assert client.post(f"/api/jobs/{job.id}/factcheck", json={}).status_code == 200
    assert job.state["factcheck_running"]
    assert client.post(f"/api/jobs/{job.id}/capcut").status_code == 409


def test_capcut_in_progress_rejects_recheck_and_releases_on_failure(ready, monkeypatch):
    app, client, job = ready
    entered, release = threading.Event(), threading.Event()
    def export(*a, **kw):
        entered.set()
        assert release.wait(5)
        raise FileNotFoundError("시험용 파일 없음")
    monkeypatch.setattr(ce, "export_job", export)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, f"/api/jobs/{job.id}/capcut")
        try:
            assert entered.wait(5)
            assert job.capcut_busy
            r = client.post(f"/api/jobs/{job.id}/factcheck", json={})
            assert r.status_code == 409
            assert not job.state.get("factcheck_running")
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 409
    assert not job.capcut_busy
    assert client.post(f"/api/jobs/{job.id}/factcheck", json={}).status_code == 200


@pytest.mark.parametrize("endpoint,body", [("capcut", None), ("render", None), ("rerun", {"from": "assets"})])
def test_gate_and_reservation_hold_same_job_lock(ready, monkeypatch, endpoint, body):
    app, client, job = ready
    original = P.factcheck_status
    checked, release, recheck_started = threading.Event(), threading.Event(), threading.Event()
    def gate(j):
        result = original(j)
        if not checked.is_set():
            checked.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(P, "factcheck_status", gate)
    exported, finish_export = threading.Event(), threading.Event()
    def export(*a, **kw):
        exported.set()
        assert finish_export.wait(5)
        return {"draft_name": "시험", "registered": False}
    monkeypatch.setattr(ce, "export_job", export)
    def recheck():
        acquired = job.lock.acquire(blocking=False)
        if acquired:
            job.lock.release()
        recheck_started.set()
        assert not acquired, "gate 판독과 예약 사이에 잠금이 풀림"
        try:
            P.recheck_factcheck(app.state.runner, job)
        except P.Busy:
            return "busy"
        return "accepted"
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(client.post, f"/api/jobs/{job.id}/{endpoint}", json=body)
        try:
            assert checked.wait(5)
            competing = pool.submit(recheck)
            assert recheck_started.wait(5)
            # gate 판독 직후에도 잠금이 있어 재확인은 예약할 수 없다.
            assert not competing.done()
            release.set()
            if endpoint == "capcut":
                assert exported.wait(5)
            assert competing.result(timeout=5) == "busy"
        finally:
            release.set()
            finish_export.set()
        assert request.result(timeout=5).status_code == 200
    assert not job.capcut_busy
