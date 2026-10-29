import { fullFactValue, receiptCalculation } from "@/lib/calculations";
import { formatFiscalPeriod, formatPeriodRange } from "@/lib/format";
import type { Claim, Verdict } from "@/lib/types";

export function ReceiptCalculation({ claim, verdict }: { claim: Claim; verdict: Verdict }) {
  const calculation = receiptCalculation(claim, verdict);
  if (!calculation) return null;
  return (
    <section aria-label="Calculation from receipts" className="space-y-2 rounded-lg border border-border bg-muted/40 p-3.5 text-sm">
      <h3 className="font-medium">{calculation.label} · calculation</h3>
      <p className="text-xs text-muted-foreground">{calculation.formula}</p>
      <p className="break-words font-mono text-xs leading-relaxed">{calculation.equation}</p>
      <ul className="space-y-1.5 border-t border-border pt-2 text-xs text-muted-foreground">
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
