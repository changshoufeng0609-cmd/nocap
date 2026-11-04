"use client";

import { useEffect, useRef, useState } from "react";

type CutId = "main" | "x";
type ModelFilterId = "all" | "spark" | "wonder" | "sam" | "glimmer";

type Chapter = {
  id: string;
  time: string;
  start: number;
  end: number;
  title: string;
  model: string;
  models: ModelFilterId[];
  summary: string;
};

type Cue = {
  id: string;
  start: number;
  end: number;
  time: string;
  text: string;
};

const META_MODEL_FILTERS: {
  id: ModelFilterId;
  name: string;
  role: string;
  jumpMain: number;
  jumpX: number;
}[] = [
  { id: "all", name: "All Scenes", role: "Full workflow", jumpMain: 0, jumpX: 0 },
  { id: "spark", name: "Muse Spark 1.3", role: "Claims & 10-K", jumpMain: 27.21, jumpX: 7.89 },
  { id: "wonder", name: "Muse Wonder 1.0", role: "Fix with Receipts", jumpMain: 62.63, jumpX: 27.2 },
  { id: "sam", name: "SAM 3.1", role: "Slide Regions", jumpMain: 86.27, jumpX: 34.4 },
  { id: "glimmer", name: "Muse Glimmer", role: "Private Modal GPU", jumpMain: 104.84, jumpX: 39.59 },
];

const FOUR_ACTS = [
  { id: "act-1", code: "ACT I · 0:00–0:27", title: "The Problem: One Wrong Number", start: 0, end: 27.21 },
  { id: "act-2", code: "ACT II · 0:27–1:53", title: "Real Product: Check · Receipts · Fix · Cutoff · Slide · 10-K · Private", start: 27.21, end: 113.46 },
  { id: "act-3", code: "ACT III · 1:53–2:19", title: "Under the Hood: Four Meta Models, Code Decides", start: 113.46, end: 139.6 },
  { id: "act-4", code: "ACT IV · 2:19–2:50", title: "Proof: 500 Companies & a One-Shot Test", start: 139.6, end: 170.35 },
];

const FIVE_BEATS = [
  { id: "beat-1", code: "BEAT 1 · 0:00–0:08", title: "01 Hook (One Wrong Number)", start: 0, end: 7.89 },
  { id: "beat-2", code: "BEAT 2 · 0:08–0:15", title: "02 Check (7 ✓, 2 ✗, 2 ?)", start: 7.89, end: 14.82 },
  { id: "beat-3", code: "BEAT 3 · 0:15–0:27", title: "03 Receipts (105.85%, $2.46)", start: 14.82, end: 27.2 },
  { id: "beat-4", code: "BEAT 4 · 0:27–0:40", title: "04 Fix (9 ✓, 0 ✗) & SAM 3.1", start: 27.2, end: 39.59 },
  { id: "beat-5", code: "BEAT 5 · 0:40–1:00", title: "05 One-Shot Test & Try It", start: 39.59, end: 59.83 },
];

const MAIN_CHAPTERS: Chapter[] = [
  {
    id: "problem",
    time: "0:00",
    start: 0,
    end: 27.21,
    title: "The Problem: One Wrong Number",
    model: "Animation",
    models: [],
    summary: "An 18% that should be 105.85% becomes a public correction; AI drafts make numbers cheap to write and costly to check.",
  },
  {
    id: "check",
    time: "0:27",
    start: 27.21,
    end: 38.97,
    title: "Check the Draft (7 ✓, 2 ✗, 2 ?)",
    model: "Muse Spark 1.3 extracts · code verifies",
    models: ["spark"],
    summary: "The real interface: an AI-drafted NVIDIA story with two planted errors comes back 7 verified, 2 wrong, 2 unverifiable.",
  },
  {
    id: "receipt",
    time: "0:38",
    start: 38.97,
    end: 55.48,
    title: "Receipt: 18% vs 105.85%",
    model: "Deterministic code · SEC EDGAR",
    models: ["spark"],
    summary: "The receipt does the math from the filings: ($96,221,000,000 ÷ $46,743,000,000 − 1) × 100 ≈ 105.85%; 18% was quarter over quarter.",
  },
  {
    id: "eps",
    time: "0:55",
    start: 55.48,
    end: 62.63,
    title: "Receipt: $2.22 vs $2.46",
    model: "Deterministic code · SEC EDGAR",
    models: ["spark"],
    summary: "The draft's GAAP EPS of $2.22 is the 8-K's non-GAAP figure; the 10-Q's GAAP figure is $2.46.",
  },
  {
    id: "fix",
    time: "1:02",
    start: 62.63,
    end: 73.46,
    title: "Fix with Receipts (9 ✓, 0 ✗)",
    model: "Muse Wonder 1.0 proposes · code re-checks",
    models: ["wonder", "spark"],
    summary: "Muse Wonder proposes the corrections; code lets only the digits change and re-checks the whole draft: 9 verified, 0 wrong.",
  },
  {
    id: "cutoff",
    time: "1:13",
    start: 73.46,
    end: 86.27,
    title: "Time Travel: As of July 29",
    model: "Muse Spark 1.3 · point-in-time gate",
    models: ["spark"],
    summary: "Checked as of July 29, a figure whose filing wasn't public yet is look-ahead, not wrong: 6 verified, 1 wrong, 2 look-ahead.",
  },
  {
    id: "slide",
    time: "1:26",
    start: 86.27,
    end: 94.23,
    title: "A Real Bank of America Slide",
    model: "SAM 3.1 + Muse Spark 1.3",
    models: ["sam", "spark"],
    summary: "SAM 3.1 finds the chart regions; Muse Spark reads 32 printed labels; code checks each against filings.",
  },
  {
    id: "ask",
    time: "1:34",
    start: 94.23,
    end: 104.84,
    title: "Ask a Whole 10-K",
    model: "Muse Spark 1.3 · 101k-token 10-K",
    models: ["spark"],
    summary: "One Muse Spark request reads Meta's entire 10-K (about 101,000 tokens); code keeps only quotes found word for word.",
  },
  {
    id: "private",
    time: "1:44",
    start: 104.84,
    end: 113.46,
    title: "Private Mode on Open Weights",
    model: "Muse Glimmer · Modal GPU",
    models: ["glimmer"],
    summary: "The same check on open weights, Muse Glimmer, on the operator's own Modal GPU; the draft never leaves that deployment.",
  },
  {
    id: "how",
    time: "1:53",
    start: 113.46,
    end: 139.6,
    title: "Under the Hood",
    model: "Spark · Wonder · SAM 3.1 · Glimmer",
    models: ["spark", "wonder", "sam", "glimmer"],
    summary: "Models propose, code decides: value, period, scale, GAAP or non-GAAP, company and a point-in-time gate, against SEC EDGAR.",
  },
  {
    id: "proof",
    time: "2:19",
    start: 139.6,
    end: 170.35,
    title: "500 Companies & a One-Shot Test",
    model: "1,000 frozen test items",
    models: ["spark", "wonder", "sam", "glimmer"],
    summary: "All 500 S&P 500 companies, eight quarters. One-shot test: 306 of 360 planted errors caught, none marked verified, 17 of 1,298 true figures flagged.",
  },
];

const X_CHAPTERS: Chapter[] = [
  {
    id: "x-hook",
    time: "0:00",
    start: 0,
    end: 7.89,
    title: "One Wrong Number",
    model: "Animation",
    models: [],
    summary: "One wrong number means a public correction; NoCap checks every number against SEC filings.",
  },
  {
    id: "x-check",
    time: "0:07",
    start: 7.89,
    end: 14.82,
    title: "Check: 7 ✓, 2 ✗, 2 ?",
    model: "Muse Spark 1.3 extracts · code verifies",
    models: ["spark"],
    summary: "An AI-drafted NVIDIA story with two planted errors: 7 verified, 2 wrong, 2 unverifiable.",
  },
  {
    id: "x-receipt",
    time: "0:14",
    start: 14.82,
    end: 22.48,
    title: "Receipt: 18% vs 105.85%",
    model: "Deterministic code · SEC EDGAR",
    models: ["spark"],
    summary: "The receipt does the math from the filings: 105.85% year over year; 18% was quarter over quarter.",
  },
  {
    id: "x-eps",
    time: "0:22",
    start: 22.48,
    end: 27.2,
    title: "Receipt: $2.22 vs $2.46",
    model: "Deterministic code · SEC EDGAR",
    models: ["spark"],
    summary: "GAAP EPS: $2.22 in the draft (the 8-K's non-GAAP figure), $2.46 in the 10-Q.",
  },
  {
    id: "x-fix",
    time: "0:27",
    start: 27.2,
    end: 34.4,
    title: "Fix with Receipts: 9 ✓, 0 ✗",
    model: "Muse Wonder 1.0 proposes · code re-checks",
    models: ["wonder", "spark"],
    summary: "Muse Wonder proposes 105.9% and $2.46; only digits may change; the re-check gives 9 verified, 0 wrong.",
  },
  {
    id: "x-slide",
    time: "0:34",
    start: 34.4,
    end: 39.59,
    title: "SAM 3.1 on a Real Slide",
    model: "SAM 3.1 + Muse Spark 1.3",
    models: ["sam", "spark"],
    summary: "SAM 3.1 finds the charts on a Bank of America slide; Muse Spark reads 32 labels.",
  },
  {
    id: "x-proof",
    time: "0:39",
    start: 39.59,
    end: 51.98,
    title: "One-Shot Test",
    model: "1,000 frozen test items",
    models: ["spark", "wonder", "sam", "glimmer"],
    summary: "306 of 360 planted errors caught, none passed as verified, 17 of 1,298 true figures flagged; verified calls right 736 of 738 times.",
  },
  {
    id: "x-end",
    time: "0:51",
    start: 51.98,
    end: 59.83,
    title: "Try It Live",
    model: "Team NoCap",
    models: [],
    summary: "For financial journalists and analysts: receipts-nocap.vercel.app, live through November 17.",
  },
];

const MAIN_CUES: Cue[] = [
  { id: "c01", start: 0.6, end: 4.34, time: "0:00", text: "18%. One number in an earnings story." },
  { id: "c02", start: 4.69, end: 7.68, time: "0:04", text: "If it's wrong, it becomes a public correction." },
  { id: "c03", start: 8.58, end: 13.17, time: "0:08", text: "And AI-written drafts make numbers cheap to produce, and costly to check." },
  { id: "c04", start: 13.27, end: 19.85, time: "0:13", text: "Financial journalists and analysts trace each figure back to SEC filings by hand, one filing at a time." },
  { id: "c05", start: 20.45, end: 23.74, time: "0:20", text: "NoCap gives every number a receipt from the filing." },
  { id: "c06", start: 23.74, end: 26.61, time: "0:23", text: "Models propose; code decides." },
  { id: "c07", start: 27.41, end: 29.32, time: "0:27", text: "This is the real interface." },
  { id: "c08", start: 29.32, end: 33.56, time: "0:29", text: "An AI-drafted NVIDIA story with two planted errors. Check." },
  { id: "c09", start: 33.56, end: 35.95, time: "0:33", text: "Seven verified. Two wrong." },
  { id: "c10", start: 35.95, end: 39.17, time: "0:35", text: "Two unverifiable, because NoCap never guesses." },
  { id: "c11", start: 39.17, end: 42.68, time: "0:39", text: "The draft says revenue rose 18% from a year ago." },
  { id: "c12", start: 42.68, end: 47.81, time: "0:42", text: "The receipt does the math from the filings: 105.85%." },
  { id: "c13", start: 47.81, end: 49.64, time: "0:47", text: "The 18% was quarter over quarter." },
  { id: "c14", start: 49.64, end: 55.67, time: "0:49", text: "Each receipt names the filing, its accession number, and when EDGAR accepted it." },
  { id: "c15", start: 55.67, end: 60.2, time: "0:55", text: "GAAP earnings per diluted share: the draft says $2.22." },
  { id: "c16", start: 60.2, end: 62.83, time: "1:00", text: "The filing says $2.46." },
  { id: "c17", start: 62.83, end: 69.18, time: "1:02", text: "Fix with receipts. Muse Wonder proposes the corrections, and code lets only the digits change." },
  { id: "c18", start: 69.18, end: 71.47, time: "1:09", text: "Then the whole draft is checked again:" },
  { id: "c19", start: 71.47, end: 73.66, time: "1:11", text: "nine verified, zero wrong." },
  { id: "c20", start: 73.66, end: 78.72, time: "1:13", text: "Time travel. A June-quarter roundup, checked as of July 29." },
  { id: "c21", start: 78.72, end: 82.99, time: "1:18", text: "A figure whose filing wasn't public yet is look-ahead, not wrong:" },
  { id: "c22", start: 82.99, end: 86.47, time: "1:22", text: "six verified, one wrong, two look-ahead." },
  { id: "c23", start: 86.47, end: 89.06, time: "1:26", text: "A real Bank of America earnings slide." },
  { id: "c24", start: 89.06, end: 94.43, time: "1:29", text: "SAM 3.1 finds the chart regions, and Muse Spark reads 32 printed labels." },
  { id: "c25", start: 94.43, end: 101.76, time: "1:34", text: "Ask a whole filing. Muse Spark reads Meta's entire 10-K, about 101,000 tokens, in one request." },
  { id: "c26", start: 101.76, end: 105.04, time: "1:41", text: "Code keeps only the quotes it finds word for word." },
  { id: "c27", start: 105.04, end: 111.21, time: "1:45", text: "Private mode: the same check on open weights, Muse Glimmer, on the operator's own Modal GPU." },
  { id: "c28", start: 111.21, end: 113.76, time: "1:51", text: "The draft never leaves that deployment." },
  { id: "c29", start: 114.01, end: 117.22, time: "1:54", text: "Under the hood, four Meta models." },
  { id: "c30", start: 117.22, end: 121.09, time: "1:57", text: "Muse Spark 1.3 extracts typed claims and plans the lookups." },
  { id: "c31", start: 121.09, end: 125.32, time: "2:01", text: "Deterministic code decides, against SEC EDGAR:" },
  { id: "c32", start: 125.32, end: 130.31, time: "2:05", text: "value, period, scale, GAAP or non-GAAP, company, and a point-in-time gate." },
  { id: "c33", start: 130.31, end: 134.42, time: "2:10", text: "Muse Wonder 1.0 proposes corrections that code re-checks." },
  { id: "c34", start: 134.42, end: 139.35, time: "2:14", text: "SAM 3.1 and Muse Spark read slides. Muse Glimmer runs Private." },
  { id: "c35", start: 140.05, end: 145.33, time: "2:20", text: "It covers all 500 S&P 500 companies, the latest eight fiscal quarters." },
  { id: "c36", start: 145.33, end: 153.09, time: "2:25", text: "In a one-shot test on 1,000 fresh items from 400 companies, NoCap caught 306 of 360 planted errors." },
  { id: "c37", start: 153.09, end: 159.16, time: "2:33", text: "It marked none of them verified, and flagged 17 of 1,298 true figures as wrong." },
  { id: "c38", start: 159.31, end: 162.26, time: "2:39", text: "Try it live through November 17." },
  { id: "c39", start: 162.26, end: 168.15, time: "2:42", text: "NoCap, built by Robert Chang during the Meta Global AI Developer Hackathon." },
];

const X_CUES: Cue[] = [
  { id: "c01", start: 0.2, end: 2.65, time: "0:00", text: "One wrong number means a public correction." },
  { id: "c02", start: 2.65, end: 7.89, time: "0:02", text: "NoCap: Muse Spark finds every number, code checks it against SEC filings." },
  { id: "c03", start: 7.89, end: 11.05, time: "0:07", text: "An AI-drafted NVIDIA story, two planted errors." },
  { id: "c04", start: 11.05, end: 14.82, time: "0:11", text: "Seven verified, two wrong, two unverifiable. No guessing." },
  { id: "c05", start: 14.82, end: 17.34, time: "0:14", text: "Revenue up 18% from a year ago?" },
  { id: "c06", start: 17.34, end: 20.02, time: "0:17", text: "The receipt says 105.85%." },
  { id: "c07", start: 20.02, end: 22.48, time: "0:20", text: "18% was quarter over quarter." },
  { id: "c08", start: 22.48, end: 27.2, time: "0:22", text: "GAAP EPS: $2.22 in the draft, $2.46 in the filing." },
  { id: "c09", start: 27.2, end: 31.24, time: "0:27", text: "Fix: Muse Wonder proposes, only digits may change." },
  { id: "c10", start: 31.24, end: 34.4, time: "0:31", text: "Recheck: nine verified, zero wrong." },
  { id: "c11", start: 34.4, end: 39.59, time: "0:34", text: "Slides too: SAM 3.1 finds the charts, Muse Spark reads 32 labels." },
  { id: "c12", start: 39.59, end: 44.95, time: "0:39", text: "In testing, 306 of 360 planted errors were caught, none passed as verified," },
  { id: "c13", start: 44.95, end: 47.61, time: "0:44", text: "and 17 of 1,298 true figures flagged." },
  { id: "c14", start: 47.61, end: 51.98, time: "0:47", text: "Its verified calls were right 736 of 738 times." },
  { id: "c15", start: 51.98, end: 54.66, time: "0:51", text: "Built for financial journalists and analysts." },
  { id: "c16", start: 54.66, end: 59.53, time: "0:54", text: "receipts-nocap.vercel.app, live through November 17." },
];

export function DemoPlayer() {
  const [cut, setCut] = useState<CutId>("main");
  const [currentTime, setCurrentTime] = useState(0);
  const [showTranscript, setShowTranscript] = useState(false);
  const [selectedModel, setSelectedModel] = useState<ModelFilterId>("all");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const activeCueRef = useRef<HTMLButtonElement | null>(null);

  const chapters = cut === "main" ? MAIN_CHAPTERS : X_CHAPTERS;
  const visibleChapters =
    selectedModel === "all"
      ? chapters
      : chapters.filter((ch) => ch.models.includes(selectedModel));
  const cues = cut === "main" ? MAIN_CUES : X_CUES;
  const activeChapter =
    chapters.find((c) => currentTime >= c.start && currentTime < c.end) ?? chapters[0];
  const activeCue =
    cues.find((c) => currentTime >= c.start && currentTime <= c.end) ?? null;

  useEffect(() => {
    if (showTranscript && activeCueRef.current) {
      activeCueRef.current.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    }
  }, [activeCue?.id, showTranscript]);

  function seekTo(seconds: number) {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = seconds;
    setCurrentTime(seconds);
    v.play().catch(() => {});
  }

  function handleSwitchCut(nextCut: CutId) {
    if (nextCut === cut) return;
    const currentDuration = cut === "main" ? 170.33 : 59.83;
    const targetDuration = nextCut === "main" ? 170.33 : 59.83;
    const progress = currentTime / currentDuration;
    const targetTime = Math.min(targetDuration - 0.5, Math.max(0, progress * targetDuration));
    setCut(nextCut);
    setTimeout(() => {
      seekTo(targetTime);
    }, 50);
  }

  function handleSelectModel(m: (typeof META_MODEL_FILTERS)[number]) {
    setSelectedModel(m.id);
    if (m.id !== "all") {
      seekTo(cut === "main" ? m.jumpMain : m.jumpX);
    }
  }

  return (
    <div className="space-y-4">
      {/* Format switcher + active model pill */}
      <div className="space-y-2.5 rounded-xl border border-border bg-card px-4 py-3 shadow-2xs">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Video cut format">
            <button
              type="button"
              role="tab"
              aria-selected={cut === "main"}
              onClick={() => handleSwitchCut("main")}
              className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-xs font-semibold transition sm:text-sm ${
                cut === "main"
                  ? "bg-foreground text-background shadow-2xs"
                  : "border border-border/80 bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              <span className="font-mono text-[0.7rem] uppercase tracking-wider opacity-80">16:9</span>
              Pitch Demo (2:50)
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={cut === "x"}
              onClick={() => handleSwitchCut("x")}
              className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-xs font-semibold transition sm:text-sm ${
                cut === "x"
                  ? "bg-foreground text-background shadow-2xs"
                  : "border border-border/80 bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              <span className="rounded bg-verified/20 px-1.5 py-0.5 font-mono text-[0.68rem] font-semibold text-verified">
                9:16 Vertical
              </span>
              X &amp; Threads Cut (1:00)
            </button>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Active scene:</span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-verified/35 bg-verified-bg/60 px-2.5 py-1 font-mono text-[0.72rem] font-medium text-verified">
              <span className="size-1.5 rounded-full bg-verified" aria-hidden="true" />
              {activeChapter.model}
            </span>
          </div>
        </div>

        {/* Meta 4-Model Quick Filter Strip */}
        <div className="flex flex-wrap items-center gap-1.5 border-t border-border/60 pt-2.5">
          <span className="mr-1 font-mono text-[0.7rem] uppercase tracking-wider text-muted-foreground">
            Filter by Meta Model:
          </span>
          {META_MODEL_FILTERS.map((m) => {
            const active = selectedModel === m.id;
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => handleSelectModel(m)}
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-xs font-medium transition ${
                  active
                    ? "bg-foreground text-background shadow-2xs"
                    : "border border-border/80 bg-background text-muted-foreground hover:border-foreground/40 hover:text-foreground"
                }`}
              >
                <span>{m.name}</span>
                <span className={`text-[0.65rem] ${active ? "text-background/75" : "text-muted-foreground/80"}`}>
                  · {m.role}
                </span>
              </button>
            );
          })}
          {selectedModel !== "all" && (
            <button
              type="button"
              onClick={() => setSelectedModel("all")}
              className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-muted/40 px-2 py-0.5 font-mono text-[0.68rem] text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              ✕ Reset
            </button>
          )}
        </div>
      </div>

      {/* Main video & chapter layout */}
      <figure className="space-y-4 rounded-xl border border-border bg-card p-3 shadow-xs sm:p-5">
        {/* Timeline Act / Beat Navigation Bar */}
        {cut === "main" ? (
          <div className="grid gap-2 sm:grid-cols-4" aria-label="Four-act video narrative structure">
            {FOUR_ACTS.map((act) => {
              const isActActive = currentTime >= act.start && currentTime < act.end;
              const progressPct =
                currentTime <= act.start
                  ? 0
                  : currentTime >= act.end
                    ? 100
                    : Math.round(((currentTime - act.start) / (act.end - act.start)) * 100);
              return (
                <button
                  key={act.id}
                  type="button"
                  onClick={() => seekTo(act.start)}
                  className={`group relative overflow-hidden rounded-lg border px-3 py-2 text-left transition ${
                    isActActive
                      ? "border-verified/60 bg-verified-bg/35 shadow-2xs"
                      : "border-border/70 bg-background/70 hover:border-foreground/30 hover:bg-muted/35"
                  }`}
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <span className="font-mono text-[0.66rem] font-semibold uppercase tracking-wider text-verified">
                      {act.code}
                    </span>
                    <span className="font-mono text-[0.65rem] text-muted-foreground">{progressPct}%</span>
                  </div>
                  <p className="mt-0.5 truncate text-xs font-semibold text-foreground">{act.title}</p>
                  <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-border/60">
                    <div
                      className="h-full rounded-full bg-verified transition-all duration-200"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="grid gap-1.5 sm:grid-cols-5" aria-label="Five-beat mobile story structure">
            {FIVE_BEATS.map((beat) => {
              const isBeatActive = currentTime >= beat.start && currentTime < beat.end;
              const progressPct =
                currentTime <= beat.start
                  ? 0
                  : currentTime >= beat.end
                    ? 100
                    : Math.round(((currentTime - beat.start) / (beat.end - beat.start)) * 100);
              return (
                <button
                  key={beat.id}
                  type="button"
                  onClick={() => seekTo(beat.start)}
                  className={`group relative overflow-hidden rounded-lg border px-2.5 py-1.5 text-left transition ${
                    isBeatActive
                      ? "border-verified/60 bg-verified-bg/35 shadow-2xs"
                      : "border-border/70 bg-background/70 hover:border-foreground/30 hover:bg-muted/35"
                  }`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-mono text-[0.62rem] font-semibold uppercase tracking-wider text-verified">
                      {beat.code}
                    </span>
                    <span className="font-mono text-[0.62rem] text-muted-foreground">{progressPct}%</span>
                  </div>
                  <p className="mt-0.5 truncate text-[0.72rem] font-semibold text-foreground">{beat.title}</p>
                  <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-border/60">
                    <div
                      className="h-full rounded-full bg-verified transition-all duration-200"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                </button>
              );
            })}
          </div>
        )}
        {cut === "main" ? (
          <div className="overflow-hidden rounded-lg border border-border bg-[#0f1b16] shadow-sm">
            <video
              key="main-video"
              ref={videoRef}
              controls
              playsInline
              preload="metadata"
              poster="/demo/poster.png"
              onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
              aria-label="NoCap pitch demo, 2 minutes 50 seconds (16:9)"
              className="aspect-video w-full bg-[#0f1b16]"
            >
              <source src="/demo/receipts-demo.mp4" type="video/mp4" />
              <track kind="captions" src="/demo/receipts-demo.en.vtt" srcLang="en" label="English" />
              Your browser does not support embedded video.{" "}
              <a href="/demo/receipts-demo.mp4">Download the MP4</a>.
            </video>
          </div>
        ) : (
          <div className="grid items-start gap-6 lg:grid-cols-[380px_1fr]">
            <div className="mx-auto w-full max-w-[380px] overflow-hidden rounded-2xl border-2 border-border bg-[#0f1b16] shadow-md">
              <video
                key="x-video"
                ref={videoRef}
                controls
                playsInline
                preload="metadata"
                poster="/demo/poster-x.png"
                onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                aria-label="NoCap vertical cut for X and Threads, 60 seconds (9:16)"
                className="aspect-[9/16] w-full bg-[#0f1b16]"
              >
                <source src="/demo/nocap-x.mp4" type="video/mp4" />
                <track kind="captions" src="/demo/nocap-x.en.vtt" srcLang="en" label="English" />
                Your browser does not support embedded video.{" "}
                <a href="/demo/nocap-x.mp4">Download the vertical MP4</a>.
              </video>
            </div>
            <div className="space-y-4">
              <div className="rounded-xl border border-border/80 bg-muted/25 p-4">
                <p className="font-mono text-[0.7rem] font-semibold uppercase tracking-widest text-verified">
                  Mobile-First 9:16 Studio Layout (1080 × 1920)
                </p>
                <h3 className="mt-1 font-serif text-xl font-semibold">
                  Designed for X &amp; Threads vertical feeds
                </h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                  Instead of cropping out half the interface or shrinking desktop widescreen into a tiny strip, this vertical cut stacks the left draft/slide column and the right SEC receipt panel vertically at full mobile width, with burned-in high-contrast captions for muted autoplay.
                </p>
              </div>
              <div className="space-y-2">
                <p className="font-mono text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Jump to scene ({visibleChapters.length} chapters)
                </p>
                <div className="grid gap-2">
                  {visibleChapters.map((ch) => {
                    const isActive = activeChapter.id === ch.id;
                    return (
                      <button
                        key={ch.id}
                        type="button"
                        onClick={() => seekTo(ch.start)}
                        className={`flex flex-col gap-1 rounded-lg border p-3 text-left transition ${
                          isActive
                            ? "border-verified/60 bg-verified-bg/40 shadow-2xs"
                            : "border-border/70 bg-background/70 hover:border-foreground/30 hover:bg-muted/40"
                        }`}
                      >
                        <div className="flex w-full items-center justify-between gap-2">
                          <span className="text-sm font-semibold text-foreground">
                            <span className="mr-2 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                              {ch.time}
                            </span>
                            {ch.title}
                          </span>
                          <span className="rounded border border-border/80 bg-card px-2 py-0.5 font-mono text-[0.68rem] text-muted-foreground">
                            {ch.model}
                          </span>
                        </div>
                        <p className="text-xs leading-relaxed text-muted-foreground">{ch.summary}</p>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Interactive Chapter Bar for 16:9 Main Walkthrough */}
        {cut === "main" && (
          <div className="space-y-2.5 pt-1">
            <div className="flex flex-wrap items-center justify-between gap-2 px-0.5">
              <p className="font-mono text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Interactive Chapters ({visibleChapters.length}) · Click any scene to jump
              </p>
              {activeCue && (
                <p className="truncate font-serif text-xs italic text-muted-foreground sm:max-w-md">
                  “{activeCue.text}”
                </p>
              )}
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              {visibleChapters.map((ch) => {
                const isActive = activeChapter.id === ch.id;
                return (
                  <button
                    key={ch.id}
                    type="button"
                    onClick={() => seekTo(ch.start)}
                    className={`group flex flex-col justify-between rounded-lg border p-2.5 text-left transition ${
                      isActive
                        ? "border-verified/60 bg-verified-bg/40 shadow-2xs"
                        : "border-border/70 bg-background/70 hover:border-foreground/30 hover:bg-muted/35"
                    }`}
                  >
                    <div>
                      <div className="flex items-center justify-between gap-1.5">
                        <span
                          className={`rounded px-1.5 py-0.5 font-mono text-[0.7rem] font-semibold ${
                            isActive
                              ? "bg-verified text-white"
                              : "bg-muted text-muted-foreground group-hover:text-foreground"
                          }`}
                        >
                          {ch.time}
                        </span>
                        <span className="truncate font-mono text-[0.66rem] text-muted-foreground">
                          {ch.model}
                        </span>
                      </div>
                      <p className="mt-1.5 text-xs font-semibold text-foreground">{ch.title}</p>
                      <p className="mt-0.5 line-clamp-2 text-[0.72rem] leading-snug text-muted-foreground">
                        {ch.summary}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Recording disclosure */}
        <figcaption className="rounded-lg border border-border/70 bg-muted/35 p-3.5 text-xs leading-relaxed text-muted-foreground sm:text-sm">
          <strong className="font-medium text-foreground">Recording disclosure:</strong> The 2:50 pitch demo combines illustrative animation, whose
          figures come from NoCap&rsquo;s real checks, with the real NoCap interface, recorded locally on November 4, 2026 from the release
          source with live model requests paused, plus original music and English captions. Draft checks replay cached extraction. Fix,
          Slide, Ask and Private replay recorded live runs for the same inputs (Fix from October 29; the Bank of America slide, the Meta
          10-K question and the Modal Glimmer Private check from October 30) with their waits shortened. The live-check notice and the
          enabled Fix button show the judging-window settings; the recording itself made no model calls. It is a demonstration, not a
          latency measurement.
        </figcaption>

        {/* Download links + Interactive Transcript toggle */}
        <div className="flex flex-wrap items-center justify-between gap-3 px-1 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <a
              href="/demo/receipts-demo.mp4"
              download
              className="inline-flex items-center gap-2 rounded-lg border border-border/80 bg-background/80 px-3 py-1.5 font-mono text-xs font-medium text-foreground transition hover:border-foreground/40 hover:bg-muted/40"
            >
              <span className="rounded bg-muted px-1.5 py-0.5 text-[0.65rem] font-semibold text-muted-foreground">
                16:9 MP4
              </span>
              Pitch Demo
            </a>
            <a
              href="/demo/nocap-x.mp4"
              download
              className="inline-flex items-center gap-2 rounded-lg border border-border/80 bg-background/80 px-3 py-1.5 font-mono text-xs font-medium text-foreground transition hover:border-foreground/40 hover:bg-muted/40"
            >
              <span className="rounded bg-verified/20 px-1.5 py-0.5 text-[0.65rem] font-semibold text-verified">
                9:16 MP4
              </span>
              X &amp; Threads Cut
            </a>
            <a
              href={cut === "main" ? "/demo/receipts-demo.en.vtt" : "/demo/nocap-x.en.vtt"}
              download
              className="inline-flex items-center gap-1.5 rounded-lg border border-border/70 bg-background/60 px-2.5 py-1.5 font-mono text-xs text-muted-foreground transition hover:text-foreground"
            >
              <span>Captions (.vtt)</span>
            </a>
          </div>
          <button
            type="button"
            onClick={() => setShowTranscript((v) => !v)}
            aria-expanded={showTranscript}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 font-mono text-xs font-medium text-foreground transition hover:bg-muted/60"
          >
            {showTranscript ? "Hide" : "Show"} Interactive Transcript ({cues.length} cues)
          </button>
        </div>

        {/* Synchronized clickable transcript */}
        {showTranscript && (
          <div className="max-h-72 overflow-y-auto rounded-lg border border-border bg-background p-3">
            <div className="grid gap-1">
              {cues.map((cue) => {
                const isCurrent = activeCue?.id === cue.id;
                return (
                  <button
                    key={cue.id}
                    ref={isCurrent ? activeCueRef : null}
                    type="button"
                    onClick={() => seekTo(cue.start)}
                    className={`flex items-baseline gap-3 rounded-md px-2.5 py-1.5 text-left text-xs transition sm:text-sm ${
                      isCurrent
                        ? "bg-verified-bg/60 font-medium text-foreground"
                        : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                    }`}
                  >
                    <span className="shrink-0 font-mono text-xs text-verified">{cue.time}</span>
                    <span>{cue.text}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </figure>
    </div>
  );
}
