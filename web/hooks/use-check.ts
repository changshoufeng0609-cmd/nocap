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
  if (a.type === "cancel") return state.phase === "streaming" ? { ...state, phase: "idle" } : state;
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

export function useCheck(callbacks: Callbacks = {}) {
  const [state, dispatch] = useReducer(reducer, initial);
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef(0);
  const claimCount = useRef(0);
  const cb = useRef(callbacks);
  useEffect(() => {
    cb.current = callbacks;
  });

  const start = useCallback((request: CheckRequest) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const run = ++runRef.current;
    const receivedVerdicts = new Map<string, Verdict>();
    claimCount.current = 0;
    dispatch({ type: "start", run, request });
    void streamCheck(
      request,
      {
        onMeta: (meta) => dispatch({ type: "meta", run, meta }),
        onClaim: (claim) => {
          claimCount.current += 1;
          dispatch({ type: "claim", run, claim });
        },
        onVerdict: (claimId, verdict) => {
          receivedVerdicts.set(claimId, verdict);
          dispatch({ type: "verdict", run, claimId, verdict });
        },
        onDone: (done) => {
          dispatch({ type: "done", run, done });
          cb.current.onDone?.(done, { claims: claimCount.current, verdicts: [...receivedVerdicts.values()] });
        },
        onError: (error) => {
          dispatch({ type: "error", run, error });
          cb.current.onError?.(error, request);
        },
      },
      ac.signal,
    );
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: "cancel" });
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { state, start, cancel };
}
