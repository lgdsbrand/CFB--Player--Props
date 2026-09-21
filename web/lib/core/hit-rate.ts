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

  // First-quarter markets grade against the same log, one period narrower.
  // NFL only in practice — every college row is null here, and a null value is
  // already skipped rather than counted as a miss.
  q1_pass_yards: "q1PassYards",
  q1_rush_attempts: "q1RushAttempts",
  q1_rush_yards: "q1RushYards",
  q1_rush_tds: "q1RushTds",
  q1_targets: "q1Targets",
  q1_receptions: "q1Receptions",
  q1_rec_yards: "q1RecYards",
  q1_rec_tds: "q1RecTds",
  q1_offensive_tds: "q1OffensiveTds",
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
  /**
   * Kickoff, carried so the sort below has a chronological tiebreak within a
   * week. Nullable because `games.venue_id` and `start_date` both are for games
   * CFBD posts far ahead — see `orderGames` for what happens when it is missing.
   */
  startDate: string | null;
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
        startDate: game.startDate,
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
    .sort(orderGames);
}

/**
 * Most recent game first — and TOTAL, which is the whole point.
 *
 * THE BUG THIS FIXES WAS VISIBLE ON THE BOARD AND NONDETERMINISTIC. Sorting on
 * `(season, week)` alone leaves ties, because CFBD's week 1 spans 9-10 days and
 * really does hold two games for some teams — Gabe Burkle played Kansas State
 * and South Dakota, both filed as 2025 week 1. With six games and a five-game
 * window, WHICH of those two fell inside L5 decided the hit rate, and nothing
 * decided which: `Array.sort` is stable, so it preserved whatever order Postgres
 * returned, and Postgres is free to return them either way for a query whose
 * only ORDER BY is `week desc`. The same prop showed "2 of 2" on one filter and
 * "2 of 3" on another, off identical data.
 *
 * `start_date` is the honest tiebreak — within a week, the later kickoff is the
 * more recent game. `gameId` is the last resort, and it is there for
 * determinism rather than for meaning: CFBD ids are not guaranteed
 * chronological, but they ARE unique, so the comparator is a total order and
 * two renders of the same games cannot disagree. A game with no kickoff sorts
 * after games that have one, rather than winning the tie by being null.
 */
export function orderGames(
  a: Pick<GradedGame, "season" | "week" | "startDate" | "gameId">,
  b: Pick<GradedGame, "season" | "week" | "startDate" | "gameId">,
): number {
  if (a.season !== b.season) return b.season - a.season;
  if (a.week !== b.week) return b.week - a.week;
  if (a.startDate !== b.startDate) {
    if (a.startDate === null) return 1;
    if (b.startDate === null) return -1;
    return a.startDate < b.startDate ? 1 : -1;
  }
  return b.gameId - a.gameId;
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

/**
 * A short season's sample, topped up with last season's most recent games.
 *
 * THE CLIENT'S RULE, 2026-09-10: early in a season "Last 5" reaches back into
 * last season rather than grading one game, unless the player is a rookie.
 * Rookies need no special case — they have no games last season to borrow.
 *
 * EVERY GAME FROM THIS SEASON IS KEPT, and last season only fills the gap up to
 * `minGames`. So the borrowing stops by itself once this season has enough, and
 * a player's current form is never pushed out by an older game.
 *
 * SPORT-AGNOSTIC: this only trims what it is given. Whether last season is
 * loaded at all is a sport policy (`borrowsPriorSeasonForm`), and a log holding
 * one season passes through unchanged.
 *
 * GENERIC OVER THE ROW so the ungraded path can share it. A `FormGame`
 * (`lib/core/form.ts`) has no line and no outcome, but topping a sample up is
 * pure chronology — it reads season, week, kickoff and id and nothing else.
 * Two copies of this trimming rule would be free to disagree about which games
 * a young NFL season borrows, and then the chart and the columns beside it
 * would describe different games.
 *
 * THE L5/L10 WINDOWS DO NOT NEED IT, and it is worth knowing why. `orderGames`
 * puts every game from this season ahead of every game from last, so the first
 * N games of a two-season log already ARE this season topped up. This exists
 * for the surfaces that read a whole sample — the venue and rank splits and the
 * game log — where an untrimmed log would pull in all of last season and keep
 * it there until December.
 */
export function topUpFromPriorSeason<
  T extends Pick<GradedGame, "season" | "week" | "startDate" | "gameId">,
>(graded: T[], season: number, minGames: number): T[] {
  const ordered = [...graded].sort(orderGames);
  const current = ordered.filter((game) => game.season === season);
  const shortfall = minGames - current.length;
  if (shortfall <= 0) return current;
  const prior = ordered
    .filter((game) => game.season === season - 1)
    .slice(0, shortfall);
  return [...current, ...prior];
}

/** How many games in a sample were played before `season` began. */
export function priorSeasonCount(
  games: readonly Pick<GradedGame, "season">[],
  season: number,
): number {
  return games.filter((game) => game.season < season).length;
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
 * Narrow a log to one venue, for the board's venue filter.
 *
 * GENERIC OVER THE ROW, and that is the point: it is applied to the RAW game
 * log before anything reads it, so the hit-rate columns, the season-to-date
 * column and the usage figures beside them are all computed from the same set
 * of games. Filtering only the graded games would have left a card showing
 * "L5 at home" next to a target share over every game, which is two different
 * denominators under one heading.
 *
 * ORDER MATTERS AND IT IS THE OTHER REASON THIS EXISTS. It runs BEFORE a window
 * is taken, so `venue=home, window=5` is "his last five HOME games" and not
 * "the home games among his last five". The second reading gives a column
 * labelled L5 a denominator that moves between 0 and 5 for reasons the reader
 * cannot see, and on a college schedule it is 0 often enough to look broken.
 *
 * NEUTRAL SITES ARE IN NEITHER BUCKET, exactly as in `splitByVenue` — the one
 * rule, written once. So `home` and `away` need not sum to `all`.
 *
 * `all` returns the same array rather than a copy: nothing downstream mutates
 * it, and the board does this per visible row on every render.
 *
 * THE UNION IS SPELLED OUT RATHER THAN IMPORTED as `BoardVenue`. This module is
 * loaded by Node's test runner, which does not understand the `@/*` alias, and
 * `board-params` is the board's URL contract — a core primitive should not
 * depend on it. Keep the two in step by hand; they are three literals.
 */
export function gamesAtVenue<T extends { isHome: boolean; neutralSite: boolean }>(
  games: T[],
  venue: "all" | "home" | "away",
): T[] {
  if (venue === "all") return games;
  return venue === "home"
    ? games.filter((g) => g.isHome && !g.neutralSite)
    : games.filter((g) => !g.isHome && !g.neutralSite);
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

/**
 * Semantic colour token for a hit rate. A token name, never a hex value — the
 * colours live in `app/globals.css` so the reskin stays one file.
 *
 * SHARED BY THE LAST-5 ROW AND THE TABLE'S HIT-RATE COLUMNS, so a figure cannot
 * be tinted one way on a card and another way in a table of the same week.
 *
 * THE BANDS ARE DELIBERATELY WIDE AND THE MIDDLE ONE IS DELIBERATELY GREY. A
 * hit rate near 50% over five games is noise, and colouring it would dress four
 * coin flips up as a signal — which on a board whose whole claim is calibration
 * is the one lie that matters. Only a rate far enough from even to survive a
 * five-game sample gets a colour.
 *
 * A NULL RATE IS NOT ZERO and gets no colour at all. Nothing was decided; a red
 * dash would read as "never hits".
 */
export function hitRateTone(
  rate: number | null,
): "positive" | "negative" | "muted" {
  if (rate === null) return "muted";
  if (rate >= 0.6) return "positive";
  if (rate <= 0.4) return "negative";
  return "muted";
}
