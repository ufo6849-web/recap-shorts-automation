import { ImagePlus, Mic, Pause, Play, RotateCcw, ShieldCheck, SkipBack, Wand2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fetchTimeline, mediaUrl } from "../api";
import { isArchived } from "../lib";
import JobHeader from "../components/JobHeader";
import { PREVIEW_H, PREVIEW_W, usePlayer } from "../editor/player";
import { shotAt } from "../editor/engine";
import TimelineStrip from "../editor/TimelineStrip";
import { go, useLoad } from "../hooks";
import { Button, Card, Chip, ICON, IconButton, Label, Status, cx, fmtTime } from "../ui";
import type { AssetEntry, Timeline } from "../types";
import BlurModal from "./BlurModal";

const KIND: Record<string, string> = { cap: "글 캡처", ai: "AI 그림" };

export default function SceneEditor({ id }: { id: string }) {
  const job = useLoad(() => api.job(id), [id], 2500);
  const scenes = useLoad(() => api.scenes(id), [id]);
  const tlLoad = useLoad(() => fetchTimeline(id), [id]);
  const assets = useLoad(() => api.assets(id), [id]);
  const [bust, setBust] = useState(0);
  const introLoad = useLoad(() => api.introPreview(id), [id, bust]);
  const intro = introLoad.data?.intro ?? null;
  const [tl, setTl] = useState<Timeline | null>(null);
  const [orig, setOrig] = useState("");
  const [sel, setSel] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [prompting, setPrompting] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [blurFor, setBlurFor] = useState<string | null>(null);
  // 장면 안 경계를 사람이 옮긴 문장(moved)과 '자동으로 나누기'로 저장할 문장(resetCuts → null)
  const [moved, setMoved] = useState<Set<number>>(() => new Set());
  const [resetCuts, setResetCuts] = useState<Set<number>>(() => new Set());
  const timeEl = useRef<HTMLSpanElement>(null);

  useEffect(() => { if (tlLoad.data && !tl) { setTl(structuredClone(tlLoad.data)); setOrig(JSON.stringify(tlLoad.data)); } }, [tlLoad.data, tl]);

  const resolve = useCallback((f: string) => `${mediaUrl(id, f)}${bust ? `?v=${bust}` : ""}`, [id, bust]);
  const voiceUrl = tl ? mediaUrl(id, tl.audio.voice) + (bust ? `?v=${bust}` : "") : null;
  const player = usePlayer(tl, voiceUrl, resolve, bust, intro);
  const { subscribe, tRef, seek, requestDraw } = player;

  useEffect(() => subscribe((t) => {
    if (timeEl.current) timeEl.current.textContent = fmtTime(t);
    if (tl) setSel((p) => { const k = shotAt(tl, t); return k === p ? p : k; });   // 고르기 칸이 재생선을 따라간다
  }), [subscribe, tl]);
  useEffect(() => { requestDraw(); }, [tl, requestDraw]);

  // 이미지를 다시 만들거나 목소리를 다시 녹음한 뒤 작업이 끝나면 새 파일로 바꿔 끼운다
  const running = job.data?.status === "running";
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) {
      setBust((b) => b + 1); setNote("다시 만든 결과를 불러왔습니다.");
      scenes.reload(); assets.reload();
      fetchTimeline(id).then((t) => { setTl(structuredClone(t)); setOrig(JSON.stringify(t)); }).catch(() => {});
    }
    wasRunning.current = running;
  }, [running]); // eslint-disable-line react-hooks/exhaustive-deps

  const sentences = scenes.data?.sentences ?? [];
  const byFile = useMemo(() => new Map((assets.data ?? []).map((e) => [e.file, e])), [assets.data]);
  const dirty = tl ? JSON.stringify(tl) !== orig || moved.size > 0 || resetCuts.size > 0 : false;
  /** 샷이 속한 문장 번호: 시작 시각이 문장 시작 이상인 마지막 문장(훅은 0초부터) */
  const sentenceIdxOf = useCallback((start: number) => {
    let k = sentences[0]?.index ?? 0;
    for (const s of sentences) if ((s.index === sentences[0]?.index ? 0 : s.start) <= start + 0.002) k = s.index;
    return k;
  }, [sentences]);
  const scoreOf = useCallback((i: number): number | null => {
    if (!tl || !scenes.data) return null;
    const sh = tl.shots[i];
    return scenes.data.scores[String(sentenceIdxOf(sh.start))]?.[sh.file] ?? null;
  }, [tl, scenes.data, sentenceIdxOf]);

  const pending = useMemo(() => {
    if (!tl) return [] as string[];
    return [...new Set(tl.shots.map((s) => s.file))].filter((f) => byFile.get(f)?.needs_review && !byFile.get(f)?.sensitive?.flagged);
  }, [tl, byFile]);

  if ((scenes.error || tlLoad.error) && !tl) return <Card className="p-6 text-[15px] text-bad-text">장면 정보를 불러오지 못했습니다. {scenes.error ?? tlLoad.error}</Card>;
  if (!tl || !scenes.data || !job.data) return <div className="text-[13px] text-sub">불러오는 중</div>;

  const si = Math.min(sel, tl.shots.length - 1);
  const shot = tl.shots[si];
  const sIdx = sentenceIdxOf(shot.start);
  const sent = sentences.find((s) => s.index === sIdx);
  const entry: AssetEntry | undefined = byFile.get(shot.file);
  const sc = scoreOf(si);
  const edit = (fn: (t: Timeline) => void) => setTl((p) => { if (!p) return p; const n = structuredClone(p); fn(n); return n; });
  const swap = (a: number, b: number) => edit((t) => { const x = t.shots[a], y = t.shots[b]; [x.file, y.file] = [y.file, x.file]; [x.source_id, y.source_id] = [y.source_id, x.source_id]; });
  const setFile = (file: string) => edit((t) => { t.shots[si].file = file; t.shots[si].source_id = file.split("/").pop()?.replace(/\.[a-z]+$/i, ""); });

  const plan = (): Record<string, string[]> => {
    const out: Record<string, string[]> = {};
    for (const s of sentences) out[String(s.index)] = [];
    for (const sh of tl.shots) if (sh.kind !== "meme") out[String(sentenceIdxOf(sh.start))].push(sh.file);   // 짤 샷은 배치표가 아니다(fx 단계가 다시 넣음)
    return out;
  };
  /** 저장할 사용자 컷: 이번에 옮긴 문장(현재 tl 샷 시각) + 이미 사용자 컷이 있는 문장(그대로), 자동으로 돌린 문장은 null. */
  const cutsOut = (): Record<string, number[] | null> | null => {
    const p = plan();
    const out: Record<string, number[] | null> = {};
    for (const s of sentences) {
      const k = String(s.index);
      const files = p[k] ?? [];
      if (resetCuts.has(s.index)) { if (files.length > 1) out[k] = null; continue; }
      const userSaved = Array.isArray(s.cuts) && s.cuts.length > 0;
      if (!moved.has(s.index) && !userSaved) continue;
      if (files.length < 2) continue;
      const ts = tl.shots
        .filter((sh) => sh.kind !== "meme" && sentenceIdxOf(sh.start) === s.index)
        .map((sh) => sh.start).slice(1);
      out[k] = ts.map((t) => Math.round(t * 1000) / 1000);
    }
    return Object.keys(out).length ? out : null;
  };
  /** 경계를 끌어 옮김: shots[i].end = shots[i+1].start = t, 그 문장을 '옮김'으로 표시(자동 되돌리기는 취소) */
  const resizeCut = (i: number, t: number) => {
    const a = tl.shots[i], b = tl.shots[i + 1];
    if (!a || !b || a.kind === "meme" || b.kind === "meme") return;
    const idx = sentenceIdxOf(a.start);
    if (idx !== sentenceIdxOf(b.start)) return;
    setMoved((p) => new Set(p).add(idx));
    setResetCuts((p) => { const n = new Set(p); n.delete(idx); return n; });
    edit((tl2) => {
      tl2.shots[i].end = Math.round(t * 1000) / 1000;
      tl2.shots[i + 1].start = Math.round(t * 1000) / 1000;
    });
  };
  /** 그 문장을 자동으로 나누기(서버에서 null 로 저장 → 자동 컷). 미리보기는 저장 뒤 새 타임라인으로 바뀐다. */
  const autoCuts = (idx: number) => {
    setResetCuts((p) => new Set(p).add(idx));
    setMoved((p) => { const n = new Set(p); n.delete(idx); return n; });
  };
  const act = async (name: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(name); setNote(null);
    try { await fn(); setNote(ok); await job.reload(); } catch (e) { setNote(`실패했습니다. ${(e as Error).message}`); } finally { setBusy(null); }
  };
  const persist = async () => {
    await api.saveScenes(id, plan(), cutsOut() ?? undefined);
    const t = await fetchTimeline(id);
    setTl(structuredClone(t)); setOrig(JSON.stringify(t));
    setMoved(new Set()); setResetCuts(new Set());
    scenes.reload();
  };
  const save = () => act("save", persist, "저장했습니다. 영상은 다시 만들어야 합니다.");
  const renderIt = () => act("render", async () => { if (dirty) await persist(); await api.render(id); }, "영상 만들기를 시작했습니다.");

  const scoreRow = scenes.data.scores[String(sIdx)] ?? {};
  const top = Object.entries(scoreRow).sort((a, b) => b[1] - a[1]).slice(0, 7).map(([f]) => f);
  const candFiles = showAll ? (scenes.data.candidates.map((c) => c.file)) : [...new Set([shot.file, ...top])];
  const needBlurFile = pending[0];
  const openBlur = (f: string) => { setBlurFor(f); };
  const blurEntry = blurFor ? byFile.get(blurFor) : undefined;

  return (
    <div>
      <JobHeader job={job.data} tab="scenes" />
      {running && <Card className="mb-4 flex items-center gap-3 px-5 py-3 text-[15px]"><Status tone="accent" pulse>작업 중</Status><span className="text-sub">{job.data.steps.find((s) => s.status === "running")?.detail ?? job.data.steps.find((s) => s.status === "running")?.label} — 끝나면 미리보기가 새 파일로 바뀝니다.</span></Card>}
      {pending.length > 0 && (
        <Card className="mb-4 flex flex-wrap items-center justify-between gap-3 px-5 py-3">
          <span className="text-[15px]"><Status tone="warn">가림 확인 필요</Status><span className="ml-3 text-sub">영상에 쓰인 글 캡처 {pending.length}장에서 얼굴·아이디가 보이는지 확인하지 않았습니다.</span></span>
          <Button size="sm" variant="secondary" icon={<ShieldCheck size={15} {...ICON} />} disabled={isArchived(job.data)} onClick={() => { const k = tl.shots.findIndex((s) => s.file === needBlurFile); setSel(k); seek(tl.shots[k].start + Math.min(0.25, (tl.shots[k].end - tl.shots[k].start) / 2)); openBlur(needBlurFile); }}>확인하기</Button>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        <div>
          <div className="stage flex justify-center rounded-[14px] border border-line p-4">
            <canvas ref={player.canvasRef} width={PREVIEW_W} height={PREVIEW_H} className="aspect-[9/16] w-full max-w-[300px] rounded-[10px] bg-black" aria-label="영상 미리보기" />
          </div>
          <div className="mt-3 flex items-center gap-2">
            <IconButton title="처음으로" onClick={() => seek(0)}><SkipBack size={18} {...ICON} /></IconButton>
            <button onClick={() => (player.playing ? player.pause() : player.play())} aria-label={player.playing ? "멈춤" : "재생"}
              className="press flex h-10 w-10 items-center justify-center rounded-full bg-accent text-white">
              {player.playing ? <Pause size={16} fill="currentColor" strokeWidth={0} /> : <Play size={16} fill="currentColor" strokeWidth={0} className="ml-0.5" />}
            </button>
            <span className="ml-1 text-[15px] font-medium"><span ref={timeEl}>0:00.0</span> <span className="text-sub">/ {fmtTime(tl.duration)}</span></span>
          </div>
        </div>

        <Card className="p-5 lg:self-start">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <Label>{si + 1}번 그림 · {shot.start.toFixed(1)}–{shot.end.toFixed(1)}초</Label>
              <p className="mt-1 text-[15px] leading-relaxed">{sent ? <><span className="text-sub">{sent.index === 0 ? "훅" : `${sent.index}번 문장`} · </span>{sent.text}</> : "이 구간에 해당하는 문장이 없습니다."}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
                <span className="text-sub">길이</span>
                <span className="font-semibold tabular-nums">{(shot.end - shot.start).toFixed(2)}초</span>
                {shot.kind === "meme" && <Chip>전체화면 짤</Chip>}
                {!sent?.cuts?.length && moved.has(sIdx) && <Chip tone="accent">이 문장 경계를 옮김 · 저장 전</Chip>}
                {!!sent?.cuts?.length && !resetCuts.has(sIdx) && <Chip tone="accent">저장된 사용자 컷</Chip>}
                {resetCuts.has(sIdx) && <Chip tone="outline">저장하면 자동으로 나눔</Chip>}
                {sent && (sent.cuts?.length || moved.has(sIdx) || resetCuts.has(sIdx)) && (sent.images?.length ?? 0) > 1 && (
                  <Button size="sm" variant="ghost" icon={<Wand2 size={14} {...ICON} />} disabled={isArchived(job.data)}
                    onClick={() => autoCuts(sent.index)}>이 문장 자동으로 나누기</Button>
                )}
              </div>
              {intro && shot.start < intro.t_cut - 1e-3 && (
                <p className="mt-2 text-[13px] text-sub">
                  {shot.end <= intro.t_cut + 1e-3 ? "이 구간은 인트로 카드가 덮어서 이 그림은 영상에 보이지 않습니다." : `${intro.t_cut.toFixed(1)}초까지는 인트로 카드가 덮고, 그 뒤부터 이 그림이 보입니다.`}
                  {" "}인트로 머리줄은 ‘{[intro.label_icon, intro.label].filter(Boolean).join(" ")}’로 고정입니다.
                </p>
              )}
            </div>
            <div className="shrink-0 text-right">
              <div className="text-[11px] font-medium text-sub">어울림</div>
              <div className={cx("text-[28px] font-semibold leading-none tracking-[-0.02em]", sc != null && sc < 0.35 && "text-warn-text")}>{sc != null ? sc.toFixed(2) : "–"}</div>
            </div>
          </div>

          <div className="mt-5 flex gap-4">
            <img src={resolve(shot.file)} alt="" className="aspect-[4/3] w-[168px] shrink-0 rounded-[10px] bg-fill object-cover" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Chip>{entry?.role === "news" ? "뉴스" : (KIND[entry?.kind ?? ""] ?? "그림")}</Chip>
                {entry?.kind === "cap" && entry.role !== "news" && (entry.needs_review && !entry.sensitive?.flagged ? <Chip tone="warn">가림 확인 필요</Chip> : <Chip tone="ok">{entry.blurred ? "가림 확인됨" : "확인함"}</Chip>)}
                {entry?.sensitive && entry.sensitive.level !== "none" && <Chip tone={entry.sensitive.level === "strong" ? "bad" : "warn"}>자극적인 그림{entry.sensitive.treatment === "gray" ? " · 흑백" : entry.sensitive.treatment === "gray_blur" ? " · 흑백+흐림" : ""}</Chip>}
                {entry?.recommend_regenerate && <Chip tone="warn">다시 만들기 추천</Chip>}
              </div>
              <div className="mt-2 truncate text-[13px] text-sub" title={shot.file}>{shot.file}</div>
              {(entry?.needs_review || entry?.recommend_regenerate || (entry?.sensitive && entry.sensitive.level !== "none")) && entry.review_reason && <div className="mt-1 text-[13px] text-warn-text">{entry.review_reason}</div>}
              <div className="mt-3 flex flex-wrap gap-2">
                {entry?.kind === "cap" && entry.role !== "news" && <Button size="sm" variant={entry.needs_review ? "primary" : "secondary"} icon={<ShieldCheck size={15} {...ICON} />} disabled={isArchived(job.data)} onClick={() => openBlur(shot.file)}>가리기</Button>}
                {entry?.kind === "ai" && <Button size="sm" variant="secondary" icon={<Wand2 size={15} {...ICON} />} disabled={busy !== null || running || isArchived(job.data)} onClick={() => setPrompting((v) => !v)}>이미지 다시 만들기</Button>}
                <Button size="sm" variant="secondary" icon={<Mic size={15} {...ICON} />} disabled={busy !== null || running || !sent || isArchived(job.data)}
                  onClick={() => sent && act("redo", () => api.redoVoice(id, sent.index), `${sent.index === 0 ? "훅" : sent.index + "번 문장"}을 다시 녹음합니다.`)}>문장 다시 녹음</Button>
              </div>
            </div>
          </div>
          {prompting && (
            <div className="rise mt-4 flex gap-2">
              <input value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="바꾸고 싶은 그림 설명 (비우면 원래 설명으로)" aria-label="그림 설명"
                className="h-10 min-w-0 flex-1 rounded-[10px] border border-line bg-fill px-3 text-[15px] outline-none focus:border-accent" />
              <Button variant="primary" disabled={busy !== null} onClick={() => act("regen", () => api.regenerate(id, shot.file, prompt || undefined), "그림을 다시 만드는 중입니다. 한 장에 1분쯤 걸립니다.").then(() => setPrompting(false))}>만들기</Button>
            </div>
          )}

          <div className="mt-5 border-t border-line pt-4">
            <div className="flex items-center justify-between"><Label>이 문장에 어울리는 그림</Label>
              <button onClick={() => setShowAll(!showAll)} className="press flex items-center gap-1 text-[13px] text-accent"><ImagePlus size={14} {...ICON} />{showAll ? "후보만 보기" : "전체 그림에서 고르기"}</button>
            </div>
            <div className="mt-2 grid grid-cols-4 gap-2 sm:grid-cols-6">
              {candFiles.map((f) => {
                const s = scoreRow[f], e = byFile.get(f);
                return (
                  <button key={f} onClick={() => setFile(f)} title={f}
                    className={cx("press relative aspect-[4/3] overflow-hidden rounded-lg bg-fill", f === shot.file ? "ring-2 ring-accent" : "ring-1 ring-inset ring-line-strong hover:ring-accent")}>
                    <img src={resolve(f)} alt="" loading="lazy" className="h-full w-full object-cover" />
                    {s != null && <span className={cx("absolute bottom-0.5 left-0.5 rounded px-1 text-[11px] font-medium leading-4 text-white", s < 0.35 ? "bg-[#b36b00]" : "bg-black/65")}>{s.toFixed(2)}</span>}
                    {e?.needs_review && <span className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-warn" title="가림 확인 필요" />}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-[13px] text-sub">점수는 {scenes.data.scored_by === "clef" ? "Clef가 “이 그림이 이 문장이 나오는 동안 보여 주기에 어울리나”를 본 값에 규칙 점수를 섞은" : "Clef 없이 규칙(문장용 AI 그림 우선, 캡처와 AI 번갈아)으로 매긴"} 참고 값입니다. 낮아도 자동으로 바꾸지 않습니다.</p>
          </div>
        </Card>
      </div>

      <Card className="mt-6 p-4">
        <TimelineStrip tl={tl} sentences={sentences} scoreOf={scoreOf} resolve={resolve} voiceUrl={voiceUrl} subscribe={subscribe}
          onSeek={seek} sel={si} onSel={setSel} onSwap={swap} activeSentence={sentenceIdxOf(tRef.current)} onResize={resizeCut} />
        <p className="px-1 pt-1 text-[13px] text-sub">그림을 끌어 다른 그림 위에 놓으면 맞바꿉니다. 같은 문장 안 그림 사이의 경계를 끌면 두 그림의 길이가 바뀌고, 놓을 때 말이 끊기는 어절 사이에 붙습니다(Alt 를 누르면 자유롭게). 문장과 문장 사이 경계는 목소리에 맞춰져 있어 고정입니다.</p>
      </Card>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <span className="text-[13px] text-sub">{note ?? (dirty ? "저장하지 않은 수정이 있습니다." : "")}</span>
        <div className="flex gap-2">
          <Button variant="ghost" icon={<RotateCcw size={15} {...ICON} />} disabled={!dirty && !moved.size && !resetCuts.size} onClick={() => { if (tlLoad.data) { setTl(structuredClone(tlLoad.data)); setOrig(JSON.stringify(tlLoad.data)); } setMoved(new Set()); setResetCuts(new Set()); }}>되돌리기</Button>
          <Button variant="secondary" disabled={!dirty || busy !== null || isArchived(job.data)} onClick={save}>저장</Button>
          <Button variant="primary" disabled={busy !== null || running || isArchived(job.data)} onClick={renderIt}>{dirty ? "저장하고 영상 만들기" : "영상 다시 만들기"}</Button>
          <Button variant="secondary" onClick={() => go(`/job/${encodeURIComponent(id)}/review`)}>검수로</Button>
        </div>
      </div>

      {blurEntry && blurEntry.src && (
        <BlurModal id={id} entry={blurEntry} assets={assets.data ?? []} bust={bust} onClose={() => setBlurFor(null)}
          onDone={(cards) => { setBlurFor(null); setBust((b) => b + 1); assets.reload(); job.reload(); fetchTimeline(id).then((t) => { setTl(structuredClone(t)); setOrig(JSON.stringify(t)); }).catch(() => {}); setNote(`가림을 적용했습니다. 카드 ${cards.length}장을 다시 만들었고, 영상은 다시 만들어야 합니다.`); }} />
      )}
    </div>
  );
}
