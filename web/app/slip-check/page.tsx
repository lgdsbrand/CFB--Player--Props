import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { SlipChecker } from "@/components/slip-check/slip-checker";
import { WeekStrip } from "@/components/week-strip";
import { parseBoardParams, type RawParams } from "@/lib/core/board-params";
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
  const params = parseBoardParams(raw, { edgesOnlyDefault: false });
  const weeks = await getSlateWeeks(sport);
  const active = findWeek(weeks, params.season, params.week);
  const shared = decodeSlipKeys(typeof raw.slip === "string" ? raw.slip : null);

  return (
    <Shell sport={sport}>
      <div className="flex flex-col gap-1">
        <span className="label-caption">Legends Sports · {SPORT_LABEL[sport]}</span>
        <h1 className="text-2xl font-extrabold tracking-tight">Slip Check</h1>
        <p className="text-muted text-sm">
          Pick your book, add your legs, and see what the model makes of each one and of the whole
          slip.
        </p>
      </div>

      {active ? (
        <>
          <WeekStrip weeks={weeks} active={active} basePath="/slip-check" sport={sport} />
          <SlipChecker
            key={`${sport}-${active.season}-${active.week}`}
            sport={sport}
            season={active.season}
            week={active.week}
            sharedKeys={shared}
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
