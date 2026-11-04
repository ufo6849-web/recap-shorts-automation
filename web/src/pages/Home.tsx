import { Cloud, KeyRound, Search } from "lucide-react";
import { useState } from "react";
import { api, createJobAsking, mediaUrl } from "../api";
import { ApiError } from "../apiError";
import { jobTone } from "../components/JobHeader";
import { go, useLoad } from "../hooks";
import { isArchived } from "../lib";
import { Button, Card, ICON, Progress, SectionLabel, Status, fmtAgo } from "../ui";
import type { JobState } from "../types";
import TopicSheet from "./TopicSheet";

function JobCard({ job }: { job: JobState }) {
  const st = jobTone(job.status);
  const cur = job.steps.find((s) => s.status === "running") ?? job.steps.find((s) => s.status === "waiting");
  const doneN = job.steps.filter((s) => s.status === "done").length;
  const thumb = job.thumbnail ?? job.artifacts.images?.[0];
  return (
    <a href={`#/job/${encodeURIComponent(job.id)}`} className="group block no-underline">
      <Card className="rise overflow-hidden transition-colors duration-200 group-hover:border-line-strong">
        <div className="aspect-[4/3] w-full overflow-hidden bg-fill">
          {thumb ? <img src={mediaUrl(job.id, thumb)} alt="" loading="lazy" className="h-full w-full object-cover" /> : <div className="flex h-full items-center justify-center text-[13px] text-sub">그림 준비 전</div>}
        </div>
        <div className="p-4">
          <div className="line-clamp-2 min-h-[2.9em] text-[15px] font-semibold leading-snug text-ink">{job.title}</div>
          <div className="mt-3 flex items-center justify-between gap-2">
            <span className="flex items-center gap-2">
              <Status tone={st.tone} pulse={st.pulse}>{st.label}</Status>
              {isArchived(job) && <span className="inline-flex items-center gap-1 rounded-full bg-fill px-2 py-0.5 text-[12px] font-medium text-sub"><Cloud size={12} {...ICON} />구글 드라이브{job.offline ? " · 꺼짐" : ""}</span>}
            </span>
            <span className="text-[13px] text-sub">{fmtAgo(job.created_at)}</span>
          </div>
          {job.status !== "done" && (
            <div className="mt-3">
              <Progress value={job.progress ?? (doneN + (cur?.progress ?? 0)) / job.steps.length} />
              <div className="mt-1.5 text-[13px] text-sub">{cur ? `${cur.label} · ${doneN + 1} / ${job.steps.length}단계` : ""}</div>
            </div>
          )}
        </div>
      </Card>
    </a>
  );
}

/** 키가 하나도 없을 때의 이유(서버 문구와 같다). 키는 있는데 준비가 안 됐으면 그 이유를 카드에 보인다. */
const NEED_KEY = "Gemini 또는 OpenRouter API 키가 필요합니다";

export default function Home({ sheet }: { sheet: boolean }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const jobs = useLoad(() => api.jobs(), [], 4000);
  const health = useLoad(() => api.health(), [], 30000);
  const noKey = health.data?.gemini_key.ok === false;
  const badKey = !!health.data?.gemini_key.bad;
  const reason = health.data?.gemini_key.detail;

  const valid = /^https?:\/\/\S+/.test(url.trim());
  async function start() {
    if (!valid || busy || noKey) return;
    setBusy(true); setErr(null);
    try {
      const r = await createJobAsking({ url: url.trim() });      // 이미 만든 글이면 한 번 묻는다
      if (r) go(`/job/${encodeURIComponent(r.id)}`); else setBusy(false);
    } catch (e) {
      if (e instanceof ApiError && e.code === "no_key") { health.reload(); }   // 키가 없는 채로 눌렀을 때 — 안내로 바꾼다
      setErr((e as Error).message); setBusy(false);
    }
  }

  return (
    <div>
      <section className="max-w-[760px] pt-4">
        <h1 className="text-[34px] font-semibold leading-[1.2] tracking-[-0.02em]">커뮤니티 쇼츠 자동 제작</h1>
        <p className="mt-2 text-[15px] text-sub">커뮤 글 주소를 넣으면 대본, 목소리, 자료 화면, 영상까지 만들고 중간에 확인을 받습니다.</p>
        {noKey && (
          <Card className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-3 border-line-strong p-4 sm:flex-nowrap">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-fill text-accent"><KeyRound size={18} {...ICON} /></span>
            <div className="min-w-0 flex-1">
              <div className="text-[15px] font-semibold">{badKey ? "AI 키가 맞지 않습니다." : "영상을 만들려면 Gemini 또는 OpenRouter API 키가 필요합니다."}</div>
              <div className="mt-0.5 text-[13px] text-sub">{badKey
                ? "연결 확인에서 키가 틀렸다고 나왔습니다. 맞는 키를 넣기 전에는 만들기가 꺼져 있습니다."
                : reason && reason !== NEED_KEY ? reason
                : "키를 넣기 전에는 만들기가 꺼져 있습니다. 키 하나만 넣으면 영상을 만들 수 있습니다."}</div>
            </div>
            <Button variant="primary" onClick={() => go("/settings")} className="w-full shrink-0 sm:w-auto">{badKey ? "설정에서 키 확인" : "설정에서 키 넣기"}</Button>
          </Card>
        )}
        <form className="mt-6 flex flex-col gap-2 rounded-[14px] border border-line bg-surface p-2 shadow-[0_1px_2px_rgba(0,0,0,0.04)] sm:flex-row sm:items-center" onSubmit={(e) => { e.preventDefault(); start(); }}>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="디시인사이드·블라인드 글 주소" aria-label="커뮤 글 주소"
            className="h-11 min-w-0 flex-1 rounded-[10px] bg-transparent px-3 text-[15px] text-ink outline-none placeholder:text-sub/70 focus-visible:outline-none" />
          <div className="flex gap-2">
            <Button type="button" variant="secondary" size="lg" icon={<Search size={17} {...ICON} />} onClick={() => go("/find")} className="flex-1 sm:flex-none">인기글에서 찾기</Button>
            <Button type="submit" variant="primary" size="lg" disabled={!valid || busy || noKey} title={noKey ? (badKey ? "AI 키를 확인하세요" : reason || NEED_KEY) : undefined} className="flex-1 sm:flex-none">{busy ? "시작하는 중" : "만들기"}</Button>
          </div>
        </form>
        {err && <div className="mt-2 text-[13px] text-bad-text">{err}</div>}
      </section>

      <section className="mt-12">
        <SectionLabel>최근 작업</SectionLabel>
        {jobs.data && jobs.data.length === 0 && (
          <Card className="px-6 py-10 text-[15px] text-sub">아직 만든 영상이 없습니다. 위에 글 주소를 넣거나, 인기글에서 골라 시작해 보세요.</Card>
        )}
        {!jobs.data && !jobs.error && <div className="text-[13px] text-sub">불러오는 중</div>}
        {jobs.error && <Card className="px-6 py-5 text-[15px] text-bad-text">작업 목록을 불러오지 못했습니다. {jobs.error}</Card>}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {jobs.data?.map((j) => <JobCard key={j.id} job={j} />)}
        </div>
      </section>
      {sheet && <TopicSheet onClose={() => go("/")} />}
    </div>
  );
}
