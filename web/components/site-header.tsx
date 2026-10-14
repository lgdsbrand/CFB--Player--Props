import Image from "next/image";
import Link from "next/link";

import legendsLogo from "@/assets/legends-logo.png";
import { HamburgerMenu, MoreMenu } from "@/components/header-menus";
import { BOARD_PATH, scopedHref } from "@/lib/core/board-params";
import { BAR_SPORTS, CLIENT_SITE_ORIGIN, hisInfoHref, hisSiteHref } from "@/lib/core/client-site";
import { DEFAULT_SPORT, SPORTS, type Sport } from "@/lib/core/sport";

/**
 * The app shell's header: the client's own header, so the football pages read
 * as part of his site (client, 2026-10-14: "can we do the menu like my baseball
 * page?").
 *
 * THREE LAYERS, AS ON HIS MLB PAGE (his App.jsx, lgdsbrand/mlb_predictor_engine_v2):
 *   1. his logo and LEGENDS SPORTS wordmark, with this page's subtitle; the
 *      hamburger on phones and tablets;
 *   2. his row of sports, desktop only (`lib/core/client-site.ts`). Every entry
 *      opens his site; NFL or CFB is highlighted, because this app is the
 *      Player Props (and NFL Fantasy Model) tab of his NFL and CFB pages;
 *   3. our own tabs, his MLB sub-tab style, with the NFL/CFB league switch at
 *      the end of the row (the old NCAAF/NFL toggle, restyled as his).
 * His classes are used as he wrote them (Tailwind's default palette), so the
 * header matches his pixel for pixel rather than through our theme tokens.
 *
 * Sport rides on every tab, built by `scopedHref`: a tab that dropped it would
 * send an NFL reader to college (see `sport-links.test.ts`). `/health` is
 * deliberately not a tab: it is an operator's deploy proof, unlinked and
 * noindexed (`app/health/page.tsx`).
 */

const TABS: { href: string; label: string; icon: string }[] = [
  { href: "/", label: "Overview", icon: "🏈" },
  { href: BOARD_PATH, label: "Props", icon: "🎯" },
  { href: "/games", label: "Games", icon: "🏟️" },
  { href: "/cheat-sheets", label: "Sheets", icon: "📋" },
  { href: "/builder", label: "Builder", icon: "🧱" },
  // The slip checker (client, 2026-10-12): the same slip, checked leg by leg.
  { href: "/slip-check", label: "Check", icon: "✅" },
  // The game models' graded picks (client, 2026-10-08).
  { href: "/tracker", label: "Tracker", icon: "📈" },
  { href: "/no-vig", label: "No-Vig", icon: "⚖️" },
  { href: "/arbitrage", label: "Arb", icon: "🔀" },
];

const LEAGUE_LABEL: Record<Sport, string> = { nfl: "NFL", cfb: "CFB" };

const SUBTITLE: Record<Sport, string> = {
  nfl: "NFL props, games & tracker (Beta)",
  cfb: "College football props, games & tracker (Beta)",
};

// His widths, so the three rows line up with his pages'.
const WIDTH = "mx-auto max-w-7xl xl:max-w-[1440px] 2xl:max-w-[1600px]";

const TAB_BASE =
  "flex shrink-0 cursor-pointer items-center gap-1 rounded-lg text-[10px] font-black uppercase transition-all duration-200";
const TAB_ACTIVE =
  "border border-blue-500/30 bg-blue-600/20 font-extrabold text-blue-400 shadow-[0_0_12px_rgba(59,130,246,0.15)]";
const TAB_IDLE = "border border-transparent text-slate-400 hover:bg-slate-900/40 hover:text-slate-200";

export function SiteHeader({
  activeHref = "/",
  sport = DEFAULT_SPORT,
}: {
  activeHref?: string;
  sport?: Sport;
}) {
  const withSport = (href: string) => scopedHref(href, { sport });

  return (
    <header className="sticky top-0 z-20 flex w-full flex-col border-b border-slate-900/80 bg-slate-950/90 shadow-[0_4px_30px_rgba(0,0,0,0.4)] backdrop-blur-md select-none">
      {/* 1. Brand */}
      <div className="w-full border-b border-slate-900/40 px-4 py-3 md:px-8">
        <div className={`${WIDTH} flex items-center justify-between gap-4`}>
          <a href={CLIENT_SITE_ORIGIN} className="flex min-w-0 flex-shrink-0 flex-col">
            <span className="flex flex-wrap items-center justify-start gap-2 md:gap-3">
              <Image
                src={legendsLogo}
                alt="Legends Sports"
                priority
                className="h-9 w-auto object-contain brightness-110 drop-shadow-[0_0_15px_rgba(59,130,246,0.35)] md:h-11 lg:h-12"
              />
              <span className="mx-1 hidden text-xl font-light text-slate-400 sm:inline md:mx-2 md:text-2xl">|</span>
              <span className="mt-0.5 bg-gradient-to-r from-cyan-400 via-blue-500 to-indigo-500 bg-clip-text text-lg font-black tracking-widest text-transparent uppercase md:text-xl">
                Legends Sports
              </span>
            </span>
            <span className="mt-0.5 text-[9px] font-bold tracking-tight text-gray-500 md:text-[10px]">
              {SUBTITLE[sport]}
            </span>
          </a>
          <HamburgerMenu sport={sport} />
        </div>
      </div>

      {/* 2. His sports, desktop */}
      <div className="hidden w-full border-b border-slate-900/20 px-8 py-2 lg:block">
        <div className={`${WIDTH} flex items-center justify-between`}>
          <nav className="flex items-center gap-1 rounded-xl border border-slate-900 bg-slate-900/30 p-0.5" aria-label="Sports">
            {BAR_SPORTS.map((s) => {
              const selected = s.football === sport;
              const className =
                "flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-[10px] font-black tracking-widest uppercase transition-all duration-200 " +
                (selected
                  ? "border border-indigo-500/20 bg-indigo-600/20 font-extrabold text-indigo-400 shadow-[0_0_12px_rgba(99,102,241,0.1)]"
                  : "border border-transparent text-slate-400 hover:bg-slate-900/40 hover:text-slate-200");
              const body = (
                <>
                  <span>{s.icon}</span>
                  <span>{s.name}</span>
                  {s.status === "BETA" ? (
                    <span className="rounded border border-cyan-500/30 bg-cyan-950 px-1 text-[7px] text-cyan-400">
                      BETA
                    </span>
                  ) : null}
                </>
              );
              return (
                <a key={s.id} href={hisSiteHref(s.id)} className={className}>
                  {body}
                </a>
              );
            })}
            <MoreMenu />
          </nav>
          <div className="flex flex-shrink-0 items-center gap-2">
            {(["about", "contact"] as const).map((dialog) => (
              <a
                key={dialog}
                href={hisInfoHref(dialog)}
                className="cursor-pointer rounded-lg border border-slate-900 px-3 py-1.5 text-[10px] font-black tracking-widest text-slate-400 uppercase transition-all hover:bg-slate-900/60 hover:text-slate-200"
              >
                {dialog === "about" ? "About" : "Contact"}
              </a>
            ))}
          </div>
        </div>
      </div>

      {/* 3. Our tabs, his MLB sub-tab style, then the league switch. On a
          phone the tabs scroll sideways (his "touch friendly" strip) while the
          switch stays put; nothing here may widen the page, which a header row
          once did to every route at 390px. */}
      <div className="w-full border-b border-slate-900/20 bg-slate-950/40 px-4 py-2 md:px-8">
        <div className={`${WIDTH} flex items-center gap-3`}>
          {/* On a phone the switch leaves room for two or three tabs, so the
              right edge fades: a tab cut off flat reads as a rendering fault,
              a fade reads as "more this way". Right only, so Overview at the
              start is never faded. */}
          <div className="no-scrollbar min-w-0 flex-1 overflow-x-auto [mask-image:linear-gradient(to_right,black_calc(100%-1.75rem),transparent)] md:[mask-image:none]">
            <nav className="flex min-w-max items-center justify-start gap-2 md:gap-1.5" aria-label="Football">
              {TABS.map((tab) => {
                const active = tab.href === activeHref;
                return (
                  <Link
                    key={tab.href}
                    href={withSport(tab.href)}
                    aria-current={active ? "page" : undefined}
                    className={`${TAB_BASE} px-3 py-1.5 tracking-wider md:px-4 md:tracking-widest ${active ? TAB_ACTIVE : TAB_IDLE}`}
                  >
                    <span>{tab.icon}</span>
                    <span>{tab.label}</span>
                  </Link>
                );
              })}
            </nav>
          </div>
          {/* Same page, other league. Links, not a control: each option is a
              shareable URL and works without JavaScript. */}
          <nav
            aria-label="League"
            className="flex shrink-0 items-center gap-0.5 rounded-xl border border-slate-900 bg-slate-900/30 p-0.5"
          >
            {SPORTS.map((option) => {
              const active = option === sport;
              return (
                <Link
                  key={option}
                  href={scopedHref(activeHref, { sport: option })}
                  aria-current={active ? "true" : undefined}
                  className={
                    "rounded-lg px-2.5 py-1.5 text-[10px] font-black tracking-widest uppercase transition-all duration-200 " +
                    (active
                      ? "border border-indigo-500/20 bg-indigo-600/20 text-indigo-400"
                      : "border border-transparent text-slate-400 hover:bg-slate-900/40 hover:text-slate-200")
                  }
                >
                  {LEAGUE_LABEL[option]}
                </Link>
              );
            })}
          </nav>
        </div>
      </div>
    </header>
  );
}
