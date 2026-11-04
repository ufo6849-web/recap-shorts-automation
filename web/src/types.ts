// 백엔드 계약(docs/03_v2_app_design.md 1.2)의 형식. 설계서에 형식이 없는 응답(topics, scenes, script 상세, review)은
// 아래 "가정" 주석을 따른다. 서버가 실제로 보내는 모양이 기준이다.

export type StepStatus = "todo" | "running" | "waiting" | "done" | "error";
export type JobStatus = "running" | "waiting" | "done" | "error";

export interface SubStep { label: string; status: StepStatus; note?: string | null }
export interface Step {
  key: StepKey; label: string; status: StepStatus; progress: number; detail?: string | null;
  sub?: SubStep[]; started_at?: string | null; ended_at?: string | null; error?: string | null;
}
export type StepKey = "source" | "read" | "script" | "voice" | "assets" | "scenes" | "render" | "review";
export interface LogLine { t: string; msg: string }

/** out/review.json (artifacts.review). hint_only: Clef 점수는 참고용. */
/** review.json 의 sfx_rights 한 줄: 영상에 들어간 효과음 중 저작권 위험이 medium 이상인 것(config/sfx_rights.json, 원본 파일 이름만 보고 매긴 판단). */
export interface SfxRightsWarn { t: number; id: string; level: "high" | "medium"; note: string; source?: string }
/** assets.json 항목의 sensitive — 자극적인 그림 판정과 흑백/흐림 처리(recap_shorts/images/sensitive.py). */
export type SensitiveTreatment = "none" | "gray" | "gray_blur" | "none_by_user";
export interface SensitiveInfo {
  level: "none" | "mild" | "strong"; kind: string; reason?: string; treatment: SensitiveTreatment;
  user?: boolean; flagged?: boolean; blocked?: boolean; note?: string;
}
export interface ReviewJson {
  duration: number; shots: number; captions: number; first_cut: number; syllables: number; speech_rate: number | null;
  asr_mean_match: number | null; script_flags: unknown[]; needs_review_assets?: string[]; sfx_rights?: SfxRightsWarn[]; sensitive?: (Omit<SensitiveInfo, "note"> & { file: string })[];
  clef: null | { opens_finished?: number | null; face?: number | null; music?: number | null; narration?: number | null; caption_cut?: number | null; topic_match?: number | null; tokens?: number; neurons?: number; hint_only?: boolean };
}

/** 사실 확인 판정(11.3 claims[].verdict): true 사실 · partly 일부 사실 · false 사실 아님 · unverified 확인 불가. */
export type FactVerdict = "true" | "partly" | "false" | "unverified";
export interface FactSource { url: string; title: string; outlet: string; date: string | null }
export interface FactClaim { id: string; claim: string; source_quote: string; verdict: FactVerdict; why: string; fact: string | null; sources: FactSource[] }
/** 서버 view(11.5): factcheck.json(11.3)에서 input_hash 를 뺀 것에 stale·gate·gate_reason·running 을 더한다.
 *  status "missing" 은 정책 작업인데 기록이 없을 때라 claims 는 [] 이고 나머지 값은 빠질 수 있다.
 *  gate: clear 통과 · need_ack 확인하고 계속 · blocked 막힘(gate_reason 이 이유). */
export interface FactcheckView {
  version?: number; status: "skipped" | "pass" | "warn" | "stop" | "failed" | "missing";
  kind?: "info" | "general" | "uncertain" | null; forced?: boolean; reason?: string | null;
  claims: FactClaim[]; overall_why?: string; error?: string | null;
  override?: { at: string; note: string } | null; ack?: { at: string } | null;
  model?: string | null; search?: string; cost_usd?: number | null; checked_at?: string;
  stale?: boolean; gate: "clear" | "need_ack" | "blocked"; gate_reason: string | null; running?: boolean;
}

export interface JobState {
  id: string; title: string;
  /** site: "dcinside"(없던 작업도 이렇게 읽는다) | "blind". blind 는 no 가 글 주소의 별칭(문자열)이고 topic 은 블라인드 토픽 이름. */
  source: { site?: "dcinside" | "blind"; gallery: string; no: number | string; url: string; title: string; topic?: string };
  created_at: string; status: JobStatus; steps: Step[];
  artifacts: { script?: string; voice?: string; video?: string; images?: string[]; description?: string; [k: string]: unknown };
  log: LogLine[]; progress?: number; thumbnail?: string | null; options?: Record<string, unknown>;
  /** 구글 드라이브 보관 상태. status "done" 이면 드라이브로 옮겨져 고칠 수 없다. offline 은 드라이브를 읽을 수 없는 때. */
  archive?: { status: "done" | "error" | "skipped"; path?: string; at?: string; error?: string | null; warning?: string };
  offline?: boolean;
  /** 사실 확인 정책 버전. 기능 이전 작업은 없거나 null. */
  factcheck_policy?: number | null;
  /** 읽기 단계의 사실 확인 결과(없으면 null). */
  factcheck?: FactcheckView | null;
}
/** GET /api/storage */
export interface Storage { enabled: boolean; found: boolean; root: string | null; folder: string | null; path: string | null; delete_local: boolean; free_bytes: number | null; total_bytes: number | null }

export interface HealthItem { ok: boolean; detail?: string; bad?: boolean }   // bad: 키는 있지만 연결 확인에서 틀렸다고 나옴
/** gemini_key 는 'AI 키 준비' 상태다(이름은 화면 호환 때문에 그대로). ok = 준비됨, detail = 쓰는 키 또는 준비 안 된 이유. */
export interface Health { gemini_key: HealthItem; cf_keys: HealthItem; gpu: HealthItem; image_server: HealthItem; ffmpeg: HealthItem; asr_model?: HealthItem }

/** GET /api/topics — 실제 응답. triage 는 Clef 분류(없으면 null). category 는 {분야 라벨: 확률}. */
export interface Triage {
  category?: Record<string, number> | null; risks?: Record<string, number | null>; interest?: number | null;
  hidden?: boolean; hidden_by?: string[]; error?: string;
}
/** 이미 만든 소재 표시(recap_shorts/collect/made.py). same_post: 같은 글 · same_original: 같은 원본 · similar: 제목이 비슷한 이야기.
 *  count 는 같은 글로 만든 작업 수, finished 는 영상까지 끝났는지, score 는 제목 겹침(similar 만 의미 있음). */
export interface Made { kind: "same_post" | "same_original" | "similar"; job_id: string; our_title: string; source_title: string; created_at: string; score: number; count: number; finished: boolean; archived: boolean }
export interface Topic { no: number | string; title: string; date?: string; views: number; recommends: number; comments?: number; url: string; triage: Triage | null; made?: Made | null }
/** GET /api/topics?source=dcbest|blind — 디시 실시간 베스트·블라인드 토픽 베스트. 닉네임·IP·회사명은 받지 않는다. */
export interface BestTopic {
  no: number | string; title: string; tag: string; views: number; recommends: number; comments: number; posted_at: string; age_hours: number; url: string;
  category: { id: string; label: string } | null; hidden: boolean; hidden_reason: string[]; triaged: boolean;
  source_check: boolean; source_check_reason: string | null;
  /** 채널 대주제 맞춤 점수: 2 딱 맞음 · 1 조금 · 0 아님. 대주제가 없거나 점수를 못 매겼으면 필드 자체가 없다. */
  fit?: 0 | 1 | 2;
  /** 우리가 이미 만든 글이면 표시(없으면 null). */
  made?: Made | null;
  fc?: TopicFactcheck | null;
}
export interface TopicFactcheck {
  kind: "news" | "general" | "uncertain";
  status: "false" | "partly" | "true" | "unverified" | "failed" | null;
  chip: { label: string; color: "bad" | "warn" | "outline" | "ok" } | null;
  why: string | null; sources: { url: string; outlet: string; date: string | null }[] | null;
  at: string | null; expired: boolean;
}
export interface TopicFcEstimate {
  run_id: string; news: number; todo: number; done: number; classification_usd: number;
  est_usd: number; per_item_usd: number; capped: boolean; selected_nos: (string | number)[];
}
export interface TopicFcStatus {
  run_id: string | null; source: "dcbest" | "blind" | null;
  state: "idle" | "running" | "cancelling" | "done" | "stopped" | "failed";
  done: number; total: number; cost_usd: number; stop_reason: string | null; error: string | null;
  results: { no: string | number; fc: TopicFactcheck }[];
}
export type BestSort = "views" | "comments" | "recent";
/** source: dcbest(디시 실시간 베스트) | blind(블라인드 토픽 베스트 — triage 는 "skipped"). */
export interface BestResponse {
  source: "dcbest" | "blind"; sort: BestSort; hours: number; pages: number; fetched_at: string;
  triage: "ok" | "partial" | "unavailable" | "skipped"; triage_note: string; hidden_count: number; items: BestTopic[];
  /** 채널 대주제(없으면 null). fit: off(대주제 없음) | ok | partial | unavailable — 점수를 못 매겼으면 목록은 원래 순서이고 fit_note 에 이유. */
  channel: { main_topic: string; label: string } | null; fit: "off" | "ok" | "partial" | "unavailable"; fit_note: string; unfit_count: number;
  /** made_count: 만든 글·비슷한 이야기로 표시된 항목 수, made_same_count: 그중 같은 글, made_hidden: hide_made 로 뺀 수. */
  made_count?: number; made_same_count?: number; made_hidden?: number;
  fc_counts: { news: number; checked: number; doubt: number }; fc_ready: boolean; fc_note: string | null;
}
/** GET/PUT /api/channel — main_topic: null(없음) | 프리셋 id | "custom"(직접 입력 글은 custom). */
export interface ChannelPreset { id: string; label: string; desc: string }
export interface Channel { main_topic: string | null; label: string | null; custom: string | null; source: "default" | "local"; presets: ChannelPreset[] }
export interface TopicsResponse { gallery: string; pages: number; fetched_at: string; triage: "ok" | "unavailable"; items: Topic[]; made_count?: number; made_same_count?: number }
/** POST /api/jobs 가 409(code "duplicate")로 알려 주는, 같은 글로 이미 만든 작업(최신 순, 최대 5개). */
export interface DuplicateJob { id: string; title: string; created_at: string; finished: boolean; archived: boolean }

/** take: 우리 해석 문장(대본 규칙 F9). 옛 대본·손으로 쓴 대본은 필드가 없을 수 있다. */
export interface ScriptSentence { text: string; evidence: string | null; source_id: string | null; emphasis: string | null; image_hint: string | null; take?: boolean }
export interface Script { title_lines: string[]; hook: string; sentences: ScriptSentence[]; closing?: string | null; flags?: unknown[] }
/** script_check.json */
export interface ScriptCheck {
  ok?: boolean; deleted?: unknown[]; flags?: unknown[]; warnings?: unknown[];
  checks?: { syllables?: { total: number; range: [number, number]; ok: boolean }; title_lines?: { ok: boolean }; hook?: { ok: boolean; chars?: number; syllables?: number; range?: [number, number]; ends_noun?: boolean } };
  per_sentence_syllables?: number[]; refine?: { applied: boolean; error: string | null; changed?: { where: string; before: string; after: string }[] };
}
/** GET/PUT /script */
export interface ScriptDoc { script: Script; check: ScriptCheck | null; draft: Script | null }

// ---- timeline.json (docs/02_program_design.md 3.1) ----
/** kind: "meme"(전체화면 짤 샷 — 배치표에 넣지 않는다) · cut: "user"(장면 편집기에서 사람이 옮긴 경계) */
export interface Shot { file: string; start: number; end: number; motion: "zoom_in" | "static" | "enter"; focus?: [number, number]; source_id?: string; kind?: string; cut?: string }
export interface Caption { text: string; start: number; end: number; emphasis?: string | null }
export interface Timeline {
  fps: number; size: [number, number]; duration: number; title_lines: string[];
  hook: { text: string; start: number; end: number };
  shots: Shot[]; captions: Caption[];
  audio: { voice: string; bgm?: string | null; bgm_db?: number; sfx_on_cuts?: boolean };
}
/** GET /intro_preview — 렌더러가 그린 인트로 글자 묶음(group, SS 배율 PNG)과 시간 값. 0~t_cut 동안 그림 상자를 이 카드가 덮는다. */
export interface IntroPreview {
  t_pop: number; t_cut: number; pop_s: number; pop_text_scale: number; zoom_pre_per_s: number; zoom_post_per_s: number;
  panel_ratio_end: number; panel_color: string; group_cy: number; ss: number;
  label?: string | null; label_icon?: string | null; lines?: string[] | null; group: string;
}
/** GET /scenes — 실제 응답. timeline 은 포함되지 않아 /media/{id}/timeline.json 을 따로 읽는다.
 *  span: 그 문장 그림이 차지하는 구간 [start, end] · gaps: 어절 경계 후보 시각(영상 시계) · cuts: 저장된 사용자 컷(없으면 null). */
export interface SceneImage { file: string; kind: string | null; score: number | null }
export interface SceneSentence { index: number; role?: string; text: string; start: number; end: number; hint?: string; span?: [number, number]; gaps?: number[]; cuts?: number[] | null; images: SceneImage[] }
export interface SceneCandidate { file: string; kind: string; sentence: number | null; status: string; needs_review?: boolean | null; review_reason?: string | null; recommend_regenerate?: boolean | null }
export interface Scenes { sentences: SceneSentence[]; candidates: SceneCandidate[]; scored_by: string | null; scores: Record<string, Record<string, number>>; note?: string | null }
/** GET /assets — assets.json 한 항목 */
export interface AssetEntry {
  file: string; kind: "cap" | "ai" | string; status: string; src?: string; crop?: [number, number, number, number] | null; attachment?: number;
  needs_review?: boolean | null; review_reason?: string | null; recommend_regenerate?: boolean | null; blur_boxes?: number[][]; blurred?: boolean; hint?: string;
  sensitive?: SensitiveInfo;
  /** role "news": 사실 확인 근거 기사 그림(뉴스 카드). 장면 편집기에서 "뉴스" 칩으로 보이고 가리기 단추는 없다. */
  role?: string; fact_id?: string; outlet?: string; title?: string; source_url?: string; date?: string | null;
}

/** voice/timings.json 의 일부(미리 듣기·문장 구간). */
export interface VoiceTimings { duration: number; sentences: { index: number; role?: string; text: string; start: number; end: number; match?: number; syllables?: number }[] }

// ---- GET/PUT /api/settings — 계약서 §2·§3·§4·§5 ----
/** 키 하나. source: "env"(환경변수가 정함) | "file"(설정 파일) | null. masked 는 "••••" + 끝 4자(짧으면 "••••"), 없으면 null. */
export interface SettingsKey {
  name: string; label: string; group: string; required: boolean;
  env: string; set: boolean; source: "env" | "file" | null; masked: string | null;
}
/** 키 묶음. url 은 "키 받는 곳". need 는 묶음이 어떻게 필요한지 한 줄(예: "둘 중 하나 필수") — 있으면 "필수" 배지 대신 보인다. */
export interface SettingsGroup { id: string; title: string; desc: string; url: string; need?: string }
/** AI 그림 만드는 곳. auto·local·gemini·off. */
export interface ImageEngine { id: string; label: string }
/** 설정 화면에서 받는 중인 모델. got·total 은 바이트, 다 받으면 extracting(압축 푸는 중) → done. */
export interface ModelJob { state: "downloading" | "extracting" | "done" | "error"; got: number; total: number; message: string }
/** models/ 아래 모델 파일. size_mb 는 받는 곳의 크기(내려받은 크기가 아님).
 *  env = 환경변수로 폴더를 정해 두어 앱에서 받지 않음. job = 이번에 앱을 켠 뒤 받기 버튼을 눌렀을 때만. */
export interface ModelFile { id: string; label: string; ok: boolean; size_mb: number; dir: string; missing_effect: string; env: boolean; job: ModelJob | null }
/** GET /api/settings/models · POST /api/settings/models/{id}/install */
export interface ModelsView { models: ModelFile[]; setup_cmd: string }
/** 쓸 모델 한 자리(대본·목소리·이미지). source: "env"(환경변수가 정함, 못 바꿈) | "file"(고른 값) | null(기본값). */
export interface ModelRole { role: "script" | "tts" | "image"; label: string; env: string; default: string; value: string; source: "env" | "file" | null }
/** 목소리 엔진(gemini | supertonic)과 로컬 목소리 번호(0~9). sid_env = 환경변수 RECAP_TTS_SID 가 번호를 정함. */
export interface VoiceSettings {
  engine: string; engines: { id: string; label: string }[];
  /** Gemini 미리 만든 목소리 이름(기본 Charon)과 고를 거리(라벨 = "이름 — 설명"). */
  gemini_voice: string; gemini_voices: { id: string; label: string }[];
  sid: number; sid_env: boolean; sids: number[];
}
/** GET /api/settings/gemini-models — source "api" = 이 키로 쓸 수 있는 모델 목록, "builtin" = 써 본 모델만. */
export interface GeminiModelChoice { id: string; label: string; recommended: boolean }
export interface GeminiModels { source: "api" | "builtin"; note: string; roles: Record<string, GeminiModelChoice[]> }
export interface Settings {
  file: string;
  /** AI 키(Gemini 또는 OpenRouter)가 준비되면 true — 영상을 만들 수 있는 최소 조건. */
  ready: boolean;
  /** ready 가 false 면 이유 한 줄(한국어), 준비됐으면 null. */
  ready_reason?: string | null;
  keys: SettingsKey[];
  groups: SettingsGroup[];
  image_engine: string;
  image_engines: ImageEngine[];
  image_server: { url: string; up: boolean };
  model_roles: ModelRole[];
  voice: VoiceSettings;
  sfx: SfxSettings;
  memes: MemeSettings;
  models: ModelFile[];
  setup_cmd: string;
}
/** 효과음 폴더 하나 — manifest 가 없으면 파일·하위 폴더 이름에서 종류를 읽는다(skipped = 종류를 몰라 뺀 파일 수). */
export interface SfxFolder {
  path: string; exists: boolean; count: number; skipped: number; manifest: boolean; types: Record<string, number>; error: string | null;
  /** 분석한 꾸러미면 원본 폴더 경로(화면에 보이는 이름), 아니면 null */
  label: string | null;
  /** 분석한 꾸러미면 어느 것인지(결과 보기 링크용), 아니면 null */
  pack: { kind: "sfx" | "meme"; id: string } | null;
}
/** dirs 가 비어 있으면(using "default") config 의 기본 꾸러미(default)를 쓴다. */
export interface SfxSettings { using: "default" | "custom"; fill_missing: boolean; dirs: SfxFolder[]; default: SfxFolder[]; known_types: string[]; max_dirs: number }
/** PUT /api/settings 본문. keys·models 에 없는 이름은 그대로 두고, "" 또는 null 은 설정 파일에서 지운다(모델은 기본으로). */
export interface SettingsPatch {
  keys?: Record<string, string | null>; image_engine?: string;
  models?: Record<string, string | null>; voice?: { engine?: string; gemini_voice?: string; sid?: number };
  /** dirs 빈 목록·null = 기본 효과음으로 되돌림 */
  sfx?: { dirs?: string[] | null; fill_missing?: boolean };
  /** dirs 빈 목록·null = 기본 짤로 되돌림 */
  memes?: { dirs?: string[] | null };
}
/** POST /api/settings/test — service 는 묶음 id(gemini | openrouter | cloudflare). */
export interface SettingsTestResult { service: string; ok: boolean; message: string }

/** POST /api/jobs/{id}/capcut — CapCut 초안으로 넘긴 결과. mode: native(그대로) | approx(비슷하게) | skipped(넣지 않음). */
export interface CapCutReportLine { element: string; count: number; mode: "native" | "approx" | "skipped"; note: string }
export interface CapCutResult {
  draft_dir: string; draft_name: string; registered: boolean; in_capcut_folder: boolean; duration: number;
  report: CapCutReportLine[]; warnings: string[]; seconds: number; media_bytes: number; exported_at: string;
  check: { ok: boolean; errors: string[] };
  /** GET 으로 읽은 마지막 기록일 때만: 그 초안 폴더가 아직 있는지 */
  exists?: boolean;
}

// ---- 효과음·짤 폴더 분석(docs/library_analysis.md) ----
export type LibKind = "sfx" | "meme";
export type PackState = "queued" | "measuring" | "listening" | "building" | "done" | "error" | "stopped";
/** 분석 꾸러미 한 개의 요약. groups = 효과음이면 종류별, 짤이면 계열별 개수. listened false = Gemini 없이 이름으로만 정함. */
export interface PackSummary {
  kind: LibKind; id: string; source_dir: string; out_dir: string; state: PackState;
  progress: { done: number; total: number; step: string };
  counts: { files: number; kept: number; dropped: number; groups: Record<string, number> };
  used: boolean; listened: boolean; model: string | null; message: string | null; created_at: string; updated_at: string;
}
/** GET /api/library */
export interface LibraryList { running: { kind: LibKind; id: string } | null; packs: PackSummary[] }
export interface SfxHeard {
  desc: string; speech: "none" | "words" | "sentence"; words: string; music: boolean; source: string;
  owner_clear: boolean; offensive: boolean; earrape: boolean; quality: number; fit: Record<string, number>;
}
export interface SfxItem {
  key: string; name: string; dur: number; active_len: number; heard: SfxHeard | null;
  keep: boolean; type: string | null; score: number | null;
  auto: { keep: boolean; type: string | null; why: string[] };
  user: { keep?: boolean; type?: string };
  out: { id: string; duration: number } | null;
}
export interface MemeSeen {
  reaction: boolean; name: string; meaning: string; beats: string[]; family: string | null;
  has_text: boolean; text: string; hate: boolean; sexual: boolean; gore: boolean; real_person: boolean; quality: number;
}
export interface MemeItem {
  key: string; w: number; h: number; transparent: boolean; dup_of: string | null; seen: MemeSeen | null;
  keep: boolean; name: string; meaning: string; beats: string[]; family: string | null; has_text: boolean; real_person: boolean;
  auto: { keep: boolean; why: string[] };
  user: { keep?: boolean; name?: string; meaning?: string; beats?: string[] };
  out: { id: string } | null;
}
/** GET /api/library/{kind}/{id} — items 는 kind 에 따라 SfxItem 또는 MemeItem. */
export interface PackDetail extends PackSummary {
  groups_def: { id: string; label: string; want: number }[];
  beats_def: { id: string; label: string }[];
  families_def: { id: string; label: string }[];
  items: (SfxItem | MemeItem)[];
}
/** PUT .../items 의 고침 한 줄 — 준 것만 바꾸고, reset 은 사람 값을 지워 자동 값으로 되돌린다. */
export interface LibraryChange { key: string; keep?: boolean; type?: string; name?: string; meaning?: string; beats?: string[]; reset?: true }
/** POST /api/library/add — 목록(manifest.json)이 있으면 바로 설정에, 없으면 분석 시작. */
export type LibraryAddResult = { added: true; settings: Settings } | { analysis: PackSummary };
export interface MemeFolder { path: string; label: string | null; pack: { kind: "sfx" | "meme"; id: string } | null; exists: boolean; count: number; manifest: boolean; error: string | null }
/** using: custom = 내 폴더, default = 같이 든 기본 짤, none = 둘 다 없어 짤을 안 넣음. */
export interface MemeSettings { using: "custom" | "default" | "none"; dirs: MemeFolder[]; default: MemeFolder[]; max_dirs: number }
