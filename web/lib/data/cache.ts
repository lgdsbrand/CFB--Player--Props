import { unstable_cache } from "next/cache";

/**
 * Caching for the reads that do not change between page views.
 *
 * WHY THIS EXISTS, MEASURED. The app is one process talking to a Supabase
 * instance over the public internet, and the round trip dominates everything
 * else: from a development machine a query returning 26 bytes takes ~415ms and
 * one returning 80KB takes ~424ms. Payload size is noise; the distance is the
 * cost. So the number that decides how fast a page feels is not how much it
 * reads but HOW MANY TIMES IT WAITS — and a page waits once per sequential
 * round trip, not once per query, because the parallel ones overlap.
 *
 * That is why only whole waves are worth caching. The board opens by fetching
 * the week strip, the config, the market catalogue and the conference list
 * together; every one of those changes when the worker runs, at most weekly, so
 * caching them removes an entire wait rather than shaving a query. Caching one
 * member of a wave whose other members still go to the database saves nothing
 * at all — which is the trap this file is written to avoid, and the reason
 * `getPlayerIdentity` is deliberately NOT here (see the player page).
 *
 * IN PRODUCTION THIS MATTERS LESS AND STILL MATTERS. Deployed with the Vercel
 * function in Supabase's own region the round trip falls to single-digit
 * milliseconds, so the waits stop being visible. What remains is load: an
 * uncached catalogue read is one database query per page view per reader, for
 * data that is identical for all of them.
 *
 * PER-WEEK READS GET A SHORTER CACHE, NOT NONE (`cachedLiveRead`). They used to
 * be read live on the rule that a stale board is a worse failure than a slow
 * one. That rule did not survive 2026-10-22: the production database (Micro)
 * crashed nine times in three hours on Thursday night football while the board
 * was rendered about twice a second, each render running six uncached queries
 * that returned the same rows to every reader (120 `v_slate_games` and 120
 * `board_counts` calls a minute, measured in `pg_stat_statements`). A board
 * that is down is staler than one a minute old. Odds land every 20 minutes and
 * picks lock at kickoff, so a minute changes nothing a reader acts on. Truly
 * live data (scores, the live model, the live simulation) is not read through
 * here: the browser polls it directly.
 */

/**
 * How long a catalogue read may be reused.
 *
 * The worker writes this data on a weekly cron (see docs/runbook.md), so any
 * value under an hour is conservative. Five minutes is chosen so that a manual
 * pipeline run during development shows up in the UI without anyone having to
 * remember this file exists.
 */
export const CATALOGUE_TTL_SECONDS = 300;

/** Tag for everything here, so one call can drop the lot if it ever needs to. */
export const CATALOGUE_TAG = "catalogue";

/**
 * Wrap a read whose result is the same for every reader.
 *
 * `keyParts` must distinguish every distinct call: a function taking a season
 * and a week needs both in the key, or week 1 will serve week 8's rows. The
 * arguments are also serialised into the key by `unstable_cache`, so the parts
 * here name the FUNCTION and the arguments do the rest.
 */
export function cachedRead<Args extends unknown[], Result>(
  name: string,
  read: (...args: Args) => Promise<Result>,
): (...args: Args) => Promise<Result> {
  return unstable_cache(read, [name], {
    revalidate: CATALOGUE_TTL_SECONDS,
    tags: [CATALOGUE_TAG],
  });
}

/**
 * How long a per-week read (board rows, the slate, game odds) may be reused.
 * Agreed 2026-10-22: a minute behind, in exchange for
 * one database read per page per minute instead of one per reader.
 */
export const LIVE_READ_TTL_SECONDS = 60;

/** Tag for the per-week reads, separate so they can be dropped on their own. */
export const LIVE_READ_TAG = "live-reads";

/**
 * Wrap a per-week read whose result is the same for every reader of the page.
 *
 * Same contract as `cachedRead`: `name` identifies the function, the arguments
 * key the entry, and the result must be plain JSON (see `getTeamDirectory` for
 * what a Map does on the first cache hit). Use `cachedLiveMapRead` for a read
 * that returns a Map.
 */
export function cachedLiveRead<Args extends unknown[], Result>(
  name: string,
  read: (...args: Args) => Promise<Result>,
): (...args: Args) => Promise<Result> {
  return unstable_cache(read, [name], {
    revalidate: LIVE_READ_TTL_SECONDS,
    tags: [LIVE_READ_TAG],
  });
}

/**
 * `cachedLiveRead` for a read that returns a Map: the entries are what is
 * cached, and the Map is rebuilt on the way out, on hits and misses alike.
 */
export function cachedLiveMapRead<Args extends unknown[], K, V>(
  name: string,
  read: (...args: Args) => Promise<Map<K, V>>,
): (...args: Args) => Promise<Map<K, V>> {
  const cached = cachedLiveRead(name, async (...args: Args) => [...(await read(...args))]);
  return async (...args: Args) => new Map(await cached(...args));
}
