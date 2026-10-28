"use client";

import { FileImageIcon, FileTextIcon } from "lucide-react";
import { useState } from "react";

import { ReceiptsApp } from "@/components/receipts-app";
import { SlideApp } from "@/components/slide-app";
import { cn } from "@/lib/utils";

const MODES = ["draft", "slide"] as const;

export function CheckWorkspace() {
  const [mode, setMode] = useState<(typeof MODES)[number]>("draft");
  return (
    <>
      <div className="mx-auto w-full max-w-6xl px-4 pt-5 sm:px-6">
        <div role="tablist" aria-label="What to check" className="inline-flex gap-1 rounded-lg border border-border bg-muted/40 p-1">
          {MODES.map((item) => (
            <button key={item} id={`mode-${item}`} role="tab" type="button" aria-selected={mode === item} aria-controls="check-workspace-panel" tabIndex={mode === item ? 0 : -1} onClick={() => setMode(item)} onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const next = event.key === "Home" ? "draft" : event.key === "End" ? "slide" : mode === "draft" ? "slide" : "draft";
              setMode(next);
              document.getElementById(`mode-${next}`)?.focus();
            }} className={cn("inline-flex min-h-10 items-center gap-2 rounded-md px-4 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring", mode === item ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
              {item === "draft" ? <FileTextIcon className="size-4" aria-hidden /> : <FileImageIcon className="size-4" aria-hidden />}{item === "draft" ? "Draft" : "Slide"}
            </button>
          ))}
        </div>
      </div>
      <div id="check-workspace-panel" role="tabpanel" aria-labelledby={`mode-${mode}`} className="flex flex-1 flex-col">
        {mode === "draft" ? <ReceiptsApp /> : <SlideApp />}
      </div>
    </>
  );
}
