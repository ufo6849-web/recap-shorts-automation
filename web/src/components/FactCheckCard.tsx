import { useState, type ReactNode } from "react";
import { api } from "../api";
import { isArchived } from "../lib";
import { Button, Card, Chip, Label } from "../ui";
import type { FactClaim, FactSource, FactVerdict, FactcheckView, JobState } from "../types";

// 판정 칩: 고정 폭 60px(시안 mockup.html 의 claim()). 색은 ui.tsx 의 Chip 톤과 같다.
const VERDICT: Record<FactVerdict, { label: string; tone: "ok" | "warn" | "bad" }> = {
  true: { label: "사실", tone: "ok" },
  partly: { label: "일부 사실", tone: "warn" },
  false: { label: "사실 아님", tone: "bad" },
  unverified: { label: "확인 불가", tone: "warn" },
};
const MODEL_NAME: Record<string, string> = { "anthropic/claude-opus-5.5": "Claude Opus 5.5" };
// 중단 · 주의 · 통과: 제목과 설명 문구(시안 그대로).
const TOP: Record<"stop" | "warn" | "pass", { chip: ReactNode; title: string; sub: string }> = {
  stop: { chip: <Chip tone="bad">중단</Chip>, title: "보도와 다른 주장이 있어 멈췄습니다", sub: "원문에서 그 주장을 고친 뒤 다시 확인하거나, 직접 확인했다면 이유를 적고 계속하세요." },
  warn: { chip: <Chip tone="warn">주의</Chip>, title: "보도와 조금 다른 주장이 있습니다", sub: "대본은 아래 ‘대본에 쓸 사실’을 따릅니다. 확인되지 않은 수치는 대본에서 빠집니다." },
  pass: { chip: <Chip tone="ok">통과</Chip>, title: "보도로 확인했습니다", sub: "대본은 아래 ‘대본에 쓸 사실’을 따릅니다." },
};
const H3 = "mt-2 text-[18px] font-semibold tracking-[-0.01em]";
const SRC = "inline-flex h-7 max-w-full items-center gap-1 rounded-lg border border-line px-2.5 text-[13px] text-ink no-underline hover:bg-fill";

const cost = (c: number) => `$${c.toFixed(c < 0.01 ? 3 : 2)}`;
const timeOf = (iso: string) => new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
/** 출처 링크는 http(s) 만 건다. 서버가 한 번 거른 값이지만 화면에서도 한 번 더 본다. */
const linkable = (u: string) => /^https?:\/\//i.test(u);

/** 출처 칩: 새 탭으로 연다. 언론사 · 날짜(있을 때만) · ↗ */
function SourceLink({ s }: { s: FactSource }) {
  const body = <>{s.outlet}{s.date && <span className="text-sub">· {s.date.replaceAll("-", ".")}</span>}<span className="text-sub">↗</span></>;
  return linkable(s.url)
    ? <a href={s.url} target="_blank" rel="noopener noreferrer" title={s.title} className={SRC}>{body}</a>
    : <span title={s.title} className={SRC}>{body}</span>;
}

function ClaimRow({ c }: { c: FactClaim }) {
  const v = VERDICT[c.verdict] ?? VERDICT.unverified;
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <div className="flex items-start gap-3">
        <Chip tone={v.tone} className="mt-0.5 w-[60px] shrink-0 justify-center">{v.label}</Chip>
        <div className="min-w-0">
          <p className="text-[15px] font-medium leading-snug">{c.claim}</p>
          <p className="mt-1 text-[13px] leading-relaxed text-sub">{c.why}</p>
          {c.fact && <p className="mt-2 text-[13px] leading-relaxed"><span className="font-medium text-ok-text">대본에 쓸 사실</span> {c.fact}</p>}
          {c.sources.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{c.sources.map((s, i) => <SourceLink key={i} s={s} />)}</div>}
        </div>
      </div>
    </li>
  );
}

/** 읽기 단계의 사실 확인 카드(11.7·시안). 중단·주의·건너뜀·실패·다시 확인 필요·확인 중을 보여 준다. */
export default function FactCheckCard({ job, fc, onChanged }: { job: JobState; fc: FactcheckView; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const off = busy || isArchived(job) || !!fc.running;   // 단추를 모두 끄는 조건
  const readWaiting = job.steps.find((s) => s.key === "read")?.status === "waiting";
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); await onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const recheck = (force: boolean) => act(() => api.factcheck(job.id, force));
  // 직접 확인: 메모를 남기고, 읽기 단계가 기다리는 중이면 바로 넘어간다(시안의 "적고 계속하기").
  const confirmManually = () => act(async () => {
    await api.factcheckOverride(job.id, note.trim());
    if (readWaiting) await api.next(job.id);
    setNote("");
  });

  const wrap = (children: ReactNode) => (
    <Card className="rise p-6">
      {children}
      {err && <p className="mt-3 text-[13px] text-bad-text">{err}</p>}
    </Card>
  );
  const head = (chip: ReactNode) => <div className="flex flex-wrap items-center gap-2"><Label>사실 확인</Label>{chip}</div>;
  // 직접 확인은 인라인 메모 칸이다(모달 아님). 넘긴 뒤에는 메모를 보여 준다.
  const manual = fc.override
    ? <div className="mt-5 rounded-[12px] bg-fill p-4"><div className="text-[13px] font-medium">직접 확인으로 넘겼습니다</div><p className="mt-1 whitespace-pre-wrap text-[15px] leading-relaxed">{fc.override.note}</p></div>
    : (
      <div className="mt-5 rounded-[12px] bg-fill p-4">
        <div className="text-[13px] font-medium">직접 확인했음</div>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} disabled={off} aria-label="직접 확인한 내용"
          placeholder="무엇을 어떻게 확인했는지 적어 주세요 (필수)"
          className="mt-2 h-20 w-full resize-none rounded-[10px] border border-line-strong bg-surface px-3 py-2 text-[15px] outline-none focus:border-accent disabled:opacity-60" />
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="primary" disabled={off || !note.trim()} onClick={confirmManually}>적고 계속하기</Button>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => setNote("")}>취소</Button>
        </div>
      </div>
    );
  const meta = [fc.model && (MODEL_NAME[fc.model] ?? fc.model), fc.search && "웹 검색", fc.cost_usd != null && cost(fc.cost_usd), fc.checked_at && timeOf(fc.checked_at)].filter(Boolean).join(" · ");
  const metaRow = (
    <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
      <span className="text-[13px] text-sub">{meta}</span>
      <Button size="sm" variant="secondary" className="ml-auto" disabled={off} onClick={() => recheck(false)}>다시 확인</Button>
    </div>
  );

  if (fc.running) return wrap(
    <>
      {head(<Chip tone="accent">확인 중</Chip>)}
      <p className="mt-2 text-[15px] text-sub">기사를 찾아 주장을 확인하고 있습니다.</p>
      <div className="mt-5 flex border-t border-line pt-4"><Button size="sm" variant="secondary" className="ml-auto" disabled>다시 확인</Button></div>
    </>);
  if (fc.stale || fc.status === "missing") return wrap(
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><Label>사실 확인</Label><h3 className={H3}>글이 바뀌어 다시 확인해야 합니다</h3></div>
      <Button variant="primary" disabled={off} onClick={() => recheck(false)}>다시 확인</Button>
    </div>);
  if (fc.status === "skipped") return wrap(
    <div className="flex flex-wrap items-center gap-3">
      <Label>사실 확인</Label>
      <Chip tone="mute">건너뜀</Chip>
      <span className="text-[15px] text-sub">일반 글{fc.reason ? `(${fc.reason})` : ""}이라 확인하지 않았습니다.</span>
      <Button size="sm" variant="secondary" className="ml-auto" disabled={off} onClick={() => recheck(true)}>그래도 확인하기</Button>
    </div>);
  if (fc.status === "failed") return wrap(
    <>
      {head(<Chip tone="bad">실패</Chip>)}
      <h3 className={H3}>사실 확인을 끝내지 못했습니다</h3>
      <p className="mt-1 text-[15px] text-bad-text">{fc.error ?? "사실 확인에 실패했습니다. 다시 확인해 주세요."}</p>
      {manual}
      {metaRow}
    </>);
  const top = TOP[fc.status as "stop" | "warn" | "pass"];
  return wrap(
    <>
      {head(top.chip)}
      <h3 className={H3}>{top.title}</h3>
      <p className="mt-1 text-[15px] text-sub">{top.sub}</p>
      <ul className="mt-5 divide-y divide-line border-t border-line pt-4">{(fc.claims ?? []).map((c) => <ClaimRow key={c.id} c={c} />)}</ul>
      {fc.status === "stop" && manual}
      {metaRow}
    </>);
}
