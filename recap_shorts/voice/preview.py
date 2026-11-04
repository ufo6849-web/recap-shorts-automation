"""설정 화면의 목소리 미리듣기: 예시 문장 하나를 고른 엔진·모델·목소리로 합성한다.

같은 (엔진, 모델, 목소리, 말투, 문장)은 <작업 폴더>/_preview/ 에 저장해 두고 다시 부르지 않는다 — Gemini 목소리는
한 번 = 요청 1회(하루 한도에 들어감)라 같은 버튼을 여러 번 눌러도 한 번만 쓴다. 말투는 영상과 같은 DEFAULT_STYLE.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from recap_shorts import settings

DEFAULT_TEXT = "요즘 커뮤니티에서 난리 난 이야기, 삼십 초 만에 정리해 줄게."
MAX_CHARS = 120


class PreviewError(RuntimeError):
    """미리듣기를 만들 수 없는 이유. status 는 REST 코드(400: 값이 틀림, 409: 지금은 못 함, 502: 바깥 API 실패)."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def make(work_root: Path, engine: str | None = None, model: str | None = None, voice: str | None = None,
         sid: int | None = None, text: str | None = None) -> tuple[Path, bool]:
    """(wav 경로, 저장해 둔 것을 썼는가). 값을 안 주면 설정 화면에 저장된 값."""
    from recap_shorts import modelfiles
    text = " ".join((text or "").split())[:MAX_CHARS] or DEFAULT_TEXT
    engine = engine or settings.voice_engine()
    if engine not in settings.VOICE_ENGINE_IDS:
        raise PreviewError("모르는 목소리 엔진입니다")
    if engine == "supertonic":
        if not modelfiles.ok("supertonic"):
            raise PreviewError("로컬 목소리 모델이 없습니다 — 아래 '모델 파일'에서 먼저 받으세요", 409)
        sid = settings.supertonic_sid() if sid is None else sid
        if not isinstance(sid, int) or isinstance(sid, bool) or sid not in settings.SUPERTONIC_SIDS:
            raise PreviewError("목소리 번호는 0~9 입니다")
        key = f"supertonic|{sid}|{text}"
    else:
        from recap_shorts.voice import gemini
        model = model or settings.model("tts")
        voice = voice or settings.gemini_voice()
        if voice not in settings.GEMINI_VOICE_IDS:
            raise PreviewError("모르는 Gemini 목소리입니다")
        if not settings.valid_model_id("tts", model):
            raise PreviewError("모델 이름에 쓸 수 없는 글자가 있습니다")
        if not settings.get_key("gemini_api_key"):
            raise PreviewError("Gemini API 키가 없습니다 — 위 Gemini 칸에 키를 넣으세요", 409)
        key = f"gemini|{model}|{voice}|{gemini.DEFAULT_STYLE}|{text}"
    out = Path(work_root) / "_preview" / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".wav")
    if out.is_file() and out.stat().st_size > 44:
        return out, True
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.wav")
    try:
        if engine == "supertonic":
            import soundfile as sf

            from recap_shorts.voice import tts
            x, sr = tts.synth_one(text, sid=sid)
            sf.write(str(tmp), x, sr, subtype="PCM_16")
        else:
            from recap_shorts.voice import gemini
            from recap_shorts.voice.spoken import to_spoken
            try:
                gemini.call(to_spoken(text), tmp, voice=voice, style=gemini.DEFAULT_STYLE, model=model)
            except RuntimeError as e:       # 화면에는 한국어 한 줄만(영문 API 원문은 보이지 않게)
                from recap_shorts import errors
                msg = ("Gemini API 키가 맞지 않습니다 — 위 Gemini 칸의 키를 확인하세요"
                       if errors.kind_of(e) == errors.BAD_KEY else errors.screen_text(e, "미리듣기를 만들지 못했습니다"))
                raise PreviewError(msg, 502) from e
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out, False
