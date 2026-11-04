import { Settings as SettingsIcon } from "lucide-react";
import { useEffect } from "react";
import { api, MOCK } from "./api";
import { go, useLoad, useRoute } from "./hooks";
import { Button, ICON, cx } from "./ui";
import Home from "./pages/Home";
import Job from "./pages/Job";
import ScriptEdit from "./pages/ScriptEdit";
import SceneEditor from "./pages/SceneEditor";
import Review from "./pages/Review";
import Settings from "./pages/Settings";
import Library from "./pages/Library";

const HEALTH: [keyof import("./types").Health, string][] = [
  ["gemini_key", "AI 키"], ["cf_keys", "Clef"], ["image_server", "AI 그림"], ["ffmpeg", "ffmpeg"],
];

function TopBar() {
  const { data, reload } = useLoad(() => api.health(), [], 30000);
  // 설정에서 키를 저장하면 30초 주기를 기다리지 않고 바로 다시 읽는다.
  useEffect(() => {
    const f = () => { void reload(); };
    window.addEventListener("recap:health", f);
    return () => window.removeEventListener("recap:health", f);
  }, [reload]);
  const healthTitle = data ? HEALTH.map(([k, label]) => { const h = data[k]; return h ? `${label}: ${h.detail ?? (h.ok ? "정상" : "꺼짐")}` : null; }).filter(Boolean).join("\n") : "";
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/90 backdrop-blur-sm">
      <div className="mx-auto flex h-14 max-w-[1280px] items-center justify-between gap-3 px-4 sm:gap-4 sm:px-6">
        <a href="#/" className="shrink-0 whitespace-nowrap text-[15px] font-semibold tracking-[-0.01em] text-ink no-underline">커뮤니티 쇼츠 자동 제작</a>
        <div className="flex min-w-0 items-center gap-2 sm:gap-4">
          {MOCK && <span className="hidden text-[11px] font-medium text-sub sm:inline">예시 데이터</span>}
          <a href="#/settings" title={healthTitle || undefined} aria-label="연결 상태 — 설정 열기" className="scroll-thin inline-flex min-w-0 items-center gap-3 overflow-x-auto rounded-lg px-1 py-1 no-underline hover:bg-fill sm:gap-4">
            {data && HEALTH.map(([k, label]) => {
              const h = data[k] ?? { ok: false };
              return (
                <span key={k} title={h.detail ?? (h.ok ? "정상" : "꺼짐")} className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-medium text-sub">
                  <span className={cx("h-1.5 w-1.5 rounded-full", h.ok ? "bg-ok" : "bg-warn")} />{label}{!h.ok && " 꺼짐"}
                </span>
              );
            })}
          </a>
          <Button variant="secondary" size="sm" icon={<SettingsIcon size={15} {...ICON} />} onClick={() => go("/settings")} className="shrink-0">설정</Button>
        </div>
      </div>
    </header>
  );
}

export default function App() {
  const route = useRoute();
  const m = route.match(/^\/job\/([^/]+)(?:\/(script|scenes|review))?$/);
  const lib = route.match(/^\/library\/(sfx|meme)\/([^/]+)$/);
  let page;
  if (lib) page = <Library kind={lib[1] as "sfx" | "meme"} id={decodeURIComponent(lib[2])} />;
  else if (route === "/settings") page = <Settings />;
  else if (m) {
    const id = decodeURIComponent(m[1]);
    page = m[2] === "script" ? <ScriptEdit id={id} /> : m[2] === "scenes" ? <SceneEditor id={id} /> : m[2] === "review" ? <Review id={id} /> : <Job id={id} />;
  } else page = <Home sheet={route === "/find"} />;
  return (
    <div className="min-h-screen">
      <TopBar />
      <main key={route === "/settings" || m || lib ? route : "home"} className="mx-auto max-w-[1280px] px-4 pb-16 pt-8 sm:px-6">{page}</main>
    </div>
  );
}
