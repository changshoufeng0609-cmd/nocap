"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { MOCK, fetchHealth } from "@/lib/api";
import { formatDay } from "@/lib/format";
import type { Health } from "@/lib/types";

export function SiteFooter() {
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    fetchHealth(ac.signal).then((h) => {
      if (!ac.signal.aborted) setHealth(h);
    });
    return () => ac.abort();
  }, []);

  return (
    <footer className="mt-auto border-t border-border/80">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-1.5 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-start sm:justify-between sm:gap-8 sm:px-6">
        <div className="space-y-1.5 sm:shrink-0">
          <p className="font-medium text-foreground/80">Research and fact-checking only. Not investment advice.</p>
          <Link href="/demo" className="underline underline-offset-4">Watch the demo</Link>
        </div>
        <p className="text-pretty sm:text-right">
          Data: SEC EDGAR filings (XBRL financial data and 8-K earnings releases)
          {health ? (
            <>
              {" "}
              for {health.companies.length} companies, {health.facts.toLocaleString("en-US")} facts, snapshot{" "}
              {formatDay(health.data_snapshot)}
            </>
          ) : null}
          .
          {MOCK ? " Mock mode: replaying checks recorded from the NoCap CLI." : null}
        </p>
      </div>
    </footer>
  );
}
