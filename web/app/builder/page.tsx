import { BetBuilder } from "@/components/builder/bet-builder";
import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { WeekStrip } from "@/components/week-strip";
import { parseBoardParams, type RawParams } from "@/lib/core/board-params";
import { isSupabaseConfigured } from "@/lib/core/env";
import { DEFAULT_SPORT, resolveSport, SPORT_LABEL, type Sport } from "@/lib/core/sport";
import { getAppConfig } from "@/lib/data/config";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";

/**
 * The bet builder (client, 2026-10-08): "a button or parameters someone could
 * hit and it would make a bet slip for them", after nerdytips.com/bet-builder.
 *
 * The page only chooses the week; the picks are read by the client component
 * from `/builder/pool` and searched in the browser, so every setting answers
 * at once. See `lib/core/builder.ts` for what a pick is and how a slip is
 * chosen.
 */
export default async function BuilderPage({
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
  const [weeks, config] = await Promise.all([getSlateWeeks(sport), getAppConfig()]);
  const active = findWeek(weeks, params.season, params.week);

  return (
    <Shell sport={sport}>
      <div className="flex flex-col gap-1">
        <span className="label-caption">Legends Sports · {SPORT_LABEL[sport]}</span>
        <h1 className="text-2xl font-extrabold tracking-tight">Bet Builder</h1>
        <p className="text-muted text-sm">
          Pick the odds you want to end on and how strong each pick must be. The builder makes the
          slip.
        </p>
      </div>

      {active ? (
        <>
          <WeekStrip weeks={weeks} active={active} basePath="/builder" sport={sport} />
          <BetBuilder
            // A new week or sport is a new pool: start the component fresh.
            key={`${sport}-${active.season}-${active.week}`}
            sport={sport}
            season={active.season}
            week={active.week}
            edgeThreshold={config.edgeThreshold}
          />
        </>
      ) : (
        <div className="panel p-6">
          <h2 className="section-header mb-2">No slate yet</h2>
          <p className="text-muted max-w-prose text-sm">No week has picks to build from.</p>
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
      <SiteHeader activeHref="/builder" sport={sport} />
      <main className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}
