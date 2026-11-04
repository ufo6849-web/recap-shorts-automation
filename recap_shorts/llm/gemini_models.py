"""설정 화면 '쓸 모델'의 고를 거리: Gemini API models.list(무료)에서 대본·목소리·이미지 모델을 나눠 보여 준다.

키가 없거나 목록을 못 받으면 아래 BUILTIN(우리가 실제로 써 본 모델)만 보여 준다. 목록은 10분 캐시.
라벨에 붙는 가격은 config/prices.json 에 적힌 모델만(없으면 붙이지 않음).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import requests

from recap_shorts import errors

URL = "https://generativelanguage.googleapis.com/v1beta/models"
PRICES = Path(__file__).resolve().parents[2] / "config" / "prices.json"
TTL = 600.0

# 써 본 모델(추천). 맨 앞이 기본값 — settings.MODEL_ROLES 의 default 와 같아야 한다.
BUILTIN = {
    "script": [("gemini-4-argon", "Gemini 4 Argon"), ("gemini-3.8-flash", "Gemini 3.8 Flash"),
               ("anthropic/claude-opus-5.5", "Claude Opus 5.5 · OpenRouter"),
               ("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5 · OpenRouter")],
    "tts": [("gemini-3.8-flash-tts", "Gemini 3.8 Flash TTS"), ("gemini-3.8-flash-lite-tts", "Gemini 3.8 Flash Lite TTS")],
    "image": [("gemini-3.1-flash-image", "Nano Banana 2"), ("gemini-3-pro-image", "Nano Banana Pro"),
              ("gemini-3.1-flash-lite-image", "Nano Banana 2 Lite")],
}
# 써 본 모델만 보여 주는 자리(대본은 두 개만) — 그 밖의 모델은 목록에 붙이지 않는다
ONLY_BUILTIN = {"script"}
# 글을 쓰는 모델이 아닌 것(대본 목록에서 뺌)
NOT_TEXT = ("computer-use", "robotics", "transcribe", "customtools", "embedding", "aqa", "live", "native-audio")

_cache: dict = {"at": 0.0, "key_tail": "", "items": None}
_lock = threading.Lock()


def role_of(name: str) -> str | None:
    """모델 이름 → script | tts | image | None."""
    n = name.lower()
    if not n.startswith("gemini"):
        return None
    if "-tts" in n:
        return "tts"
    if "image" in n or "banana" in n:
        return "image"
    if any(x in n for x in NOT_TEXT):
        return None
    return "script"


def _fetch(key: str, timeout: float = 8.0) -> list[tuple[str, str]]:
    """(이름, 표시 이름) — generateContent 를 받는 gemini 모델만."""
    out, token = [], None
    for _ in range(10):
        params = {"pageSize": 1000}
        if token:
            params["pageToken"] = token
        r = requests.get(URL, params=params, headers={"x-goog-api-key": key}, timeout=timeout)
        r.raise_for_status()
        d = r.json()
        for m in d.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                out.append((m["name"].removeprefix("models/"), m.get("displayName") or ""))
        token = d.get("nextPageToken")
        if not token:
            break
    return out


def _prices() -> dict:
    try:
        return json.loads(PRICES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def price_note(role: str, name: str, prices: dict | None = None) -> str:
    p = _prices() if prices is None else prices
    if role == "script" and name in p.get("gemini_text", {}):
        t = p["gemini_text"][name]
        return f"입력 ${t['input_per_m']:g} · 출력 ${t['output_per_m']:g} / 100만 토큰"
    if role == "tts" and name in p.get("gemini_tts", {}):
        t = p["gemini_tts"][name]
        return f"입력 ${t['input_per_m']:g} · 음성 ${t['audio_out_per_m']:g} / 100만 토큰"
    if role == "image" and name in p.get("image", {}):
        return f"장당 ${p['image'][name]['per_image']:g}"
    return ""


def catalog(key: str, refresh: bool = False) -> dict:
    """{"source": "api"|"builtin", "note", "roles": {role: [{"id","label","recommended"}]}}.
    추천(BUILTIN, 이 키로 쓸 수 있는 것만) → 그 밖의 모델(이름 역순: 새 버전이 위) 순서. ONLY_BUILTIN 자리는 추천만."""
    items, source, note = None, "builtin", "Gemini 키가 없어 써 본 모델만 보여 줍니다"
    if key:
        with _lock:
            tail = key[-6:]
            fresh = _cache["items"] is not None and _cache["key_tail"] == tail and time.time() - _cache["at"] < TTL
            if fresh and not refresh:
                items = _cache["items"]
            else:
                try:
                    items = _fetch(key)
                    _cache.update(at=time.time(), key_tail=tail, items=items)
                except (requests.RequestException, ValueError, KeyError) as e:
                    code = e.response.status_code if isinstance(e, requests.HTTPError) and e.response is not None else None
                    if code in (400, 401, 403):        # 목록도 키 시험과 같은 기준(400/401/403 = 키 문제)
                        note = f"모델 목록을 받지 못해 써 본 모델만 보여 줍니다 — Gemini 키가 맞지 않는 것 같습니다 " \
                               f"(HTTP {code}) · 오른쪽 위 '설정'에서 키를 확인하세요"
                    else:
                        note = f"모델 목록을 받지 못해 써 본 모델만 보여 줍니다 — {errors.reason(e)}"
        if items is not None:
            source, note = "api", "이 키로 쓸 수 있는 모델(Gemini API 목록)입니다"
    prices = _prices()
    roles: dict[str, list[dict]] = {}
    for role, rec in BUILTIN.items():
        names = {n: d for n, d in (items or []) if role_of(n) == role}
        out = []
        for n, d in rec:
            if items is None or n in names or "/" in n:          # vendor/name(OpenRouter)은 Gemini 목록과 상관없이 늘 보인다
                out.append({"id": n, "label": _label(role, n, names.get(n) or d, prices), "recommended": True})
        if role not in ONLY_BUILTIN:
            for n in sorted(set(names) - {n for n, _ in rec}, reverse=True):
                out.append({"id": n, "label": _label(role, n, names[n], prices), "recommended": False})
        roles[role] = out
    return {"source": source, "note": note, "roles": roles}


def _label(role: str, name: str, display: str, prices: dict) -> str:
    p = price_note(role, name, prices)
    head = f"{display} ({name})" if display and display.lower() != name.lower() else name
    return f"{head} — {p}" if p else head
