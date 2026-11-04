"""앱 안 설정 파일(config/settings.local.json): API 키, AI 그림 엔진, 쓸 모델(대본·목소리·이미지), 목소리 엔진, 효과음·짤 폴더.

- 키·모델 읽기 순서: 환경변수(앞뒤 공백 뺀 값이 비어 있지 않으면) → 설정 파일 → 없음("") 또는 기본 모델.
- 파일 쓰기는 임시 파일 + os.replace, 프로세스 안 threading.Lock(반쯤 쓴 파일이 남지 않게).
- 키 값은 어디에도 출력·로그·응답에 넣지 않는다(예외 메시지 포함).
- 경로는 부를 때마다 본다: 환경변수 RECAP_SETTINGS_FILE → config/settings.local.json.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = REPO_ROOT / "config" / "settings.local.json"

# 화면에 나오는 순서 그대로(계약서 2절)
KEYS = [
    {"name": "gemini_api_key", "env": "RECAP_GEMINI_API_KEY", "label": "Gemini API 키", "group": "gemini",
     "required": False},
    {"name": "openrouter_api_key", "env": "RECAP_OPENROUTER_API_KEY", "label": "OpenRouter API 키",
     "group": "openrouter", "required": False},
    {"name": "cf_account_id", "env": "RECAP_CF_ACCOUNT_ID", "label": "Cloudflare 계정 ID", "group": "cloudflare",
     "required": False},
    {"name": "cf_api_token", "env": "RECAP_CF_API_TOKEN", "label": "Cloudflare API 토큰", "group": "cloudflare",
     "required": False},
]

GROUPS = [
    {"id": "gemini", "title": "Gemini",
     "desc": "대본·그림 읽기·목소리·AI 그림. 이 키 하나로 전부 됩니다",
     "url": "https://aistudio.google.com/apikey", "need": "둘 중 하나 필수"},
    {"id": "openrouter", "title": "OpenRouter",
     "desc": "대본(Claude 등)·그림 읽기. Gemini 키가 없으면 Gemini 모델도 이 키로 부릅니다. 목소리는 로컬 엔진을 쓰세요",
     "url": "https://openrouter.ai/settings/keys", "need": "둘 중 하나 필수"},
    {"id": "cloudflare", "title": "Cloudflare (Clef)",
     "desc": "실시간 베스트 주제 분류, AI 그림 글자 점검, 완성 영상 점검. 없으면 이 점검만 건너뜁니다.",
     "url": "https://dash.cloudflare.com/profile/api-tokens"},
]

IMAGE_ENGINES = [
    {"id": "local", "label": "로컬 이미지 서버 (사용 한도에 걸리면 Gemini 로 대신)"},
    {"id": "gemini", "label": "Gemini 이미지 (유료, 장당 약 0.07달러)"},
    {"id": "off", "label": "AI 그림 안 만듦 — 글 캡처·사진만"},
]
ENGINE_IDS = [e["id"] for e in IMAGE_ENGINES]
# 고른 적이 없거나 모르는 값(예전의 "auto" 포함)이면 기본: Gemini 키가 있으면 gemini, 없으면 off(image_engine 참고).

# 쓸 모델(설정 화면 '쓸 모델'). 기본값은 각 모듈의 기본과 같다(llm/gemini_api.TEXT_MODEL, voice/gemini.DEFAULT_MODEL,
# images/gemini_image — tests/test_settings.py 가 맞는지 본다). 환경변수가 있으면 그게 먼저이고 화면에서 못 바꾼다.
MODEL_ROLES = [
    {"role": "script", "label": "대본", "env": "RECAP_SCRIPT_MODEL", "default": "gemini-4-argon"},
    {"role": "tts", "label": "목소리", "env": "RECAP_TTS_MODEL", "default": "gemini-3.8-flash-tts"},
    {"role": "image", "label": "이미지", "env": "RECAP_GEMINI_IMAGE_MODEL", "default": "gemini-3.1-flash-image"},
]
ROLE_IDS = [r["role"] for r in MODEL_ROLES]
MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,79}$")
VENDOR_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*/[a-z0-9][a-z0-9.\-:]*$")     # OpenRouter 꼴(대본만, 100자 이하)
VOICE_ENGINES = [
    {"id": "gemini", "label": "Gemini 목소리"},
    {"id": "supertonic", "label": "로컬 목소리 (Supertonic) — 이 컴퓨터에서 만듦, 무료"},
]
VOICE_ENGINE_IDS = [e["id"] for e in VOICE_ENGINES]
SUPERTONIC_SIDS = list(range(10))
# Gemini 미리 만든 목소리 30개와 공식 설명(ai.google.dev/gemini-api/docs/speech-generation, 2026-09-30 확인).
# 기본 Charon 은 voice/gemini.DEFAULT_VOICE 와 같다.
GEMINI_VOICES = [
    ("Charon", "정보 전달형"), ("Kore", "단단한"), ("Orus", "단단한"), ("Alnilam", "단단한"), ("Sadaltager", "지적인"),
    ("Rasalgethi", "정보 전달형"), ("Iapetus", "또렷한"), ("Erinome", "또렷한"), ("Schedar", "고른"), ("Achird", "친근한"),
    ("Sulafat", "따뜻한"), ("Vindemiatrix", "부드러운"), ("Achernar", "부드러운"), ("Algieba", "매끄러운"), ("Despina", "매끄러운"),
    ("Callirrhoe", "여유로운"), ("Umbriel", "여유로운"), ("Zubenelgenubi", "캐주얼한"), ("Puck", "경쾌한"), ("Laomedeia", "경쾌한"),
    ("Sadachbia", "활기찬"), ("Fenrir", "흥분한"), ("Zephyr", "밝은"), ("Autonoe", "밝은"), ("Leda", "젊은"), ("Aoede", "산뜻한"),
    ("Enceladus", "숨소리 섞인"), ("Algenib", "거친"), ("Gacrux", "성숙한"), ("Pulcherrima", "적극적인"),
]
GEMINI_VOICE_IDS = [n for n, _ in GEMINI_VOICES]
DEFAULT_GEMINI_VOICE = "Charon"
VOICE_FIELDS = ("engine", "gemini_voice", "sid")
# 효과음 폴더(설정 화면 '효과음'). 비어 있으면 config/fx.json 의 sfx.libraries(기본 꾸러미)를 쓴다.
# fill_missing = 고른 폴더에 없는 종류는 기본 꾸러미에서 채움(기본 켬).
SFX_FIELDS = ("dirs", "fill_missing")
SFX_MAX_DIRS = 8
# 짤 폴더(설정 화면 '짤'). 비어 있으면 config/fx.json 의 memes.library(기본 짤 꾸러미, 없으면 짤 없음).
MEME_FIELDS = ("dirs",)
MEME_MAX_DIRS = 8

_LOCK = threading.Lock()


class SettingsError(Exception):
    """설정 저장이 안 되는 이유. status 는 REST 코드(400: 값이 틀림, 409: 환경변수가 정함)."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def path() -> Path:
    return Path(os.environ.get("RECAP_SETTINGS_FILE") or DEFAULT_FILE)


def _spec(name: str) -> dict:
    for k in KEYS:
        if k["name"] == name:
            return k
    raise KeyError(name)


def _env_value(name: str) -> str:
    return str(os.environ.get(_spec(name)["env"], "")).strip()


def _load(p: Path | str | None = None) -> dict:
    """설정 파일 내용. 없거나 깨졌으면 빈 꼴. p 를 주면 그 파일(시험용)."""
    p = path() if p is None else Path(p)
    empty = {"keys": {}, "image_engine": None, "models": {}, "voice": {}, "sfx": {}, "memes": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    keys, models, voice, sfx, memes = (data.get(k) for k in ("keys", "models", "voice", "sfx", "memes"))
    return {"keys": keys if isinstance(keys, dict) else {}, "image_engine": data.get("image_engine"),
            "models": models if isinstance(models, dict) else {}, "voice": voice if isinstance(voice, dict) else {},
            "sfx": sfx if isinstance(sfx, dict) else {}, "memes": memes if isinstance(memes, dict) else {}}


def file_keys(p: Path | str | None = None) -> dict[str, str]:
    """설정 파일에 적힌 키만(환경변수 무시)."""
    out = {}
    for name, v in _load(p)["keys"].items():
        if isinstance(v, str) and v.strip():
            out[name] = v.strip()
    return out


def _file_value(name: str) -> str:
    v = _load()["keys"].get(name)
    return str(v).strip() if v not in (None, "") else ""


def get_key(name: str) -> str:
    """환경변수 → 설정 파일 → "". 이름이 목록에 없으면 KeyError."""
    if name not in [k["name"] for k in KEYS]:
        raise KeyError(name)
    return _env_value(name) or _file_value(name)


def key_source(name: str) -> str | None:
    """"env" | "file" | None."""
    if name not in [k["name"] for k in KEYS]:
        raise KeyError(name)
    if _env_value(name):
        return "env"
    return "file" if _file_value(name) else None


def image_engine() -> str:
    """설정 파일의 값. 없거나 모르는 값이면 Gemini 키가 있을 때 gemini, 없을 때 off(키 없이 그림 엔진이 돌지 않게)."""
    v = _load()["image_engine"]
    if v in ENGINE_IDS:
        return v
    return "gemini" if get_key("gemini_api_key") else "off"


def _role(role: str) -> dict:
    for r in MODEL_ROLES:
        if r["role"] == role:
            return r
    raise KeyError(role)


def _model_env(role: str) -> str:
    return str(os.environ.get(_role(role)["env"], "")).strip()


def _model_file(role: str) -> str:
    v = _load()["models"].get(role)
    v = v.strip() if isinstance(v, str) else ""
    return v if valid_model_id(role, v) else ""


def model(role: str) -> str:
    """script | tts | image 에 쓸 Gemini 모델: 환경변수 → 설정 파일 → 기본."""
    return _model_env(role) or _model_file(role) or _role(role)["default"]


def model_source(role: str) -> str | None:
    """"env" | "file" | None(기본값)."""
    if _model_env(role):
        return "env"
    return "file" if _model_file(role) else None


def voice_engine() -> str:
    """목소리 엔진: gemini | supertonic. 설정 파일의 값, 없거나 모르는 값이면 Gemini 키가 있을 때 gemini, 없을 때 supertonic."""
    v = _load()["voice"].get("engine")
    if v in VOICE_ENGINE_IDS:
        return v
    return "gemini" if get_key("gemini_api_key") else "supertonic"


def gemini_voice() -> str:
    """Gemini 목소리 이름(설정 파일, 없거나 모르는 이름이면 Charon)."""
    v = _load()["voice"].get("gemini_voice")
    return v if v in GEMINI_VOICE_IDS else DEFAULT_GEMINI_VOICE


def supertonic_sid() -> int:
    """로컬 목소리 번호 0~9: 환경변수 RECAP_TTS_SID → 설정 파일 → 0."""
    env = str(os.environ.get("RECAP_TTS_SID", "")).strip()
    if env.isdigit():
        return int(env)
    v = _load()["voice"].get("sid")
    return v if isinstance(v, int) and v in SUPERTONIC_SIDS else 0


def sfx_dirs() -> list[str]:
    """설정 화면에서 고른 효과음 폴더(절대 경로, 고른 순서). 없으면 [] — 그때는 config/fx.json 의 기본 꾸러미."""
    v = _load()["sfx"].get("dirs")
    out: list[str] = []
    for d in v if isinstance(v, list) else []:
        if isinstance(d, str) and d.strip() and d.strip() not in out:
            out.append(d.strip())
    return out[:SFX_MAX_DIRS]


def sfx_fill_missing() -> bool:
    """고른 폴더에 없는 종류를 기본 꾸러미에서 채울지(설정 파일, 없으면 True)."""
    v = _load()["sfx"].get("fill_missing")
    return v if isinstance(v, bool) else True


def _sfx_dir_path(d: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(d.strip().strip('"'))))
    return (p if p.is_absolute() else REPO_ROOT / p).resolve()


def _saved_paths(dirs: list[str]) -> set[str]:
    out = set()
    for d in dirs:
        try:
            out.add(str(_sfx_dir_path(d)))
        except (OSError, ValueError):
            pass
    return out


def _check_sfx(sfx: dict | None) -> list[str] | None:
    """효과음 설정 확인. 준 dirs 를 절대 경로 목록으로 바꿔 돌려준다(dirs 를 안 줬으면 None)."""
    if sfx is None:
        return None
    if not isinstance(sfx, dict):
        raise SettingsError("sfx 는 객체여야 합니다")
    if "fill_missing" in sfx and not isinstance(sfx["fill_missing"], bool):
        raise SettingsError("fill_missing 은 true 또는 false 입니다")
    if "dirs" not in sfx:
        return None
    dirs = sfx["dirs"]
    if dirs is None:
        return []
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        raise SettingsError("dirs 는 폴더 경로 목록이어야 합니다")
    from recap_shorts.fx import sfx as S
    saved = _saved_paths(sfx_dirs())
    out: list[str] = []
    for d in dirs:
        if not d.strip():
            continue
        if len(d) > 1024:
            raise SettingsError("폴더 경로가 너무 깁니다")
        p = _sfx_dir_path(d)
        if str(p) in saved:      # 이미 목록에 있던 폴더는 다시 보지 않는다 — 앱 밖에서 지운 폴더가 있어도 다른 폴더를 뺄 수 있게
            if str(p) not in out:
                out.append(str(p))
            continue
        if not p.is_dir():
            raise SettingsError(f"폴더가 없습니다: {d.strip()}")
        try:
            items, _, _ = S.folder_items(p, durations=False)
        except ValueError as e:
            raise SettingsError(str(e))
        if not items:
            raise SettingsError(f"이 폴더에서 효과음을 찾지 못했습니다: {d.strip()} — 파일 이름을 종류_번호(예: pop_01.wav)로 하거나 "
                                "종류 이름 폴더(예: pop\\)에 넣으세요")
        if str(p) not in out:
            out.append(str(p))
    if len(out) > SFX_MAX_DIRS:
        raise SettingsError(f"효과음 폴더는 {SFX_MAX_DIRS}개까지입니다")
    return out


def _pack_view(p: Path) -> tuple[str | None, dict | None]:
    """폴더가 분석 꾸러미(fx/library.py)면 (원본 폴더 경로, {"kind","id"})."""
    from recap_shorts.fx import library
    pk = library.pack_of(p)
    return (pk["source_dir"], {"kind": pk["kind"], "id": pk["id"]}) if pk else (None, None)


def _sfx_folder_view(p: Path, shown: str) -> dict:
    from recap_shorts.fx import sfx as S
    err = None
    try:
        items, skipped, has_mf = S.folder_items(p, durations=False)
    except ValueError as e:
        items, skipped, has_mf, err = [], 0, True, str(e)
    types: dict[str, int] = {}
    for it in items:
        types[it["type"]] = types.get(it["type"], 0) + 1
    label, pack = _pack_view(p)
    return {"path": shown, "label": label, "pack": pack, "exists": p.is_dir(), "count": len(items), "skipped": skipped,
            "manifest": has_mf, "types": dict(sorted(types.items())), "error": err}


def meme_dirs() -> list[str]:
    """설정 화면에서 고른 짤 폴더(절대 경로, 고른 순서). 없으면 [] — 그때는 config/fx.json 의 memes.library."""
    v = _load()["memes"].get("dirs")
    out: list[str] = []
    for d in v if isinstance(v, list) else []:
        if isinstance(d, str) and d.strip() and d.strip() not in out:
            out.append(d.strip())
    return out[:MEME_MAX_DIRS]


def _check_memes(memes: dict | None) -> list[str] | None:
    """짤 설정 확인. 준 dirs 를 절대 경로 목록으로(안 줬으면 None). 폴더에 manifest.json 이나 그림이 있어야 한다."""
    if memes is None:
        return None
    if not isinstance(memes, dict):
        raise SettingsError("memes 는 객체여야 합니다")
    if "dirs" not in memes:
        return None
    dirs = memes["dirs"]
    if dirs is None:
        return []
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        raise SettingsError("dirs 는 폴더 경로 목록이어야 합니다")
    from recap_shorts.fx import memes as M
    saved = _saved_paths(meme_dirs())
    out: list[str] = []
    for d in dirs:
        if not d.strip():
            continue
        if len(d) > 1024:
            raise SettingsError("폴더 경로가 너무 깁니다")
        p = _sfx_dir_path(d)
        if str(p) in saved:      # 효과음과 같음 — 이미 있던 폴더는 없어졌어도 그대로 둔다(화면에 '폴더가 없습니다'로 보임)
            if str(p) not in out:
                out.append(str(p))
            continue
        if not p.is_dir():
            raise SettingsError(f"폴더가 없습니다: {d.strip()}")
        items, note = M.load_folder(p)
        if not items:
            raise SettingsError(f"이 폴더에서 짤을 찾지 못했습니다: {d.strip()}" + (f" — {note}" if note else ""))
        if str(p) not in out:
            out.append(str(p))
    if len(out) > MEME_MAX_DIRS:
        raise SettingsError(f"짤 폴더는 {MEME_MAX_DIRS}개까지입니다")
    return out


def _meme_folder_view(p: Path, shown: str) -> dict:
    from recap_shorts.fx import memes as M
    items, note = M.load_folder(p) if p.is_dir() else ([], "")
    label, pack = _pack_view(p)
    return {"path": shown, "label": label, "pack": pack, "exists": p.is_dir(), "count": len(items),
            "manifest": (p / "manifest.json").is_file(), "error": note if (p.is_dir() and not items and note) else None}


def memes_view() -> dict:
    """설정 화면의 '짤' 묶음: 고른 폴더, 기본 짤 꾸러미(없을 수 있음)."""
    from recap_shorts.fx.config import load_config, resolve_path
    dirs = meme_dirs()
    lib = str((load_config().get("memes") or {}).get("library") or "")
    default = [_meme_folder_view(resolve_path(lib), lib)] if lib else []
    using = "custom" if dirs else ("default" if any(f["count"] for f in default) else "none")
    return {"using": using, "dirs": [_meme_folder_view(Path(d), d) for d in dirs], "default": default,
            "max_dirs": MEME_MAX_DIRS}


def sfx_view() -> dict:
    """설정 화면의 '효과음' 묶음: 고른 폴더와 기본 꾸러미 각각의 효과음 수·종류."""
    from recap_shorts.fx import sfx as S
    from recap_shorts.fx.config import load_config, resolve_path
    dirs = sfx_dirs()
    default = [str(d) for d in ((load_config().get("sfx") or {}).get("libraries") or [])]
    return {"using": "custom" if dirs else "default", "fill_missing": sfx_fill_missing(),
            "dirs": [_sfx_folder_view(Path(d), d) for d in dirs],
            "default": [_sfx_folder_view(resolve_path(d), d) for d in default],
            "known_types": S.KNOWN_TYPES, "max_dirs": SFX_MAX_DIRS}


def valid_model_id(role: str, v) -> bool:
    """모델 이름 꼴. 앞뒤 공백은 부르는 쪽이 뺀다. script 만 vendor/name(OpenRouter 의 Claude 등)을 받는다."""
    if not isinstance(v, str):
        return False
    if role == "script" and len(v) <= 100 and VENDOR_MODEL_RE.match(v):
        return True
    return bool(MODEL_ID_RE.match(v))


def _check_models(models: dict | None) -> None:
    if models is None:
        return
    if not isinstance(models, dict):
        raise SettingsError("models 는 객체여야 합니다")
    for role, v in models.items():
        if role not in ROLE_IDS:
            raise SettingsError(f"모르는 모델 자리입니다: {role}")
        if v in (None, ""):
            continue
        if not isinstance(v, str) or not valid_model_id(role, v.strip()):
            raise SettingsError("모델 이름에 쓸 수 없는 글자가 있습니다")
        if _model_env(role):
            raise SettingsError(f"환경변수 {_role(role)['env']} 로 정해져 있어 여기서 바꿀 수 없습니다", status=409)


def _check_voice(voice: dict | None) -> None:
    if voice is None:
        return
    if not isinstance(voice, dict):
        raise SettingsError("voice 는 객체여야 합니다")
    if "engine" in voice:
        if voice["engine"] not in VOICE_ENGINE_IDS:
            raise SettingsError("모르는 목소리 엔진입니다")
        if voice["engine"] == "supertonic":
            from recap_shorts import modelfiles
            if not modelfiles.ok("supertonic"):
                raise SettingsError("로컬 목소리 모델이 없습니다 — 아래 '모델 파일'에서 먼저 받으세요")
    if "gemini_voice" in voice and voice["gemini_voice"] not in GEMINI_VOICE_IDS:
        raise SettingsError("모르는 Gemini 목소리입니다")
    if "sid" in voice:
        if not isinstance(voice["sid"], int) or isinstance(voice["sid"], bool) or voice["sid"] not in SUPERTONIC_SIDS:
            raise SettingsError("목소리 번호는 0~9 입니다")
        if str(os.environ.get("RECAP_TTS_SID", "")).strip():
            raise SettingsError("환경변수 RECAP_TTS_SID 로 정해져 있어 여기서 바꿀 수 없습니다", status=409)


def _valid_value(v: object) -> bool:
    """앞뒤 공백 뺀 뒤 1~512자, 공백·제어문자 없음, 출력 가능한 ASCII 만(0x21~0x7E)."""
    if not isinstance(v, str):
        return False
    s = v.strip()
    return 1 <= len(s) <= 512 and all("!" <= ch <= "~" for ch in s)


def save(keys: dict | None = None, image_engine: str | None = None, models: dict | None = None,
         voice: dict | None = None, sfx: dict | None = None, memes: dict | None = None) -> dict:
    """keys·models 에 없는 이름은 그대로 두고, "" 또는 null 은 설정 파일에서 지운다(모델은 기본으로 돌아감).
    voice = {"engine": gemini|supertonic, "sid": 0~9} 중 준 것만 바꾼다.
    sfx = {"dirs": [폴더…] (빈 목록·null = 기본 꾸러미로), "fill_missing": bool} 중 준 것만 바꾼다.
    memes = {"dirs": [폴더…] (빈 목록·null = 기본 짤로)}.
    환경변수가 정하고 있는 값을 바꾸려 하면 SettingsError(409), 값·이름이 틀리면 SettingsError(400)."""
    if keys is not None and not isinstance(keys, dict):
        raise SettingsError("keys 는 객체여야 합니다")
    if image_engine is not None and image_engine not in ENGINE_IDS:
        raise SettingsError("모르는 image_engine 입니다")
    _check_models(models)
    _check_voice(voice)
    sfx_dirs_new = _check_sfx(sfx)
    meme_dirs_new = _check_memes(memes)
    known = {k["name"] for k in KEYS}
    for name in keys or {}:
        if name not in known:
            raise SettingsError(f"모르는 키 이름입니다: {name}")
    for name, v in (keys or {}).items():
        if v not in (None, "") and not _valid_value(v):
            raise SettingsError("키에 쓸 수 없는 글자가 있습니다")
    for name, v in (keys or {}).items():
        if v not in (None, "") and _env_value(name):
            raise SettingsError(f"환경변수 {_spec(name)['env']} 로 정해져 있어 여기서 바꿀 수 없습니다", status=409)

    with _LOCK:
        data = _load()
        cur = dict(data["keys"])
        for name, v in (keys or {}).items():
            if v in (None, ""):
                cur.pop(name, None)
            else:
                cur[name] = v.strip()
        if image_engine is not None:
            data["image_engine"] = image_engine
        mods = {r: v for r, v in data["models"].items() if r in ROLE_IDS}
        for role, v in (models or {}).items():
            if v in (None, ""):
                mods.pop(role, None)
            else:
                mods[role] = v.strip()
        vo = {k: v for k, v in data["voice"].items() if k in VOICE_FIELDS}
        vo.update({k: v for k, v in (voice or {}).items() if k in VOICE_FIELDS})
        sx = {k: v for k, v in data["sfx"].items() if k in SFX_FIELDS}
        if sfx_dirs_new is not None:
            if sfx_dirs_new:
                sx["dirs"] = sfx_dirs_new
            else:
                sx.pop("dirs", None)
        if isinstance((sfx or {}).get("fill_missing"), bool):
            sx["fill_missing"] = sfx["fill_missing"]
        mm = {k: v for k, v in data["memes"].items() if k in MEME_FIELDS}
        if meme_dirs_new is not None:
            if meme_dirs_new:
                mm["dirs"] = meme_dirs_new
            else:
                mm.pop("dirs", None)
        out = {"keys": cur}
        if data["image_engine"] is not None:
            out["image_engine"] = data["image_engine"]
        if mods:
            out["models"] = mods
        if vo:
            out["voice"] = vo
        if sx:
            out["sfx"] = sx
        if mm:
            out["memes"] = mm
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return public_view()


def _masked(v: str) -> str | None:
    if not v:
        return None
    return ("••••" + v[-4:]) if len(v) >= 16 else "••••"


def _display_path(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return p.name


def llm_ready() -> bool:
    """글을 쓰는 모델(대본·주제 분류)을 부를 키가 있는가: Gemini 나 OpenRouter 중 하나면 된다."""
    return bool(get_key("gemini_api_key") or get_key("openrouter_api_key"))


def readiness() -> dict:
    """영상 만들기 준비 상태: 대본 모델과 그림 읽기가 갈 곳이 있고, 목소리가 준비돼 있는가.
    reason 은 처음 걸린 조건의 한국어 문장, via 는 화면 표시용(gemini | openrouter | mixed | None)."""
    from recap_shorts import modelfiles
    from recap_shorts.llm import gemini_api as G
    no_key = "Gemini 또는 OpenRouter API 키가 필요합니다"
    has_g, has_o = bool(get_key("gemini_api_key")), bool(get_key("openrouter_api_key"))
    script = model("script")
    reason, via = None, set()
    try:
        via.add(G.route(script, has_g, has_o)[0])
    except G.GeminiApiError:
        reason = f"대본 모델({script})은 OpenRouter 키가 필요합니다" if "/" in script else no_key
    try:
        via.add(G.route(G.VISION_MODEL, has_g, has_o)[0])
    except G.GeminiApiError:
        reason = reason or no_key
    voice = voice_engine()
    if voice == "gemini" and has_g:
        via.add("gemini")
    if reason is None and voice == "gemini" and not has_g:
        reason = "목소리가 Gemini 인데 Gemini 키가 없습니다 — 설정에서 로컬 목소리를 고르세요"
    if reason is None and voice == "supertonic" and not modelfiles.ok("supertonic"):   # 대본 비용을 쓴 뒤 목소리에서 멈추지 않게
        reason = "로컬 목소리(Supertonic) 모델이 없습니다 — 설정 → 모델 파일에서 먼저 받으세요"
    shown = None if not via else ("mixed" if len(via) > 1 else next(iter(via)))
    return {"ok": reason is None, "reason": reason, "via": shown}


def public_view() -> dict:
    """GET /api/settings 응답 꼴. 키 값은 들어가지 않는다."""
    from recap_shorts import modelfiles
    from recap_shorts.images import provider

    rd = readiness()
    keys = []
    for k in KEYS:
        v = get_key(k["name"])
        keys.append({"name": k["name"], "label": k["label"], "group": k["group"], "required": k["required"],
                     "env": k["env"], "set": bool(v), "source": key_source(k["name"]), "masked": _masked(v)})
    return {"file": _display_path(path()),
            "ready": rd["ok"], "ready_reason": rd["reason"],
            "keys": keys,
            "groups": GROUPS,
            "image_engine": image_engine(),
            "image_engines": IMAGE_ENGINES,
            "image_server": {"url": provider.local_url(), "up": provider.local_server_up()},
            "model_roles": [{"role": r["role"], "label": r["label"], "env": r["env"], "default": r["default"],
                             "value": model(r["role"]), "source": model_source(r["role"])} for r in MODEL_ROLES],
            "voice": {"engine": voice_engine(), "engines": VOICE_ENGINES,
                      "gemini_voice": gemini_voice(), "gemini_voices": [{"id": n, "label": f"{n} — {d}"} for n, d in GEMINI_VOICES],
                      "sid": supertonic_sid(),
                      "sid_env": bool(str(os.environ.get("RECAP_TTS_SID", "")).strip()), "sids": SUPERTONIC_SIDS},
            "sfx": sfx_view(),
            "memes": memes_view(),
            "models": modelfiles.status(),
            "setup_cmd": modelfiles.setup_cmd()}
