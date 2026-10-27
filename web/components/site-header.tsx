import { ThemeToggle } from "@/components/theme-toggle";

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

const BUILT_ON = "Built on Meta Muse Spark · SEC EDGAR";

export function SiteHeader() {
  return (
    <header className="border-b border-border/80">
      <div className="mx-auto flex w-full max-w-6xl items-start justify-between gap-4 px-4 py-5 sm:px-6 sm:py-7">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <LogoMark className="size-7 shrink-0" />
            <h1 className="font-serif text-[1.7rem] leading-none font-semibold tracking-[-0.015em]">Receipts</h1>
          </div>
          <p className="mt-2.5 text-[0.95rem] text-pretty text-muted-foreground">
            Every financial number, checked against SEC filings before it ships.
          </p>
          <p className="mt-1.5 text-xs text-muted-foreground sm:hidden">{BUILT_ON}</p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <p className="hidden text-xs text-muted-foreground sm:block">{BUILT_ON}</p>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
