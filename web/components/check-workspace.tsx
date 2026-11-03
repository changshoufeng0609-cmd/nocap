"use client";

import { CirclePauseIcon, FileImageIcon, FileTextIcon, MessageSquareTextIcon, ShieldCheckIcon, ZapIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { ReceiptsApp } from "@/components/receipts-app";
import { SlideApp } from "@/components/slide-app";
import { AskWorkspace } from "@/components/ask-workspace";
import { PrivateCheck } from "@/components/private-check";
import { cn } from "@/lib/utils";
import { fetchHealth } from "@/lib/api";
import { formatUtcMinutes } from "@/lib/format";
import type { Health } from "@/lib/types";

const MODES = ["draft", "slide", "ask", "private"] as const;
const LABELS = { draft: "Draft", slide: "Slide", ask: "Ask", private: "Private" };
const ICONS = { draft: FileTextIcon, slide: FileImageIcon, ask: MessageSquareTextIcon, private: ShieldCheckIcon };
// The open-window notice suggests a first step that exists on the current tab. Private also needs the operator's own
// Modal GPU, which the live window alone does not provide, so on Private the notice names only the tabs it opens.
const LIVE_SCOPE = { draft: "checks are", slide: "checks are", ask: "checks are", private: "Draft, Slide and Ask checks are" };
const LIVE_HINT = {
  draft: "Try one: paste an earnings paragraph, or change a number in a saved example, then press Check.",
  slide: "Try one: upload an earnings chart, or replay the real SEC slide at $0.",
  ask: "Try one: ask for one company's figure in one fiscal quarter.",
  private: "Private runs on the operator's own Modal GPU; try its recorded run at $0.",
};

export function CheckWorkspace() {
  const [mode, setMode] = useState<(typeof MODES)[number]>("draft");
  const [livePaused, setLivePaused] = useState(false);
  const [draftCachePolicy, setDraftCachePolicy] = useState<Health["draft_cache_policy"]>();
  // Only a confirmed open window is announced; an unreachable health check shows neither notice.
  const [liveOpen, setLiveOpen] = useState<Pick<Health, "live_until"> | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetchHealth(controller.signal).then((health) => {
      if (!controller.signal.aborted) {
        setLivePaused(health?.live_requests_paused === true);
        setDraftCachePolicy(health?.draft_cache_policy);
        setLiveOpen(health && health.live_requests_paused === false ? { live_until: health.live_until } : null);
      }
    });
    return () => controller.abort();
  }, []);
  return (
    <>
      <div className="mx-auto w-full max-w-6xl px-4 pt-5 sm:px-6">
        {livePaused ? <p role="status" className="mb-4 flex items-start gap-2.5 rounded-lg border border-time-line/35 bg-time-tint/55 px-3.5 py-3 text-sm text-foreground/80"><CirclePauseIcon className="mt-0.5 size-4 shrink-0 text-time" aria-hidden /><span>Live model requests are currently paused. In Draft, choose a built-in example to explore its saved results and SEC receipts. The demo video is also available.</span></p> : null}
        {liveOpen ? <p role="status" className="mb-4 flex items-start gap-2.5 rounded-lg border border-ok-line/35 bg-ok-tint/50 px-3.5 py-3 text-sm text-foreground/80"><ZapIcon className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden /><span>{`Live ${LIVE_SCOPE[mode]} open${liveOpen.live_until ? ` until ${formatUtcMinutes(liveOpen.live_until)}` : ""}. ${LIVE_HINT[mode]}`}</span></p> : null}
        <div className="pb-1"><div role="tablist" aria-label="What to check" className="grid w-full grid-cols-4 gap-1 rounded-xl border border-border bg-muted/50 p-1 shadow-2xs sm:inline-flex sm:w-auto">
          {MODES.map((item) => {
            const Icon = ICONS[item];
            return (
            <button key={item} id={`mode-${item}`} role="tab" type="button" aria-selected={mode === item} aria-controls="check-workspace-panel" tabIndex={mode === item ? 0 : -1} onClick={() => setMode(item)} onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const position = MODES.indexOf(item);
              const next = event.key === "Home" ? MODES[0] : event.key === "End" ? MODES[MODES.length - 1] : MODES[(position + (event.key === "ArrowRight" ? 1 : -1) + MODES.length) % MODES.length];
              setMode(next);
              const target = document.getElementById(`mode-${next}`);
              target?.focus();
              target?.scrollIntoView({ block: "nearest", inline: "nearest" });
            }} className={cn("inline-flex min-h-10 min-w-0 items-center justify-center gap-1.5 rounded-lg px-1.5 text-[0.8125rem] font-medium outline-none transition-all focus-visible:ring-2 focus-visible:ring-ring sm:gap-2 sm:px-4 sm:text-sm", mode === item ? "bg-card text-foreground shadow-[0_1px_2px_rgb(0_0_0/0.06),inset_0_1px_0_rgb(255_255_255/0.6)] ring-1 ring-border/80 dark:shadow-[0_1px_2px_rgb(0_0_0/0.4),inset_0_1px_0_rgb(255_255_255/0.06)]" : "text-muted-foreground hover:bg-card/60 hover:text-foreground")}>
              <Icon className={cn("size-4 shrink-0 transition-colors", mode === item ? "text-foreground" : "text-muted-foreground")} aria-hidden /><span className="truncate">{LABELS[item]}</span>
            </button>
          );})}
        </div></div>
      </div>
      <div id="check-workspace-panel" role="tabpanel" aria-labelledby={`mode-${mode}`} className="flex flex-1 flex-col">
        {/* Keep ordinary work only in this page's memory; Private still clears on unmount. */}
        <div hidden={mode !== "draft"} className={mode === "draft" ? "flex flex-1 flex-col" : undefined}>
          <ReceiptsApp active={mode === "draft"} livePaused={livePaused} draftCachePolicy={draftCachePolicy} />
        </div>
        {mode === "slide" ? <SlideApp /> : mode === "ask" ? <AskWorkspace livePaused={livePaused} /> : mode === "private" ? <PrivateCheck /> : null}
      </div>
    </>
  );
}
