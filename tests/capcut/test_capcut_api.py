"""검수 화면의 '캡컷으로 넘기기' API: 가짜 의존성으로 장면 배치까지 간 작업을 임시 초안 폴더로만 넘긴다."""
import json

import pytest
from fastapi.testclient import TestClient

from recap_shorts.app import create_app
from recap_shorts.capcut import desktop
from tests.app.test_api import idle, scenes_job
from tests.pipeline.fakes import FakeDeps


@pytest.fixture
def env(tmp_path, monkeypatch):
    drafts = tmp_path / "com.lveditor.draft"
    drafts.mkdir()
    (drafts / "root_meta_info.json").write_text(json.dumps({"all_draft_store": [], "draft_ids": 0, "root_path": str(drafts)}),
                                                encoding="utf-8")
    monkeypatch.setenv("RECAP_CAPCUT_DRAFTS_DIR", str(drafts))
    monkeypatch.setattr(desktop, "capcut_running", lambda: False)
    app = create_app(work_root=tmp_path / "work", deps=FakeDeps())
    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8020") as client:     # 넘기기는 이 PC 에서만(local_only)
        yield client, app, drafts


def test_export_before_scenes_is_409(env):
    client, app, _ = env
    r = client.post("/api/jobs", json={"gallery": "baseball_new13", "no": 2101})
    idle(app)
    jid = r.json()["id"]
    assert client.post(f"/api/jobs/{jid}/capcut").status_code == 409
    assert client.get(f"/api/jobs/{jid}/capcut").json() == {"last": None}
    assert client.post("/api/jobs/nope/capcut").status_code == 404


def test_export_registers_draft_and_remembers_it(env):
    client, app, drafts = env
    jid = scenes_job(client, app)
    r = client.post(f"/api/jobs/{jid}/capcut")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["registered"] and res["check"]["ok"], res
    assert (drafts / res["draft_name"] / "draft_content.json").is_file()
    idx = json.loads((drafts / "root_meta_info.json").read_text(encoding="utf-8"))
    assert idx["all_draft_store"][0]["draft_name"] == res["draft_name"]
    assert {x["element"] for x in res["report"]} >= {"그림 컷", "자막", "목소리"}
    last = client.get(f"/api/jobs/{jid}/capcut").json()["last"]
    assert last["draft_name"] == res["draft_name"] and last["exists"] is True
    assert any("캡컷 초안" in e["msg"] for e in client.get(f"/api/jobs/{jid}").json()["log"])


def test_double_click_while_busy_is_409(env, monkeypatch):
    client, app, _ = env
    jid = scenes_job(client, app)
    from recap_shorts.capcut import export as ce
    seen = {}

    def slow(job_dir, **kw):             # 첫 요청이 아직 만드는 중일 때 같은 작업으로 또 누르면
        seen["second"] = client.post(f"/api/jobs/{jid}/capcut").status_code
        raise RuntimeError("멈춤")
    monkeypatch.setattr(ce, "export_job", slow)
    assert client.post(f"/api/jobs/{jid}/capcut").status_code == 500
    assert seen["second"] == 409
