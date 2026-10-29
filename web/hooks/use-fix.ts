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
export function useFix() {
  const [state, setState] = useState<FixState>({ phase: "idle" });
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback((request: FixRequest) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({ phase: "running", stage: "checking" });
    const active = () => !ac.signal.aborted && abortRef.current === ac;
    void streamFix(request, {
      onProgress: (stage) => { if (active()) setState({ phase: "running", stage }); },
      onResult: (result) => { if (active()) setState({ phase: "done", result }); },
      onError: (error) => { if (active()) setState({ phase: "error", error }); },
    }, ac.signal);
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);
  return { state, start };
}
