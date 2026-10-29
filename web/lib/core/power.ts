/**
 * Power ratings and game score (client, 2026-10-29) — the shapes the games
 * page shows. Built by the worker (`worker/core/power.py`, migration
 * 20261029120000): a team's rating is points per game better than an
 * average team on a neutral field, as known BEFORE the week on screen; the
 * game score is 0-100, 60% how good both teams are by power rank and 40% how
 * close the game is. Display only: nothing reads it to make a pick.
 */

/** One game's power numbers at its week. Ranks are null outside the ranked league (FCS). */
export interface GamePower {
  gameId: number;
  homeRank: number | null;
  awayRank: number | null;
  homeRating: number | null;
  awayRating: number | null;
  pHomeWin: number;
  gameScore: number;
}

/** One row of the power rankings table. */
export interface PowerRanking {
  teamId: number;
  rank: number;
  rating: number;
  games: number;
  school: string;
  abbreviation: string | null;
  color: string | null;
  altColor: string | null;
}

/** Amber from 80, green from 65: the same bands as the NBA and NHL pages. */
export function gameScoreTier(score: number): "hot" | "good" | null {
  if (score >= 80) return "hot";
  if (score >= 65) return "good";
  return null;
}

/** "+7.4", "-0.3", "0.0": a rating with its sign and one decimal. */
export function formatPower(rating: number): string {
  const fixed = rating.toFixed(1);
  return rating > 0 && fixed !== "0.0" ? `+${fixed}` : fixed === "-0.0" ? "0.0" : fixed;
}
