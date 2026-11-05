import { ApiError } from "./apiError";
import { fmtWhen } from "./lib";
import { mockLibraryFileUrl, mockRequest, mockWav } from "./mock";
import type { AssetEntry, SensitiveInfo, BestResponse, BestSort, Channel, DuplicateJob, GeminiModels, Health, JobState, ModelsView, Scenes, ScriptDoc, Settings, SettingsPatch, SettingsTestResult, Storage, Timeline, TopicsResponse, VoiceTimings, ReviewJson, IntroPreview, CapCutResult, LibKind, LibraryAddResult, LibraryChange, LibraryList, PackDetail, PackSummary, TopicFcEstimate, TopicFcStatus } from "./types";

const qs = new URLSearchParams(location.search);
if (qs.get("mock") === "1") { try { sessionStorage.setItem("mock", "1"); } catch { /* 저장 못 해도 괜찮음 */ } }
if (qs.get("mock") === "0") { try { sessionStorage.removeItem("mock"); } catch { /* */ } }
export const MOCK = (() => {
  if (qs.get("mock") === "1") return true;
  if (qs.get("mock") === "0") return false;
  try { return sessionStorage.getItem("mock") === "1"; } catch { return false; }
})();

async function errorOf(r: Response): Promise<ApiError> {
  let msg: unknown = `${r.status}`;
  let detail: Record<string, unknown> | undefined;
  try {
    const j = await r.json();
    if (j.detail && typeof j.detail === "object" && !Array.isArray(j.detail)) detail = j.detail;
    msg = j.detail?.message ?? j.detail ?? j.message ?? msg;
  } catch { /* 본문이 JSON 이 아님 */ }
  return new ApiError(typeof msg === "string" ? msg : JSON.stringify(msg), r.status, detail);
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (MOCK) return mockRequest<T>(method, path, body);
  const r = await fetch(path, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : undefined, body: body !== undefined ? JSON.stringify(body) : undefined });
  if (!r.ok) throw await errorOf(r);
  return r.status === 204 ? (undefined as T) : r.json();
}

/** 목소리 미리듣기 본문 — 안 준 값은 서버에 저장된 설정. */
export interface VoicePreviewBody { engine?: string; model?: string; voice?: string; sid?: number; text?: string }

/** allow_duplicate: 같은 글로 이미 만든 작업이 있어도 새로 만든다(없으면 서버가 409 duplicate 로 막는다). */
export type CreateJobBody = ({ url: string; title?: string } | { gallery: string; no: number | string }) & { allow_duplicate?: boolean };
/** 글 출처 — 주제 찾기의 탭. dcbest = 디시 실시간 베스트, blind = 블라인드 토픽 베스트. */
export type TopicSource = "dcbest" | "blind";

export const api = {
  storage: () => req<Storage>("GET", "/api/storage"),
  archive: (id: string) => req<unknown>("POST", `/api/jobs/${id}/archive`),
  health: () => req<Health>("GET", "/api/health"),
  topics: (gallery: string, pages: number) => req<TopicsResponse>("GET", `/api/topics?gallery=${encodeURIComponent(gallery)}&pages=${pages}`),
  topicsBest: (sort: BestSort, hours: number, pages: number, source: TopicSource = "dcbest") =>
    req<BestResponse>("GET", source === "blind" ? `/api/topics?source=blind&sort=${sort}&hours=${hours}` : `/api/topics?source=dcbest&sort=${sort}&hours=${hours}&pages=${pages}`),
  channel: () => req<Channel>("GET", "/api/channel"),
  setChannel: (b: { main_topic: string | null; custom?: string | null }) => req<Channel>("PUT", "/api/channel", b),
  refreshBest: (sort: BestSort, hours: number, pages: number, source: TopicSource = "dcbest") =>
    req<BestResponse>("POST", "/api/topics/refresh", source === "blind" ? { source, sort, hours } : { source, sort, hours, pages }),
  topicFcEstimate: (source: TopicSource, nos: (string | number)[], force = false) =>
    req<TopicFcEstimate>("POST", "/api/topics/factcheck/estimate", { source, nos, force }),
  topicFcRun: (run_id: string) => req<{ started: boolean; total: number; run_id: string }>("POST", "/api/topics/factcheck/run", { run_id }),
  topicFcStatus: () => req<TopicFcStatus>("GET", "/api/topics/factcheck/status"),
  topicFcCancel: (run_id: string) => req<TopicFcStatus>("POST", "/api/topics/factcheck/cancel", { run_id }),
  createJob: (b: CreateJobBody) => req<{ id: string }>("POST", "/api/jobs", b),
  jobs: () => req<JobState[]>("GET", "/api/jobs"),
  job: (id: string) => req<JobState>("GET", `/api/jobs/${id}`),
  next: (id: string) => req<unknown>("POST", `/api/jobs/${id}/continue`),
  /** 사실 확인을 다시 한다(force: 건너뛴 일반 글도 주장을 뽑는다). 대기열을 거치고, 작업이 바쁘면 409. */
  factcheck: (id: string, force = false) => req<unknown>("POST", `/api/jobs/${id}/factcheck`, { force }),
  /** 직접 확인했음: 메모(1~500자)를 남기고 막힌 판정을 넘긴다. 판정이 stop·failed 이고 글이 바뀌지 않았을 때만 받는다. */
  factcheckOverride: (id: string, note: string) => req<unknown>("POST", `/api/jobs/${id}/factcheck/override`, { note }),
  script: (id: string) => req<ScriptDoc>("GET", `/api/jobs/${id}/script`),
  saveScript: (id: string, script: ScriptDoc["script"]) => req<ScriptDoc>("PUT", `/api/jobs/${id}/script`, { script }),
  /** 백그라운드로 돌고 {id,status:"running"} 를 바로 돌려준다 — 끝나면 script 를 다시 읽는다 */
  refineScript: (id: string) => req<unknown>("POST", `/api/jobs/${id}/script/refine`),
  redoVoice: (id: string, index: number) => req<unknown>("POST", `/api/jobs/${id}/voice/redo`, { index }),
  scenes: (id: string) => req<Scenes>("GET", `/api/jobs/${id}/scenes`),
  introPreview: (id: string) => req<{ intro: IntroPreview | null }>("GET", `/api/jobs/${id}/intro_preview`),
  /** cuts: 사용자가 이번에 옮긴 문장(또는 이미 사용자 컷이 있는 문장)만 {문장 번호: [컷 시각...] | null}. null = 자동으로 나누기.
   *  보내지 않은 문장은 서버가 지금 상태 그대로 둔다(그림 수가 그대로면 기존 사용자 컷 유지). */
  saveScenes: (id: string, plan: Record<string, string[]>, cuts?: Record<string, number[] | null>) =>
    req<Scenes>("PUT", `/api/jobs/${id}/scenes`, cuts === undefined ? { plan } : { plan, cuts }),
  assets: (id: string) => req<AssetEntry[]>("GET", `/api/jobs/${id}/assets`),
  regenerate: (id: string, file: string, prompt?: string) => req<unknown>("POST", `/api/jobs/${id}/assets/regenerate`, { file, prompt }),
  blur: (id: string, file: string, boxes: number[][]) => req<{ file: string; boxes: number[][]; cards: string[] }>("POST", `/api/jobs/${id}/assets/blur`, { file, boxes }),
  /** 자극적인 그림의 처리를 사람이 고른다: none=원래대로, gray=흑백만, gray_blur=흑백+흐림. 영상에 쓰는 그림이면 영상이 낡은 것이 된다(video_stale). */
  sensitive: (id: string, file: string, treatment: "none" | "gray" | "gray_blur") =>
    req<{ file: string; sensitive: SensitiveInfo; needs_review: boolean; video_stale: boolean }>("POST", `/api/jobs/${id}/assets/sensitive`, { file, treatment }),
  topic: (id: string) => req<{ topic: string; source_text: string; tone_end?: string }>("GET", `/api/jobs/${id}/topic`),
  saveTopic: (id: string, t: { topic?: string; source_text?: string }) => req<unknown>("PUT", `/api/jobs/${id}/topic`, t),
  render: (id: string) => req<unknown>("POST", `/api/jobs/${id}/render`),
  /** 마지막으로 넘긴 CapCut 초안(없으면 null) */
  capcut: (id: string) => req<{ last: CapCutResult | null }>("GET", `/api/jobs/${id}/capcut`),
  /** 영상에 쓴 타임라인을 CapCut 초안 폴더로(몇 초, 외부 호출 없음). CapCut 이 켜져 있으면 프로젝트 목록에는 안 올린다(warnings). */
  capcutExport: (id: string) => req<CapCutResult>("POST", `/api/jobs/${id}/capcut`),
  capcutOpen: () => req<{ launched: boolean; reason?: string }>("POST", "/api/capcut/open"),
  /** 설정 파일(config/settings.local.json). 키 값은 오지 않고 masked(끝 네 글자)만 온다. */
  settings: () => req<Settings>("GET", "/api/settings"),
  /** 바꿀 키만 보낸다. "" 또는 null = 지움. 응답은 GET 과 같은 꼴. */
  saveSettings: (b: SettingsPatch) => req<Settings>("PUT", "/api/settings", b),
  /** service = 묶음 id(gemini | cloudflare). 키가 맞는지만 본다(HTTP 200, ok/message). */
  testSettings: (service: string) => req<SettingsTestResult>("POST", "/api/settings/test", { service }),
  /** 이 컴퓨터에 Windows 폴더 고르기 창을 띄운다(고를 때까지 기다림). 취소하면 path null, 창을 못 띄우면 501. */
  pickSfxFolder: () => req<{ path: string | null }>("POST", "/api/settings/sfx/pick"),
  // ---- 효과음·짤 폴더 분석(docs/library_analysis.md) ----
  /** 분석 중인 것(running)과 만들어 둔 꾸러미 목록. */
  library: () => req<LibraryList>("GET", "/api/library"),
  /** Windows 폴더 고르기 창 — 취소하면 path null, 창을 못 띄우면 501. */
  libraryPick: (kind: LibKind) => req<{ path: string | null }>("POST", "/api/library/pick", { kind }),
  /** 폴더에 목록(manifest.json)이 있으면 바로 설정에(settings), 없으면 분석 시작(analysis). 다른 분석이 돌면 409. */
  libraryAdd: (kind: LibKind, dir: string) => req<LibraryAddResult>("POST", "/api/library/add", { kind, dir }),
  /** 분석 시작·다시 분석. use 가 true 면 끝난 뒤 설정에 넣는다. */
  libraryAnalyze: (kind: LibKind, dir: string, use?: boolean) => req<PackSummary>("POST", "/api/library/analyze", use === undefined ? { kind, dir } : { kind, dir, use }),
  libraryPack: (kind: LibKind, id: string) => req<PackDetail>("GET", `/api/library/${kind}/${encodeURIComponent(id)}`),
  /** 고친 뒤 꾸러미를 다시 만든 결과 전체. 분석 중이면 409. */
  libraryChange: (kind: LibKind, id: string, changes: LibraryChange[]) => req<PackDetail>("PUT", `/api/library/${kind}/${encodeURIComponent(id)}/items`, { changes }),
  /** 설정의 폴더 목록에 넣기(on)·빼기. */
  libraryUse: (kind: LibKind, id: string, on: boolean) => req<PackSummary>("POST", `/api/library/${kind}/${encodeURIComponent(id)}/use`, { on }),
  libraryStop: (kind: LibKind, id: string) => req<PackSummary>("POST", `/api/library/${kind}/${encodeURIComponent(id)}/stop`),
  /** 만든 꾸러미만 지운다(원본 폴더는 그대로). */
  libraryDelete: (kind: LibKind, id: string) => req<void>("DELETE", `/api/library/${kind}/${encodeURIComponent(id)}`),
  /** 모델 파일 상태(받는 중이면 job 에 진행 상황). */
  models: () => req<ModelsView>("GET", "/api/settings/models"),
  /** 뒤에서 받기 시작(이미 있거나 받는 중이면 그대로). 환경변수로 폴더를 정한 모델은 409. */
  installModel: (id: string) => req<ModelsView>("POST", `/api/settings/models/${id}/install`),
  /** '쓸 모델'의 고를 거리(키가 있으면 Gemini API 목록, 10분 캐시 — refresh 면 다시 받음). */
  geminiModels: (refresh = false) => req<GeminiModels>("GET", `/api/settings/gemini-models${refresh ? "?refresh=true" : ""}`),
  /** 목소리 미리듣기 — wav 를 Blob 으로. cached = 저장해 둔 소리(Gemini 요청을 쓰지 않음). */
  previewVoice: async (b: VoicePreviewBody): Promise<{ blob: Blob; cached: boolean }> => {
    if (MOCK) { await new Promise((ok) => setTimeout(ok, 400)); return { blob: mockWav(), cached: false }; }
    const r = await fetch("/api/settings/voice/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
    if (!r.ok) throw await errorOf(r);
    return { blob: await r.blob(), cached: r.headers.get("X-Preview-Cached") === "1" };
  },
};

/** 꾸러미 파일 주소 — key = 원본 파일(소리·그림), out = 꾸러미 파일(효과음 _sheet = 전체 들어보기). id·key 에 한글·슬래시가 있어도 되게 인코딩.
 *  목 모드: 그림은 자리표시 그림, 소리는 짧은 삐 소리(일부러 못 트는 파일도 있음). */
export function libraryFileUrl(kind: LibKind, id: string, q: { key?: string; out?: string }): string {
  if (MOCK) return mockLibraryFileUrl(kind, q);
  const base = `/api/library/${kind}/${encodeURIComponent(id)}/file`;
  return q.out !== undefined ? `${base}?out=${encodeURIComponent(q.out)}` : `${base}?key=${encodeURIComponent(q.key ?? "")}`;
}

/** "만들기": 같은 글로 이미 만든 작업이 있으면(서버 409 duplicate) 날짜·제목을 보여 주며 한 번 묻고, 그래도 만들겠다고 하면
 *  allow_duplicate 로 다시 보낸다. 사람이 취소하면 null(아무것도 만들지 않음). 다른 오류는 그대로 던진다. */
export async function createJobAsking(body: CreateJobBody): Promise<{ id: string } | null> {
  try {
    return await api.createJob(body);
  } catch (e) {
    if (!(e instanceof ApiError) || e.code !== "duplicate") throw e;
    const jobs = (e.detail?.jobs as DuplicateJob[] | undefined) ?? [];
    const n = Number(e.detail?.count ?? jobs.length);
    const j = jobs[0];
    const what = j ? `${fmtWhen(j.created_at)} · ${j.title}${n > 1 ? ` · 총 ${n}번 만듦` : ""}${j.finished ? "" : " · 영상까지는 안 끝남"}` : e.message;
    if (!confirm(`이미 만든 글입니다 (${what}). 그래도 새로 만들까요?`)) return null;
    return api.createJob({ ...body, allow_duplicate: true });
  }
}

/** 목 모드에서는 mock 작업 id 를 실제 파일이 있는 폴더 이름으로 바꿔 준다. */
export const mediaUrl = (id: string, path: string) => {
  const real = MOCK && id.startsWith("mock") ? "sample" : id;
  return `/media/${real}/${path.replace(/^\/+/, "")}`;
};
export async function fetchText(id: string, path: string): Promise<string | null> {
  try { const r = await fetch(mediaUrl(id, path)); return r.ok ? await r.text() : null; } catch { return null; }
}
export async function fetchTimings(id: string): Promise<VoiceTimings | null> {
  try { const r = await fetch(mediaUrl(id, "voice/timings.json")); return r.ok ? await r.json() : null; } catch { return null; }
}

export async function fetchTimeline(id: string): Promise<Timeline> {
  const r = await fetch(`${mediaUrl(id, "timeline.json")}?t=${Date.now()}`);
  if (!r.ok) throw new Error("타임라인이 아직 없습니다");
  return r.json();
}
export async function fetchReview(id: string, path: string): Promise<ReviewJson | null> {
  try { const r = await fetch(`${mediaUrl(id, path)}?t=${Date.now()}`); return r.ok ? await r.json() : null; } catch { return null; }
}
