import { coverageExcerpt } from "@/lib/coverage";
import type { NumericCoverage } from "@/lib/types";

/** Separate from verdicts: an omitted candidate has no claim or evidence verdict yet. */
export function NumberCoverage({ coverage, text, label = "Number coverage" }: {
  coverage?: NumericCoverage;
  text: string;
  label?: string;
}) {
  const scanNote = coverage ? `This scan uses digit patterns and excludes recognized dates and identifiers; it can miss other number formats.${coverage.excluded_count ? ` ${coverage.excluded_count} scanned ${coverage.excluded_count === 1 ? "token" : "tokens"} excluded.` : ""}` : "";
  // Nothing unlinked: one line of fine print, with the scan's limits one click away.
  if (coverage && !coverage.uncovered.length) {
    return (
      <section aria-label={label} className="border-b border-border px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <h3 className="text-sm font-medium">{label}</h3>
          <p className="text-xs leading-relaxed text-muted-foreground">{coverage.covered_count} of {coverage.candidate_count} digit-based candidates linked to extracted claims. Linked does not mean verified.</p>
        </div>
        <details className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          <summary className="w-fit cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">How the scan works</summary>
          <p className="mt-1">{scanNote}</p>
        </details>
      </section>
    );
  }
  return (
    <section aria-label={label} className="border-b border-border px-4 py-3.5 sm:px-6">
      <h3 className="text-sm font-medium">{label}</h3>
      {!coverage ? (
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">Coverage is unavailable for this result. Numbers without a claim may not have been checked.</p>
      ) : (
        <>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {coverage.covered_count} of {coverage.candidate_count} digit-based candidates linked to extracted claims.
            {" "}{coverage.uncovered.length} not checked.
          </p>
          {coverage.uncovered.length ? (
            <details className="mt-3 rounded-md border border-dashed border-foreground/25 px-3 py-2" open>
              <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">Not checked · {coverage.uncovered.length}</summary>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">These candidates could not be linked to a uniquely located claim. Review these numeric occurrences manually.</p>
              <ul className="mt-2 max-h-64 space-y-3 overflow-y-auto" aria-label="Numbers not checked">
                {coverage.uncovered.map((item) => {
                  const excerpt = coverageExcerpt(text, item);
                  return (
                    <li key={`${item.start}-${item.end}`} className="text-xs leading-relaxed">
                      <span className="text-muted-foreground">Line {excerpt.line} · </span>
                      <span className="whitespace-pre-wrap break-words">{excerpt.before}<mark className="rounded-sm bg-muted px-0.5 font-semibold text-foreground underline decoration-dashed underline-offset-2">{excerpt.value}</mark>{excerpt.after}</span>
                    </li>
                  );
                })}
              </ul>
            </details>
          ) : null}
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            Linked does not mean verified. {scanNote}
          </p>
        </>
      )}
    </section>
  );
}
