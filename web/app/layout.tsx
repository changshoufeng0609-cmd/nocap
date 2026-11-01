import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Newsreader } from "next/font/google";

import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const newsreader = Newsreader({
  variable: "--font-newsreader",
  subsets: ["latin"],
  axes: ["opsz"],
  style: ["normal", "italic"],
});

const title = "NoCap · Check financial numbers against SEC filings";
const description =
  "Check every number in an earnings draft against the SEC filings of S&P 500 companies. Each ✓ or ✗ links to the filing that settles it; anything unsupported stays unverifiable.";

// The link-preview image is app/opengraph-image.png (and twitter-image.png), built by docs/demo/make_og_image.py.
export const metadata: Metadata = {
  metadataBase: new URL("https://receipts-nocap.vercel.app"),
  title,
  description,
  openGraph: { type: "website", siteName: "NoCap", title, description },
  twitter: { card: "summary_large_image", title, description },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfaf7" },
    { media: "(prefers-color-scheme: dark)", color: "#141312" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} ${newsreader.variable} antialiased`}
    >
      <body className="flex min-h-dvh flex-col">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <TooltipProvider delayDuration={250}>
            {children}
            <Toaster position="bottom-center" closeButton />
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
