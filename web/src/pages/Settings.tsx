import { Check, ChevronLeft, Copy, Download, ExternalLink, Eye, EyeOff, FolderOpen, Play, Plus, RefreshCw, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type VoicePreviewBody } from "../api";
import { go, useCopy, useLoad } from "../hooks";
import { Button, Card, Chip, ICON, Progress, Status, cx } from "../ui";
import type { GeminiModelChoice, GeminiModels, LibKind, MemeFolder, MemeSettings, ModelFile, ModelRole, ModelsView, PackSummary, Settings, SettingsGroup, SettingsKey, SettingsPatch, SfxFolder, SfxSettings } from "../types";

const idOf = (k: SettingsKey) => `set-${k.name}`;
const modelBusy = (m: ModelFile) => m.job?.state === "downloading" || m.job?.state === "extracting";

/** 모델 한 줄: 없으면 받기 버튼, 받는 중이면 진행 막대(받은 MB → 압축 푸는 중), 실패하면 이유와 다시 받기. */
function ModelRow({ m, starting, onInstall }: { m: ModelFile; starting: boolean; onInstall: () => void }) {
  const j = m.job;
  const busy = modelBusy(m);
  const mb = (b: number) => (b / 1_000_000).toFixed(0);
  return (
    <div className="border-b border-line py-3 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-medium">{m.label}</span>
            {m.ok ? <Status tone="ok">있음</Status> : busy ? <Status tone="mute" pulse>받는 중</Status> : <Status tone="warn">없음</Status>}
          </div>
          <div className="mt-0.5 break-all font-mono text-[12px] text-sub">{m.dir}</div>
          <div className="mt-0.5 text-[13px] text-sub">{m.missing_effect}</div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="text-[13px] text-sub">{m.size_mb} MB</span>
          {!m.ok && !m.env && !busy && (
            <Button variant="primary" size="sm" icon={<Download size={14} {...ICON} />} disabled={starting} onClick={onInstall}>
              {j?.state === "error" ? "다시 받기" : "받기"}
            </Button>
          )}
        </div>
      </div>
      {busy && j && (
        <div role="status" className="mt-2">
          <Progress value={j.state === "extracting" ? 1 : j.got / Math.max(j.total, 1)} />
          <div className="mt-1 text-[13px] text-sub">{j.state === "extracting" ? "압축 푸는 중…" : `받는 중 ${mb(j.got)} / ${mb(j.total)} MB`}</div>
        </div>
      )}
      {!m.ok && j?.state === "error" && <p role="status" className="mt-2 text-[13px] text-bad-text">{j.message}</p>}
      {!m.ok && m.env && <p className="mt-1 text-[13px] text-sub">환경변수로 폴더를 정해 두어 여기서 받지 않습니다.</p>}
    </div>
  );
}

/** 키 상태 칩: 저장됨(끝 네 글자) · 환경변수에서 읽음 · 없음. */
function KeyState({ k }: { k: SettingsKey }) {
  if (!k.set) return <Chip tone={k.required ? "warn" : "mute"}>없음</Chip>;
  if (k.source === "env") return <Chip tone="mute">환경변수 {k.env} 에서 읽음</Chip>;
  return <Chip tone="ok">저장됨 {k.masked ?? "••••"}</Chip>;
}

function KeyField({ k, value, onChange, onClear, busy }: {
  k: SettingsKey; value: string; onChange: (v: string) => void; onClear: () => void; busy: boolean;
}) {
  const [show, setShow] = useState(false);
  const id = idOf(k);
  const env = k.source === "env";
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <label htmlFor={id} className="text-[13px] font-medium">{k.label}</label>
        <KeyState k={k} />
        {k.set && !env && (
          <button type="button" onClick={onClear} disabled={busy} aria-label={`${k.label} 지우기`}
            className="press ml-auto shrink-0 rounded-lg px-2 py-1 text-[12px] font-medium text-sub hover:bg-fill hover:text-bad-text disabled:opacity-40">지우기</button>
        )}
      </div>
      <div className="relative mt-1.5">
        <input id={id} type={show ? "text" : "password"} value={value} onChange={(e) => onChange(e.target.value)}
          autoComplete="off" spellCheck={false} disabled={env} placeholder="새 키 붙여넣기"
          aria-describedby={env ? `${id}-env` : undefined}
          className={cx("h-10 w-full rounded-[10px] border border-line bg-fill pl-3 text-[14px] text-ink outline-none placeholder:text-sub/70 focus:border-accent disabled:cursor-not-allowed disabled:opacity-60",
            env || !value ? "pr-3" : "pr-11")} />
        {!env && value.length > 0 && (
          <button type="button" onClick={() => setShow(!show)} aria-label={show ? "키 가리기" : "키 보기"} aria-pressed={show}
            className="press absolute right-1 top-1 inline-flex h-8 w-8 items-center justify-center rounded-lg text-sub hover:text-ink">
            {show ? <EyeOff size={16} {...ICON} /> : <Eye size={16} {...ICON} />}
          </button>
        )}
      </div>
      {env && <p id={`${id}-env`} className="mt-1 text-[13px] text-sub">환경변수로 정해져 있어 여기서 바꿀 수 없습니다.</p>}
    </div>
  );
}

const SELECT = "h-10 w-full rounded-[10px] border border-line bg-fill px-3 text-[14px] text-ink outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-60";

/** Gemini 모델 고르기: 써 본 모델 → 그 밖의 모델. 지금 값이 목록에 없으면(환경변수·예전 값) 맨 위에 그대로 둔다. */
function ModelSelect({ r, choices, busy, onPick }: { r: ModelRole; choices: GeminiModelChoice[] | undefined; busy: boolean; onPick: (id: string) => void }) {
  const list = choices ?? [];
  const opt = (c: GeminiModelChoice) => <option key={c.id} value={c.id}>{c.label}{c.id === r.default ? " · 기본" : ""}</option>;
  const rec = list.filter((c) => c.recommended);
  const rest = list.filter((c) => !c.recommended);
  return (
    <>
      <select id={`model-${r.role}`} value={r.value} disabled={busy || r.source === "env"} onChange={(e) => onPick(e.target.value)} className={SELECT}>
        {!list.some((c) => c.id === r.value) && <option value={r.value}>{r.value}{r.value === r.default ? " · 기본" : ""}</option>}
        {rec.length > 0 && <optgroup label="써 본 모델">{rec.map(opt)}</optgroup>}
        {rest.length > 0 && <optgroup label="그 밖의 모델">{rest.map(opt)}</optgroup>}
      </select>
      {r.source === "env" && <p className="mt-1 text-[13px] text-sub">환경변수 {r.env} 로 정해져 있어 여기서 바꿀 수 없습니다.</p>}
    </>
  );
}

const PREVIEW_TEXT = "요즘 커뮤니티에서 난리 난 이야기, 삼십 초 만에 정리해 줄게.";

/** 목소리 미리듣기: 지금 고른 엔진·모델·목소리로 예시 문장을 만들어 바로 튼다. 같은 값이면 서버가 저장해 둔 소리를 준다. */
function VoicePreview({ body, gemini }: { body: VoicePreviewBody; gemini: boolean }) {
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "making" | "playing">("idle");
  const [note, setNote] = useState<{ ok: boolean; msg: string } | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const url = useRef<string | null>(null);

  const stop = () => { audio.current?.pause(); setState("idle"); };
  useEffect(() => () => { audio.current?.pause(); if (url.current) URL.revokeObjectURL(url.current); }, []);
  // 엔진·목소리·모델을 바꾸면 이전 결과 안내를 지우고 틀던 소리를 멈춘다
  const sig = JSON.stringify(body);
  useEffect(() => { stop(); setNote(null); }, [sig]);

  async function play() {
    if (state === "playing") { stop(); return; }
    setState("making");
    setNote(null);
    try {
      const { blob, cached } = await api.previewVoice({ ...body, text: text.trim() || undefined });
      if (url.current) URL.revokeObjectURL(url.current);
      url.current = URL.createObjectURL(blob);
      audio.current?.pause();
      const a = new Audio(url.current);
      audio.current = a;
      a.onended = () => setState("idle");
      await a.play();
      setState("playing");
      setNote({ ok: true, msg: cached ? "저장해 둔 소리를 틀었습니다(요청 안 씀)" : gemini ? "새로 만들었습니다(Gemini 목소리 요청 1회)" : "새로 만들었습니다" });
    } catch (e) {
      setState("idle");
      setNote({ ok: false, msg: (e as Error).message });
    }
  }

  return (
    <div className="mt-3 rounded-[10px] border border-line bg-fill p-3">
      <label htmlFor="voice-preview-text" className="text-[11px] font-medium text-sub">미리듣기 문장</label>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <input id="voice-preview-text" value={text} maxLength={120} onChange={(e) => setText(e.target.value)} placeholder={PREVIEW_TEXT}
          className="h-9 min-w-0 flex-1 rounded-[10px] border border-line bg-surface px-3 text-[14px] text-ink outline-none placeholder:text-sub/70 focus:border-accent" />
        <Button size="sm" variant="primary" disabled={state === "making"} onClick={play}
          icon={state === "playing" ? <Square size={14} {...ICON} /> : <Play size={14} {...ICON} />}>
          {state === "making" ? "만드는 중…" : state === "playing" ? "멈춤" : "미리듣기"}
        </Button>
      </div>
      <div role="status" className="mt-1.5 min-h-[18px] text-[12px]">
        {note ? <span className={note.ok ? "text-sub" : "text-bad-text"}>{note.msg}</span>
          : <span className="text-sub">{gemini ? "Gemini 목소리는 새 소리를 만들 때마다 요청 1회(하루 한도에 들어감) — 같은 목소리·문장은 다시 요청하지 않습니다." : "이 컴퓨터에서 만들어 돈이 들지 않습니다."}</span>}
      </div>
    </div>
  );
}

const typesText = (f: SfxFolder) => Object.entries(f.types).map(([t, n]) => `${t} ${n}`).join(" · ");
const RUNNING: PackSummary["state"][] = ["queued", "measuring", "listening", "building"];
const isRunning = (p: PackSummary) => RUNNING.includes(p.state);

/** 폴더 한 줄: 경로, 개수·종류, 없어진 폴더·이름을 몰라 뺀 파일. 분석한 꾸러미면 원본 폴더 + 결과 보기. onRemove 가 있으면 빼기 버튼. */
function SfxFolderRow({ f, onRemove, busy, meme }: { f: SfxFolder | MemeFolder; onRemove?: () => void; busy?: boolean; meme?: boolean }) {
  const noun = meme ? "짤" : "효과음";
  const unit = meme ? "장" : "개";
  const types = "types" in f ? typesText(f) : "";
  const skipped = "skipped" in f ? f.skipped : 0;
  const pack = f.pack;
  return (
    <div className="flex flex-wrap items-start gap-x-3 gap-y-2 border-b border-line py-2.5 last:border-b-0">
      <div className="min-w-0 flex-1 basis-[200px]">
        <div className="break-all font-mono text-[13px]">{f.label ?? f.path}</div>
        <div className="mt-0.5 text-[13px] text-sub">
          {!f.exists ? <span className="text-bad-text">폴더가 없습니다 — {meme ? "기본 짤로" : "기본 효과음으로"} 대신합니다</span>
            : f.error ? <span className="text-bad-text">{f.error}</span>
            : <>{pack && "분석한 꾸러미 · "}{noun} {f.count}{unit}{f.count > 0 && types && <> · {types}</>}</>}
        </div>
        {f.exists && skipped > 0 && (
          <div className="text-[12px] text-sub">{f.manifest ? `목록(manifest.json)에 있지만 파일이 없는 항목 ${skipped}개` : `이름으로 종류를 알 수 없어 뺀 파일 ${skipped}개`}</div>
        )}
      </div>
      {(pack || onRemove) && (
        <div className="flex shrink-0 items-center gap-1">
          {pack && <Button size="sm" variant="secondary" onClick={() => go(`/library/${pack.kind}/${encodeURIComponent(pack.id)}`)}>결과 보기</Button>}
          {onRemove && <Button size="sm" variant="ghost" disabled={busy} icon={<X size={14} {...ICON} />} onClick={onRemove} aria-label={`${f.label ?? f.path} 빼기`}>빼기</Button>}
        </div>
      )}
    </div>
  );
}

type Done = { tone: "ok" | "warn" | "bad"; text: string; pack?: PackSummary };

/** 폴더 분석 지켜보기: 설정을 열 때 이미 도는 분석이 있으면 이어서 보고(kind 가 같을 때만), 1.5초마다 다시 읽다가 끝나면 설정을 다시 읽어 넘긴다. */
function useAnalysis(kind: LibKind, onSaved: (v: Settings) => void) {
  const [pack, setPack] = useState<PackSummary | null>(null);
  const [done, setDone] = useState<Done | null>(null);
  const [stopping, setStopping] = useState(false);
  const watching = pack?.id ?? null;
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const finish = async (p: PackSummary) => {
    setPack(null);
    setStopping(false);
    const unit = kind === "meme" ? "장을" : "개를";
    const noun = kind === "meme" ? "짤" : "효과음";
    // 끝났어도 message 가 있으면(Gemini 가 도중에 막힘, 영상에 넣지 못함 등) 함께 알린다
    if (p.state === "done") setDone({ tone: p.message ? "warn" : "ok", text: `${noun} ${p.counts.kept}${unit} 골랐습니다${p.message ? ` — ${p.message}` : ""}`, pack: p });
    else if (p.state === "stopped") setDone({ tone: "warn", text: "분석을 멈췄습니다 — 멈춘 데까지 꾸러미에 남아 있습니다", pack: p });
    else setDone({ tone: "bad", text: p.message || "분석에 실패했습니다" });
    try { const s = await api.settings(); if (alive.current) onSaved(s); } catch { /* 다음에 설정을 열 때 보임 */ }
  };

  // 설정을 열 때 이미 돌고 있는 분석
  useEffect(() => {
    let dead = false;
    api.library().then((l) => {
      if (dead || !l.running || l.running.kind !== kind) return;
      const p = l.packs.find((x) => x.id === l.running!.id && x.kind === kind);
      if (p && isRunning(p)) setPack(p);
    }).catch(() => { /* 분석 목록을 못 읽어도 설정은 쓸 수 있음 */ });
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!watching) return;
    let dead = false;
    let timer: number | undefined;
    const loop = async () => {
      try {
        const l = await api.library();
        const p = l.packs.find((x) => x.id === watching && x.kind === kind);
        if (dead) return;
        if (p && isRunning(p)) setPack(p);
        else if (p) { await finish(p); return; }
        else { setPack(null); return; }
      } catch { /* 한 번 놓쳐도 다음에 다시 */ }
      if (!dead) timer = window.setTimeout(loop, 1500);
    };
    timer = window.setTimeout(loop, 1500);
    return () => { dead = true; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watching]);

  return {
    pack, done, stopping,
    start: (p: PackSummary) => { setDone(null); setPack(p); },
    clear: () => setDone(null),
    stop: async () => {
      if (!pack) return;
      setStopping(true);
      try { await api.libraryStop(kind, pack.id); } catch (e) { setDone({ tone: "bad", text: (e as Error).message }); setStopping(false); }
    },
  };
}

/** 분석 진행 막대 + 멈춤, 끝난 뒤 결과 한 줄. */
function AnalysisBlock({ kind, a }: { kind: LibKind; a: ReturnType<typeof useAnalysis> }) {
  const p = a.pack;
  const tone = { ok: "text-ok-text", warn: "text-warn-text", bad: "text-bad-text" } as const;
  return (
    <>
      {p && (
        <div role="status" className="mt-3 rounded-[10px] border border-line bg-fill p-3">
          <div className="flex items-center justify-between gap-3">
            <span className="min-w-0 text-[13px] font-medium">분석 중 — {p.progress.step} {p.progress.done}/{p.progress.total}</span>
            <Button size="sm" variant="secondary" disabled={a.stopping} icon={<Square size={14} {...ICON} />} onClick={a.stop}>{a.stopping ? "멈추는 중…" : "멈춤"}</Button>
          </div>
          <Progress className="mt-2" value={p.progress.done / Math.max(p.progress.total, 1)} />
          <div className="mt-1.5 break-all text-[12px] text-sub">{p.source_dir}</div>
        </div>
      )}
      {a.done && (
        <div role="status" className={cx("mt-3 text-[13px]", tone[a.done.tone])}>
          {a.done.text}
          {a.done.pack && a.done.pack.counts.kept > 0 && <> · <a href={`#/library/${kind}/${encodeURIComponent(a.done.pack.id)}`} className="font-medium underline">결과 보기</a></>}
        </div>
      )}
    </>
  );
}

/** '효과음' 묶음 — 폴더를 고르면 프로그램이 소리를 하나씩 듣고 종류별로 골라 따로 꾸러미를 만든다. 안 고르면 config 의 기본 효과음. */
function SfxCard({ sfx, onSaved }: { sfx: SfxSettings; onSaved: (v: Settings) => void }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "pick">("");
  const [msg, setMsg] = useState<{ ok: boolean; msg: string } | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const a = useAnalysis("sfx", onSaved);
  const analyzing = !!a.pack;
  const custom = sfx.using === "custom";
  const current = sfx.dirs.map((d) => d.path);
  const total = (fs: SfxFolder[]) => fs.reduce((n, f) => n + f.count, 0);
  const kinds = (fs: SfxFolder[]) => new Set(fs.flatMap((f) => Object.keys(f.types))).size;

  function show(ok: boolean, text: string) {
    setMsg({ ok, msg: text });
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(null), ok ? 2500 : 8000);
  }

  async function save(p: SettingsPatch["sfx"], done: string) {
    setBusy("save");
    try {
      onSaved(await api.saveSettings({ sfx: p }));
      show(true, done);
      return true;
    } catch (e) {
      show(false, (e as Error).message);
      return false;
    } finally {
      setBusy("");
    }
  }

  async function add(p: string) {
    const v = p.trim();
    if (!v) return;
    setBusy("save");
    setMsg(null);
    a.clear();
    try {
      const r = await api.libraryAdd("sfx", v);
      if ("settings" in r) { onSaved(r.settings); show(true, "목록(manifest.json)이 있는 폴더라 분석 없이 그대로 넣었습니다. 다음에 만드는 영상부터 씁니다."); }
      else a.start(r.analysis);
      setPath("");
    } catch (e) {
      show(false, (e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function pick() {
    setBusy("pick");
    setMsg(null);
    try {
      const r = await api.libraryPick("sfx");
      setBusy("");
      if (r.path) await add(r.path);
    } catch (e) {
      setBusy("");
      show(false, (e as Error).message);
    }
  }

  return (
    <Card className="mt-4 p-5 sm:p-6">
      <h2 className="text-[20px] font-semibold tracking-[-0.01em]">효과음</h2>
      <p className="mt-1 text-[13px] leading-relaxed text-sub">내 효과음 폴더를 고르면 프로그램이 소리를 하나씩 듣고, 종류별로 골라 따로 꾸러미를 만듭니다. 내 원래 폴더는 바뀌지 않습니다. AI 요청을 쓰고(소리 6개에 1번), AI 키가 없으면 파일 이름만 보고 정합니다. 폴더를 고르지 않으면 이 프로그램에 같이 들어 있는 기본 효과음을 씁니다.</p>
      <div className="mt-3">
        {custom
          ? <Status tone="ok">내 폴더 사용 중 — 효과음 {total(sfx.dirs)}개, {kinds(sfx.dirs)}종류</Status>
          : <Status tone="mute">기본 효과음 사용 중 — 효과음 {total(sfx.default)}개, {kinds(sfx.default)}종류</Status>}
      </div>

      {custom && (
        <div className="mt-3 rounded-[10px] border border-line px-3">
          {sfx.dirs.map((f) => (
            <SfxFolderRow key={f.path} f={f} busy={!!busy}
              onRemove={() => save({ dirs: current.filter((p) => p !== f.path) }, current.length > 1 ? "폴더를 뺐습니다" : "기본 효과음으로 되돌렸습니다")} />
          ))}
        </div>
      )}

      <AnalysisBlock kind="sfx" a={a} />

      {sfx.dirs.length < sfx.max_dirs && (
        <div className="mt-3">
          <label htmlFor="sfx-path" className="text-[13px] font-medium">{custom ? "폴더 더 넣기" : "내 효과음 폴더 쓰기"}</label>
          <div className="mt-1.5 flex flex-wrap gap-2">
            <input id="sfx-path" value={path} onChange={(e) => setPath(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !analyzing) add(path); }}
              placeholder="C:\Users\나\효과음" spellCheck={false}
              className="h-9 min-w-0 flex-1 basis-[200px] rounded-[10px] border border-line bg-surface px-3 font-mono text-[13px] text-ink outline-none placeholder:text-sub/70 focus:border-accent" />
            <Button size="sm" variant="secondary" disabled={!!busy || analyzing || !path.trim()} icon={<Plus size={14} {...ICON} />} onClick={() => add(path)}>넣기</Button>
            <Button size="sm" variant="secondary" disabled={!!busy || analyzing} icon={<FolderOpen size={14} {...ICON} />} onClick={pick}>{busy === "pick" ? "고르는 중…" : "폴더 고르기"}</Button>
          </div>
        </div>
      )}

      {custom && (
        <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-[10px] px-1 py-1 text-[15px] leading-snug has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60">
          <input type="checkbox" checked={sfx.fill_missing} disabled={!!busy}
            onChange={(e) => save({ fill_missing: e.target.checked }, "저장했습니다")} className="mt-[3px] h-4 w-4 shrink-0 accent-[var(--c-accent)]" />
          <span className="min-w-0">내 폴더에 없는 종류는 기본 효과음으로 채우기
            <span className="block text-[13px] text-sub">끄면 내 폴더에 없는 종류의 자리는 효과음 없이 지나갑니다.</span>
          </span>
        </label>
      )}

      <div role="status" className="mt-2 min-h-[20px] text-[13px]">
        {msg && <span className={msg.ok ? "text-ok-text" : "text-bad-text"}>{msg.msg}</span>}
      </div>

      <details className="mt-1 rounded-[10px] border border-line bg-fill p-3 text-[13px]">
        <summary className="cursor-pointer font-medium">폴더 만드는 법</summary>
        <p className="mt-2 leading-relaxed text-sub">파일 이름은 아무거나 괜찮습니다. 프로그램이 들어 보고 종류를 정합니다. 이름에 종류가 적혀 있으면(예: <code className="font-mono">pop_01.wav</code>, <code className="font-mono">boom_02.mp3</code>, 또는 종류 이름 폴더 안 <code className="font-mono">pop\아무이름.mp3</code>) 그 종류를 그대로 따릅니다. wav·mp3·ogg·flac·m4a 를 읽습니다. 폴더에 <code className="font-mono">manifest.json</code> 이 이미 있으면 분석 없이 그 목록대로 씁니다.</p>
        <p className="mt-2 text-[12px] font-medium text-sub">쓸 수 있는 종류</p>
        <div className="mt-1 flex flex-wrap gap-1">{sfx.known_types.map((t) => <Chip key={t}>{t}</Chip>)}</div>
        <p className="mt-3 text-[12px] font-medium text-sub">기본 효과음</p>
        <div className="mt-1 rounded-[10px] border border-line bg-surface px-3">
          {sfx.default.map((f) => <SfxFolderRow key={f.path} f={f} />)}
        </div>
        <p className="mt-2 leading-relaxed text-sub">음악·문장 대사·욕설·성적인 소리와 권리자가 분명한 소리는 알아서 빼지만, 저작권을 모두 점검하지는 못합니다(검수 화면 경고도 없습니다). 남이 만든 소리는 출처와 쓰는 조건을 직접 확인하세요.</p>
      </details>
    </Card>
  );
}

/** '짤' 묶음 — 폴더를 고르면 프로그램이 그림을 하나씩 보고 어떤 반응인지 적어 따로 꾸러미를 만든다. 안 고르면 같이 든 기본 짤. */
function MemeCard({ memes, onSaved }: { memes: MemeSettings; onSaved: (v: Settings) => void }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "pick">("");
  const [msg, setMsg] = useState<{ ok: boolean; msg: string } | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const a = useAnalysis("meme", onSaved);
  const analyzing = !!a.pack;
  const total = (fs: MemeFolder[]) => fs.reduce((n, f) => n + f.count, 0);

  function show(ok: boolean, text: string) {
    setMsg({ ok, msg: text });
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(null), ok ? 2500 : 8000);
  }

  async function remove(f: MemeFolder) {
    setBusy("save");
    try {
      onSaved(await api.saveSettings({ memes: { dirs: memes.dirs.map((d) => d.path).filter((p) => p !== f.path) } }));
      show(true, memes.dirs.length > 1 ? "폴더를 뺐습니다" : "기본 짤로 되돌렸습니다");
    } catch (e) {
      show(false, (e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function add(p: string) {
    const v = p.trim();
    if (!v) return;
    setBusy("save");
    setMsg(null);
    a.clear();
    try {
      const r = await api.libraryAdd("meme", v);
      if ("settings" in r) { onSaved(r.settings); show(true, "목록(manifest.json)이 있는 폴더라 분석 없이 그대로 넣었습니다. 다음에 만드는 영상부터 씁니다."); }
      else a.start(r.analysis);
      setPath("");
    } catch (e) {
      show(false, (e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function pick() {
    setBusy("pick");
    setMsg(null);
    try {
      const r = await api.libraryPick("meme");
      setBusy("");
      if (r.path) await add(r.path);
    } catch (e) {
      setBusy("");
      show(false, (e as Error).message);
    }
  }

  const custom = memes.using === "custom";
  return (
    <Card className="mt-4 p-5 sm:p-6">
      <h2 className="text-[20px] font-semibold tracking-[-0.01em]">짤</h2>
      <p className="mt-1 text-[13px] leading-relaxed text-sub">내 짤 폴더를 고르면 프로그램이 그림을 하나씩 보고 어떤 반응인지 적어 둡니다. 영상은 그 뜻을 보고 문장에 맞는 짤을 붙입니다. 혐오 상징·성적인 그림·잔인한 그림은 알아서 빼고, 실제 사람 사진은 표시만 합니다. 저작권은 따지지 않으니 직접 확인하세요. 내 원래 폴더는 바뀌지 않고, AI 요청을 씁니다(그림 6장에 1번).</p>
      <div className="mt-3">
        {memes.using === "custom" ? <Status tone="ok">내 짤 사용 중 — {total(memes.dirs)}장</Status>
          : memes.using === "default" ? <Status tone="mute">기본 짤 사용 중 — {total(memes.default)}장</Status>
          : <Status tone="warn">짤 없음 — 짤 폴더를 넣으면 영상에 반응 짤을 넣습니다</Status>}
      </div>

      {custom && (
        <div className="mt-3 rounded-[10px] border border-line px-3">
          {memes.dirs.map((f) => <SfxFolderRow key={f.path} f={f} meme busy={!!busy} onRemove={() => remove(f)} />)}
        </div>
      )}

      <AnalysisBlock kind="meme" a={a} />

      {memes.dirs.length < memes.max_dirs && (
        <div className="mt-3">
          <label htmlFor="meme-path" className="text-[13px] font-medium">{custom ? "폴더 더 넣기" : "내 짤 폴더 쓰기"}</label>
          <div className="mt-1.5 flex flex-wrap gap-2">
            <input id="meme-path" value={path} onChange={(e) => setPath(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !analyzing) add(path); }}
              placeholder="C:\Users\나\짤" spellCheck={false}
              className="h-9 min-w-0 flex-1 basis-[200px] rounded-[10px] border border-line bg-surface px-3 font-mono text-[13px] text-ink outline-none placeholder:text-sub/70 focus:border-accent" />
            <Button size="sm" variant="secondary" disabled={!!busy || analyzing || !path.trim()} icon={<Plus size={14} {...ICON} />} onClick={() => add(path)}>넣기</Button>
            <Button size="sm" variant="secondary" disabled={!!busy || analyzing} icon={<FolderOpen size={14} {...ICON} />} onClick={pick}>{busy === "pick" ? "고르는 중…" : "폴더 고르기"}</Button>
          </div>
        </div>
      )}

      <div role="status" className="mt-2 min-h-[20px] text-[13px]">
        {msg && <span className={msg.ok ? "text-ok-text" : "text-bad-text"}>{msg.msg}</span>}
      </div>

      <details className="mt-1 rounded-[10px] border border-line bg-fill p-3 text-[13px]">
        <summary className="cursor-pointer font-medium">폴더 만드는 법</summary>
        <p className="mt-2 leading-relaxed text-sub">png·jpg·webp·gif 그림이 든 폴더면 됩니다. 파일 이름은 아무거나 괜찮고, 하위 폴더도 읽습니다. 반응 짤이 아닌 그림, 너무 작은 그림, 똑같은 그림은 알아서 뺍니다. AI 키가 없으면 파일 이름을 짤의 뜻으로 씁니다. 폴더에 <code className="font-mono">manifest.json</code> 이 이미 있으면 분석 없이 그 목록대로 씁니다.</p>
        <p className="mt-3 text-[12px] font-medium text-sub">기본 짤</p>
        <div className="mt-1 rounded-[10px] border border-line bg-surface px-3">
          {memes.default.length ? memes.default.map((f) => <SfxFolderRow key={f.path} f={f} meme />) : <p className="py-2.5 text-sub">같이 든 기본 짤이 없습니다.</p>}
        </div>
      </details>
    </Card>
  );
}

interface GroupState { saving?: boolean; testing?: boolean; error?: string | null; test?: { ok: boolean; message: string } | null }

export default function SettingsPage() {
  const { data, error, setData } = useLoad(() => api.settings(), []);
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [gstate, setGstate] = useState<Record<string, GroupState>>({});
  const [showReady, setShowReady] = useState(false);
  const [engineBusy, setEngineBusy] = useState(false);
  const [engineMsg, setEngineMsg] = useState<{ ok: boolean; msg: string } | null>(null);
  const flashTimer = useRef<number | null>(null);
  const { done, copy } = useCopy();
  const [modelStarting, setModelStarting] = useState<string | null>(null);
  const [modelErr, setModelErr] = useState<string | null>(null);
  const [cat, setCat] = useState<GeminiModels | null>(null);
  const [catBusy, setCatBusy] = useState(false);
  const [useBusy, setUseBusy] = useState(false);
  const [useMsg, setUseMsg] = useState<{ ok: boolean; msg: string } | null>(null);
  const useTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    if (useTimer.current) window.clearTimeout(useTimer.current);
  }, []);

  async function loadCatalog(refresh = false) {
    setCatBusy(true);
    try {
      setCat(await api.geminiModels(refresh));
    } catch (e) {
      setCat({ source: "builtin", note: `모델 목록을 불러오지 못했습니다. ${(e as Error).message}`, roles: {} });
    } finally {
      setCatBusy(false);
    }
  }
  // 처음 한 번, 그리고 Gemini 키를 넣거나 지우면 다시(이 키로 쓸 수 있는 모델 목록이 바뀐다). OpenRouter 키만 바뀌면 다시 받지 않는다.
  const geminiKey = data?.keys.find((k) => k.name === "gemini_api_key");
  const geminiKeyState = geminiKey ? `${geminiKey.set}/${geminiKey.source ?? ""}` : null;
  useEffect(() => {
    if (geminiKeyState !== null) loadCatalog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geminiKeyState]);

  /** '쓸 모델' 저장 — 고르면 바로. */
  async function saveUse(p: SettingsPatch) {
    if (useBusy) return;
    setUseBusy(true);
    let msg = { ok: true, msg: "저장했습니다" };
    try {
      setData(await api.saveSettings(p));
    } catch (e) {
      msg = { ok: false, msg: (e as Error).message };
    } finally {
      setUseBusy(false);
    }
    setUseMsg(msg);
    if (useTimer.current) window.clearTimeout(useTimer.current);
    useTimer.current = window.setTimeout(() => setUseMsg(null), msg.ok ? 2000 : 6000);
  }

  const applyModels = (v: ModelsView) => setData((d) => (d ? { ...d, models: v.models, setup_cmd: v.setup_cmd } : d));

  // 받는 중인 모델이 있으면 1초마다 진행 상황을 다시 읽는다. 다 끝나면 설정 전체를 다시 읽어 준비 상태(ready·ready_reason)와 TopBar 상태 점도 맞춘다.
  const modelsBusy = !!data?.models.some(modelBusy);
  useEffect(() => {
    if (!modelsBusy) return;
    const t = window.setInterval(async () => {
      try {
        const v = await api.models();
        applyModels(v);
        if (!v.models.some(modelBusy)) apply(await api.settings());     // 설치가 끝났다 — 로컬 목소리 준비 여부가 바뀔 수 있다
      } catch { /* 다음 주기에 다시 */ }
    }, 1000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelsBusy]);

  async function installModel(id: string) {
    setModelStarting(id);
    setModelErr(null);
    try {
      applyModels(await api.installModel(id));
    } catch (e) {
      setModelErr((e as Error).message);
    } finally {
      setModelStarting(null);
    }
  }

  const patch = (id: string, p: GroupState) => setGstate((s) => ({ ...s, [id]: { ...s[id], ...p } }));

  /** 저장 결과를 화면과 TopBar 상태 점에 함께 반영한다(30초 주기를 기다리지 않게). */
  function apply(v: Settings) {
    setData(v);
    window.dispatchEvent(new Event("recap:health"));
  }

  async function testGroup(id: string) {
    patch(id, { testing: true, error: null });
    try {
      const r = await api.testSettings(id);
      patch(id, { testing: false, test: r });
      window.dispatchEvent(new Event("recap:health"));     // 틀린 키면 상태 점·첫 화면 안내가 바로 바뀌게(서버가 확인 결과를 기억)
    } catch (e) {
      patch(id, { testing: false, error: (e as Error).message });
    }
  }

  async function saveGroup(g: SettingsGroup, keys: SettingsKey[]) {
    const body: Record<string, string> = {};
    for (const k of keys) { const v = typed[k.name] ?? ""; if (v.trim() !== "") body[k.name] = v; }
    if (Object.keys(body).length === 0) return;
    const wasReady = data?.ready ?? false;
    patch(g.id, { saving: true, error: null, test: null });
    try {
      const v = await api.saveSettings({ keys: body });
      apply(v);
      setTyped((t) => { const n = { ...t }; for (const name of Object.keys(body)) delete n[name]; return n; });
      patch(g.id, { saving: false });
      if (!wasReady && v.ready) setShowReady(true);
      await testGroup(g.id);      // 저장하면 연결을 한 번 확인한다
    } catch (e) {
      patch(g.id, { saving: false, error: (e as Error).message });
    }
  }

  async function clearKey(g: SettingsGroup, k: SettingsKey) {
    if (!confirm(`${k.label}를 설정 파일에서 지울까요?`)) return;
    patch(g.id, { error: null });
    try {
      const v = await api.saveSettings({ keys: { [k.name]: "" } });
      apply(v);
      patch(g.id, { test: null });
      if (!v.ready) setShowReady(false);
    } catch (e) {
      patch(g.id, { error: (e as Error).message });
    }
  }

  function flash(ok: boolean, msg: string) {
    setEngineMsg({ ok, msg });
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setEngineMsg(null), 2000);
  }

  async function pickEngine(id: string) {
    if (!data || engineBusy || id === data.image_engine) return;
    setEngineBusy(true);
    try {
      const v = await api.saveSettings({ image_engine: id });
      setData(v);
      flash(true, "저장했습니다");
    } catch (e) {
      flash(false, (e as Error).message);
    } finally {
      setEngineBusy(false);
    }
  }

  if (error && !data) return <Card className="max-w-[760px] p-6 text-[15px] text-bad-text">설정을 불러오지 못했습니다. {error}</Card>;
  if (!data) return <div className="max-w-[760px] pt-4 text-[13px] text-sub">불러오는 중</div>;

  const missing = data.models.filter((m) => !m.ok);
  const roleOf = (id: string) => data.model_roles.find((r) => r.role === id);
  const supertonicOk = !!data.models.find((m) => m.id === "supertonic")?.ok;
  const voice = data.voice;
  const [rScript, rTts, rImage] = [roleOf("script"), roleOf("tts"), roleOf("image")];

  return (
    <div className="max-w-[760px] pt-4">
      <a href="#/" className="mb-3 inline-flex items-center gap-1 text-[13px] text-sub no-underline hover:text-ink"><ChevronLeft size={16} {...ICON} />처음으로</a>
      <h1 className="text-[34px] font-semibold leading-[1.2] tracking-[-0.02em]">설정</h1>
      <p className="mt-2 text-[15px] text-sub">키는 이 컴퓨터의 <code className="break-all font-mono text-[13px]">{data.file}</code> 에만 저장됩니다. 화면에는 끝 네 글자만 보입니다.</p>

      {showReady && (gstate.gemini?.test?.ok || gstate.openrouter?.test?.ok) && (      /* 연결 확인까지 통과했을 때만 — 틀린 키로 "만들 수 있습니다"라고 하지 않게 */
        <Card role="status" className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
          <Status tone="ok">AI 키를 저장했습니다. 이제 영상을 만들 수 있습니다.</Status>
          <Button variant="primary" size="sm" className="ml-auto" onClick={() => go("/")}>영상 만들러 가기</Button>
        </Card>
      )}

      {data.groups.map((g) => {
        const keys = data.keys.filter((k) => k.group === g.id);
        const gs = gstate[g.id] ?? {};
        const req = keys.some((k) => k.required);
        const dirty = keys.some((k) => (typed[k.name] ?? "").trim() !== "");
        return (
          <Card key={g.id} className="mt-4 p-5 sm:p-6">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-[20px] font-semibold tracking-[-0.01em]">{g.title}</h2>
              <Chip tone={req || g.need ? "accent" : "outline"}>{g.need ?? (req ? "필수" : "선택")}</Chip>
            </div>
            <p className="mt-1 text-[13px] leading-relaxed text-sub">{g.desc}</p>
            <a href={g.url} target="_blank" rel="noopener noreferrer" aria-label={`${g.title} 키 받는 곳 (새 창)`}
              className="mt-2 inline-flex items-center gap-1 text-[13px] font-medium text-accent no-underline hover:underline">
              키 받는 곳<ExternalLink size={13} {...ICON} />
            </a>

            <div className="mt-4 space-y-4">
              {keys.map((k) => (
                <KeyField key={k.name} k={k} value={typed[k.name] ?? ""} busy={!!gs.saving}
                  onChange={(v) => setTyped((t) => ({ ...t, [k.name]: v }))}
                  onClear={() => clearKey(g, k)} />
              ))}
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
              <Button variant="primary" size="sm" disabled={!dirty || !!gs.saving} onClick={() => saveGroup(g, keys)}>{gs.saving ? "저장하는 중" : "저장"}</Button>
              <Button variant="secondary" size="sm" disabled={!!gs.testing || !!gs.saving} onClick={() => testGroup(g.id)}>연결 확인</Button>
              <span className="text-[13px] text-sub">저장하면 연결을 한 번 확인합니다.</span>
            </div>
            <div role="status" className="mt-2 min-h-[20px] text-[13px]">
              {gs.error ? <span className="text-bad-text">{gs.error}</span>
                : gs.testing ? <Status tone="mute" pulse>확인 중…</Status>
                : gs.test ? <Status tone={gs.test.ok ? "ok" : "bad"}>{gs.test.message}</Status>
                : null}
            </div>
          </Card>
        );
      })}

      <Card className="mt-4 p-5 sm:p-6">
        <h2 className="text-[20px] font-semibold tracking-[-0.01em]">쓸 모델</h2>
        <p className="mt-1 text-[13px] leading-relaxed text-sub">대본·목소리·이미지에 어떤 모델을 쓸지 고릅니다. 고르면 바로 저장되고, 다음에 만드는 영상부터 씁니다.</p>
        <div className="mt-4 space-y-5">
          {rScript && (
            <div>
              <label htmlFor="model-script" className="text-[13px] font-medium">대본</label>
              <p className="mb-1.5 text-[13px] text-sub">대본 초안을 쓰고 다듬는 모델(Gemini 또는 OpenRouter)</p>
              <ModelSelect r={rScript} choices={cat?.roles.script} busy={useBusy} onPick={(id) => saveUse({ models: { script: id } })} />
            </div>
          )}
          <div>
            <span className="text-[13px] font-medium">목소리</span>
            <fieldset className="mt-1" aria-label="목소리 엔진">
              <legend className="sr-only">목소리 엔진</legend>
              <div className="space-y-0.5">
                {voice.engines.map((e) => {
                  const off = e.id === "supertonic" && !supertonicOk;
                  return (
                    <label key={e.id} className="flex cursor-pointer items-start gap-3 rounded-[10px] px-2 py-2 text-[15px] leading-snug hover:bg-fill has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60">
                      <input type="radio" name="voice_engine" value={e.id} checked={voice.engine === e.id} disabled={useBusy || off}
                        onChange={() => saveUse({ voice: { engine: e.id } })} className="mt-[3px] h-4 w-4 shrink-0 accent-[var(--c-accent)]" />
                      <span className="min-w-0">
                        {e.label}
                        {off && <span className="block text-[13px] text-sub">아래 모델 파일에서 로컬 목소리를 받으면 고를 수 있습니다</span>}
                      </span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <div className="mt-1.5">
              {voice.engine === "supertonic" ? (
                <>
                  <label htmlFor="voice-sid" className="sr-only">로컬 목소리 번호</label>
                  <select id="voice-sid" value={voice.sid} disabled={useBusy || voice.sid_env} onChange={(e) => saveUse({ voice: { sid: Number(e.target.value) } })} className={SELECT}>
                    {voice.sids.map((n) => <option key={n} value={n}>목소리 {n}번</option>)}
                  </select>
                  {voice.sid_env && <p className="mt-1 text-[13px] text-sub">환경변수 RECAP_TTS_SID 로 정해져 있어 여기서 바꿀 수 없습니다.</p>}
                </>
              ) : rTts && (
                <div className="grid gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
                  <div>
                    <label htmlFor="voice-name" className="text-[11px] font-medium text-sub">목소리</label>
                    <select id="voice-name" value={voice.gemini_voice} disabled={useBusy} onChange={(e) => saveUse({ voice: { gemini_voice: e.target.value } })} className={cx(SELECT, "mt-1")}>
                      {voice.gemini_voices.map((g) => <option key={g.id} value={g.id}>{g.label}{g.id === "Charon" ? " · 기본" : ""}</option>)}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="model-tts" className="text-[11px] font-medium text-sub">모델</label>
                    <div className="mt-1"><ModelSelect r={rTts} choices={cat?.roles.tts} busy={useBusy} onPick={(id) => saveUse({ models: { tts: id } })} /></div>
                  </div>
                </div>
              )}
            </div>
            <VoicePreview gemini={voice.engine !== "supertonic"}
              body={voice.engine === "supertonic" ? { engine: "supertonic", sid: voice.sid }
                : { engine: "gemini", voice: voice.gemini_voice, model: rTts?.value }} />
          </div>
          {rImage && (
            <div>
              <label htmlFor="model-image" className="text-[13px] font-medium">이미지</label>
              <p className="mb-1.5 text-[13px] text-sub">AI 그림 만드는 곳이 Gemini 일 때, 또는 로컬 이미지 서버가 한도에 걸려 대신 만들 때 씁니다</p>
              <ModelSelect r={rImage} choices={cat?.roles.image} busy={useBusy} onPick={(id) => saveUse({ models: { image: id } })} />
            </div>
          )}
        </div>
        <div className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-line pt-4">
          <Status tone={cat?.source === "api" ? "ok" : "mute"} pulse={catBusy}>{!cat ? "모델 목록을 불러오는 중" : cat.note}</Status>
          <Button size="sm" variant="secondary" className="ml-auto" icon={<RefreshCw size={14} {...ICON} />} disabled={catBusy} onClick={() => loadCatalog(true)}>목록 새로 받기</Button>
        </div>
        <div role="status" className="mt-2 min-h-[20px] text-[13px]">
          {useMsg && <span className={useMsg.ok ? "text-ok-text" : "text-bad-text"}>{useMsg.msg}</span>}
        </div>
      </Card>

      <Card className="mt-4 p-5 sm:p-6">
        <h2 className="text-[20px] font-semibold tracking-[-0.01em]">AI 그림 만드는 곳</h2>
        <p className="mt-1 text-[13px] leading-relaxed text-sub">영상에 넣을 AI 그림을 어디서 만들지 고릅니다. 고르면 바로 저장됩니다.</p>
        <fieldset className="mt-3" aria-label="AI 그림 만드는 곳">
          <legend className="sr-only">AI 그림 만드는 곳</legend>
          <div className="space-y-0.5">
            {data.image_engines.map((e) => (
              <label key={e.id} className="flex cursor-pointer items-start gap-3 rounded-[10px] px-2 py-2 text-[15px] leading-snug hover:bg-fill has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60">
                <input type="radio" name="image_engine" value={e.id} checked={data.image_engine === e.id} disabled={engineBusy}
                  onChange={() => pickEngine(e.id)} className="mt-[3px] h-4 w-4 shrink-0 accent-[var(--c-accent)]" />
                <span className="min-w-0">{e.label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
          <Status tone={data.image_server.up ? "ok" : "mute"}>로컬 이미지 서버({data.image_server.url}) {data.image_server.up ? "켜짐" : "꺼짐"}</Status>
          {engineMsg && <span role="status" className={cx("text-[13px]", engineMsg.ok ? "text-ok-text" : "text-bad-text")}>{engineMsg.msg}</span>}
        </div>
      </Card>

      <SfxCard sfx={data.sfx} onSaved={setData} />
      <MemeCard memes={data.memes} onSaved={setData} />

      <Card className="mt-4 p-5 sm:p-6">
        <h2 className="text-[20px] font-semibold tracking-[-0.01em]">모델 파일</h2>
        <p className="mt-1 text-[13px] leading-relaxed text-sub">받아쓰기(발음 확인)와 로컬 목소리에 쓰는 파일입니다. 없어도 영상은 만들어지고, 그 기능만 건너뜁니다.</p>
        <div className="mt-2">
          {data.models.map((m) => (
            <ModelRow key={m.id} m={m} starting={modelStarting === m.id} onInstall={() => installModel(m.id)} />
          ))}
        </div>
        {modelErr && <p role="status" className="mt-2 text-[13px] text-bad-text">{modelErr}</p>}
        {missing.length > 0 && (
          <div className="mt-3 rounded-[10px] border border-line bg-fill p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[11px] font-medium text-sub">버튼 대신 명령으로 받기</span>
              <Button size="sm" variant="secondary" icon={done ? <Check size={14} {...ICON} /> : <Copy size={14} {...ICON} />} onClick={() => copy(data.setup_cmd)}>{done ? "복사됨" : "복사"}</Button>
            </div>
            <pre className="scroll-thin mt-2 select-all overflow-x-auto whitespace-pre-wrap break-all font-mono text-[13px] leading-relaxed">{data.setup_cmd}</pre>
            <p className="mt-2 text-[13px] text-sub">명령은 이 프로그램 폴더에서 PowerShell 로 실행합니다.</p>
          </div>
        )}
      </Card>
    </div>
  );
}
