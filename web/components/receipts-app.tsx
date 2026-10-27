"use client";

import { Loader2Icon, PencilLineIcon, TriangleAlertIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { toast } from "sonner";

import { ClaimsList } from "@/components/claims-list";
import { AsOfPicker, ExamplePicker, type AsOfState } from "@/components/draft-controls";
import { HighlightedDraft } from "@/components/highlighted-draft";
import { HowItWorks } from "@/components/how-it-works";
import { ReceiptDetail } from "@/components/receipt-detail";
import { SummaryStrip } from "@/components/summary-strip";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useCheck } from "@/hooks/use-check";
import { useMediaQuery } from "@/hooks/use-media-query";
import { fetchExamples } from "@/lib/api";
import { STATUS_META, reasonText } from "@/lib/claims";
import { fromUtcInputValue, toUtcInputValue } from "@/lib/format";
import { MAX_DRAFT_CHARS, type CheckRequest, type Example } from "@/lib/types";
import { cn } from "@/lib/utils";

const PANEL_ID = "receipt-panel";
const TITLE_ID = "receipt-title";

// The current UTC minute for the as-of picker. Read only in the browser: under Cache Components the
// server prerender must not capture the clock.
let clockValue = "";
const clockListeners = new Set<() => void>();
let clockTimer: ReturnType<typeof setInterval> | null = null;
function subscribeClock(onChange: () => void) {
  clockListeners.add(onChange);
  if (!clockTimer) {
    clockTimer = setInterval(() => {
      const next = toUtcInputValue(new Date());
      if (next !== clockValue) {
        clockValue = next;
        clockListeners.forEach((l) => l());
      }
    }, 10_000);
  }
  return () => {
    clockListeners.delete(onChange);
    if (!clockListeners.size && clockTimer) {
      clearInterval(clockTimer);
      clockTimer = null;
    }
  };
}
function readClock() {
  if (!clockValue) clockValue = toUtcInputValue(new Date());
  return clockValue;
}
const noopSubscribe = () => () => {};

function isEditable(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.getAttribute("role") === "combobox";
}

export function ReceiptsApp() {
  const [examples, setExamples] = useState<Example[] | null>(null);
  const [examplesError, setExamplesError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [exampleId, setExampleId] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<AsOfState>({ mode: "now" });
  const [view, setView] = useState<"edit" | "results">("edit");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const nowValue = useSyncExternalStore(subscribeClock, readClock, () => "");
  const isMac = useSyncExternalStore(
    noopSubscribe,
    () => /Mac|iPhone|iPad|iPod/.test(navigator.platform),
    () => true,
  );
  const isDesktop = useMediaQuery("(min-width: 1024px)");

  const touched = useRef(false);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const pendingFocus = useRef<string | null>(null);
  // Where focus returns when the mobile receipt sheet closes: the mark or list row of the last claim shown.
  const sheetReturn = useRef<{ kind: "mark" | "list"; id: string | null }>({ kind: "mark", id: null });
  const sheetOpen = useRef(false);
  const userSelected = useRef(false);
  const examplesRef = useRef<Example[] | null>(null);
  const runExampleRef = useRef<(ex: Example) => void>(() => {});

  const { state, start, cancel } = useCheck({
    onError: (error, request) => {
      const list = examplesRef.current ?? [];
      toast.error(error.message, {
        description: error.retryable
          ? "Try again, or run one of the pre-checked examples."
          : "The examples are pre-checked and still work.",
        action: error.retryable
          ? { label: "Retry", onClick: () => start(request) }
          : list.length
            ? { label: "Run an example", onClick: () => runExampleRef.current(list[0]) }
            : undefined,
      });
      setAnnouncement(`The check failed. ${error.message}`);
    },
    onDone: (done, { claims }) => {
      if (!claims) {
        toast("No financial figures found in this draft.", {
          description: "Receipts checks numbers such as revenue, EPS, margins, growth and guidance.",
        });
      }
      const c = done.counts;
      setAnnouncement(
        `Check complete. ${c.verified} verified, ${c.wrong} wrong, ${c.unverifiable} unverifiable, ${c.approximate} approximate.`,
      );
    },
  });

  const activeExample = useMemo(
    () => examples?.find((e) => e.id === exampleId && e.text === draft) ?? null,
    [examples, exampleId, draft],
  );

  const loadExample = useCallback(
    (ex: Example) => {
      cancel();
      setDraft(ex.text);
      setExampleId(ex.id);
      setAsOf(ex.as_of ? { mode: "custom", value: toUtcInputValue(new Date(ex.as_of)) } : { mode: "now" });
      setView("edit");
      setSelectedId(null);
    },
    [cancel],
  );

  const run = useCallback(
    (request: CheckRequest) => {
      userSelected.current = false;
      setSelectedId(null);
      setView("results");
      setAnnouncement("Checking the draft.");
      start(request);
    },
    [start],
  );

  const runExample = useCallback(
    (ex: Example) => {
      loadExample(ex);
      run({ text: ex.text, as_of: ex.as_of, context: ex.context, example_id: ex.id });
    },
    [loadExample, run],
  );

  useEffect(() => {
    examplesRef.current = examples;
    runExampleRef.current = runExample;
  });

  // Examples: load once; pre-fill the first one unless the user has already typed.
  useEffect(() => {
    const ac = new AbortController();
    fetchExamples(ac.signal)
      .then((list) => {
        setExamples(list);
        if (list.length && !touched.current) loadExample(list[0]);
      })
      .catch((e: Error) => {
        if (ac.signal.aborted) return;
        setExamples([]);
        setExamplesError(e.message);
        toast.error(e.message, { description: "Refresh in a moment to load the examples." });
      });
    return () => ac.abort();
  }, [loadExample]);

  const streaming = state.phase === "streaming";

  const submit = () => {
    if (streaming) return;
    if (!draft.trim()) {
      toast("Paste a draft or load an example first.");
      return;
    }
    if (draft.length > MAX_DRAFT_CHARS) {
      toast.error(
        `This draft is ${draft.length.toLocaleString("en-US")} characters; the limit is ${MAX_DRAFT_CHARS.toLocaleString("en-US")}.`,
      );
      return;
    }
    let asOfIso: string | null = null;
    if (asOf.mode === "custom") {
      asOfIso = fromUtcInputValue(asOf.value);
      if (!asOfIso) {
        toast.error("Enter a full as-of date and time, or choose Now.");
        return;
      }
    }
    run({ text: draft, as_of: asOfIso, context: activeExample?.context ?? null, example_id: activeExample?.id ?? null });
  };

  const showResults = view === "results" && state.request !== null;
  const claims = showResults ? state.claims : [];
  const selectedClaim = selectedId ? (claims.find((c) => c.id === selectedId) ?? null) : null;
  const selectedIndex = selectedClaim ? claims.indexOf(selectedClaim) : -1;

  const select = useCallback(
    (id: string, focus?: "title") => {
      userSelected.current = true;
      pendingFocus.current = focus ?? null;
      if (!sheetOpen.current) {
        const active = document.activeElement as HTMLElement | null;
        sheetReturn.current.kind = active?.dataset.listId ? "list" : "mark";
      }
      sheetReturn.current.id = id;
      setSelectedId(id);
      const claim = state.claims.find((c) => c.id === id);
      const v = state.verdicts[id];
      if (claim) {
        setAnnouncement(
          v
            ? `Receipt for ${claim.value_text}: ${STATUS_META[v.status].label}. ${reasonText(v.reason)}.`
            : `Receipt for ${claim.value_text}: still checking.`,
        );
      }
    },
    [state.claims, state.verdicts],
  );

  const step = (delta: 1 | -1) => {
    if (!claims.length) return;
    const i = selectedIndex < 0 ? 0 : (selectedIndex + delta + claims.length) % claims.length;
    const next = claims[i].id;
    select(next);
    // Arrowing from a mark in the draft moves focus along, so Tab continues from the new claim.
    if ((document.activeElement as HTMLElement | null)?.dataset.markId) {
      document.querySelector<HTMLElement>(`[data-mark-id="${next}"]`)?.focus();
    }
  };

  const backToList = () => {
    // From inside the panel, focus lands on the claim's list row; from a mark in the draft it stays put.
    const inPanel = document.getElementById(PANEL_ID)?.contains(document.activeElement);
    if (selectedId && inPanel) pendingFocus.current = `list:${selectedId}`;
    setSelectedId(null);
  };

  const edit = () => {
    cancel();
    setView("edit");
    setSelectedId(null);
    requestAnimationFrame(() => document.getElementById("draft")?.focus());
  };

  // Keyboard: ⌘/Ctrl+Enter checks; with a receipt open, ← → step through claims and Esc closes it.
  const keyRef = useRef({ submit, step, backToList, selected: !!selectedClaim, isDesktop });
  useEffect(() => {
    keyRef.current = { submit, step, backToList, selected: !!selectedClaim, isDesktop };
  });
  useEffect(() => {
    // Capture phase, so ⌘↵ on a focused control (the example picker opens on Enter) only submits.
    const onSubmitKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        keyRef.current.submit();
      }
    };
    const onKey = (e: KeyboardEvent) => {
      const k = keyRef.current;
      if (!k.selected || !k.isDesktop || e.defaultPrevented || e.altKey || e.metaKey || e.ctrlKey) return;
      if (isEditable(e.target)) return;
      // Leave keys inside an open picker or dialog to that widget.
      if ((e.target as HTMLElement | null)?.closest?.('[role="listbox"], [role="dialog"], [role="menu"]')) return;
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        k.step(e.key === "ArrowRight" ? 1 : -1);
      } else if (e.key === "Escape") {
        k.backToList();
      }
    };
    window.addEventListener("keydown", onSubmitKey, { capture: true });
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onSubmitKey, { capture: true });
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  // When the check finishes on a wide screen, open the first red flag's receipt.
  useEffect(() => {
    if (state.phase !== "done" || !isDesktop || userSelected.current) return;
    const first = state.claims.find((c) => state.verdicts[c.id]?.status === "wrong");
    if (!first) return;
    const t = setTimeout(() => {
      if (!userSelected.current) setSelectedId((cur) => cur ?? first.id);
    }, 700);
    return () => clearTimeout(t);
  }, [state.phase, state.run, state.claims, state.verdicts, isDesktop]);

  // Focus follows the panel: into the receipt title when opened from the list, back to the list item on return.
  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    pendingFocus.current = null;
    if (target === "title") titleRef.current?.focus();
    else if (target.startsWith("list:")) {
      document.querySelector<HTMLElement>(`[data-list-id="${target.slice(5)}"]`)?.focus();
    }
  }, [selectedId]);

  // Keep the selected claim's mark on screen while stepping through receipts.
  useEffect(() => {
    if (!selectedId || !isDesktop) return;
    const el = document.querySelector<HTMLElement>(`[data-mark-id="${selectedId}"]`);
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.top < 80 || r.bottom > window.innerHeight - 40) {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
    }
  }, [selectedId, isDesktop]);

  const checkedAsOf = state.meta?.as_of ?? state.request?.as_of ?? null;
  const receipt = selectedClaim ? (
    <ReceiptDetail
      claim={selectedClaim}
      verdict={state.verdicts[selectedClaim.id]}
      asOf={checkedAsOf}
      index={selectedIndex}
      total={claims.length}
      onPrev={() => step(-1)}
      onNext={() => step(1)}
      onBack={isDesktop ? backToList : undefined}
      onClose={isDesktop ? undefined : () => setSelectedId(null)}
      titleRef={titleRef}
      titleId={TITLE_ID}
    />
  ) : null;

  const over = draft.length > MAX_DRAFT_CHARS;

  let panel: React.ReactNode;
  if (!showResults) panel = <HowItWorks />;
  else if (isDesktop && receipt) panel = receipt;
  else {
    panel = (
      <div className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="font-serif text-xl font-medium tracking-tight">Claims</h2>
          <span className="text-xs text-muted-foreground">
            {claims.length ? "Select one to see its receipt" : null}
          </span>
        </div>
        {claims.length || streaming ? (
          <ClaimsList
            claims={claims}
            verdicts={state.verdicts}
            selectedId={selectedId}
            onSelect={(id) => select(id, "title")}
            streaming={streaming}
            panelId={PANEL_ID}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            {state.phase === "error"
              ? "No claims were checked."
              : "No financial figures found. Receipts checks numbers such as revenue, EPS, margins, growth and guidance."}
          </p>
        )}
      </div>
    );
  }

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 pt-6 pb-16 sm:px-6 sm:pt-8">
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:gap-10">
        <section aria-label="Draft" className="min-w-0">
          <div
            className={cn(
              "overflow-hidden rounded-xl border border-border bg-card shadow-[0_1px_3px_rgb(0_0_0/0.04)] transition-shadow",
              "has-[#draft:focus-visible]:border-ring/60 has-[#draft:focus-visible]:ring-3 has-[#draft:focus-visible]:ring-ring/25",
            )}
          >
            <div className="flex flex-col gap-4 border-b border-border px-4 py-4 sm:flex-row sm:items-start sm:gap-5 sm:px-6">
              <ExamplePicker
                examples={examples}
                error={examplesError}
                activeId={activeExample?.id ?? null}
                onPick={(id) => {
                  const ex = examples?.find((e) => e.id === id);
                  if (ex) loadExample(ex);
                }}
              />
              <AsOfPicker asOf={asOf} nowValue={nowValue} onChange={setAsOf} />
            </div>

            {showResults && !(state.phase === "error" && !claims.length) ? <SummaryStrip state={state} /> : null}

            {state.phase === "error" && state.error ? (
              <Alert className="rounded-none border-x-0 border-t-0 bg-bad-tint/50 px-4 py-3.5 sm:px-6">
                <TriangleAlertIcon className="text-bad" />
                <AlertTitle className="text-bad">{state.error.message}</AlertTitle>
                <AlertDescription className="text-foreground/80 [&_p:not(:last-child)]:mb-0">
                  <p>
                    {state.error.retryable ? "Try again in a moment. Meanwhile," : "Live checks are paused, but"} the
                    examples are pre-checked and still work:
                  </p>
                  {examples?.length ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {examples.map((ex) => (
                        <Button key={ex.id} size="sm" variant="outline" className="bg-card" onClick={() => runExample(ex)}>
                          {ex.title}
                        </Button>
                      ))}
                    </div>
                  ) : null}
                </AlertDescription>
              </Alert>
            ) : null}

            {showResults ? (
              <div className="px-4 py-5 sm:px-6 sm:py-6">
                <HighlightedDraft
                  text={state.request!.text}
                  claims={claims}
                  verdicts={state.verdicts}
                  selectedId={selectedId}
                  onSelect={(id) => select(id)}
                  panelId={PANEL_ID}
                />
              </div>
            ) : (
              <>
                <label htmlFor="draft" className="sr-only">
                  Draft to check
                </label>
                <Textarea
                  id="draft"
                  value={draft}
                  onChange={(e) => {
                    touched.current = true;
                    setDraft(e.target.value);
                  }}
                  placeholder="Paste a draft with financial figures: an earnings story, a research note, or an AI-written summary."
                  spellCheck
                  aria-invalid={over || undefined}
                  className="proof max-h-[65vh] min-h-60 resize-none overflow-y-auto rounded-none border-0 px-4 py-5 shadow-none focus-visible:ring-0 sm:px-6 sm:py-6 dark:bg-transparent"
                />
              </>
            )}

            <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3 sm:px-6">
              {showResults ? (
                <Button variant="ghost" size="sm" className="-ml-2" onClick={edit}>
                  <PencilLineIcon />
                  Edit draft
                </Button>
              ) : (
                <span className={cn("text-xs tabular-nums", over ? "font-medium text-bad" : "text-muted-foreground")}>
                  {draft.length.toLocaleString("en-US")} / {MAX_DRAFT_CHARS.toLocaleString("en-US")}
                </span>
              )}
              <div className="flex items-center gap-3">
                <KbdGroup className="hidden sm:inline-flex" aria-hidden>
                  <Kbd>{isMac ? "⌘" : "Ctrl"}</Kbd>
                  <Kbd>↵</Kbd>
                </KbdGroup>
                <Button
                  size="lg"
                  className="min-w-28 px-4"
                  onClick={submit}
                  disabled={streaming || over}
                  aria-keyshortcuts={isMac ? "Meta+Enter" : "Control+Enter"}
                >
                  {streaming ? (
                    <>
                      <Loader2Icon className="animate-spin" aria-hidden />
                      Checking…
                    </>
                  ) : (
                    "Check"
                  )}
                </Button>
              </div>
            </div>
          </div>
        </section>

        <aside
          id={isDesktop ? PANEL_ID : undefined}
          aria-label={selectedClaim && isDesktop ? "Receipt" : showResults ? "Claims" : "About Receipts"}
          className="min-w-0 lg:sticky lg:top-6 lg:max-h-[calc(100dvh-3rem)] lg:self-start lg:overflow-y-auto lg:pr-1 lg:pb-4"
        >
          {panel}
        </aside>
      </div>

      <Sheet open={!isDesktop && !!receipt} onOpenChange={(open) => !open && setSelectedId(null)}>
        <SheetContent
          id={PANEL_ID}
          side="bottom"
          showCloseButton={false}
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            sheetOpen.current = true;
            titleRef.current?.focus();
          }}
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            sheetOpen.current = false;
            const { kind, id } = sheetReturn.current;
            if (id) document.querySelector<HTMLElement>(`[data-${kind}-id="${id}"]`)?.focus();
          }}
          className="max-h-[88dvh] gap-0 overflow-y-auto rounded-t-2xl px-4 pt-3 pb-8"
        >
          <div className="mx-auto mb-3 h-1 w-10 shrink-0 rounded-full bg-border" aria-hidden />
          <SheetTitle className="sr-only">Receipt</SheetTitle>
          <SheetDescription className="sr-only">The filing evidence for one claim.</SheetDescription>
          {receipt}
        </SheetContent>
      </Sheet>
    </main>
  );
}
