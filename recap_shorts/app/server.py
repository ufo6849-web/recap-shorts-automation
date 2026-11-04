"""FastAPI 앱 — 설계서 docs/03_v2_app_design.md 1.2 의 REST 계약.

job 실행은 pipeline.Runner(스레드 1개 큐)가 한다. 작업 중(status=running)인 job 을 바꾸는 요청은 409.
web/dist 가 있으면 "/"에서 프론트를 제공한다.

계약 밖으로 덧붙인 것: GET/PUT /api/jobs/{id}/topic, POST /script/refine, GET /assets, 목록의 thumbnail·progress,
POST /assets/blur(사람이 그린 사각형을 모자이크하고 캡처 카드를 다시 만듦), POST /assets/sensitive(자극적인 그림의 흑백/흐림을 사람이 고름·되돌림),
/fonts/*(assets/fonts 정적 제공),
/api/topics 의 항목별 made(이미 만든 소재 표시)·hide_made, POST /api/jobs 의 중복 막기(409 + allow_duplicate).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from recap_shorts import archive, errors
from recap_shorts import factcheck as fc
from recap_shorts import pipeline as P
from recap_shorts.collect import made as made_mod

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GALLERY = "baseball_new13"
TOPICS_TTL = 600.0
FIT_RETRY_AFTER = 60.0      # 맞춤 점수 호출이 실패하면 이 시간 동안은 다시 부르지 않는다(하루 한도·키 없음일 때 화면 조작마다 기다리지 않게)
ARCHIVED_MSG = "구글 드라이브에 보관된 작업이라 고칠 수 없습니다"
# 단계 → 그 단계가 부르는 조건(settings.readiness 의 need). 자료 화면은 그림 읽기(비전) 호출을 부른다.
STEP_NEEDS = {"script": ("script",), "read": ("read",), "assets": ("read",), "voice": ("voice",)}
WHO = {"gemini": "Gemini", "openrouter": "OpenRouter"}


def _ok(detail: str, ok: bool = True) -> dict:
    return {"ok": bool(ok), "detail": detail}


def _fingerprint(*secrets: str) -> str:
    import hashlib
    return hashlib.sha256("\n".join(s or "" for s in secrets).encode("utf-8")).hexdigest()[:16]


class WebFiles(StaticFiles):
    """web/dist. index.html 은 매번 바뀌었는지 묻게 한다(ETag 로 304) — 캐시 머리말이 없으면 브라우저·앱 창(WebView2)이
    다시 빌드한 화면 대신 예전 index.html(예전 js 이름)을 그대로 써서 고친 화면이 안 보일 수 있다."""

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(".html"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


def settle(job: P.Job) -> None:
    """곁가지 작업(다시 녹음·이미지 다시 만들기 등)이 끝난 뒤 job 상태를 단계 상태에서 다시 정한다."""
    steps = job.state["steps"]
    if any(s["status"] == "error" for s in steps):
        job.set_status("error")
    elif any(s["status"] == "waiting" for s in steps):
        job.set_status("waiting")
    elif all(s["status"] == "done" for s in steps):
        job.set_status("done")
    else:
        job.set_status("waiting")


def invalidate_video(job: P.Job) -> None:
    """영상·검수가 이미 만들어졌다면 낡은 것이므로 다시 하도록 되돌린다."""
    for key in ("render", "review"):
        if job.step(key)["status"] != "todo":
            job.reset_from(key)
            return


def create_app(work_root: str | Path | None = None, deps: P.Deps | None = None, start_runner: bool = True) -> FastAPI:
    store = P.JobStore(work_root)
    deps = deps or P.Deps()
    runner = P.Runner(store, deps)

    @asynccontextmanager
    async def lifespan(_app):
        if start_runner:
            runner.start()
        yield
        runner.stop()

    app = FastAPI(title="recap-shorts", version="2", lifespan=lifespan)
    app.state.store, app.state.runner, app.state.deps = store, runner, deps
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    topics_cache: dict[tuple, tuple[float, dict]] = {}
    topics_lock = threading.Lock()
    create_lock = threading.Lock()      # 중복 확인과 job 만들기를 한 덩어리로(같은 글을 동시에 두 번 눌러도 하나만)
    fit_lock = threading.Lock()
    # 설정 화면의 '연결 확인'에서 키가 틀렸다고 나온 키(지문만, 값은 두지 않음) — 상태 점검이 '키 있음'만 보고 초록이 되지 않게
    bad_keys: dict[str, str] = {}
    fit_down = {"until": 0.0, "why": ""}

    def fit_llm(prompt: str, model: str):
        if time.time() < fit_down["until"]:
            raise errors.mark(RuntimeError(f"{fit_down['why']} — 잠시 쉬는 중"), fit_down.get("kind") or errors.OTHER,
                              fit_down.get("http"))
        try:
            return deps.topic_fit_llm(prompt, model)
        except Exception as e:  # noqa: BLE001  화면에는 짧은 한국어 이유만(topicfit.errors.reason)
            fit_down.update(until=time.time() + FIT_RETRY_AFTER, why=errors.reason(e),
                            kind=errors.kind_of(e), http=errors.http_of(e))
            raise

    def job_or_404(jid: str) -> P.Job:
        j = store.get(jid)
        if j is None:
            raise HTTPException(404, f"job 없음: {jid}")
        return j

    def idle_or_409(job: P.Job) -> None:
        if job.archived:
            raise HTTPException(409, ARCHIVED_MSG)
        if job.state["status"] == "running":
            raise HTTPException(409, "지금 작업 중입니다 — 끝난 뒤에 다시 시도하세요")

    def view(job: P.Job, brief: bool = False) -> dict:
        with job.lock:
            st = json.loads(json.dumps(job.state))
        imgs = st.get("artifacts", {}).get("images") or []
        thumb = imgs[0] if imgs else None
        if thumb is None and (job.dir / "post").is_dir():
            first = sorted((job.dir / "post").glob("img_*"))
            thumb = first[0].relative_to(job.dir).as_posix() if first else None
        st["thumbnail"] = thumb
        if job.archived:        # 드라이브가 꺼져 있으면 파일을 못 읽는다(목록은 색인 요약으로 보임)
            try:
                st["offline"] = not (job.dir / "state.json").is_file()
            except OSError:
                st["offline"] = True
        st["factcheck_policy"] = st.get("factcheck_policy")
        current = P.factcheck_status(job)
        st["factcheck"] = P.factcheck_view(job, status=current)
        if not current["legacy"]:
            rec = current["record"] or {}
            for step in st["steps"]:
                if step["key"] != "read":
                    continue
                for sub in step.get("sub") or []:
                    if sub["label"] != "사실 확인":
                        continue
                    if st.get("factcheck_running") or (step["status"] == "running" and sub["status"] == "running"):
                        status, note = "running", "확인 중"
                    elif current["stale"] or current["missing"]:
                        status, note = "error", "글이 바뀌어 다시 확인해야 합니다"
                    elif current["gate"] == "clear" and rec.get("status") in ("stop", "failed"):
                        status, note = "done", "직접 확인으로 넘김"
                    elif current["gate"] == "clear" and rec.get("status") == "warn":
                        status, note = "done", "주의 확인하고 넘김"
                    else:
                        status = "error" if current["gate"] == "blocked" else "done"
                        note = P._FC_NOTE.get(rec.get("status"), current["gate_reason"] or "확인 결과 없음")
                    sub.update(status=status, note=note)
        if brief:
            st["log"] = st["log"][-5:]
        return st

    def factcheck_gate_or_409(job: P.Job) -> None:
        """사실 확인이 통과하지 않았으면(막힘·낡음·기록 없음, 주의 확인 전) 다시 하기·렌더·넘기기를 막는다. 기능 이전 작업은 통과."""
        if job.state.get("factcheck_running"):
            raise HTTPException(409, "사실 확인 중입니다 — 끝난 뒤에 다시 시도하세요")
        if job.capcut_busy:
            raise HTTPException(409, "캡컷 초안을 이미 만드는 중입니다")
        st = P.factcheck_status(job)
        if st["gate"] != "clear":
            raise HTTPException(409, st["gate_reason"])

    # ---------------------------------------------------------------- 상태 점검
    # 'gemini_key' 항목은 화면 호환 때문에 이름을 그대로 두고, 뜻은 'AI 키 준비'(설정 readiness)다.
    HEALTH_VIA = {"gemini": "Gemini 키로 부름", "openrouter": "OpenRouter 키로 부름(Gemini 키 없음)",
                  "mixed": "Gemini·OpenRouter 키 있음"}

    @app.get("/api/health")
    def health():
        out = {}
        gem_bad = False
        try:
            rd = pipeline_ready(fake_ok=False)          # 가짜 부품 앱도 실제 설정을 본다(상태 점검)
            gem_bad = rd["bad"] == "gemini"
            if rd["bad"]:                               # 실제로 쓰일 공급자의 키가 연결 확인에서 틀렸다고 나온 것
                out["gemini_key"] = _ok(rd["reason"], False)
                out["gemini_key"]["bad"] = True
            elif not rd["ok"]:
                out["gemini_key"] = _ok(rd["reason"], False)
            else:
                out["gemini_key"] = _ok(HEALTH_VIA.get(rd["via"], "준비됨"))
        except Exception as e:  # noqa: BLE001
            out["gemini_key"] = _ok(f"설정을 읽지 못함 — {errors.screen_text(e, '파일을 읽을 수 없습니다')}", False)
        try:
            from recap_shorts import settings
            from recap_shorts.clef import client as cc
            conf = bool(cc.configured())
            if conf and bad_keys.get("cloudflare") == _fingerprint(settings.get_key("cf_account_id"), settings.get_key("cf_api_token")):
                out["cf_keys"] = _ok("Cloudflare 키가 맞지 않습니다(Clef 꺼짐) — 설정에서 확인하세요", False)
            else:
                out["cf_keys"] = _ok("Cloudflare 키 있음" if conf else "Cloudflare 키 없음(Clef 꺼짐)", conf)
        except Exception as e:  # noqa: BLE001
            out["cf_keys"] = _ok(f"Clef 불러오기 실패 — {errors.screen_text(e, '설정을 읽을 수 없습니다')}", False)
        smi = shutil.which("nvidia-smi")
        gpu = None
        if smi:
            try:
                import subprocess
                r = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=5)
                gpu = (r.stdout.strip().splitlines() or [None])[0] if r.returncode == 0 else None
                if gpu:  # "GPU 0: NVIDIA ... (UUID: GPU-...)" → 이름만 (UUID 는 화면·기록에 남기지 않는다)
                    gpu = re.sub(r"\s*\(UUID:[^)]*\)", "", re.sub(r"^GPU \d+:\s*", "", gpu)).strip()
            except Exception:  # noqa: BLE001
                gpu = None
        out["gpu"] = _ok(gpu or "GPU 없음", bool(gpu))
        try:
            from recap_shorts import settings
            from recap_shorts.images import provider
            on = settings.image_engine() != "off"
            if settings.image_engine() == "gemini" and gem_bad:     # Gemini 이미지는 같은 키를 쓴다
                out["image_server"] = _ok("Gemini 이미지 — Gemini API 키가 맞지 않습니다", False)
            else:
                out["image_server"] = _ok(provider.engine_detail(), bool(on and deps.image_available()))
        except Exception:  # noqa: BLE001
            out["image_server"] = _ok("AI 그림 상태를 확인하지 못함", False)
        try:
            from recap_shorts.voice import asr
            has = asr.available()
            out["asr_model"] = _ok("받아쓰기 모델 있음" if has else "받아쓰기 모델 없음 — 발음 확인을 건너뜁니다", has)
        except Exception:  # noqa: BLE001
            out["asr_model"] = _ok("받아쓰기 모델 없음 — 발음 확인을 건너뜁니다", False)
        ff = shutil.which("ffmpeg")
        out["ffmpeg"] = _ok("ffmpeg 있음" if ff else "ffmpeg 없음", bool(ff))
        return out

    # ---------------------------------------------------------------- 설정(키·AI 그림 엔진)
    def local_only(request: Request) -> None:
        """/api/settings* 는 이 컴퓨터에서만: Host·Origin 머리말의 호스트가 127.0.0.1/localhost/::1 이어야 한다."""
        allowed = ("127.0.0.1", "localhost", "::1")

        def host_only(v: str) -> str:
            v = (v or "").strip().lower()
            if v.startswith("["):                 # [::1]:8020
                return v[1:v.find("]")] if "]" in v else v
            return v.rsplit(":", 1)[0] if v.count(":") == 1 else v

        if host_only(request.headers.get("host", "")) not in allowed:
            raise HTTPException(403, "이 컴퓨터에서만 설정을 바꿀 수 있습니다")
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse
            if (urlparse(origin).hostname or "") not in allowed:
                raise HTTPException(403, "이 컴퓨터에서만 설정을 바꿀 수 있습니다")

    def _scrub(msg: str, *keys: str) -> str:
        for k in keys:
            if k:
                msg = msg.replace(k, "<key>")
        return msg

    @app.get("/api/settings", dependencies=[Depends(local_only)])
    def get_settings():
        from recap_shorts import settings
        return settings.public_view()

    @app.put("/api/settings", dependencies=[Depends(local_only)])
    def put_settings(body: dict = Body(...)):
        from recap_shorts import settings
        try:
            return settings.save(body.get("keys"), body.get("image_engine"), body.get("models"), body.get("voice"),
                                 body.get("sfx"), body.get("memes"))
        except settings.SettingsError as e:
            raise HTTPException(e.status, str(e))

    @app.post("/api/settings/sfx/pick", dependencies=[Depends(local_only)])
    def post_sfx_pick():
        """효과음 폴더 고르기 — 이 컴퓨터에 Windows 폴더 고르기 창을 띄우고 고른 경로를 돌려준다(저장은 화면이 PUT 으로).
        {"path": "C:\\..."} 또는 취소하면 {"path": null}. Windows 가 아니거나 창을 못 띄우면 501(경로를 직접 붙여 넣기)."""
        from recap_shorts import folder_pick
        try:
            return {"path": folder_pick.pick("효과음 폴더를 고르세요")}
        except folder_pick.PickError as e:
            raise HTTPException(501, str(e))

    # ---------------------------------------------------------------- 효과음·짤 폴더 분석(docs/library_analysis.md)
    def _lib(fn, *a, **kw):
        from recap_shorts.fx import library
        try:
            return fn(*a, **kw)
        except library.LibraryError as e:
            raise HTTPException(e.status, str(e))

    @app.get("/api/library", dependencies=[Depends(local_only)])
    def get_library():
        from recap_shorts.fx import library
        return library.list_all()

    @app.post("/api/library/pick", dependencies=[Depends(local_only)])
    def post_library_pick(body: dict = Body(default={})):
        """효과음·짤 폴더 고르기 창(효과음 폴더 고르기와 같음). {"path"} 또는 취소면 {"path": null}."""
        from recap_shorts import folder_pick
        title = "짤 폴더를 고르세요" if (body or {}).get("kind") == "meme" else "효과음 폴더를 고르세요"
        try:
            return {"path": folder_pick.pick(title)}
        except folder_pick.PickError as e:
            raise HTTPException(501, str(e))

    @app.post("/api/library/add", dependencies=[Depends(local_only)])
    def post_library_add(body: dict = Body(...)):
        from recap_shorts.fx import library
        b = body or {}
        return _lib(library.add, str(b.get("kind") or ""), str(b.get("dir") or ""))

    @app.post("/api/library/analyze", dependencies=[Depends(local_only)])
    def post_library_analyze(body: dict = Body(...)):
        from recap_shorts.fx import library
        b = body or {}
        return _lib(library.start, str(b.get("kind") or ""), str(b.get("dir") or ""), use=bool(b.get("use")))

    @app.get("/api/library/{kind}/{pid}", dependencies=[Depends(local_only)])
    def get_library_pack(kind: str, pid: str):
        from recap_shorts.fx import library
        return _lib(lambda: library.detail(library.get(kind, pid)))

    @app.put("/api/library/{kind}/{pid}/items", dependencies=[Depends(local_only)])
    def put_library_items(kind: str, pid: str, body: dict = Body(...)):
        from recap_shorts.fx import library
        return _lib(library.apply_changes, kind, pid, (body or {}).get("changes"))

    @app.post("/api/library/{kind}/{pid}/use", dependencies=[Depends(local_only)])
    def post_library_use(kind: str, pid: str, body: dict = Body(default={})):
        from recap_shorts.fx import library
        return _lib(library.use, kind, pid, bool((body or {}).get("on", True)))

    @app.post("/api/library/{kind}/{pid}/stop", dependencies=[Depends(local_only)])
    def post_library_stop(kind: str, pid: str):
        from recap_shorts.fx import library
        return _lib(library.stop, kind, pid)

    @app.delete("/api/library/{kind}/{pid}", status_code=204, dependencies=[Depends(local_only)])
    def delete_library_pack(kind: str, pid: str):
        from fastapi.responses import Response
        from recap_shorts.fx import library
        _lib(library.delete, kind, pid)
        return Response(status_code=204)

    @app.get("/api/library/{kind}/{pid}/file", dependencies=[Depends(local_only)])
    def get_library_file(kind: str, pid: str, key: str | None = None, out: str | None = None):
        from recap_shorts.fx import library
        p = _lib(library.file_path, kind, pid, key=key, out=out)
        return FileResponse(p, headers={"Cache-Control": "no-store"})

    @app.post("/api/settings/voice/preview", dependencies=[Depends(local_only)])
    def post_voice_preview(body: dict = Body(default={})):
        """목소리 미리듣기 — 본문 {engine, model, voice, sid, text} 중 안 준 것은 저장된 설정. 응답은 wav 파일 그대로.
        같은 값이면 저장해 둔 소리를 돌려준다(X-Preview-Cached: 1, Gemini 요청을 쓰지 않음)."""
        from recap_shorts.voice import preview
        b = body or {}
        try:
            wav, cached = preview.make(store.root, b.get("engine"), b.get("model"), b.get("voice"), b.get("sid"), b.get("text"))
        except preview.PreviewError as e:
            raise HTTPException(e.status, str(e))
        return FileResponse(wav, media_type="audio/wav", headers={"Cache-Control": "no-store", "X-Preview-Cached": "1" if cached else "0"})

    @app.get("/api/settings/gemini-models", dependencies=[Depends(local_only)])
    def get_gemini_models(refresh: bool = False):
        """'쓸 모델'의 고를 거리 — 키가 있으면 Gemini API 목록(무료 호출, 10분 캐시), 없으면 써 본 모델만."""
        from recap_shorts import settings
        from recap_shorts.llm import gemini_models
        return gemini_models.catalog(settings.get_key("gemini_api_key"), refresh=refresh)

    @app.post("/api/settings/test", dependencies=[Depends(local_only)])
    def post_settings_test(body: dict = Body(...)):
        import requests as rq

        from recap_shorts import settings
        service = (body or {}).get("service")
        if service not in ("gemini", "openrouter", "cloudflare"):
            raise HTTPException(400, f"모르는 service 입니다: {service}")
        out = {"service": service, "ok": False, "message": "키가 없습니다"}

        def fail(msg: str, *secrets: str) -> dict:
            out["message"] = _scrub(msg, *secrets)
            return out

        if service == "gemini":
            key = settings.get_key("gemini_api_key")
            if not key:
                return out
            try:
                r = rq.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 1},
                           headers={"x-goog-api-key": key}, timeout=10)
            except Exception:  # noqa: BLE001
                return fail("연결 실패 — 인터넷 연결을 확인하세요", key)
            if r.status_code == 200:
                bad_keys.pop("gemini", None)
                out.update(ok=True, message="연결됨")
            elif r.status_code in (400, 401, 403):
                bad_keys["gemini"] = _fingerprint(key)
                return fail(f"키가 맞지 않습니다 (HTTP {r.status_code})", key)
            elif r.status_code == 429:
                bad_keys.pop("gemini", None)
                return fail("키는 맞지만 사용 한도에 걸렸습니다", key)
            else:
                return fail(f"확인 실패 (HTTP {r.status_code})", key)
        elif service == "openrouter":
            key = settings.get_key("openrouter_api_key")
            if not key:
                return out
            try:
                r = rq.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=10)
            except Exception:  # noqa: BLE001
                return fail("연결 실패 — 인터넷 연결을 확인하세요", key)
            if r.status_code == 200:
                bad_keys.pop("openrouter", None)
                try:
                    v = (r.json().get("data") or {}).get("limit_remaining")
                except Exception:  # noqa: BLE001
                    v = None
                left = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
                out.update(ok=True, message="연결됨" + (f" (남은 한도 ${left:.2f})" if left is not None else ""))
            elif r.status_code in (401, 403):
                bad_keys["openrouter"] = _fingerprint(key)
                return fail(f"키가 맞지 않습니다 (HTTP {r.status_code})", key)
            elif r.status_code == 402:                  # 잔액 부족은 인증이 된 뒤의 답 — 틀린 키 표시는 지운다
                bad_keys.pop("openrouter", None)
                return fail("OpenRouter 잔액이 모자랍니다 (HTTP 402) — openrouter.ai 에서 확인하세요", key)
            elif r.status_code == 429:
                bad_keys.pop("openrouter", None)
                return fail("키는 맞지만 사용 한도에 걸렸습니다", key)
            else:
                return fail(f"확인 실패 (HTTP {r.status_code})", key)
        else:
            acc, tok = settings.get_key("cf_account_id"), settings.get_key("cf_api_token")
            if not (acc and tok):
                return out if not (acc or tok) else fail("계정 ID 와 토큰이 둘 다 필요합니다", acc, tok)
            try:
                r = rq.get(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/models/search",
                           params={"per_page": 1}, headers={"Authorization": f"Bearer {tok}"}, timeout=10)
            except Exception:  # noqa: BLE001
                return fail("연결 실패 — 인터넷 연결을 확인하세요", acc, tok)
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    data = {}
                if isinstance(data, dict) and data.get("success"):
                    bad_keys.pop("cloudflare", None)
                    out.update(ok=True, message="연결됨 (Workers AI 사용 가능)")
                else:
                    return fail(f"확인 실패 (HTTP {r.status_code})", acc, tok)
            elif r.status_code in (401, 403):
                bad_keys["cloudflare"] = _fingerprint(acc, tok)
                return fail("토큰이 맞지 않거나 Workers AI 권한이 없습니다", acc, tok)
            elif r.status_code == 404:
                bad_keys["cloudflare"] = _fingerprint(acc, tok)
                return fail("계정 ID 가 맞지 않습니다", acc, tok)
            else:
                return fail(f"확인 실패 (HTTP {r.status_code})", acc, tok)
        return out

    # 모델 파일 받기(설정 화면의 받기 버튼) — 뒤에서 받고, 화면은 GET 으로 진행 상황(job)을 본다
    def models_view() -> dict:
        from recap_shorts import modelfiles
        return {"models": modelfiles.status(), "setup_cmd": modelfiles.setup_cmd()}

    @app.get("/api/settings/models", dependencies=[Depends(local_only)])
    def get_models():
        return models_view()

    @app.post("/api/settings/models/{model_id}/install", dependencies=[Depends(local_only)])
    def post_model_install(model_id: str):
        from recap_shorts import modelfiles
        if model_id not in [m["id"] for m in modelfiles.MODELS]:
            raise HTTPException(404, f"모르는 모델입니다: {model_id}")
        try:
            modelfiles.start(model_id)
        except modelfiles.ModelError as e:
            raise HTTPException(409, str(e))
        return models_view()

    # ---------------------------------------------------------------- 주제
    def fit_views(items: list[dict], hide_unfit: bool) -> tuple[list[dict], dict]:
        """채널 대주제 맞춤 점수. 글 모델 키(Gemini 나 OpenRouter)가 하나도 없으면 부르지 않고 목록만 원래 순서로 보여 준다
        (점수를 못 매긴 이유에 'RuntimeError' 같은 것을 화면에 내보내지 않게)."""
        from recap_shorts import settings
        from recap_shorts.collect import topicfit
        ccfg = topicfit.load_config()
        topic = topicfit.resolve(ccfg)
        if topic is not None and not (fake_parts() or settings.llm_ready()):
            return items, {"channel": {"main_topic": topic.id, "label": topic.label},
                           "fit": "unavailable", "fit_note": "키가 없어 맞춤 점수는 건너뜀", "unfit_count": 0}
        with fit_lock:          # 같은 글을 두 요청이 동시에 묻지 않게(뒤 요청은 보관된 점수를 읽는다)
            return topicfit.apply_to_views(
                items, topic, fit_llm, store.root / "_topics",
                batch=ccfg["fit"]["batch"], model=ccfg["fit"]["model"], hide_unfit=hide_unfit)

    def mark_made(items: list[dict], gallery: str, hide_made: bool, site: str = "") -> tuple[list[dict], dict]:
        """항목마다 made(이미 만든 소재 표시)를 붙인다 — 로컬·보관 job 색인과 제목만 견주므로 네트워크 호출이 없다.
        hide_made 이면 같은 글로 만든 항목은 뺀다(비슷한 이야기는 표시만 하고 남김)."""
        extra = made_mod.annotate_items(made_mod.get_index(store.root), items, gallery, site=site)
        before = len(items)
        if hide_made:
            items = made_mod.drop_made(items)
        return items, {**extra, "made_hidden": before - len(items)}

    def dcbest_topics(pages: int, sort: str, hours: int, refresh: bool, hide_unfit: bool = False, hide_made: bool = False) -> dict:
        """디시 실시간 베스트 주제 목록. 목록은 10분 캐시, Clef 분류는 글마다 work/_topics 에 보관(다시 묻지 않음).
        채널 대주제가 정해져 있으면 제목마다 맞춤 점수(fit)를 붙여 딱 맞음 → 조금 → 아님 순으로 내보낸다(없으면 예전 그대로).
        항목마다 made 를 붙인다(만든 적 있는 글·비슷한 이야기 — recap_shorts/collect/made.py)."""
        from recap_shorts.collect import dcbest
        from recap_shorts.collect.http import BlockedError, HttpClient
        if sort not in dcbest.SORTS:
            raise HTTPException(400, f"sort 는 {', '.join(dcbest.SORTS)} 중 하나")
        cfg = dcbest.load_config()
        limits = cfg.get("defaults", {})
        pages = max(1, min(pages, int(limits.get("max_pages", 5))))
        key = ("dcbest", pages, hours)
        with topics_lock:
            hit = topics_cache.get(key)
            if hit and not refresh and time.time() - hit[0] < TOPICS_TTL:
                raw, fetched_at, tri_state, tri_note, tris = hit[1]
            else:
                try:
                    raw = dcbest.fetch_best(pages, hours, "views", HttpClient(), max_pages=int(limits.get("max_pages", 5)))
                except BlockedError as e:
                    raise HTTPException(502, f"디시 실시간 베스트를 가져오지 못함: {e}")
                except dcbest.DcbestNotAllowed as e:
                    raise HTTPException(403, str(e))
                try:
                    from recap_shorts.clef import client as cc
                    spent = lambda: cc.METER.neurons  # noqa: E731
                except Exception:  # noqa: BLE001
                    spent = None
                ordered = dcbest.sort_items(raw, "views")      # 분류는 조회수 많은 글부터(예산이 모자라면 아래쪽이 빠짐)
                tris, tri_state, tri_note = dcbest.triage_items(
                    ordered, deps.clef_triage, store.root / "_topics", batch=int(limits.get("triage_batch", 8)),
                    neuron_budget=float(limits.get("triage_neuron_budget", 600)), spent_fn=spent)
                fetched_at = P.now()
                topics_cache[key] = (time.time(), (raw, fetched_at, tri_state, tri_note, tris))
        items = [dcbest.topic_view(it, tris.get(it.no), cfg) for it in dcbest.sort_items(raw, sort)]
        items, fit_extra = fit_views(items, hide_unfit)
        items, made_extra = mark_made(items, "dcbest", hide_made)
        return {"source": "dcbest", "sort": sort, "hours": hours, "pages": pages, "fetched_at": fetched_at,
                "triage": tri_state, "triage_note": tri_note, "items": items,
                "hidden_count": sum(1 for i in items if i["hidden"]), **fit_extra, **made_extra}

    def blind_topics(sort: str, hours: int, refresh: bool, hide_unfit: bool = False, hide_made: bool = False) -> dict:
        """블라인드 토픽 베스트 목록 — dcbest_topics 와 같은 모양으로 돌려준다(쪽 넘김·더보기가 없어 pages 는 늘 1).
        목록은 10분 캐시. 채널 대주제가 있으면 제목 맞춤 점수를 붙이고(없으면 붙이지 않음), 항목마다 made 를 붙인다."""
        from recap_shorts.collect import blind
        from recap_shorts.collect.http import BlockedError, HttpClient
        if sort not in blind.SORTS:
            raise HTTPException(400, f"sort 는 {', '.join(blind.SORTS)} 중 하나")
        key = ("blind", hours)
        with topics_lock:
            hit = topics_cache.get(key)
            if hit and not refresh and time.time() - hit[0] < TOPICS_TTL:
                raw, fetched_at = hit[1]
            else:
                try:
                    raw = blind.fetch_best(1, hours, "views", HttpClient(), max_pages=1)
                except BlockedError as e:
                    raise HTTPException(502, f"블라인드 인기글을 가져오지 못함: {e}")
                except blind.BlindNotAllowed as e:
                    raise HTTPException(403, str(e))
                fetched_at = P.now()
                topics_cache[key] = (time.time(), (raw, fetched_at))
        items = [dict(blind.to_dict(it), category=None, hidden=False, hidden_reason="", triaged=False,
                      source_check=None, source_check_reason="") for it in blind.sort_items(raw, sort)]   # 분야 라벨(Clef) 없음 — 토픽 이름은 tag 로 보인다
        items, fit_extra = fit_views(items, hide_unfit)
        items, made_extra = mark_made(items, "blind", hide_made, site="blind")
        return {"source": "blind", "sort": sort, "hours": hours, "pages": 1, "fetched_at": fetched_at,
                "triage": "skipped", "triage_note": "블라인드는 Clef 분류 없이 그대로 보여 줍니다",
                "items": items, "hidden_count": 0, **fit_extra, **made_extra}

    # ---------------------------------------------------------------- 채널 대주제
    @app.get("/api/channel")
    def channel_get():
        """채널 대주제와 고를 수 있는 프리셋. main_topic: null(없음) | 프리셋 id | "custom"."""
        from recap_shorts.collect import topicfit
        return topicfit.public_view(topicfit.load_config())

    @app.put("/api/channel")
    def channel_put(body: dict = Body(...)):
        """대주제를 바꾼다 — config/channel.local.json(gitignore)에 저장. 본문 {main_topic, custom?}; custom 을 생략하면 이전 글을 둔다."""
        from recap_shorts.collect import topicfit
        if "main_topic" not in body:
            raise HTTPException(400, "main_topic 이 필요합니다(없음은 null)")
        try:
            cfg = topicfit.save_local(body["main_topic"], body["custom"] if "custom" in body else ...)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return topicfit.public_view(cfg)

    @app.post("/api/topics/refresh")
    def topics_refresh(body: dict = Body(default={})):
        """목록을 새로 불러온다(10분 캐시 무시). Clef 분류는 이미 한 글은 다시 묻지 않는다."""
        source = body.get("source") or "dcbest"
        if source == "blind":
            try:
                return blind_topics(str(body.get("sort") or "views"), int(body.get("hours") or 48), True,
                                    bool(body.get("hide_unfit", False)), bool(body.get("hide_made", False)))
            except (TypeError, ValueError):
                raise HTTPException(400, "hours 는 숫자여야 합니다")
        if source != "dcbest":
            raise HTTPException(400, "source 는 dcbest 또는 blind 입니다")
        from recap_shorts.collect import dcbest
        d = dcbest.load_config().get("defaults", {})
        try:
            return dcbest_topics(int(body.get("pages") or d.get("pages", 3)), str(body.get("sort") or d.get("sort", "views")),
                                 int(body.get("hours") or d.get("hours", 48)), True, bool(body.get("hide_unfit", False)),
                             bool(body.get("hide_made", False)))
        except (TypeError, ValueError):
            raise HTTPException(400, "pages·hours 는 숫자여야 합니다")

    @app.get("/api/topics")
    def topics(gallery: str = DEFAULT_GALLERY, pages: int | None = Query(None, ge=1, le=5), minor: bool = False,
               refresh: bool = False, source: str = "gallery", sort: str | None = None,
               hours: int | None = Query(None, ge=1, le=336), hide_unfit: bool = False, hide_made: bool = False):
        if source == "dcbest":
            from recap_shorts.collect import dcbest
            d = dcbest.load_config().get("defaults", {})
            return dcbest_topics(pages or int(d.get("pages", 3)), sort or str(d.get("sort", "views")),
                                 hours or int(d.get("hours", 48)), refresh, hide_unfit, hide_made)
        if source == "blind":
            return blind_topics(sort or "views", hours or 48, refresh, hide_unfit, hide_made)
        if source != "gallery":
            raise HTTPException(400, "source 는 dcbest·blind·gallery 중 하나")
        pages = pages or 2
        key = (gallery, pages, minor)

        def with_made(body: dict) -> dict:      # 캐시한 본문은 그대로 두고, 요청마다 made 를 새로 붙인 복사본을 돌려준다
            items, extra = mark_made([dict(i) for i in body["items"]], gallery, hide_made)
            return {**body, "items": items, **extra}
        with topics_lock:
            hit = topics_cache.get(key)
            if hit and not refresh and time.time() - hit[0] < TOPICS_TTL:
                return with_made(hit[1])
            from recap_shorts.collect import dcinside
            from recap_shorts.collect.http import BlockedError, HttpClient
            client = HttpClient()
            items, seen = [], set()
            try:
                for pg in range(1, pages + 1):
                    for p in dcinside.list_posts(gallery, minor, pg, client=client, recommend=True):
                        if p.no not in seen:
                            seen.add(p.no)
                            items.append({"no": p.no, "title": p.title, "date": p.date, "views": p.views,
                                          "recommends": p.recommends, "comments": p.comments, "url": p.url,
                                          "triage": None})
            except BlockedError as e:
                raise HTTPException(502, f"디시 목록을 가져오지 못함: {e}")
            except (dcinside.NotAllowed, ValueError) as e:
                raise HTTPException(400, str(e))
            triage_state = "unavailable"
            try:
                res = deps.clef_triage([{"no": it["no"], "title": it["title"]} for it in items])
            except Exception:  # noqa: BLE001
                res = None
            if res:
                by = {r.get("no"): r for r in res}
                for it in items:
                    it["triage"] = by.get(it["no"])
                triage_state = "ok"
            body = {"gallery": gallery, "pages": pages, "fetched_at": P.now(), "triage": triage_state, "items": items}
            topics_cache[key] = (time.time(), body)
            return with_made(body)

    # ---------------------------------------------------------------- jobs
    def fake_parts() -> bool:
        """가짜 부품(FakeDeps)으로 띄운 시험 앱은 키를 보지 않는다(시험용 앱은 막지 않는다)."""
        return type(deps) is not P.Deps

    def pipeline_ready(job_options: dict | None = None, steps: list[str] | None = None, *, fake_ok: bool = True) -> dict:
        """대본·그림 읽기·목소리가 갈 곳이 있고 틀린 키가 아닌가. health 와 작업 문이 같이 쓴다.
        job_options 의 script_model·tts_engine 을 설정 대신 쓰고, steps(앞으로 돌릴 단계)에 필요한 조건만 본다(None 이면 전부).
        fake_ok 면 가짜 부품 시험 앱은 통과한다(health 는 끈다 — 실제 설정을 본다)."""
        if fake_ok and fake_parts():
            return {"ok": True, "reason": None, "via": None, "bad": None}
        from recap_shorts import settings
        opts = job_options or {}
        need = None if steps is None else {n for s in steps for n in STEP_NEEDS.get(s, ())}
        rd = settings.readiness(script_model=opts.get("script_model") or None,
                                tts_engine=opts.get("tts_engine") or None, need=need)
        via = rd["via"]
        used = {"gemini", "openrouter"} if via == "mixed" else ({via} if via else set())
        for prov, name in (("gemini", "gemini_api_key"), ("openrouter", "openrouter_api_key")):
            k = settings.get_key(name)
            if prov in used and k and bad_keys.get(prov) == _fingerprint(k):
                return {"ok": False, "reason": f"{WHO[prov]} API 키가 맞지 않습니다 — 설정에서 키를 확인하세요",
                        "via": via, "bad": prov}
        return {"ok": rd["ok"], "reason": rd["reason"], "via": via, "bad": None}

    def steps_to_run(job: P.Job) -> list[str]:
        """이어 하기가 돌릴 단계. 첫 대기 단계는 P.continue_job 이 확인으로 끝내므로 뺀다(이미 끝난 단계 조건으로 막지 않게)."""
        waiting = next((s["key"] for s in job.state["steps"] if s["status"] == "waiting"), None)
        return [s["key"] for s in job.state["steps"] if s["status"] != "done" and s["key"] != waiting]

    def job_options_checked(opts: dict) -> dict:
        """작업 옵션 중 준비 상태를 바꾸는 것(대본 모델·목소리 엔진)만 확인한다. 틀리면 400, 대본 모델은 앞뒤 공백을 뺀다."""
        from recap_shorts import settings
        out = dict(opts)
        if out.get("script_model") not in (None, ""):
            sm = out["script_model"].strip() if isinstance(out["script_model"], str) else out["script_model"]
            if not settings.valid_model_id("script", sm):
                raise HTTPException(400, "대본 모델 이름에 쓸 수 없는 글자가 있습니다")
            out["script_model"] = sm
        if out.get("tts_engine") not in (None, "") and out["tts_engine"] not in settings.VOICE_ENGINE_IDS:
            raise HTTPException(400, "모르는 목소리 엔진입니다")
        return out

    def no_key_409(message: str) -> HTTPException:
        return HTTPException(409, {"code": "no_key", "message": message})

    def require_gemini_key(what: str) -> None:
        """Gemini 전용 곁가지(목소리 다시 녹음·AI 그림 다시 만들기)의 검사 — OpenRouter 로는 부를 수 없다."""
        from recap_shorts import settings
        if fake_parts() or settings.get_key("gemini_api_key"):
            return
        raise no_key_409(f"Gemini API 키가 없어 {what}을(를) 할 수 없습니다 — 오른쪽 위 '설정'에서 키를 넣으세요")

    def require_llm_key(what: str) -> None:
        """글 모델(대본 다듬기)의 검사 — Gemini 나 OpenRouter 키 중 하나면 된다."""
        from recap_shorts import settings
        if fake_parts() or settings.llm_ready():
            return
        raise no_key_409(f"{what}을(를) 할 수 없습니다 — Gemini 또는 OpenRouter API 키가 필요합니다")

    @app.post("/api/jobs")
    def create_job(body: dict = Body(...)):
        from recap_shorts.collect import blind, dcinside
        opts = body.get("options") or {}
        if not isinstance(opts, dict):
            raise HTTPException(400, "options 는 객체여야 합니다")
        opts = job_options_checked(opts)
        rd = pipeline_ready(opts)                  # 요청이 고른 대본 모델·목소리 엔진으로 준비 상태를 본다
        if not rd["ok"]:
            raise no_key_409(rd["reason"])
        url, site, topic = body.get("url"), "dcinside", ""
        if url:
            try:                                    # 디시 글 주소가 아니면 블라인드로 한 번 더 본다
                gallery, no, minor = dcinside.parse_dc_url(url)
            except ValueError:
                try:
                    no, url = blind.parse_blind_url(url)
                except ValueError:
                    raise HTTPException(400, "디시인사이드·블라인드 글 주소만 받습니다")
                gallery, minor, site = "blind", False, "blind"
        elif body.get("gallery") and body.get("no"):
            gallery, no, minor = str(body["gallery"]), body["no"], bool(body.get("minor", False))
            site = "blind" if gallery == "blind" else "dcinside"
            if site == "blind":
                no, url = str(no), blind.post_url(no)
            else:
                no = int(no)
                try:
                    url = dcinside.view_url(gallery, no, minor)
                except ValueError as e:
                    raise HTTPException(400, str(e))
        else:
            raise HTTPException(400, "url 또는 gallery+no 가 필요합니다")
        with create_lock:
            if body.get("allow_duplicate") is not True:
                idx = made_mod.get_index(store.root)
                dup = [j for j in idx.same_post(gallery, no) if j.site == site]
                if dup:     # 같은 글로 이미 만든 job 이 있음 — 사람이 그래도 만들겠다고 하면(allow_duplicate) 다시 보낸다
                    dup = sorted(dup, key=lambda j: j.created_at, reverse=True)
                    raise HTTPException(409, {"code": "duplicate", "message": made_mod.duplicate_message(dup), "count": len(dup),
                                              "jobs": [{"id": j.job_id, "title": j.our_title, "created_at": j.created_at,
                                                        "finished": j.finished, "archived": j.archived} for j in dup[:5]]})
            job = store.create({"site": site, "gallery": gallery, "no": no, "url": url, "minor": minor,
                                "title": body.get("title"), "topic": topic}, opts)
        runner.submit(job, detail="작업 시작")
        return {"id": job.id}

    @app.get("/api/jobs")
    def list_jobs():
        return [view(j, brief=True) for j in store.all()]

    @app.get("/api/jobs/{jid}")
    def get_job(jid: str):
        return view(job_or_404(jid))

    @app.post("/api/jobs/{jid}/continue")
    def cont(jid: str):
        job = job_or_404(jid)
        if job.archived:
            raise HTTPException(409, ARCHIVED_MSG)
        rd = pipeline_ready(job.options, steps_to_run(job))
        if not rd["ok"]:                               # 앞으로 돌릴 단계가 대본·그림 읽기·목소리를 부르면 준비부터
            raise no_key_409(rd["reason"])
        try:
            P.continue_job(runner, job)
        except (P.Busy, P.FactcheckBlocked) as e:
            raise HTTPException(409, str(e))
        return view(job)

    @app.post("/api/jobs/{jid}/rerun")
    def rerun(jid: str, body: dict = Body(...)):
        """어떤 단계부터 다시(앞 단계 결과는 그대로). 자료 화면을 다시 만들 때 이미 만든 AI 그림은 그대로 쓴다."""
        job = job_or_404(jid)
        key = body.get("from")
        if key not in ("assets", "scenes", "render"):
            raise HTTPException(400, "from 은 assets·scenes·render 중 하나")
        with job.lock:
            idle_or_409(job)
            factcheck_gate_or_409(job)
            rd = pipeline_ready(job.options, P.STEP_KEYS[P.STEP_KEYS.index(key):])   # key 부터 끝까지가 다시 돈다
            if not rd["ok"]:
                raise no_key_409(rd["reason"])
            if any(job.step(k)["status"] != "done" for k in P.STEP_KEYS[:P.STEP_KEYS.index(key)]):
                raise HTTPException(409, "앞 단계가 끝나야 다시 할 수 있습니다")
            job.reset_from(key)
            job.log(f"'{dict(P.STEPS)[key]}' 단계부터 다시")
            job.save()
            runner.submit(job, detail=f"{dict(P.STEPS)[key]}부터 다시")
        return view(job)

    @app.post("/api/jobs/{jid}/factcheck", dependencies=[Depends(local_only)])
    def factcheck_recheck(jid: str, body: dict = Body(default={})):
        """사실 확인을 다시 한다(force=true 면 일반 글도 주장을 뽑아 확인). 작업 중이거나 읽기가 끝나지 않았으면 409."""
        job = job_or_404(jid)
        idle_or_409(job)
        try:
            P.recheck_factcheck(runner, job, force=body.get("force") is True)
        except P.Busy as e:
            raise HTTPException(409, str(e))
        return view(job)

    @app.post("/api/jobs/{jid}/factcheck/override", dependencies=[Depends(local_only)])
    def factcheck_override(jid: str, body: dict = Body(...)):
        """사실 아님·확인 실패 판정을 사람이 직접 확인했다고 넘긴다(메모 1~500자). 글이 바뀌었으면 안 된다."""
        job = job_or_404(jid)
        note = body.get("note")
        note = note.strip() if isinstance(note, str) else ""
        if not 1 <= len(note) <= 500:
            raise HTTPException(400, "메모는 1~500자로 적어 주세요")
        with job.lock:
            idle_or_409(job)
            if job.state.get("factcheck_running"):
                raise HTTPException(409, "사실 확인 중입니다 — 끝난 뒤에 다시 시도하세요")
            if job.capcut_busy:
                raise HTTPException(409, "캡컷 초안을 이미 만드는 중입니다")
            st = P.factcheck_status(job)
            rec = st["record"]
            if rec is None or rec.get("status") not in ("stop", "failed") or st["stale"]:
                raise HTTPException(400, "사실 아님·확인 실패 판정이고 글이 바뀌지 않았을 때만 직접 확인으로 넘길 수 있습니다")
            rec["override"] = {"at": P.now(), "note": note, "input_hash": rec["input_hash"]}
            fc.save_record(job.dir, rec)
            job.log("사실 확인 판정을 직접 확인으로 넘김")
        return view(job)

    @app.post("/api/jobs/{jid}/archive")
    def archive_now(jid: str):
        """끝난 job 을 구글 드라이브로 옮긴다(손으로). 설정의 enabled 와 상관없이 드라이브가 있으면 옮긴다."""
        job = job_or_404(jid)
        if job.archived:
            raise HTTPException(409, "이미 구글 드라이브에 보관된 작업입니다")
        if job.state["status"] == "running":
            raise HTTPException(409, "지금 작업 중입니다 — 끝난 뒤에 다시 시도하세요")
        if job.state["status"] != "done":
            raise HTTPException(409, "끝난 작업만 옮길 수 있습니다")
        if archive.archive_base() is None:
            raise HTTPException(503, "구글 드라이브를 찾지 못했습니다")

        def task(j: P.Job):
            archive.archive_job_record(j)
            j.set_status("done")

        runner.submit(job, task, "구글 드라이브로 옮기기")
        return {"id": job.id, "status": "running"}

    @app.get("/api/storage")
    def storage():
        return archive.storage_info()

    # ---------------------------------------------------------------- 대본
    def script_view(job: P.Job) -> dict:
        d = job.dir / "script_final"
        if not (d / "script.json").is_file():
            raise HTTPException(409, "대본이 아직 없습니다")

        def rd(name):
            return json.loads((d / name).read_text(encoding="utf-8")) if (d / name).is_file() else None
        return {"script": rd("script.json"), "check": rd("script_check.json"), "draft": rd("script_draft.json")}

    @app.get("/api/jobs/{jid}/script")
    def get_script(jid: str):
        return script_view(job_or_404(jid))

    def spoken_lines(script: dict | None) -> list[str]:
        s = script or {}
        return [s.get("hook") or ""] + [x.get("text") or "" for x in s.get("sentences") or []] + [s.get("closing") or ""]

    def save_script(job: P.Job, script: dict, check: dict, detail: str) -> str:
        """대사가 그대로고 장면 설명(image_hint 등)만 바뀌었으면 목소리는 두고 자료 화면부터 다시. 다시 할 단계 key 를 돌려준다."""
        d = job.dir / "script_final"
        old = json.loads((d / "script.json").read_text(encoding="utf-8")) if (d / "script.json").is_file() else None
        same_voice = (old is not None and spoken_lines(old) == spoken_lines(script)
                      and job.step("voice")["status"] == "done" and (job.dir / "voice" / "timings.json").is_file())
        (d / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "script_check.json").write_text(json.dumps(check, ensure_ascii=False, indent=2), encoding="utf-8")
        start = "assets" if same_voice else "voice"
        with job.lock:
            job.reset_from(start)
            job.update_step("script", status="waiting", ended_at=P.now(), progress=1.0, detail=detail)
            job.state["title"] = " ".join(script.get("title_lines") or []) or job.state["title"]
            job.set_status("waiting")
        return start

    @app.put("/api/jobs/{jid}/script")
    def put_script(jid: str, body: dict = Body(...)):
        from recap_shorts.script.guard import run_guard
        from recap_shorts.script.pipeline import normalize_script
        job = job_or_404(jid)
        idle_or_409(job)
        script_view(job)
        raw = body.get("script", body)
        if not isinstance(raw, dict) or not isinstance(raw.get("sentences"), list):
            raise HTTPException(400, "script 형식이 아닙니다(sentences 필요)")
        topic = json.loads((job.dir / "topic.json").read_text(encoding="utf-8"))
        script, check = run_guard(normalize_script(raw), topic)
        start = save_script(job, script, check, "고친 대본 — 확인하세요")
        job.log("대본을 고침 — " + ("대사는 그대로라 목소리는 두고 자료 화면부터 다시 합니다" if start == "assets"
                                     else "목소리부터 다시 해야 합니다"))
        return script_view(job)

    @app.post("/api/jobs/{jid}/script/refine")
    def refine_script(jid: str):
        """대본 다듬기 다시 (LLM 한 번)."""
        from recap_shorts.script.guard import run_guard
        from recap_shorts.script.pipeline import normalize_script, refine_script as _refine
        job = job_or_404(jid)
        idle_or_409(job)
        if (job.options.get("script_llm") or os.environ.get("RECAP_SCRIPT_LLM") or "gemini_api").strip().lower() in ("gemini_api", "gemini"):
            require_llm_key("대본 다듬기")   # 대본 LLM 이 agy 면 키가 없어도 된다
        cur = script_view(job)["script"]

        def task(j: P.Job):
            topic = json.loads((j.dir / "topic.json").read_text(encoding="utf-8"))
            llm = deps.script_llm(j.options["script_llm"], j.options["script_model"])
            base, base_check = run_guard(normalize_script(cur), topic)
            script, check, info = _refine(topic, base, base_check, llm)
            check = dict(check)
            check["refine"] = info
            rec = fc.load_record(j.dir)
            if rec and any(x.get("verdict") in ("false", "unverified") for x in rec.get("claims") or []):
                script, check = P._script_factcheck(P.Ctx(j, "script", deps), script, check, rec)
            save_script(j, script, check, "다시 다듬음 — 확인하세요")
            j.log("대본 다듬기를 다시 함" + ("" if info["applied"] else f" (적용 안 됨: {info['error']})"))

        runner.submit(job, task, "대본 다듬기 다시")
        return {"id": job.id, "status": "running"}

    # ---------------------------------------------------------------- 목소리
    @app.post("/api/jobs/{jid}/voice/redo")
    def voice_redo(jid: str, body: dict = Body(...)):
        job = job_or_404(jid)
        idle_or_409(job)
        tpath = job.dir / "voice" / "timings.json"
        if not tpath.is_file():
            raise HTTPException(409, "목소리가 아직 없습니다")
        sents = json.loads(tpath.read_text(encoding="utf-8"))["sentences"]
        try:
            idx = int(body["index"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "index 가 필요합니다")
        if not 0 <= idx < len(sents):
            raise HTTPException(400, f"index 범위 밖: 0~{len(sents) - 1}")
        variants: list[tuple[str, str]] = []
        for v in body.get("variants") or []:
            if isinstance(v, str):
                a, _, b = v.partition("|")
                variants.append((a, b))
            else:
                variants.append((str(v.get("spoken") or sents[idx].get("spoken") or sents[idx]["text"]),
                                 str(v.get("style") or "")))
        if not variants:
            variants = [(sents[idx].get("spoken") or sents[idx]["text"], "")]
        if len(variants) > 3:
            raise HTTPException(400, "variants 는 3개까지(Gemini TTS 하루 한도)")
        tp = json.loads(tpath.read_text(encoding="utf-8"))
        if tp.get("engine") != "supertonic":
            require_gemini_key("목소리 다시 녹음")   # 로컬 목소리(supertonic)면 Gemini 키 없이도 된다

        def task(j: P.Job):
            j.update_step("voice", status="running", detail=f"문장 {idx} 다시 녹음 중 (요청 {len(variants)}회)")
            try:
                res = deps.redo_voice(j.dir / "voice", idx, variants)
            except Exception as e:  # noqa: BLE001
                P.write_error_log(j, "voice", e)
                j.update_step("voice", status="done", detail=f"다시 녹음 실패: {errors.screen_text(e)}")
                j.log(f"문장 {idx} 다시 녹음 실패: {errors.screen_text(e)}")
                settle(j)
                return
            with j.lock:
                m = res["chosen"].get("match")
                j.update_step("voice", status="done",
                              detail=f"문장 {idx} 다시 녹음 끝 (일치 {m})" if m is not None else
                              f"문장 {idx} 다시 녹음 끝 (받아쓰기 없이 첫 판)")
                invalidate_video(j)
                j.log(f"문장 {idx} 다시 녹음: 판 {res['chosen']['variant']} 선택")
                settle(j)

        runner.submit(job, task, f"문장 {idx} 다시 녹음")
        return {"id": job.id, "status": "running"}

    # ---------------------------------------------------------------- 장면
    def _scene_cuts(doc: dict) -> dict[int, dict]:
        """shot_plan.json 의 cuts 를 {문장 index: {"t": [...], "span": [a, b]}} 로."""
        raw = doc.get("cuts")
        if not isinstance(raw, dict):
            return {}
        return {int(k): v for k, v in raw.items() if str(k).isdigit() and isinstance(v, dict)}

    def _merge_cuts(job: P.Job, cuts, plan: dict[str, list[str]], old: dict[int, dict]) -> dict[str, dict]:
        """PUT /scenes 의 cuts 를 저장할 꼴로. cuts: {"<index>": [t...] | null} (선택).
        - null → 그 문장 사용자 컷을 지운다(자동으로).
        - body 에 없는 문장: 그 문장 그림 수가 그대로면 기존 사용자 컷 유지, 바뀌었으면 지운다.
        검사를 어기면 400 과 한국어 이유."""
        from recap_shorts import timeline as T
        timings = json.loads((job.dir / "voice" / "timings.json").read_text(encoding="utf-8"))
        spans = T.sentence_spans(timings)
        if cuts is not None and not isinstance(cuts, dict):
            raise HTTPException(400, "cuts 는 {문장 번호: [컷 시각...] | null} 형식이어야 합니다")
        cuts = cuts or {}
        out: dict[str, dict] = {}
        sent_idx = {int(s["index"]) for s in timings["sentences"]}
        for i, n_files, span in ((int(s["index"]), len(plan[str(s["index"])]), spans[int(s["index"])])
                                 for s in timings["sentences"] if str(s["index"]) in plan):
            key = str(i)
            if key in cuts:
                v = cuts[key]
                if v is None:
                    continue
                got = _checked_cuts(i, v, span, n_files)
                if got["t"]:                     # 그림 1장([])이면 사용자 컷 없음과 같다
                    out[key] = got
                continue
            prev = old.get(i)
            if not prev:
                continue
            if len(prev.get("t") or []) != n_files - 1:      # 그림 수가 바뀌면 지운다
                continue
            out[key] = prev
        for k in cuts:
            if not str(k).isdigit() or int(k) not in sent_idx:
                raise HTTPException(400, f"문장 {k} 이 대본에 없습니다")
        return out

    def _checked_cuts(index: int, ts, span: tuple[float, float], n_files: int) -> dict:
        """한 문장의 사용자 컷 검사: 숫자, 개수 = 그림 수 - 1, 오름차순, 문장 구간 안, 샷 0.4초 이상."""
        from recap_shorts.timeline import USER_CUT_MIN_S
        if not isinstance(ts, list):
            raise HTTPException(400, f"문장 {index}: cuts 는 컷 시각 목록이어야 합니다")
        if len(ts) != n_files - 1:
            raise HTTPException(400, f"문장 {index}: 컷은 {n_files - 1}개여야 합니다(그림 {n_files}장)")
        vals: list[float] = []
        for t in ts:
            if isinstance(t, bool) or not isinstance(t, (int, float)):
                raise HTTPException(400, f"문장 {index}: 컷 시각은 숫자여야 합니다")
            vals.append(float(t))
        a, b = span
        if any(vals[k] >= vals[k + 1] for k in range(len(vals) - 1)):
            raise HTTPException(400, f"문장 {index}: 컷 시각이 오름차순이 아닙니다")
        if vals and (vals[0] < a - 1e-6 or vals[-1] > b + 1e-6):
            raise HTTPException(400, f"문장 {index}: 컷이 문장 구간({a:.2f}~{b:.2f}초) 밖입니다")
        edges = [a] + vals + [b]
        if any(y - x < USER_CUT_MIN_S - 1e-9 for x, y in zip(edges, edges[1:])):
            raise HTTPException(400, f"문장 {index}: 샷이 {USER_CUT_MIN_S:.1f}초보다 짧습니다")
        return {"t": [round(v, 3) for v in vals], "span": [a, b]}

    def _scene_gaps(job: P.Job, timings: dict) -> dict[int, list[float]]:
        """문장 index -> 어절 경계 후보 시각(영상 시계, 샷 시각과 같은 시계). ASR 은 저장된 것만 쓴다("cache") —
        화면을 열 때마다 받아쓰기 모델을 돌리면 안 된다. 못 구하면 빈 목록."""
        from recap_shorts import wordcuts
        wc = wordcuts.from_options(job.dir, timings, job.options, asr="cache")
        out: dict[int, list[float]] = {}
        if wc is not None:
            for s in timings["sentences"]:
                try:
                    out[int(s["index"])] = [round(c.t, 3) for c in wc.cuts_in(int(s["index"]))]
                except Exception:  # noqa: BLE001  한 문장이 실패해도 화면은 뜨게
                    out[int(s["index"])] = []
        return out

    def scenes_view(job: P.Job) -> dict:
        sp = job.dir / "shot_plan.json"
        if not sp.is_file():
            raise HTTPException(409, "장면 배치가 아직 없습니다")
        doc = json.loads(sp.read_text(encoding="utf-8"))
        script = json.loads((job.dir / "script_final" / "script.json").read_text(encoding="utf-8"))
        timings = json.loads((job.dir / "voice" / "timings.json").read_text(encoding="utf-8"))
        infos = P.sentence_infos(script, timings)
        kinds = {e["file"]: e for e in P.load_assets(job)}
        from recap_shorts import timeline as T
        spans = T.sentence_spans(timings)
        saved = _scene_cuts(doc)
        gaps = _scene_gaps(job, timings)
        sents = []
        for s, info in zip(timings["sentences"], infos):
            i = str(s["index"])
            sc = (doc.get("scores") or {}).get(i, {})
            a, b = spans.get(int(s["index"]), (float(s["start"]), float(s["end"])))
            saved_cut = saved.get(int(s["index"]))
            sents.append({"index": s["index"], "role": s.get("role"), "text": s["text"], "start": s["start"],
                          "end": s["end"], "hint": info["hint"], "span": [a, b],
                          "gaps": gaps.get(int(s["index"]), []),
                          "cuts": [float(t) for t in saved_cut["t"]] if isinstance(saved_cut, dict)
                          and isinstance(saved_cut.get("t"), list) else None,
                          "images": [{"file": f, "kind": (kinds.get(f) or {}).get("kind"), "score": sc.get(f)}
                                     for f in doc["plan"].get(i, [])]})
        cands = [{"file": f, "kind": e["kind"], "sentence": e.get("sentence"), "status": e["status"],
                  "needs_review": e.get("needs_review"), "review_reason": e.get("review_reason"),
                  "recommend_regenerate": e.get("recommend_regenerate")} for f, e in kinds.items()]
        return {"sentences": sents, "candidates": cands, "scored_by": doc.get("scored_by"),
                "scores": doc.get("scores", {}), "note": doc.get("note")}

    @app.get("/api/jobs/{jid}/scenes")
    def get_scenes(jid: str):
        return scenes_view(job_or_404(jid))

    @app.get("/api/jobs/{jid}/intro_preview")
    def intro_preview(jid: str):
        """장면 편집기 미리보기용 인트로 카드. 렌더러(render/intro.IntroCard)가 만든 글자 묶음 그림과 시간 값을 그대로 넘긴다.
        미리보기가 카드를 안 그리면 첫 샷(글 머리 캡처)이 보여서 인트로가 고정 템플릿으로 나오지 않는 것처럼 보인다."""
        job = job_or_404(jid)
        try:
            ic = json.loads((job.dir / "timeline.json").read_text(encoding="utf-8")).get("intro")
        except (OSError, ValueError):
            return {"intro": None}
        if not ic or ic.get("style", "card") != "card":
            return {"intro": None}
        import base64
        import io

        from recap_shorts.render import intro as RI
        card = RI.IntroCard(ic)
        buf = io.BytesIO()
        card.group.save(buf, "PNG")
        return {"intro": {"t_pop": card.t_pop, "t_cut": card.t_cut, "pop_s": card.pop_s, "pop_text_scale": card.pts,
                          "zoom_pre_per_s": card.zpre, "zoom_post_per_s": card.zpost, "panel_ratio_end": card.r_end,
                          "panel_color": "#%02x%02x%02x" % card.panel, "group_cy": card.cy_g, "ss": RI.SS,
                          "label": ic.get("label"), "label_icon": ic.get("label_icon"), "lines": ic.get("lines"),
                          "group": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")}}

    @app.put("/api/jobs/{jid}/scenes")
    def put_scenes(jid: str, body: dict = Body(...)):
        job = job_or_404(jid)
        idle_or_409(job)
        scenes_view(job)
        plan = body.get("plan")
        if plan is None and isinstance(body.get("sentences"), list):
            plan = {str(s["index"]): [(x["file"] if isinstance(x, dict) else x) for x in s.get("images", [])]
                    for s in body["sentences"]}
        if not isinstance(plan, dict):
            raise HTTPException(400, "plan 또는 sentences 가 필요합니다")
        sp = job.dir / "shot_plan.json"
        doc = json.loads(sp.read_text(encoding="utf-8"))
        old, new = doc["plan"], {}
        base = job.dir.resolve()
        for k in old:
            files = plan.get(k, old[k])
            if not isinstance(files, list) or not files:
                raise HTTPException(400, f"문장 {k} 에는 그림이 1장 이상 있어야 합니다")
            for f in files:
                p = (job.dir / str(f)).resolve()
                if base not in p.parents or not p.is_file():
                    raise HTTPException(400, f"그림 파일이 없습니다: {f}")
            new[k] = [str(f) for f in files]
        old_cuts = _scene_cuts(doc)
        new_cuts = _merge_cuts(job, body.get("cuts"), new, old_cuts)
        doc["plan"], doc["note"] = new, "사람이 편집함"
        if new_cuts:
            doc["cuts"] = new_cuts
        else:
            doc.pop("cuts", None)
        sp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            P.write_timeline(job)
        except Exception as e:  # noqa: BLE001
            sp.write_text(json.dumps({**doc, "plan": old, "cuts": {str(k): v for k, v in old_cuts.items()}}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
            raise HTTPException(400, f"타임라인을 만들 수 없음: {e}")
        with job.lock:
            invalidate_video(job)
            job.log("장면 배치를 고침")
            settle(job)
        return scenes_view(job)

    # ---------------------------------------------------------------- 자료 이미지
    @app.get("/api/jobs/{jid}/assets")
    def get_assets(jid: str):
        job = job_or_404(jid)
        p = job.dir / "assets" / "assets.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []

    @app.post("/api/jobs/{jid}/assets/regenerate")
    def regen(jid: str, body: dict = Body(...)):
        from recap_shorts.images import provider, sensitive
        job = job_or_404(jid)
        idle_or_409(job)
        f = body.get("file")
        ap = job.dir / "assets" / "assets.json"
        entries = json.loads(ap.read_text(encoding="utf-8")) if ap.is_file() else []
        if next((x for x in entries if x["file"] == f and x["kind"] == "ai"), None) is None:
            raise HTTPException(404, "다시 만들 수 있는 AI 이미지가 아닙니다")
        if provider.engine() == "gemini":
            require_gemini_key("AI 그림 다시 만들기")   # 503(엔진 가용성)에 가려지지 않게 먼저 본다
        if not deps.image_available():
            raise HTTPException(503, "AI 그림을 만들 수 없습니다 — 설정에서 엔진을 확인하세요")
        new_hint = (body.get("prompt") or "").strip()

        def task(j: P.Job):
            ents = json.loads(ap.read_text(encoding="utf-8"))
            ent = next(x for x in ents if x["file"] == f)
            try:
                if new_hint:
                    en = new_hint if new_hint.isascii() else deps.hint_prompts([new_hint])[0]
                else:
                    en = ent.get("prompt_en") or deps.hint_prompts([ent.get("hint") or ""])[0]
                full = provider.build_prompt(en)
                tmp = j.dir / (f + ".new.png")
                res = deps.generate_image(full, tmp)
                if res["ok"] and tmp.is_file():
                    shutil.move(str(tmp), str(j.dir / f))
                    ent.update(status="ok", error=None, prompt=full, prompt_en=en, seconds=res.get("seconds"),
                               recommend_regenerate=False, review_reason=None, reused_from=None, inspect=None)
                    ent.pop("sensitive", None)                  # 새 그림이라 낡은 흑백/흐림 기록은 버리고 다시 판정
                    sensitive.orig_path(j.dir / f).unlink(missing_ok=True)
                    if j.options.get("sensitive_treatment", True):
                        sensitive.run(j.dir, [ent], read_fn=deps.sensitive_read, log=j.log)
                    if new_hint:
                        ent["hint"] = new_hint
                    j.log(f"이미지 다시 만듦: {f} ({res.get('seconds')}초)")
                    with j.lock:
                        invalidate_video(j)
                else:
                    ent["error_last"] = errors.screen_text(res.get("error"), "이미지를 다시 만들지 못했습니다")
                    j.log(f"이미지 다시 만들기 실패: {ent['error_last']}")
            except Exception as ex:  # noqa: BLE001
                P.write_error_log(j, "assets", ex)
                j.log(f"이미지 다시 만들기 오류: {errors.screen_text(ex)}")
            ap.write_text(json.dumps(ents, ensure_ascii=False, indent=2), encoding="utf-8")
            settle(j)

        runner.submit(job, task, f"이미지 다시 만들기: {f}")
        return {"id": job.id, "status": "running"}

    @app.post("/api/jobs/{jid}/assets/blur")
    def blur_asset(jid: str, body: dict = Body(...)):
        """캡처 카드를 만든 원본 그림 위에 사람이 그린 사각형(원본 픽셀 좌표 [x0,y0,x1,y1])을 모자이크하고,
        그 원본에서 나온 카드를 모두 다시 만든다. boxes=[] 이면 '가릴 곳 없음'으로 확인만 한다.
        boxes 는 그 원본 그림에 대한 전체 목록(이전 것을 대체) — assets.json 의 blur_boxes 에 기록한다."""
        from PIL import Image
        from recap_shorts.images import sensitive
        from recap_shorts.render.cards import make_card
        job = job_or_404(jid)
        idle_or_409(job)
        f = body.get("file")
        ap = job.dir / "assets" / "assets.json"
        entries = json.loads(ap.read_text(encoding="utf-8")) if ap.is_file() else []
        ent = next((x for x in entries if x["file"] == f and x.get("kind") == "cap" and x.get("src")), None)
        if ent is None:
            raise HTTPException(404, "가릴 수 있는 캡처 카드가 아닙니다")
        src = job.dir / ent["src"]
        if not src.is_file() or job.dir.resolve() not in src.resolve().parents:
            raise HTTPException(404, "원본 그림이 없습니다")
        with Image.open(src) as im:
            W, H = im.size
        boxes: list[tuple[int, int, int, int]] = []
        for b in body.get("boxes") or []:
            try:
                x0, y0, x1, y1 = (int(round(float(v))) for v in b)
            except (TypeError, ValueError):
                raise HTTPException(400, "boxes 는 [x0,y0,x1,y1] 숫자 목록이어야 합니다")
            x0, x1 = sorted((max(0, min(W, x0)), max(0, min(W, x1))))
            y0, y1 = sorted((max(0, min(H, y0)), max(0, min(H, y1))))
            if x1 - x0 >= 4 and y1 - y0 >= 4:
                boxes.append((x0, y0, x1, y1))
        if len(boxes) > 60:
            raise HTTPException(400, "사각형이 너무 많습니다(60개까지)")
        redone = []
        with job.lock:
            for e in entries:
                if e.get("kind") != "cap" or e.get("src") != ent["src"]:
                    continue
                crop = tuple(e["crop"]) if e.get("crop") else None
                if crop and (crop[2] > W or crop[3] > H):       # 원본 밖 crop(예전 칸 카드) → 그림 전체
                    crop = None
                focus = e.get("role") == "focus"
                make_card(src, job.dir / e["file"], crop=crop, blur_boxes=boxes, label=None,
                          highlight=tuple(e["highlight"]) if e.get("highlight") else None,
                          max_upscale=2.6 if focus else 2.2)
                e.update(blur_boxes=[list(b) for b in boxes], blurred=bool(boxes), needs_review=False, unblurred_risk=False,
                         review_reason="가림 확인됨" if boxes else "가릴 곳 없음으로 확인됨")
                sensitive.after_remake(job.dir, e)           # 카드를 새로 만들었으니 흑백/흐림 처리를 다시 입힘
                redone.append(e["file"])
            ap.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            used = False
            sp = job.dir / "shot_plan.json"
            if sp.is_file():
                used = any(x in redone for fs in json.loads(sp.read_text(encoding="utf-8"))["plan"].values() for x in fs)
            if used:
                invalidate_video(job)
                settle(job)
            job.log(f"가림 {len(boxes)}곳 적용: {ent['src']} → 카드 {len(redone)}장 다시 만듦")
        return {"file": f, "src": ent["src"], "boxes": [list(b) for b in boxes], "cards": redone}

    @app.post("/api/jobs/{jid}/assets/sensitive")
    def sensitive_asset(jid: str, body: dict = Body(...)):
        """자극적인 그림의 처리를 사람이 고른다. treatment: none(원래대로 — .orig.png 를 되돌림) | gray(흑백만) | gray_blur(흑백+흐림).
        처리는 항상 처리 전 그림(.orig.png)에서 다시 만든다. 영상에 쓰는 그림이면 영상·검수를 낡은 것으로 되돌린다(blur 와 같은 길)."""
        from recap_shorts.images import sensitive
        job = job_or_404(jid)
        idle_or_409(job)
        f, choice = body.get("file"), body.get("treatment")
        if choice not in sensitive.TREATMENTS:
            raise HTTPException(400, f"treatment 는 {' | '.join(sensitive.TREATMENTS)} 중 하나여야 합니다")
        ap = job.dir / "assets" / "assets.json"
        entries = json.loads(ap.read_text(encoding="utf-8")) if ap.is_file() else []
        ent = next((x for x in entries if x["file"] == f and x.get("status") in ("ok", "reused")), None)
        if ent is None:
            raise HTTPException(404, "처리할 수 있는 그림이 아닙니다")
        p = (job.dir / ent["file"]).resolve()
        if job.dir.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404, "그림 파일이 없습니다")
        with job.lock:
            try:
                sens = sensitive.set_treatment(job.dir, ent, choice)
            except FileNotFoundError as ex:
                raise HTTPException(409, str(ex))
            ap.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            used = False
            sp = job.dir / "shot_plan.json"
            if sp.is_file():
                used = any(f == x for fs in json.loads(sp.read_text(encoding="utf-8"))["plan"].values() for x in fs)
            if used:
                invalidate_video(job)
                settle(job)
            job.log(f"그림 처리를 {sens['treatment']} 로 정함: {f}")
        return {"file": f, "sensitive": sens, "needs_review": bool(ent.get("needs_review")), "video_stale": used}

    # ---------------------------------------------------------------- 렌더
    @app.post("/api/jobs/{jid}/render")
    def render_job(jid: str):
        job = job_or_404(jid)
        with job.lock:
            idle_or_409(job)
            factcheck_gate_or_409(job)
            sc = job.step("scenes")
            if sc["status"] not in ("waiting", "done"):
                raise HTTPException(409, "장면 배치가 끝난 뒤에 렌더할 수 있습니다")
            if sc["status"] == "waiting":
                sc.update(status="done")
            job.reset_from("render")
            job.save()
            runner.submit(job, detail="렌더 요청")
        return view(job)

    # ---------------------------------------------------------------- CapCut 초안으로 넘기기

    @app.get("/api/capcut/status")
    def capcut_status():
        from recap_shorts.capcut import desktop as cd
        return {**cd.status(), "running": cd.capcut_running()}

    @app.post("/api/capcut/open", dependencies=[Depends(local_only)])
    def capcut_open():
        from recap_shorts.capcut import desktop as cd
        return cd.launch()

    @app.get("/api/jobs/{jid}/capcut")
    def capcut_last(jid: str):
        from recap_shorts.capcut import export as ce
        return {"last": ce.last_export(job_or_404(jid).dir)}

    @app.post("/api/jobs/{jid}/capcut", dependencies=[Depends(local_only)])
    def capcut_export(jid: str):
        """영상에 쓴 타임라인을 CapCut 초안 폴더로(그림·글자·소리를 따로 고칠 수 있게). 몇 초 걸리고, 외부 호출은 없다."""
        from recap_shorts.capcut import export as ce
        job = job_or_404(jid)
        with job.lock:
            if job.state["status"] == "running":
                raise HTTPException(409, "지금 작업 중입니다 — 끝난 뒤에 다시 시도하세요")
            factcheck_gate_or_409(job)
            if job.step("scenes")["status"] not in ("done", "waiting"):
                raise HTTPException(409, "장면 배치까지 끝난 뒤에 캡컷으로 넘길 수 있습니다")
            job.capcut_busy = True
        try:
            r = ce.export_job(job.dir, record=not job.readonly)
        except FileNotFoundError as e:
            raise HTTPException(409, str(e))
        except Exception as e:  # noqa: BLE001
            P.write_error_log(job, "capcut", e)
            raise HTTPException(500, f"캡컷 초안을 만들지 못했습니다: {errors.screen_text(e)}")
        finally:
            with job.lock:
                job.capcut_busy = False
        if not job.readonly:
            job.log(f"캡컷 초안을 만들었습니다: {r['draft_name']}" + ("" if r["registered"] else " (프로젝트 목록에는 안 올림)"))
        return r

    # ---------------------------------------------------------------- topic 확인
    @app.get("/api/jobs/{jid}/topic")
    def get_topic(jid: str):
        job = job_or_404(jid)
        p = job.dir / "topic.json"
        if not p.is_file():
            raise HTTPException(409, "자료 읽기가 아직 끝나지 않았습니다")
        return json.loads(p.read_text(encoding="utf-8"))

    @app.put("/api/jobs/{jid}/topic")
    def put_topic(jid: str, body: dict = Body(...)):
        job = job_or_404(jid)
        idle_or_409(job)
        p = job.dir / "topic.json"
        if not p.is_file():
            raise HTTPException(409, "자료 읽기가 아직 끝나지 않았습니다")
        cur = json.loads(p.read_text(encoding="utf-8"))
        for k in ("topic", "source_text", "tone_end"):
            if isinstance(body.get(k), str):
                cur[k] = body[k]
        fc.atomic_json(p, cur)
        with job.lock:
            if job.step("script")["status"] != "todo":
                job.reset_from("script")
                job.update_step("read", status="waiting")
                job.set_status("waiting")
            job.log("topic 을 고침 — 대본부터 다시 합니다")
        return cur

    # ---------------------------------------------------------------- 파일
    @app.get("/media/{jid}/{path:path}")
    def media(jid: str, path: str):
        job = job_or_404(jid)
        base = job.dir.resolve()
        p = (base / path).resolve()
        if base not in p.parents:
            raise HTTPException(404)
        if not p.is_file():
            raise HTTPException(404, "파일 없음")
        # 가림·다시 만들기로 같은 이름의 그림이 바뀌므로 브라우저가 매번 바뀌었는지 묻게 한다(ETag 로 304)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    # ---------------------------------------------------------------- 프론트
    fonts = ROOT / "assets" / "fonts"
    if fonts.is_dir():
        app.mount("/fonts", StaticFiles(directory=fonts), name="fonts")   # 장면 편집기 미리보기 자막 글꼴(Pretendard)
    dist = ROOT / "web" / "dist"
    if dist.is_dir():
        app.mount("/", WebFiles(directory=dist, html=True), name="web")
    else:
        @app.get("/")
        def index():
            return JSONResponse({"app": "recap-shorts", "web": "web/dist 가 없습니다 — web/ 에서 npm run build 하세요",
                                 "api": "/docs"})

    return app
