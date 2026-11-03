import { fullFactValue, receiptCalculation } from "@/lib/calculations";
import { formatFiscalPeriod, formatPeriodRange } from "@/lib/format";
import type { Claim, Verdict } from "@/lib/types";

export function ReceiptCalculation({ claim, verdict }: { claim: Claim; verdict: Verdict }) {
  const calculation = receiptCalculation(claim, verdict);
  if (!calculation) return null;
  return (
    <section aria-label="Calculation from receipts" className="space-y-2 rounded-lg border border-border bg-muted/40 p-3.5 text-sm shadow-2xs">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">{calculation.label} · calculation</h3>
        <span className="rounded border border-border/80 bg-background/80 px-1.5 py-0.5 font-mono text-[0.68rem] text-muted-foreground">
          Deterministic math
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{calculation.formula}</p>
      <p className="rounded-md border border-border/70 bg-background/80 px-2.5 py-1.5 break-words font-mono text-xs leading-relaxed">
        {calculation.equation}
      </p>
      <ul className="space-y-1.5 border-t border-dashed border-border pt-2 text-xs text-muted-foreground">
        {calculation.inputs.map(({ label, fact, receipt }) => (
          <li key={`${label}-${receipt}`}>
            <span className="font-medium text-foreground">{label}: {fullFactValue(fact)}</span>
            <span className="block">{formatFiscalPeriod(fact) ?? formatPeriodRange(fact)} · {fact.basis} · receipt {receipt}</span>
          </li>
        ))}
      </ul>
      <p className="text-[0.7rem] text-muted-foreground">Calculated from the cited figures; percentage rounded for display.</p>
    </section>
  );
}
