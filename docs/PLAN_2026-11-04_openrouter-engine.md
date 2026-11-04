# PLAN 2026-11-04 — OpenRouter 엔진 (대본 Claude Opus 5.5 · 읽기 Gemini 3.8 Flash) + 리눅스 글 캡처

브랜치 `feat/openrouter-engine` (워크트리 `../recap-shorts-wt-openrouter`, 기준 main `3fe163d`)
개정 1 (2026-11-04 10:10): 설계 회의(Sol·Haiku) 결과를 반영했다. 맨 아래 "회의록" 참고.

## 목표

Gemini 키 없이 OpenRouter 키 하나로 영상을 끝까지 만든다. 대본은 Claude Opus 5.5, 그림 읽기·가림·짤 판단은 Gemini 3.8 Flash 를 쓴다(2026-11-04 비교에서 각 역할 1등). Gemini 키가 있는 기존 사용자는 동작이 바뀌지 않는다. 칼리(리눅스)에서 글 캡처가 돌게 한다.

근거: 같은 글로 4개 모델을 비교했다(스크래치패드 `cmp/`).
- 대본: Opus 5.5 가 가장 구체적이었다.
- 가림 상자·댓글 거르기: Gemini 3.8 Flash 가 가장 정확했다. Claude 계열은 얼굴 일부를 덜 가렸다.

## 범위 밖 (다음 단계)

- 목소리 엔진은 이미 있는 Supertonic 을 쓴다. Qwen3-TTS 정리는 다음에 한다.
- AI 그림의 나비 ComfyUI 연결은 다음 단계다. 이번 검증에서는 AI 그림을 끈다.
- Gemini TTS·Gemini 이미지 생성·Gemini 모델 목록 조회는 Gemini 키 전용으로 남긴다(OpenRouter 로 옮기지 않음).
- 틀린 Gemini 키와 맞는 OpenRouter 키를 같이 가진 경우를 자동으로 우회하지 않는다. health 가 틀린 키를 알려 주므로, 키를 고치거나 지우면 된다.
- Windows 전용 부분(데스크톱 창, 드라이브 보관, CapCut 등록, 맑은 고딕 뉴스 카드)은 다루지 않는다.

## 데이터 모양

### 1. 경로 표 — `llm/gemini_api.py` 의 순수 함수

```
route(model: str, has_gemini: bool, has_openrouter: bool) -> tuple[str, str]   # ("gemini"|"openrouter", 보낼 모델 이름)
```

| 모델 이름 | Gemini 키 | OpenRouter 키 | 결과 |
|---|---|---|---|
| `vendor/name` (예: `anthropic/claude-opus-5.5`) | 상관없음 | 있음 | `("openrouter", "anthropic/claude-opus-5.5")` |
| `vendor/name` | 상관없음 | 없음 | NO_KEY, provider=openrouter: "이 모델은 OpenRouter 키가 필요합니다" |
| 슬래시 없음 (`gemini-…`) | 있음 | 상관없음 | `("gemini", model)`. 지금과 같다 |
| 슬래시 없음 | 없음 | 있음 | `("openrouter", "google/" + model)` |
| 슬래시 없음 | 없음 | 없음 | NO_KEY, provider=gemini(지금 문구) |

- 키 입력은 시험이 바꿔 끼우는 `_key()`(Gemini)와 `_openrouter_key()` 가 비었는지로 정한다. `tests/test_errors.py:81-96`, `tests/pipeline/test_units.py:29-31,42` 가 `_key` 만 바꿔 끼운다.
- 공급자를 고르는 설정 칸은 두지 않는다.

### 2. 준비 상태 — `settings.readiness()` 하나를 모두가 쓴다

```
readiness() -> {"ok": bool, "reason": str | None, "via": "gemini" | "openrouter" | "mixed" | None}
```

`ok` 는 아래가 모두 참일 때 참이다.
- **대본 모델:** `model("script")` 가 경로 표로 갈 곳이 있다(오류가 아님).
- **그림 읽기:** `gemini-3.8-flash` 가 경로 표로 갈 곳이 있다. 곧 Gemini 키나 OpenRouter 키가 있다.
- **목소리:** `voice_engine()` 이 `gemini` 이면 Gemini 키가 있다.

`reason` 은 처음 걸린 조건의 한국어 문장이다.
- "Gemini 또는 OpenRouter API 키가 필요합니다"
- "대본 모델(anthropic/…)은 OpenRouter 키가 필요합니다"
- "목소리가 Gemini 인데 Gemini 키가 없습니다 — 설정에서 로컬 목소리를 고르세요"

`via` 는 화면 표시에만 쓴다.

부르는 곳은 다음과 같다.
- `public_view()` 의 `ready`/`ready_reason`
- `/api/health` 의 `gemini_key` 항목(이름은 화면 호환 때문에 그대로 둔다)
- 서버의 작업 만들기·이어 하기·다시 하기 문, 주제 분류 문

### 3. 키가 없을 때의 기본 엔진 (설정 파일에 값이 없을 때만)

- `voice_engine()`: Gemini 키가 있으면 `gemini`(지금과 같음), 없으면 `supertonic` 이다.
- `image_engine()`: Gemini 키가 있으면 `gemini`(지금과 같음), 없으면 `off` 이다.
- 사용자가 설정 화면에서 고른 값은 그대로 따른다.

## 파일별 변경

### W1 — 백엔드 (한 작업자. settings·서버·LLM 이 계약을 나눠 가짐)

1. `recap_shorts/settings.py`
   - 키와 그룹을 고친다.
     - `KEYS` 에 `openrouter_api_key`(env `RECAP_OPENROUTER_API_KEY`, group `openrouter`)를 넣는다. `gemini_api_key.required` 는 False 로 바꾼다.
     - `GROUPS` 에 openrouter 그룹을 넣는다. 주소는 `https://openrouter.ai/settings/keys` 이다.
     - gemini·openrouter 두 그룹에 `"need": "둘 중 하나 필수"` 를 붙인다.
     - 그룹 설명은 Gemini 가 "대본·그림 읽기·목소리·AI 그림. 이 키 하나로 전부 됩니다", OpenRouter 가 "대본(Claude 등)·그림 읽기. Gemini 키가 없으면 Gemini 모델도 이 키로 부릅니다. 목소리는 로컬 엔진을 쓰세요" 이다.
   - `valid_model_id(role, v)` 를 추가한다.
     - `script` 는 지금 정규식과 `vendor/name` 꼴(`^[a-z0-9][a-z0-9.\-]*/[a-z0-9][a-z0-9.\-:]*$`, 100자 이하)을 받는다. 다른 역할은 지금 정규식만 받는다.
     - `_check_models`, `_model_file`, `voice/preview.py:45` 가 이 함수를 쓴다.
   - `llm_ready()` 를 추가한다. Gemini 키나 OpenRouter 키가 있으면 참이다. `readiness()` 는 위 데이터 모양 2절, `voice_engine()`·`image_engine()` 의 기본값은 3절대로 바꾼다.
   - `public_view()` 에 `ready = readiness()["ok"]` 와 `ready_reason` 을 넣는다.
2. `recap_shorts/llm/gemini_api.py`
   - `route()` 와 `_openrouter_key()` 를 추가하고, `_post` 가 경로 표를 보고 나눠 보낸다. Gemini 쪽 URL·헤더·본문·재시도·사용량 기록은 바이트 단위로 지금과 같아야 한다.
   - `_post_openrouter(or_model, body, timeout, sleep)` 를 추가한다. Gemini 요청을 OpenRouter `https://openrouter.ai/api/v1/chat/completions` 형식으로 바꾸고, 응답은 다시 Gemini 응답 모양으로 돌려준다. 그래서 `_text_of`/`generate_text`/`read_images`/`analyze_sfx`/`analyze_meme`/`sensitive._is_block` 은 그대로 둔다.
     - parts 변환:
       - `text` 는 `{"type":"text"}` 로 바꾼다.
       - `inline_data` 중 `image/*` 는 `{"type":"image_url","image_url":{"url":"data:<mime>;base64,…"}}` 로 바꾼다.
       - `audio/*` 는 `{"type":"input_audio","input_audio":{"data":…, "format": "wav"|"mp3"}}` 로 바꾼다(`fx/analyze_sfx.py:300`).
       - 그 밖의 mime 은 ValueError 다.
     - 설정 변환:
       - `responseMimeType=application/json` 이면 `response_format={"type":"json_object"}` 를 보낸다.
       - `thinkingConfig.thinkingLevel` 은 `reasoning.effort` 로 옮긴다.
       - `temperature` 는 `anthropic/` 모델이 아닐 때만 보낸다.
       - `max_tokens` 는 32000, `usage` 는 `{"include": true}` 다.
     - 응답 정규화:
       - `choices[0].message.content` 가 문자열이면 그대로 쓴다. 배열이면 `type=="text"` 인 조각을 이어 붙인다.
       - `finish_reason` 이 `content_filter` 이거나 `message.refusal` 이 있으면 `{"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}` 를 돌려준다. 그러면 `_text_of` 가 blockReason 오류를 내고 `sensitive._is_block` 이 알아본다.
       - 내용이 비었으면 `finishReason` 을 같이 담는다.
     - 사용량 기록: 성공한 응답 한 번에 한 번만 `usage.log("openrouter", model=or_model, prompt_tokens, output_tokens, reasoning_tokens, cost, images=<그림 수>, audio=<소리 수>)` 를 남긴다. 숫자가 아니면 None 으로 둔다.
     - 다시 보내기:
       - 연결 실패, 429, 5xx, 200 인데 `choices` 가 없는 경우는 2회까지(5초, 10초 간격) 다시 보낸다.
       - 400 이고 오류 문구에 `response_format` 또는 `json` 이 들어 있을 때만, `response_format` 을 빼고 한 번 다시 보낸다. 다른 400 은 바로 실패다.
     - 오류 분류(`GeminiApiError` 에 `provider="openrouter"` 를 붙인다):
       - 401·403 은 BAD_KEY, 402 는 QUOTA("OpenRouter 잔액이나 키 한도가 모자랍니다"), 429 를 다 쓰면 QUOTA, 연결 실패는 NET, 그 밖은 HTTP 다.
       - 오류 문구는 `OpenRouter 실패(<모델>): HTTP …` 이고, 키 값은 넣지 않는다.
   - 기존 Gemini 오류에는 `provider="gemini"` 를 붙인다. 문구는 그대로 둔다.
3. `recap_shorts/errors.py`
   - `screen_text()` 가 예외의 `provider`(없으면 문구에 "OpenRouter" 가 있는지)를 보고 OpenRouter 문구를 낸다.
     - NO_KEY: "OpenRouter API 키가 없습니다 — …"
     - BAD_KEY: "OpenRouter API 키가 맞지 않습니다 — …"
     - QUOTA: "OpenRouter 잔액·한도가 모자랍니다 — openrouter.ai 에서 확인하세요"
     - NET: "OpenRouter 연결 실패 — …"
   - Gemini 문구와 기존 테스트(`tests/test_errors.py:29,31-32,96`, `tests/pipeline/test_error_screen.py:45,49`)는 그대로 둔다.
   - `kind_for_http(402)` 는 QUOTA 다.
4. `recap_shorts/llm/gemini_models.py`
   - 대본 추천에 `("anthropic/claude-opus-5.5", "Claude Opus 5.5 · OpenRouter")` 와 `("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5 · OpenRouter")` 를 넣는다.
   - `vendor/` 항목은 Gemini 목록 필터(`gemini_models.py:116-120`, `n in names`)와 관계없이 늘 보인다. Gemini 키가 있어도 마찬가지다.
   - 기본값은 그대로 `gemini-4-argon` 이다. OpenRouter 에 `google/gemini-4-argon` 이 있음을 확인했다(2026-11-04 목록).
5. `recap_shorts/fx/library.py:216-223` 의 `_gemini_ready()` 는 `settings.llm_ready()` 를 쓴다. 시험 중과 `RECAP_FX_NO_LLM` 일 때 끄는 규칙은 그대로 둔다.
6. `recap_shorts/app/server.py`
   - `has_gemini_key()` 를 쓰는 다섯 곳(428, 624, 634, 690, 706)을 정리한다.
     - 대본·그림 읽기 문은 `pipeline_ready()`(= `settings.readiness()`, FakeDeps 시험이면 통과)로 바꾼다.
     - Gemini 전용 곁가지는 `require_gemini_key(what)` 로 바꾼다. 목소리 다시 녹음(845, 엔진이 gemini 일 때만)과 AI 그림 다시 만들기(1072, 엔진이 gemini 일 때만)가 여기에 든다.
     - 대본 다듬기(800)와 주제 분류(428)는 `require_llm_key(what)` 를 쓴다.
     - 690/706 의 `GEMINI_STEPS` 문은 `readiness()` 를 쓴다.
   - `NO_KEY` 응답의 message 는 `readiness()["reason"]` 이다.
   - `/api/health` 의 `gemini_key` 는 `readiness()` 결과다.
     - detail 은 준비되면 "Gemini 키로 부름" / "OpenRouter 키로 부름(Gemini 키 없음)" / "Gemini·OpenRouter 키 있음", 아니면 reason 이다.
     - `bad` 는 실제로 쓰일 공급자의 키가 `bad_keys` 에 있을 때 참이다.
   - `/api/settings/test` 에 `service="openrouter"` 를 추가한다.
     - `GET https://openrouter.ai/api/v1/key`(Bearer) 를 부른다.
     - 200 은 연결됨(가능하면 남은 한도를 같이 보인다), 401·403 은 틀린 키(`bad_keys["openrouter"]`), 402 는 잔액, 429 는 한도로 본다.
     - 키 값은 `_scrub` 으로 지운다.
7. `recap_shorts/pipeline.py:1401-1407`: 설명 문구의 목소리 출처를 실제 엔진(`voice/timings.json` 의 engine)으로 적는다. Supertonic 인데 "Google Gemini TTS" 로 적지 않는다.
8. `scripts/cost_estimate.py:28-38`: kind `openrouter` 는 기록된 `cost` 를 더한다. 표 이름은 "OpenRouter (<모델>)" 이다.
9. 테스트. 기존 테스트는 계약이 바뀐 곳만 고친다.
   - 기존 테스트 수정:
     - `tests/app/test_settings_api.py:38`(키 목록), `tests/test_settings.py:102`(그룹 목록), `tests/test_settings.py:104-105`·`tests/app/test_api.py:45-46`(필드), `tests/app/test_settings_api.py:198`(문구), `tests/app/test_api.py:568-620`(키 없음 문).
     - 키 없음 문 테스트는 단순히 바꾸지 말고 공급자별 경계 테스트로 나눈다.
   - 새 `tests/test_openrouter.py`:
     - 경로 표 다섯 줄을 모두 시험한다.
     - 요청 변환을 시험한다: 글·그림·소리·JSON·thinking·temperature 생략(anthropic), 넣음(google).
     - 응답 정규화를 시험한다: 문자열, 배열, 빈 내용, content_filter, refusal.
     - 사용량을 한 번만 기록하는지 본다. response_format 400 으로 다시 보낼 때도 성공 1회만 기록한다.
     - 다른 400 은 다시 보내지 않는지 본다.
     - 오류를 시험한다: 401→BAD_KEY, 402→QUOTA, 429 다시 보낸 뒤 QUOTA, NET. 화면 문구는 OpenRouter 이고 키는 보이지 않아야 한다.
     - Gemini 경로가 바이트 단위로 그대로인지(URL·헤더·본문) 본다.
   - 설정 시험: 키 조합 넷(Gemini만/OpenRouter만/둘 다/없음) × 목소리 엔진 × 대본 모델(`anthropic/…`)로 readiness 를 시험한다. vendor 모델을 저장한 뒤 다시 읽어도 남는지, 다른 역할은 vendor 모델을 거부하는지, 기본 엔진 3절을 본다.
   - 서버 시험:
     - OpenRouter 키만 있으면 작업 만들기를 허용하는지, 목소리가 gemini 이면 막고 reason 을 내는지 본다.
     - OpenRouter 만 있을 때 Gemini 목소리 재녹음·Gemini 그림 재생성은 계속 막히는지 본다.
     - health 문구와 bad, settings test openrouter(requests 는 가짜)를 본다.
     - Gemini 키가 있어도 카탈로그에 Claude 가 남는지 본다.
   - 통합 시험(`tests/pipeline/test_openrouter_flow.py`):
     - 실제 `Deps` 로 `step_read` → `step_script` 를 돌린다. `requests.post` 만 가짜로 하고 OpenRouter 응답 모양을 돌려준다.
     - Gemini 키 없음, OpenRouter 키 있음, 대본 `anthropic/claude-opus-5.5` 로 설정한다.
     - 보낸 요청의 모델이 읽기 단계는 `google/gemini-3.8-flash`, 대본 단계는 `anthropic/claude-opus-5.5` 인지 본다.
   - `fx/library` 가 OpenRouter 만 있을 때 청취를 하는지(`_gemini_ready`) 본다.
   - cost_estimate 의 openrouter 합산을 본다.

### W2 — 글 캡처·화면 (W1 과 파일이 겹치지 않음)

화면이 기대는 API 계약은 W1 이 만든다.
- `GET /api/settings`:
  - `groups[].need?: string` 이 생긴다.
  - `keys[]` 에 `openrouter_api_key` 가 생기고, `gemini_api_key.required` 는 false 가 된다.
  - `ready: boolean`, `ready_reason: string | null` 이 생긴다.
- `GET /api/health`: `gemini_key` 의 의미가 "AI 키 준비"로 바뀐다. detail 은 위 문구이고, `bad` 는 그대로다.
- `POST /api/settings/test` 가 `service: "openrouter"` 를 받는다.

W2 가 고칠 파일:
1. `recap_shorts/collect/browser.py`: Edge 채널, (Windows 면) Edge 경로, Playwright 기본 Chromium 순서로 띄운다.
   - 세 번째 단계는 두 번째 except 안에 둔다.
   - 기본 Chromium 도 실패하면 "브라우저를 띄우지 못했습니다 — Edge 를 설치하거나 `python -m playwright install chromium` 을 실행하세요" 로 올린다.
   - 맨 위 설명에 "리눅스는 Playwright Chromium" 을 적는다.
2. `recap_shorts/collect/community.py:30`: `os.name == "nt"` 일 때만 `chrome_channel`/`channel="msedge"` 를 넘긴다.
3. `README.md`: "리눅스에서 실행" 짧은 절을 추가한다.
   - 명령은 `python3.11 -m venv`, `pip install -r …`, `python -m playwright install chromium`, `npm ci && npm run build`, `python scripts/setup_models.py --all`, `python -m recap_shorts.app` 이다.
   - "OpenRouter 키만으로 쓰기"(대본 Claude, 목소리 Supertonic, AI 그림 끔)를 같이 적는다.
4. `web/src/types.ts`: `SettingsGroup.need?: string` 과 `Settings.ready_reason?: string | null` 을 추가한다.
5. `web/src/pages/Settings.tsx`
   - 그룹 카드는 `g.need` 가 있으면 "필수" 배지 대신 그 문구를 보인다.
   - 저장 성공 안내(700행)는 gemini 와 openrouter 둘 다에 걸고, 문구는 "AI 키" 로 바꾼다.
   - 대본 모델 설명(751-755행)은 "대본 초안을 쓰고 다듬는 모델(Gemini 또는 OpenRouter)" 로 바꾼다.
6. `web/src/App.tsx`: 상태 칩 이름을 `Gemini` 에서 `AI 키` 로 바꾼다.
7. `web/src/pages/Home.tsx`
   - 키 안내 카드 제목은 "영상을 만들려면 Gemini 또는 OpenRouter API 키가 필요합니다" 다. 키는 있는데 준비가 안 됐으면 health detail(이유)을 그대로 보인다.
   - 틀린 키 문구는 "AI 키가 맞지 않습니다" 다.
8. `web/src/mock/index.ts`: 위 계약대로 mock 을 맞춘다(키·그룹·Claude 모델·ready·ready_reason·health·settings test openrouter·작업 만들기 문).
9. 테스트: `tests/collect/test_browser_fallback.py` 를 추가한다. 가짜 playwright 로 Edge 채널 실패, Edge 경로 실패, 기본 Chromium 성공 순서를 시험하고, 셋 다 실패하면 안내 문구가 나오는지 본다.

## 실행 순서

1. 회의를 마쳤다. 판정은 아래 회의록에 있다.
2. 구현한다. W1·W2 를 Haiku 5.5 max 작업자 둘에게 각자 워크트리(`feat/openrouter-engine` 에서 가지)로 맡겨 동시에 진행한다. 끝나면 Opus 가 두 가지를 이 브랜치에 합친다.
3. 검증한다. 구현한 작업자가 아닌 쪽에서 다시 돌린다.
   - pytest 전체: 기준선(main)은 804 통과, 5 실패다. 실패 5개는 Windows 전용(뉴스 카드 맑은 고딕 2, 데스크톱 2, 인트로 픽셀 1)이다. 새 실패가 없어야 한다.
   - `npm run typecheck && npm run build`
4. Codex 리뷰를 받는다. Sol 에게 diff 를 보내고, 지적이 맞는지 확인해 고친 뒤 고친 부분을 다시 Sol 에게 보낸다.
5. 실제 영상 1편을 만든다.
   - 설정은 OpenRouter 키(환경변수로 넣음), Gemini 키 없음이다.
   - 대본 모델은 설정 화면에서 Claude Opus 5.5 를 고른다. 목소리·AI 그림은 기본값이 Supertonic·끔이 되는지 본다.
   - 실제 브라우저로 설정 화면과 첫 화면을 1440·375 폭에서 확인한다.
   - 글 주소 하나로 확인 단계 네 곳을 API 로 넘기며 `out/final.mp4` 까지 만든다.
   - 영상은 ffprobe 로 1080×1920, 30fps, 30~40초인지 본다.
   - `work/_usage.jsonl` 의 openrouter 기록으로 모델과 비용을 확인한다.
   - 효과음 청취(소리 입력)를 OpenRouter 로 한 번 실제로 불러 본다(짧은 wav 하나).
6. 보고한다. 병합은 사용자 승인을 따로 받는다.

## 검증 기준

- Gemini 키 없이 OpenRouter 키만으로 첫 화면의 만들기가 켜지고, 영상 1편이 끝까지 나온다.
- 기록에 대본은 `anthropic/claude-opus-5.5`, 읽기·가림은 `google/gemini-3.8-flash` 가 찍힌다.
- Gemini 키가 있는 기존 사용자는 동작이 같다. Gemini 경로 바이트 동일 테스트와 기존 테스트로 확인한다.
- OpenRouter 오류는 화면에 OpenRouter 문구로 나오고, 키 값은 응답·로그·오류 문구에 나오지 않는다.

## 회의록 (2026-11-04)

참석은 Sol(codex gpt-5.6-sol high, 읽기 전용)과 Haiku 5.5 max(읽기 전용)이고, 판정은 Opus 5.5 가 했다. 두 의견 모두 "수정 후 진행"이었다. 받아들인 지적은 아래와 같다.

| 지적 | 낸 쪽 | 반영 |
|---|---|---|
| 키 검사를 하나로 합치면 Gemini 전용 곁가지(목소리 재녹음·그림 재생성)가 잘못 열린다 | Sol | `require_gemini_key` / `require_llm_key` 로 나눔 |
| 준비 판정이 화면 버튼·서버 문·health 에서 서로 다르다. 목소리 기본값이 gemini 라 대본 비용을 쓴 뒤 목소리에서 멈춘다 | Sol·Haiku | `readiness()` 하나로 통일, 키가 없을 때 기본 엔진을 정함 |
| 정규식이 읽을 때도 `anthropic/…` 를 지워 기본 모델로 되돌린다 | Sol | `valid_model_id(role, v)` |
| Gemini 키가 있으면 Claude 선택지가 목록에서 사라진다 | Sol·Haiku | vendor 항목은 늘 보이게 |
| OpenRouter 오류가 Gemini 문구로 보이고, 402 가 HTTP 로 분류된다 | Sol·Haiku | provider 별 문구, 402→QUOTA |
| 효과음 청취는 소리를 보낸다. 짤·효과음 분석이 Gemini 키만 보고 조용히 꺼진다 | Haiku | input_audio 변환, `_gemini_ready()` → `llm_ready()` |
| 민감 그림 판정이 Gemini 의 blockReason 문구에 기댄다 | Haiku | 거절을 promptFeedback.blockReason 으로 옮김 |
| 틀린 OpenRouter 키가 초록불로 보인다 | Haiku | `bad_keys["openrouter"]` |
| 응답 정규화·400 재시도 조건·사용량 중복 기록이 정의되지 않았다 | Sol | 2절 규칙 명시 |
| 브라우저 세 번째 단계는 중첩해야 하고, Chromium 설치 단계가 필요하다 | Haiku | 중첩, 안내 문구, README 리눅스 절 |
| mock·성공 배너·"필수" 배지·Gemini 문구 | Sol | W2 에 포함 |
| HTTP 만 가짜로 한 통합 시험이 필요하다 | Sol | `test_openrouter_flow.py` |
| 설명 문구가 Supertonic 이어도 Gemini TTS 로 적는다 | Sol | 실제 엔진으로 |

받아들이지 않은 것: 틀린 Gemini 키와 맞는 OpenRouter 키를 같이 가진 경우의 자동 우회(Sol). 키 상태는 서버 메모리에만 있어 경로 표가 볼 수 없고, health 가 이미 알려 주기 때문이다. 범위 밖에 적었다.
