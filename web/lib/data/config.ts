/**
 * Runtime configuration from `app_config`.
 *
 * These values are read, never hardcoded. CLAUDE.md §9 leaves the odds source
 * and the hit-rate basis open with the client, and §6's edge threshold is a
 * number they may want to tune — all of which have to be changeable without a
 * deploy. `app_config` is world-readable and holds no secrets by design.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { AppConfig, HitRateBasis } from "@/lib/core/types";
import { unwrap } from "@/lib/data/query";

/**
 * Fallbacks matching the seed migration.
 *
 * Used ONLY for a key missing from the table, which means the seed did not run
 * — not for a failed read, which throws. The distinction matters: a missing key
 * is a deployment that is behind, and a failed read is an outage.
 */
const DEFAULTS: AppConfig = {
  edgeThreshold: 0.05,
  edgesOnlyDefault: false,
  hitRateBasis: "threshold",
  hitRateWindows: [5, 10],
  minGamesForDefenseRank: 2,
};

const KEYS = [
  "edge_threshold",
  "edges_only_default",
  "hit_rate_basis",
  "hit_rate_windows",
  "min_games_for_defense_rank",
] as const;

export async function getAppConfig(): Promise<AppConfig> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<{ key: string; value: unknown }[]>(
    await supabase.from("app_config").select("key, value").in("key", KEYS),
    "app_config",
  );

  const byKey = new Map(rows.map((row) => [row.key, row.value]));

  return {
    edgeThreshold: asNumber(byKey.get("edge_threshold"), DEFAULTS.edgeThreshold),
    edgesOnlyDefault: asBoolean(
      byKey.get("edges_only_default"),
      DEFAULTS.edgesOnlyDefault,
    ),
    hitRateBasis: asBasis(byKey.get("hit_rate_basis")),
    hitRateWindows: asNumberArray(
      byKey.get("hit_rate_windows"),
      DEFAULTS.hitRateWindows,
    ),
    minGamesForDefenseRank: asNumber(
      byKey.get("min_games_for_defense_rank"),
      DEFAULTS.minGamesForDefenseRank,
    ),
  };
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asNumberArray(value: unknown, fallback: number[]): number[] {
  return Array.isArray(value) && value.every((v) => typeof v === "number")
    ? value
    : fallback;
}

/**
 * An unrecognised basis falls back to `threshold` rather than throwing.
 *
 * Threshold needs nothing but the game log, so it always renders. Honouring an
 * unknown value, or failing the page, would take the board down over a typo in
 * a config row.
 */
function asBasis(value: unknown): HitRateBasis {
  return value === "closing_line" ? "closing_line" : "threshold";
}
