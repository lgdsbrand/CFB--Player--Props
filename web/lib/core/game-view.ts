/**
 * Analyze Games — turning one game's rows into what the page renders.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). A game and its ratings in, the
 * favourite and the position matchups out. Nothing here knows what a
 * conference is, what CFBD is, or how a rating was fitted.
 *
 * The per-team props grouping that lived here went with the page's props
 * tables (2026-10-06); a game's props are on the board now.
 *
 * WHAT THIS VIEW IS NOT. It does not predict the game. The spread and total
 * here are the BOOK'S numbers, ingested from CFBD. The game model (CLAUDE.md
 * §11) has its own module and its own labelled panel, and is never mixed into
 * this one.
 */

// Relative, with the extension: these carry VALUES, and Node's test runner does
// not understand the `@/*` alias. See the same note in `splits.ts`.
import { rankBasis, rankedValue, type RankBasis } from "./defense-view.ts";
import { POSITION_GROUPS, type PositionGroup } from "./types.ts";

/** The shape this module needs from a game. Structural, not imported. */
export type ViewGame = {
  gameId: number;
  homeTeamId: number;
  awayTeamId: number;
  homeAbbreviation: string | null;
  awayAbbreviation: string | null;
  homeSchool: string;
  awaySchool: string;
  neutralSite: boolean;
  homeSpread: number | null;
};

export type ViewRating = {
  defenseTeamId: number;
  positionGroup: PositionGroup;
  gamesIncluded: number;
  rankVsPosition: number | null;
  adjRushYardsAllowedPg: number | null;
  adjRecYardsAllowedPg: number | null;
};

// -----------------------------------------------------------------------------
// The line
// -----------------------------------------------------------------------------

export type Favourite = {
  teamId: number;
  abbreviation: string | null;
  school: string;
  /** Points laid, always POSITIVE. A pick'em reports 0. */
  points: number;
  isHome: boolean;
};

/**
 * Which team the book favours, and by how much.
 *
 * `homeSpread` arrives on the home team's perspective — CFBD's convention,
 * verified 228 of 228 when game lines were ingested — so a negative number means
 * the home team is laying points. This resolves it to a team and a positive
 * magnitude, because "TCU -3.5" is how a reader states it and "home spread
 * -3.5" is not.
 *
 * Returns null when no book has priced the game, which is most of an opening
 * slate. A pick'em is NOT null: 0 is a real market judgement and reads
 * differently from "not priced yet".
 */
export function favourite(game: ViewGame): Favourite | null {
  const spread = game.homeSpread;
  if (spread === null || !Number.isFinite(spread)) return null;

  const homeFavoured = spread <= 0;
  return {
    teamId: homeFavoured ? game.homeTeamId : game.awayTeamId,
    abbreviation: homeFavoured ? game.homeAbbreviation : game.awayAbbreviation,
    school: homeFavoured ? game.homeSchool : game.awaySchool,
    points: Math.abs(spread),
    isHome: homeFavoured,
  };
}

// -----------------------------------------------------------------------------
// The matchup grid
// -----------------------------------------------------------------------------

export type SideMatchup = {
  /** The defense being described. */
  defenseTeamId: number;
  defenseAbbreviation: string | null;
  /** The offense that faces it — whose players a reader would open. */
  offenseTeamId: number;
  offenseAbbreviation: string | null;
  /** National rank among rated defenses; 1 is the BEST defense. */
  rank: number | null;
  /** The opponent-adjusted per-game figure the rank was built from. */
  value: number | null;
  gamesRated: number;
};

export type PositionMatchup = {
  position: PositionGroup;
  /** What the rank and value measure — always shown, never assumed. */
  basis: RankBasis;
  /** The HOME team's defense: what the AWAY offense faces. */
  homeDefense: SideMatchup;
  /** The AWAY team's defense: what the HOME offense faces. */
  awayDefense: SideMatchup;
  /**
   * Size of the national field for THIS position, so a rank reads as "3 of 136".
   *
   * Per position rather than one number for the grid: each position is ranked
   * separately, and the fields genuinely differ in size — a defense can be
   * rated against the run and not yet against tight ends. A shared denominator
   * would be wrong for at least one row and nobody would be able to tell which.
   */
  rankedDefenses: number;
};

/**
 * Positions the grid shows, in depth-chart order.
 *
 * The app's `PositionGroup` is already exactly the four the board covers — the
 * database enum is wider (OL, DL, K...) but nothing outside the ingest layer
 * ever sees those. So this is `POSITION_GROUPS` under a name that says why the
 * grid has four rows, rather than a filter that currently removes nothing.
 */
export const MATCHUP_POSITIONS: readonly PositionGroup[] = POSITION_GROUPS;

/**
 * What each defense in this game concedes to each position.
 *
 * BOTH SIDES ALWAYS, unlike the weekly-targets panel. That panel answers "whose
 * players should I look at this week" and so lists only the soft half of a
 * matchup; this page answers "what is going on in this game", where the tough
 * side is half the answer. A grid with one column filled would also read as
 * missing data rather than as a deliberate omission.
 *
 * An unrated defense keeps its row with a null rank. Early in a season nothing
 * is rated, and dropping the row would leave a grid whose gaps a reader has to
 * interpret; a stated "not rated yet" is the honest version, and it is the same
 * choice the targets panel makes when it counts unrated defenses separately
 * rather than sorting them last.
 */
export function gameMatchups(
  game: ViewGame,
  ratings: ViewRating[],
): PositionMatchup[] {
  const byKey = new Map<string, ViewRating>();
  const fieldSize = new Map<PositionGroup, number>();
  for (const rating of ratings) {
    byKey.set(`${rating.defenseTeamId}-${rating.positionGroup}`, rating);
    if (rating.rankVsPosition !== null) {
      fieldSize.set(
        rating.positionGroup,
        (fieldSize.get(rating.positionGroup) ?? 0) + 1,
      );
    }
  }

  const side = (
    defenseTeamId: number,
    offenseTeamId: number,
    defenseAbbreviation: string | null,
    offenseAbbreviation: string | null,
    position: PositionGroup,
  ): SideMatchup => {
    const rating = byKey.get(`${defenseTeamId}-${position}`);
    return {
      defenseTeamId,
      defenseAbbreviation,
      offenseTeamId,
      offenseAbbreviation,
      rank: rating?.rankVsPosition ?? null,
      value: rating ? rankedValue(rating, position) : null,
      gamesRated: rating?.gamesIncluded ?? 0,
    };
  };

  return MATCHUP_POSITIONS.map((position) => ({
    position,
    basis: rankBasis(position),
    homeDefense: side(
      game.homeTeamId,
      game.awayTeamId,
      game.homeAbbreviation,
      game.awayAbbreviation,
      position,
    ),
    awayDefense: side(
      game.awayTeamId,
      game.homeTeamId,
      game.awayAbbreviation,
      game.homeAbbreviation,
      position,
    ),
    rankedDefenses: fieldSize.get(position) ?? 0,
  }));
}

/**
 * The softest matchup in this game, if any defense is rated.
 *
 * "Softest" is the HIGHEST national rank, because rank 1 is the best defense —
 * the same inversion the board's opponent-rank sort had to be renamed over.
 * Used for the one-line summary on a game card, where there is room for exactly
 * one fact and it should be the actionable one.
 */
export function softestMatchup(matchups: PositionMatchup[]): {
  position: PositionGroup;
  side: SideMatchup;
  /** The field the rank is out of, so "127" cannot be read without its scale. */
  rankedDefenses: number;
} | null {
  let best: {
    position: PositionGroup;
    side: SideMatchup;
    rankedDefenses: number;
  } | null = null;
  for (const matchup of matchups) {
    for (const side of [matchup.homeDefense, matchup.awayDefense]) {
      if (side.rank === null) continue;
      if (best === null || side.rank > (best.side.rank ?? 0)) {
        best = {
          position: matchup.position,
          side,
          rankedDefenses: matchup.rankedDefenses,
        };
      }
    }
  }
  return best;
}
