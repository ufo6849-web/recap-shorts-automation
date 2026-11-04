// ?mock=1 — 백엔드 없이 화면을 볼 수 있게 하는 가짜 응답. 형식은 docs/03_v2_app_design.md 1.2 와 같고,
// 이미지·소리·영상·대본·타임라인은 목 작업 폴더(아래 MEDIA)의 실제 파일을 /media/<폴더>/... 로 읽는다.
import { ApiError } from "../apiError";
import type { AssetEntry, BestResponse, BestTopic, Channel, GeminiModels, Health, ImageEngine, JobState, ModelFile, ModelRole, Scenes, ScriptDoc, Settings, SettingsGroup, SettingsKey, SettingsPatch, SfxFolder, Step, StepKey, StepStatus, TopicsResponse, VoiceTimings } from "../types";
import { BEST, BEST_FIT, MADE_JOBS, TOPICS } from "./topics";
import { mockLibraryFileUrl as libFileUrl, mockLibraryRequest, mockMemeSettings, mockSettle, mockSfxFolderOf, usedDirs } from "./library";

const PRESETS = [{ id: "daily_drama", label: "일상 논란·썰", desc: "" }, { id: "money_life", label: "돈·생활 경제", desc: "" }, { id: "curious_facts", label: "알고 보면 신기한 상식", desc: "" }];
let channel: { main_topic: string | null; custom: string | null } = { main_topic: "daily_drama", custom: null };
const channelView = (): Channel => ({ ...channel, label: channel.main_topic === "custom" ? channel.custom : (PRESETS.find((p) => p.id === channel.main_topic)?.label ?? null), source: "local", presets: PRESETS });

// ---- 설정(계약서 §2~§5). 처음에는 Gemini 키가 없어서 홈 화면에 "키 넣기" 안내가 뜬다. ----
const KEY_DEFS: [string, string, string, boolean, string][] = [
  ["gemini_api_key", "Gemini API 키", "gemini", false, "RECAP_GEMINI_API_KEY"],
  ["openrouter_api_key", "OpenRouter API 키", "openrouter", false, "RECAP_OPENROUTER_API_KEY"],
  ["cf_account_id", "Cloudflare 계정 ID", "cloudflare", false, "RECAP_CF_ACCOUNT_ID"],
  ["cf_api_token", "Cloudflare API 토큰", "cloudflare", false, "RECAP_CF_API_TOKEN"],
];
const GROUP_DEFS: SettingsGroup[] = [
  { id: "gemini", title: "Gemini", desc: "대본·그림 읽기·목소리·AI 그림. 이 키 하나로 전부 됩니다", url: "https://aistudio.google.com/apikey", need: "둘 중 하나 필수" },
  { id: "openrouter", title: "OpenRouter", desc: "대본(Claude 등)·그림 읽기. Gemini 키가 없으면 Gemini 모델도 이 키로 부릅니다. 목소리는 로컬 엔진을 쓰세요", url: "https://openrouter.ai/settings/keys", need: "둘 중 하나 필수" },
  { id: "cloudflare", title: "Cloudflare (Clef)", desc: "실시간 베스트 주제 분류, AI 그림 글자 점검, 완성 영상 점검. 없으면 이 점검만 건너뜁니다.", url: "https://dash.cloudflare.com/profile/api-tokens" },
];
const ENGINES: ImageEngine[] = [
  { id: "local", label: "로컬 이미지 서버 (사용 한도에 걸리면 Gemini 로 대신)" },
  { id: "gemini", label: "Gemini 이미지 (유료, 장당 약 0.07달러)" },
  { id: "off", label: "AI 그림 안 만듦 — 글 캡처·사진만" },
];
const MODELS: ModelFile[] = [
  { id: "sensevoice", label: "받아쓰기(발음 확인)", ok: false, size_mb: 163, dir: "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
    missing_effect: "없으면 발음 확인·어절 컷을 건너뜁니다", env: false, job: null },
  { id: "supertonic", label: "로컬 목소리(Supertonic)", ok: false, size_mb: 129, dir: "models/sherpa-onnx-supertonic-3-tts-int8-2026-05-11",
    missing_effect: "없으면 로컬 목소리 엔진만 못 씁니다(기본은 Gemini 목소리)", env: false, job: null },
];
const settingsState: { keys: Record<string, string>; image_engine: string; models: Record<string, string>; voice: { engine: string; gemini_voice: string; sid: number }; sfx: { fill_missing: boolean } } = {
  keys: { gemini_api_key: "", openrouter_api_key: "", cf_account_id: "", cf_api_token: "" },
  image_engine: "gemini",
  models: {},
  voice: { engine: "gemini", gemini_voice: "Charon", sid: 0 },
  sfx: { fill_missing: true },
};
const ROLE_DEFS: [ModelRole["role"], string, string, string][] = [
  ["script", "대본", "RECAP_SCRIPT_MODEL", "gemini-4-argon"],
  ["tts", "목소리", "RECAP_TTS_MODEL", "gemini-3.8-flash-tts"],
  ["image", "이미지", "RECAP_GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"],
];
const GEMINI_MODELS: GeminiModels = {
  source: "builtin", note: "Gemini 키가 없어 써 본 모델만 보여 줍니다",
  roles: {
    script: [{ id: "gemini-4-argon", label: "Gemini 4 Argon (gemini-4-argon) — 입력 $2 · 출력 $10 / 100만 토큰", recommended: true },
      { id: "gemini-3.8-flash", label: "Gemini 3.8 Flash (gemini-3.8-flash) — 입력 $0.75 · 출력 $3.75 / 100만 토큰", recommended: true },
      { id: "anthropic/claude-opus-5.5", label: "Claude Opus 5.5 · OpenRouter", recommended: true },
      { id: "anthropic/claude-sonnet-5.5", label: "Claude Sonnet 5.5 · OpenRouter", recommended: true }],
    tts: [{ id: "gemini-3.8-flash-tts", label: "Gemini 3.8 Flash TTS (gemini-3.8-flash-tts) — 입력 $0.5 · 음성 $9 / 100만 토큰", recommended: true },
      { id: "gemini-3.8-flash-lite-tts", label: "Gemini 3.8 Flash Lite TTS (gemini-3.8-flash-lite-tts)", recommended: true }],
    image: [{ id: "gemini-3.1-flash-image", label: "Nano Banana 2 (gemini-3.1-flash-image) — 장당 $0.067", recommended: true },
      { id: "gemini-3-pro-image", label: "Nano Banana Pro (gemini-3-pro-image) — 장당 $0.134", recommended: true }],
  },
};
const SETUP_CMD = ".venv\\Scripts\\python scripts\\setup_models.py";
const mockSetupCmd = () => {
  const miss = MODELS.filter((m) => !m.ok).map((m) => m.id);
  return miss.length === 2 ? `${SETUP_CMD} --all` : miss[0] === "supertonic" ? `${SETUP_CMD} --supertonic` : SETUP_CMD;
};
/** 받기 버튼 흉내: 0.3초마다 10%씩, 다 받으면 1초 동안 압축 푸는 중 → 있음. */
function mockInstall(id: string) {
  const m = MODELS.find((x) => x.id === id);
  if (!m || m.ok || m.job?.state === "downloading" || m.job?.state === "extracting") return;
  const total = m.size_mb * 1_000_000;
  m.job = { state: "downloading", got: 0, total, message: "" };
  const t = window.setInterval(() => {
    const j = m.job!;
    j.got = Math.min(total, j.got + total / 10);
    if (j.got < total) return;
    window.clearInterval(t);
    j.state = "extracting";
    window.setTimeout(() => { m.ok = true; m.job = { ...j, state: "done", message: "받았습니다" }; }, 1000);
  }, 300);
}
const modelsView = () => ({ models: MODELS.map((m) => ({ ...m, job: m.job ? { ...m.job } : null })), setup_cmd: mockSetupCmd() });
const maskedOf = (v: string) => (!v ? null : v.length >= 16 ? `••••${v.slice(-4)}` : "••••");
/** 서버의 readiness() 흉내: AI 키(Gemini 또는 OpenRouter)가 하나라도 있으면 준비, 없으면 이유 한 줄. */
const NEED_KEY = "Gemini 또는 OpenRouter API 키가 필요합니다";
const readyReason = (): string | null => (settingsState.keys.gemini_api_key || settingsState.keys.openrouter_api_key ? null : NEED_KEY);
/** health 의 gemini_key 자리: 준비되면 쓰는 키, 아니면 이유. */
const keyDetail = (): string => {
  const { gemini_api_key: g, openrouter_api_key: o } = settingsState.keys;
  return g && o ? "Gemini·OpenRouter 키 있음" : g ? "Gemini 키로 부름" : o ? "OpenRouter 키로 부름(Gemini 키 없음)" : NEED_KEY;
};
function settingsView(): Settings {
  mockSettle();
  const keys: SettingsKey[] = KEY_DEFS.map(([name, label, group, required, env]) => {
    const v = settingsState.keys[name] ?? "";
    return { name, label, group, required, env, set: !!v, source: v ? "file" : null, masked: maskedOf(v) };
  });
  return {
    file: "config/settings.local.json",
    ready: readyReason() === null, ready_reason: readyReason(),
    keys, groups: GROUP_DEFS,
    image_engine: settingsState.image_engine,
    image_engines: ENGINES,
    model_roles: ROLE_DEFS.map(([role, label, env, def]) => ({ role, label, env, default: def, value: settingsState.models[role] || def,
      source: settingsState.models[role] ? "file" as const : null })),
    voice: { ...settingsState.voice, engines: [{ id: "gemini", label: "Gemini 목소리" }, { id: "supertonic", label: "로컬 목소리 (Supertonic) — 이 컴퓨터에서 만듦, 무료" }],
      gemini_voices: [["Charon", "정보 전달형"], ["Kore", "단단한"], ["Erinome", "또렷한"], ["Puck", "경쾌한"]].map(([id, d]) => ({ id, label: `${id} — ${d}` })),
      sid_env: false, sids: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] },
    image_server: { url: "http://127.0.0.1:8791", up: false },
    memes: mockMemeSettings(),
    sfx: { using: usedDirs.sfx.length ? "custom" : "default", fill_missing: settingsState.sfx.fill_missing,
      dirs: usedDirs.sfx.map(mockSfxFolderOf),
      default: [mockSfxFolder("assets/sfx_packs/user_meme", { awkward: 2, boom: 2, pop: 2, sad: 3 }), mockSfxFolder("assets/sfx_packs/chime", { sparkle: 2 })],
      known_types: ["awkward", "boing", "boom", "ding", "huh", "laugh", "pop", "sad", "sparkle", "whoosh"], max_dirs: 8 },
    models: modelsView().models,
    setup_cmd: mockSetupCmd(),
  };
}
function mockSfxFolder(path: string, types: Record<string, number>, skipped = 0): SfxFolder {
  return { path, exists: true, count: Object.values(types).reduce((a, b) => a + b, 0), skipped, manifest: !skipped, types, error: null, label: null, pack: null };
}
/** 서버와 같은 규칙: 앞뒤 공백을 빼고 1~512자, 출력 가능한 ASCII 만. */
const KEY_RE = /^[\x21-\x7E]{1,512}$/;

/** 그림·영상 파일이 있는 작업 폴더. 목 작업 id 는 전부 "mock" 으로 시작하고 api.mediaUrl 이 이 폴더로 바꿔 준다. */
const MEDIA = "sample";
const REAL = "mock_sample";
const STEPS: [StepKey, string, boolean][] = [
  ["source", "글 가져오기", false], ["read", "자료 읽기", false], ["script", "대본", true], ["voice", "목소리", true],
  ["assets", "자료 화면", false], ["scenes", "장면 배치", true], ["render", "영상 만들기", false], ["review", "검수", true],
];
const iso = (minAgo: number) => new Date(Date.now() - minAgo * 60000).toISOString();
const wait = (ms = 120) => new Promise((r) => setTimeout(r, ms));

const SUBS: Partial<Record<StepKey, string[]>> = {
  source: ["글 본문 가져오기", "첨부 이미지 저장", "글 캡처"],
  read: ["이미지 글자 읽기", "이미지 분류", "개인정보 가림 확인"],
  script: ["초안 쓰기", "다듬기", "사실 확인"],
  voice: ["한 번에 녹음", "문장별로 나누기", "받아쓰기 확인"],
  assets: ["글 캡처 카드 만들기", "AI 그림 만들기", "그림 검사"],
  scenes: ["문장과 그림 어울림 재기", "겹치지 않게 놓기"],
  render: ["프레임 그리기", "mp4 묶기"],
  review: ["영상 점검", "규격표와 재기", "설명 문구 만들기"],
};
const NOTES: Partial<Record<StepKey, (string | null)[]>> = {
  script: ["236초", "118초", "통과"], voice: ["32.7초", "9문장", "평균 일치 0.97"], assets: ["12장", "12 / 12장", "글자 섞임 0장"],
};

function makeSteps(cur: StepKey | "all", status: StepStatus, progress: number): Step[] {
  const idx = cur === "all" ? STEPS.length : STEPS.findIndex((s) => s[0] === cur);
  return STEPS.map(([key, label], i) => {
    const st: StepStatus = i < idx ? "done" : i === idx ? status : "todo";
    const subs = SUBS[key] ?? [];
    const p = st === "done" || st === "waiting" ? 1 : st === "running" ? progress : 0;
    const sub = subs.map((l, k) => {
      const at = (k + 1) / subs.length;
      const ss: StepStatus = st === "done" || st === "waiting" ? "done" : st === "running" ? (p >= at ? "done" : p >= at - 1 / subs.length ? "running" : "todo") : "todo";
      return { label: l, status: ss, note: ss === "done" ? (NOTES[key]?.[k] ?? null) : null };
    });
    return {
      key, label, status: st, progress: p, sub,
      detail: st === "running" ? `${label} 하는 중` : st === "waiting" ? "확인을 기다리고 있습니다" : null,
      started_at: i <= idx ? iso(30 - i * 3) : null, ended_at: st === "done" ? iso(28 - i * 3) : null, error: null,
    };
  });
}

const IMAGES = [
  "assets/cap/c01_p1.png", "assets/cap/c01_p2.png", "assets/cap/c02_p1.png", "assets/cap/c02_p2.png", "assets/cap/c03_p1.png", "assets/cap/c03_p2.png",
  "assets/cap/c04.png", "assets/ai/ai_00.png", "assets/ai/ai_01.png",
];
const LOGS = [
  "글 가져오기 시작", "본문 1,204자, 첨부 이미지 9장 저장", "이미지 글자 읽기 끝 (9장)", "대본 초안 236초", "다듬기 118초 — 문장 9개, 음절 210",
  "사실 확인 통과", "대본 확인 완료", "Gemini 목소리 합성 시작", "문장 나누기 9개", "받아쓰기 확인 평균 0.97", "자료 화면 12장 준비", "장면 배치 끝", "영상 만들기 끝 (33.2초)",
];

function statusOf(steps: Step[]): JobState["status"] {
  return steps.some((s) => s.status === "error") ? "error" : steps.some((s) => s.status === "running") ? "running" : steps.some((s) => s.status === "waiting") ? "waiting" : "done";
}
function makeJob(id: string, title: string, no: number, cur: StepKey | "all", status: StepStatus, progress: number, minAgo: number): JobState {
  const steps = makeSteps(cur, status, progress);
  const done = (k: StepKey) => { const s = steps.find((x) => x.key === k)!; return s.status === "done" || s.status === "waiting"; };
  return {
    id, title, created_at: iso(minAgo), status: statusOf(steps), steps,
    source: { gallery: "baseball_new13", no, url: `https://gall.dcinside.com/board/view/?id=baseball_new13&no=${no}`, title },
    artifacts: {
      script: done("script") ? "script_final/script.json" : undefined, voice: done("voice") ? "voice/voice.wav" : undefined,
      video: done("render") ? "out/final.mp4" : undefined, images: done("assets") ? IMAGES : [],
      review: done("render") ? "out/review.json" : undefined, description: cur === "review" || cur === "all" ? "out/description.txt" : undefined,
      timings: done("voice") ? "voice/timings.json" : undefined,
    },
    log: LOGS.slice(0, 2 + steps.filter((s) => s.status === "done").length * 2).map((msg, i) => ({ t: iso(minAgo - i * 1.2), msg })),
    progress: 0.5, thumbnail: done("source") ? "post/img_1.png" : null,
  };
}

// ---- 메모리 저장소 ----
const jobs: JobState[] = [
  makeJob("mock_run", "[가상] 가게 벽 뜯었더니 삼십 년 전 편지가 나왔다", 1001, "voice", "running", 0.62, 6),
  makeJob("mock_script", "[가상] 벽 속 봉투 주인을 찾아 나선 가게", 1007, "script", "waiting", 1, 41),
  makeJob(REAL, "[가상] 삼십 년 된 편지의 주인은 같은 동네에 살았다", 1001, "review", "waiting", 1, 190),
];
const rate = new Map<string, number>([["mock_run", 0.012]]);
let last = Date.now();
function tick() {
  const now = Date.now(), dt = (now - last) / 1000; last = now;
  for (const j of jobs) {
    const run = j.steps.find((s) => s.status === "running");
    if (!run) continue;
    run.progress = Math.min(1, run.progress + (rate.get(j.id) ?? 0.05) * dt);
    run.sub?.forEach((s, k, a) => { const at = (k + 1) / a.length; s.status = run.progress >= at ? "done" : run.progress >= at - 1 / a.length ? "running" : "todo"; });
    if (run.progress >= 1) finish(j, run);
  }
}
function finish(j: JobState, run: Step) {
  const human = STEPS.find((s) => s[0] === run.key)![2];
  run.status = human ? "waiting" : "done"; run.progress = 1; run.detail = human ? "확인을 기다리고 있습니다" : null; run.sub?.forEach((s) => (s.status = "done"));
  j.log.push({ t: new Date().toISOString(), msg: `${run.label} ${human ? "끝 — 확인 기다림" : "끝"}` });
  if (!human) startNext(j);
  j.status = statusOf(j.steps);
}
function startNext(j: JobState) {
  const nx = j.steps.find((s) => s.status === "todo");
  if (nx) { nx.status = "running"; nx.progress = 0; nx.detail = `${nx.label} 하는 중`; nx.sub?.forEach((s) => (s.status = "todo")); j.log.push({ t: new Date().toISOString(), msg: `${nx.label} 시작` }); }
}

/** 대본·그림 목록·장면은 /media/<MEDIA>/... 에 들어 있는 실제 작업 폴더 파일을 그대로 읽는다. */
async function real<T>(path: string): Promise<T> { const r = await fetch(`/media/${MEDIA}/${path}`); if (!r.ok) throw new Error(`mock: ${path} ${r.status}`); return r.json(); }

let scriptCache: ScriptDoc | null = null;
let assetsCache: AssetEntry[] | null = null;
let planCache: Record<string, string[]> | null = null;
async function getScript(): Promise<ScriptDoc> {
  if (!scriptCache) scriptCache = { script: await real("script_final/script.json"), check: await real("script_final/script_check.json"), draft: null };
  return scriptCache;
}
async function getAssets(): Promise<AssetEntry[]> { return (assetsCache ??= await real<AssetEntry[]>("assets/assets.json")); }
async function getScenes(): Promise<Scenes> {
  const doc = await real<{ plan: Record<string, string[]>; scores: Record<string, Record<string, number>>; scored_by: string; note: string }>("shot_plan.json");
  const tm = await real<VoiceTimings>("voice/timings.json");
  const plan = planCache ?? doc.plan;
  const all = await getAssets();
  const kinds = new Map(all.map((e) => [e.file, e]));
  return {
    sentences: tm.sentences.map((s) => ({ index: s.index, role: s.role, text: s.text, start: s.start, end: s.end,
      images: (plan[String(s.index)] ?? []).map((f) => ({ file: f, kind: kinds.get(f)?.kind ?? null, score: doc.scores[String(s.index)]?.[f] ?? null })) })),
    candidates: all.map((e) => ({ file: e.file, kind: e.kind, sentence: null, status: e.status, needs_review: e.needs_review, review_reason: e.review_reason, recommend_regenerate: e.recommend_regenerate })),
    scored_by: doc.scored_by, scores: doc.scores, note: doc.note,
  };
}

/** 미리듣기 흉내: 0.6초짜리 440Hz 소리(16비트 모노 wav). */
export function mockWav(): Blob {
  const sr = 24000, n = Math.round(sr * 0.6), buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const str = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.round(Math.sin((2 * Math.PI * 440 * i) / sr) * 6000), true);
  return new Blob([buf], { type: "audio/wav" });
}

let pickCount = 0;
/** 효과음·짤 꾸러미 파일 주소(목 전용). */
export const mockLibraryFileUrl = (kind: "sfx" | "meme", q: { key?: string; out?: string }) => libFileUrl(kind, q, mockWav);

export async function mockRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  await wait();
  tick();
  const p = path.split("?")[0];
  let m: RegExpMatchArray | null;
  const ok = (v: unknown) => JSON.parse(JSON.stringify(v ?? null)) as T;
  if (p === "/api/health") return ok({
    gemini_key: { ok: readyReason() === null, detail: keyDetail() },
    cf_keys: { ok: true, detail: "Cloudflare 키 있음" }, gpu: { ok: false, detail: "GPU 없음" },
    image_server: { ok: false, detail: "AI 그림 꺼짐 — 캡처 카드만" },
    ffmpeg: { ok: true, detail: "ffmpeg 있음" },
    asr_model: { ok: false, detail: "받아쓰기 모델 없음 — 발음 확인을 건너뜁니다" },
  } satisfies Health);
  if (p === "/api/settings/models") return ok(modelsView());
  if (p === "/api/settings/gemini-models") { await wait(300); return ok(GEMINI_MODELS); }
  const mi = /^\/api\/settings\/models\/(\w+)\/install$/.exec(p);
  if (mi && method === "POST") {
    if (!MODELS.some((m) => m.id === mi[1])) throw new ApiError("모르는 모델입니다", 404, { message: "모르는 모델입니다" });
    mockInstall(mi[1]);
    return ok(modelsView());
  }
  if (p.startsWith("/api/library")) {
    if (p === "/api/library/pick") await wait(400);
    const r = mockLibraryRequest(method, p, body, { settings: settingsView, pick: ++pickCount });
    if (r !== undefined) return ok(r);
  }
  if (p === "/api/settings/sfx/pick") { await wait(400); return ok({ path: "C:\Users\나\효과음" }); }
  if (p === "/api/settings" || p === "/api/settings/test") {
    if (p === "/api/settings/test") {
      await wait(400);
      const svc = (body as { service?: string }).service ?? "";
      const has = (...names: string[]) => names.every((n) => !!settingsState.keys[n]);
      if (!KEY_DEFS.some(([, , g]) => g === svc)) throw new ApiError("모르는 서비스입니다", 400, { message: "모르는 서비스입니다" });
      if (svc === "gemini") return ok({ service: svc, ok: has("gemini_api_key"), message: has("gemini_api_key") ? "연결됨" : "키가 없습니다" });
      if (svc === "openrouter") return ok({ service: svc, ok: has("openrouter_api_key"), message: has("openrouter_api_key") ? "연결됨" : "키가 없습니다" });
      return ok({ service: svc, ok: has("cf_account_id", "cf_api_token"), message: has("cf_account_id", "cf_api_token") ? "연결됨 (Workers AI 사용 가능)" : has("cf_account_id") || has("cf_api_token") ? "계정 ID 와 토큰이 둘 다 필요합니다" : "키가 없습니다" });
    }
    if (method === "PUT") {
      const b = (body ?? {}) as SettingsPatch;
      for (const [name, raw] of Object.entries(b.keys ?? {})) {
        if (!KEY_DEFS.some(([n]) => n === name)) throw new ApiError("모르는 키입니다", 400, { message: "모르는 키입니다" });
        const v = String(raw ?? "").trim();
        if (v && !KEY_RE.test(v)) throw new ApiError("키에 쓸 수 없는 글자가 있습니다", 400, { message: "키에 쓸 수 없는 글자가 있습니다" });
        settingsState.keys[name] = v;
      }
      if (b.image_engine !== undefined) {
        if (!ENGINES.some((e) => e.id === b.image_engine)) throw new ApiError("모르는 그림 방식입니다", 400, { message: "모르는 그림 방식입니다" });
        settingsState.image_engine = b.image_engine;
      }
      for (const [role, v] of Object.entries(b.models ?? {})) {
        if (v) settingsState.models[role] = v; else delete settingsState.models[role];
      }
      if (b.voice?.engine === "supertonic" && !MODELS.find((m) => m.id === "supertonic")?.ok)
        throw new ApiError("로컬 목소리 모델이 없습니다 — 아래 '모델 파일'에서 먼저 받으세요", 400, { message: "로컬 목소리 모델이 없습니다" });
      if (b.voice) settingsState.voice = { ...settingsState.voice, ...b.voice };
      if (b.sfx?.dirs !== undefined) {
        const dirs = [...new Set((b.sfx.dirs ?? []).map((d) => d.trim()).filter(Boolean))];
        if (dirs.some((d) => d.includes("없는"))) throw new ApiError("폴더가 없습니다", 400, { message: "폴더가 없습니다" });
        usedDirs.sfx = dirs;
      }
      if (b.memes?.dirs !== undefined) {
        const dirs = [...new Set((b.memes.dirs ?? []).map((d) => d.trim()).filter(Boolean))];
        if (dirs.some((d) => d.includes("없는"))) throw new ApiError("폴더가 없습니다", 400, { message: "폴더가 없습니다" });
        usedDirs.meme = dirs;
      }
      if (typeof b.sfx?.fill_missing === "boolean") settingsState.sfx.fill_missing = b.sfx.fill_missing;
      await wait(250);
    }
    return ok(settingsView());
  }
  if (p === "/api/topics/refresh" || (p === "/api/topics" && (path.includes("source=dcbest") || path.includes("source=blind")))) {
    await wait(500);
    const src = path.includes("source=blind") || (body as { source?: string } | undefined)?.source === "blind" ? "blind" : "dcbest";
    const sort = (/sort=(\w+)/.exec(path)?.[1] ?? (body as { sort?: string } | undefined)?.sort ?? "views") as "views" | "comments" | "recent";
    if (src === "blind") {      // 블라인드: Clef 분류 없음(triage skipped), 쪽 넘김 없음 — 화면 확인용 가짜 두 줄
      return ok({ source: "blind", sort, hours: 48, pages: 1, fetched_at: iso(1), triage: "skipped",
        triage_note: "블라인드는 Clef 분류 없이 그대로 보여 줍니다", hidden_count: 0,
        items: [{ no: "aaaa0001", title: "[가상] 회사 근처 점심값이 2년 만에 두 배가 된 이유", tag: "블라블라", views: 12840, recommends: 41, comments: 96, posted_at: iso(180), age_hours: 3, url: "https://www.teamblind.com/kr/post/aaaa0001", category: null, hidden: false, hidden_reason: [], triaged: false, source_check: false, source_check_reason: null, made: null },
          { no: "bbbb0002", title: "[가상] 요즘 신입이 첫 월급으로 가장 많이 산 것", tag: "토픽 베스트", views: 7320, recommends: 25, comments: 33, posted_at: iso(540), age_hours: 9, url: "https://www.teamblind.com/kr/post/bbbb0002", category: null, hidden: false, hidden_reason: [], triaged: false, source_check: false, source_check_reason: null, made: null }],
        channel: null, fit: "off", fit_note: "", unfit_count: 0 } satisfies BestResponse);
    }
    let items: BestTopic[] = [...BEST].sort((a, b) => sort === "comments" ? b.comments - a.comments : sort === "recent" ? a.age_hours - b.age_hours : b.views - a.views);
    const label = channelView().label;
    if (channel.main_topic && label) {     // 서버처럼: 딱 맞음 → 조금 → 아님, 같은 묶음 안은 원래 순서
      items = items.map((i) => ({ ...i, fit: BEST_FIT[Number(i.no)] })).sort((a, b) => (b.fit ?? 0) - (a.fit ?? 0));
    }
    const madeN = items.filter((i) => i.made);
    return ok({ source: "dcbest", sort, hours: 48, pages: 3, fetched_at: iso(1), triage: "ok", triage_note: "", hidden_count: items.filter((i) => i.hidden).length, items,
      made_count: madeN.length, made_same_count: madeN.filter((i) => i.made!.kind === "same_post").length, made_hidden: 0,
      channel: channel.main_topic && label ? { main_topic: channel.main_topic, label } : null, fit: channel.main_topic ? "ok" : "off", fit_note: "", unfit_count: items.filter((i) => i.fit === 0).length });
  }
  if (p === "/api/channel") {
    if (method === "PUT") { const b = body as { main_topic: string | null; custom?: string | null }; channel = { main_topic: b.main_topic, custom: b.custom ?? channel.custom }; }
    return ok(channelView());
  }
  if (p === "/api/topics") { await wait(500); return ok({ gallery: "baseball_new13", pages: 2, fetched_at: iso(3), triage: "ok", items: TOPICS } satisfies TopicsResponse); }
  if (p === "/api/jobs" && method === "GET") return ok([...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at)));
  if (p === "/api/jobs" && method === "POST") {
    const b = body as { url?: string; gallery?: string; no?: number; allow_duplicate?: boolean };
    const reason = readyReason();
    if (reason) {      // 서버와 같은 규칙: 준비가 안 되면 만들지 않는다(409 no_key, 문구는 준비 이유)
      throw new ApiError(reason, 409, { code: "no_key", message: reason });
    }
    const no = b.no ?? (Number(/no=(\d+)/.exec(b.url ?? "")?.[1]) || 1001);
    const prior = MADE_JOBS[no];       // 서버처럼: 이미 만든 글이면 allow_duplicate 없이는 409
    if (prior && !b.allow_duplicate) {
      const msg = `이미 만든 글입니다${prior.count > 1 ? ` (${prior.count}번)` : ""}: ${prior.created_at.slice(5, 16).replace("T", " ")} · ${prior.title}`;
      throw new ApiError(msg, 409, { code: "duplicate", message: msg, count: prior.count, jobs: [{ id: "mock_made", title: prior.title, created_at: prior.created_at, finished: prior.finished, archived: true }] });
    }
    const t = TOPICS.find((x) => x.no === no);
    const id = `mock_${Date.now()}`;
    const j = makeJob(id, t?.title ?? "새 작업", no, "source", "running", 0.05, 0);
    j.log = [{ t: new Date().toISOString(), msg: "글 가져오기 시작" }];
    rate.set(id, 0.09);
    jobs.unshift(j);
    return ok({ id });
  }
  if (p === "/api/capcut/open") return ok({ launched: true });
  if ((m = p.match(/^\/api\/jobs\/([^/]+)(\/.*)?$/))) {
    const j = jobs.find((x) => x.id === m![1]);
    if (!j) throw new Error("작업을 찾을 수 없습니다");
    const sub = m[2] ?? "";
    if (sub === "" && method === "GET") return ok(j);
    if (sub === "/capcut") {
      if (method === "GET") return ok({ last: null });
      await wait(900);
      return ok({ draft_dir: "C:/Users/me/AppData/Local/CapCut/User Data/Projects/com.lveditor.draft/" + j.title + "_1102-120000", draft_name: j.title + "_1102-120000",
        registered: true, in_capcut_folder: true, duration: 35.2, warnings: [], seconds: 4.2, media_bytes: 18_400_000, exported_at: new Date().toISOString(), check: { ok: true, errors: [] },
        report: [{ element: "그림 컷", count: 15, mode: "native", note: "컷마다 사진 하나" }, { element: "인트로 카드", count: 1, mode: "approx", note: "짧은 영상" }, { element: "자막", count: 24, mode: "native", note: "고칠 수 있는 글자" }] });
    }
    if (sub === "/continue") {
      const w = j.steps.find((s) => s.status === "waiting");
      if (w) { w.status = "done"; j.log.push({ t: new Date().toISOString(), msg: `${w.label} 확인` }); startNext(j); }
      j.status = statusOf(j.steps);
      return ok({ ok: true });
    }
    if (sub === "/script") {
      if (method === "PUT") { const d = await getScript(); d.script = (body as { script: ScriptDoc["script"] }).script; }
      return ok(await getScript());
    }
    if (sub === "/script/refine") { await wait(300); return ok({ id: j.id, status: "running" }); }
    if (sub === "/topic") return ok({ topic: "삼십 년 된 편지의 주인", source_text: "[가상] 오래된 가게를 고치던 목수가 벽 안에서 먼지 묻은 봉투 하나를 꺼냈다. 봉투에는 이름과 지금은 없는 골목 이름이 적혀 있었다.", tone_end: "" });
    if (sub === "/assets") return ok(await getAssets());
    if (sub === "/assets/blur") {
      const b = body as { file: string; boxes: number[][] };
      const all = await getAssets();
      const src = all.find((e) => e.file === b.file)?.src;
      const cards: string[] = [];
      for (const e of all) if (e.src === src) { e.blur_boxes = b.boxes; e.blurred = b.boxes.length > 0; e.needs_review = false; e.review_reason = b.boxes.length ? "가림 확인됨" : "가릴 곳 없음으로 확인됨"; cards.push(e.file); }
      return ok({ file: b.file, boxes: b.boxes, cards });
    }
    if (sub === "/assets/sensitive") {
      const b = body as { file: string; treatment: "none" | "gray" | "gray_blur" };
      const e = (await getAssets()).find((x) => x.file === b.file);
      if (e) { e.sensitive = { level: "none", kind: "none", ...e.sensitive, treatment: b.treatment === "none" ? "none_by_user" : b.treatment, user: true, flagged: false }; e.needs_review = false; }
      return ok({ file: b.file, sensitive: e?.sensitive, needs_review: false, video_stale: false });
    }
    if (sub === "/voice/redo" || sub === "/assets/regenerate" || sub === "/render") {
      const key: StepKey = sub === "/voice/redo" ? "voice" : sub === "/render" ? "render" : "assets";
      const st = j.steps.find((s) => s.key === key)!;
      st.status = "running"; st.progress = 0.05; rate.set(j.id, 0.25);
      j.status = "running"; j.log.push({ t: new Date().toISOString(), msg: `${st.label} 다시 시작` });
      return ok({ ok: true });
    }
    if (sub === "/scenes") {
      if (method === "PUT") planCache = (body as { plan: Record<string, string[]> }).plan;
      return ok(await getScenes());
    }
  }
  throw new Error(`mock: ${method} ${path}`);
}
