/**
 * Which sport the app is reading.
 *
 * The database gained a `sport` dimension (migration 0035) because the client
 * chose one app with a toggle rather than two products. There is no toggle yet
 * and no NFL data behind it, so every read here resolves to `DEFAULT_SPORT`.
 *
 * THE FILTERS EXIST ANYWAY, AND THAT IS THE POINT. A column added without the
 * reads that respect it is worse than no column: the day NFL rows land, the
 * college board silently lists NFL players and the week strip sums two slates
 * into one entry. Neither would raise anything. Wiring the filter now costs one
 * predicate per query against a column where every row matches, which the
 * planner ignores.
 *
 * WHEN THE TOGGLE ARRIVES it becomes a URL parameter resolved once per request
 * and threaded through in place of this constant. The FOUR cached reads that
 * currently close over it — `slate-weeks`, `conferences`, the game selector and
 * `defense-ratings` — must take the sport into their cache KEY at that point,
 * or the second sport serves the first one's catalogue for the length of the
 * TTL.
 *
 * `defense-ratings` joined that list in N4 and is the one worth naming twice.
 * It reads `defense_position_ratings`, which has no sport column of its own and
 * inherits one through the defense, so its filter is a PostgREST
 * `teams!inner(sport)` embed rather than a plain `.eq("sport", …)`. Its
 * arguments are (season, week, positionGroup) — none of which distinguishes a
 * league, since college and the NFL share every season and cutoff.
 */

export type Sport = "cfb" | "nfl";

export const DEFAULT_SPORT: Sport = "cfb";

/**
 * The sports this app can serve, in the order a toggle should list them.
 *
 * College first because it is the product that exists: six phases of modelling,
 * every `app_config` number measured on it, and the only sport with a backtest
 * of its own. The NFL borrows that calibration (N5b) and says so.
 */
export const SPORTS: readonly Sport[] = ["cfb", "nfl"] as const;

/** How each sport is named in the UI. */
export const SPORT_LABEL: Record<Sport, string> = {
  cfb: "College Football",
  nfl: "NFL",
};

/** Short form, for a toggle where space is tight. */
export const SPORT_SHORT: Record<Sport, string> = {
  cfb: "NCAAF",
  nfl: "NFL",
};

/**
 * Read the sport out of a URL parameter, falling back to college.
 *
 * UNKNOWN VALUES DEGRADE, THEY DO NOT THROW. A stale or hand-edited link with
 * `?sport=xfl` should show the default board, not an error page — the same rule
 * `board-params` applies to every other filter, and for the same reason: these
 * URLs get shared, bookmarked and truncated.
 */
export function resolveSport(value: string | string[] | undefined): Sport {
  const first = Array.isArray(value) ? value[0] : value;
  return SPORTS.includes(first as Sport) ? (first as Sport) : DEFAULT_SPORT;
}

/**
 * Whether this sport's projections lean on another sport's calibration.
 *
 * The board has to be able to say so. `run_projections` logs BORROWED
 * CALIBRATION and records `calibration_sport` on the model run, but a reader
 * looking at a confidence percentage sees none of that. Hardcoded rather than
 * read from `model_runs` because it is a fact about which backtests exist, and
 * the honest default while none exists for a sport is "yes, borrowed".
 */
export function usesBorrowedCalibration(sport: Sport): boolean {
  return sport !== DEFAULT_SPORT;
}
