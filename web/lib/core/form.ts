/**
 * Recent form WITHOUT a line to grade against.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * WHY THIS EXISTS. Everything in `hit-rate.ts` needs a line: it answers "did
 * the called side win", and with no posted number there is no side and no
 * answer. So the moment a book has not priced a prop, the chart, the L5/L10
 * columns, the splits and the game log all went empty — which on a board whose
 * odds have been frozen since 12 September is most of it. The client asked for
 * this directly on 2026-09-21: "even if theres not a book line it would still
 * show their last 5/10 stats and would make the mark on where the model
 * projects them at".
 *
 * AN AVERAGE IS NOT A HIT RATE AND MUST NEVER RENDER LIKE ONE. "62.4" and "62%"
 * are four characters apart and mean unrelated things. Every surface showing a
 * figure from this module has to make the difference obvious — no percent sign,
 * its own tooltip, and the denominator in games. The one thing that must not
 * happen is an average appearing in a column a reader has learned to read as a
 * rate.
 *
 * WHAT THIS DELIBERATELY DOES NOT DO: invent a line. Grading past games against
 * the model's own projection would produce a real-looking percentage that
 * answers no question anybody asked — "how often did he beat what we think he
 * will do" — and on a model that loses to closing lines
 * (`model-probabilities-lose-to-a-coin-flip`) dressing that up as a hit rate
 * would be the most misleading number on the site. The projection appears as a
 * MARK on the chart, next to the bars, and nothing is scored against it.
 */

// Relative, with the extension. `lib/core` is imported both by the Next bundler
// (which understands `@/*`) and by Node's test runner (which does not), so any
// import carrying a VALUE has to resolve without the alias.
import { orderGames, statValue } from "./hit-rate.ts";

import type { PlayerGameLogRow } from "@/lib/core/types";

/**
 * One past game's value for a market, with no outcome attached.
 *
 * DELIBERATELY NOT A `GradedGame` WITH NULLS. That type's `line` and `outcome`
 * are non-nullable because every consumer of it is entitled to assume a game
 * was graded; widening them to null would push a "did this actually get graded"
 * check into every one of those call sites. A separate, smaller type makes the
 * ungraded case impossible to pass somewhere that needs a grade.
 */
export type FormGame = {
  gameId: number;
  season: number;
  week: number;
  startDate: string | null;
  value: number;
  opponentTeamId: number;
  opponentAbbreviation: string | null;
  isHome: boolean;
  neutralSite: boolean;
};

/**
 * A player's games as raw values for one market, most recent first.
 *
 * Games with no value for the market are dropped, exactly as `gradeGames`
 * drops them — a receiver's log carries nulls for `pass_yards`, and counting
 * those as zeroes would drag every average toward nothing.
 */
export function formGames(
  games: PlayerGameLogRow[],
  statColumn: string,
): FormGame[] {
  return games
    .map((game) => {
      const value = statValue(game, statColumn);
      if (value === null) return null;
      return {
        gameId: game.gameId,
        season: game.season,
        week: game.week,
        startDate: game.startDate,
        value,
        opponentTeamId: game.opponentTeamId,
        opponentAbbreviation: game.opponentAbbreviation,
        isHome: game.isHome,
        neutralSite: game.neutralSite,
      } satisfies FormGame;
    })
    .filter((game): game is FormGame => game !== null)
    // THE SAME COMPARATOR THE GRADED PATH USES, not a second sort written here.
    // `orderGames` is a total order for a reason: CFBD's week 1 spans ten days
    // and really does hold two games for some teams, and a tie there once made
    // a hit rate nondeterministic between renders.
    .sort(orderGames);
}

export type FormSummary = {
  /** The window asked for, which may exceed the games available. */
  window: number;
  /** Games actually counted. Below `window` whenever the log is shorter. */
  played: number;
  /** Null when nothing was played — never silently 0, which reads as a real 0. */
  mean: number | null;
  /** Highest and lowest in the window, so a mean is not read as a typical game. */
  min: number | null;
  max: number | null;
  games: FormGame[];
};

/**
 * Mean value over the most recent `window` games.
 *
 * MIN AND MAX TRAVEL WITH THE MEAN. A 60-yard average from games of 12 and 108
 * is a different player from one who goes 58, 61, 61, and the mean alone cannot
 * tell them apart. The same instinct as `splits.ts` carrying `decided` beside
 * every rate: the figure that qualifies the headline number ships with it
 * rather than being left for the reader to ask about.
 */
export function formSummary(form: FormGame[], window: number): FormSummary {
  const games = form.slice(0, Math.max(window, 0));
  if (games.length === 0) {
    return { window, played: 0, mean: null, min: null, max: null, games };
  }

  let total = 0;
  let min = games[0]!.value;
  let max = games[0]!.value;
  for (const game of games) {
    total += game.value;
    if (game.value < min) min = game.value;
    if (game.value > max) max = game.value;
  }

  return {
    window,
    played: games.length,
    mean: total / games.length,
    min,
    max,
    games,
  };
}

/** A named subset of the log with its average. Mirrors `Split` in `splits.ts`. */
export type FormSplit = {
  key: string;
  label: string;
  hint?: string;
  summary: FormSummary;
};

/**
 * Recent-form windows, e.g. L5 and L10 — the ungraded twin of `windowSplits`.
 *
 * The windows come from `app_config.hit_rate_windows` like the graded ones, so
 * the two surfaces cannot end up describing different spans of games.
 */
export function formWindows(form: FormGame[], windows: number[]): FormSplit[] {
  return windows.map((window) => ({
    key: `l${window}`,
    label: `L${window}`,
    summary: formSummary(form, window),
  }));
}

/**
 * Average across this season to date — the ungraded twin of `seasonToDate`.
 *
 * SEASON ONLY, enforced here rather than assumed of the caller, for the same
 * reason the graded version enforces it: on the NFL the log deliberately reaches
 * into last season to fill a young L5, and those games must not leak into a
 * figure labelled SZN.
 */
export function formSeasonToDate(
  form: FormGame[],
  season: number,
): FormSummary | null {
  const current = form.filter((game) => game.season === season);
  if (current.length === 0) return null;
  return formSummary(current, current.length);
}

/**
 * The mean formatted for display, or an em dash.
 *
 * NO PERCENT SIGN, EVER, and one decimal place. The decimal is not precision
 * theatre: it is the cheapest available signal that this figure is not a rate.
 * A bare "62" in a column beside "61%" is ambiguous; "62.4" is not.
 *
 * Whole-number markets still get the decimal for that reason — 1.0 receptions
 * reads as an average, "1" reads as a count.
 */
export function formatFormMean(mean: number | null): string {
  return mean === null ? "—" : mean.toFixed(1);
}
