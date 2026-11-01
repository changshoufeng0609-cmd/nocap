"use client";

import { Fragment, useMemo, useState, type ReactNode } from "react";

import { PendingDots } from "@/components/status";
import {
  STATUS_META,
  correction,
  displayStatus,
  layoutDraft,
  reasonText,
  valueIsWrong,
  worst,
  type DisplayStatus,
  type Segment,
} from "@/lib/claims";
import type { Claim, Verdict } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  text: string;
  claims: Claim[];
  verdicts: Record<string, Verdict>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  panelId: string;
}

export function markLabel(claim: Claim, verdict: Verdict | undefined): string {
  if (!verdict) return `${claim.value_text}, ${claim.metric_text}: checking.`;
  const fix = correction(claim, verdict);
  const filed = fix && verdict.reason !== "look_ahead" ? ` Filed figure: ${fix}.` : "";
  return `${claim.value_text}, ${claim.metric_text}: ${STATUS_META[displayStatus(verdict)].label}. ${reasonText(verdict.reason)}.${filed}`;
}

/** The submitted draft with every claim underlined in place and a verdict mark after its number. */
export function HighlightedDraft({ text, claims, verdicts, selectedId, onSelect, panelId }: Props) {
  const layout = useMemo(() => layoutDraft(text, claims), [text, claims]);
  const byId = useMemo(() => new Map(claims.map((c) => [c.id, c])), [claims]);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const statusOf = (id: string): DisplayStatus => displayStatus(verdicts[id]);

  // Under the pointer, the most specific claim wins (shortest span), then the most severe.
  const primary = (ids: string[]) =>
    [...ids].sort((a, b) => {
      const d = (layout.spanLength.get(a) ?? 0) - (layout.spanLength.get(b) ?? 0);
      if (d) return d;
      const sa = statusOf(a);
      const sb = statusOf(b);
      return sa === sb ? 0 : worst([sa, sb]) === sa ? -1 : 1;
    })[0];

  const marksAt = (pos: number): ReactNode[] => {
    const marks = markButtons(pos);
    // Several claims on one number (GAAP and non-GAAP) keep their own buttons but read as one unit.
    return marks.length > 1 ? [<span key={`marks-${pos}`} className="claim-mark-group">{marks}</span>] : marks;
  };

  const markButtons = (pos: number): ReactNode[] =>
    (layout.marks.get(pos) ?? []).map((id) => {
      const claim = byId.get(id);
      if (!claim) return null;
      const verdict = verdicts[id];
      const status = displayStatus(verdict);
      const fix = verdict ? correction(claim, verdict) : null;
      const selected = selectedId === id;
      return (
        <button
          key={`mark-${id}`}
          type="button"
          className="claim-mark"
          data-status={status}
          data-selected={selected || undefined}
          data-mark-id={id}
          aria-label={`${markLabel(claim, verdict)} Open its receipt.`}
          aria-expanded={selected}
          aria-controls={panelId}
          onClick={() => onSelect(id)}
          onMouseEnter={() => setHoverId(id)}
          onMouseLeave={() => setHoverId(null)}
          onFocus={() => setHoverId(id)}
          onBlur={() => setHoverId(null)}
        >
          <span className="claim-mark-glyph" aria-hidden>
            {status === "pending" ? <PendingDots /> : STATUS_META[status].glyph}
          </span>
          {fix ? (
            <span className="claim-mark-fix" aria-hidden>
              {fix}
            </span>
          ) : null}
        </button>
      );
    });

  /** Text [a, b) of a segment, styled as its claims dictate. */
  const piece = (seg: Segment, a: number, b: number): ReactNode => {
    if (a >= b) return null;
    const slice = text.slice(a, b);
    if (!seg.covering.length) return <Fragment key={`t${a}`}>{slice}</Fragment>;
    const isValue = seg.valueOf.length > 0;
    const owners = isValue ? seg.valueOf : seg.covering;
    const status = worst(owners.map(statusOf));
    const target = primary(owners);
    const strike = isValue && seg.valueOf.some((id) => valueIsWrong(verdicts[id]));
    return (
      <span
        key={`s${a}`}
        className={cn("claim-span", isValue && "claim-value")}
        data-status={status}
        data-strike={strike || undefined}
        data-selected={(selectedId && seg.covering.includes(selectedId)) || undefined}
        data-hover={(hoverId && seg.covering.includes(hoverId)) || undefined}
        onClick={() => onSelect(target)}
        onMouseEnter={() => setHoverId(target)}
        onMouseLeave={() => setHoverId(null)}
      >
        {slice}
      </span>
    );
  };

  // A mark never wraps away from its number: the value's last word, its marks and any punctuation
  // right after them form one unbreakable unit.
  const nodes: ReactNode[] = [marksAt(0)];
  const segs = layout.segments;
  let carry = 0;
  for (let i = 0; i < segs.length; i++) {
    const seg = segs[i];
    const start = seg.start + carry;
    carry = 0;
    const marks = marksAt(seg.end);
    if (!marks.length) {
      nodes.push(piece(seg, start, seg.end));
      continue;
    }
    const tail = /\S+$/.exec(text.slice(start, seg.end));
    const tailStart = tail ? start + tail.index : seg.end;
    const next = segs[i + 1];
    const punct = next ? (/^[.,;:!?%)\]}”’»"']+/.exec(text.slice(next.start, next.end))?.[0] ?? "") : "";
    carry = punct.length;
    nodes.push(piece(seg, start, tailStart));
    nodes.push(
      <span key={`g${seg.end}`} className="whitespace-nowrap">
        {piece(seg, tailStart, seg.end)}
        {marks}
        {next && punct ? piece(next, next.start, next.start + punct.length) : null}
      </span>,
    );
  }

  return (
    <div className="proof" data-testid="highlighted-draft">
      {nodes}
    </div>
  );
}
