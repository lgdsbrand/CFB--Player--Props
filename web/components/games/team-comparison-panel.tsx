import {
  STRENGTH_METRICS,
  ordinal,
  type RankedValue,
  type TeamStrength,
} from "@/lib/core/team-strength";

/**
 * Both teams' opponent-adjusted offense and defense, as ranked bars (client,
 * 2026-10-06, after the Bettor Odds card).
 *
 * Away on the left and home on the right, matching "AWAY @ HOME" everywhere
 * else; each bar grows from the centre line, full at 1st and empty at last,
 * so the longer bar is the better unit on that row. The better of the two
 * values is the brighter one.
 *
 * Point in time: ranked as of the game's week, from games before it
 * (`getTeamStrength`). Nothing here knows which sport it is; the NFL has no
 * rows and the page does not render the panel.
 */
export function TeamComparisonPanel({
  away,
  home,
  awayStrength,
  homeStrength,
  week,
  league,
}: {
  away: string;
  home: string;
  awayStrength: TeamStrength | undefined;
  homeStrength: TeamStrength | undefined;
  week: number;
  /** What the ranks are out of, e.g. "FBS teams" — the page knows the sport. */
  league: string;
}) {
  const of = Math.max(
    ...STRENGTH_METRICS.map(
      (m) => awayStrength?.metrics[m.key]?.of ?? homeStrength?.metrics[m.key]?.of ?? 0,
    ),
  );
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">📈 Team comparison</h2>
        <p className="text-muted text-xs">
          Opponent-adjusted: what each unit would do against an average
          opponent, from games before this one.{" "}
          {of > 0 ? `Ranked among ${of} ${league}; ` : ""}1st is the best, and
          for plays per game the most. Early in a season these rest on few
          games.
        </p>
      </div>

      {!awayStrength && !homeStrength ? (
        <p className="text-muted text-xs">
          {week <= 1
            ? "No games played yet this season — the comparison starts in week 2."
            : "Neither team has a rating entering this week."}
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          <div className="grid grid-cols-[1fr_auto_1fr] items-end gap-x-3 pb-1">
            <TeamHead label={away} strength={awayStrength} align="left" />
            <span />
            <TeamHead label={home} strength={homeStrength} align="right" />
          </div>
          {(["offense", "defense"] as const).map((side) => (
            <div key={side} className="flex flex-col gap-1.5">
              <h3 className="label-caption border-border-subtle mt-2 border-b pb-1 text-center">
                {side === "offense" ? "Offense" : "Defense"}
              </h3>
              {STRENGTH_METRICS.filter((m) => m.side === side).map((metric) => (
                <ComparisonRow
                  key={metric.key}
                  label={metric.label}
                  format={metric.format}
                  away={awayStrength?.metrics[metric.key]}
                  home={homeStrength?.metrics[metric.key]}
                />
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function TeamHead({
  label,
  strength,
  align,
}: {
  label: string;
  strength: TeamStrength | undefined;
  align: "left" | "right";
}) {
  return (
    <span className={"flex flex-col " + (align === "right" ? "items-end" : "items-start")}>
      <span className="text-ink text-sm font-extrabold">{label}</span>
      <span className="text-dim text-[0.6875rem]">
        {strength
          ? `from ${strength.gamesIncluded} game${strength.gamesIncluded === 1 ? "" : "s"}`
          : "no rating"}
      </span>
    </span>
  );
}

/**
 * One ranked metric, away against home. Exported for the NFL's Matchup stats
 * panel, which ranks the fantasy model's inputs the same way, so a bar means
 * the same thing on both sports' pages.
 */
export function ComparisonRow({
  label,
  format,
  away: a,
  home: h,
}: {
  label: string;
  format: (value: number) => string;
  away: RankedValue | undefined;
  home: RankedValue | undefined;
}) {
  // Brighter for the better rank; equal when tied or one side is missing.
  const awayBetter = a && h ? a.rank < h.rank : false;
  const homeBetter = a && h ? h.rank < a.rank : false;
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-x-3">
      <Side value={a} format={format} better={awayBetter} align="left" />
      <span className="label-caption w-24 text-center leading-tight sm:w-44">
        {label}
      </span>
      <Side value={h} format={format} better={homeBetter} align="right" />
    </div>
  );
}

function Side({
  value,
  format,
  better,
  align,
}: {
  value: RankedValue | undefined;
  format: (value: number) => string;
  better: boolean;
  align: "left" | "right";
}) {
  if (!value) {
    return (
      <span className={"text-dim text-xs " + (align === "left" ? "text-right" : "text-left")}>—</span>
    );
  }
  // The bar grows OUT from the centre label: right-anchored on the away side.
  return (
    <span className="flex flex-col gap-1">
      <span
        className={
          "flex items-baseline gap-1.5 " + (align === "left" ? "justify-end" : "justify-start")
        }
      >
        <span
          className={
            "text-sm font-extrabold tabular-nums " + (better ? "text-ink" : "text-muted")
          }
        >
          {format(value.value)}
        </span>
        <span className="text-dim text-[0.6875rem] tabular-nums">{ordinal(value.rank)}</span>
      </span>
      <span
        className={
          "bg-panel-inset flex h-1.5 w-full overflow-hidden rounded-full " +
          (align === "left" ? "justify-end" : "justify-start")
        }
      >
        <span
          className={"h-full rounded-full " + (better ? "bg-accent-cyan" : "bg-accent-cyan/40")}
          style={{ width: `${Math.max(value.share * 100, 2)}%` }}
        />
      </span>
    </span>
  );
}
