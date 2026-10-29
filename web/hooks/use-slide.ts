"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { streamSlide } from "@/lib/slides";
import type { ErrorEvent, SlideClaim, SlideDone, SlideMeta, SlideRegion, SlideRequest, SlideStage, Verdict } from "@/lib/types";

export interface SlideState {
  phase: "idle" | "running" | "done" | "error";
  stage: SlideStage;
  meta: SlideMeta | null;
  regions: SlideRegion[];
  claims: SlideClaim[];
  verdicts: Record<string, Verdict>;
  done: SlideDone | null;
  error: ErrorEvent | null;
}
const initial: SlideState = { phase: "idle", stage: "segmenting", meta: null, regions: [], claims: [], verdicts: {}, done: null, error: null };

/** Each new image/context/cutoff invalidates every old event, including an in-flight fetch. */
export function useSlide() {
  const [state, setState] = useState<SlideState>(initial);
  const abortRef = useRef<AbortController | null>(null);
  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState(initial);
  }, []);
  const start = useCallback((request: SlideRequest) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({ ...initial, phase: "running" });
    const update = (fn: (previous: SlideState) => SlideState) => {
      if (!ac.signal.aborted && abortRef.current === ac) setState(fn);
    };
    void streamSlide(request, {
      onMeta: (meta) => update((s) => ({ ...s, meta })),
      onProgress: (stage) => update((s) => ({ ...s, stage })),
      onRegion: (region) => update((s) => ({ ...s, regions: [...s.regions, region] })),
      onClaim: (claim) => update((s) => ({ ...s, claims: [...s.claims, claim] })),
      onVerdict: (verdict) => update((s) => ({ ...s, verdicts: { ...s.verdicts, [verdict.claim_id]: verdict } })),
      onDone: (done) => update((s) => ({ ...s, phase: "done", done })),
      onError: (error) => update((s) => ({ ...s, phase: "error", error })),
    }, ac.signal);
  }, []);
  useEffect(() => () => abortRef.current?.abort(), []);
  return { state, start, reset };
}
