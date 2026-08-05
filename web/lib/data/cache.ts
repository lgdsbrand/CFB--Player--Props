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
 * NOT USED FOR ANYTHING PER-WEEK-PER-PLAYER. Projections, picks, board rows and
 * game logs are all read live. They are what the reader came for, they change
 * when the pipeline runs, and a stale board is a worse failure than a slow one.
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
