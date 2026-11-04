import { Check, Copy, Download } from "lucide-react";
import { useState } from "react";
import { api, fetchReview, fetchText, fetchTimeline, mediaUrl } from "../api";
import CapCutExport from "../components/CapCutExport";
import JobHeader from "../components/JobHeader";
import { go, useCopy, useLoad } from "../hooks";
import { clock, flagText, isArchived } from "../lib";
import { Button, Card, Chip, ICON, SectionLabel, Status, cx } from "../ui";
import type { AssetEntry, JobState, ReviewJson, SensitiveInfo } from "../types";

interface Metric { label: string; value: number; ref_low: number; ref_high: number; unit?: string; ref_name?: string }
interface ClefRow { key: string; label: string; value: number; good: boolean; note: string }
// 규격표(docs/02_program_design.md 2절) 범위. 같은 자로 재는 별도 측정 결과가 백엔드에 없어서 review.json 의 값으로 계산한다.
function metricsOf(r: ReviewJson): Metric[] {
  const out: Metric[] = [
    { label: "길이", value: +r.duration.toFixed(1), ref_low: 30, ref_high: 38, unit: "초", ref_name: "규격표" },
    { label: "대본 음절", value: r.syllables, ref_low: 190, ref_high: 220, unit: "음절", ref_name: "규격표" },
    { label: "첫 컷", value: +r.first_cut.toFixed(2), ref_low: 2.4, ref_high: 2.9, unit: "초", ref_name: "규격표" },
    { label: "그림 길이(평균)", value: +(r.duration / Math.max(1, r.shots)).toFixed(2), ref_low: 1.2, ref_high: 1.6, unit: "초", ref_name: "규격표" },
  ];
  if (r.speech_rate) out.splice(2, 0, { label: "초당 음절", value: +r.speech_rate.toFixed(2), ref_low: 6.3, ref_high: 6.7, unit: "음절/초", ref_name: "규격표" });
  return out;
}
const CLEF: [keyof NonNullable<ReviewJson["clef"]>, string, boolean, string, string][] = [
  ["topic_match", "주제와 영상이 맞나", true, "제목과 영상 내용이 같은 이야기로 보입니다.", "영상 내용이 제목과 다르게 보일 수 있습니다. 눈으로 확인하세요."],
  ["opens_finished", "첫 장면이 눈에 띄나", true, "첫 장면이 주제와 이어져 보입니다.", "첫 장면이 약해 보일 수 있습니다."],
  ["narration", "목소리가 들리나", true, "내레이션이 들립니다.", "내레이션이 잘 안 들릴 수 있습니다. 소리를 확인하세요."],
  ["face", "또렷한 사람 얼굴이 나오나", false, "얼굴이 또렷하게 보이는 장면이 없어 보입니다.", "얼굴이 또렷한 장면이 있을 수 있습니다. 가림을 확인하세요."],
  ["caption_cut", "자막이 화면 밖으로 잘렸나", false, "잘린 자막이 없어 보입니다.", "잘린 자막이 있을 수 있습니다. 눈으로 확인하세요."],
  ["music", "배경음악이 들리나", false, "배경음악이 없어 보입니다(의도한 대로).", "음악이 들린다고 나왔습니다. 의도한 것이 아니라면 확인하세요."],
];
function clefRows(r: ReviewJson): ClefRow[] {
  const c = r.clef;
  if (!c) return [];
  return CLEF.flatMap(([k, label, good, okNote, warnNote]) => {
    const v = c[k] as number | null | undefined;
    if (v == null) return [];
    const ok = good ? v >= 0.5 : v < 0.5;
    return [{ key: k, label, value: v, good: ok, note: ok ? okNote : warnNote }];
  });
}

interface WarnRow { key: string; what: string; msg: string; open: boolean; state: string; file?: string; to?: "script" | "scenes"; tone?: "warn" | "bad" }
const KIND: Record<string, string> = { cap: "글 캡처", ai: "AI 그림" };
// review.json 의 대본 경고(script_flags)와 확인할 그림(needs_review_assets). 그림은 지금 assets.json 상태로 다시 본다 —
// 검수 뒤 편집기에서 가리거나 다시 만들었으면 '확인함'으로 바뀐다. 위 '가림 확인'에 이미 나온 캡처와 영상에 없는 그림은 뺀다.
function warnRows(r: ReviewJson, live: Map<string, AssetEntry>, used: Set<string> | null, shown: Set<string>): WarnRow[] {
  const out: WarnRow[] = (r.script_flags ?? []).map((f, k) => {
    const t = flagText(f);
    return { key: `flag${k}`, what: "대본", msg: t.index != null ? `문장 ${t.index + 1}: ${t.msg}` : t.msg, open: true, state: "확인", to: "script" };
  });
  for (const f of r.needs_review_assets ?? []) {
    if (shown.has(f) || (used && !used.has(f))) continue;
    const e = live.get(f);
    const open = !e || !!e.needs_review || !!e.recommend_regenerate;
    out.push({ key: f, file: f, what: KIND[e?.kind ?? ""] ?? "그림", to: "scenes", open,
      msg: e?.review_reason || (e?.recommend_regenerate ? "다시 만들기 추천" : "사람 확인 필요"),
      state: !open ? "확인함" : e?.recommend_regenerate ? "다시 만들기 추천" : "확인 필요" });
  }
  // 효과음 저작권(config/sfx_rights.json, 원본 파일 이름만 보고 매긴 판단): 위험 높음(high)이 먼저, 같은 위험이면 영상 시각 순. 막지도 빼지도 않는다.
  const sfx = [...(r.sfx_rights ?? [])].sort((a, b) => (a.level === b.level ? 0 : a.level === "high" ? -1 : 1) || a.t - b.t);
  sfx.forEach((w, k) => out.push({ key: `sfx${k}`, what: `효과음 ${clock(w.t)} · ${w.id}`, msg: w.note, open: true,
    state: w.level === "high" ? "저작권 위험 높음" : "저작권 주의", tone: w.level === "high" ? "bad" : "warn" }));
  return out;
}

// 사실 확인 줄(11.7): 주의를 확인하고 넘긴 것 · 직접 확인으로 넘긴 것 · 사실 확인 없이 만든 이전 작업(정책 없음, 읽기는 끝남)
function factRows(j: JobState): WarnRow[] {
  const fc = j.factcheck;
  if (j.factcheck_policy == null) return j.steps.find((s) => s.key === "read")?.status === "done"
    ? [{ key: "fc-legacy", what: "사실 확인", msg: "사실 확인 없이 만든 이전 작업", open: true, state: "확인 안 함" }] : [];
  if (fc?.override) return [{ key: "fc-override", what: "사실 확인", msg: `직접 확인으로 넘김 — ${fc.override.note}`, open: true, state: "직접 확인", tone: "warn" }];
  if (fc?.status === "warn" && fc.ack) return [{ key: "fc-ack", what: "사실 확인", msg: "일부 주장이 보도로 확인되지 않았습니다", open: false, state: "확인함" }];
  return [];
}

// 자극적인 그림 처리(config/sensitive.json, recap_shorts/images/sensitive.py): 판정 level·kind 와 지금 처리 상태
const SENS_KIND: Record<string, string> = { blood_injury: "피·부상", gore_body: "신체 훼손", corpse_death: "시신·사망", accident_disaster: "사고·재난", violence_weapon: "폭력·무기", animal_harm: "동물 피해", medical_procedure: "의료 시술", sexual_suggestive: "선정적", other: "기타" };
const SENS_NOW: Record<string, string> = { gray: "흑백", gray_blur: "흑백+흐림", none: "처리 안 함", none_by_user: "원래대로" };
type SensChoice = "none" | "gray" | "gray_blur";
const SENS_CHOICES: [SensChoice, string, string][] = [["none", "원래대로", "처리하지 않고 원래 그림을 씁니다"], ["gray", "흑백만", "흑백으로만 바꿉니다"], ["gray_blur", "흐림", "흑백으로 바꾸고 전체를 강하게 흐립니다"]];
const sensNow = (s: SensitiveInfo): SensChoice => (s.treatment === "gray" ? "gray" : s.treatment === "gray_blur" ? "gray_blur" : "none");

function SensitiveSection({ id, items, bust, busy, archived, onPick, onRender, stale, renderBusy }: {
  id: string; items: AssetEntry[]; bust: number; busy: string | null; archived: boolean; stale: boolean; renderBusy: boolean;
  onPick: (file: string, c: SensChoice) => void; onRender: () => void;
}) {
  const [orig, setOrig] = useState<Set<string>>(new Set());   // 원본을 보고 있는 그림
  const flip = (f: string) => setOrig((p) => { const n = new Set(p); if (n.has(f)) n.delete(f); else n.add(f); return n; });
  if (items.length === 0 && !stale) return null;
  return (
    <section>
      <SectionLabel>자극적인 그림 처리</SectionLabel>
      <Card className="divide-y divide-line">
        {items.map((e) => {
          const s = e.sensitive!;
          const cur = sensNow(s);
          const treated = s.treatment === "gray" || s.treatment === "gray_blur";
          const seeOrig = orig.has(e.file) && treated;
          const src = seeOrig ? e.file.replace(/\.[a-z0-9]+$/i, ".orig.png") : e.file;
          return (
            <div key={e.file} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 px-5 py-4">
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <button type="button" onClick={() => flip(e.file)} disabled={!treated} title={treated ? "누르면 원본과 처리된 그림을 번갈아 봅니다" : undefined}
                  className="relative shrink-0 disabled:cursor-default">
                  <img src={`${mediaUrl(id, src)}?v=${bust}`} alt="" className="aspect-[4/3] w-[96px] rounded-lg bg-fill object-cover" />
                  {treated && <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[11px] font-medium text-white">{seeOrig ? "원본" : "처리됨"}</span>}
                </button>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-[15px] font-medium">
                    <Chip tone={s.level === "strong" ? "bad" : "warn"}>{s.level === "strong" ? "강함" : "약함"}</Chip>
                    <span>{SENS_KIND[s.kind] ?? "기타"}</span>
                    <span className="text-[13px] font-normal text-sub">{SENS_NOW[s.treatment] ?? s.treatment}{s.user ? " · 사람이 정함" : ""}</span>
                  </div>
                  <div className="mt-0.5 text-[13px] text-sub">{s.blocked ? "모델이 안전 문제로 답을 막은 그림입니다(강함으로 봄). " : ""}{s.reason || "이유 없음"}</div>
                  {s.flagged && <div className="mt-0.5 text-[13px] text-warn-text">강한 자극으로 판정 — 직접 보고 처리를 정하세요</div>}
                </div>
              </div>
              <div className="flex shrink-0 gap-1.5">
                {SENS_CHOICES.map(([c, label, tip]) => (
                  <Button key={c} size="sm" variant={cur === c ? "primary" : "secondary"} title={tip}
                    disabled={busy !== null || archived || (cur === c && !s.flagged)}
                    onClick={() => onPick(e.file, c)}>{label}</Button>
                ))}
              </div>
            </div>
          );
        })}
        {stale && (
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
            <span className="text-[13px] text-sub">처리를 바꿨습니다. 영상에 반영하려면 영상을 다시 만들어야 합니다.</span>
            <Button size="sm" variant="primary" disabled={renderBusy || archived} onClick={onRender}>영상 다시 만들기</Button>
          </div>
        )}
      </Card>
      <p className="mt-2 text-[13px] text-sub">피·부상·시신·선정적인 그림은 흑백이나 흐림으로 가리면 유튜브 광고 제한(노란 딱지)을 피하는 데 도움이 됩니다. 판정은 AI 가 그림만 보고 한 것이라 틀릴 수 있습니다.</p>
    </section>
  );
}

function RangeRow({ m }: { m: Metric }) {
  const span = (m.ref_high - m.ref_low) || 1;
  const lo = m.ref_low - span * 0.6, hi = m.ref_high + span * 0.6;
  const pos = (v: number) => `${Math.max(0, Math.min(1, (v - lo) / (hi - lo))) * 100}%`;
  const inside = m.value >= m.ref_low && m.value <= m.ref_high;
  const word = inside ? "범위 안" : m.value < m.ref_low ? "낮음" : "높음";
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 py-3.5 lg:grid-cols-[110px_100px_minmax(0,1fr)_150px]">
      <div className="text-[15px] font-medium">{m.label}</div>
      <div className="text-right lg:text-left"><span className="text-[15px] font-semibold">{m.value}</span><span className="ml-1 text-[13px] text-sub">{m.unit}</span></div>
      <div className="relative col-span-2 h-1.5 rounded-full bg-fill lg:col-span-1" aria-label={`기준 ${m.ref_low}~${m.ref_high}`}>
        <div className="absolute inset-y-0 rounded-full bg-[color-mix(in_srgb,var(--c-ok)_32%,transparent)]" style={{ left: pos(m.ref_low), width: `calc(${pos(m.ref_high)} - ${pos(m.ref_low)})` }} />
        <div className={cx("absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface", inside ? "bg-ok" : "bg-warn")} style={{ left: pos(m.value) }} />
      </div>
      <div className="col-span-2 flex items-baseline justify-between gap-3 lg:col-span-1 lg:block lg:text-right">
        <div className={cx("text-[13px] font-medium", inside ? "text-ok-text" : "text-warn-text")}>{word}</div>
        <div className="text-[13px] text-sub">{m.ref_name ? `${m.ref_name} ` : ""}{m.ref_low}–{m.ref_high}</div>
      </div>
    </div>
  );
}

export default function Review({ id }: { id: string }) {
  const job = useLoad(() => api.job(id), [id], 3000);
  const j = job.data;
  const desc = useLoad(() => (j?.artifacts.description ? fetchText(id, j.artifacts.description) : Promise.resolve(null)), [id, j?.artifacts.description]);
  const rv0 = useLoad(() => (j?.artifacts.review ? fetchReview(id, String(j.artifacts.review)) : Promise.resolve(null)), [id, j?.artifacts.review, j?.status]);
  const assets = useLoad(() => api.assets(id), [id, j?.status]);
  const tl = useLoad(() => fetchTimeline(id).catch(() => null), [id, j?.status]);
  const review = rv0.data;
  const used = new Set((tl.data?.shots ?? []).map((x) => x.file));
  // 강한 자극으로 판정돼 needs_review 가 켜진 그림(sensitive.flagged)은 얼굴·아이디 '가림 확인'이 아니라 아래 '자극적인 그림 처리'에서 다룬다
  const unchecked = (assets.data ?? []).filter((e) => e.needs_review && used.has(e.file) && !e.sensitive?.flagged);
  const sens = (assets.data ?? []).filter((e) => e.sensitive && e.sensitive.level !== "none" && (!tl.data || used.has(e.file)))
    .sort((a, b) => Number(b.sensitive!.level === "strong") - Number(a.sensitive!.level === "strong"));
  const warns = [...(j ? factRows(j) : []), ...(review ? warnRows(review, new Map((assets.data ?? []).map((e) => [e.file, e])), tl.data ? used : null, new Set([...unchecked, ...sens].map((e) => e.file))) : [])];
  const { done, copy } = useCopy();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sensBusy, setSensBusy] = useState<string | null>(null);
  const [sensStale, setSensStale] = useState(false);
  const [sensBust, setSensBust] = useState(0);
  const pickSens = async (file: string, c: SensChoice) => {
    setSensBusy(file); setErr(null);
    try { const r = await api.sensitive(id, file, c); if (r.video_stale) setSensStale(true); setSensBust((b) => b + 1); await Promise.all([assets.reload(), job.reload()]); }
    catch (e) { setErr((e as Error).message); } finally { setSensBusy(null); }
  };
  const rerender = async () => { setBusy(true); setErr(null); try { await api.render(id); setSensStale(false); await job.reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  const sensSection = <SensitiveSection id={id} items={sens} bust={sensBust} busy={sensBusy} archived={j ? isArchived(j) : false} stale={sensStale} renderBusy={busy} onPick={pickSens} onRender={rerender} />;

  if (job.error && !j) return <Card className="p-6 text-[15px] text-bad-text">작업을 불러오지 못했습니다. {job.error}</Card>;
  if (!j) return <div className="text-[13px] text-sub">불러오는 중</div>;
  const rv = j.steps.find((s) => s.key === "review")!;
  const video = j.artifacts.video;
  const approved = rv.status === "done";
  const canApprove = rv.status === "waiting";
  const unseenStrong = sens.filter((e) => e.sensitive?.flagged).length;
  const approve = async () => { if (unchecked.length && !confirm(`가림을 확인하지 않은 글 캡처 ${unchecked.length}장이 영상에 들어 있습니다. 그래도 승인할까요?`)) return; if (unseenStrong && !confirm(`강한 자극으로 판정된 그림 ${unseenStrong}장의 처리를 아직 확인하지 않았습니다. 그래도 승인할까요?`)) return; setBusy(true); setErr(null); try { await api.next(id); await job.reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };

  return (
    <div>
      <JobHeader job={j} tab="review" />
      {!video ? (
        <div className="space-y-6">
          <Card className="px-6 py-10 text-[15px] text-sub">영상이 아직 만들어지지 않았습니다. 영상 만들기가 끝나면 여기서 점검 결과를 볼 수 있습니다.</Card>
          {sensSection}
          {err && <p className="text-[13px] text-bad-text">{err}</p>}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-[300px_minmax(0,1fr)]">
          <div className="space-y-4 md:sticky md:top-20 md:self-start">
            <div className="stage flex justify-center rounded-[14px] border border-line p-4">
              <video src={mediaUrl(id, video)} controls preload="metadata" className="aspect-[9/16] w-full max-w-[240px] rounded-[10px] bg-black object-cover" />
            </div>
            <Card className="space-y-3 p-5">
              {approved ? <Status tone="ok">승인했습니다</Status> : canApprove ? <Status tone="warn">승인을 기다리고 있습니다</Status> : <Status tone="mute">{rv.status === "running" ? "점검하는 중" : "아직 점검 전입니다"}</Status>}
              <Button variant="primary" className="w-full" size="lg" disabled={!canApprove || busy || isArchived(j)} onClick={approve}>승인</Button>
              <a href={mediaUrl(id, video)} download className="press inline-flex h-10 w-full items-center justify-center gap-2 rounded-[10px] bg-fill text-[15px] font-semibold text-ink no-underline hover:brightness-95"><Download size={16} {...ICON} />영상 파일 받기</a>
              <CapCutExport id={id} disabled={j.status === "running"} />
              <Button variant="ghost" className="w-full" onClick={() => go(`/job/${encodeURIComponent(id)}/scenes`)}>장면 편집기에서 고치기</Button>
              {err && <p className="text-[13px] text-bad-text">{err}</p>}
              <p className="text-[13px] text-sub">승인해도 업로드는 하지 않습니다. 파일을 받아 직접 올리고, 아래 설명 문구를 붙여 넣으세요.</p>
            </Card>
          </div>

          <div className="space-y-6">
            {unchecked.length > 0 && (
              <section>
                <SectionLabel>가림 확인</SectionLabel>
                <Card className="p-5">
                  <p className="text-[15px]"><Status tone="warn">확인 필요</Status><span className="ml-3">영상에 쓰인 글 캡처 {unchecked.length}장은 얼굴·아이디가 보이는지 사람이 확인해야 합니다. Clef가 아직 이 판단을 못 해 줍니다.</span></p>
                  <div className="mt-3 flex flex-wrap gap-2">{unchecked.map((e) => <img key={e.file} src={mediaUrl(id, e.file)} alt="" className="aspect-[4/3] w-[120px] rounded-lg bg-fill object-cover" />)}</div>
                  <Button className="mt-4" variant="secondary" onClick={() => go(`/job/${encodeURIComponent(id)}/scenes`)}>장면 편집기에서 가리기</Button>
                </Card>
              </section>
            )}
            {sensSection}
            {warns.length > 0 && (
              <section>
                <SectionLabel>확인할 것</SectionLabel>
                <Card className="divide-y divide-line">
                  {warns.map((w) => (
                    <div key={w.key} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 py-4">
                      <div className="flex min-w-0 flex-1 items-center gap-3">
                        {w.file && <img src={mediaUrl(id, w.file)} alt="" className="aspect-[4/3] w-[72px] shrink-0 rounded-lg bg-fill object-cover" />}
                        <div className="min-w-0"><div className="text-[15px] font-medium">{w.what}</div><div className="mt-0.5 text-[13px] text-sub">{w.msg}</div></div>
                      </div>
                      <div className="flex items-center gap-3">
                        <Status tone={w.tone ?? (w.open ? "warn" : "ok")} className={w.tone === "bad" ? "font-semibold" : undefined}>{w.state}</Status>
                        {w.to && <Button size="sm" variant="secondary" onClick={() => go(`/job/${encodeURIComponent(id)}/${w.to}`)}>{w.to === "script" ? "대본 편집" : "장면 편집기"}</Button>}
                      </div>
                    </div>
                  ))}
                </Card>
                <p className="mt-2 text-[13px] text-sub">검수 단계에서 남긴 경고입니다. 막지 않고 알려 주기만 합니다.</p>
                {review?.sfx_rights && review.sfx_rights.length > 0 && <p className="mt-1 text-[13px] text-sub">효과음 저작권 경고는 원본 파일 이름만 보고 매긴 점검이라 소리를 직접 대조한 것이 아닙니다. 영상에서 빼려면 작업 폴더의 fx/sfx_drop.json 에 시각과 id 를 적고 다시 렌더하세요.</p>}
              </section>
            )}
            <section>
              <SectionLabel>Clef 점검</SectionLabel>
              <Card className="divide-y divide-line">
                {(review ? clefRows(review) : []).length === 0 && <div className="px-5 py-6 text-[15px] text-sub">점검 결과가 아직 없습니다.</div>}
                {review && clefRows(review).map((c) => (
                  <div key={c.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 px-5 py-4">
                    <div><div className="text-[15px] font-medium">{c.label}</div><div className="mt-0.5 text-[13px] text-sub">{c.note}</div></div>
                    <div className="flex items-center gap-3">
                      <span className="text-[15px] font-semibold">{c.value.toFixed(2)}</span>
                      <Status tone={c.good ? "ok" : "warn"}>{c.good ? "문제 없음" : "확인"}</Status>
                    </div>
                  </div>
                ))}
              </Card>
              <p className="mt-2 text-[13px] text-sub">{review?.clef ? "Clef 점수는 같은 영상도 질문을 바꾸면 크게 흔들립니다. 참고용 경고로만 보세요." : "Clef 점검 결과가 없습니다(키 없음 또는 실패). 영상을 직접 보고 확인하세요."}</p>
            </section>

            <section>
              <SectionLabel>규격표와 비교</SectionLabel>
              <Card className="divide-y divide-line px-5">
                {!review && <div className="py-6 text-[15px] text-sub">측정 결과가 아직 없습니다.</div>}
                {review && metricsOf(review).map((m) => <RangeRow key={m.label} m={m} />)}
              </Card>
              <p className="mt-2 text-[13px] text-sub">초록 띠가 규격표(쇼츠 32편에서 잰 값)의 범위입니다. 벗어나도 막지 않고 알려 주기만 합니다.</p>
            </section>

            <section>
              <SectionLabel right={desc.data ? <Button size="sm" variant="secondary" icon={done ? <Check size={14} {...ICON} /> : <Copy size={14} {...ICON} />} onClick={() => copy(desc.data!)}>{done ? "복사됨" : "복사"}</Button> : undefined}>설명 문구</SectionLabel>
              <Card className="p-5">
                {desc.data ? <pre className="scroll-thin max-h-[300px] overflow-y-auto whitespace-pre-wrap font-[inherit] text-[13px] leading-relaxed">{desc.data}</pre>
                  : <p className="text-[15px] text-sub">설명 문구는 검수 단계에서 만들어집니다.</p>}
              </Card>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
