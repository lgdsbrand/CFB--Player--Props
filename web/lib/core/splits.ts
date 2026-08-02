/**
 * Hit-rate splits for the player detail view (CLAUDE.md §7).
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * Four cuts of the same graded log: recent windows (L5 / L10), venue, and
 * opponent strength vs the player's position. Nothing here re-grades anything —
 * `gradeGames` decided each game's outcome once, against one line, and these
 * only partition that result.
 *
 * WHY THE SAMPLE SIZE TRAVELS WITH EVERY RATE. A college season is twelve
 * games, so an away split is five or six of them and a rank band can be two.
 * "67% away" from three games is not a fact about the player, and a UI that
 * shows the percentage without the denominator invites reading it as one. Every
 * summary here carries `decided`, and the caller is expected to show it.
 */

// Relative, with the extension. `lib/core` is imported both by the Next
// bundler (which understands `@/*`) and by Node's test runner (which does not),
// so any import carrying a VALUE has to resolve without the alias. Type-only
// imports may keep it — they are erased before Node sees the file.
import {
  hitRate,
  splitByVenue,
  type GradedGame,
  type HitRateSummary,
} from "./hit-rate.ts";

/** A named subset of the log with its hit rate. */
export type Split = {
  key: string;
  label: string;
  /** Longer explanation for a tooltip; omitted where the label says it all. */
  hint?: string;
  summary: HitRateSummary;
};

/** Hit rate over every game in a bucket, rather than a fixed-length window. */
export function summariseAll(games: GradedGame[]): HitRateSummary {
  return hitRate(games, games.length);
}

/**
 * Recent-form windows, e.g. L5 and L10.
 *
 * The windows come from `app_config.hit_rate_windows` rather than a constant:
 * CLAUDE.md §7 names L5 and L10, and the client may want a third without a
 * deploy. A window longer than the log is not an error — it uses what exists,
 * and the denominator says so.
 */
export function windowSplits(
  graded: GradedGame[],
  windows: number[],
): Split[] {
  return windows.map((window) => ({
    key: `l${window}`,
    label: `L${window}`,
    summary: hitRate(graded, window),
  }));
}

/**
 * Home / away / neutral.
 *
 * SECONDARY IN THIS SPORT, ON PURPOSE (CLAUDE.md §7). College schedules are
 * uneven and neutral-site games are common, so a home/away split here rests on
 * fewer games than the same split would in the NFL — where it is a first-class
 * filter. Neutral games are their own bucket rather than being folded into
 * either side, because folding them would put a bowl game in the "home" column
 * for whichever team the schedule happened to list first.
 *
 * Buckets with no games are dropped: an empty column reads as a zero.
 */
export function venueSplits(graded: GradedGame[]): Split[] {
  const byVenue = splitByVenue(graded);
  return [
    { key: "home", label: "Home", games: byVenue.home },
    { key: "away", label: "Away", games: byVenue.away },
    { key: "neutral", label: "Neutral", games: byVenue.neutral },
  ]
    .filter((bucket) => bucket.games.length > 0)
    .map((bucket) => ({
      key: bucket.key,
      label: bucket.label,
      summary: summariseAll(bucket.games),
    }));
}

/**
 * A band of opponent ranks vs the player's position.
 *
 * RANK 1 ALLOWS THE MOST. The scale is inverted relative to a conventional
 * defensive ranking, so a LOW rank is the SOFT matchup — the labels here carry
 * that, and every surface showing a raw rank has to say it too.
 */
export type RankBand = {
  key: "soft" | "middle" | "tough";
  label: string;
  hint: string;
  minRank: number;
  maxRank: number;
};

/**
 * Split the ranked field into thirds.
 *
 * Terciles rather than a fixed cut like "top 32": the number of FBS defenses
 * moves with realignment (it is 130-odd and has changed twice recently), so a
 * hardcoded boundary would quietly stop meaning "a third" (CLAUDE.md §4).
 */
export function rankBands(rankedDefenses: number): RankBand[] {
  const third = Math.max(Math.ceil(rankedDefenses / 3), 1);
  return [
    {
      key: "soft",
      label: "Soft",
      hint: `Opponent ranked 1–${third} vs the position — the defenses allowing the MOST`,
      minRank: 1,
      maxRank: third,
    },
    {
      key: "middle",
      label: "Middle",
      hint: `Opponent ranked ${third + 1}–${third * 2} vs the position`,
      minRank: third + 1,
      maxRank: third * 2,
    },
    {
      key: "tough",
      label: "Tough",
      hint: `Opponent ranked ${third * 2 + 1}+ vs the position — the defenses allowing the LEAST`,
      minRank: third * 2 + 1,
      maxRank: Number.POSITIVE_INFINITY,
    },
  ];
}

/**
 * Hit rate against each band of opponent strength.
 *
 * `rankByGameId` maps a past game to the opponent's rank AS IT STOOD ENTERING
 * THAT WEEK, which is the caller's job to look up (see `getDefenseRanksAt`).
 * Ranking those opponents by where they finished would answer a question nobody
 * could have asked at the time.
 *
 * GAMES WITH NO RANK ARE DROPPED, NOT BUCKETED. A defense goes unranked when
 * the week has too few games behind it or the opponent is not FBS. Filing those
 * under "tough" or "soft" would invent a matchup difficulty; the count of
 * dropped games is returned so the UI can say how many it set aside.
 */
export function rankSplits(
  graded: GradedGame[],
  rankByGameId: Map<number, number>,
  bands: RankBand[],
): { splits: Split[]; unranked: number } {
  let unranked = 0;
  const buckets = new Map<string, GradedGame[]>();

  for (const game of graded) {
    const rank = rankByGameId.get(game.gameId);
    if (rank === undefined) {
      unranked += 1;
      continue;
    }
    const band = bands.find((b) => rank >= b.minRank && rank <= b.maxRank);
    if (!band) {
      unranked += 1;
      continue;
    }
    const list = buckets.get(band.key) ?? [];
    list.push(game);
    buckets.set(band.key, list);
  }

  const splits = bands
    .filter((band) => (buckets.get(band.key)?.length ?? 0) > 0)
    .map((band) => ({
      key: band.key,
      label: band.label,
      hint: band.hint,
      summary: summariseAll(buckets.get(band.key) ?? []),
    }));

  return { splits, unranked };
}
