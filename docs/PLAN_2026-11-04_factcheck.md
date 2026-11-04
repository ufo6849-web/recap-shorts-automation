# 계획: 정보성 글 사실 확인과 뉴스 카드 (2026-11-04)

브랜치 `feat/factcheck`(워크트리 `recap-shorts-wt-factcheck`)는 `feat/openrouter-engine`(PR #1, fe50e82) 위에서 시작한다.

개정 1 (2026-11-04 19:55): 설계 회의(Sol·Haiku) 결과를 반영했다. 맨 아래 9절 "회의록"과 10절 "개정 사항"이 앞 절보다 우선한다.

## 1. 목표

사용자 요청 원문은 다음과 같다.
- "실제 뉴스와 검증부분을 추가할수있나? 뉴스 화면도 넣으면 좋겠는데.. 실제 있는일은 가짜뉴스인지도 판별하면 좋겠다. 그냥 일반 사항은 거치지 않아도 좋지만 이런정보성글은 가짜뉴스 부분을 검증이있어야 할꺼 같은데?"
- "권장으로가자. 시사는 권장으로 가야한다."

정리하면 다음과 같다.
- 정보성·시사 글(실제 사건, 정책, 기업, 수치, 건강·과학 주장)은 영상으로 만들기 전에 실제 보도와 대조해 사실 여부를 판정한다.
- 사실 아님 판정이 있으면 사람이 확인하기 전에는 다음 단계로 넘어가지 않는다. 시사 글에는 이 검증을 끌 수 없다.
- 일반 글(경험담, 유머, 감상, 잡담)은 검증 없이 지나간다.
- 판정 근거 기사는 우리가 직접 그린 뉴스 카드(제목·언론사·날짜, 로고나 기사 화면은 쓰지 않음)로 영상에 넣고, 설명 문구에 출처로 적는다.

## 2. 지금 상태 (직접 확인함)

- **사실 가드:** `script/guard.py:215` `run_guard`는 대본이 원문(`source_text`)과 `topic["facts"]`에 근거하는지만 본다. 원문 자체가 사실인지는 보지 않는다.
- **비어 있는 자리:** `topic["facts"]`는 프롬프트(`script/prompt.py:99-103`)와 가드(`guard.py:218-219`)가 이미 읽는다. 그런데 저장소에 이 값을 쓰는 코드가 없다. 검증된 사실을 넣을 자리가 이미 있는 셈이다.
- **읽기 단계:** `pipeline.py:546` `step_read`가 `topic.json`을 쓴다. 첨부 그림에서 읽은 글자도 `source_text`에 들어간다(`pipeline.py:566`, `:592`).
- **승인:** 서버의 `/continue`(`server.py:759`)는 키 준비만 보고 첫 `waiting` 단계를 넘긴다. 내용 때문에 막는 검사는 서버에 없다.
- **뉴스 카드:** `collect/news_card.py`와 `collect/news_api.py`는 CLI(`collect/__main__.py:58`)에서만 쓴다. 웹 파이프라인의 자료 목록은 `assets/assets.json`이고, 항목 종류는 `cap`, `ai`뿐이다(`pipeline.py:855`, `:1114`). `arrange.kind_of`(`images/arrange.py:272`)도 `news`를 모른다.
- **글꼴:** `news_card.py`는 윈도 맑은 고딕만 찾는다(`:11-25`). 렌더는 저장소에 든 Pretendard를 먼저 쓴다(`render/layout.py:55-80`).
- **출처 문구:** `pipeline.py:1370` `make_description`에는 뉴스 출처 자리가 없다.
- **화면:** 읽기 확인은 `web/src/pages/Job.tsx`의 `NowCard`(`:44`)와 `TopicCard`(`:164`)에서 한다. 다크 모드는 `web/src/index.css:23`(`prefers-color-scheme`)로 이미 있다.

## 3. 엔진 시험 결과 (2026-11-04, 앱 밖 시험, 합계 $1.30)

시험 글은 진짜 글(데모 "물 탄 우유"), 일부러 만든 가짜 글, 일반 잡담 글이다.

| 방식 | 진짜 글 | 가짜 글 | 비용·시간(글당) |
|---|---|---|---|
| 분류: Gemini 3.8 Flash | 정보성 | 정보성 | 약 $0.001, 3초 |
| 판정: Google News RSS 제목만 + Opus | 확인 불가 3 | 확인 불가 2 (못 가림) | $0.02 |
| 판정: Opus + 기본 웹검색 | 주의 | 중단 | $0.15~0.85, 17~28초 |
| 판정: Flash + 웹검색 | 주의("검토"를 사실로 봄) | 중단 | $0.03~0.08, 12~154초 |
| **판정: Opus + Exa 5건** | **주의(검토 단계라고 짚음)** | **중단(두 주장 모두 사실 아님)** | **$0.06~0.08, 13~20초** |

일반 글은 분류에서 걸러져 판정을 부르지 않았다. Opus + Exa가 근거로 든 링크는 모두 실제로 열렸다(조선비즈, 연합뉴스 등).

**결정:** 분류는 그림 읽기 모델(`gemini-3.8-flash`)로 한다. 판정은 `anthropic/claude-opus-5.5`에 OpenRouter 웹검색(`engine: exa`, `max_results: 5`)을 붙여 한다.

## 4. 설계

### 4.1 흐름

`step_read`가 `topic.json`을 쓴 뒤 "사실 확인" 하위 단계를 돈다. 새 단계를 STEPS에 넣지 않는다. 읽기 확인 화면이 이미 사람이 멈춰 보는 곳이고, 대본 비용을 쓰기 전에 멈출 수 있기 때문이다.

1. **분류:** `source_text`와 제목을 Flash에 넣는다. 답은 `{kind: "info"|"general", reason, claims: [{claim, query, source_quote}]}`이다.
   - 주장은 최대 3개다.
   - `source_quote`는 `source_text` 안에 그대로 있는 구절이어야 한다. 없으면 버린다.
2. **일반 글:** `status: "skipped"`로 끝낸다.
3. **정보성 글:** 주장마다 웹검색으로 판정한다(호출 1번에 주장 전부). 답은 `{results: [{id, verdict, why, fact, sources: [{url, title, date}]}], overall_why}`이다.
   - `verdict`는 `true` / `partly` / `false` / `unverified` 중 하나다.
   - `fact`는 보도로 확인된 내용을 한 문장으로 적은 것이다. `partly`면 바로잡은 문장이다.
4. **출처 검증 (결정적):** 모델이 든 URL 가운데 실제 검색 인용(OpenRouter `annotations[].url_citation`, Gemini `groundingMetadata`)에 있는 것만 남긴다. `true`·`partly`인데 남은 출처가 없으면 `unverified`로 내린다.
5. **전체 판정 (코드가 정함):**
   - `false`가 하나라도 있으면 `stop`이다.
   - `unverified`나 `partly`가 있으면 `warn`이다.
   - 모두 `true`면 `pass`다.
   - 호출이 실패하면 `failed`다.
6. **`topic["facts"]` 채우기:** `true`·`partly`인 주장의 `fact`를 `{id: "F1", text: fact, evidence: fact, source: url}`로 넣는다.
   - 가드는 이 문장도 근거로 인정한다(`guard.py:218`).
   - 프롬프트에 규칙 두 가지를 더한다. "원문과 facts가 다르면 facts를 따른다", "사실 아님 주장은 사실처럼 말하지 않는다".
7. **가드 보강:** `false` 판정 주장의 `source_quote`와 겹치는 evidence를 가진 대본 문장은 지우고 flag를 남긴다. 원문에 거짓 주장이 남아 있어도 대본에는 못 들어가게 하려는 것이다.

### 4.2 멈춤 규칙

- `stop`과 `failed`는 서버가 `/continue`를 409로 막는다. 사람이 "직접 확인했음"(메모 필수)을 누르거나, 글을 고친 뒤 다시 확인해 `stop`이 풀려야 넘어간다.
- `warn`도 `auto_steps`에 `read`가 있어도 반드시 멈춘다. 이때 이어 하기는 막지 않는다.
- `pass`와 `skipped`는 지금처럼 동작한다.
- `PUT topic`으로 글을 고치면 `factcheck.stale = true`가 된다. 고치기 전 판정이 `stop`·`failed`였으면 다시 확인하기 전까지 막는다.

### 4.3 키와 엔진 경로

- 판정 모델은 OpenRouter 키가 있으면 `anthropic/claude-opus-5.5` + web plugin이다. Gemini 키만 있으면 `gemini-4-argon` + `tools: [{google_search: {}}]`이다.
- `_post`는 Gemini 모양 본문을 받는다. 그래서 `tools.google_search`를 OpenRouter `plugins: [{id: "web", engine: "exa", max_results: 5}]`로 바꾸는 일을 어댑터(`_or_request`)가 맡는다. 응답의 `annotations`는 `groundingMetadata.groundingChunks[].web{uri,title}`로 바꾼다(`_gemini_shape`).
- 둘 다 없으면 앱이 원래 돌지 않는다(readiness). 대본 엔진이 `agy` CLI여도 판정은 위 경로로 `_post`를 쓴다.
- Gemini 키 경로는 볼트에 Gemini 키가 없어 실제 호출로 확인하지 못한다. 가짜 응답 시험으로만 확인하고, 그렇다고 결과에 적는다.

### 4.4 뉴스 카드

- `true`·`partly` 주장의 출처 기사로 카드를 그린다. 기사당 1장, 영상당 최대 2장이다.
- `news_card.py`는 `render.layout.load_font`(Pretendard)를 쓰게 바꿔 리눅스에서도 돌게 한다. 색은 영상 안에서 읽히게 다시 정한다(시안 승인 대상).
- `assets.json`에 `{kind: "cap", role: "news", file, fact_id, source_url, outlet, title, date}`를 더한다. `arrange`는 `fact_id`의 fact 문장을 evidence로 쓴 대본 문장에 이 카드를 우선 배치한다.
- 언론사 이름은 `config/outlets.json`(`news_api.outlet_for`)에서 찾고, 없으면 도메인을 쓴다. 날짜는 모델이 기사에서 읽은 값이고, 없으면 카드에 날짜를 빼고 그린다.
- 뉴스 사이트 페이지는 앱이 직접 열지 않는다. 기사 내용은 검색 제공자(Exa·Google)를 거쳐서만 본다.
- `make_description`에 "뉴스 출처: 언론사 · 제목 · URL" 줄을 더한다.

### 4.5 화면

- **제작 탭, 읽기 확인:** `TopicCard` 아래에 "사실 확인" 카드를 둔다.
  - 상태 배지는 통과·주의·중단·건너뜀(일반 글)·확인 실패·다시 확인 필요 중 하나다.
  - 주장마다 판정 칩, 주장, 이유, 출처(언론사 · 날짜 · 링크)를 보여 준다.
  - 단추는 "다시 확인"(약 $0.08), "직접 확인했음"(중단·실패일 때, 메모 입력), "이대로 계속"(중단이면 비활성)이다.
- **대본 편집·검수:** 판정이 `warn`이거나 직접 확인으로 넘긴 경우 경고 줄을 하나 띄운다(`ScriptEdit.issuesOf`, `Review.warnRows`).
- **장면 탭:** 뉴스 카드는 다른 그림처럼 보이고 "뉴스" 표시가 붙는다.
- 시안은 1440·375, 라이트·다크로 만들어 사용자 승인을 받은 뒤 구현한다.

### 4.6 데이터: `work/<job>/factcheck.json`

```json
{"version": 1, "status": "warn", "kind": "info", "reason": "...", "stale": false,
 "claims": [{"id": "C1", "claim": "...", "source_quote": "...", "verdict": "partly", "why": "...", "fact": "...",
             "sources": [{"url": "https://...", "title": "...", "outlet": "조선비즈", "date": "2026-11-02"}]}],
 "overall_why": "...", "override": null, "model": "anthropic/claude-opus-5.5", "search": "exa:5",
 "cost_usd": 0.07, "checked_at": "2026-11-04T19:40:00+09:00"}
```

- `override`는 `{at, note}`다. `GET /api/jobs/{id}` 응답에 `factcheck`로 싣는다.
- `POST /api/jobs/{id}/factcheck`는 다시 확인한다. 대본 이후가 끝났으면 `PUT topic`처럼 뒤 단계를 되돌린다.
- `POST /api/jobs/{id}/factcheck/override {note}`는 직접 확인으로 넘긴다.

## 5. 작업 나눔

- **W1 (백엔드, Haiku 5.5 max):**
  - 새 파일 `recap_shorts/factcheck.py`
  - `llm/gemini_api.py`(tools↔plugins, annotations↔grounding)
  - `pipeline.py`(step_read 하위 단계, auto_steps 강제 멈춤, 뉴스 카드 자료, description)
  - `script/prompt.py`, `script/guard.py`, `images/arrange.py`, `collect/news_card.py`
  - `app/server.py`(continue 차단, 다시 확인·직접 확인 API, view)
  - 시험: `tests/test_factcheck.py`, `tests/pipeline/test_factcheck_flow.py`
- **W2 (화면, Haiku 5.5 max):** `web/src/types.ts`, `api.ts`, `pages/Job.tsx`(FactCheckCard), `ScriptEdit.tsx`, `Review.tsx`, `mock/index.ts`
- 공유 계약은 4.6의 JSON과 API다. `server.py`는 W1만, `types.ts`는 W2만 고친다.

## 6. 검증 기준

1. `pytest`에서 새 실패가 0이어야 한다. 기준선은 Windows 전용 실패 5개다. `npm run typecheck`, `npm run build`도 통과해야 한다.
2. 단위 시험(유료 호출 없음):
   - 분류 `general`이면 판정을 부르지 않는다.
   - 인용에 없는 URL은 버리고, 출처가 없으면 `unverified`로 내린다.
   - `false`면 `/continue`가 409를 내고, override 뒤에는 넘어간다.
   - `warn`은 auto여도 멈춘다.
   - `stale`이 처리된다.
   - 가드가 거짓 주장 문장을 지운다.
   - `tools.google_search`↔`plugins` 변환이 맞다.
3. 실제 호출: 데모 글(정보성)과 가짜 글, 일반 글 1개로 끝까지 영상을 만든다.
   - 가짜 글은 읽기 확인에서 막혀야 한다.
   - 데모 글은 "검토" 단계를 대본이 바르게 말하고, 뉴스 카드가 장면에 들어가야 한다.
4. 실제 브라우저로 1440·375, 라이트·다크에서 확인하고 콘솔 오류 0이어야 한다.
5. Codex(Sol) 리뷰를 받고, 지적을 회수한 뒤 다시 받는다.

## 7. 범위 밖

- 거짓 글을 "팩트체크 영상"으로 바꿔 만들기.
- 기사 화면 캡처, 언론사 로고.
- 네이버 뉴스 API. 볼트의 `naver-apihub-*`는 검색 키가 아니었다(401).
- Clef 켜기(별도 질문).

## 8. 회의에서 물을 것

1. 새 단계 대신 `step_read` 하위 단계로 두는 것이 맞나.
2. `warn`을 사람 확인 필수로 할까, 경고만 할까.
3. 대본 문장에 거짓 주장이 들어간 것을 `source_quote` 겹침으로 잡는 방식이 충분한가.
4. 카드 날짜를 모델이 읽은 값으로 쓰는 위험.
5. Gemini 키만 있는 경로를 이번에 넣을지, OpenRouter 키를 필수로 할지.

## 9. 회의록 (2026-11-04 19:50)

참석은 Sol(codex gpt-5.6-sol high, 읽기 전용)과 Haiku 5.5(읽기 전용)였고, 판정은 Opus 5.5가 했다. 둘 다 "수정 후 진행"이었다. Sol은 높음 8, 중간 9, 낮음 1이었고, Haiku는 높음 3, 중간 4, 낮음 1이었다. 판정에 쓴 지적은 코드를 직접 열어 확인했다(`pipeline.py:1543`, `:1401`, `render/layout.py:31`, `images/sensitive.py:393`, `script/guard.py:266`, `server.py:1282`·`:1315`).

8절 질문에 대한 결론은 다음과 같다.
1. 화면에서는 읽기의 하위 단계로 둔다. 안에서는 입력 해시를 가진 독립 상태로 두고, 모든 진행 경로가 같은 검사 함수를 거친다.
2. `warn`은 자동 진행이어도 멈추고, "확인하고 계속"을 눌러야 넘어간다. 메모는 필요 없지만, 누른 시각과 입력 해시를 저장한다.
3. `source_quote` 겹침만으로는 부족하다. 제목·훅·마무리와 바꿔 쓴 문장으로 샌다. 대본 생성 뒤 검사를 하나 더 둔다(10절 5).
4. 카드 날짜는 구조화된 값(URL 경로의 날짜)만 쓰고, 없으면 뺀다. 제목은 검색 인용 메타데이터의 값을 쓴다.
5. 1차에서는 정보성 글 판정에 OpenRouter 키를 필수로 한다. Gemini 키만 있으면 정보성 글은 `failed`(이유 표시)로 멈추고, 직접 확인으로만 넘어간다.

| 지적 | 낸 쪽 | 결정 |
|---|---|---|
| 자동 진행(`auto_steps`)이 확인 단계를 서버 없이 넘긴다 | Sol·Haiku | 받아들임. Runner가 읽기 직후 공통 검사를 부른다 |
| `/continue` 말고도 rerun·render·capcut 입구가 있다 | Sol | 받아들임. 같은 검사를 쓴다. 기능 이전 작업은 경고와 함께 허용한다 |
| stale 차단이 이전 상태에 따라 달라진다 | Sol·Haiku | 받아들임. 입력 해시가 다르면 늘 다시 확인한다 |
| 재시작·파일 누락·구버전 정책이 없다 | Sol | 받아들임. 끝날 때만 원자적으로 쓰고, 누락은 "다시 확인 필요"로 본다 |
| 분류기가 일반으로 잘못 보면 검증이 빠진다 | Sol | 받아들임. `uncertain`은 정보성으로 보고, 건너뜀에도 "그래도 확인하기"를 둔다 |
| 제목·훅·마무리와 바꿔 쓴 문장으로 거짓 주장이 샌다 | Sol·Haiku | 받아들임. 방법은 바꿨다(10절 5) |
| 문장마다 `source_id`를 붙이는 스키마 변경 | Sol | 받아들이지 않음. 대본 뒤 검사가 같은 구멍을 더 적은 변경으로 막는다 |
| 인용에 있는 URL이라고 그 주장을 지지하는 것은 아니다 | Sol | 일부. 제목·날짜는 메타데이터만 쓰고, 출처 없는 판정은 `unverified`로 내린다. 문장 단위 결속은 범위 밖이고, 사람이 출처를 연다 |
| URL 안전성 | Sol | 받아들임 |
| 다시 확인 중복 실행 | Sol | 받아들임. Runner 대기열을 거치고, 작업이 바쁘면 409 |
| 사용량 이중 기록 | Sol | 받아들임. 어댑터만 기록한다 |
| 시간 초과가 길다 | Sol | 받아들임. 분류 30초, 판정 120초 |
| 원자적 쓰기와 다시 확인 뒤 정리 | Sol | 받아들임 |
| 뉴스 카드 배치 정보가 사라진다 | Sol·Haiku | 받아들임 |
| 카드 1080×600이 4:3 그림 칸에서 잘린다 | Sol | 받아들임. 1080×810으로 그린다 |
| cap 가리기 단추와 민감 검사가 카드에도 걸린다 | Sol | 받아들임 |
| 설명 문구가 늘 "대조하지 않았습니다"라고 쓴다 | Sol | 받아들임 |
| 새 API에 `local_only`가 없다 | Haiku | 받아들임 |
| 사람이 대본을 직접 고친 뒤 | Haiku | 일부. 결정적 가드만 돌리고 flag를 띄운다. 사람이 쓴 글은 막지 않는다 |
| 시험 픽스처와 상태×입구 표 시험 | Sol·Haiku | 받아들임 |

## 10. 개정 사항 (앞 절보다 우선)

1. **공통 검사 `factcheck_gate(job)`:** `pipeline.py`에 하나 둔다. 결과는 `clear` / `need_ack` / `blocked(이유)` 중 하나다.
   - Runner는 `read`가 끝나면 이 검사를 부른다. `clear`가 아니면 `auto_steps`여도 `waiting`으로 멈춘다(`pipeline.py:1543` 앞).
   - `continue_job`은 읽기를 넘길 때 `blocked`면 거부하고, `need_ack`면 확인 기록을 남긴다.
   - `rerun`, `render`, `capcut`도 같은 검사를 부른다. 기능 이전에 만든 작업(state에 `factcheck_policy`가 없음)은 허용하고, 검수 경고 줄을 띄운다.
2. **입력 해시:** `factcheck.json`에 제목+`source_text`의 해시를 저장한다. 지금 값과 다르면 이전 판정과 상관없이 `stale`이다. 이때 override·확인 기록·facts·뉴스 카드를 모두 무효로 한다.
3. **저장:** `factcheck.json`은 끝날 때만 임시 파일+교체로 쓴다. 실행 중 상태는 파일에 쓰지 않는다. 새 정책으로 읽은 작업에 파일이 없거나 깨졌으면 "다시 확인 필요"(blocked)다. `version`은 정책 버전이다.
4. **분류:** `kind`는 `info` / `general` / `uncertain`이고, `uncertain`은 정보성으로 처리한다. 프롬프트는 시사·정책·건강·기업·사건 신호가 있으면 정보성으로 보게 한다. 건너뜀 상태에도 "그래도 확인하기"를 둔다.
5. **대본 뒤 검사:** 대본을 만든 뒤 Flash를 한 번 더 부른다(약 $0.001).
   - 제목·훅·본문·마무리 가운데 `false`·`unverified` 주장을 사실처럼 말하는 곳이 있는지 묻는다.
   - `false`인 본문 문장은 지우고 flag를 남긴다. 제목·훅·마무리는 flag를 남긴다. flag가 있으면 대본 확인에서 자동 진행이어도 멈춘다.
   - 본문 문장의 `source_quote` 겹침 검사(결정적)는 그대로 둔다. 사람이 고친 대본(`PUT script`)에는 결정적 검사만 돈다.
6. **출처:**
   - 인용(`annotations.url_citation`)에 있는 URL만 남긴다. 제목은 인용 메타데이터에서 가져온다.
   - 날짜는 URL 경로의 `YYYY/MM/DD`나 `YYYYMMDD`가 있을 때만 쓴다.
   - URL 조건은 http(s), 공인 호스트, 사용자 정보 없음, 500자 이하다. 정규화한 값끼리 비교한다.
   - `true`·`partly`인데 남은 출처가 없으면 `unverified`로 내린다.
7. **키:** OpenRouter 키가 없으면 정보성 글은 `failed`이고, 이유는 "사실 확인에는 OpenRouter 키가 필요합니다"다.
8. **실행:**
   - "다시 확인"과 "그래도 확인하기"는 Runner 대기열을 거친다. 작업이 바쁘면 409를 낸다.
   - 시간 초과는 분류 30초, 판정 120초다.
   - 사용량은 어댑터만 기록한다. `factcheck.json`의 `cost_usd`는 응답의 usage 값만 옮긴다.
9. **뉴스 카드:**
   - 크기는 1080×810이다. 어두운 바탕 가운데 흰 카드에 뉴스 칩, 언론사, 날짜, 제목(최대 3줄), "사실 확인 근거 기사"를 넣는다.
   - 글꼴은 Pretendard다. 글꼴에 없는 글자(예: 〇)는 비슷한 글자로 바꾸거나 뺀다.
   - 자료는 `kind: "cap", role: "news"`이고 민감 검사에서 뺀다. 장면 편집기에서 가리기 단추를 숨긴다.
   - `sentence_infos`에 evidence를 싣는다. 카드는 그 fact 문장을 evidence로 쓴 문장에만 점수를 받고, 다른 문장에서는 0점이다.
10. **설명 문구:** 고지를 상태별로 바꾼다.
    - 통과: "아래 뉴스로 주요 주장을 확인했습니다."
    - 주의·직접 확인: "일부 주장은 보도로 확인되지 않았습니다."
    - 건너뜀: 지금 문구를 그대로 쓴다.
    - `[뉴스 출처]` 줄을 더한다.
11. **API:** `POST /api/jobs/{id}/factcheck`, `POST .../factcheck/override {note}`, `POST .../factcheck/ack`에 `local_only`를 붙인다.
12. **화면:** 주의 상태의 진행 단추는 "확인하고 계속"이다. 다시 확인이 필요한 상태(글 고침, 파일 없음)는 "글이 바뀌어 다시 확인해야 합니다"와 "다시 확인" 단추로 보여 준다.
13. **시험:** 상태(skipped·pass·warn·stop·failed·stale·missing) × 입구(auto·continue·rerun·render·capcut) 표로 매개변수 시험을 만든다. 카드 1080×810 렌더 프레임 시험도 둔다.

## 11. 구현 계약 (작업자 공용, 2026-11-04 20:05)

작업자는 넷이다. 파일 소유를 겹치지 않게 나눈다. 다른 작업자의 파일은 읽기만 한다.

| 작업자 | 소유 파일 |
|---|---|
| W1 핵심 | `recap_shorts/factcheck.py`(새), `recap_shorts/llm/gemini_api.py`, `tests/test_factcheck.py`(새) |
| W4 연결 | `recap_shorts/pipeline.py`, `recap_shorts/app/server.py`, `tests/pipeline/fakes.py`, `tests/pipeline/test_factcheck_flow.py`(새), `README.md` |
| W3 카드·배치·가드 | `recap_shorts/collect/news_card.py`, `recap_shorts/images/arrange.py`, `recap_shorts/images/sensitive.py`, `recap_shorts/script/guard.py`, `recap_shorts/script/prompt.py`, `tests/test_factcheck_render.py`(새), 이 파일들의 기존 시험 |
| W2 화면 | `web/src/types.ts`, `web/src/api.ts`, `web/src/pages/Job.tsx`, `web/src/pages/Review.tsx`, `web/src/pages/SceneEditor.tsx`, `web/src/mock/index.ts` |

### 11.1 `recap_shorts/factcheck.py` (W1이 만들고 W4가 부른다)

```python
POLICY_VERSION = 1
CLASSIFY_MODEL = "gemini-3.8-flash"          # route()가 Gemini 키 또는 OpenRouter로 보낸다
VERIFY_MODEL = "anthropic/claude-opus-5.5"   # OpenRouter 전용
class FactcheckError(RuntimeError): ...      # str(e)는 화면에 그대로 보일 한국어 문장

def input_hash(title: str, source_text: str) -> str                 # sha256(title + "\n" + source_text) hex
def classify(title: str, source_text: str, *, force: bool = False) -> dict
    # -> {"kind": "info"|"general"|"uncertain", "reason": str,
    #     "claims": [{"id": "C1", "claim": str, "query": str, "source_quote": str}]}  최대 3개
    # source_quote 가 source_text 에 (normalize_loose 기준) 없으면 그 주장을 버린다. force=True 면 일반 글이어도 주장을 뽑는다.
    # timeout 30초. 실패하면 FactcheckError.
def verify(claims: list[dict]) -> dict
    # OpenRouter 키가 없으면 FactcheckError("사실 확인에는 OpenRouter 키가 필요합니다. 설정에서 넣어 주세요.")
    # body 에 tools=[{"google_search": {}}] 를 넣어 _post(VERIFY_MODEL, body, timeout=120) 로 부른다.
    # -> {"results": [{"id","verdict": "true"|"partly"|"false"|"unverified","why": str,"fact": str|None,
    #                  "sources": [{"url","title","outlet","date": "YYYY-MM-DD"|None}]}],
    #     "overall_why": str, "model": VERIFY_MODEL, "search": "exa:5", "cost_usd": float|None}
    # 출처: 응답 groundingMetadata 의 인용 URL 에 있는 것만 남긴다(정규화 후 비교). 제목은 인용 메타데이터의 title.
    # 날짜는 date_from_url 만. outlet 은 collect.news_api.outlet_for. true/partly 인데 출처가 0개면 unverified 로 내린다.
def overall_status(results: list[dict]) -> str        # false 있으면 "stop", unverified/partly 있으면 "warn", 모두 true 면 "pass"
def build_record(title, source_text, cls: dict | None, ver: dict | None, *, error: str | None = None, forced: bool = False) -> dict
    # factcheck.json 내용(11.3). cls.kind == "general" 이고 forced 가 아니면 status "skipped". error 가 있으면 "failed".
def topic_additions(record: dict) -> dict
    # {"facts": [{"id":"F1","text":fact,"evidence":fact,"source":url,"claim_id":"C1"}],      # true·partly 이고 출처 있음
    #  "false_claims": [{"id":"C2","claim":str,"source_quote":str}],                          # false
    #  "unverified_claims": [{"id":"C3","claim":str}]}                                        # unverified
def gate(record: dict | None, title: str, source_text: str, policy: int | None) -> tuple[str, str | None]
    # policy is None(기능 이전 작업) -> ("clear", None)
    # record None / version 다름 -> ("blocked", "사실 확인 결과가 없습니다. 다시 확인해 주세요.")
    # input_hash 다름 -> ("blocked", "글이 바뀌어 사실 확인을 다시 해야 합니다.")
    # skipped, pass -> clear
    # warn -> ack.input_hash 가 맞으면 clear, 아니면 ("need_ack", "주의 판정을 확인하고 계속해 주세요.")
    # stop -> override.input_hash 가 맞으면 clear, 아니면 ("blocked", "사실 아님 판정이 있어 계속할 수 없습니다. 고치거나 직접 확인해 주세요.")
    # failed -> override 가 맞으면 clear, 아니면 ("blocked", record["error"] 또는 "사실 확인에 실패했습니다. 다시 확인해 주세요.")
def script_check(script: dict, record: dict) -> list[dict]
    # false·unverified 주장이 없으면 [] (호출 안 함). 있으면 CLASSIFY_MODEL 로 한 번 묻는다(timeout 30초).
    # -> [{"field": "title"|"hook"|"sentence"|"closing", "index": int|None, "claim_id": str, "verdict": "false"|"unverified", "text": str}]
def description_notice(record: dict | None) -> tuple[str, list[str]]
    # (고지 한 문장, ["언론사 · 제목 · URL", ...]) — 고지는 10절 10번 문구. record None/skipped 면 기존 문구
    # "내용은 위 커뮤니티 게시글 기준이며 공식 기록과 대조하지 않았습니다."
def save_record(job_dir: Path, record: dict) -> None    # 임시 파일 + os.replace
def load_record(job_dir: Path) -> dict | None            # 없음·깨짐·version 다름이면 None
def safe_url(url: str) -> str | None                     # http(s)만, 사용자 정보 없음, 사설·localhost 호스트 아님, 500자 이하, fragment 제거
def date_from_url(url: str) -> str | None                # /2026/11/02/ 또는 20261102 꼴 -> "2026-11-02", 아니면 None
```

`gemini_api.py`(W1)에서 할 일은 다음과 같다.
- `_or_request`는 body의 `tools`에 `google_search`가 있으면 `plugins: [{"id": "web", "engine": "exa", "max_results": 5}]`로 바꾼다.
- `_gemini_shape`는 응답의 `message.annotations[].url_citation{url,title}`을 `candidates[0].groundingMetadata.groundingChunks[].web{uri,title}`로 옮긴다. OpenRouter `usage.cost`는 `usageMetadata.costUsd`로 옮긴다.
- 사용량 기록은 지금처럼 어댑터만 한다.
- Gemini 직통 경로에서 `tools`는 그대로 보낸다. 1차에서는 `verify`가 OpenRouter만 쓰므로 실제로 쓰이지 않는다.

### 11.2 `topic.json` 추가 키 (W4가 쓰고 W3가 읽는다)

- `facts`, `false_claims`, `unverified_claims`: 모양은 `topic_additions`와 같다.
- 다시 확인할 때마다 세 키를 통째로 바꾼다.

### 11.3 `work/<job>/factcheck.json` (W1 모양, W4가 저장)

```json
{"version": 1, "input_hash": "…", "status": "skipped|pass|warn|stop|failed", "kind": "info|general|uncertain|null",
 "forced": false, "reason": "…", "claims": [{"id": "C1", "claim": "…", "source_quote": "…", "verdict": "true|partly|false|unverified",
 "why": "…", "fact": "…|null", "sources": [{"url": "…", "title": "…", "outlet": "…", "date": "YYYY-MM-DD|null"}]}],
 "overall_why": "…", "error": "…|null", "override": null, "ack": null,
 "model": "…", "search": "exa:5", "cost_usd": 0.07, "checked_at": "ISO8601"}
```

`override`는 `{"at", "note", "input_hash"}`이고, `ack`는 `{"at", "input_hash"}`이다.

### 11.4 파이프라인 (W4)

- **`step_read`:** `topic.json`을 쓴 뒤 하위 단계 "사실 확인"을 돈다.
  - `classify`를 부르고, 정보성이면 `verify`를 부른다. `FactcheckError`면 record를 `failed`로 만든다.
  - 그다음 `save_record`로 저장하고, `topic_additions`를 `topic.json`에 넣는다.
  - `job.state["factcheck_policy"] = POLICY_VERSION`을 저장한다.
  - 사실 확인이 실패해도 읽기 단계는 오류로 끝나지 않는다. `failed` 판정으로 멈춘다.
- **외부 호출:** `Deps`에 `factcheck_classify`, `factcheck_verify`, `factcheck_script_check`를 더하고, `FakeDeps`에 가짜를 넣는다.
- **Runner:** `read`가 끝나면 `gate`를 부르고, `clear`가 아니면 `auto_steps`여도 `waiting`으로 멈춘다(`pipeline.py:1543` 앞).
  - `script`가 끝나면 `script_check.json`의 `factcheck_flags`가 비어 있지 않을 때 `waiting`으로 멈춘다.
- **`continue_job`:** 첫 waiting이 `read`일 때 gate가 `blocked`면 `FactcheckBlocked(reason)`을 낸다. `need_ack`면 record에 `ack`를 저장하고 진행한다.
- **`step_script`:** `generate_script` 뒤에 `deps.factcheck_script_check`를 부른다.
  - 결과는 `script_check.json`의 `factcheck_flags`에 넣고, 같은 내용을 `flags`에 "사실 확인: …" 한국어 문장으로 더한다.
  - field가 `sentence`이고 verdict가 `false`면 그 문장을 지우고 `deleted`에 남긴다.
- **다시 확인:** `recheck_factcheck(runner, job, force)`는 `runner.submit(job, fn=...)`으로 대기열에 넣고, 실행 중이면 `Busy`다.
  - 대본 이후가 끝났으면 `reset_from("script")`로 되돌린다. 끝나면 `read`를 `waiting`으로 둔다.
  - 이전 뉴스 카드 파일과 assets 항목을 지운다.
- **뉴스 카드:** `step_assets`에서 `topic["facts"]`의 출처로 카드를 그린다(`news_card.draw_card`, 출처 기사마다 1장, 최대 2장).
  - 파일은 `assets/news/card_01.png`이다.
  - assets 항목은 `{"file", "kind": "cap", "role": "news", "status": "ok", "fact_id", "fact_text", "source_url", "outlet", "title", "date"}`이다.
- **`sentence_infos`:** 본문 문장의 `evidence`를 `"evidence"` 키로 싣는다.
- **`make_description`:** `description_notice(record)`로 고지와 `[뉴스 출처]` 줄을 넣는다.

### 11.5 서버 (W4)

- **`view(job)`:** 두 키를 더한다.
  - `"factcheck_policy": int|null`
  - `"factcheck"`: null이거나 record에서 `input_hash`를 뺀 값에 아래 키를 더한 것이다.
    - `"stale": bool`
    - `"gate": "clear"|"need_ack"|"blocked"`
    - `"gate_reason": str|null`
    - `"running": bool`
  - 정책 작업인데 record가 없고 read가 끝났으면 `{"status": "missing", "claims": [], "gate": "blocked", "gate_reason": …}`를 싣는다.
- **`POST /api/jobs/{id}/factcheck` `{"force": bool}`:** `local_only`다. read가 끝나지 않았거나 실행 중이면 409이고, 성공하면 view를 돌려준다.
- **`POST /api/jobs/{id}/factcheck/override` `{"note": "1~500자"}`:** `local_only`다. status가 `stop`·`failed`이고 stale이 아닐 때만 받고, 아니면 400이다.
- **`/continue`:** `FactcheckBlocked`를 409(사유 그대로)로 바꾼다.
- **`/rerun`, `/render`, `/capcut`:** `gate`가 `blocked`면 409다. 기능 이전 작업은 허용한다.

### 11.6 카드·배치·가드 (W3)

- **`news_card.draw_card(title: str, outlet: str, date: str | None, out: Path) -> Path`:** 1080×810 PNG다. 모양은 시안 `news_card_1080x810.png`을 따른다.
  - 어두운 세로 그라데이션 바탕 가운데 흰 둥근 카드(980×640)를 둔다.
  - 카드에는 노란 "뉴스" 칩, 언론사, "· 날짜"(있을 때만), 제목 최대 3줄(넘치면 …), 구분선, 왼쪽 초록 "사실 확인 근거 기사", 오른쪽 회색 "원문 링크는 설명란에"를 넣는다.
  - 글꼴은 `render.layout`의 Pretendard다. 글꼴에 없는 글자는 빼거나 비슷한 글자로 바꾼다(〇→○ 등).
  - 기존 CLI(`collect/__main__.py`)가 쓰는 `draw_news_card`, `fmt_date`, `FontNotFound`는 계속 동작해야 한다.
- **`arrange`:**
  - `kind_of`는 role이 `news`면 `"news"`를 돌려준다.
  - `heuristic_scores`에서 news 그림은 문장 `evidence`와 그림 `fact_text`가 `normalize_loose` 기준으로 같거나 한쪽이 다른 쪽을 품으면 0.95, 아니면 0.0이다.
  - `off_script`는 news를 빼지 않는다. `assign`·`plan_with_ai_cap`은 news를 cap처럼 센다.
- **`sensitive`:** role이 `news`면 검사하지 않는다.
- **`guard.run_guard`:** 본문 문장의 evidence가 `topic["false_claims"][].source_quote`와 겹치면(정규화 후 6자 이상이 한쪽이 다른 쪽에 들어감) 그 문장을 지운다. 지운 사실은 `deleted`에 남기고, `flags`에 "사실 확인: 사실 아님 주장(C2)과 같은 근거라 문장 n 삭제"를 더한다.
- **`prompt.build_prompt`:** facts·false_claims·unverified_claims가 있으면 블록과 규칙을 더한다.
  - 원문과 facts가 다르면 facts를 따른다.
  - false_claims의 주장은 사실처럼 말하지 않는다.
  - unverified_claims의 수치·주장은 쓰지 않는다.

### 11.7 화면 (W2)

- **타입:** `JobState`에 `factcheck_policy?: number | null`과 `factcheck?: FactcheckView | null`을 더한다. `AssetEntry`에 `role?`, `fact_id?`, `outlet?`, `title?`, `source_url?`, `date?`를 더한다.
- **`api.ts`:** `factcheck(id, force)`, `factcheckOverride(id, note)`를 더한다.
- **`Job.tsx`:**
  - 읽기 단계이고 `job.factcheck`가 있으면 NowCard 아래 TopicCard 위에 FactCheckCard를 둔다. 모양은 시안을 따른다.
  - 판정 칩은 고정 폭 60px이다. 출처는 링크(`target=_blank rel=noopener`)다.
  - "다시 확인"과 "그래도 확인하기"는 `api.factcheck`를 부른다.
  - "직접 확인했음"은 인라인 메모 칸(모달 금지)이고, 메모가 비면 단추를 끈다.
  - NowCard의 읽기 waiting은 gate에 따라 바뀐다.
    - `blocked`: gate_reason을 보여 주고 진행 단추를 끈다.
    - `need_ack`: 단추 이름을 "확인하고 계속"으로 바꾼다.
  - `running`이면 "확인 중" 표시를 하고 단추를 끈다.
  - `stale`·`missing`이면 "글이 바뀌어 다시 확인해야 합니다"와 "다시 확인" 단추를 보여 준다.
  - 비용은 `cost_usd`가 있을 때만 쓴다.
- **`Review.tsx` `warnRows`:** 세 경우에 경고 줄을 더한다.
  - warn을 확인하고 넘김: "사실 확인: 일부 주장이 보도로 확인되지 않았습니다"
  - override: "사실 확인: 직접 확인으로 넘김 — {note}"
  - `factcheck_policy`가 없고 read가 끝난 작업: "사실 확인 없이 만든 이전 작업"
- **`SceneEditor.tsx`:** role이 `news`면 칩을 "뉴스"로 하고 가리기 단추를 숨긴다.
- **`mock/index.ts`:** 세 상태(stop·warn·skipped) 예시 작업을 넣어 `?mock=1`에서 볼 수 있게 한다.
- 문구와 색은 시안(`ui_*.png`)과 같게 한다. 앱 토큰(`index.css`)만 쓴다.
