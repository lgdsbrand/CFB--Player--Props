import type { BoardRow } from "@/lib/core/types";

/**
 * A player can have TWO games in one week, and the page has to pick one.
 *
 * Not a hypothetical and not a data fault: CFBD's week 1 spans nine or ten days
 * in every season on record, because what the sport calls Week 0 and Week 1 are
 * both `week: 1` to the API — verified against it, which reports week 1 for all
 * 211 games from 27 Aug to 7 Sep 2026. Teams playing twice inside that window:
 * 13 in 2023, 6 in 2024, 8 in 2025, 12 in 2026. Week 21, the postseason offset,
 * does the same for two to five teams a season.
 *
 * THE BOARD IS RIGHT TO SHOW BOTH. Memphis's receiver really does play UNLV on
 * 30 Aug and Arkansas State on 5 Sep, so two cards with two opponents is correct
 * output. It is the PLAYER PAGE that assumed one game per player-week: it read
 * every row for the week, sorted them by market, and rendered interleaved
 * duplicate tabs — then described whichever game happened to sort first in its
 * header, its defense panel and its conditions panel.
 */

export type PlayerGame = {
  gameId: number;
  startDate: string | null;
  opponentTeamId: number;
  opponentSchool: string;
  opponentAbbreviation: string | null;
  isHome: boolean;
  neutralSite: boolean;
};

/**
 * The distinct games behind a player's rows, in kickoff order.
 *
 * A game with no kickoff time sorts last rather than first. `start_time_tbd` is
 * a real state in this schema and it means the game has not been scheduled yet,
 * which makes it the LAST thing to happen, not the earliest — sorting nulls
 * first would open the page on a game with no date over one kicking off today.
 */
export function orderedGames(rows: BoardRow[]): PlayerGame[] {
  const byId = new Map<number, PlayerGame>();

  for (const row of rows) {
    if (byId.has(row.gameId)) continue;
    byId.set(row.gameId, {
      gameId: row.gameId,
      startDate: row.startDate,
      opponentTeamId: row.opponentTeamId,
      opponentSchool: row.opponentSchool,
      opponentAbbreviation: row.opponentAbbreviation,
      isHome: row.isHome,
      neutralSite: row.neutralSite,
    });
  }

  return [...byId.values()].sort((a, b) => {
    if (a.startDate === null && b.startDate === null) return a.gameId - b.gameId;
    if (a.startDate === null) return 1;
    if (b.startDate === null) return -1;
    const delta = Date.parse(a.startDate) - Date.parse(b.startDate);
    // Game id as a tiebreak so two kickoffs at the same minute keep a stable
    // order across requests, the same reason `getSlateGames` does it.
    return delta !== 0 ? delta : a.gameId - b.gameId;
  });
}

/**
 * Which game the page opens on: the next one not yet kicked off.
 *
 * LOOK FORWARD, falling back to the most recent. The same rule the week
 * selector settled on — a reader arriving mid-week wants the game about to be
 * played, not the one finished on Saturday, and once both are done the later
 * one is the useful default. Opening on the earliest would strand a player on a
 * game from nine days ago for the whole of week 1.
 */
export function defaultGameId(
  games: PlayerGame[],
  now: Date = new Date(),
): number | null {
  if (games.length === 0) return null;
  const upcoming = games.find(
    (game) => game.startDate === null || Date.parse(game.startDate) > now.getTime(),
  );
  return (upcoming ?? games[games.length - 1]).gameId;
}

/** The requested game if the player actually has it, otherwise the default. */
export function resolveGameId(
  games: PlayerGame[],
  requested: number | undefined,
  now: Date = new Date(),
): number | null {
  if (requested !== undefined && games.some((game) => game.gameId === requested)) {
    return requested;
  }
  return defaultGameId(games, now);
}

export function rowsForGame(rows: BoardRow[], gameId: number | null): BoardRow[] {
  if (gameId === null) return rows;
  return rows.filter((row) => row.gameId === gameId);
}
