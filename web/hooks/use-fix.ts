"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { streamFix } from "@/lib/api";
import type { ErrorEvent, FixRequest, FixResult, FixStage } from "@/lib/types";

type FixState =
  | { phase: "idle" }
  | { phase: "running"; stage: FixStage }
  | { phase: "done"; result: FixResult }
  | { phase: "error"; error: ErrorEvent };

/** Mounted for exactly one completed check. Unmounting cancels delivery of every old event. */
export function useFix(enabled = true) {
  const [state, setState] = useState<FixState>({ phase: "idle" });
  const abortRef = useRef<AbortController | null>(null);
  const enabledRef = useRef(enabled);

  const start = useCallback((request: FixRequest) => {
    if (!enabledRef.current) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({ phase: "running", stage: "checking" });
    const active = () => enabledRef.current && !ac.signal.aborted && abortRef.current === ac;
    void streamFix(request, {
      onProgress: (stage) => { if (active()) setState({ phase: "running", stage }); },
      onResult: (result) => { if (active()) { abortRef.current = null; setState({ phase: "done", result }); } },
      onError: (error) => { if (active()) { abortRef.current = null; setState({ phase: "error", error }); } },
    }, ac.signal);
  }, []);

  useEffect(() => {
    enabledRef.current = enabled;
    return () => {
      enabledRef.current = false;
      abortRef.current?.abort();
      abortRef.current = null;
      setState((previous) => previous.phase === "running" ? { phase: "idle" } : previous);
    };
  }, [enabled]);
  return { state, start };
}
