/**
 * Shared plumbing for the read layer.
 *
 * Every function in `lib/data` reads Supabase with the anon key and returns
 * domain types from `lib/core/types`. Nothing here writes: there are no write
 * policies anywhere in the schema, and the worker is the only thing that
 * mutates data (CLAUDE.md §2).
 */

import type { PostgrestError } from "@supabase/supabase-js";

/** An untyped result row, as PostgREST returns it. */
export type DbRow = Record<string, unknown>;

/**
 * Turn a Supabase result into data, or throw with enough context to fix it.
 *
 * WHY THROW RATHER THAN RETURN EMPTY. A denied read and an empty week look
 * identical downstream once the error is dropped — the board renders "no picks
 * this week" whether the model produced none or RLS refused the query. One of
 * those is a normal Tuesday and the other is an outage, and a board that cannot
 * tell them apart will confidently show the wrong one. Next's error boundary
 * turns a throw into a visible failure, which is the correct outcome.
 *
 * WHY IT CASTS. Without generated database types the Supabase client cannot
 * infer a row shape from a select list built by concatenation, and falls back
 * to an error type. Rather than sprinkle assertions through every query, the
 * cast happens here, once, and each caller states the shape it expects. That
 * shape is not taken on trust: `npm run check:schema` selects the same columns
 * against the live database, so a rename fails a check instead of rendering as
 * a blank cell.
 */
export function unwrap<T>(
  result: { data: unknown; error: PostgrestError | null },
  context: string,
): T {
  if (result.error) {
    throw new Error(
      `Read failed (${context}): ${result.error.message}` +
        (result.error.hint ? ` — ${result.error.hint}` : ""),
    );
  }
  if (result.data === null) {
    throw new Error(`Read returned no data (${context}).`);
  }
  return result.data as T;
}

/**
 * Rows PostgREST will return in ONE response, no matter what range is asked
 * for. This is Supabase's `db-max-rows` and it is a server setting, not a
 * client default — `range(0, 11999)` returns 1,000 rows and reports a count of
 * 3,788 without complaint.
 *
 * Verified against the live project rather than assumed, because getting it
 * wrong is silent: a truncated result looks exactly like a short one. Anything
 * that must see a whole week has to page in chunks of this size and compare
 * what it got against the exact count.
 */
export const MAX_ROWS_PER_REQUEST = 1000;

/** Numeric columns arrive as JSON numbers, but nullable ones arrive as null. */
export function num(value: unknown): number | null {
  return typeof value === "number" ? value : value === null ? null : Number(value);
}

/** Same, for a column the schema guarantees is NOT NULL. */
export function requireNum(value: unknown, column: string): number {
  const parsed = num(value);
  if (parsed === null || Number.isNaN(parsed)) {
    throw new Error(`Expected a number in ${column}, got ${String(value)}`);
  }
  return parsed;
}
