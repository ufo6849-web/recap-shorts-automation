# recap-shorts-automation

커뮤니티 인기글 하나를 30~40초짜리 한국어 요약 쇼츠로 만들어 주는 Windows 앱입니다. 글·댓글 화면을 찍고, 대본을 쓰고, 목소리를 입히고, 영상 파일로 만듭니다. 화면은 React(`web/`), 백엔드는 FastAPI(`recap_shorts/app`), 데스크톱 창은 pywebview 입니다.

영상 형식은 공개된 쇼츠 32편을 재서 만든 규격표([`docs/02_program_design.md`](docs/02_program_design.md))를 따릅니다.

## 빠른 시작

디시인사이드·블라인드 인기글 주소를 넣으면 30~40초짜리 한국어 요약 쇼츠를 만들어 줍니다. 글·댓글 화면을 찍고, 대본을 쓰고, 목소리를 입히고, 영상 파일로 만듭니다.

**필요한 것**: Windows 10/11, 인터넷 연결. 설치 스크립트가 아래를 확인합니다 — Python 3.11 이상, Node.js 20 이상(화면을 한 번 만들 때만. 아래 참고), ffmpeg(영상 인코딩), Microsoft Edge(글 화면 캡처).

1. **받기** — 이 저장소를 압축으로 받아 풀거나 `git clone` 합니다.
2. **설치** — 폴더를 열고, 주소 표시줄에 `powershell` 을 쳐서 창을 연 뒤:
   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1
   ```
   파이썬 패키지 설치, 화면(`web/dist`)이 없을 때만 화면 빌드, AI 모델 파일 받기, 바탕화면 바로가기 만들기를 한 번에 합니다. 다시 실행해도 이미 된 단계는 건너뜁니다. 중간에 멈추면 빨간 `실패:` 줄과 노란 `고치는 법:` 줄이 나옵니다. 모델 받기만은 실패해도 멈추지 않고 노란 `주의:` 줄을 남긴 뒤 끝까지 갑니다(앱은 열리고, 발음 확인·로컬 목소리만 빠짐).

   화면(`web/dist`)은 git 에 들어 있지 않습니다. 그래서 `git clone` 으로 받았으면 이 단계에서 Node.js 20 이상으로 화면을 한 번 만듭니다(Node.js 가 없으면 https://nodejs.org/ 주소를 알려 주고 멈춤). 처음부터 화면이 들어 있는 압축 파일로 받으면 Node.js 없이 지나갑니다.
3. **바로가기 열기** — 바탕화면의 **커뮤니티 쇼츠 자동 제작** 을 누릅니다(콘솔 창 없이 앱 창이 뜹니다).
4. **Gemini 키 넣기** — 앱 오른쪽 위 **설정** 에서 **Gemini API 키** 를 넣고 **저장** 을 누릅니다(저장할 때 연결을 한 번 확인합니다. **연결 확인** 버튼으로 다시 시험할 수 있습니다). 키는 https://aistudio.google.com/apikey 에서 무료로 만듭니다.
5. **영상 만들기** — 디시인사이드·블라인드 글 주소를 넣고 **만들기** 를 누릅니다(**인기글에서 찾기** 로 실시간 베스트 글을 골라도 됩니다). 중간중간 확인 단계에서 멈추니 화면을 보고 넘기면 됩니다.

**블라인드(teamblind.com/kr) 글** 도 같은 8단계로 만듭니다(인기글에서 찾기의 **블라인드 인기글** 탭에서 골라도 됩니다).
- 로그인 없이 보이는 글만 받습니다 — 검색(`/kr/search/`)·내 활동(`/kr/user/history`)은 robots.txt 가 막아서 쓰지 않습니다.
- 댓글은 로그인 없이 화면에 그려진 만큼만 캡처합니다(‘더보기’ 뒤에 있는 댓글은 받지 않음).
- 작성자 닉네임·회사명은 읽지도 저장하지도 않고, 글 캡처에서는 흐리게 가립니다.

설치 스크립트 옵션:

| 옵션 | 뜻 |
|---|---|
| `-DryRun` | 무엇을 할지만 보여 주고 아무것도 바꾸지 않습니다 |
| `-NoModels` | AI 모델 파일(받아쓰기 약 163 MB) 받기를 건너뜁니다 |
| `-Supertonic` | 로컬 목소리 모델(약 129 MB)도 함께 받습니다 |
| `-NoShortcut` | 바탕화면 바로가기를 만들지 않습니다 |
| `-RebuildWeb` | 화면(web/dist)이 이미 있어도 다시 만듭니다 |

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/02_program_design.md`](docs/02_program_design.md) | 제작 규격표(화면·자막·길이·소리), 8단계 파이프라인, 모듈·데이터 형식 |
| [`docs/03_v2_app_design.md`](docs/03_v2_app_design.md) | 웹 앱 화면 설계·단계·API |
| [`docs/fx_config.md`](docs/fx_config.md) | 짤(밈)·효과음·인트로 설정(`config/fx.json`) 항목 설명 |

## 저장소에 없는 것

- **짤(밈) 이미지**(`assets_local/`)는 이 저장소에 들어 있지 않습니다(남의 그림이라서). 없으면 짤은 넣지 않습니다. 직접 모은 것을 쓰려면 앱의 **설정 → 짤** 에서 폴더를 고릅니다(아래 [키와 설정](#2-키와-설정)).
- **효과음**은 기본 꾸러미(`assets/sfx_packs/`, 밈 효과음 16개와 차임 2개)가 들어 있습니다. 내 효과음을 쓰려면 앱의 **설정 → 효과음** 에서 폴더를 고릅니다. 출처를 알 수 없는 소리라 쓰는 조건은 [라이선스](#라이선스)를 보세요.
- **AI 모델 파일**(`models/`)도 들어 있지 않습니다. `setup.ps1` 이 `scripts/setup_models.py` 로 받습니다(직접 받는 명령은 아래 [키와 설정](#2-키와-설정) 참고).
- **작업 폴더**(`work/`)와 그 안의 남의 글·그림·영상은 들어 있지 않습니다. 앱을 돌리면 이 컴퓨터에 생깁니다.

## 앱 설치·실행 (`recap_shorts`)

디시인사이드·블라인드 인기글 하나를 30~40초짜리 한국어 요약 쇼츠로 만드는 웹 앱입니다. 백엔드는 FastAPI(`recap_shorts/app`), 화면은 React(`web/`)입니다.
설계는 [`docs/02_program_design.md`](docs/02_program_design.md)(규격표)와 [`docs/03_v2_app_design.md`](docs/03_v2_app_design.md)(앱)에 있습니다.

### 1. 설치

보통은 위 [빠른 시작](#빠른-시작)의 `setup.ps1` 하나로 끝납니다. 손으로 하려면:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-app.txt -r requirements-render.txt -r requirements-voice.txt
```

- `requirements-qwen.txt` 는 Qwen 목소리 전용 가상환경(`tools\qwen_tts\.venv`)용입니다. 앱에는 필요 없습니다. Qwen 목소리를 쓰려면 그 파일 맨 위 주석의 순서대로 따로 설치하세요.
- ffmpeg 가 PATH 에 있어야 합니다(9.0.2에서 시험). 없으면 `winget install --id Gyan.FFmpeg -e` 로 받습니다.
- 글 캡처는 Playwright 로 **Microsoft Edge** 를 띄워서 찍습니다. Edge 가 설치돼 있어야 합니다(Windows 10/11 에는 보통 들어 있음).
- 화면은 `web/dist` 가 있으면 앱이 `/` 에서 보여 줍니다. 없으면 한 번 빌드하세요.
  ```powershell
  cd web
  npm ci
  npm run build
  ```

### 리눅스에서 실행 (칼리 등)

Windows 의 `setup.ps1` 대신 아래 순서로 씁니다. 글 캡처는 설치된 Edge 가 있으면 Edge 를, 없으면 Playwright Chromium 을 씁니다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-app.txt -r requirements-render.txt -r requirements-voice.txt
python -m playwright install chromium
cd web && npm ci && npm run build && cd ..
python scripts/setup_models.py --all
python -m recap_shorts.app
```

- `python -m playwright install chromium` 이 시스템 라이브러리 오류로 안 되면 `python -m playwright install --with-deps chromium` 을 씁니다(sudo 암호를 묻습니다).
- ffmpeg 이 PATH 에 있어야 합니다(데비안·칼리: `sudo apt install ffmpeg`). 화면 빌드에는 Node.js 20 이상이 필요합니다.
- 앱 주소는 http://127.0.0.1:8020 입니다.
- **OpenRouter 키만으로 쓰기** (Gemini 키 없이): 설정에서 **OpenRouter API 키** 를 넣고, 대본은 **Claude Opus 5.5 · OpenRouter** 를 고릅니다. 목소리는 **로컬 목소리(Supertonic)**, AI 그림은 **AI 그림 안 만듦** 으로 둡니다. Gemini 키가 없으면 목소리와 AI 그림의 기본값도 이 값입니다(설정에서 다르게 고르면 그대로 따릅니다). 그림 읽기와 가림 판단은 OpenRouter 의 Gemini 3.8 Flash 가 맡습니다.

### 2. 키와 설정

키는 **앱 화면의 설정**에서 넣습니다. 값은 `config/settings.local.json` 에 저장되고, 이 파일은 git 에 들어가지 않습니다(`.gitignore`). 키 값은 화면·로그·저장소 어디에도 남기지 않습니다(화면에는 가린 값만 보이고, 16자 이상이면 끝 4자만 보입니다).

**환경 변수가 있으면 그게 먼저입니다.** 환경 변수로 넣은 키는 설정 화면에서 바꿀 수 없습니다(그 자리에 "환경변수 `이름` 에서 읽음"으로 나오고, 아래에 "환경변수로 정해져 있어 여기서 바꿀 수 없습니다"라고 적힙니다).

| 키 | 환경 변수 | 무엇에 쓰나 | 없으면 |
|---|---|---|---|
| Gemini API 키 | `RECAP_GEMINI_API_KEY` | 대본, 목소리(Gemini TTS), 첨부 그림 읽기, AI 그림(그림 만드는 곳이 Gemini 일 때) | 영상을 만들 수 없습니다. **이 키 하나는 꼭 필요합니다** |
| Cloudflare 계정 ID | `RECAP_CF_ACCOUNT_ID` | Clef 점검 — 실시간 베스트 주제 분류, AI 그림 글자 점검, 완성 영상 점검 | 이 점검만 건너뜁니다 |
| Cloudflare API 토큰 | `RECAP_CF_API_TOKEN` | 위와 같음(계정 ID 와 둘 다 필요) | 위와 같음 |

키를 화면에서 넣으면 **저장할 때 한 번 연결을 시험**합니다(그룹마다). **연결 확인** 버튼으로 다시 시험할 수 있습니다. 결과만 보여 주고 키 값은 어디에도 남기지 않습니다.

**AI 그림을 어디서 만들지** (설정 화면의 선택):

| 값 | 뜻 |
|---|---|
| 로컬 이미지 서버 | 로컬 이미지 서버(`RECAP_IMAGE_URL`, 기본 `http://127.0.0.1:8791`)로 만듭니다. 서버가 사용 한도에 걸리면 Gemini 로 대신 만듭니다 |
| Gemini 이미지 (기본) | Gemini 이미지로 만듭니다 |
| AI 그림 안 만듦 | 글 캡처·사진만 씁니다 |

**Gemini 이미지는 돈이 듭니다**(장당 과금). 한 번도 고르지 않았으면 Gemini 이미지로 만드니, 무료로만 쓰려면 **AI 그림 안 만듦** 을 고르세요. 로컬 이미지 서버는 따로 띄우는 이미지 생성 서버이며 이 저장소에 들어 있지 않습니다(요청·응답 형식은 `recap_shorts/images/provider.py` 참고) — 없어도 영상은 만들어집니다. (예전의 "자동"은 없앴습니다. 설정 파일에 남아 있으면 Gemini 이미지로 봅니다.)

**쓸 모델** (설정 화면의 '쓸 모델'). 고르면 바로 저장되고 다음에 만드는 영상부터 씁니다. 목록은 넣은 Gemini 키로 쓸 수 있는 모델(Gemini API 모델 목록, 무료 조회)에서 가져오고, 키가 없으면 써 본 모델만 보여 줍니다.

| 자리 | 기본 | 고를 수 있는 것 |
|---|---|---|
| 대본 | `gemini-4-argon` | `gemini-4-argon`, `gemini-3.8-flash` 두 가지 (비용 차이는 아래 [비용](#비용)) |
| 목소리 | Gemini 목소리 Charon, `gemini-3.8-flash-tts` | Gemini 목소리 30개(이름 — 설명)와 TTS 모델, 또는 **로컬 목소리(Supertonic)** 와 목소리 번호 0~9 — 로컬 목소리는 모델 파일을 받은 뒤에만 고를 수 있습니다(무료, 이 컴퓨터에서 만듦) |
| 이미지 | `gemini-3.1-flash-image` (Nano Banana 2) | Gemini 이미지 모델 (예: Nano Banana Pro, 2 Lite). AI 그림 만드는 곳이 Gemini 일 때와 로컬 서버가 한도에 걸려 대신 만들 때 씁니다 |

**효과음·짤** (설정 화면의 '효과음'·'짤'). **폴더 고르기**(Windows 폴더 창) 또는 경로를 붙여 넣어 내 폴더를 넣으면 그 폴더가 먼저이고, 폴더를 여러 개 넣을 수 있습니다. 폴더에 `manifest.json` 이 없으면 프로그램이 Gemini 로 소리를 듣고(짤은 그림을 보고) 종류·뜻을 매겨 따로 꾸러미(`assets_local/analyzed/`)를 만듭니다. 내 원래 폴더는 바뀌지 않고, 소리·그림 6개에 Gemini 요청 1번입니다. 효과음은 종류마다 몇 개씩만 고르고(같은 소리·욕설·음악·권리자가 분명한 소리는 뺌), 짤은 혐오·성적·잔인한 그림과 같은 그림을 뺍니다. 끝나면 결과 화면에서 종류·뜻을 고치거나 빼고 다시 넣을 수 있습니다. Gemini 키가 없으면 효과음은 파일 이름(`pop_01.wav`, 종류 이름 폴더 `pop\아무이름.mp3`)으로 종류를 정하고, 짤은 파일 이름을 뜻으로 씁니다. 규칙은 [`docs/library_analysis.md`](docs/library_analysis.md). 효과음 폴더를 고르지 않으면 저장소에 같이 든 기본 꾸러미(`assets/sfx_packs/`)에서 고릅니다. **내 폴더에 없는 종류는 기본 효과음으로 채우기**(기본 켬)를 끄면 그 자리는 효과음 없이 지나갑니다. 고른 폴더가 없어지면 기본으로 대신합니다. 내 폴더의 소리·짤은 저작권 점검(검수 화면 경고)을 하지 않습니다.

목소리 아래 **미리듣기** 는 고른 목소리로 예시 문장(직접 고쳐 쓸 수 있음)을 만들어 바로 틉니다. Gemini 목소리는 새 소리를 만들 때마다 요청 1회(하루 한도에 들어감)이고, 같은 목소리·모델·문장은 `work/_preview/` 에 저장해 둔 소리를 다시 틉니다.

써 본 모델은 목록 위쪽에 `config/prices.json` 의 가격과 함께 나옵니다. 목소리·이미지의 그 밖의 모델은 목소리 자르기·그림 품질을 시험해 보지 않았습니다. 아래 환경 변수로 정한 자리는 화면에서 바꿀 수 없습니다.

**모델 파일 받기.** 받아쓰기(발음 확인) 모델과 로컬 목소리 모델은 `models/` 폴더에 받습니다. `setup.ps1` 이 자동으로 부릅니다. 빠진 모델은 앱의 **설정 → 모델 파일** 에서 **받기** 를 눌러 받을 수 있습니다(진행 막대가 나오고, 다 받으면 바로 씁니다 — 앱을 다시 켤 필요 없음). 명령으로 받으려면:

```powershell
.venv\Scripts\python scripts\setup_models.py              # 받아쓰기(SenseVoice)만, 약 163 MB
.venv\Scripts\python scripts\setup_models.py --supertonic # 로컬 목소리(Supertonic)도, 약 129 MB 더
.venv\Scripts\python scripts\setup_models.py --all        # 둘 다
```

- 받아쓰기 모델이 없으면 발음 확인·어절 컷·목소리 자르기 다듬기를 건너뜁니다(영상은 만들어짐).
- 로컬 목소리 모델이 없으면 로컬 목소리 엔진만 못 씁니다(기본 목소리는 Gemini).

바꾸고 싶을 때만 넣는 설정(환경 변수):

| 이름 | 뜻 |
|---|---|
| `RECAP_SETTINGS_FILE` | 설정 파일 위치 (기본 `config/settings.local.json`) |
| `RECAP_IMAGE_URL` | 로컬 이미지 서버 주소 (기본 `http://127.0.0.1:8791`) |
| `RECAP_IMAGE_FALLBACK` | 로컬 이미지 서버가 사용 한도에 걸리면 Gemini 이미지로 대신 만들기 (기본 `gemini`, 빈 값이면 끔) |
| `RECAP_GEMINI_IMAGE_MODEL` | Gemini 이미지 모델 (설정 화면의 '이미지'보다 먼저) |
| `RECAP_TTS_MODEL` | Gemini 목소리 모델 (설정 화면의 '목소리'보다 먼저) |
| `RECAP_SCRIPT_LLM`, `RECAP_SCRIPT_MODEL` | 대본 LLM (`gemini_api` 기본, 또는 `agy`)과 모델 (설정 화면의 '대본'보다 먼저) |
| `RECAP_AGY_PATH` | agy CLI 위치 (`RECAP_SCRIPT_LLM=agy` 일 때) |
| `RECAP_SUPERTONIC_DIR`, `RECAP_TTS_SID`, `RECAP_TTS_STEPS`, `RECAP_TTS_THREADS` | 로컬 목소리(Supertonic) — 폴더를 안 정하면 `models/` 아래를 봄. `RECAP_TTS_SID` 는 설정 화면의 목소리 번호보다 먼저 |
| `RECAP_SENSEVOICE_DIR` | 받아쓰기 확인(SenseVoice) 모델 폴더 — 안 정하면 `models/` 아래를 봄 |
| `RECAP_QWEN_PYTHON`, `RECAP_QWEN_TIMEOUT` | Qwen 목소리용 파이썬·제한 시간 |
| `RECAP_WORK_ROOT` | 작업 폴더 (기본 `work/`) |
| `RECAP_USAGE_LOG` | API 사용량 기록 파일 (기본 `work/_usage.jsonl`, 빈 값이면 끔) |
| `RECAP_FX_NO_LLM` | 짤 고르기·박자 나누기에서 Gemini 를 부르지 않음(규칙으로만) |
| `COLLECT_CONTACT` | 수집기 User-Agent 에 넣을 연락처 |

### 3. 실행

**프로그램처럼 쓰기 (cmd 창 없음).** `setup.ps1` 이 한 번 만들어 둡니다(직접 만들려면 아래 두 줄).

```powershell
.venv\Scripts\python scripts\make_app_icon.py
powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1
```

바탕화면·시작 메뉴의 **커뮤니티 쇼츠 자동 제작** 을 누르면 콘솔 창 없이 앱 창이 뜹니다. 실제로 실행되는 명령은 `.venv\Scripts\pythonw -m recap_shorts.desktop` 입니다.

- 창 하나에 앱 화면이 뜹니다. **확장 탭(선택)**: gitignore 된 `recap_shorts/local_tabs.py` 를 두면 같은 창에 탭이 더 뜹니다(이 앱은 첫 탭 '썰쇼츠', 탭 옆 점: 주황=켜는 중, 초록=켜짐, 회색=꺼짐. 그 파일이 없으면 탭 없이 앱 화면만 열립니다). 모듈은 `tabs(log_dir)` 함수 하나만 내보내면 되고, 탭 객체는 `id`·`title`·`url`·`probe` 와 `tab()`·`start()`·`stop()`·`state()`·`busy()`·`relaunch()` 를 가집니다(읽는 곳은 `recap_shorts/shell.py`).
- 탭은 각자 자기 서버를 띄우고 자기 설정을 읽습니다. `--no-extra-tabs` 로 끌 수 있습니다.
- 창을 닫으면 이 창이 띄운 서버도 꺼집니다. 어느 쪽이든 영상을 만드는 중이면 닫기 전에 한 번 묻습니다.
- ffmpeg·Edge 같은 하위 프로그램도 창을 띄우지 않습니다.
- 아이콘을 다시 누르면 새 창 대신 열린 창이 앞으로 나옵니다.
- 로그는 `work\_logs\desktop.log` 에 남습니다(5 MB씩 3개). print 출력은 `desktop.stdout.log` 에 남습니다.
- 창은 pywebview(Edge WebView2)로 띄웁니다. pywebview 가 없으면 Edge 앱 모드 창으로 엽니다.

**개발용(콘솔에 로그).**

```powershell
.venv\Scripts\python -m recap_shorts.app
```

브라우저에서 http://127.0.0.1:8020 을 엽니다. 8020 은 기본 번호일 뿐이라, 다른 프로그램이 쓰고 있으면 `--port 9000` 처럼 바꾸면 됩니다. `--work <폴더>` 로 작업 폴더를, `--host` 로 주소(기본 `127.0.0.1`)를 바꿀 수 있습니다. 이 서버가 이미 떠 있으면 바로가기는 새 서버를 띄우지 않고 그 서버에 창만 엽니다. 이때 창을 닫아도 서버는 꺼지지 않습니다.

로컬 이미지 서버(`127.0.0.1:8791`)는 **없어도 됩니다**. 기본값은 Gemini 이미지이고, 로컬 이미지 서버를 골랐는데 서버가 꺼져 있으면 AI 그림 없이 글 캡처·사진만으로 영상을 만듭니다.

### 비용

**실제로 잰 값.** 커뮤니티 인기글 하나로 대본 모델만 바꿔 두 편을 만들었습니다. 글 가져오기·자료 읽기는 한 번만 하고 대본부터 갈라 만들었으니, 두 편은 같은 재료에서 대본 모델만 다릅니다. AI 그림은 로컬 이미지 서버로 만들었고, 단가는 `config/prices.json`(2026-10-18 확인)을 썼습니다.

| | 대본 Gemini 4 Argon | 대본 Gemini 3.8 Flash |
|---|---|---|
| 영상 1편 — 지금 | 약 $0.18 | 약 $0.17 |
| 영상 1편 — 2026-12-01부터 (Argon 값 2배) | 약 $0.23 | 약 $0.17 |
| 영상 1편 — 2027-01-01부터 (3.8 Flash·목소리 값도 2배) | 약 $0.37 | 약 $0.35 |
| 한 달 (하루 3편 × 30일) — 지금 | 약 $16 | 약 $16 |
| 한 달 — 2026-12-01부터 | 약 $21 | 약 $16 |
| 한 달 — 2027-01-01부터 | 약 $33 | 약 $31 |
| 만든 영상 길이 | 약 35초 | 약 31초 |

어디에 드는지 (지금 단가, 영상 1편):

| 단계 | Argon 편 | 3.8 Flash 편 | 쓰는 모델 |
|---|---|---|---|
| 자료 읽기 (첨부 그림·댓글 읽기 9회) | 약 $0.02 | 약 $0.02 | 3.8 Flash (두 편이 같은 결과를 씀) |
| 대본 (초안 + 다듬기 2회) | 약 $0.05 | 약 $0.04 | 고른 대본 모델 |
| 목소리 (요청 1회) | 약 $0.01 | 약 $0.01 | 3.8 Flash TTS |
| 자료 화면 (캡처 자리 찾기·대상 사진 찾기·그림 검사 17~18회) | 약 $0.09 | 약 $0.09 | 3.8 Flash |
| 장면 배치 (효과음 박자·짤 고르기 2회) | 약 $0.01 | 약 $0.01 | 3.8 Flash |
| AI 그림 | $0 (8장) | $0 (6장) | 로컬 이미지 서버 |
| Clef 판단 | $0 (약 240 뉴런) | $0 (약 210 뉴런) | 하루 무료 1만 뉴런 안 |

- 대본 단계만 보면 3.8 Flash 의 토큰 단가는 Argon 의 37.5%(입력·출력 모두)이지만, 생각을 길게 해서 출력(생각 포함)이 4배쯤 나왔습니다(약 8.6천 대 약 2천 토큰). 그래서 지금은 편당 약 1센트 차이이고, 걸린 시간은 Argon 약 30초, 3.8 Flash 약 1분이었습니다.
- 돈의 3분의 2쯤은 대본이 아니라 3.8 Flash 로 그림·캡처를 읽는 호출입니다. 대본 모델을 바꿔도 이 부분은 그대로입니다.
- AI 그림을 Gemini 이미지(`gemini-3.1-flash-image`, 장당 $0.067)로 만들면 그림 수만큼 더해집니다. 이번 두 편이면 약 $0.54(8장), 약 $0.40(6장)이 더 들어 편당 약 $0.6~0.7 입니다.
- 글 하나로 잰 값입니다. 첨부 그림 수, 글 길이, 다시 만든 그림 수에 따라 달라집니다.

**직접 계산하기.** 영상 1편을 만들 때 드는 돈(외부 호출)은 앱이 `work/_usage.jsonl` 에 기록합니다. 그 기록과 `config/prices.json` 의 단가로 계산하려면:

```powershell
.venv\Scripts\python scripts\cost_estimate.py --since 2026-01-01T00:00:00 --per-day 3 --days 30
```

- `--since`/`--until` 은 그 구간을 "영상 1편"으로 봅니다(UTC).
- `config/prices.json` 은 단가와 출처를 적어 둔 파일입니다. 가격은 바뀌니 결제 전에 출처 링크의 공식 가격표를 확인하세요.
- Gemini 이미지·Gemini 목소리는 유료입니다. 로컬 이미지 서버로 만든 그림과 합성 효과음은 이 계산에서 0원입니다. Cloudflare Clef 는 하루 무료 한도(1만 뉴런)를 넘길 때만 돈이 듭니다.

### 4. 8단계와 확인 단계 4개

글 하나가 아래 8단계를 차례로 지나갑니다. **확인** 단계에서는 멈추고 사람을 기다립니다. 화면에서 확인하고 넘기면 다음 단계로 갑니다(작업 옵션 `auto_steps` 에 넣은 확인 단계는 멈추지 않음).

| # | 단계 | 하는 일 | 멈춤 |
|---|---|---|---|
| 1 | 글 가져오기 | 글 본문·첨부 그림·글 머리와 댓글 캡처(디시·블라인드) | |
| 2 | 자료 읽기 | 첨부 그림의 글자·종류 읽기 | **확인** |
| 3 | 대본 | 대본 쓰기·다듬기, 근거 없는 문장 빼기·원문에 없는 숫자 표시 | **확인** (대본 편집 화면) |
| 4 | 목소리 | 내레이션 합성, 받아쓰기로 발음 확인 | |
| 5 | 자료 화면 | 글 캡처 카드·가림, AI 그림 만들기·품질 검사(나쁜 장은 한 번 다시 만들고 다시 검사) | |
| 6 | 장면 배치 | 문장마다 그림 고르기, 타임라인 | **확인** (장면 편집기) |
| 7 | 영상 만들기 | 렌더링(`out/final.mp4`) | |
| 8 | 검수 | 설명 문구(출처·짤·효과음 표기), Clef 점검, 규격표 비교, 확인할 것 | **확인** (승인) |

승인해도 업로드는 하지 않습니다. 영상 파일을 받아 직접 올리고, 검수 화면의 설명 문구를 붙여 넣으세요.

### 5. 끝난 영상을 구글 드라이브로 (기본 꺼짐, 켠 경우에만)

**기본값은 꺼짐이다.** 켜면 **Google Drive 데스크톱 앱**이 연결한 '내 드라이브' 아래 `recap-shorts-보관\<job id>\` 로 job 폴더를 통째로 복사합니다.

- 드라이브가 없으면 **영상은 `work/` 폴더에 그대로 남습니다.** 작업 자체는 실패하지 않습니다(목록에 "구글 드라이브를 찾지 못해 로컬에 그대로 둡니다"로 표시).
- 복사한 뒤 양쪽 파일을 SHA-256 으로 대조합니다. 모두 같을 때만 로컬 폴더를 지웁니다. 하나라도 다르면 로컬을 그대로 둡니다.
- 대조 결과는 드라이브 폴더의 `_archive_manifest.json` 에 남습니다.
- 옮긴 작업은 목록에 **구글 드라이브** 로 표시됩니다. 영상·설명 문구는 드라이브에서 바로 열리지만 고칠 수는 없습니다.
- 로컬에는 작은 색인 `work\_archive.json`, 비용 장부 `work\_usage*.jsonl`, 로그만 남습니다.

**켜는 두 가지 방법** — 설정 파일은 `config\storage.json` 이고, 그 위에 `config\storage.local.json`(gitignore, 이 PC 에서만)을 덮어 읽습니다(`recap_shorts/archive.py load_config`).

| 방법 | 하는 일 |
|---|---|
| 모든 PC 에서 켜기 | `config\storage.json` 의 `archive.enabled` 를 `true` 로 |
| 이 PC 에서만 켜기 | `config\storage.local.json` 을 만들어 `{"archive": {"enabled": true}}` 처럼 **바꿀 값만** 적는다(적은 키만 덮어씀) |

`archive` 안의 키: `enabled`(자동으로 옮길지), `root`(드라이브 위치. `auto` 면 `G:\내 드라이브` 를 먼저, 없으면 다른 드라이브의 `내 드라이브`/`My Drive`), `folder`(보관 폴더 이름), `delete_local`(대조가 모두 같을 때 로컬을 지울지). `root` 에 폴더 경로를 직접 넣으려면 `{"archive": {"enabled": true, "root": "D:/보관"}}` 처럼 쓴다.

- 손으로 옮기기: `POST /api/jobs/{jid}/archive`(끝난 작업만, 설정이 꺼져 있어도 동작). 드라이브 상태 보기: `GET /api/storage`.
- 시험(`pytest`) 중에는 설정과 상관없이 항상 꺼져 있습니다.

### 6. 캡컷으로 넘기기 (CapCut 데스크톱을 쓰는 경우)

검수 화면의 **캡컷으로 넘기기** 를 누르면 영상 파일 하나가 아니라 **CapCut 에서 그림·글자·소리를 따로 고칠 수 있는 초안**을 만듭니다. 외부 호출은 없고 몇 초 걸립니다.

- 초안은 CapCut 초안 위치(`%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft`)에 만들고, CapCut 프로젝트 목록 맨 앞에 올립니다. 목록 파일(`root_meta_info.json`)은 날짜를 붙인 백업을 먼저 남깁니다.
- **CapCut 이 켜져 있으면 목록에는 올리지 않습니다**(켜진 CapCut 이 끌 때 목록을 덮어쓸 수 있어서). 초안 폴더는 만들어지니, CapCut 을 끄고 다시 누르면 됩니다.
- 그림·소리·글꼴은 초안 안 `assets\` 로 복사합니다. 끝난 작업이 구글 드라이브로 옮겨져도 초안은 깨지지 않습니다.
- 들어가는 것:

| 영상의 요소 | CapCut 에서 | |
|---|---|---|
| 그림 컷 | 컷마다 사진 하나(렌더처럼 4:3 으로 잘라 둠). 확대는 배율 키프레임 | 그대로 |
| 제목 두 줄·자막 | 고칠 수 있는 글자. 같은 글꼴(Pretendard)·크기·줄바꿈·강조 색·외곽선 | 그대로 |
| 짤(스티커) | 같은 자리·크기의 사진, 튀어나옴·사라짐은 키프레임 | 그대로(흔들림은 빠짐) |
| 목소리·효과음 | 따로 오디오 트랙. 렌더와 같은 크기로 맞춘 사본이라 볼륨은 100% | 그대로 |
| 인트로 카드(처음 몇 초) | 움직임을 담은 짧은 영상 조각 | 비슷하게(글자는 못 고침) |
| 등장 효과·짤 컷 튀어나옴 | 올라오기만 / 효과 없음 | 비슷하게 |
| 그림 상자 밖 | 상자 자리만 뚫린 검은 판(확대·짤이 제목·자막 띠로 넘치지 않게) | — |

- 명령으로: `.venv\Scripts\python -m recap_shorts.capcut work\<job 폴더> [--drafts-dir 폴더] [--no-register]`
- 초안 위치를 바꾸려면 `RECAP_CAPCUT_DRAFTS_DIR`, 글자가 CapCut 에서 크거나 작게 보이면 `RECAP_CAPCUT_PX_PER_SIZE`(기본 5.5, 글자가 크면 올림)를 바꿉니다. 초안은 CapCut 9.x 에서 열리는 형식입니다. CapCut 이 새 버전에서 형식을 바꾸면 안 열릴 수 있습니다.

### 시험

```powershell
.venv\Scripts\python -m pytest -q
cd web
npm run typecheck
```

시험은 디시·Gemini·이미지 서버·목소리·렌더·Clef 를 가짜로 바꿔서 돌립니다(유료 API 를 부르지 않음).

## 쓰는 사람의 책임

이 앱은 커뮤니티 글·댓글·첨부 그림을 화면으로 찍어 영상에 넣는 도구입니다. **찍은 글·그림·댓글의 저작권과 초상권은 원작자에게 있고, 이 앱은 그 권리를 넘겨주지 않습니다.**

- 각 사이트(디시인사이드·블라인드)의 이용약관, 유튜브 정책, 개인정보를 지키는 일은 쓰는 사람 몫입니다. 닉네임·아이디·IP·회사명·얼굴이 화면에 남지 않았는지 확인하세요(앱이 가림 상자를 그려 주지만 확인은 사람이).
- 만든 영상을 올릴지, 어디에 올릴지, 어떻게 표시할지도 쓰는 사람이 정하고 책임집니다.
- 앱은 robots.txt 와 요청 간격을 지키고 로그인·차단 우회를 하지 않지만, **그것이 남의 글을 쓸 권리를 주지는 않습니다.** 필요하면 원작자에게 허락을 받으세요.
- AI 그림·AI 목소리를 쓴 영상은 그렇다는 사실이 드러나게 표시하세요.

저장소에는 남의 영상·글·그림이 들어 있지 않습니다. 남이 만든 소리는 기본 효과음 꾸러미(`assets/sfx_packs/`)에만 있고, 이용 조건은 아래 라이선스 절에 적었습니다. 작업 폴더(`work/`)는 이 컴퓨터에만 남고 커밋되지 않습니다.

## 폴더 구조

```
recap-shorts-automation/
├─ README.md
├─ setup.ps1                 한 번에 설치하는 스크립트 (파이썬 패키지·화면·모델·바로가기)
├─ requirements-app.txt      앱(수집·서버·화면)
├─ requirements-render.txt   영상 렌더
├─ requirements-voice.txt    목소리·받아쓰기
├─ requirements-qwen.txt     Qwen 목소리 전용 가상환경용
├─ config/
│  ├─ settings.local.json    (gitignore) 앱 화면에서 넣은 키
│  ├─ channel.json           주제 대주제 프리셋 (+ channel.local.json, gitignore)
│  ├─ collect_sites.json     글 캡처 가림·광고 숨김
│  ├─ outlets.json           언론사 도메인 → 이름 (출처 표시용)
│  ├─ topics.json            주제 찾기(디시 실시간 베스트) 설정 — 쪽 수·기간·분류 예산
│  ├─ fx.json                짤·효과음·인트로 설정 (설명: docs/fx_config.md)
│  ├─ prices.json            외부 호출 단가 (비용 계산용)
│  ├─ sfx_rights.json        효과음 출처·표기
│  ├─ sensitive.json         주제에서 거르는 말
│  └─ storage.json           구글 드라이브로 옮길지 (+ storage.local.json, gitignore)
├─ models/                   (gitignore) 받아쓰기·로컬 목소리 모델 — scripts/setup_models.py 로 받음
├─ assets/                   글꼴(Pretendard)·아이콘·직접 만든 합성 효과음(sfx/)·기본 효과음 꾸러미(sfx_packs/)
├─ assets_local/             (gitignore) 짤 꾸러미, 분석해 만든 꾸러미(analyzed/) — 이 저장소에 없음
├─ docs/                     설계 문서 (02 규격표, 03 앱, fx_config)
├─ recap_shorts/             백엔드
│  ├─ local_tabs.py          (gitignore) 확장 탭 — 없으면 썰쇼츠만
│  ├─ app/                   FastAPI 서버 (server.py)
│  ├─ collect/  script/  voice/  images/  render/  capcut/  clef/  fx/
│  ├─ llm/                   Gemini 텍스트·비전 호출(gemini_api)과 모델 목록(gemini_models)
│  ├─ archive.py             끝난 작업을 구글 드라이브로 (SHA-256 확인 뒤에만 로컬 삭제)
│  ├─ errors.py              오류를 화면용 한국어로 (예외 이름·경로를 화면에 내보내지 않음)
│  ├─ modelfiles.py          로컬 모델 파일(models/) 받기·검사
│  ├─ usage.py               외부 호출 기록(work/_usage.jsonl) — 비용 계산용
│  ├─ wordcuts.py            그림 컷을 문장 안 어절 경계에 놓기
│  └─ pipeline.py  desktop.py  shell.py  timeline.py  settings.py
├─ scripts/
│  ├─ setup_models.py        AI 모델 파일 받기
│  ├─ install_shortcut.ps1   바탕화면 바로가기
│  ├─ make_app_icon.py       앱 아이콘 만들기
│  ├─ make_sfx.py            효과음 합성
│  ├─ cost_estimate.py       사용량 기록으로 비용 계산
│  └─ clef_eval.py           Clef 점검 시험
├─ tests/                    pytest (가짜 API 로만 돎)
├─ tools/qwen_tts/           Qwen 목소리 호출 스크립트
└─ web/                      React 화면 (빌드 결과 dist/ 는 gitignore)
```

## 진행 상황

- [x] 1단계 규격 — 공개된 쇼츠 32편을 재서 규격표(`docs/02_program_design.md`)로 정리
- [x] 2단계 프로그램 설계 — `docs/02_program_design.md`(규격표), `docs/03_v2_app_design.md`(앱)
- [x] 3단계 구현 — 8단계 제작 앱(`recap_shorts`, `web/`), 설치·실행은 위 [앱 설치·실행](#앱-설치실행-recap_shorts)
- [ ] 4단계 우리 결과물을 같은 자로 다시 재서 규격과 비교

## 라이선스

MIT 라이선스입니다 — [LICENSE](LICENSE).

이 저장소에 함께 든 것 중 MIT 가 아닌 것:
- 글꼴 Pretendard(`assets/fonts/`)는 SIL Open Font License 1.1 을 따릅니다(`assets/fonts/OFL.txt`).
- 기본 효과음 꾸러미(`assets/sfx_packs/`)는 MIT 가 아닙니다. 자세한 것은 [`assets/sfx_packs/README.md`](assets/sfx_packs/README.md).
  - `chime/`: OtoLogic(https://otologic.jp) 효과음을 길이·속도만 바꾼 것으로, CC BY 4.0 을 따릅니다. 영상에 쓰면 출처 표기가 필요하고, 앱이 설명 문구에 자동으로 넣습니다.
  - `user_meme/`: 인터넷에 도는 밈 효과음 모음(16개)이고 원 출처와 이용 조건을 확인하지 못했습니다. 권리자가 분명한 소리(게임·상표·노래에서 잘라 낸 것)와 영상·시스템 소리에서 가져온 것으로 보이는 소리는 뺐습니다. 라이선스가 없으니 재사용을 허락한다는 뜻이 아니고, 남은 소리도 안전하다는 보증이 없으니 영상에 쓸 때의 책임은 쓰는 사람에게 있습니다(검수 화면이 주의할 소리를 알려 줍니다).

저장소에 들어 있지 않고 따로 받는 것은 각자의 라이선스를 따릅니다: 받아쓰기·목소리 모델(`scripts/setup_models.py` 가 받는 파일), Qwen3-TTS 모델 파일(Apache-2.0).
