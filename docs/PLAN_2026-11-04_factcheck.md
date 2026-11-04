# 계획: 정보성 글 사실 확인과 뉴스 카드 (2026-11-04)

브랜치 `feat/factcheck`(워크트리 `recap-shorts-wt-factcheck`)는 `feat/openrouter-engine`(PR #1, fe50e82) 위에서 시작한다.

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
