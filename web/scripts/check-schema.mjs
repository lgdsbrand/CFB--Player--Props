/**
 * Verify the hand-written read-layer types still match the real database.
 *
 *   npm run check:schema
 *
 * WHY THIS EXISTS. `lib/core/types.ts` is written by hand because the Supabase
 * CLI is not installed here and the project is not linked. Hand-written types
 * carry one specific risk: they keep compiling perfectly after the view they
 * describe has changed underneath them. TypeScript cannot catch that — it has
 * no idea what the database looks like — so the failure surfaces at runtime as
 * an undefined field rendering as a blank cell.
 *
 * So this asks the database directly: select every column each read layer
 * names, and confirm the rows come back with the expected keys. A renamed or
 * dropped column fails here, in a second, instead of in production.
 *
 * It is NOT a type checker. It proves the columns exist and are readable by the
 * anon role; `npm run typecheck` proves the TypeScript is consistent with
 * itself. Both are needed and neither subsumes the other.
 */

import { readFileSync } from "node:fs";
import process from "node:process";

import { createClient } from "@supabase/supabase-js";

function loadEnv() {
  const env = { ...process.env };
  try {
    const file = readFileSync(new URL("../.env.local", import.meta.url), "utf8");
    for (const line of file.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      if (!env[key]) env[key] = trimmed.slice(eq + 1).trim();
    }
  } catch {
    // No .env.local — fall back to the ambient environment (CI).
  }
  return env;
}

/**
 * Every column the read layer selects, per source.
 *
 * Kept as literal lists rather than imported from the query modules: those are
 * TypeScript and this is a plain script, and duplicating ~40 strings is a
 * smaller cost than a build step for a check that must run before the build.
 */
const EXPECTED = {
  v_board_rows: [
    "projection_id", "pick_id", "season", "week", "market_key", "market_name",
    "market_label", "market_emoji", "is_binary", "player_id", "player_name",
    "position_group", "team_id", "team_school", "team_abbreviation",
    "team_color", "team_alt_color", "opponent_team_id", "opponent_school",
    "opponent_abbreviation", "game_id", "start_date", "neutral_site", "is_home",
    "line", "side", "confidence", "model_prob_over", "book_prob_over", "edge",
    "has_book_line", "has_call", "over_price", "under_price", "sportsbook_key",
    "sportsbook_name", "projected_median", "projected_p10", "projected_p90",
    "prior_weight", "opponent_rank_vs_position", "conference_name",
    "conference_is_displayed", "display_confidence", "effective_sample",
  ],
  v_player_game_log: [
    "player_id", "game_id", "season", "week", "position_group", "is_home",
    "opponent_team_id", "opponent_abbreviation", "opponent_school",
    "start_date", "neutral_site",
    "pass_attempts", "pass_completions", "pass_yards", "pass_tds",
    "interceptions", "rush_attempts", "rush_yards", "rush_tds", "targets",
    "receptions", "rec_yards", "rec_tds", "offensive_tds",
  ],
  v_defense_position_game_log: [
    "split_id", "game_id", "defense_team_id", "offense_team_id",
    "offense_school", "offense_abbreviation", "season", "week",
    "position_group", "start_date", "neutral_site", "defense_is_home", "plays",
    "rush_attempts", "rush_yards_allowed", "rush_tds_allowed", "targets",
    "receptions_allowed", "rec_yards_allowed", "rec_tds_allowed",
  ],
  players: ["id", "name", "position_group"],
  ai_reads: ["content", "model", "prompt_version", "generated_at"],
  v_latest_prop_lines: [
    "line_id", "game_id", "player_id", "market_key", "sportsbook_key",
    "sportsbook_name", "line", "over_price", "under_price", "book_prob_over",
    "captured_at",
  ],
  v_slate_weeks: [
    "season", "week", "games", "projections", "players", "first_kickoff",
    "last_kickoff",
  ],
  markets: [
    "key", "display_name", "short_label", "emoji", "stat_column", "is_binary",
    "default_line", "unit", "sort_order", "is_active",
  ],
  market_positions: ["market_key", "position_group", "sort_order"],
  conferences: ["id", "name", "abbreviation", "is_displayed"],
  app_config: ["key", "value"],
  defense_position_ratings: [
    "defense_team_id", "position_group", "as_of_week", "season",
    "games_included", "adj_rush_yards_allowed_pg", "adj_rec_yards_allowed_pg",
    "adj_receptions_allowed_pg", "adj_rush_tds_allowed_pg",
    "adj_rec_tds_allowed_pg", "rank_vs_position", "shrinkage_weight",
  ],
  games: [
    "id", "season", "week", "start_date", "neutral_site", "home_team_id",
    "away_team_id",
  ],
  // Conference membership is season-scoped because realignment moves teams
  // between the seasons this project covers, so the weekly-targets directory
  // reads it here and never from `teams`.
  team_seasons: ["team_id", "season", "conference_id"],
  teams: ["id", "school", "abbreviation", "color", "alt_color"],
};

/** RPCs, which need their argument names checked as well as their columns. */
const EXPECTED_RPC = {
  defense_position_splits_through: {
    args: { p_season: 2025, p_as_of_week: 10 },
    columns: [
      "defense_team_id", "position_group", "games_included", "rush_attempts",
      "rush_yards_allowed", "rush_tds_allowed", "targets", "receptions_allowed",
      "rec_yards_allowed", "rec_tds_allowed", "rush_yards_allowed_pg",
      "rec_yards_allowed_pg",
    ],
  },
};

const env = loadEnv();
const url = env.NEXT_PUBLIC_SUPABASE_URL;
const key = env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!url || !key) {
  console.error(
    "check:schema needs NEXT_PUBLIC_SUPABASE_URL and " +
      "NEXT_PUBLIC_SUPABASE_ANON_KEY (web/.env.local or the environment).",
  );
  process.exit(2);
}

const supabase = createClient(url, key, { auth: { persistSession: false } });
const failures = [];

for (const [source, columns] of Object.entries(EXPECTED)) {
  const { data, error } = await supabase
    .from(source)
    .select(columns.join(", "))
    .limit(1);

  if (error) {
    failures.push(`${source}: ${error.message}`);
    continue;
  }
  if (!data || data.length === 0) {
    // Selecting successfully proves the columns exist; an empty table cannot
    // prove more than that, and is not an error in itself.
    console.log(`  ~ ${source}: columns accepted (no rows to inspect)`);
    continue;
  }
  const missing = columns.filter((column) => !(column in data[0]));
  if (missing.length > 0) {
    failures.push(`${source}: missing ${missing.join(", ")}`);
  } else {
    console.log(`  ok ${source} (${columns.length} columns)`);
  }
}

for (const [fn, spec] of Object.entries(EXPECTED_RPC)) {
  const { data, error } = await supabase.rpc(fn, spec.args);
  if (error) {
    failures.push(`${fn}(): ${error.message}`);
    continue;
  }
  const row = Array.isArray(data) ? data[0] : data;
  if (!row) {
    console.log(`  ~ ${fn}(): callable (no rows to inspect)`);
    continue;
  }
  const missing = spec.columns.filter((column) => !(column in row));
  if (missing.length > 0) {
    failures.push(`${fn}(): missing ${missing.join(", ")}`);
  } else {
    console.log(`  ok ${fn}() (${spec.columns.length} columns)`);
  }
}

if (failures.length > 0) {
  console.error("\nSchema drift detected:");
  for (const failure of failures) console.error(`  ✗ ${failure}`);
  console.error(
    "\nThe read layer names columns the database does not serve to anon. " +
      "Either a migration renamed something, or RLS no longer grants the read.",
  );
  process.exit(1);
}

console.log("\nRead layer matches the database.");
