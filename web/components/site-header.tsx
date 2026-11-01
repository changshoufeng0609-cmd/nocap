import Link from "next/link";

import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={className}>
      <rect width="32" height="32" rx="7" className="fill-foreground" />
      <path d="M9 6.5h14v19l-2.33-1.6-2.34 1.6L16 23.9l-2.33 1.6-2.34-1.6L9 25.5z" className="fill-background" />
      <path
        d="M12.3 15.6l2.6 2.6 4.8-5.2"
        fill="none"
        className="stroke-ok"
        strokeWidth="2.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// The hosted Meta models the public site calls live: Spark (Draft, Slide, Ask), Wonder (Fix), SAM 3.1 (Slide).
const META_MODELS = "Muse Spark, Muse Wonder & SAM 3.1";

const NAV_LINK =
  "rounded-md px-2 py-1 text-sm text-foreground/80 transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2";

function BuiltOn({ className }: { className?: string }) {
  return (
    <p className={cn("w-fit max-w-full rounded-full border border-border bg-card/60 px-2.5 py-0.5 text-[11px] text-muted-foreground", className)}>
      <span className="max-[379px]:hidden">Built on </span>Meta {META_MODELS} · SEC EDGAR
    </p>
  );
}

export function SiteHeader() {
  return (
    <header className="border-b border-border/80">
      <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6 sm:py-7">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <LogoMark className="size-7 shrink-0" />
            <h1 className="font-serif text-[1.7rem] leading-none font-semibold tracking-[-0.015em]"><Link href="/">NoCap</Link></h1>
          </div>
          <nav aria-label="Main navigation" className="flex shrink-0 items-center gap-1 sm:gap-2">
            <BuiltOn className="mr-2 hidden lg:block" />
            <Link href="/demo" className={NAV_LINK}>Demo</Link>
            <Link href="/benchmarks" className={NAV_LINK}>Benchmarks</Link>
            <ThemeToggle />
          </nav>
        </div>
        <p className="mt-2.5 text-[0.95rem] text-pretty text-muted-foreground">
          Check financial numbers against the SEC filings of S&amp;P 500 companies before you publish.
        </p>
        <BuiltOn className="mt-2 lg:hidden" />
      </div>
    </header>
  );
}
