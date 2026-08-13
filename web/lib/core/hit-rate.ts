/**
 * Hit rates over a player's recent games.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * WHY THIS IS IN THE APP AND NOT THE DATABASE. A hit rate is a question about a
 * LINE, and the line is only known at render time — the board shows the current
 * line, and the same game log produces a different hit rate against a different
 * one. Computing it in SQL would mean either materialising a figure per
 * (player, market, line) or freezing the line into the data.
 *
 * THE BASIS IS CONFIGURATION, NOT A CONSTANT (CLAUDE.md §9.2, settled to
 * `threshold` in migration 0017). Two readings of "did this player hit":
 *
 *   threshold     grade every past game against ONE line — the line showing
 *                 now. Cheap, needs no historical odds, and is what most props
 *                 sites do.
 *   closing_line  grade each past game against the line that game closed at.
 *                 Truer, and needs a paid line backfill we do not have.
 *
 * Threshold is the default on merit: college books post props late and
 * selectively, so closing-line grading would leave the L10 chart mostly gaps —
 * and a gap reads to a user as "did not play" rather than "no line existed".
 * `gradeGames` takes the basis so the second method is a branch, not a rewrite.
 */

import type { BetSide, PlayerGameLogRow } from "@/lib/core/types";

/**
 * `markets.stat_column` → the field carrying that stat on a game-log row.
 *
 * Explicit rather than derived by case conversion. The database is the source
 * of truth for the market catalogue, so a market can be added by INSERT with no
 * deploy — and if that happens, an unmapped key must surface as "no data"
 * rather than as a silently wrong number from a coincidental name match.
 */
const STAT_COLUMN_FIELDS = {
  pass_attempts: "passAttempts",
  pass_completions: "passCompletions",
  pass_yards: "passYards",
  pass_tds: "passTds",
  interceptions: "interceptions",
  rush_attempts: "rushAttempts",
  rush_yards: "rushYards",
  rush_tds: "rushTds",
  targets: "targets",
  receptions: "receptions",
  rec_yards: "recYards",
  rec_tds: "recTds",
  offensive_tds: "offensiveTds",
} as const satisfies Record<string, keyof PlayerGameLogRow>;

export function statValue(
  game: PlayerGameLogRow,
  statColumn: string,
): number | null {
  const field = STAT_COLUMN_FIELDS[statColumn as keyof typeof STAT_COLUMN_FIELDS];
  if (!field) return null;
  const value = game[field];
  return typeof value === "number" ? value : null;
}

/** How one past game resolved against a line. */
export type GameOutcome = "over" | "under" | "push";

export type GradedGame = {
  gameId: number;
  season: number;
  week: number;
  value: number;
  line: number;
  outcome: GameOutcome;
  /** Did the CALLED side win this game? Null on a push. */
  hit: boolean | null;
  opponentTeamId: number;
  opponentAbbreviation: string | null;
  isHome: boolean;
  neutralSite: boolean;
};

export type HitRateSummary = {
  window: number;
  hits: number;
  /** Games counted — pushes excluded, so this can be below `window`. */
  decided: number;
  pushes: number;
  /** Null when nothing was decided; never silently 0, which reads as 0%. */
  rate: number | null;
  games: GradedGame[];
};

/**
 * Grade a player's games against one line, most recent first.
 *
 * PUSHES ARE EXCLUDED FROM THE DENOMINATOR rather than counted as losses. Books
 * post half-points precisely to avoid them, so on a real line this never fires
 * — but a synthetic development line is the player's rounded trailing average
 * and lands on a whole number often, and `pass_tds` sits at 1.0 or 2.0 where
 * exact ties are common. The model's own grading treats `value > line` as over,
 * which would quietly file every tie as an under-hit and inflate that side.
 */
/**
 * How one value resolves against one line.
 *
 * EXPORTED SO THE LINE STEPPER CANNOT DRIFT FROM THIS. The stepper re-grades
 * the same games against a shifted line in the browser, and a second copy of
 * `value > line` written there would be free to disagree about the push — which
 * is the one case the rule exists to get right, and the one nobody would notice
 * being wrong.
 */
export function outcomeFor(value: number, line: number): GameOutcome {
  return value > line ? "over" : value < line ? "under" : "push";
}

/** Did the CALLED side win? Null on a push, which is excluded, not lost. */
export function didHit(outcome: GameOutcome, side: BetSide): boolean | null {
  return outcome === "push" ? null : outcome === side;
}

export function gradeGames(
  games: PlayerGameLogRow[],
  statColumn: string,
  line: number,
  side: BetSide,
): GradedGame[] {
  return games
    .map((game) => {
      const value = statValue(game, statColumn);
      if (value === null) return null;

      const outcome = outcomeFor(value, line);

      return {
        gameId: game.gameId,
        season: game.season,
        week: game.week,
        value,
        line,
        outcome,
        hit: didHit(outcome, side),
        opponentTeamId: game.opponentTeamId,
        opponentAbbreviation: game.opponentAbbreviation,
        isHome: game.isHome,
        neutralSite: game.neutralSite,
      } satisfies GradedGame;
    })
    .filter((game): game is GradedGame => game !== null)
    .sort((a, b) =>
      a.season === b.season ? b.week - a.week : b.season - a.season,
    );
}

/**
 * Hit rate over the most recent `window` games.
 *
 * THE WINDOW COUNTS APPEARANCES, NOT WEEKS. `v_player_game_log` has a row only
 * where the player recorded a box score, so "last 5" means the last five games
 * he played — skipping a bye or an injury rather than scoring it as a loss.
 * That matches how a reader interprets "L5" and how the model treats a
 * did-not-play, which it declines to grade at all.
 */
export function hitRate(graded: GradedGame[], window: number): HitRateSummary {
  const slice = graded.slice(0, window);
  const decided = slice.filter((game) => game.hit !== null);
  const hits = decided.filter((game) => game.hit === true).length;

  return {
    window,
    hits,
    decided: decided.length,
    pushes: slice.length - decided.length,
    rate: decided.length > 0 ? hits / decided.length : null,
    games: slice,
  };
}

/** Home / away split. Neutral sites count as neither (CLAUDE.md §7). */
export function splitByVenue(graded: GradedGame[]): {
  home: GradedGame[];
  away: GradedGame[];
  neutral: GradedGame[];
} {
  return {
    home: graded.filter((g) => g.isHome && !g.neutralSite),
    away: graded.filter((g) => !g.isHome && !g.neutralSite),
    neutral: graded.filter((g) => g.neutralSite),
  };
}

/**
 * Hit rate as the board displays it: a whole-number percentage, or an em dash.
 *
 * A null rate means nothing was decided, which is NOT 0%. Rendering "0%" for
 * "no games" is the kind of small lie that makes a board untrustworthy.
 */
export function formatHitRate(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}
