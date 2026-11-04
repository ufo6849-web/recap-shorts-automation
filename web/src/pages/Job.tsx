import { Check, Copy, Pause, Play } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fetchText, mediaUrl } from "../api";
import FactCheckCard from "../components/FactCheckCard";
import JobHeader from "../components/JobHeader";
import Waveform, { loadBuffer } from "../components/Waveform";
import { go, useCopy, useLoad } from "../hooks";
import { isArchived, scriptLines, scriptSyllables } from "../lib";
import { Button, Card, ICON, Label, Progress, SectionLabel, StepMark, cx, fmtClock } from "../ui";
import type { JobState, Script, Step, StepKey } from "../types";

const STATUS_TEXT: Record<Step["status"], string> = { todo: "대기", running: "하는 중", waiting: "확인 기다림", done: "완료", error: "오류" };
const pct = (v: number) => Math.round(v * 100);

function defaultStep(job: JobState): StepKey {
  return (job.steps.find((s) => s.status === "running") ?? job.steps.find((s) => s.status === "waiting") ?? job.steps.find((s) => s.status === "error") ?? [...job.steps].reverse().find((s) => s.status === "done") ?? job.steps[0]).key;
}

function Rail({ job, sel, onSel }: { job: JobState; sel: StepKey; onSel: (k: StepKey) => void }) {
  return (
    <ol className="grid grid-cols-4 gap-2 lg:grid-cols-8">
      {job.steps.map((s, i) => {
        const on = s.key === sel;
        return (
          <li key={s.key}>
            <button onClick={() => onSel(s.key)} aria-current={on ? "step" : undefined}
              className={cx("press flex h-full w-full flex-col rounded-[14px] border bg-surface p-3 text-left shadow-[0_1px_2px_rgba(0,0,0,0.04)]", on ? "border-accent ring-1 ring-accent" : "border-line hover:border-line-strong")}>
              <span className="flex items-center justify-between">
                <span className="text-[11px] font-medium text-sub">{i + 1}단계</span>
                <StepMark status={s.status} size={16} />
              </span>
              <span className="mt-2 text-[15px] font-semibold leading-tight">{s.label}</span>
              <span className={cx("mt-0.5 text-[13px]", s.status === "waiting" ? "text-warn-text" : s.status === "error" ? "text-bad-text" : s.status === "running" ? "text-accent" : "text-sub")}>
                {s.status === "running" ? `${pct(s.progress)}%` : STATUS_TEXT[s.status]}
              </span>
              <Progress value={s.progress} tone={s.status === "done" ? "ok" : "accent"} className="mt-3" />
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function NowCard({ job, step, onNext, onReload }: { job: JobState; step: Step; onNext: () => Promise<void>; onReload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const base = `/job/${encodeURIComponent(job.id)}`;
  const shown = step.status === "done" || step.status === "waiting" ? 1 : step.progress;
  const [err, setErr] = useState<string | null>(null);
  const act = async () => {
    setBusy(true); setErr(null);
    try { await onNext(); }
    catch (e) { setErr((e as Error).message); }
    finally {
      try { await onReload(); } catch (e) { setErr((previous) => previous ?? (e as Error).message); }
      setBusy(false);
    }
  };
  const locked = isArchived(job);
  // 읽기 단계: 사실 확인이 막았으면 넘기지 못하고, 확인이 필요하면 단추 이름이 "확인하고 계속"이 된다(11.7).
  const fc = step.key === "read" ? job.factcheck : null;
  const held = fc?.gate === "blocked" || !!fc?.running;
  const gateMsg = fc && fc.gate !== "clear" ? fc.gate_reason : null;
  const title = step.status === "todo" ? `${step.label} — 차례를 기다리는 중` : step.label;
  return (
    <Card className="rise p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Label>지금 하는 일</Label>
          <h2 className="mt-1 text-[20px] font-semibold tracking-[-0.01em]">{title}</h2>
          <p className="mt-1 text-[15px] text-sub">{step.error ?? step.detail ?? (step.status === "done" ? "끝났습니다." : step.status === "todo" ? "앞 단계가 끝나면 시작합니다." : "")}</p>
        </div>
        <div className="shrink-0 text-right">
          <span className={cx("text-[56px] font-semibold leading-none tracking-[-0.03em]", step.status === "todo" && "text-sub")}>{step.status === "todo" ? "–" : pct(shown)}</span>
          <span className="ml-0.5 text-[20px] font-medium text-sub">%</span>
        </div>
      </div>
      <Progress value={shown} className="mt-5 h-1.5" />
      {!!step.sub?.length && (
        <ul className="mt-5 space-y-2.5">
          {step.sub.map((s) => (
            <li key={s.label} className="flex items-center gap-3 text-[15px]">
              <StepMark status={s.status} size={16} />
              <span className={cx(s.status === "todo" && "text-sub")}>{s.label}</span>
              {s.note && <span className="ml-auto text-[13px] text-sub">{s.note}</span>}
            </li>
          ))}
        </ul>
      )}
      {err && <p role="alert" className="mt-4 text-[13px] text-bad-text">{err}</p>}
      {step.status === "waiting" && (
        <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-line pt-5">
          <span className="mr-auto text-[15px] font-medium">{gateMsg ?? ({ read: "읽은 자료를 확인해 주세요.", script: "대본을 읽고 고칠 곳이 있는지 봐 주세요.", voice: "목소리를 들어 보고 어색한 문장은 다시 녹음하세요.", assets: "그림을 보고 바꿀 것이 있으면 바꿔 주세요.", scenes: "장면 편집기에서 그림 놓은 순서를 확인해 주세요.", review: "최종 영상을 보고 승인해 주세요." } as Record<string, string>)[step.key] ?? "확인하고 계속하세요."}</span>
          {step.key === "script" && <Button variant="secondary" disabled={locked} onClick={() => go(`${base}/script`)}>대본 편집</Button>}
          {(step.key === "scenes" || step.key === "voice" || step.key === "assets") && <Button variant="secondary" disabled={locked} onClick={() => go(`${base}/scenes`)}>{step.key === "assets" ? "이미지 바꾸기" : "장면 편집기 열기"}</Button>}
          {step.key === "review" ? <Button variant="primary" onClick={() => go(`${base}/review`)}>검수 화면으로</Button>
            : <Button variant="primary" disabled={busy || locked || held} onClick={act}>{fc?.gate === "need_ack" ? "확인하고 계속" : "이대로 계속"}</Button>}
        </div>
      )}
      {step.status === "error" && (
        <div className="mt-6 flex items-center gap-2 border-t border-line pt-5">
          <span className="mr-auto text-[15px] text-bad-text">{step.error ?? "문제가 생겨 멈췄습니다."}</span>
          <Button variant="primary" disabled={busy || locked} onClick={act}>다시 시도</Button>
        </div>
      )}
    </Card>
  );
}

function ScriptCard({ job, script }: { job: JobState; script: Script }) {
  const t = script.title_lines;
  return (
    <Card className="rise p-5">
      <Label>대본</Label>
      <div className="mt-3 rounded-[10px] bg-black px-4 py-3 text-center text-[17px] font-extrabold leading-snug">
        <div className="text-white">{t[0]}</div><div className="text-[#FFD400]">{t[1]}</div>
      </div>
      <p className="mt-3 text-[15px] font-medium">“{script.hook}”</p>
      <p className="mt-1 text-[13px] text-sub">문장 {scriptLines(script)}개 · 음절 {scriptSyllables(script)}</p>
      <Button size="sm" variant="secondary" className="mt-4" disabled={isArchived(job)} onClick={() => go(`/job/${encodeURIComponent(job.id)}/script`)}>대본 편집</Button>
    </Card>
  );
}

function VoiceCard({ job }: { job: JobState }) {
  const url = mediaUrl(job.id, job.artifacts.voice!);
  const [dur, setDur] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [frac, setFrac] = useState(0);
  const a = useRef<HTMLAudioElement>(null);
  useEffect(() => { loadBuffer(url).then((b) => setDur(b.duration)).catch(() => setDur(null)); }, [url]);
  const toggle = () => { const e = a.current; if (!e) return; if (e.paused) e.play(); else e.pause(); };
  return (
    <Card className="rise p-5">
      <Label>목소리</Label>
      <div className="mt-3 flex items-center gap-3">
        <button onClick={toggle} aria-label={playing ? "멈춤" : "듣기"} className="press flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent text-white">
          {playing ? <Pause size={16} fill="currentColor" strokeWidth={0} /> : <Play size={16} fill="currentColor" strokeWidth={0} className="ml-0.5" />}
        </button>
        <div className="min-w-0 flex-1"><Waveform url={url} bars={56} height={40} progress={frac} onSeek={(f) => { const e = a.current; if (e && dur) { e.currentTime = f * dur; setFrac(f); } }} /></div>
      </div>
      <p className="mt-3 text-[13px] text-sub">{dur ? `길이 ${dur.toFixed(1)}초` : "길이 재는 중"} · Charon</p>
      <audio ref={a} src={url} preload="metadata" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => { setPlaying(false); setFrac(0); }}
        onTimeUpdate={(e) => { const t = e.currentTarget; if (t.duration) setFrac(t.currentTime / t.duration); }} />
    </Card>
  );
}

function ImagesCard({ job }: { job: JobState }) {
  const imgs = job.artifacts.images ?? [];
  const assets = useLoad(() => api.assets(job.id), [job.id, imgs.length], null);
  const by = new Map((assets.data ?? []).map((e) => [e.file, e]));
  const need = imgs.filter((f) => by.get(f)?.needs_review).length;
  return (
    <Card className="rise p-5 sm:col-span-2">
      <div className="flex items-center justify-between"><Label>자료 화면</Label><span className="text-[13px] text-sub">{imgs.length}장</span></div>
      <div className="mt-3 grid grid-cols-4 gap-2 sm:grid-cols-6">
        {imgs.slice(0, 12).map((f) => {
          const e = by.get(f);
          return (
            <div key={f} className="relative aspect-[4/3] overflow-hidden rounded-lg bg-fill">
              <img src={mediaUrl(job.id, f)} alt="" loading="lazy" className="h-full w-full object-cover" />
              {e?.needs_review && <span className="absolute bottom-1 left-1 rounded bg-[#b36b00] px-1.5 text-[11px] font-medium leading-5 text-white">가림 확인 필요</span>}
              {e && e.kind === "cap" && !e.needs_review && e.review_reason?.startsWith("가림") && <span className="absolute bottom-1 left-1 rounded bg-black/65 px-1.5 text-[11px] font-medium leading-5 text-white">가림 확인됨</span>}
            </div>
          );
        })}
      </div>
      {need > 0 && (
        <div className="mt-3 flex items-center justify-between gap-3 text-[13px]">
          <span className="text-warn-text">글 캡처 {need}장은 얼굴·아이디가 보이는지 사람이 확인해야 합니다.</span>
          <Button size="sm" variant="secondary" disabled={isArchived(job)} onClick={() => go(`/job/${encodeURIComponent(job.id)}/scenes`)}>장면 편집기에서 가리기</Button>
        </div>
      )}
    </Card>
  );
}

function TopicCard({ job }: { job: JobState }) {
  const { data } = useLoad(() => api.topic(job.id), [job.id]);
  const [topic, setTopic] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return null;
  const t = topic ?? data.topic, x = text ?? data.source_text;
  const dirty = t !== data.topic || x !== data.source_text;
  const save = async () => { try { await api.saveTopic(job.id, { topic: t, source_text: x }); setMsg("저장했습니다. 대본부터 다시 만듭니다."); } catch (e) { setMsg((e as Error).message); } };
  return (
    <Card className="rise p-5">
      <Label>읽은 자료</Label>
      <p className="mt-1 text-[13px] text-sub">글과 첨부 그림에서 읽어 낸 내용입니다. 틀린 곳이 있으면 고친 뒤 계속하세요.</p>
      <input value={t} onChange={(e) => setTopic(e.target.value)} aria-label="주제" className="mt-3 h-10 w-full rounded-[10px] border border-line bg-fill px-3 text-[15px] font-medium outline-none focus:border-accent" />
      <textarea value={x} onChange={(e) => setText(e.target.value)} rows={8} aria-label="읽은 글"
        className="scroll-thin mt-2 w-full resize-y rounded-[10px] border border-line bg-fill p-3 text-[13px] leading-relaxed outline-none focus:border-accent" />
      <div className="mt-3 flex items-center justify-between gap-3"><span className="text-[13px] text-sub">{msg}</span><Button size="sm" variant="secondary" disabled={!dirty || isArchived(job)} onClick={save}>저장</Button></div>
    </Card>
  );
}

function VideoCard({ job }: { job: JobState }) {
  const base = `/job/${encodeURIComponent(job.id)}`;
  return (
    <Card className="rise flex flex-col gap-5 p-5 sm:col-span-2 sm:flex-row">
      <video src={mediaUrl(job.id, job.artifacts.video!)} controls preload="metadata" className="aspect-[9/16] w-[180px] shrink-0 rounded-[10px] bg-black object-cover" />
      <div className="flex min-w-0 flex-col">
        <Label>영상</Label>
        <p className="mt-2 text-[15px] font-medium">영상이 만들어졌습니다.</p>
        <p className="mt-1 text-[13px] text-sub">1080 × 1920 · 30fps. 장면 편집기에서 고치고 다시 만들거나, 검수 화면에서 점검 결과를 보고 승인하세요.</p>
        <div className="mt-auto flex flex-wrap gap-2 pt-4">
          <Button size="sm" variant="secondary" disabled={isArchived(job)} onClick={() => go(`${base}/scenes`)}>장면 편집기</Button>
          <Button size="sm" variant="primary" onClick={() => go(`${base}/review`)}>검수</Button>
        </div>
      </div>
    </Card>
  );
}

function LogCard({ log }: { log: JobState["log"] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { const e = ref.current; if (e) e.scrollTop = e.scrollHeight; }, [log.length]);
  return (
    <Card className="p-5">
      <Label>제작 기록</Label>
      <div ref={ref} className="scroll-thin mt-3 max-h-[420px] space-y-2 overflow-y-auto pr-1">
        {log.length === 0 && <p className="text-[13px] text-sub">아직 기록이 없습니다.</p>}
        {log.map((l, i) => (
          <div key={i} className="grid grid-cols-[56px_1fr] gap-2 text-[13px] leading-snug"><span className="text-sub">{fmtClock(l.t)}</span><span>{l.msg}</span></div>
        ))}
      </div>
    </Card>
  );
}

function DescriptionCard({ job }: { job: JobState }) {
  const path = job.artifacts.description;
  const { data } = useLoad(() => (path ? fetchText(job.id, path) : Promise.resolve(null)), [job.id, path]);
  const { done, copy } = useCopy();
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between"><Label>설명 문구</Label>
        {data && <Button size="sm" variant="secondary" icon={done ? <Check size={14} {...ICON} /> : <Copy size={14} {...ICON} />} onClick={() => copy(data)}>{done ? "복사됨" : "복사"}</Button>}
      </div>
      {data ? <pre className="scroll-thin mt-3 max-h-[260px] overflow-y-auto whitespace-pre-wrap rounded-[10px] bg-fill p-3 font-[inherit] text-[13px] leading-relaxed">{data}</pre>
        : <p className="mt-3 text-[13px] text-sub">검수 단계가 끝나면 영상 설명란에 붙일 문구가 여기에 나옵니다. 출처는 직접 적으시면 됩니다.</p>}
    </Card>
  );
}

export default function Job({ id }: { id: string }) {
  const [sel, setSel] = useState<StepKey | null>(null);
  const [every, setEvery] = useState(1500);
  const { data: job, error, reload } = useLoad(() => api.job(id), [id], every);
  useEffect(() => { if (job) setEvery(job.status === "running" || job.factcheck?.running ? 1500 : 6000); }, [job?.status, job?.factcheck?.running]);  // eslint-disable-line react-hooks/exhaustive-deps
  const hasScript = !!job?.artifacts.script;
  const script = useLoad(() => (hasScript ? api.script(id) : Promise.resolve(null)), [id, hasScript]);

  const current = useMemo(() => (job ? defaultStep(job) : null), [job]);
  if (error && !job) return <Card className="p-6 text-[15px] text-bad-text">작업을 불러오지 못했습니다. {error}</Card>;
  if (!job || !current) return <div className="text-[13px] text-sub">불러오는 중</div>;
  const step = job.steps.find((s) => s.key === (sel ?? current))!;

  return (
    <div>
      <JobHeader job={job} tab="make" />
      <Rail job={job} sel={step.key} onSel={setSel} />
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-6">
          <NowCard job={job} step={step} onNext={async () => { await api.next(id); }} onReload={reload} />
          {step.key === "read" && job.factcheck && <FactCheckCard job={job} fc={job.factcheck} onChanged={reload} />}
          {step.key === "read" && step.status !== "todo" && step.status !== "running" && <TopicCard job={job} />}
          <section>
            <SectionLabel>만들어진 것</SectionLabel>
            {!job.artifacts.script && !job.artifacts.voice && !job.artifacts.images?.length && !job.artifacts.video
              ? <Card className="px-6 py-8 text-[15px] text-sub">만들어지는 대로 여기에 쌓입니다.</Card> : null}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {script.data && <ScriptCard job={job} script={script.data.script} />}
              {job.artifacts.voice && <VoiceCard job={job} />}
              {!!job.artifacts.images?.length && <ImagesCard job={job} />}
              {job.artifacts.video && <VideoCard job={job} />}
            </div>
          </section>
        </div>
        <aside className="space-y-4">
          <LogCard log={job.log} />
          <DescriptionCard job={job} />
        </aside>
      </div>
    </div>
  );
}
