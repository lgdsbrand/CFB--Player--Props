/**
 * Player game logs — the actuals behind the hit-rate chart and splits.
 *
 * `player_game_stats` is the single home for realised outcomes, and
 * `v_player_game_log` is its display shape. Reading actuals here is not the
 * lookahead the schema guards against: a completed game is a fact, and the
 * cutoff rules govern what may FEED a projection, not what may be shown next to
 * one after the fact.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { PlayerGameLogRow, PositionGroup } from "@/lib/core/types";
import { type DbRow, unwrap } from "@/lib/data/query";

const COLUMNS =
  "player_id, game_id, season, week, position_group, is_home, " +
  "opponent_team_id, opponent_abbreviation, opponent_school, start_date, " +
  "neutral_site, " +
  "pass_attempts, pass_completions, pass_yards, pass_tds, interceptions, " +
  "rush_attempts, rush_yards, rush_tds, targets, receptions, rec_yards, " +
  "rec_tds, offensive_tds";

/**
 * One player's completed games, most recent first.
 *
 * `before` restricts to games strictly earlier than a week, which is what the
 * board needs: showing week 10's own result beside a week 10 projection would
 * be marking the model's homework with the answer sheet visible. Omit it on a
 * page reviewing a finished week.
 */
export async function getPlayerGameLog(
  playerId: number,
  {
    season,
    before,
    limit = 20,
  }: { season?: number; before?: number; limit?: number } = {},
): Promise<PlayerGameLogRow[]> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("v_player_game_log")
    .select(COLUMNS)
    .eq("player_id", playerId)
    .order("season", { ascending: false })
    .order("week", { ascending: false })
    // THE TIEBREAKS ARE NOT COSMETIC — see `orderGames` in the core, which
    // applies the same rule. CFBD's week 1 spans 9-10 days and holds two games
    // for some teams, so `week desc` alone leaves real ties and Postgres may
    // break them either way between two identical requests. With a LIMIT here,
    // an unstable order can change WHICH games come back at all.
    .order("start_date", { ascending: false, nullsFirst: false })
    .order("game_id", { ascending: false })
    .limit(limit);

  if (season !== undefined) query = query.eq("season", season);
  if (before !== undefined) query = query.lt("week", before);

  return unwrap<DbRow[]>(await query, "v_player_game_log").map(toGameLogRow);
}

export type PlayerIdentity = {
  id: number;
  name: string;
  positionGroup: PositionGroup;
};

/**
 * A player's name and position, independent of any slate.
 *
 * The detail page gets everything else from the board rows, which is the right
 * source — team, opponent and position are all season- or game-scoped. This
 * exists for the one case those cannot cover: an id with no rows in the
 * requested week. That has to render "not on this slate" under the player's
 * actual name, and it has to be distinguishable from an id that does not exist,
 * which is a 404.
 */
export async function getPlayerIdentity(
  playerId: number,
): Promise<PlayerIdentity | null> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("players")
      .select("id, name, position_group")
      .eq("id", playerId)
      .limit(1),
    "players (identity)",
  );
  if (rows.length === 0) return null;

  return {
    id: rows[0].id as number,
    name: rows[0].name as string,
    positionGroup: rows[0].position_group as PositionGroup,
  };
}

/**
 * Players per batch when loading logs for a board page.
 *
 * PostgREST caps a response at 1,000 rows and truncates SILENTLY. A board page
 * of 25 players across a full season is ~300 rows, but the cap is close enough
 * that it has to be a decision rather than an accident: a truncated log would
 * quietly shorten someone's L5 and change the hit rate on screen.
 */
const LOG_BATCH_PLAYERS = 40;

/**
 * Game logs for many players at once, keyed by player.
 *
 * The board's last-5 row needs a log for every player on the page. One read per
 * card would be 25 round trips per render; this is one or two.
 *
 * `before` applies the same rule as the single-player read: grading a week 10
 * projection against week 10's own result would be marking the model's homework
 * with the answers visible.
 */
export async function getGameLogsByPlayer(
  playerIds: number[],
  { season, before }: { season: number; before?: number },
): Promise<Map<number, PlayerGameLogRow[]>> {
  const byPlayer = new Map<number, PlayerGameLogRow[]>();
  if (playerIds.length === 0) return byPlayer;

  const supabase = createServerSupabaseClient();
  const unique = [...new Set(playerIds)];

  for (let start = 0; start < unique.length; start += LOG_BATCH_PLAYERS) {
    const chunk = unique.slice(start, start + LOG_BATCH_PLAYERS);

    let query = supabase
      .from("v_player_game_log")
      .select(COLUMNS)
      .eq("season", season)
      .in("player_id", chunk)
      .order("week", { ascending: false })
      // Same total order as the single-player read and as `orderGames`. It
      // matters most HERE: the batch's player set changes with the board's
      // filters, which changes the plan, which is exactly how the same prop
      // came to show two different L5 figures on two filters of one week.
      .order("start_date", { ascending: false, nullsFirst: false })
      .order("game_id", { ascending: false });

    if (before !== undefined) query = query.lt("week", before);

    for (const raw of unwrap<DbRow[]>(await query, "v_player_game_log (batch)")) {
      const row = toGameLogRow(raw);
      const list = byPlayer.get(row.playerId) ?? [];
      list.push(row);
      byPlayer.set(row.playerId, list);
    }
  }

  return byPlayer;
}

function toGameLogRow(row: Record<string, unknown>): PlayerGameLogRow {
  const n = (key: string) => (row[key] as number | null) ?? null;
  return {
    playerId: row.player_id as number,
    gameId: row.game_id as number,
    season: row.season as number,
    week: row.week as number,
    positionGroup: (row.position_group as PositionGroup | null) ?? null,
    isHome: row.is_home as boolean,
    opponentTeamId: row.opponent_team_id as number,
    opponentAbbreviation: (row.opponent_abbreviation as string | null) ?? null,
    opponentSchool: row.opponent_school as string,
    startDate: (row.start_date as string | null) ?? null,
    neutralSite: row.neutral_site as boolean,

    passAttempts: n("pass_attempts"),
    passCompletions: n("pass_completions"),
    passYards: n("pass_yards"),
    passTds: n("pass_tds"),
    interceptions: n("interceptions"),
    rushAttempts: n("rush_attempts"),
    rushYards: n("rush_yards"),
    rushTds: n("rush_tds"),
    targets: n("targets"),
    receptions: n("receptions"),
    recYards: n("rec_yards"),
    recTds: n("rec_tds"),
    offensiveTds: n("offensive_tds"),
  };
}
