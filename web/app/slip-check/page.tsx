import Link from "next/link";

import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { SavedSlips } from "@/components/slip-check/saved-slips";
import { SlipChecker } from "@/components/slip-check/slip-checker";
import { WeekStrip } from "@/components/week-strip";
import { parseBoardParams, scopedHref, type RawParams } from "@/lib/core/board-params";
import { isSupabaseConfigured } from "@/lib/core/env";
import { decodeSlipKeys } from "@/lib/core/slip-check";
import { DEFAULT_SPORT, resolveSport, SPORT_LABEL, type Sport } from "@/lib/core/sport";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";

export const metadata = {
  title: "Slip Check · Legends Sports",
  description: "Pick a book, add your legs, and see what the model makes of each one and of the slip.",
};

/**
 * The slip checker (client, 2026-10-12, after nhlpropking.com/bet-slip-check).
 * The page only chooses the week; the catalogue is read by the client from
 * `/slip-check/catalogue`. See `lib/core/slip-check.ts` for the numbers.
 *
 * `?view=saved` is the My slips tab: slips saved in this browser, tracked
 * (`lib/core/slip-track.ts`). It reads no week; the list is the browser's.
 */
export default async function SlipCheckPage({
  searchParams,
}: {
  searchParams: Promise<RawParams>;
}) {
  if (!isSupabaseConfigured()) {
    return (
      <Shell>
        <NotConfigured />
      </Shell>
    );
  }

  const raw = await searchParams;
  const sport = resolveSport(raw.sport);
  const checkHref = scopedHref("/slip-check", { sport });
  const savedHref = scopedHref("/slip-check", { sport }, { view: "saved" });

  if (raw.view === "saved") {
    return (
      <Shell sport={sport}>
        <Intro sport={sport} />
        <Tabs active="saved" checkHref={checkHref} savedHref={savedHref} />
        <SavedSlips checkHref={checkHref} />
      </Shell>
    );
  }

  const params = parseBoardParams(raw, { edgesOnlyDefault: false });
  const weeks = await getSlateWeeks(sport);
  const active = findWeek(weeks, params.season, params.week);
  const shared = decodeSlipKeys(typeof raw.slip === "string" ? raw.slip : null);

  return (
    <Shell sport={sport}>
      <Intro sport={sport} />
      <Tabs active="check" checkHref={checkHref} savedHref={savedHref} />

      {active ? (
        <>
          <WeekStrip weeks={weeks} active={active} basePath="/slip-check" sport={sport} />
          <SlipChecker
            key={`${sport}-${active.season}-${active.week}`}
            sport={sport}
            season={active.season}
            week={active.week}
            sharedKeys={shared}
            savedHref={savedHref}
          />
        </>
      ) : (
        <div className="panel p-6">
          <h2 className="section-header mb-2">No slate yet</h2>
          <p className="text-muted max-w-prose text-sm">No week has prices to check against.</p>
        </div>
      )}
    </Shell>
  );
}

function Shell({
  children,
  sport = DEFAULT_SPORT,
}: {
  children: React.ReactNode;
  sport?: Sport;
}) {
  return (
    <>
      <SiteHeader activeHref="/slip-check" sport={sport} />
      <main className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}

function Intro({ sport }: { sport: Sport }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="label-caption">Legends Sports · {SPORT_LABEL[sport]}</span>
      <h1 className="text-2xl font-extrabold tracking-tight">Slip Check</h1>
      <p className="text-muted text-sm">
        Pick your book, add your legs, and see what the model makes of each one and of the whole
        slip. Save it to follow it live.
      </p>
    </div>
  );
}

function Tabs({
  active,
  checkHref,
  savedHref,
}: {
  active: "check" | "saved";
  checkHref: string;
  savedHref: string;
}) {
  const tab = (on: boolean) =>
    "rounded-full border px-3.5 py-1.5 text-xs font-extrabold tracking-[0.12em] uppercase transition-colors " +
    (on
      ? "border-accent-cyan/60 bg-accent-cyan/10 text-ink"
      : "border-border-subtle text-muted hover:text-ink hover:border-border-strong");
  return (
    <nav aria-label="Slip Check" className="flex gap-2">
      <Link href={checkHref} className={tab(active === "check")} aria-current={active === "check" ? "page" : undefined}>
        Check a slip
      </Link>
      <Link href={savedHref} className={tab(active === "saved")} aria-current={active === "saved" ? "page" : undefined}>
        My slips
      </Link>
    </nav>
  );
}
