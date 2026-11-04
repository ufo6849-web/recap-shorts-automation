# 소재 목록 가짜뉴스 체크 계획 (2026-11-05)

## 1. 목표

소재(인기글) 목록에서 만들 글을 고르기 전에 가짜뉴스를 걸러 낸다.

- 목록 위에 "가짜뉴스 체크" 단추를 둔다.
- 누르면 보이는 글 가운데 시사 글만 제목으로 판정한다. 돌리기 전에 "시사 12건 · 약 $0.70"처럼 건수와 예상 비용을 보여 준다.
- 판정이 끝난 글에는 의심·주의·통과 칩을 붙인다. "가짜 의심 숨기기" 토글로 의심 글을 뺄 수 있다.
- 결과는 글마다 보관해 다시 묻지 않는다.
- 사용자 승인: 2026-11-04 "1번권장으로 가자"(목록 위 단추 하나와 숨기기 토글, 수동 실행).

제목 판정은 거르기용 참고 값이다. 고른 글은 제작 단계에서 본문·그림까지 보는 기존 사실 확인(feat/factcheck)을 그대로 거친다. 제목 판정의 "통과"가 그 확인을 대신하지 않는다.

## 2. 지금 있는 것 (재사용)

- `recap_shorts/collect/topicfit.py`: 채널 대주제 맞춤 점수다. 제목을 묶음(30)으로 Gemini Flash에 묻고, 글마다 `work/_topics/fit_<번호>_<키>.json`에 보관한다. `FIT_VERSION`으로 무효화하고, 서버 `fit_views`가 `fit_lock`으로 중복 질문을 막는다. 화면에는 "딱 맞음/조금" 칩과 "안 맞는 글 숨기기" 토글이 있다(`web/src/pages/BestPanel.tsx`).
- `recap_shorts/factcheck.py`
  - `classify`: Flash로 분류하고, 본문을 비신뢰 경계로 감싼다. `GENERAL_BLOCK_SIGNALS` 승격이 있다.
  - `verify(claims, title=, source_text=)`: Opus 5.5 + OpenRouter web(exa 5건)으로 판정한다. 첫 줄은 검색어다(84e7afb).
  - `overall_status`, `safe_url`, `outlet_for`.
- 목록 응답: `GET /api/topics?source=dcbest|blind`, `POST /api/topics/refresh`. 항목 모양은 `BestTopic`(`web/src/types.ts`)이다.

## 3. 실측 (2026-11-04 23:40, 제목만, 수정된 판정 코드 c47da93)

| 제목 | 판정 | 출처 | 비용 | 시간 |
|---|---|---|---|---|
| 관계 후 연락 거부 男에 149차례 연락…흉기 들고 | true | 4 | $0.060 | 12.2초 |
| "일을 왜함?" 충격적인 기초수급자 근황 | partly | 1 | $0.073 | 15.3초 |
| 119 구급대원한테 부모님까지 들먹이며 욕설한 경찰 | true | 2 | $0.050 | 12.5초 |
| 식약처 가공유 판매 전면 금지 확정 (가짜) | false | 1 | $0.058 | 10.9초 |

평균은 1건 $0.06, 13초다. 예상 비용은 `건수 × $0.06`으로 보이고, 실측이 쌓이면 보관된 비용의 평균으로 바꾼다.

## 4. 설계

### 4.1 흐름

1. **분류(단추를 누르면, 비용 거의 0):** 보이는 글의 제목을 Flash에 묶음 30으로 보내 시사/일반을 나눈다.
   - 분류 결과는 글마다 보관한다.
   - 제목은 비신뢰 자료 경계로 감싼다(`factcheck._untrusted` 재사용).
   - 모델이 general이어도 `GENERAL_BLOCK_SIGNALS`가 제목에 걸리면 시사로 올린다.
2. **확인 줄(인라인, 모달 없음):** 목록 위에 "시사 12건 (이미 확인 3건 제외 9건) · 약 $0.54 [검사 시작] [취소]"를 보인다. 시사가 0건이면 "시사로 보이는 글이 없습니다"만 보인다.
3. **판정(검사 시작):** 서버의 뒤쪽 작업자 하나가 시사 제목을 하나씩 판정한다.
   - 판정은 `verify([{id:"C1", claim: 제목, query: 제목}], title=제목, source_text="")`로 한다.
   - 결과는 글마다 보관한다.
   - 화면은 2초마다 진행 상황을 받아 "판정 중 4/9 · $0.24"를 보이고 칩을 채운다.
4. **칩:** 판정 하나를 칩 하나로 옮긴다(§4.2).
5. **토글:** "가짜 의심 숨기기 (n)"는 의심 칩 글을 목록에서 뺀다. 클라이언트에서 거르고, "안 맞는 글 숨기기"와 같은 방식이다.

### 4.2 판정 → 칩

| verify 결과 | 칩 | 색 | 뜻 |
|---|---|---|---|
| false | 의심 | bad | 보도와 다르거나, 반드시 보도될 공적 사건인데 보도가 없다 |
| unverified + public_event | 주의 | warn | 공적 사건인데 관련 보도를 못 찾았다 |
| unverified (그 밖) | 확인 안 됨 | mute | 제목만으로는 확인할 보도가 없다(낚시성·사연 글에 흔함) |
| partly | 주의 | warn | 핵심은 맞지만 과장·세부가 다르다 |
| true | 통과 | good | 보도로 확인된다 |
| 판정 실패 | 확인 실패 | mute | 다시 누르면 다시 시도한다 |

- 칩에 마우스를 올리면 판정 이유 한 줄과 출처 언론사가 보인다.
- 일반 글에는 칩을 달지 않는다.
- 칩 옆에 작게 "제목 기준"을 쓴다.

### 4.3 데이터: `work/_topics/fc_<site>_<번호>.json`

```json
{"v": 1, "title_hash": "sha256(제목)", "kind": "news|general",
 "status": "false|partly|true|unverified|failed|null", "public_event": false,
 "why": "…", "sources": [{"url": "…", "outlet": "…", "date": "…"}],
 "cost_usd": 0.06, "model": "anthropic/claude-opus-5.5", "at": "ISO"}
```

- 제목이 바뀌면(`title_hash`가 다르면) 무효로 본다. `TOPIC_FC_VERSION`을 올려도 무효가 된다.
- `kind`만 있고 `status`가 null이면 분류만 하고 판정은 안 한 상태다.
- 쓰기는 임시 파일 + `os.replace`로 한다(`factcheck.atomic_json` 재사용).
- 출처 URL은 `safe_url`을 거친다.

### 4.4 API (전부 `local_only`)

- `POST /api/topics/factcheck/estimate {source, items:[{no, title}]}` → `{news, done, todo, est_usd, per_item_usd}`
  - 분류는 여기서 한다(보관된 것은 다시 묻지 않음).
  - 한 번에 최대 200건이다.
- `POST /api/topics/factcheck/run {source, nos:[…]}` → `{started, total}`
  - 이미 돌고 있으면 409다.
  - 한 번에 최대 30건이고 넘으면 400이다. 사유는 "한 번에 30건까지 판정합니다"다.
  - 판정 작업자는 제작 대기열(Runner)과 따로 돈다. 제작을 막지 않는다.
  - OpenRouter 키가 없으면 409다.
- `GET /api/topics/factcheck/status` → `{running, done, total, cost_usd, error}`
- `POST /api/topics/factcheck/cancel` → 지금 판정 중인 건까지만 하고 멈춘다.
- 목록 응답 `items[]`에 보관된 결과 `fc: {kind, status, chip, why, sources, at} | null`을 붙이고, 응답 머리에 `fc_counts: {news, checked, doubt}`를 붙인다. 목록 응답은 네트워크를 부르지 않는다(보관된 것만).

### 4.5 화면 (`BestPanel.tsx`)

- 둘째 줄(채널 대주제·숨기기 토글이 있는 줄)에 "가짜뉴스 체크" 단추와 "가짜 의심 숨기기 (n)" 토글을 둔다. 줄이 없으면 만든다.
- 확인 줄과 진행 줄은 목록 위 한 줄(인라인)이다. 모달은 쓰지 않는다.
- 카드 칩 줄에 판정 칩을 맨 앞에 둔다. 칩 폭은 고정이다.
- 키가 없으면 단추를 끄고 "OpenRouter 키가 있어야 합니다"를 title로 보인다.
- 375 폭에서는 단추와 토글이 줄바꿈된다. 가로 넘침이 없어야 한다.

### 4.6 비용·안전

- 판정은 사람이 눌러야만 한다. 목록을 새로 불러와도 자동으로 판정하지 않는다.
- 한 번에 30건까지다(최대 약 $2).
- 제목은 비신뢰 자료다. 판정 프롬프트는 기존 `verify`를 그대로 쓴다(경계·검색어 첫 줄 정리 포함).

## 5. 파일별 변경

| 파일 | 변경 |
|---|---|
| `recap_shorts/collect/topic_factcheck.py` (새) | 제목 분류(묶음), 판정 작업자(스레드 하나, 취소), 보관 읽기·쓰기, 칩 매핑, 목록 항목 주석 |
| `recap_shorts/app/server.py` | §4.4 라우트 4개, 목록 응답에 `fc`·`fc_counts` |
| `web/src/types.ts`, `web/src/api.ts` | 타입·호출 |
| `web/src/pages/BestPanel.tsx` | 단추·확인 줄·진행 줄·칩·토글 (시안 승인 뒤) |
| `web/src/mock/index.ts` | `?mock=1` 예시 |
| `tests/collect/test_topic_factcheck.py` (새), `tests/app/…` | 행동 시험 |

## 6. 작업 나눔

- **T1 (Astra, 서버):** `topic_factcheck.py`와 server 라우트, 시험. 화면과 무관하므로 시안 승인 전에 시작한다.
- **T2 (시안, 메인):** 1440·375, 라이트·다크. 앱 토큰(`index.css`)과 실제 컴포넌트 모양을 쓴다.
- **T3 (Astra, 화면):** 시안 승인 뒤 `BestPanel.tsx`·types·api·mock을 한다.

## 7. 검증 기준

- 시험: 분류 묶음·보관·무효화, 30건 제한, 동시 실행 409, 취소, 키 없음, 비신뢰 제목(지시문 제목)이 칩을 바꾸지 않음, 목록 응답이 네트워크를 부르지 않음.
- 실제: 지금 인기글 목록에서 단추를 누르고, 예상 비용과 실제 비용을 비교한다. 칩·토글은 실제 브라우저 1440·375 라이트·다크에서 보고, 콘솔 오류 0이어야 한다.
- 리뷰: Codex Sol.

## 8. 범위 밖

- 목록을 불러올 때 자동 판정.
- 본문·그림까지 보는 목록 단계 판정. 그것은 제작 단계의 사실 확인이 한다.
- 블라인드 글 본문 읽기.

## 9. 회의에서 물을 것

1. 칩 매핑(§4.2): "확인 안 됨"을 따로 둘지, 주의에 합칠지.
2. 한 번에 30건 제한과 예상 비용 산식이 적절한가.
3. 판정 작업자를 Runner와 따로 두는 것의 위험(동시 OpenRouter 호출, 하루 한도).
4. 제목 분류를 Flash 묶음으로 할 때 지시문 제목·낚시성 제목의 오분류 위험.
