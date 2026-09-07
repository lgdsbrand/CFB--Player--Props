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
