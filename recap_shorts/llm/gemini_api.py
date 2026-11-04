"""Gemini API(generateContent, REST) 텍스트·비전 클라이언트.

- 주소: https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent, 헤더 x-goog-api-key.
- 키는 recap_shorts.voice.gemini.api_key() 에서만 읽고, 출력·로그·저장하지 않는다.
- 429/5xx 는 2회까지 다시 시도한다(5초, 10초 간격). 하루 한도(RESOURCE_EXHAUSTED + day) 메시지는 바로 올린다.
- 대본용 어댑터 GeminiApiLLM 은 script.llm.AgyLLM 과 같은 인터페이스(complete_json, calls)를 가진다.
- OpenRouter: vendor/name 모델이거나 Gemini 키가 없으면 OpenRouter chat/completions 로 보낸다(route 표).
  요청·답을 Gemini 모양으로 바꿔 주므로 호출하는 쪽(_text_of·generate_text·read_images …)은 그대로 쓴다.
"""
from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import requests

from recap_shorts import errors
from recap_shorts.script.llm import parse_json_loose

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MAX_TOKENS = 32000
TEXT_MODEL = "gemini-4-argon"
VISION_MODEL = "gemini-3.8-flash"
RETRIES = 2
JSON_RETRIES = 1        # 그림 읽기 답의 JSON 이 깨졌을 때 다시 묻는 횟수
MAX_IMAGE_SIDE = 2400
MAX_INLINE_BYTES = 6_000_000


class GeminiApiError(RuntimeError):
    """kind·http·provider 를 붙여 둔다 — 화면 문구는 recap_shorts.errors.screen_text 가 만든다.
    provider 는 "gemini" | "openrouter" (없으면 문구에 OpenRouter 가 있는지로 짐작한다)."""

    def __init__(self, message: str, *, kind: str | None = None, http: int | None = None, provider: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.http = http
        self.provider = provider


def _key() -> str:
    from recap_shorts.voice.gemini import api_key
    return api_key()


def _key_or_empty() -> str:
    try:
        return _key()
    except RuntimeError:      # 키가 없으면 경로 표(route)가 NO_KEY 로 알린다
        return ""


def _openrouter_key() -> str:
    from recap_shorts import settings
    return settings.get_key("openrouter_api_key")


def route(model: str, has_gemini: bool, has_openrouter: bool) -> tuple[str, str]:
    """모델 이름 → ("gemini" | "openrouter", 보낼 모델 이름). 설계서 경로 표 그대로.
    갈 곳이 없으면 NO_KEY 예외(provider 로 어느 키인지 알린다)."""
    if "/" in model:                                              # vendor/name 은 OpenRouter 로만 간다
        if has_openrouter:
            return "openrouter", model
        raise GeminiApiError("이 모델은 OpenRouter 키가 필요합니다", kind=errors.NO_KEY, provider="openrouter")
    if has_gemini:
        return "gemini", model
    if has_openrouter:                                            # Gemini 키가 없으면 같은 모델을 google/ 이름으로
        return "openrouter", "google/" + model
    raise GeminiApiError("Gemini API 키가 없습니다 — 화면 오른쪽 위 '설정'에서 넣으세요", kind=errors.NO_KEY, provider="gemini")


def _post(model: str, body: dict, timeout: float = 300.0, sleep=time.sleep) -> dict:
    okey = _openrouter_key()                 # 한 번만 읽는다: 다시 읽는 사이 키가 지워지면 빈 키로 나갈 수 있다
    provider, name = route(model, bool(_key_or_empty()), bool(okey))
    if provider == "openrouter":
        return _post_openrouter(name, body, timeout, sleep, key=okey)
    url = f"{BASE}/{model}:generateContent"
    last = ""
    for attempt in range(RETRIES + 1):
        try:
            r = requests.post(url, headers={"x-goog-api-key": _key(), "Content-Type": "application/json"},
                              json=body, timeout=timeout)
        except requests.RequestException as e:
            last = f"{type(e).__name__}"
            if attempt < RETRIES:
                sleep(5 * (attempt + 1))
                continue
            raise GeminiApiError(f"Gemini API 연결 실패({model}): {last}", kind=errors.NET, provider="gemini") from None
        if r.status_code == 200:
            data = r.json()
            um = data.get("usageMetadata") or {}
            from recap_shorts import usage
            usage.log("gemini_text", model=model, prompt_tokens=um.get("promptTokenCount"),
                      output_tokens=um.get("candidatesTokenCount"), thought_tokens=um.get("thoughtsTokenCount"),
                      total_tokens=um.get("totalTokenCount"),
                      images=sum(1 for c in body.get("contents", []) for p in c.get("parts", []) if "inline_data" in p))
            return data
        try:
            msg = str(r.json().get("error", {}).get("message", ""))[:300]
        except Exception:  # noqa: BLE001
            msg = r.text[:300]
        last = f"HTTP {r.status_code}: {msg}"
        kind = errors.kind_for_http(r.status_code, msg)
        if kind == errors.BAD_KEY:                     # 키가 틀리면 다시 시도해도 같다 — 바로 멈춘다
            raise GeminiApiError(f"Gemini API 실패({model}): {last}", kind=kind, http=r.status_code, provider="gemini")
        if r.status_code not in (429, 500, 502, 503, 504) or attempt >= RETRIES:
            raise GeminiApiError(f"Gemini API 실패({model}): {last}", kind=kind, http=r.status_code, provider="gemini")
        sleep(5 * (attempt + 1))
    raise GeminiApiError(f"Gemini API 실패({model}): {last}", provider="gemini")


# ---------------------------------------------------------------- OpenRouter (경로 표의 openrouter 줄)
def _audio_format(mime: str) -> str:
    sub = mime.split("/", 1)[-1]
    if sub in ("wav", "x-wav", "wave"):
        return "wav"
    if sub in ("mp3", "mpeg"):
        return "mp3"
    raise ValueError(f"OpenRouter 로 보낼 수 없는 소리 형식: {mime}")


def _or_parts(parts: list, seen: dict) -> list[dict]:
    out = []
    for p in parts:
        if "text" in p:
            out.append({"type": "text", "text": p["text"]})
            continue
        d = p.get("inline_data")
        if not d:
            raise ValueError("OpenRouter 로 보낼 수 없는 내용")
        mime = str(d.get("mime_type") or "")
        if mime.startswith("image/"):
            seen["images"] += 1
            out.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{d['data']}"}})
        elif mime.startswith("audio/"):
            seen["audio"] += 1
            out.append({"type": "input_audio", "input_audio": {"data": d["data"], "format": _audio_format(mime)}})
        else:
            raise ValueError(f"OpenRouter 로 보낼 수 없는 파일 형식: {mime}")
    return out


def _or_request(or_model: str, body: dict) -> tuple[dict, dict]:
    """Gemini 요청 본문 → OpenRouter chat 요청. (요청, 세어 본 그림·소리 수)."""
    seen = {"images": 0, "audio": 0}
    messages = [{"role": "user", "content": _or_parts(c.get("parts") or [], seen)} for c in body.get("contents") or []]
    req: dict = {"model": or_model, "messages": messages, "max_tokens": OR_MAX_TOKENS, "usage": {"include": True}}
    cfg = body.get("generationConfig") or {}
    if cfg.get("responseMimeType") == "application/json":
        req["response_format"] = {"type": "json_object"}
    level = (cfg.get("thinkingConfig") or {}).get("thinkingLevel")
    if level:
        req["reasoning"] = {"effort": level}
    if "temperature" in cfg and not or_model.startswith("anthropic/"):   # Claude 는 temperature 를 빼고 부른다
        req["temperature"] = cfg["temperature"]
    return req, seen


def _gemini_shape(data: dict, or_model: str) -> dict:
    """OpenRouter 답 → Gemini 답 모양. 거절·빈 내용은 OpenRouter 오류로 낸다(Gemini 문구가 붙지 않게).
    거절 문구의 blockReason=SAFETY 는 images/sensitive._is_block 이 알아보는 표식이다."""
    ch = (data.get("choices") or [{}])[0] or {}
    msg = ch.get("message") or {}
    finish = ch.get("finish_reason")
    if finish == "content_filter" or msg.get("refusal"):
        raise GeminiApiError(f"OpenRouter 응답이 거절됨({or_model}, blockReason=SAFETY)", provider="openrouter")
    content = msg.get("content")
    if isinstance(content, list):
        text = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    else:
        text = content if isinstance(content, str) else ""
    if not text.strip():
        raise GeminiApiError(f"OpenRouter 응답이 비어 있음(finishReason={finish})", provider="openrouter")
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}


def _log_openrouter(or_model: str, data: dict, seen: dict) -> None:
    from recap_shorts import usage
    u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    det = u.get("completion_tokens_details") if isinstance(u.get("completion_tokens_details"), dict) else {}

    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    usage.log("openrouter", model=or_model, prompt_tokens=num(u.get("prompt_tokens")),
              output_tokens=num(u.get("completion_tokens")), reasoning_tokens=num(det.get("reasoning_tokens")),
              cost=num(u.get("cost")), images=seen["images"], audio=seen["audio"])


def _json_dict(r) -> dict:
    try:
        d = r.json()
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _error_text(r, data: dict, key: str) -> str:
    err = data.get("error")
    msg = (err.get("message") if isinstance(err, dict) else err) or getattr(r, "text", "") or ""
    s = str(msg)
    if key:                                  # 자르기 전에 가린다 — 키가 300자 경계에 걸리면 앞부분이 남는다(빈 키는 건드리지 않음)
        s = s.replace(key, "<key>")
    return s[:300]


def _post_openrouter(or_model: str, body: dict, timeout: float = 300.0, sleep=time.sleep, *, key: str) -> dict:
    req, seen = _or_request(or_model, body)
    tries, stripped = 0, False
    while True:
        try:
            r = requests.post(OR_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json=req, timeout=timeout)
        except requests.RequestException as e:
            if tries < RETRIES:
                tries += 1
                sleep(5 * tries)
                continue
            raise GeminiApiError(f"OpenRouter 연결 실패({or_model}): {type(e).__name__}", kind=errors.NET,
                                 provider="openrouter") from None
        data = _json_dict(r)
        if r.status_code == 200 and data.get("choices"):
            _log_openrouter(or_model, data, seen)                # 성공한 답 한 번에 한 번만 기록
            return _gemini_shape(data, or_model)
        msg = _error_text(r, data, key)
        if r.status_code == 400 and "response_format" in req and not stripped and \
                ("response_format" in msg.lower() or "json" in msg.lower()):
            req = {k: v for k, v in req.items() if k != "response_format"}   # JSON 모드를 안 받는 모델: 한 번만 빼고 다시
            stripped = True
            continue
        if (r.status_code == 200 or r.status_code == 429 or r.status_code >= 500) and tries < RETRIES:
            tries += 1
            sleep(5 * tries)
            continue
        if r.status_code == 402:
            raise GeminiApiError("OpenRouter 잔액이나 키 한도가 모자랍니다", kind=errors.QUOTA, http=402, provider="openrouter")
        what = "답이 없음" if r.status_code == 200 else f"HTTP {r.status_code}"
        raise GeminiApiError(f"OpenRouter 실패({or_model}): {what}" + (f" — {msg}" if msg else ""),
                             kind=errors.kind_for_http(r.status_code, msg),
                             http=None if r.status_code == 200 else r.status_code, provider="openrouter")


def _text_of(resp: dict) -> str:
    cands = resp.get("candidates") or []
    if not cands:
        fb = resp.get("promptFeedback") or {}
        raise GeminiApiError(f"Gemini 응답에 후보가 없음: {str(fb)[:200]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought") and "text" in p)
    if not text.strip():
        raise GeminiApiError(f"Gemini 응답이 비어 있음(finishReason={cands[0].get('finishReason')})")
    return text


def generate_text(prompt: str, model: str = TEXT_MODEL, json: bool = True, *, temperature: float | None = None,
                  timeout: float = 300.0, thinking: str | None = None) -> dict | str:
    """json=True → 모델 답에서 JSON 객체를 꺼내 dict 로, False → 문자열.
    thinking = Gemini 3 의 thinkingLevel("low" 등). None 이면 모델 기본값(생각을 많이 해서 느릴 수 있음)."""
    cfg: dict = {}
    if json:
        cfg["responseMimeType"] = "application/json"
    if temperature is not None:
        cfg["temperature"] = temperature
    if thinking:
        cfg["thinkingConfig"] = {"thinkingLevel": thinking}
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if cfg:
        body["generationConfig"] = cfg
    text = _text_of(_post(model, body, timeout))
    return parse_json_loose(text) if json else text.strip()


def _image_part(img) -> dict:
    """경로·바이트·PIL 이미지 → inline_data. 너무 크면 줄여 JPEG 로."""
    from PIL import Image
    if isinstance(img, (str, Path)):
        im = Image.open(img)
    elif isinstance(img, (bytes, bytearray)):
        im = Image.open(io.BytesIO(img))
    else:
        im = img
    im.load()
    im = im.convert("RGB")
    s = min(1.0, MAX_IMAGE_SIDE / max(im.size))
    if s < 1.0:
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    mime = "image/png"
    if buf.tell() > MAX_INLINE_BYTES:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        mime = "image/jpeg"
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(buf.getvalue()).decode("ascii")}}


def read_images(images: list, instruction: str, model: str = VISION_MODEL, *, json: bool = False,
                timeout: float = 300.0, thinking: str | None = None) -> dict | str:
    """이미지(들) + 지시문 → 모델 답. 이미지는 지시문 앞에 순서대로 붙는다.
    thinking = Gemini 3 의 thinkingLevel("low" 등, generate_text 와 같음). None 이면 모델 기본값.
    json=True 인데 답의 JSON 이 깨졌으면 한 번 더 묻는다 — 긴 글을 옮겨 적을 때 가끔 깨지고, 다시 물으면 대개 제대로 온다."""
    parts = [_image_part(i) for i in images] + [{"text": instruction}]
    cfg: dict = {"responseMimeType": "application/json"} if json else {}
    if thinking:
        cfg["thinkingConfig"] = {"thinkingLevel": thinking}
    body = {"contents": [{"role": "user", "parts": parts}]}
    if cfg:
        body["generationConfig"] = cfg
    for attempt in range(JSON_RETRIES + 1):
        text = _text_of(_post(model, body, timeout))
        if not json:
            return text.strip()
        try:
            return parse_json_loose(text)
        except ValueError:
            if attempt >= JSON_RETRIES:
                raise


class GeminiApiLLM:
    """script.generate_script 가 쓰는 어댑터(AgyLLM 과 같은 모양)."""
    name = "gemini_api"

    def __init__(self, model: str = TEXT_MODEL, *, timeout: float = 300.0, max_calls: int | None = None):
        self.model, self.timeout, self.max_calls = model, timeout, max_calls
        self.calls: list[dict] = []

    def complete_json(self, prompt: str) -> dict:
        if self.max_calls is not None and len(self.calls) >= self.max_calls:
            raise GeminiApiError(f"Gemini 호출 한도 {self.max_calls}회 도달")
        t0 = time.time()
        try:
            out = generate_text(prompt, self.model, json=True, timeout=self.timeout)
        except Exception as e:  # noqa: BLE001
            self.calls.append({"seconds": round(time.time() - t0, 1), "ok": False, "error": str(e)[:300],
                               "model": self.model, "prompt_chars": len(prompt)})
            raise
        self.calls.append({"seconds": round(time.time() - t0, 1), "ok": True, "model": self.model,
                           "prompt_chars": len(prompt)})
        return out
