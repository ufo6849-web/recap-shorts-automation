"""unit 시험은 엔드포인트를 직접 호출한다. test_http_*는 외부 TestClient 실행용."""
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from recap_shorts import factcheck as F
from recap_shorts.app import create_app
from recap_shorts.collect import blind, dcbest, topic_factcheck as T
from tests.pipeline.fakes import FakeDeps

PREFIX = "/api/topics/factcheck"


def endpoint(app, path):
    return next(r.endpoint for r in app.routes if getattr(r, "path", "") == path)


def listing(app, source="dcbest"):
    return endpoint(app, "/api/topics")(source=source, pages=1, sort="views", hours=48,
                                        refresh=False, hide_unfit=False, hide_made=False)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("실제 API 호출 금지")
    monkeypatch.setattr(F.G, "_post", forbidden)
    kwargs = dict(title="서버 캐시의 제목", tag="일상", views=100, recommends=1, comments=1,
                  posted_at="2026-11-04T12:00:00+09:00", age_hours=1, url="https://www.teamblind.com/kr/post/test-Abc123")
    monkeypatch.setattr(dcbest, "fetch_best", lambda *a, **k: [dcbest.BestItem(no=1, **kwargs)])
    monkeypatch.setattr(blind, "fetch_best", lambda *a, **k: [blind.BestItem(no="Abc123", **kwargs)])
    monkeypatch.setattr(dcbest, "triage_items", lambda *a, **k: ({}, "skipped", ""))
    app = create_app(work_root=tmp_path, deps=FakeDeps(), start_runner=False)
    service = app.state.topic_factcheck
    service.ready = lambda: True
    calls = []
    service.llm = lambda *args: calls.append(args) or {"items": [{"i": 1, "kind": "news"}]}
    service.verify = lambda *a, **k: {"results": [{"id": "C1", "verdict": "unverified", "sources": [], "why": "미확인"}], "cost_usd": .06}
    yield app, service, calls
    service.cancel()
    if service.thread:
        service.thread.join(5)
        assert not service.thread.is_alive()


def estimate(app, source="dcbest", **extra):
    return endpoint(app, PREFIX + "/estimate")({"source": source, "nos": [1] if source == "dcbest" else ["Abc123"], **extra})


@pytest.mark.parametrize("source", ["dcbest", "blind"])
def test_unit_server_cache_titles_and_annotations(app_env, source):
    app, service, calls = app_env
    result = listing(app, source)
    assert result["fc_ready"] is True
    assert result["fc_counts"] == {"news": 0, "checked": 0, "doubt": 0}
    assert result["items"][0]["fc"] is None and calls == []
    quoted = estimate(app, source, items=[{"no": 1, "title": "클라이언트가 조작한 제목"}], title="거짓 제목")
    assert len(calls) == 1
    assert "서버 캐시의 제목" in calls[0][0]
    assert "클라이언트" not in calls[0][0]
    assert "거짓 제목" not in calls[0][0]
    endpoint(app, PREFIX + "/run")({"run_id": quoted["run_id"]})
    service.thread.join(5)
    result = listing(app, source)
    assert len(calls) == 1
    assert result["fc_counts"] == {"news": 1, "checked": 1, "doubt": 0}
    assert result["items"][0]["fc"]["chip"]["label"] == "확인 안 됨"
    assert estimate(app, source)["todo"] == 0
    assert estimate(app, source, force=True)["todo"] == 1


@pytest.mark.parametrize("body", [
    {"source": "gallery", "nos": [1]}, {"source": "dcbest", "nos": "1"},
    {"source": "dcbest", "nos": [True]}, {"source": "dcbest", "nos": [{}]},
    {"source": "dcbest", "nos": [1] * 201}, {"source": "dcbest", "nos": [], "force": "yes"},
])
def test_unit_estimate_validation(app_env, body):
    app, _, _ = app_env
    with pytest.raises(HTTPException) as exc:
        endpoint(app, PREFIX + "/estimate")(body)
    assert exc.value.status_code == 400


def test_unit_missing_list_expired_snapshot_key_and_budget_conflicts(app_env):
    app, service, _ = app_env
    with pytest.raises(HTTPException) as exc:
        estimate(app)
    assert exc.value.status_code == 409
    listing(app)
    quoted = estimate(app)
    service.snapshots[quoted["run_id"]]["created"] -= 600
    with pytest.raises(HTTPException) as exc:
        endpoint(app, PREFIX + "/run")({"run_id": quoted["run_id"]})
    assert exc.value.status_code == 409
    quoted = estimate(app)
    service.ready = lambda: False
    assert listing(app)["fc_ready"] is False
    assert "OpenRouter" in listing(app)["fc_note"]
    with pytest.raises(HTTPException) as exc:
        endpoint(app, PREFIX + "/run")({"run_id": quoted["run_id"]})
    assert exc.value.status_code == 409
    service.ready = lambda: True
    F.atomic_json(service.cache / "_fc_budget.json", {service._day(): 5.0})
    with pytest.raises(HTTPException) as exc:
        endpoint(app, PREFIX + "/run")({"run_id": quoted["run_id"]})
    assert exc.value.status_code == 409


def test_unit_run_status_and_cancel_conflict(app_env):
    app, service, _ = app_env
    listing(app)
    quoted = estimate(app)
    entered, release = threading.Event(), threading.Event()
    original = service.verify
    def verify(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    service.verify = verify
    try:
        endpoint(app, PREFIX + "/run")({"run_id": quoted["run_id"]})
        assert entered.wait(5)
        with pytest.raises(HTTPException) as exc:
            endpoint(app, PREFIX + "/run")({"run_id": quoted["run_id"]})
        assert exc.value.status_code == 409
        state = endpoint(app, PREFIX + "/status")()
        assert set(state) == {"run_id", "source", "state", "done", "total", "cost_usd", "stop_reason", "error"}
        assert state["run_id"] == quoted["run_id"] and state["state"] == "running"
        for _ in range(2):
            assert endpoint(app, PREFIX + "/cancel")()["state"] == "cancelling"
    finally:
        release.set()
        service.thread.join(5)
    assert endpoint(app, PREFIX + "/status")()["stop_reason"] == "cancelled"


@pytest.mark.parametrize("suffix", ["estimate", "run", "status", "cancel"])
def test_unit_all_routes_local_only(app_env, suffix):
    app, _, _ = app_env
    route = next(r for r in app.routes if getattr(r, "path", "") == PREFIX + "/" + suffix)
    guard = next(d.call for d in route.dependant.dependencies if d.call.__name__ == "local_only")
    for host, origin, allowed in [(b"localhost", b"http://localhost:5173", True),
                                   (b"remote.example", b"http://localhost", False),
                                   (b"localhost", b"https://remote.example", False)]:
        request = Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": [(b"host", host), (b"origin", origin)]})
        if allowed:
            guard(request)
        else:
            with pytest.raises(HTTPException) as exc:
                guard(request)
            assert exc.value.status_code == 403


def test_unit_classification_error_is_sanitized(app_env):
    app, service, _ = app_env
    listing(app)
    def fail(*a):
        raise RuntimeError("비밀키 값")
    service.llm = fail
    with pytest.raises(HTTPException) as exc:
        estimate(app)
    assert exc.value.status_code == 502
    assert "비밀키" not in exc.value.detail


def test_http_roundtrip(app_env):
    app, service, _ = app_env
    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost") as client:
        assert client.get("/api/topics", params={"source": "dcbest"}).status_code == 200
        quoted = client.post(PREFIX + "/estimate", json={"source": "dcbest", "nos": [1]}).json()
        assert quoted["classification_usd"] == .01
        response = client.post(PREFIX + "/run", json={"run_id": quoted["run_id"]})
        assert response.status_code == 200
        service.thread.join(5)
        state = client.get(PREFIX + "/status").json()
        assert state["state"] == "done" and state["done"] == 1
        assert client.post(PREFIX + "/cancel").status_code == 200
        assert client.post(PREFIX + "/run", json={"run_id": "missing"}).status_code == 409


@pytest.mark.parametrize("method,suffix", [("post", "estimate"), ("post", "run"), ("get", "status"), ("post", "cancel")])
def test_http_local_only(app_env, method, suffix):
    app, _, _ = app_env
    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost") as client:
        assert client.request(method, PREFIX + "/" + suffix, headers={"Origin": "https://evil.example"}, json={}).status_code == 403
        assert client.request(method, PREFIX + "/" + suffix, headers={"Host": "evil.example"}, json={}).status_code == 403


@pytest.mark.parametrize("peer,allowed", [
    ("127.0.0.1", True), ("::1", True), ("::ffff:127.0.0.1", True),
    ("203.0.113.5", False), ("::ffff:203.0.113.5", False), ("testclient", False), (None, False),
])
def test_unit_local_only_socket_peer(app_env, peer, allowed):
    app, _, _ = app_env
    route = next(r for r in app.routes if getattr(r, "path", "") == PREFIX + "/run")
    guard = next(d.call for d in route.dependant.dependencies if d.call.__name__ == "local_only")
    request = Request({"type": "http", "client": (peer, 50000) if peer else None,
                       "headers": [(b"host", b"localhost"), (b"x-forwarded-for", b"127.0.0.1")]})
    if allowed:
        guard(request)
    else:
        with pytest.raises(HTTPException) as exc:
            guard(request)
        assert exc.value.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("post", PREFIX + "/estimate"), ("post", PREFIX + "/run"),
    ("get", PREFIX + "/status"), ("post", PREFIX + "/cancel"), ("get", "/api/settings"),
])
def test_http_remote_peer_cannot_spoof_localhost(app_env, method, path):
    app, _, _ = app_env
    with TestClient(app, base_url="http://localhost", client=("203.0.113.5", 50000)) as client:
        response = client.request(method, path, headers={"Host": "localhost", "X-Forwarded-For": "127.0.0.1"}, json={})
        assert response.status_code == 403


@pytest.mark.parametrize("spent", [4.995, 5.0])
def test_http_classification_budget_exhausted_returns_409_without_llm(app_env, spent):
    app, service, calls = app_env
    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost") as client:
        assert client.get("/api/topics", params={"source": "dcbest"}).status_code == 200
        service.cache.mkdir(parents=True, exist_ok=True)
        F.atomic_json(service.cache / "_fc_budget.json", {service._day(): spent})
        response = client.post(PREFIX + "/estimate", json={"source": "dcbest", "nos": [1]})
        assert response.status_code == 409 and "하루" in response.json()["detail"]
        assert calls == [] and service._budget()[1] == spent


def test_http_late_settlement_error_status_and_recovery(app_env, monkeypatch):
    app, service, _ = app_env
    release = threading.Event()
    result = {}
    def request():
        if release.wait(10):
            result["response"] = {"usageMetadata": {"costUsd": .1}}
    thread = threading.Thread(target=request)
    original = service._settle
    def fail(*args):
        raise OSError("비밀값")
    def timeout(*args):
        raise T._RequestTimeout(thread, result)
    thread.start()
    try:
        with TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost") as client:
            assert client.get("/api/topics", params={"source": "dcbest"}).status_code == 200
            service.llm = timeout
            monkeypatch.setattr(service, "_settle", fail)
            body = {"source": "dcbest", "nos": [1]}
            assert client.post(PREFIX + "/estimate", json=body).status_code == 502
            release.set()
            thread.join(5)
            assert not thread.is_alive()
            for _ in range(2):
                response = client.get(PREFIX + "/status")
                assert response.status_code == 200
                assert response.json()["error"] == T.LEDGER_NOTE
                assert set(response.json()) == {"run_id", "source", "state", "done", "total", "cost_usd", "stop_reason", "error"}
                assert "비밀값" not in response.text
            annotated = client.get("/api/topics", params={"source": "dcbest"})
            assert annotated.status_code == 200 and annotated.json()["fc_ready"] is False
            assert annotated.json()["fc_note"] == T.LEDGER_NOTE
            assert client.post(PREFIX + "/estimate", json=body).status_code == 409
            assert client.post(PREFIX + "/run", json={"run_id": "missing"}).status_code == 409
            assert client.post(PREFIX + "/cancel").status_code == 200
            with pytest.raises(T.Conflict):
                T._FileLock(service.cache / "_fc_run.lock", blocking=False)
            monkeypatch.setattr(service, "_settle", original)
            response = client.get(PREFIX + "/status")
            assert response.status_code == 200 and response.json()["error"] is None
            assert service._budget()[1] == pytest.approx(.1)
            assert client.get("/api/topics", params={"source": "dcbest"}).json()["fc_ready"] is True
            with T._FileLock(service.cache / "_fc_run.lock", blocking=False):
                pass
    finally:
        release.set()
        thread.join(5)
