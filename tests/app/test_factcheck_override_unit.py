"""직접 확인 엔드포인트의 잠금 경합 시험. TestClient 없이 함수를 직접 호출한다."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from recap_shorts import factcheck as F, pipeline as P
from recap_shorts.app import create_app
from tests.pipeline.fakes import FakeDeps
from tests.pipeline.test_pipeline import SRC


@pytest.fixture
def override_ready(tmp_path):
    deps = FakeDeps()
    deps.fc_kind = "info"
    deps.fc_verdicts = {"C1": {"verdict": "false"}}
    app = create_app(work_root=tmp_path, deps=deps, start_runner=False)
    job = app.state.store.create(dict(SRC))
    F.atomic_json(job.dir / "topic.json", {"topic": "제목", "source_text": "본문"})
    P.run_factcheck(job, deps)
    job.update_step("read", status="done")
    job.set_status("waiting")
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/jobs/{jid}/factcheck/override")
    return app, job, endpoint


@pytest.mark.parametrize("busy", ["recheck", "factcheck_running", "capcut_busy", "running"])
def test_override_rechecks_busy_state_after_acquiring_lock(override_ready, busy):
    app, job, endpoint = override_ready
    before = F.load_record(job.dir)
    note_read = threading.Event()

    class SignallingNote(str):
        def strip(self):
            note_read.set()
            return super().strip()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with job.lock:
            pending = pool.submit(endpoint, job.id, {"note": SignallingNote("직접 확인한 근거")})
            assert note_read.wait(5)
            # 이전 코드의 idle 검사 직후, override가 잠금을 기다리는 동안 경쟁 작업이 예약된다.
            if busy == "recheck":
                P.recheck_factcheck(app.state.runner, job)
            elif busy == "factcheck_running":
                job.state["factcheck_running"] = True
            elif busy == "capcut_busy":
                job.capcut_busy = True
            else:
                job.state["status"] = "running"
        with pytest.raises(HTTPException) as exc:
            pending.result(timeout=5)
    assert exc.value.status_code == 409
    assert F.load_record(job.dir) == before


def test_override_holds_lock_through_record_save(override_ready, monkeypatch):
    _, job, endpoint = override_ready
    saving, release = threading.Event(), threading.Event()
    save_record = F.save_record

    def held_save(*args):
        saving.set()
        assert release.wait(5)
        save_record(*args)

    monkeypatch.setattr(F, "save_record", held_save)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(endpoint, job.id, {"note": "  기사와 직접 대조함  "})
        try:
            assert saving.wait(5)
            acquired = job.lock.acquire(blocking=False)
            if acquired:
                job.lock.release()
            assert not acquired, "record를 저장하기 전에 잠금이 풀림"
        finally:
            release.set()
        result = pending.result(timeout=5)
    rec = F.load_record(job.dir)
    assert rec["override"]["note"] == "기사와 직접 대조함"
    assert rec["override"]["input_hash"] == rec["input_hash"]
    assert result["factcheck"]["gate"] == "clear"


def test_override_still_rejects_stale_record(override_ready):
    _, job, endpoint = override_ready
    before = F.load_record(job.dir)
    F.atomic_json(job.dir / "topic.json", {"topic": "제목", "source_text": "수정한 본문"})
    with pytest.raises(HTTPException) as exc:
        endpoint(job.id, {"note": "직접 확인함"})
    assert exc.value.status_code == 400
    assert F.load_record(job.dir) == before
