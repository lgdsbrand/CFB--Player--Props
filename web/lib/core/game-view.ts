/**
 * Analyze Games — turning one game's rows into what the page renders.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). A game in, board rows and ratings in,
 * ordered groups out. Nothing here knows what a conference is, what CFBD is, or
 * how a rating was fitted.
 *
 * WHAT THIS VIEW IS NOT. It does not predict the game. CLAUDE.md §10 puts
 * full game-outcome and spread prediction out of scope, and that was reaffirmed
 * with the client when this view was agreed. The spread and total shown here are
 * the BOOK'S numbers, ingested from CFBD; the only model output on the page is
 * the per-player probabilities the board already produces. If a "model spread"
 * ever appears in this file, something has gone wrong.
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

/** The minimum a prop row needs to be grouped and ordered here. */
export type ViewProp = {
  projectionId: number;
  playerId: number;
  playerName: string;
  positionGroup: PositionGroup | null;
  teamId: number;
  marketKey: string;
  displayConfidence: number | null;
  edge: number | null;
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

// -----------------------------------------------------------------------------
// The props
// -----------------------------------------------------------------------------

export type PlayerProps<T extends ViewProp> = {
  playerId: number;
  playerName: string;
  positionGroup: PositionGroup | null;
  /** Every market for this player: priced first, then the unpriced ones. */
  props: T[];
  /** The player's strongest call, for ordering within a position. */
  topConfidence: number | null;
  /** How many of `props` have no line yet — most of them, early in a week. */
  unpricedCount: number;
};

export type TeamProps<T extends ViewProp> = {
  teamId: number;
  abbreviation: string | null;
  school: string;
  isHome: boolean;
  players: PlayerProps<T>[];
  /** Rows across every player — what the table renders. */
  propCount: number;
};

/** Depth-chart order, so a table reads like a roster rather than a ranking. */
const POSITION_ORDER = new Map<string, number>(
  MATCHUP_POSITIONS.map((position, index) => [position, index]),
);

/**
 * Group one game's rows by team, then by player.
 *
 * AWAY TEAM FIRST, matching the "AWAY @ HOME" label used everywhere else in the
 * app. The alternative — home first, because home is the anchor of the spread —
 * would put the two orderings on the same screen in disagreement.
 *
 * WITHIN A TEAM: position in depth-chart order, then the player's strongest
 * call. A player's markets stay together, which is the point of grouping at all:
 * a quarterback's five markets read as one opinion about him, not as five
 * unrelated rows scattered through a confidence ranking. A player with no known
 * position sorts last rather than being dropped — a prop that exists should be
 * visible even when the roster join found nothing.
 *
 * WITHIN A PLAYER: PRICED MARKETS FIRST. Through most of a live week the
 * majority of a game's rows have no line — college books post props on Thursday
 * or Friday (CLAUDE.md §7) — so market order alone puts four unpriced rows above
 * the one call the reader came for. Ordering by whether there is a call, then by
 * how strong it is, means the top of every player's block is the part with
 * something in it, and the leans stay below rather than being hidden.
 *
 * `marketOrder` breaks the remaining ties with the catalogue's own display
 * order. Without it the fallback is the market key, which is deterministic but
 * arbitrary — REC before REC YDS before RUSH YDS is alphabetical, not sensible.
 *
 * A team with no rows still gets a group. Both teams are in the game, and an
 * empty group saying so is the honest rendering of "we project nobody here" —
 * a missing group reads as a bug.
 */
export function groupPropsByTeam<T extends ViewProp>(
  game: ViewGame,
  rows: T[],
  { marketOrder }: { marketOrder?: Map<string, number> } = {},
): TeamProps<T>[] {
  const byPlayer = new Map<number, PlayerProps<T>>();
  const teamOfPlayer = new Map<number, number>();

  for (const row of rows) {
    teamOfPlayer.set(row.playerId, row.teamId);
    const existing = byPlayer.get(row.playerId);
    if (existing) {
      existing.props.push(row);
      existing.topConfidence = maxOrNull(
        existing.topConfidence,
        row.displayConfidence,
      );
      continue;
    }
    byPlayer.set(row.playerId, {
      playerId: row.playerId,
      playerName: row.playerName,
      positionGroup: row.positionGroup,
      props: [row],
      topConfidence: row.displayConfidence,
      unpricedCount: 0,
    });
  }

  const marketRank = (key: string) =>
    marketOrder?.get(key) ?? Number.MAX_SAFE_INTEGER;

  for (const player of byPlayer.values()) {
    player.props.sort((a, b) => compareProps(a, b, marketRank));
    player.unpricedCount = player.props.filter(
      (prop) => prop.displayConfidence === null,
    ).length;
  }

  const build = (teamId: number, isHome: boolean): TeamProps<T> => {
    const players = [...byPlayer.values()]
      .filter((player) => teamOfPlayer.get(player.playerId) === teamId)
      .sort(comparePlayers);
    return {
      teamId,
      abbreviation: isHome ? game.homeAbbreviation : game.awayAbbreviation,
      school: isHome ? game.homeSchool : game.awaySchool,
      isHome,
      players,
      propCount: players.reduce((total, player) => total + player.props.length, 0),
    };
  };

  return [build(game.awayTeamId, false), build(game.homeTeamId, true)];
}

function compareProps<T extends ViewProp>(
  a: T,
  b: T,
  marketRank: (key: string) => number,
): number {
  const aPriced = a.displayConfidence !== null;
  const bPriced = b.displayConfidence !== null;
  if (aPriced !== bPriced) return aPriced ? -1 : 1;

  if (aPriced && bPriced && a.displayConfidence !== b.displayConfidence) {
    return (b.displayConfidence ?? 0) - (a.displayConfidence ?? 0);
  }

  const rankDelta = marketRank(a.marketKey) - marketRank(b.marketKey);
  if (rankDelta !== 0) return rankDelta;
  return a.marketKey.localeCompare(b.marketKey);
}

function comparePlayers<T extends ViewProp>(
  a: PlayerProps<T>,
  b: PlayerProps<T>,
): number {
  const positionDelta = positionRank(a.positionGroup) - positionRank(b.positionGroup);
  if (positionDelta !== 0) return positionDelta;

  // A player with no call at all sorts after one that has one, rather than
  // being treated as 0% — the board makes the same distinction, because "no
  // line yet" and "we think it is unlikely" are different claims (CLAUDE.md §7).
  const aConfidence = a.topConfidence;
  const bConfidence = b.topConfidence;
  if (aConfidence !== bConfidence) {
    if (aConfidence === null) return 1;
    if (bConfidence === null) return -1;
    return bConfidence - aConfidence;
  }

  return a.playerName.localeCompare(b.playerName);
}

function positionRank(position: PositionGroup | null): number {
  if (position === null) return POSITION_ORDER.size;
  return POSITION_ORDER.get(position) ?? POSITION_ORDER.size;
}

function maxOrNull(a: number | null, b: number | null): number | null {
  if (a === null) return b;
  if (b === null) return a;
  return Math.max(a, b);
}
