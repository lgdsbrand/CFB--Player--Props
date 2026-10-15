import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import "./globals.css";

import { SlipDock } from "@/components/slip/slip-dock";
import { SITE_HOST } from "@/lib/core/site";

/**
 * Inter, measured — not chosen.
 *
 * CLAUDE.md §7 asks for "a modern geometric sans" and says to confirm the family
 * from the live site. The live site's body rule is
 * `font-family: Inter, sans-serif`, so this is a match rather than a preference.
 * Geist Mono stays for the odds columns: they ship no custom mono, so there is
 * nothing to match there.
 */
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// SPORT-NEUTRAL SINCE N5e. This is the ROOT layout, rendered once for both
// boards, so it cannot name a league without being wrong on half the traffic —
// and the title is the browser tab, which a reader on the NFL board would have
// seen reading "CFB Player Props". Per-sport metadata would need
// `generateMetadata` on every page reading the same search param the page
// already reads; neutral here is honest and costs nothing.
export const metadata: Metadata = {
  // A shared link's preview image names the client's site, not this deployment.
  ...(SITE_HOST ? { metadataBase: new URL(`https://${SITE_HOST}`) } : {}),
  title: "Player Props · Legends Sports",
  description:
    "Model-derived OVER/UNDER calls with confidence for college football and NFL player props.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* His page's glows (his App.jsx root): soft indigo, cyan and purple
            light behind the see-through cards. Fixed, behind everything, and
            still for anyone who asks the system for less motion. */}
        <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
          <div className="absolute top-10 left-1/4 h-72 w-72 rounded-full bg-indigo-500/10 blur-3xl motion-safe:animate-pulse motion-safe:[animation-duration:8s]" />
          <div className="absolute right-1/4 bottom-20 h-96 w-96 rounded-full bg-cyan-500/10 blur-3xl motion-safe:animate-pulse motion-safe:[animation-duration:12s]" />
          <div className="absolute top-1/2 left-1/3 h-80 w-80 rounded-full bg-purple-500/5 blur-3xl" />
        </div>
        {children}
        <SlipDock />
      </body>
    </html>
  );
}
