/**
 * Weekly targets — the "who to target" list (CLAUDE.md §7).
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). Games in, ratings in, ranked matchups
 * out; nothing here knows what a conference is or how a rating was fitted.
 *
 * THE UNIT IS A MATCHUP, NOT A DEFENSE. A season leaderboard of soft defenses
 * is a different, less useful artefact — it lists units that are not playing,
 * and it does not say whose players to look at. Every row here is a defense
 * ON THIS WEEK'S SLATE paired with the offense facing it, because the action a
 * reader takes is "look at that offense's skill players", not "note that this
 * defense is bad".
 *
 * TWO ORDERINGS THAT LOOK ALIKE AND ARE NOT. `rank_vs_position` ranks a defense
 * against every rated defense in the country (1 = the BEST defense, so the
 * softest are the HIGHEST ranks). This module ranks the slate. A defense can top
 * the weekly list at national rank 130 of 136 — the six softer units simply are
 * not playing — so both numbers travel together and the panel shows the national
 * one, which is the one that means something.
 */

// Relative, with the extension: these carry VALUES, and Node's test runner does
// not understand the `@/*` alias. See the same note in `splits.ts`.
import { rankBasis, rankedValue, type RankBasis } from "./defense-view.ts";
import { POSITION_GROUPS, type PositionGroup } from "./types.ts";

/** The shape this module needs from a week's game. Structural, not imported. */
export type TargetGame = {
  gameId: number;
  homeTeamId: number;
  awayTeamId: number;
  neutralSite: boolean;
  startDate: string | null;
};

export type TargetRating = {
  defenseTeamId: number;
  positionGroup: PositionGroup;
  gamesIncluded: number;
  rankVsPosition: number | null;
  adjRushYardsAllowedPg: number | null;
  adjRecYardsAllowedPg: number | null;
};

export type TargetRow = {
  gameId: number;
  /** The soft defense. */
  defenseTeamId: number;
  /** The offense facing it — whose players a reader would look at. */
  offenseTeamId: number;
  defenseIsHome: boolean;
  neutralSite: boolean;
  startDate: string | null;
  /** Rank among ALL rated defenses nationally; 1 = the best defense. */
  rank: number;
  /** The opponent-adjusted per-game figure the rank was built from. */
  value: number | null;
  gamesRated: number;
};

export type PositionTargets = {
  position: PositionGroup;
  /** What the rank and the value measure — always shown, never assumed. */
  basis: RankBasis;
  rows: TargetRow[];
  /** Size of the national field, so a rank reads as "3 of 136". */
  rankedDefenses: number;
  /** Defenses on this slate after filtering, and how many carried no rating. */
  onSlate: number;
  unrated: number;
};

export const TARGETS_PER_POSITION = 5;

/**
 * Rank this week's matchups by how much each defense concedes to each position.
 *
 * `includeOffense` filters on the OFFENSE, matching the board: a reader who has
 * narrowed to one conference wants that conference's players, and the defense
 * being soft is a fact about the other team. Filtering on the defense instead
 * would return the exact complement — every matchup they cannot act on.
 *
 * A defense with no rating at this cutoff is COUNTED AND DROPPED, not ranked
 * last. Early in the season, and against a first-time FCS opponent, "we do not
 * know yet" is the truth, and sorting unknowns to the bottom of a list titled
 * "who to target" would read as "these are tough".
 */
export function buildWeeklyTargets(
  games: TargetGame[],
  ratings: TargetRating[],
  {
    includeOffense,
    limit = TARGETS_PER_POSITION,
  }: {
    includeOffense?: (offenseTeamId: number) => boolean;
    limit?: number;
  } = {},
): PositionTargets[] {
  const byPosition = new Map<PositionGroup, Map<number, TargetRating>>();
  const fieldSize = new Map<PositionGroup, number>();

  for (const rating of ratings) {
    const forPosition =
      byPosition.get(rating.positionGroup) ?? new Map<number, TargetRating>();
    forPosition.set(rating.defenseTeamId, rating);
    byPosition.set(rating.positionGroup, forPosition);

    // The national field is every RATED defense, whether or not it plays this
    // week — that is the population `rank_vs_position` was assigned within.
    if (rating.rankVsPosition !== null) {
      fieldSize.set(
        rating.positionGroup,
        Math.max(fieldSize.get(rating.positionGroup) ?? 0, rating.rankVsPosition),
      );
    }
  }

  // Each game contributes two matchups: both teams defend.
  const matchups: {
    gameId: number;
    defenseTeamId: number;
    offenseTeamId: number;
    defenseIsHome: boolean;
    neutralSite: boolean;
    startDate: string | null;
  }[] = [];

  for (const game of games) {
    for (const defenseIsHome of [true, false]) {
      const defenseTeamId = defenseIsHome ? game.homeTeamId : game.awayTeamId;
      const offenseTeamId = defenseIsHome ? game.awayTeamId : game.homeTeamId;
      if (includeOffense && !includeOffense(offenseTeamId)) continue;
      matchups.push({
        gameId: game.gameId,
        defenseTeamId,
        offenseTeamId,
        defenseIsHome,
        neutralSite: game.neutralSite,
        startDate: game.startDate,
      });
    }
  }

  return POSITION_GROUPS.map((position) => {
    const forPosition = byPosition.get(position) ?? new Map();
    const rows: TargetRow[] = [];
    let unrated = 0;

    for (const matchup of matchups) {
      const rating = forPosition.get(matchup.defenseTeamId);
      if (!rating || rating.rankVsPosition === null) {
        unrated += 1;
        continue;
      }
      rows.push({
        ...matchup,
        rank: rating.rankVsPosition,
        value: rankedValue(rating, position),
        gamesRated: rating.gamesIncluded,
      });
    }

    // DESCENDING rank: 1 is the BEST defense, so the softest matchup — the one
    // this panel exists to surface — is the HIGHEST rank.
    rows.sort((a, b) => b.rank - a.rank);

    return {
      position,
      basis: rankBasis(position),
      rows: rows.slice(0, limit),
      rankedDefenses: fieldSize.get(position) ?? 0,
      onSlate: matchups.length,
      unrated,
    };
  });
}
