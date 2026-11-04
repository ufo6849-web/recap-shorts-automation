# PLAN 2026-11-04 — OpenRouter 엔진 (대본 Claude Opus 5.5 · 읽기 Gemini 3.8 Flash) + 리눅스 글 캡처

브랜치 `feat/openrouter-engine` (워크트리 `../recap-shorts-wt-openrouter`, 기준 main `3fe163d`)

## 목표

Gemini 키 없이 OpenRouter 키 하나로 영상을 끝까지 만든다. 대본은 Claude Opus 5.5, 그림 읽기·가림·짤 판단은 Gemini 3.8 Flash 를 쓴다(2026-11-04 비교에서 각 역할 1등). 칼리(리눅스)에서 글 캡처가 돌게 한다.

근거: 같은 글로 4개 모델을 비교했다(스크래치패드 `cmp/`).
- 대본: Opus 5.5 가 가장 구체적이었다.
- 가림 상자·댓글 거르기: Gemini 3.8 Flash 가 가장 정확했다. Claude 계열은 얼굴 일부를 덜 가렸다.

## 범위 밖 (다음 단계)

- 목소리 엔진은 이미 있는 Supertonic 을 쓴다. Qwen3-TTS 정리는 다음에 한다.
- AI 그림의 나비 ComfyUI 연결은 다음 단계다. 이번 검증에서는 AI 그림을 끈다(`image_engine=off`).
- Gemini TTS·Gemini 이미지를 OpenRouter 로 옮기지 않는다. 두 기능은 Gemini 키가 있을 때만 쓴다.
- Windows 전용 부분(데스크톱 창, 드라이브 보관, CapCut 등록, 맑은 고딕 뉴스 카드)은 다루지 않는다.

## 데이터 모양

모델 이름 하나가 어디로 갈지를 정하는 경로 표다(`llm/gemini_api.py`의 순수 함수).

```
route(model, has_gemini_key, has_openrouter_key) -> ("gemini", model) | ("openrouter", or_model)
```

| 모델 이름 | Gemini 키 | OpenRouter 키 | 결과 |
|---|---|---|---|
| `vendor/name` (예: `anthropic/claude-opus-5.5`) | 상관없음 | 있음 | `("openrouter", "anthropic/claude-opus-5.5")` |
| `vendor/name` | 상관없음 | 없음 | NO_KEY 오류: "이 모델은 OpenRouter 키가 필요합니다" |
| `gemini-…` (슬래시 없음) | 있음 | 상관없음 | `("gemini", model)`. 지금과 같다 |
| `gemini-…` | 없음 | 있음 | `("openrouter", "google/" + model)` |
| `gemini-…` | 없음 | 없음 | NO_KEY 오류(지금 문구) |

공급자를 고르는 설정 칸은 따로 두지 않는다. 모델 이름과 가진 키가 경로를 정한다.

## 파일별 변경

### W1 — 백엔드 (한 작업자: settings·서버·LLM 이 계약을 나눠 가짐)

1. `recap_shorts/settings.py`
   - 키와 그룹을 추가한다.
     - `KEYS` 에 `{"name": "openrouter_api_key", "env": "RECAP_OPENROUTER_API_KEY", "label": "OpenRouter API 키", "group": "openrouter", "required": False}` 를 넣는다.
     - `GROUPS` 에 openrouter 그룹을 넣는다. 설명은 "대본·그림 읽기를 OpenRouter 로(Claude 등). Gemini 키가 없으면 Gemini 모델도 이 키로 부릅니다", 주소는 `https://openrouter.ai/settings/keys` 이다.
   - 대본 모델 이름에 `vendor/model` 꼴을 허용한다.
     - `script` 역할만 바꾼다. 다른 역할은 지금 정규식을 그대로 쓴다.
     - 슬래시 하나, 소문자·숫자·`.-:` 만, 100자 이하로 제한한다.
   - `llm_ready()` 를 추가한다. Gemini 키나 OpenRouter 키가 있으면 참이다.
   - `public_view()["ready"]` 의 조건을 "`llm_ready()` 이면서, 목소리 엔진이 gemini 이면 Gemini 키도 있음"으로 바꾼다.
2. `recap_shorts/llm/gemini_api.py`
   - 경로 표 `route()` 를 추가하고, `_post` 가 이 표를 보고 나눠 보낸다.
   - `_post_openrouter(or_model, body)` 를 추가한다. Gemini 요청을 OpenRouter `chat/completions` 형식으로 바꾸고, 응답은 다시 Gemini 응답 모양으로 돌려준다. 그래서 `_text_of`, `generate_text`, `read_images` 는 그대로 둔다.
     - 글은 `{"type":"text"}`, 그림은 `{"type":"image_url","image_url":{"url":"data:<mime>;base64,…"}}` 로 바꾼다.
     - `responseMimeType=application/json` 이면 `response_format={"type":"json_object"}` 를 보낸다. 400 으로 이 옵션을 거절하면 한 번만 옵션을 빼고 다시 보낸다.
     - `thinkingLevel` 은 `reasoning.effort` 로 옮긴다. `temperature` 는 `anthropic/` 모델에는 보내지 않는다(Claude 5.5 가 거절한다).
     - `max_tokens` 는 32000 이다.
     - 사용량은 `usage.log("openrouter", model=or_model, prompt_tokens=…, output_tokens=…, reasoning_tokens=…, cost=…, images=…)` 로 남긴다. 응답의 `usage.cost` 를 그대로 기록한다.
     - 재시도와 오류 분류는 지금 `_post` 규칙을 따른다. 429와 5xx 는 2회 다시 시도하고, 401·403 은 BAD_KEY, 402 는 QUOTA("OpenRouter 잔액·한도"), 연결 실패는 NET 이다.
   - `_openrouter_key()` 를 추가한다. 키 값은 오류 문구·로그에 넣지 않는다.
3. `recap_shorts/llm/gemini_models.py`
   - `BUILTIN["script"]` 에 `("anthropic/claude-opus-5.5", "Claude Opus 5.5 · OpenRouter")` 와 `("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5 · OpenRouter")` 를 넣는다.
   - 기본값은 그대로 `gemini-4-argon` 이다. Opus 5.5 는 설정 화면에서 고른다.
4. `recap_shorts/app/server.py`
   - `has_gemini_key()` 를 `has_llm_key()` 로 바꾸고 `settings.llm_ready()` 를 쓴다. 작업 만들기와 곁가지 작업(`require_key`)의 문을 이것으로 연다. `NO_KEY` 문구도 "Gemini 또는 OpenRouter 키" 로 바꾼다.
   - `/api/health` 의 `gemini_key` 항목은 둘 중 하나라도 있으면 통과로 한다. Gemini 키가 없고 OpenRouter 키만 있으면 "OpenRouter 키로 부름(Gemini 키 없음)" 이라고 보인다. 키가 틀렸다는 판정은 지금처럼 한다.
   - `/api/settings/test` 에 `service="openrouter"` 를 추가한다. `GET https://openrouter.ai/api/v1/key` 를 불러 200 이면 연결됨, 401·403 이면 틀린 키(`bad_keys`), 429 이면 한도로 본다.
   - 대본 다듬기의 키 검사도 `has_llm_key()` 로 바꾼다.
5. `scripts/cost_estimate.py`: kind `openrouter` 는 기록된 `cost` 를 그대로 더한다.
6. 테스트(새 파일 `tests/test_openrouter.py`, `tests/test_settings.py` 보강)
   - `route()` 표의 다섯 줄을 모두 시험한다.
   - 요청 변환을 시험한다: 글·그림·JSON 모드·thinking·temperature 생략.
   - 응답 변환과 사용량 기록을 시험한다.
   - 401 → BAD_KEY, 402 → QUOTA, 429 다시 시도, response_format 400 다시 시도를 시험한다.
   - 설정을 시험한다: 새 키, 대본 모델 슬래시 허용, 다른 역할은 거부, `ready` 규칙.
   - 서버를 시험한다: OpenRouter 키만 있을 때 작업 만들기 허용, health 문구, settings test openrouter(requests 는 가짜로).

### W2 — 글 캡처·화면 문구 (W1 과 파일이 겹치지 않음)

1. `recap_shorts/collect/browser.py`: `edge_browser()` 는 Edge 채널, Edge 경로(Windows), Playwright 기본 Chromium 순서로 띄운다. 기본 Chromium 은 리눅스·맥 용이다. 함수 이름은 그대로 둔다(부르는 곳 그대로).
2. `recap_shorts/collect/community.py:30`: `chrome_channel`/`channel="msedge"` 는 `os.name == "nt"` 일 때만 넘긴다.
3. `web/src/App.tsx`: 상단 상태 칩 이름을 `Gemini` 에서 `AI 키` 로 바꾼다.
4. `web/src/pages/Home.tsx`: 안내 문구를 "영상을 만들려면 Gemini 또는 OpenRouter API 키가 필요합니다" 로 바꾼다. 틀린 키 문구도 같이 바꾼다.
5. 테스트: Playwright 를 가짜로 만들어 띄우는 순서를 시험한다(Edge 실패 → 기본 Chromium).

## 실행 순서

1. 회의를 연다. 이 계획을 Sol(codex gpt-5.6-sol)과 Haiku 에게 동시에 보여 의견을 받고, Opus 가 판정해 계획을 고친다.
2. 구현한다. W1 과 W2 를 Haiku 5.5 max 작업자 둘에게 각자 워크트리로 맡겨 동시에 진행한다.
3. 검증한다. 구현한 작업자가 아닌 쪽에서 다시 돌린다.
   - pytest 전체: 기준선(main)은 804 통과, 5 실패다. 실패 5개는 Windows 전용(맑은 고딕 2, 데스크톱 2, 인트로 픽셀 1)이다. 새 실패가 없어야 한다.
   - `npm run typecheck && npm run build`
4. Codex 리뷰를 받는다. Sol 에게 diff 를 보내고, 지적이 맞는지 확인해 고친 뒤 고친 부분을 다시 Sol 에게 보낸다.
5. 실제 영상 1편을 만든다.
   - 설정은 OpenRouter 키, 대본 `anthropic/claude-opus-5.5`, 목소리 Supertonic, AI 그림 끔, Gemini 키 없음이다.
   - 모델 파일은 `scripts/setup_models.py --all` 로 받는다(약 292MB).
   - 실제 브라우저로 설정 화면과 첫 화면을 1440·375 폭에서 확인한다.
   - 글 주소 하나로 확인 단계 네 곳을 API 로 넘기며 `out/final.mp4` 까지 만든다.
   - 영상은 ffprobe 로 1080×1920, 30fps, 30~40초인지 본다.
   - `work/_usage.jsonl` 의 openrouter 기록으로 실제 모델과 비용을 확인한다.
6. 보고한다. 병합은 사용자 승인을 따로 받는다.

## 검증 기준

- Gemini 키 없이 OpenRouter 키만으로 첫 화면의 만들기가 켜지고, 영상 1편이 끝까지 나온다.
- 대본 단계 기록에 `anthropic/claude-opus-5.5` 가, 읽기·가림 기록에 `google/gemini-3.8-flash` 가 찍힌다.
- Gemini 키가 있는 기존 사용자는 동작이 같아야 한다. 경로 표의 세 번째 줄이 이를 지키고, 기존 테스트가 통과해야 한다.
- 키 값이 응답·로그·오류 문구에 나오지 않는다.
