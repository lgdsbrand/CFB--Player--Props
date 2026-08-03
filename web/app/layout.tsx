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

export const metadata: Metadata = {
  title: "CFB Player Props · Legends Sports",
  description:
    "Model-derived OVER/UNDER calls with confidence for college football player props.",
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
