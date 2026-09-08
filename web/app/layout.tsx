import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import "./globals.css";

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
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
