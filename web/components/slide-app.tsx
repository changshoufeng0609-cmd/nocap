"use client";

import { FileImageIcon, LandmarkIcon, Loader2Icon, ScanLineIcon, UploadIcon, XIcon, ZoomInIcon, ZoomOutIcon } from "lucide-react";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";

import { AsOfPicker, type AsOfState } from "@/components/draft-controls";
import { ReceiptDetail } from "@/components/receipt-detail";
import { StatusChip, StatusGlyph, statusLabel } from "@/components/status";
import { Button } from "@/components/ui/button";
import { useSlide } from "@/hooks/use-slide";
import { fetchHealth, MOCK } from "@/lib/api";
import { DISPLAY_STATUSES, STATUS_META, displayCounts, displayStatus, reasonText } from "@/lib/claims";
import { formatDay, formatErrorCost, formatCost, formatLatency, formatUtcMinutes, fromUtcInputValue, toUtcInputValue } from "@/lib/format";
import samples from "@/lib/samples.json";
import { MAX_SLIDE_BYTES, slideDataUrl, validateSlideImage } from "@/lib/slide-image";
import type { SlideClaim, SlideStage } from "@/lib/types";
import { cn } from "@/lib/utils";

// A real SEC slide whose live run is recorded: the server replays it at $0, even while live requests are paused.
const SAMPLE = samples.slides[0];

const STAGES: Record<SlideStage, string> = {
  segmenting: "SAM 3.1 is locating charts and tables…",
  reading: "Muse Spark is reading the financial labels…",
  verifying: "Checking the readings against SEC filings…",
};
const BOX_STYLE = {
  verified: "border-ok-line bg-ok-tint/15",
  wrong: "border-bad-line bg-bad-tint/20",
  look_ahead: "border-time-line bg-time-tint/20",
  unverifiable: "border-unk-line border-dashed bg-unk-tint/20",
  approximate: "border-approx-line border-dotted bg-approx-tint/20",
  pending: "border-foreground/50 bg-muted/20",
};
interface Upload { name: string; url: string; dataUrl: string; width: number; height: number }

export function SlideApp() {
  const [upload, setUpload] = useState<Upload | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [inputError, setInputError] = useState<string | null>(null);
  const [context, setContext] = useState("");
  const [asOf, setAsOf] = useState<AsOfState>({ mode: "now" });
  const [nowValue, setNowValue] = useState("");
  const [companies, setCompanies] = useState<string[]>([]);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showRegions, setShowRegions] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const [sample, setSample] = useState<typeof SAMPLE | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadVersion = useRef(0);
  const previewUrl = useRef<string | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const focusReceipt = useRef(false);
  const { state, start, reset } = useSlide();
  const running = state.phase === "running";
  const selectedIndex = state.claims.findIndex((claim) => claim.id === selectedId);
  const selected = state.claims[selectedIndex];
  const counts = displayCounts(Object.values(state.verdicts));
  const asOfIso = asOf.mode === "custom" ? fromUtcInputValue(asOf.value) : null;
  const groups = new Map<string, { claim: SlideClaim; indices: number[]; ids: string[] }>();
  state.claims.forEach((claim, index) => {
    const key = claim.box.join(",");
    const group = groups.get(key);
    if (group) { group.indices.push(index + 1); group.ids.push(claim.id); if (claim.id === selectedId) group.claim = claim; }
    else groups.set(key, { claim, indices: [index + 1], ids: [claim.id] });
  });

  useEffect(() => {
    const ac = new AbortController();
    void fetchHealth(ac.signal).then((health) => { if (!ac.signal.aborted && health) { setCompanies(health.companies); setSnapshot(health.data_snapshot || null); } });
    const updateClock = () => setNowValue(toUtcInputValue(new Date()));
    const first = setTimeout(updateClock, 0);
    const timer = setInterval(updateClock, 60_000);
    return () => { ac.abort(); clearTimeout(first); clearInterval(timer); };
  }, []);
  useEffect(() => () => {
    uploadVersion.current++;
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);
  useEffect(() => {
    if (!focusReceipt.current || !selected) return;
    focusReceipt.current = false;
    titleRef.current?.focus({ preventScroll: true });
    titleRef.current?.scrollIntoView({ block: "nearest", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }, [selected]);

  function invalidate() { reset(); setSelectedId(null); setInputError(null); }
  function clear(clearInput = true) {
    uploadVersion.current++;
    invalidate();
    setPreparing(false);
    setUpload(null);
    setSample(null);
    setZoomed(false);
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    if (clearInput && fileInput.current) fileInput.current.value = "";
  }
  async function loadFile(file: File) {
    clear(false);
    const version = uploadVersion.current;
    if (file.size > MAX_SLIDE_BYTES) { setInputError("Choose a PNG or JPEG no larger than 2 MiB."); return; }
    setPreparing(true);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const dimensions = validateSlideImage(bytes, file.type);
      if (version !== uploadVersion.current) return;
      const url = URL.createObjectURL(file);
      previewUrl.current = url;
      setUpload({ name: file.name, url, dataUrl: slideDataUrl(bytes, file.type), ...dimensions });
    } catch (error) {
      if (version === uploadVersion.current) setInputError(error instanceof Error ? error.message : "The image could not be opened. Try another PNG or JPEG.");
    } finally {
      if (version === uploadVersion.current) setPreparing(false);
    }
  }
  async function loadSample() {
    clear();
    const version = uploadVersion.current;
    setPreparing(true);
    try {
      const response = await fetch(SAMPLE.image);
      if (!response.ok) throw new Error("The sample slide could not be loaded. Try again.");
      const bytes = new Uint8Array(await response.arrayBuffer());
      const type = SAMPLE.image.endsWith(".png") ? "image/png" : "image/jpeg";
      const dimensions = validateSlideImage(bytes, type);
      if (version !== uploadVersion.current) return;
      const url = URL.createObjectURL(new Blob([bytes], { type }));
      previewUrl.current = url;
      const dataUrl = slideDataUrl(bytes, type);
      setUpload({ name: SAMPLE.title, url, dataUrl, ...dimensions });
      setSample(SAMPLE);
      setContext(SAMPLE.context);
      setAsOf({ mode: "now" });
      start({ image_data_url: dataUrl, context: SAMPLE.context, as_of: SAMPLE.as_of });
    } catch (error) {
      if (version === uploadVersion.current) setInputError(error instanceof Error ? error.message : "The sample slide could not be loaded. Try again.");
    } finally {
      if (version === uploadVersion.current) setPreparing(false);
    }
  }
  function check() {
    if (!upload || running || preparing) return;
    if (asOf.mode === "custom" && (!asOfIso || !Number.isFinite(Date.parse(asOfIso)))) { setInputError("Enter a complete as-of date and time, or choose Now."); return; }
    setSelectedId(null);
    setInputError(null);
    start({ image_data_url: upload.dataUrl, context: context.trim() || null, as_of: asOfIso });
  }
  function select(id: string) { focusReceipt.current = true; setSelectedId(id); }
  function step(delta: number) {
    if (state.claims.length) select(state.claims[(selectedIndex + delta + state.claims.length) % state.claims.length].id);
  }
  const dimensions = state.meta?.image ?? upload;

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 pt-5 pb-16 sm:px-6">
      <div className="mb-5 flex flex-col gap-2">
        <h2 className="font-serif text-3xl font-medium tracking-tight">Check a slide</h2>
        <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">Upload an earnings chart or table. SAM locates the regions, Muse Spark reads the numbers, and code checks each reading against SEC filings.</p>
        {companies.length ? <p className="text-xs leading-relaxed text-muted-foreground">Evidence: {companies.length} S&amp;P 500 companies{snapshot ? ` in SEC data as of ${formatDay(snapshot)}` : ""}. An upload does not add new filing evidence.</p> : null}
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_23rem] lg:gap-8">
        <section aria-label="Slide input and readings" className="min-w-0 overflow-hidden rounded-xl border border-border bg-card">
          <div className="space-y-4 border-b border-border p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <label htmlFor="slide-file" className="mb-1 block text-sm font-medium">Slide image</label>
                <p id="slide-file-hint" className="text-xs text-muted-foreground">PNG or JPEG · up to 2 MiB · 32–4,096 px per side · up to 8 MP</p>
              </div>
              {upload ? <Button variant="ghost" size="sm" onClick={() => clear()}><XIcon />Remove image</Button> : null}
            </div>
            <input ref={fileInput} id="slide-file" type="file" accept="image/png,image/jpeg" aria-describedby="slide-file-hint" onChange={(event) => { const file = event.target.files?.[0]; if (file) void loadFile(file); }} className="block w-full min-w-0 rounded-lg border border-input text-sm text-muted-foreground file:mr-3 file:cursor-pointer file:rounded-l-lg file:border-0 file:bg-muted file:px-3 file:py-2.5 file:font-medium file:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring" />
            <div className="flex flex-col gap-1.5">
              <label htmlFor="slide-context" className="text-xs font-medium text-muted-foreground">Company and period <span className="font-normal">(optional if visible on the image)</span></label>
              <input id="slide-context" value={context} maxLength={300} onChange={(event) => { invalidate(); setContext(event.target.value); }} placeholder="Bank of America, Q2 2026; GAAP unless labelled otherwise" className="h-10 w-full rounded-lg border border-input bg-transparent px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" />
            </div>
            <AsOfPicker asOf={asOf} nowValue={nowValue} onChange={(next) => { invalidate(); setAsOf(next); }} />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">On check, the image is sent to Meta for reading. NoCap does not save the upload.</p>
              <Button onClick={check} disabled={!upload || running || preparing || MOCK} size="lg" className="min-h-10">
                {running || preparing ? <Loader2Icon className="animate-spin" aria-hidden /> : <ScanLineIcon aria-hidden />}
                {preparing ? "Opening image…" : running ? "Checking slide…" : "Check slide"}
              </Button>
            </div>
            {MOCK ? <p className="text-xs text-muted-foreground">Slide checks require the live NoCap API. Start the app without mock mode to use this feature.</p> : null}
            {inputError ? <p role="alert" className="text-sm text-bad">{inputError}</p> : null}
          </div>

          {running ? (
            <div className="space-y-2 border-b border-border bg-muted/25 px-4 py-3 text-sm" role="status">
              <div className="flex items-center gap-2">
                <Loader2Icon className="size-4 animate-spin" aria-hidden />
                <span className="font-medium">{STAGES[state.stage]}</span>
                <Button variant="ghost" size="sm" className="ml-auto" onClick={invalidate}>Cancel</Button>
              </div>
              <div className="grid grid-cols-3 gap-1.5 font-mono text-[0.68rem]" aria-hidden>
                {([
                  ["segmenting", "01 · SAM 3.1 Regions"],
                  ["reading", "02 · Muse Spark OCR"],
                  ["verifying", "03 · SEC Verifier"],
                ] as const).map(([key, label]) => (
                  <span
                    key={key}
                    className={cn(
                      "rounded border px-2 py-1 text-center transition-colors",
                      state.stage === key
                        ? "border-foreground/50 bg-card font-semibold text-foreground shadow-2xs"
                        : "border-border/60 bg-background/40 text-muted-foreground",
                    )}
                  >
                    {label}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
          {state.error ? <div role="alert" className="space-y-2 border-b border-border bg-bad-tint/40 p-4 text-sm"><p className="font-medium text-bad">{state.error.message}</p><p className="text-xs text-muted-foreground">{formatErrorCost(state.error)}</p><p className="text-muted-foreground">{state.claims.length ? "The check is incomplete. Any readings below are partial results." : "No complete slide check is available."}</p>{state.error.retryable ? <Button variant="outline" size="sm" onClick={check}>Retry slide check</Button> : null}</div> : null}
          {state.done ? (
            <div className="space-y-2 border-b border-border px-4 py-3" role="status">
              <p className="text-sm font-medium">{state.claims.length ? `${state.claims.length} extracted readings checked` : "No financial readings found"}</p>
              <ul className="flex flex-wrap gap-x-4 gap-y-1.5" aria-label="Slide verdict counts">
                {DISPLAY_STATUSES.filter((status) => status !== "look_ahead" || counts[status] > 0).map((status) => <li key={status} className="flex items-center gap-1 text-xs"><StatusGlyph status={status} size="sm" /><span className="font-semibold tabular-nums">{counts[status]}</span><span className="text-muted-foreground">{STATUS_META[status].label.toLowerCase()}</span></li>)}
              </ul>
              {/* What the recorded sample's ? mean; only its exact recorded replay carries the note. */}
              {sample?.note && state.meta?.cached ? <p className="max-w-3xl text-xs leading-relaxed text-foreground/80">{sample.note}</p> : null}
              <p className="text-xs text-muted-foreground">{state.meta?.cached ? <>Recorded live run{state.meta.recorded_utc ? ` (${formatUtcMinutes(state.meta.recorded_utc)})` : ""} · $0 this replay · original run: {formatLatency(state.done.latency_s)} / {state.done.cost_is_estimate ? "estimated " : ""}{formatCost(state.done.cost_usd)}, {state.done.model_calls} model calls</> : <>{formatLatency(state.done.latency_s)} · {state.done.cost_is_estimate ? "estimated " : ""}{formatCost(state.done.cost_usd)} · {state.done.model_calls} model calls</>} · As of {state.meta?.as_of ? formatUtcMinutes(state.meta.as_of) : "the snapshot's latest filings"}</p>
              {state.done.warnings.map((warning, index) => <p key={index} className="text-xs leading-relaxed text-muted-foreground">{warning}</p>)}
            </div>
          ) : null}

          {upload && dimensions ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/20 px-3 py-2.5">
                {sample ? <p className="max-w-[75%] text-xs leading-relaxed text-muted-foreground"><span className="font-medium text-foreground/80">{sample.title}</span> · real SEC filing: {sample.source.form}, accession {sample.source.accession}, accepted {formatUtcMinutes(sample.source.accepted_utc)} · <a className="underline underline-offset-4" href={sample.source.url} target="_blank" rel="noopener noreferrer">View on SEC</a> · {sample.attribution}</p> : <p className="max-w-[65%] truncate text-xs text-muted-foreground" title={upload.name}>{upload.name}</p>}
                <div className="flex flex-wrap items-center gap-2">
                  {state.regions.length ? (
                    <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground/85 shadow-2xs hover:bg-muted/60">
                      <input type="checkbox" checked={showRegions} onChange={(e) => setShowRegions(e.target.checked)} className="accent-foreground" />
                      Show chart/table regions ({state.regions.filter((r) => r.source === "sam").length} SAM, {state.regions.filter((r) => r.source === "spark").length} Spark)
                    </label>
                  ) : null}
                  <Button size="sm" variant="ghost" aria-pressed={zoomed} onClick={() => setZoomed(!zoomed)}>{zoomed ? <ZoomOutIcon /> : <ZoomInIcon />}{zoomed ? "Fit slide" : "Zoom in"}</Button>
                </div>
              </div>
              <div className="max-w-full overflow-x-auto bg-muted/30 p-3" tabIndex={zoomed ? 0 : undefined} aria-label={zoomed ? "Enlarged slide; scroll horizontally to inspect" : "Slide preview with extracted readings"}>
                <div className={cn("relative", zoomed ? "w-[1100px] max-w-none" : "w-full")} style={{ aspectRatio: `${dimensions.width} / ${dimensions.height}` }}>
                  <Image src={upload.url} alt="Uploaded slide. Extracted readings are listed below and can be selected on the image." width={dimensions.width} height={dimensions.height} unoptimized className="block h-full w-full rounded-sm" onLoad={(event) => {
                    const image = event.currentTarget;
                    setUpload((previous) => previous?.url === upload.url ? { ...previous, width: image.naturalWidth, height: image.naturalHeight } : previous);
                  }} onError={() => { if (previewUrl.current === upload.url) { clear(); setInputError("The image could not be displayed. Export a new PNG or JPEG and try again."); } }} />
                  {(showRegions || !state.claims.length) ? state.regions.map((region) => <div key={region.id} aria-hidden className="pointer-events-none absolute border border-dashed border-foreground/50" style={{ left: `${region.box[0] / 10}%`, top: `${region.box[1] / 10}%`, width: `${(region.box[2] - region.box[0]) / 10}%`, height: `${(region.box[3] - region.box[1]) / 10}%` }}><span className="absolute top-0 left-0 max-w-full truncate bg-card/90 px-1 text-[10px] text-foreground">{region.label} · {region.source === "sam" ? "SAM" : "Spark"}</span></div>) : null}
                  {[...groups.values()].map(({ claim, indices, ids }) => {
                    const status = displayStatus(state.verdicts[claim.id]);
                    return <button key={ids[0]} type="button" data-slide-claim={claim.id} aria-label={`Reading ${indices.join(" and ")}: ${claim.value_text}, ${statusLabel(status)}. Open filing receipt.`} aria-controls="slide-receipt-panel" aria-pressed={ids.includes(selectedId ?? "")} title={`${claim.value_text} · ${statusLabel(status)}`} onClick={() => select(ids[(ids.indexOf(selectedId ?? "") + 1) % ids.length])} className={cn("absolute min-h-4 min-w-4 rounded-sm border-2 text-left focus-visible:z-20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring", BOX_STYLE[status], ids.includes(selectedId ?? "") && "z-10 ring-2 ring-foreground ring-offset-2 ring-offset-card")} style={{ left: `${claim.box[0] / 10}%`, top: `${claim.box[1] / 10}%`, width: `${(claim.box[2] - claim.box[0]) / 10}%`, height: `${(claim.box[3] - claim.box[1]) / 10}%` }}><span className="absolute -top-3 -left-2 flex min-h-5 min-w-5 items-center justify-center rounded-full border border-border bg-card px-1 font-mono text-[10px] font-semibold text-foreground shadow-sm" aria-hidden>{indices.join("·")}</span></button>;
                  })}
                </div>
              </div>
              <p className="border-t border-border px-4 py-3 text-xs leading-relaxed text-muted-foreground">Boxes mark model readings and may be imprecise. Select a numbered box or a reading to see the filing receipt. Use Zoom in for small labels.</p>
            </>
          ) : (
            <div className="m-4 flex min-h-64 flex-col items-center justify-center gap-3.5 rounded-xl border border-dashed border-border bg-muted/20 p-8 text-center text-muted-foreground sm:m-5">
              <div className="flex size-12 items-center justify-center rounded-full border border-border bg-card shadow-2xs">
                <FileImageIcon className="size-6 stroke-1.5 text-foreground/75" aria-hidden />
              </div>
              <p className="font-serif text-xl text-foreground">Give every label a receipt.</p>
              <p className="max-w-sm text-sm leading-relaxed">Choose a clear screenshot of one earnings chart or table, including its title, units and period.</p>
              <div className="flex flex-wrap justify-center gap-2.5 pt-1">
                <Button variant="default" onClick={() => void loadSample()} disabled={preparing || MOCK}><LandmarkIcon />Try a real SEC slide</Button>
                <Button variant="outline" className="bg-card" onClick={() => fileInput.current?.click()}><UploadIcon />Choose slide image</Button>
              </div>
              <p className="max-w-sm text-xs leading-relaxed">The sample is page 9 of Bank of America&apos;s 2Q26 earnings presentation, filed with the SEC. Its live SAM 3.1 and Muse Spark run is recorded and replays at $0.</p>
            </div>
          )}
        </section>

        <aside id="slide-receipt-panel" aria-label="Slide filing receipts" className="min-w-0 lg:sticky lg:top-6">
          {selected ? (
            <>
              <p className="mb-3 rounded-lg bg-muted px-3 py-2 text-xs leading-relaxed text-muted-foreground"><strong className="text-foreground">Reading {selectedIndex + 1} · {selected.reading === "axis_estimate" ? "estimated from an axis" : "printed label"}</strong><span className="mt-1 block">{selected.reading === "axis_estimate" ? "Axis estimates can receive only approximate or unverifiable verdicts; they cannot establish a numerical error." : "Confirm this transcription against the image. The verdict checks the extracted value, not the accuracy of the visual reading."}</span><span className="mt-2 block font-mono text-foreground">{selected.span_text}</span></p>
              <ReceiptDetail claim={selected} verdict={state.verdicts[selected.id]} asOf={state.meta?.as_of ?? asOfIso} index={selectedIndex} total={state.claims.length} onPrev={() => step(-1)} onNext={() => step(1)} onBack={() => { setSelectedId(null); requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-slide-list="${selected.id}"]`)?.focus()); }} titleRef={titleRef} titleId="slide-receipt-title" />
            </>
          ) : (
            <>
              <h3 className="font-serif text-xl font-medium">{state.claims.length ? "Extracted readings" : "What the slide check confirms"}</h3>
              {state.claims.length ? <><p className="mt-1 text-xs text-muted-foreground">Select a reading to inspect its SEC evidence.</p><ol className="mt-3 divide-y divide-border">{state.claims.map((claim, index) => <li key={claim.id}><button type="button" data-slide-list={claim.id} onClick={() => select(claim.id)} className="flex min-h-16 w-full items-start gap-2 rounded-md px-2 py-3 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"><span className="mt-1 font-mono text-xs text-muted-foreground">{index + 1}</span><span className="min-w-0 flex-1 space-y-1"><span className="block font-mono text-sm font-medium">{claim.value_text}</span><span className="block text-xs text-muted-foreground">{[claim.entity, claim.metric_text, claim.period_text, claim.basis].filter(Boolean).join(" · ")}</span><span className="block text-[11px] text-muted-foreground">{state.verdicts[claim.id] ? reasonText(state.verdicts[claim.id].reason) : "Checking…"} · {claim.reading === "axis_estimate" ? "axis estimate, cannot verify exactly" : "printed label"}</span></span><StatusChip status={displayStatus(state.verdicts[claim.id])} className="mt-0.5 shrink-0" /></button></li>)}</ol></> : <div className="mt-4 space-y-4 text-sm leading-relaxed text-muted-foreground"><p><strong className="text-foreground">Printed labels.</strong> Each legible number is compared with the filing for its company, period, scale and accounting basis.</p><p><strong className="text-foreground">Axis estimates.</strong> A value inferred from a chart can be approximate or unverifiable; it can never earn a verified or wrong verdict.</p><p><strong className="text-foreground">Timing.</strong> The as-of cutoff checks when the filing became public, just as it does for a draft.</p></div>}
              <p className="mt-5 rounded-lg border border-border bg-muted/40 p-3.5 text-xs leading-relaxed text-muted-foreground">The uploaded image is a claim to check, not filing evidence. Vision can miss or misread figures. Confirm each transcription and review its receipt before publishing.</p>
            </>
          )}
        </aside>
      </div>
    </main>
  );
}
