import { ChevronDown, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { api, createJobAsking, type TopicSource } from "../api";
import { go, useLoad } from "../hooks";
import { fmtWhen } from "../lib";
import { Button, Chip, ICON, Segmented, cx, fmtNum } from "../ui";
import type { BestResponse, BestSort, BestTopic, Channel, Made, TopicFcEstimate, TopicFcStatus } from "../types";

const HOURS = [12, 24, 48];
const withDot = (s: string) => (/[.!?]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);   // 서버 안내 뒤에 문장을 이어 붙일 때
const ago = (h: number) => (h < 1 ? `${Math.max(1, Math.round(h * 60))}분 전` : h < 24 ? `${Math.round(h)}시간 전` : `${Math.round(h / 24)}일 전`);

/** 같은 글(또는 같은 원본)로 이미 만들었다 — 'similar'(비슷한 이야기)는 해당하지 않는다. */
export const isSameMade = (m?: Made | null) => m?.kind === "same_post" || m?.kind === "same_original";
/** 만든 적 있음 / 비슷한 이야기 칩의 마우스 설명: 우리 영상 제목과 날짜. */
export const madeTip = (m: Made) => m.kind === "similar"
  ? `제목이 비슷한 이야기를 이미 만들었습니다 (겹침 ${Math.round(m.score * 100)}%) — ${fmtWhen(m.created_at)} · ${m.our_title}`
  : `이미 만든 글입니다${m.count > 1 ? ` (${m.count}번)` : ""} — ${fmtWhen(m.created_at)} · ${m.our_title}${m.finished ? "" : " (영상까지는 안 끝남)"}`;
export function MadeChip({ m }: { m?: Made | null }) {
  if (!m) return null;
  return isSameMade(m)
    ? <Chip tone="strong" className="cursor-help" title={madeTip(m)}>만든 적 있음</Chip>
    : <Chip tone="outline" className="cursor-help" title={madeTip(m)}>비슷한 이야기 만듦</Chip>;
}

const liveChip = (fc?: BestTopic["fc"]) => fc && fc.kind !== "general" && !fc.expired ? fc.chip : null;
const dollars = (n: number) => `$${n.toFixed(2)}`;
const activeRun = (s?: TopicFcStatus | null) => s?.state === "running" || s?.state === "cancelling";
const STOP_NOTES: Record<string, string> = {
  cancelled: "사용자가 판정을 멈췄습니다", run_limit: "이번 실행의 비용 상한에 도달했습니다",
  daily_limit: "소재 사실 확인의 하루 비용 상한에 도달했습니다", request_timeout: "판정 요청 시간이 초과되었습니다",
  http_402: "판정 서비스의 잔액이 부족합니다", http_429: "판정 서비스의 요청 한도에 도달했습니다", quota: "판정 서비스의 잔액·사용 한도가 부족합니다",
  storage_error: "판정 기록 또는 비용 장부를 처리하지 못했습니다",
};
type FcCounts = { bad: number; warn: number; outline: number; ok: number; failed: number };
function countResults(items: TopicFcStatus["results"]): FcCounts {
  const counts = { bad: 0, warn: 0, outline: 0, ok: 0, failed: 0 };
  for (const item of items) {
    const chip = liveChip(item.fc);
    if (chip) counts[item.fc?.status === "failed" ? "failed" : chip.color]++;
  }
  return counts;
}
type FcSession = { estimate: TopicFcEstimate; nos: (string | number)[]; force: boolean; excluded: string[]; status: TopicFcStatus | null;
  counts: FcCounts; view: { sort: BestSort; hours: number; pages: number } };
// 같은 탭에서 목록을 닫거나 출처를 바꿨다가 돌아와도 자신이 시작한 실행만 이어서 조회한다.
const fcSessions: Partial<Record<TopicSource, FcSession>> = {};

function useTopicCheck(source: TopicSource, data: BestResponse | null, setData: (d: BestResponse) => void,
  sort: BestSort, hours: number, pages: number, visible: BestTopic[], listChanging: boolean) {
  const [session, setSessionState] = useState<FcSession | null>(() => fcSessions[source] ?? null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const alive = useRef(true);
  const request = useRef(0);
  const setSession = useCallback((v: FcSession | null) => {
    if (v) fcSessions[source] = v; else delete fcSessions[source];
    if (alive.current) setSessionState(v);
  }, [source]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current++; }; }, []);
  const visibleKey = JSON.stringify(visible.map((t) => [String(t.no), t.title]));
  // 견적 뒤 필터/목록이 바뀌면 화면에 없는 글을 실수로 실행하지 않는다.
  useEffect(() => {
    if (session && !session.status) setSession(null);
    // 새 견적 자체는 무효화하지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleKey, data?.fetched_at, sort, hours, pages, setSession]);
  // 보관 세션은 요약 전용이다. 현재 마운트에서 받은 폴링 결과만 목록에 병합한다.
  const [polledStatus, setPolledStatus] = useState<TopicFcStatus | null>(null);
  const appliedStatus = useRef<TopicFcStatus | null>(null);
  useEffect(() => {
    const status = polledStatus;
    if (!data || data.source !== source || status?.source !== source || status.run_id !== session?.estimate.run_id || appliedStatus.current === status) return;
    // 한 응답은 한 번 적용한다. 이후 수동 새로고침의 최신 판정을 과거 응답으로 덮지 않는다.
    appliedStatus.current = status;
    const results = new Map(status.results.map((r) => [String(r.no), r.fc]));
    let changed = false;
    const items = data.items.map((item) => {
      const fc = results.get(String(item.no));
      if (!fc || item.fc === fc) return item;
      changed = true;
      return { ...item, fc };
    });
    if (changed) setData({ ...data, items, fc_counts: {
      news: items.filter((t) => t.fc && t.fc.kind !== "general").length,
      checked: items.filter((t) => t.fc && t.fc.kind !== "general" && t.fc.status && t.fc.status !== "failed" && !t.fc.expired).length,
      doubt: items.filter((t) => liveChip(t.fc)?.color === "bad").length,
    } });
  }, [data, polledStatus, session?.estimate.run_id, source, setData]);

  const running = activeRun(session?.status);
  useEffect(() => {
    if (!session || !running) return;
    let dead = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const status = await api.topicFcStatus();
        if (dead) return;
        if (status.run_id !== session.estimate.run_id || status.source !== source) {
          setMessage(status.state === "idle" ? "서버의 실행 상태가 초기화되었습니다. 목록을 새로 불러와 주세요." : "다른 판정 실행으로 상태가 바뀌었습니다. 목록을 새로 불러와 주세요.");
          const latest = fcSessions[source] ?? session;
          setSession({ ...latest, status: { ...latest.status!, state: "stopped" } });
          return;
        }
        setPollError(null);
        setPolledStatus(status);
        setSession({ ...session, status, counts: countResults(status.results) });
        if (!activeRun(status)) return;
      } catch (e) { if (!dead) setPollError((e as Error).message); }
      if (!dead) timer = window.setTimeout(poll, 2000);
    };
    void poll();
    return () => { dead = true; window.clearTimeout(timer); };
    // 진행 건수 변화로 타이머를 재시작하지 않는다. 실행 식별자/진행 여부만 따라간다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.estimate.run_id, running, source, setData, setSession]);

  async function estimate(force = false, next = false) {
    if (pending || running || listChanging) return;
    const id = ++request.current;
    const excluded = next && session ? [...session.excluded, ...session.estimate.selected_nos.slice(0, session.status?.done ?? 0).map(String)] : [];
    const nos = visible.filter((t) => !excluded.includes(String(t.no))).slice(0, 200).map((t) => t.no);
    setPending(true); setMessage(null); setPollError(null); setSession(null);
    try {
      const result = await api.topicFcEstimate(source, nos, force);
      if (alive.current && id === request.current) setSession({ estimate: result, nos, force, excluded, status: null,
        counts: countResults([]), view: { sort, hours, pages } });
    } catch (e) { if (alive.current && id === request.current) setMessage((e as Error).message); }
    finally { if (alive.current && id === request.current) setPending(false); }
  }
  async function start() {
    if (!session || fcSessions[source] !== session || pending || session.status || listChanging) return;
    setPending(true); setMessage(null);
    try {
      const result = await api.topicFcRun(session.estimate.run_id);
      setSession({ ...session, status: { run_id: result.run_id, source, state: "running", done: 0, total: result.total, cost_usd: 0, stop_reason: null, error: null, results: [] } });
    } catch (e) {
      if (alive.current) {
        setMessage((e as Error).message);
        if ((e as Error).message === "목록이 바뀌어 다시 눌러 주세요") setSession(null);
      }
    }
    finally { if (alive.current) setPending(false); }
  }
  async function cancel() {
    if (!session || pending || !running) return;
    setPending(true); setMessage(null);
    try {
      const status = await api.topicFcCancel(session.estimate.run_id);
      if (activeRun(status) && status.run_id === session.estimate.run_id && status.source === source) setSession({ ...session, status, counts: countResults(status.results) });
    } catch (e) { if (alive.current) setMessage((e as Error).message); }
    finally { if (alive.current) setPending(false); }
  }
  const invalidateEstimate = useCallback(() => {
    if (!fcSessions[source]?.status) {
      request.current++;
      setPending(false);
      setSession(null);
    }
  }, [source, setSession]);
  return { session, pending, running, message, pollError, estimate, start, cancel, invalidateEstimate, dismiss: () => { setSession(null); setMessage(null); } };
}

function Card({ t, busy, onMake, muted, topic }: { t: BestTopic; busy: boolean; onMake: () => void; muted?: boolean; topic?: string }) {
  const dup = isSameMade(t.made);
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const chip = liveChip(t.fc);
  const alwaysWhy = chip?.color === "bad" || chip?.color === "warn";
  const reasonTone = chip?.color === "bad" ? "text-bad-text" : chip?.color === "warn" ? "text-warn-text" : "text-sub";
  return (
    <div className="flex min-w-0 flex-col rounded-[14px] border border-line bg-surface p-4 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
      <div className="flex flex-wrap items-center gap-1.5">
        {chip && <>
          <button type="button" className="rounded-lg whitespace-nowrap" aria-expanded={expanded} aria-controls={detailId}
            aria-label={`${chip.label}, 판정 이유와 출처 ${expanded ? "접기" : "펼치기"}`} onClick={() => setExpanded(!expanded)}>
            <Chip tone={chip.color}>{chip.label}</Chip>
          </button>
          <span className="text-[11px] text-sub whitespace-nowrap">제목 기준</span>
        </>}
        <MadeChip m={t.made} />
        {t.fit === 2 && <Chip tone="accent"><span title={`채널 대주제 '${topic}'에 딱 맞는 글`}>딱 맞음</span></Chip>}
        {t.fit === 1 && <Chip><span title={`채널 대주제 '${topic}'에 조금 맞는 글`}>조금</span></Chip>}
        {t.tag && <Chip>{t.tag}</Chip>}
        {t.category && <Chip tone="mute">{t.category.label}</Chip>}
        {t.source_check && <Chip tone="warn" className="cursor-help"><span title={t.source_check_reason ?? "원본 출처를 확인해야 하는 글"}>출처 확인 필요</span></Chip>}
      </div>
      <div className={cx("mt-2.5 line-clamp-2 min-h-[2.9em] text-[15px] font-semibold leading-snug", (muted || t.fit === 0 || dup) && "text-sub")}>{t.title}</div>
      <div className="mt-2 text-[13px] text-sub">조회 {fmtNum(t.views)} · 댓글 {fmtNum(t.comments)} · {ago(t.age_hours)}</div>
      {chip && <div id={detailId}>
        {(alwaysWhy || expanded) && t.fc?.why && <div className={cx("mt-1 text-[13px] break-words", !expanded && "line-clamp-1", reasonTone)}>{t.fc.why}</div>}
        {expanded && <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-sub">
          {t.fc?.sources?.length ? t.fc.sources.map((s, i) => /^https?:\/\//i.test(s.url) && (
            <a key={`${s.url}-${i}`} href={s.url} target="_blank" rel="noreferrer" className="break-all underline underline-offset-2 hover:text-ink">{s.outlet || "출처"}{s.date && ` · ${s.date}`}</a>
          )) : <span>확인할 출처가 없습니다.</span>}
        </div>}
      </div>}
      {t.hidden_reason.length > 0 && <div className="mt-1 text-[13px] text-warn-text">주의: {t.hidden_reason.join(", ")}</div>}
      <div className="mt-3 flex items-center justify-between gap-2">
        <a href={t.url} target="_blank" rel="noreferrer" className="text-[13px] text-sub hover:text-ink">원문 보기</a>
        <Button size="sm" variant={muted || dup ? "secondary" : "primary"} disabled={busy} onClick={onMake}>만들기</Button>
      </div>
    </div>
  );
}

export default function BestPanel({ source = "dcbest" }: { source?: TopicSource }) {
  return <BestPanelContent key={source} source={source} />;
}

function BestPanelContent({ source }: { source: TopicSource }) {
  const [sort, setSort] = useState<BestSort>(() => fcSessions[source]?.view.sort ?? "views");
  const [hours, setHours] = useState(() => fcSessions[source]?.view.hours ?? 48);
  const [pages, setPages] = useState(() => fcSessions[source]?.view.pages ?? 3);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [hideUnfit, setHideUnfit] = useState(false);
  const [hideMade, setHideMade] = useState(false);
  const [hideDoubt, setHideDoubt] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [customText, setCustomText] = useState<string | null>(null);      // null = 아직 안 고침(저장된 글을 보여 줌)
  const [applying, setApplying] = useState(false);
  const blind = source === "blind";
  const { data, error, setData } = useLoad<BestResponse>(() => api.topicsBest(sort, hours, pages, source), [sort, hours, pages, source]);
  const ch = useLoad<Channel>(() => api.channel(), []);
  const channel = ch.data;

  const { shown, hidden, fitN, madeN } = useMemo(() => {
    const all = data?.items ?? [];
    const doubtOk = hideDoubt ? all.filter((t) => liveChip(t.fc)?.color !== "bad") : all;
    const unfitOk = hideUnfit ? doubtOk.filter((t) => t.fit !== 0) : doubtOk;      // 순서(딱 맞음 → 조금 → 아님)는 서버가 정해서 보낸다
    const items = hideMade ? unfitOk.filter((t) => !isSameMade(t.made)) : unfitOk;       // 같은 글만 숨김 — 비슷한 이야기는 표시만
    return { shown: items.filter((t) => !t.hidden), hidden: items.filter((t) => t.hidden),
      fitN: { 2: all.filter((t) => t.fit === 2).length, 1: all.filter((t) => t.fit === 1).length, 0: all.filter((t) => t.fit === 0).length },
      madeN: { same: all.filter((t) => isSameMade(t.made)).length, similar: all.filter((t) => t.made?.kind === "similar").length } };
  }, [data, hideUnfit, hideMade, hideDoubt]);

  const visible = open ? [...shown, ...hidden] : shown;
  const fc = useTopicCheck(source, data, setData, sort, hours, pages, visible, refreshing || applying);
  const fcLocked = fc.pending || fc.running;
  const listReady = data?.sort === sort && data.hours === hours && (blind || data.pages === pages);
  const estimate = fc.session?.estimate;
  const status = fc.session?.status;
  const summary = fc.session?.counts ?? { bad: 0, warn: 0, outline: 0, ok: 0, failed: 0 };

  const refresh = useCallback(async () => {
    fc.invalidateEstimate();
    setRefreshing(true); setErr(null);
    try { setData(await api.refreshBest(sort, hours, pages, source)); } catch (e) { setErr((e as Error).message); }
    setRefreshing(false);
  }, [sort, hours, pages, source, setData, fc.invalidateEstimate]);

  /** 채널 대주제를 바꾸고(저장) 목록을 다시 받는다 — 서버가 새 대주제로 점수를 매겨 순서를 바꿔 보낸다. */
  async function applyChannel(body: { main_topic: string | null; custom?: string | null }) {
    fc.invalidateEstimate();
    setApplying(true); setErr(null);
    try { ch.setData(await api.setChannel(body)); setCustomOpen(false); setData(await api.topicsBest(sort, hours, pages, source)); }
    catch (e) { setErr((e as Error).message); }
    setApplying(false);
  }
  function pickTopic(v: string) {
    if (v !== "custom") { setCustomOpen(false); applyChannel({ main_topic: v === "none" ? null : v }); return; }
    if (channel?.custom) applyChannel({ main_topic: "custom" }); else setCustomOpen(true);
  }
  const selValue = customOpen ? "custom" : (channel?.main_topic ?? "none");
  const customVal = customText ?? channel?.custom ?? "";

  async function make(t: BestTopic) {
    setBusy(true); setErr(null);
    try {
      const r = await createJobAsking({ url: t.url, title: (t.tag ? `[${t.tag}] ` : "") + t.title });      // 이미 만든 글이면 한 번 묻는다
      if (r) go(`/job/${encodeURIComponent(r.id)}`); else setBusy(false);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-6 py-3">
        <Segmented value={sort} onChange={(v) => { if (!fcLocked) { fc.invalidateEstimate(); setSort(v); } }} options={[{ value: "views", label: "조회순" }, { value: "comments", label: "댓글순" }, { value: "recent", label: "최신순" }]} />
        <label className="flex items-center gap-2 text-[13px] text-sub">기간
          <select disabled={fcLocked} value={hours} onChange={(e) => { fc.invalidateEstimate(); setHours(Number(e.target.value)); }} className="h-8 rounded-lg border border-line bg-fill px-2 text-[13px] text-ink">
            {HOURS.map((h) => <option key={h} value={h}>{h}시간</option>)}
          </select>
        </label>
        {!blind && (
          <label className="flex items-center gap-2 text-[13px] text-sub">쪽 수
            <select disabled={fcLocked} value={pages} onChange={(e) => { fc.invalidateEstimate(); setPages(Number(e.target.value)); }} className="h-8 rounded-lg border border-line bg-fill px-2 text-[13px] text-ink">
              {[1, 2, 3, 4, 5].map((p) => <option key={p} value={p}>{p}쪽</option>)}
            </select>
          </label>
        )}
        <Button size="sm" className="ml-auto" disabled={!listReady || !data?.fc_ready || fcLocked || refreshing || applying || !visible.length}
          title={!data?.fc_ready ? data?.fc_note ?? undefined : undefined} onClick={() => fc.estimate()} icon={<ShieldCheck size={15} {...ICON} />}>
          {fc.running ? "판정 중" : "가짜뉴스 체크"}
        </Button>
        <Button size="sm" disabled={refreshing || fcLocked} onClick={refresh} icon={<RefreshCw size={15} {...ICON} className={cx(refreshing && "animate-spin")} />}>{refreshing ? "불러오는 중" : "새로 불러오기"}</Button>
      </div>
      {(channel || madeN.same > 0 || (data?.fc_counts.doubt ?? 0) > 0) && (
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-6 py-2.5">
          {channel && <label className="flex items-center gap-2 text-[13px] text-sub">채널 대주제
            <select value={selValue} disabled={applying || fcLocked} onChange={(e) => pickTopic(e.target.value)} className="h-8 rounded-lg border border-line bg-fill px-2 text-[13px] text-ink">
              <option value="none">없음</option>
              {channel.presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              <option value="custom">직접 입력</option>
            </select>
          </label>}
          {channel && selValue === "custom" && (
            <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (customVal.trim()) applyChannel({ main_topic: "custom", custom: customVal }); }}>
              <input value={customVal} onChange={(e) => setCustomText(e.target.value)} maxLength={200} placeholder="예: 게임 업계 소식과 논란" aria-label="직접 입력한 채널 대주제"
                className="h-8 w-56 rounded-lg border border-line bg-fill px-2 text-[13px] text-ink outline-none placeholder:text-sub/70 focus-visible:border-accent" />
              <Button size="sm" type="submit" disabled={applying || fcLocked || !customVal.trim()}>적용</Button>
            </form>
          )}
          {applying && <span className="text-[13px] text-sub">맞춤 점수를 매기는 중</span>}
          {!applying && data?.channel && data.fit !== "unavailable" && (
            <span className="text-[13px] text-sub">딱 맞음 {fitN[2]} · 조금 {fitN[1]} · 아님 {fitN[0]}</span>
          )}
          <div className="ml-auto flex flex-wrap items-center gap-4">
            {(data?.fc_counts.doubt ?? 0) > 0 && <label className="flex items-center gap-2 text-[13px] text-sub">
              <input type="checkbox" checked={hideDoubt} disabled={fc.pending} onChange={(e) => setHideDoubt(e.target.checked)} />가짜 의심 숨기기 ({data?.fc_counts.doubt})
            </label>}
            {data?.channel && data.fit !== "unavailable" && (
              <label className="flex items-center gap-2 text-[13px] text-sub">
                <input type="checkbox" disabled={fc.pending} checked={hideUnfit} onChange={(e) => setHideUnfit(e.target.checked)} />안 맞는 글 숨기기{fitN[0] > 0 && ` (${fitN[0]})`}
              </label>
            )}
            {madeN.same > 0 && (
              <label className="flex items-center gap-2 text-[13px] text-sub" title="이미 만든 글(같은 글)만 숨깁니다. 비슷한 이야기는 표시만 하고 남겨 둡니다.">
                <input type="checkbox" disabled={fc.pending} checked={hideMade} onChange={(e) => setHideMade(e.target.checked)} />만든 글 숨기기 ({madeN.same})
              </label>
            )}
          </div>
        </div>
      )}
      <div className="scroll-thin min-h-[320px] flex-1 overflow-y-auto px-6 py-4">
        {(data?.fc_ready === false || fc.pending || fc.session || fc.message || fc.pollError) && (
          <div className="mb-3 flex flex-wrap items-center gap-3 rounded-[14px] border border-line bg-fill px-4 py-3 text-[13px] tabular-nums" aria-label="가짜뉴스 체크 상태" aria-busy={fc.pending}>
            <div className="contents" role="status" aria-live="polite">
              {data?.fc_ready === false && <span className="w-full text-sub">{data.fc_note}</span>}
              {fc.pending && !status && <span>{estimate ? "판정을 시작하는 중입니다." : "시사 글과 판정 비용을 확인하는 중입니다."}</span>}
              {!fc.pending && estimate && !status && <>
                <span className="text-ink">
                  {estimate.news === 0 ? "시사로 보이는 글이 없습니다." : estimate.todo === 0
                    ? `시사 ${estimate.news}건 · 이미 확인한 ${estimate.done}건 — 새로 판정할 글이 없습니다.`
                    : <><b className="font-semibold">시사 {estimate.news}건</b> · 이미 확인한 {estimate.done}건 빼고 {estimate.todo}건 판정 · 약 {dollars(estimate.est_usd)}</>}
                  {estimate.capped && " · 한 번에 30건까지 판정합니다"}
                </span>
                {estimate.news > 0 && <span className="text-sub">제목만 보고 판정합니다. 고른 글은 만들 때 본문까지 다시 확인합니다.</span>}
                {estimate.classification_usd > 0 && <span className="text-sub">분류에 쓴 비용 {dollars(estimate.classification_usd)} 별도</span>}
                {visible.length > 200 && <span className="text-sub">보이는 글 중 앞 200건을 확인합니다.</span>}
              </>}
              {status && fc.running && <>
                <span className="text-ink"><b className="font-semibold">{status.state === "cancelling" ? "멈추는 중" : "판정 중"} {status.done}/{status.total}</b> · {dollars(status.cost_usd)}</span>
                <div className="h-1 min-w-12 flex-1 overflow-hidden rounded-full bg-surface" role="progressbar" aria-label="제목 판정 진행" aria-valuenow={status.done} aria-valuemin={0} aria-valuemax={status.total || 1}>
                  <div className="bar h-full rounded-full bg-accent" style={{ width: `${status.total ? status.done / status.total * 100 : 0}%` }} />
                </div>
                {status.state === "cancelling" && <span className="text-sub">지금 판정 중인 글까지 마친 뒤 멈춥니다.</span>}
              </>}
              {status && !fc.running && <>
                <span className="text-ink">
                  <b className="font-semibold">{status.state === "done" ? "제목 판정 끝" : status.state === "failed" ? "제목 판정 오류" : "제목 판정 멈춤"}</b> · 시사 {estimate?.news ?? 0}건 중 {status.done}건 판정 — {" "}
                  <span className="text-bad-text">가짜 의심 {summary.bad}</span> · <span className="text-warn-text">주의 {summary.warn}</span> · {" "}
                  <span className="text-sub">확인 안 됨 {summary.outline}</span> · <span className="text-ok-text">보도 확인 {summary.ok}</span>
                  {summary.failed > 0 && <span className="text-sub"> · 확인 실패 {summary.failed}</span>} · {dollars(status.cost_usd)}
                </span>
                {status.stop_reason && <span className="w-full text-warn-text">{STOP_NOTES[status.stop_reason] ?? status.stop_reason}</span>}
                {estimate && estimate.classification_usd > 0 && <span className="text-sub">분류에 쓴 비용 {dollars(estimate.classification_usd)} 별도</span>}
              </>}
              {status?.error && <span className="w-full break-words text-bad-text">{status.error}</span>}
              {fc.message && <span className="w-full break-words text-bad-text">{fc.message}</span>}
              {fc.pollError && <span className="w-full break-words text-bad-text">{fc.pollError}</span>}
            </div>
            {estimate && !status && !fc.pending && <span className="ml-auto flex flex-wrap items-center gap-2">
              {estimate.todo > 0 && <Button size="sm" variant="primary" disabled={!listReady || !data?.fc_ready || refreshing || applying} onClick={fc.start}>검사 시작</Button>}
              <Button size="sm" variant="ghost" onClick={fc.dismiss}>취소</Button>
            </span>}
            {fc.running && <Button size="sm" variant="ghost" className="ml-auto" disabled={fc.pending || status?.state === "cancelling"} onClick={fc.cancel}>멈추기</Button>}
            {status && !fc.running && <span className="ml-auto flex flex-wrap items-center gap-2">
              {estimate?.capped && <Button size="sm" variant="ghost" disabled={fc.pending || refreshing || applying || !listReady || !data?.fc_ready || !visible.length} onClick={() => fc.estimate(fc.session?.force, true)}>다음 30건</Button>}
              <Button size="sm" variant="ghost" disabled={fc.pending || refreshing || applying || !listReady || !data?.fc_ready || !visible.length} onClick={() => fc.estimate(true)}>다시 판정</Button>
            </span>}
          </div>
        )}
        {data && (data.fit === "unavailable" || data.fit === "partial") && <div className="mb-3 text-[13px] text-warn-text">{data.fit_note}</div>}
        {data && data.triage === "skipped" && <div className="mb-3 text-[13px] text-sub">{data.triage_note}</div>}
        {data && data.triage !== "ok" && data.triage !== "skipped" && <div className="mb-3 text-[13px] text-warn-text">{withDot(data.triage_note || "Clef 분류를 쓸 수 없어 분야·위험 표시 없이 목록만 보여 줍니다.")} 제목을 직접 읽고 골라 주세요.</div>}
        {!data && !error && <div className="py-10 text-[15px] text-sub">{blind
          ? "블라인드 인기글을 불러오는 중입니다. 블라인드에 부담을 주지 않도록 3초 쉬었다 받습니다."
          : "인기글을 불러오는 중입니다. 디시에 부담을 주지 않도록 쪽마다 3초씩 쉬어서 처음에는 10초쯤 걸립니다."}{channel?.main_topic && " 채널 대주제에 맞는지 제목마다 점수도 매기느라 몇 초 더 걸릴 수 있습니다."}</div>}
        {error && <div className="py-10 text-[15px] text-bad-text">목록을 불러오지 못했습니다. {error}</div>}
        {data && shown.length === 0 && hidden.length === 0 && (
          <div className="py-10 text-[15px] text-sub">{hideDoubt && data.items.length > 0 ? "가짜 의심 글을 빼고 나니 남은 글이 없습니다. 숨기기를 끄면 다시 볼 수 있습니다." : hideUnfit && data.items.length > 0 ? "채널 대주제에 맞는 글이 없습니다. '안 맞는 글 숨기기'를 끄거나 기간·쪽 수를 늘려 보세요." : hideMade && data.items.length > 0 ? "이미 만든 글을 빼고 나니 남은 글이 없습니다. '만든 글 숨기기'를 끄거나 기간·쪽 수를 늘려 보세요." : blind ? "이 기간에 올라온 블라인드 인기글이 없습니다. 기간을 늘려 보세요." : "이 기간에 올라온 글이 없습니다. 기간이나 쪽 수를 늘려 보세요."}</div>
        )}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {shown.map((t) => <Card key={t.no} t={t} busy={busy} topic={data?.channel?.label} onMake={() => make(t)} />)}
        </div>
        {hidden.length > 0 && (
          <div className="mt-4 border-t border-line pt-2">
            <button disabled={fc.pending} onClick={() => setOpen(!open)} className="press flex w-full items-center gap-1.5 rounded-lg px-1 py-2 text-left text-[13px] font-medium text-sub hover:text-ink">
              <ChevronDown size={16} {...ICON} className={cx("transition-transform duration-200", !open && "-rotate-90")} />숨김 {hidden.length}개
              <span className="font-normal">— 특정인 비난, 성적 대상화, 확인 안 된 의혹 같은 위험 표시가 붙은 글</span>
            </button>
            {open && <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">{hidden.map((t) => <Card key={t.no} t={t} busy={busy} muted topic={data?.channel?.label} onMake={() => make(t)} />)}</div>}
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-line px-6 py-3 text-[13px] text-sub">
        <span>만들기를 누르면 이 글의 주소로 바로 제작을 시작합니다. {blind
          ? "블라인드 글은 Clef 분류 없이 제목·조회수만 보여 줍니다. 채널 대주제 맞춤 점수는 Gemini가 제목만 보고 매긴 참고 값입니다."
          : "분야·위험 표시는 Clef가, 대주제 맞춤 점수는 Gemini가 제목만 보고 매긴 참고 값입니다."} '만든 적 있음'·'비슷한 이야기 만듦'은 우리가 만든 작업(드라이브 보관 포함)과 글 주소·제목만 견준 표시이고, 다른 채널이 이미 올렸는지는 알 수 없습니다.</span>
        {err && <span className="text-bad-text">{err}</span>}
      </div>
    </>
  );
}
