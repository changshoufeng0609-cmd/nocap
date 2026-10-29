"use client";

import { FileImageIcon, FileTextIcon, MessageSquareTextIcon, ShieldCheckIcon } from "lucide-react";
import { useState } from "react";

import { ReceiptsApp } from "@/components/receipts-app";
import { SlideApp } from "@/components/slide-app";
import { AskWorkspace } from "@/components/ask-workspace";
import { PrivateCheck } from "@/components/private-check";
import { cn } from "@/lib/utils";

const MODES = ["draft", "slide", "ask", "private"] as const;
const LABELS = { draft: "Draft", slide: "Slide", ask: "Ask", private: "Private" };
const ICONS = { draft: FileTextIcon, slide: FileImageIcon, ask: MessageSquareTextIcon, private: ShieldCheckIcon };

export function CheckWorkspace() {
  const [mode, setMode] = useState<(typeof MODES)[number]>("draft");
  return (
    <>
      <div className="mx-auto w-full max-w-6xl px-4 pt-5 sm:px-6">
        <div className="max-w-full overflow-x-auto pb-1"><div role="tablist" aria-label="What to check" className="inline-flex min-w-max gap-1 rounded-lg border border-border bg-muted/40 p-1">
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
            }} className={cn("inline-flex min-h-10 items-center gap-2 rounded-md px-4 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring", mode === item ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
              <Icon className="size-4" aria-hidden />{LABELS[item]}
            </button>
          );})}
        </div></div>
      </div>
      <div id="check-workspace-panel" role="tabpanel" aria-labelledby={`mode-${mode}`} className="flex flex-1 flex-col">
        {mode === "draft" ? <ReceiptsApp /> : mode === "slide" ? <SlideApp /> : mode === "ask" ? <AskWorkspace /> : <PrivateCheck />}
      </div>
    </>
  );
}
