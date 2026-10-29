"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import { streamCheck } from "@/lib/api";
import { byDocumentOrder } from "@/lib/claims";
import type { CheckRequest, Claim, DoneEvent, ErrorEvent, MetaEvent, Verdict } from "@/lib/types";

export type Phase = "idle" | "streaming" | "done" | "error";

export interface CheckState {
  phase: Phase;
  run: number;
  request: CheckRequest | null;
  meta: MetaEvent | null;
  /** In document order, whatever order the passages finish in. */
  claims: Claim[];
  verdicts: Record<string, Verdict>;
  done: DoneEvent | null;
  error: ErrorEvent | null;
}

type Action =
  | { type: "start"; run: number; request: CheckRequest }
  | { type: "meta"; run: number; meta: MetaEvent }
  | { type: "claim"; run: number; claim: Claim }
  | { type: "verdict"; run: number; claimId: string; verdict: Verdict }
  | { type: "done"; run: number; done: DoneEvent }
  | { type: "error"; run: number; error: ErrorEvent }
  | { type: "cancel" };

const initial: CheckState = {
  phase: "idle",
  run: 0,
  request: null,
  meta: null,
  claims: [],
  verdicts: {},
  done: null,
  error: null,
};

function reducer(state: CheckState, a: Action): CheckState {
  if (a.type === "start") return { ...initial, phase: "streaming", run: a.run, request: a.request };
  // An interrupted check keeps only claims that already have verdicts: none can stay "Checking".
  if (a.type === "cancel")
    return state.phase === "streaming"
      ? { ...state, phase: "idle", claims: state.claims.filter((c) => c.id in state.verdicts) }
      : state;
  if (a.run !== state.run) return state; // an event from a superseded run
  switch (a.type) {
    case "meta":
      return { ...state, meta: a.meta };
    case "claim": {
      const claims = [...state.claims.filter((c) => c.id !== a.claim.id), a.claim].sort(byDocumentOrder);
      return { ...state, claims };
    }
    case "verdict":
      return { ...state, verdicts: { ...state.verdicts, [a.claimId]: a.verdict } };
    case "done":
      return { ...state, phase: "done", done: a.done };
    case "error":
      return { ...state, phase: "error", error: a.error };
  }
}

interface Callbacks {
  onDone?: (done: DoneEvent, state: { claims: number; verdicts: Verdict[] }) => void;
  onError?: (error: ErrorEvent, request: CheckRequest) => void;
}

export function useCheck(callbacks: Callbacks = {}, enabled = true) {
  const [state, dispatch] = useReducer(reducer, initial);
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef(0);
  const enabledRef = useRef(enabled);
  const cb = useRef(callbacks);
  useEffect(() => {
    cb.current = callbacks;
  });

  const start = useCallback((request: CheckRequest) => {
    if (!enabledRef.current) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const run = ++runRef.current;
    const active = () => enabledRef.current && !ac.signal.aborted && abortRef.current === ac && runRef.current === run;
    const receivedClaims = new Set<string>();
    const receivedVerdicts = new Map<string, Verdict>();
    dispatch({ type: "start", run, request });
    void streamCheck(
      request,
      {
        onMeta: (meta) => { if (active()) dispatch({ type: "meta", run, meta }); },
        onClaim: (claim) => {
          if (!active()) return;
          receivedClaims.add(claim.id);
          dispatch({ type: "claim", run, claim });
        },
        onVerdict: (claimId, verdict) => {
          if (!active()) return;
          receivedVerdicts.set(claimId, verdict);
          dispatch({ type: "verdict", run, claimId, verdict });
        },
        onDone: (done) => {
          if (!active()) return;
          abortRef.current = null;
          dispatch({ type: "done", run, done });
          cb.current.onDone?.(done, { claims: receivedClaims.size, verdicts: [...receivedVerdicts.values()] });
        },
        onError: (error) => {
          if (!active()) return;
          abortRef.current = null;
          dispatch({ type: "error", run, error });
          cb.current.onError?.(error, request);
        },
      },
      ac.signal,
    );
  }, []);

  const cancel = useCallback(() => {
    ++runRef.current;
    abortRef.current?.abort();
    abortRef.current = null;
    dispatch({ type: "cancel" });
  }, []);

  useEffect(() => {
    enabledRef.current = enabled;
    return () => {
      enabledRef.current = false;
      cancel();
    };
  }, [enabled, cancel]);

  return { state, start, cancel };
}
