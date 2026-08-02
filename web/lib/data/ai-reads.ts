/**
 * The cached weekly AI read (CLAUDE.md §7).
 *
 * A READ, NEVER A GENERATION. `ai_reads` has a unique key on
 * (player, season, week) and that key IS the cache: a weekly worker job writes
 * one row per player and the app only ever selects. Per-page-view LLM calls are
 * explicitly out of scope (§2, §10), so there is deliberately no code path here
 * that could call a model — not a lazy one, not a fallback.
 *
 * Phase 5 writes the rows. Until then this returns null on every player, which
 * is the same thing it will return for a player the generator skipped, so the
 * empty state is the real one rather than a placeholder that disappears later.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";

export type AiRead = {
  content: string;
  model: string;
  promptVersion: string;
  generatedAt: string;
};

export async function getAiRead(
  playerId: number,
  season: number,
  week: number,
): Promise<AiRead | null> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("ai_reads")
      .select("content, model, prompt_version, generated_at")
      .eq("player_id", playerId)
      .eq("season", season)
      .eq("week", week)
      .limit(1),
    "ai_reads",
  );
  if (rows.length === 0) return null;

  return {
    content: rows[0].content as string,
    model: rows[0].model as string,
    promptVersion: rows[0].prompt_version as string,
    generatedAt: rows[0].generated_at as string,
  };
}
