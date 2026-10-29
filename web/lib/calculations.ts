import { formatValue } from "./format";
import type { Claim, Fact, Verdict } from "./types";

export interface ReceiptCalculation {
  label: string;
  formula: string;
  equation: string;
  value: number;
  inputs: { label: string; fact: Fact; receipt: number }[];
}

const day = (date: string) => Date.parse(`${date.slice(0, 10)}T00:00:00Z`) / 86_400_000;
const sameValue = (a: number, b: number) => Math.abs(a - b) <= Math.max(1e-6, Math.abs(b) * 1e-8);

/** Unscaled operands: compact B/M labels would silently round the calculation inputs. */
export function fullFactValue(fact: Fact): string {
  const value = fact.value.toLocaleString("en-US", { maximumFractionDigits: 10 });
  if (fact.unit === "USD") return `$${value}`;
  if (fact.unit === "USD/share") return `$${value}/share`;
  return `${value}${fact.unit === "%" ? "%" : ` ${fact.unit}`}`;
}

/**
 * Explain existing verifier evidence, never produce a verdict. Only display an unambiguous
 * calculation (or one matching the server's expected percentage). A direct filed percentage
 * needs no invented derivation. Evidence with several different valid calculations stays in
 * the receipt list and the server's explanation remains authoritative.
 */
export function receiptCalculation(claim: Claim, verdict: Verdict): ReceiptCalculation | null {
  const facts = verdict.evidence;
  if (claim.unit !== "%" || verdict.status === "unverifiable" || facts.length < 2) return null;
  if (facts.some((f) => f.unit === "%" && (verdict.expected_value == null || sameValue(f.value, verdict.expected_value)))) return null;
  const candidates: ReceiptCalculation[] = [];
  const add = (label: string, formula: string, expression: string, value: number, inputs: [string, Fact][]) => {
    if (!Number.isFinite(value)) return;
    candidates.push({
      label, formula, value, equation: `${expression} ≈ ${formatValue(value, "%")}`,
      inputs: inputs.map(([inputLabel, fact]) => ({ label: inputLabel, fact, receipt: facts.indexOf(fact) + 1 })),
    });
  };
  const sameCompany = (a: Fact, b: Fact) => a.cik === b.cik && a.ticker === b.ticker;
  const samePeriod = (a: Fact, b: Fact) => a.period_start === b.period_start && a.period_end === b.period_end;
  const operatingMargin = /\boperating\s+margin/i.test(claim.metric_text);
  const isMargin = claim.metric === "gross_margin" || operatingMargin;
  if (isMargin) {
    const numeratorMetric = operatingMargin ? "operating_income" : "gross_profit";
    const label = operatingMargin ? "Operating margin" : "Gross margin";
    for (const revenue of facts.filter((f) => f.metric === "revenue" && f.unit === "USD" && f.value !== 0)) {
      for (const numerator of facts.filter((f) => f.metric === numeratorMetric && f.unit === "USD" && sameCompany(f, revenue) && samePeriod(f, revenue) && f.basis === revenue.basis && (f.segment ?? null) === (revenue.segment ?? null))) {
        add(label, `${operatingMargin ? "Operating income" : "Gross profit"} ÷ revenue × 100`, `${fullFactValue(numerator)} ÷ ${fullFactValue(revenue)} × 100`, numerator.value / revenue.value * 100, [[operatingMargin ? "Operating income" : "Gross profit", numerator], ["Revenue", revenue]]);
      }
      if (!operatingMargin) {
        for (const cost of facts.filter((f) => f.metric === "cost_of_revenue" && f.unit === "USD" && sameCompany(f, revenue) && samePeriod(f, revenue) && f.basis === revenue.basis && (f.segment ?? null) === (revenue.segment ?? null))) {
          add(label, "(Revenue − cost of revenue) ÷ revenue × 100", `(${fullFactValue(revenue)} − ${fullFactValue(cost)}) ÷ ${fullFactValue(revenue)} × 100`, (revenue.value - cost.value) / revenue.value * 100, [["Revenue", revenue], ["Cost of revenue", cost]]);
        }
      }
    }
  } else {
    for (const current of facts.filter((f) => f.unit !== "%")) {
      for (const prior of facts) {
        if (!sameCompany(current, prior) || current.metric !== prior.metric || current.unit !== prior.unit || current.basis !== prior.basis || (current.segment ?? null) !== (prior.segment ?? null) || prior.value <= 0) continue;
        const gap = day(current.period_end) - day(prior.period_end);
        const comparison = Math.abs(gap - 364) <= 20 ? "YoY" : Math.abs(gap - 91) <= 20 ? "QoQ" : null;
        if (!comparison) continue;
        // Do not mix a quarter with an annual or year-to-date total.
        if (!!current.period_start !== !!prior.period_start) continue;
        if (current.period_start && prior.period_start && Math.abs((day(current.period_end) - day(current.period_start)) - (day(prior.period_end) - day(prior.period_start))) > 20) continue;
        const stated = claim.comparison === "sequential" ? "QoQ" : claim.comparison;
        if (verdict.reason !== "period_mismatch" && stated !== "none" && stated !== comparison) continue;
        add(`${comparison} change`, "(Current ÷ prior − 1) × 100", `(${fullFactValue(current)} ÷ ${fullFactValue(prior)} − 1) × 100`, (current.value / prior.value - 1) * 100, [["Current", current], ["Prior", prior]]);
      }
    }
  }
  const eligible = verdict.expected_value == null ? candidates : candidates.filter((c) => sameValue(c.value, verdict.expected_value!));
  const first = eligible[0];
  return first && eligible.every((c) => sameValue(c.value, first.value)) ? first : null;
}
