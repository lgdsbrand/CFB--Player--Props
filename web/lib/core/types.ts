/**
 * Row shapes for the read layer.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). These describe the schema, which the NFL
 * build shares — so nothing here names a conference, a school or a college
 * concept. The one place the sport shows through is `PositionGroup`, and that
 * is a deliberate seam: the NFL build widens it and everything else compiles.
 *
 * WHY THESE ARE HAND-WRITTEN. Supabase can generate types from a live database,
 * but the CLI is not installed here and the project is not linked (the Phase 3
 * migrations were applied through the worker's connection for the same reason).
 * Generated types would also describe every table, including the ones RLS
 * closes off, which invites writing a query that type-checks and then fails at
 * runtime with a permissions error.
 *
 * These cover exactly the views the app is allowed to read, which is a smaller
 * and more honest surface. They are verified against the real database by
 * `npm run check:schema` rather than trusted — a hand-written type that has
 * silently drifted from its view is worse than no type at all.
 */

export type BetSide = "over" | "under";

export type PositionGroup = "QB" | "RB" | "WR" | "TE";

export const POSITION_GROUPS: readonly PositionGroup[] = [
  "QB",
  "RB",
  "WR",
  "TE",
] as const;

/** How past games are graded for the hit-rate figures (CLAUDE.md §9.2). */
export type HitRateBasis = "threshold" | "closing_line";

/**
 * One row of the main board: one PROJECTION, with a pick left-joined on.
 *
 * Driven by projections rather than picks, which is what implements the
 * late-line behaviour — see the comment on `v_board_rows`. The consequence for
 * this type is that every pick-derived field is nullable, and `hasCall`
 * discriminates. Treating a null `edge` as zero would report "no disagreement
 * with the market" for a row that has no market at all.
 */
/**
 * One rung of the alternate-line ladder: a line, and the model's probability of
 * clearing it.
 *
 * NO BOOK PRICE, deliberately. Books post one line, so a rung has no market to
 * be compared against, and an edge quoted against a line nobody offered would be
 * inventing a market. The edge belongs to the pick, on the posted line.
 */
export type LadderRung = {
  line: number;
  probOver: number;
};

export type BoardRow = {
  projectionId: number;
  pickId: number | null;

  season: number;
  week: number;

  marketKey: string;
  marketName: string;
  marketLabel: string | null;
  marketEmoji: string | null;
  isBinary: boolean;

  playerId: number;
  playerName: string;
  positionGroup: PositionGroup | null;

  teamId: number;
  teamSchool: string;
  teamAbbreviation: string | null;
  teamColor: string | null;
  teamAltColor: string | null;

  opponentTeamId: number;
  opponentSchool: string;
  opponentAbbreviation: string | null;

  gameId: number;
  startDate: string | null;
  neutralSite: boolean;
  isHome: boolean;

  /**
   * Where the game is played. Display only — nothing in the model reads these.
   *
   * All three are nullable because `games.venue_id` is: CFBD does not always
   * carry a venue, especially for games posted far ahead. Format them with
   * `formatVenue`, which handles the disambiguating parentheticals 60 of the
   * 844 venues carry.
   */
  venueName: string | null;
  venueCity: string | null;
  venueState: string | null;

  /**
   * The game line, as CONTEXT for the prop. Display only — no model input
   * reads these (see migration 0030 for why that boundary matters).
   *
   * `teamSpread` is already flipped to THIS player's team by the view:
   * negative means his team is favoured, whichever side of the game he is on.
   */
  teamSpread: number | null;
  gameTotal: number | null;
  gameLineProviders: number | null;

  /**
   * AP Top 25 rank entering this week, or null when unranked — which is most
   * rows, since only 25 teams carry one. Point-in-time: the poll published
   * before this week was played, never the season's final one.
   */
  teamPollRank: number | null;
  opponentPollRank: number | null;

  /** Null until there is a line to call against. */
  line: number | null;
  side: BetSide | null;
  /**
   * Mass of the distribution on the CALLED side.
   *
   * Not what the card shows for every market, and not what to sort or filter
   * on — see `displayConfidence`.
   */
  confidence: number | null;
  modelProbOver: number | null;
  /** De-vigged. Null means "no book probability", never zero edge. */
  bookProbOver: number | null;
  edge: number | null;

  hasBookLine: boolean;
  hasCall: boolean;

  overPrice: number | null;
  underPrice: number | null;
  sportsbookKey: string | null;
  sportsbookName: string | null;

  /** SECONDARY detail only, never the headline claim (CLAUDE.md §1). */
  projectedMedian: number | null;
  projectedP10: number | null;
  projectedP90: number | null;
  /**
   * Share of the projection carried by priors rather than this season.
   *
   * PLAYER-LEVEL: identical on every market a player has this week, because the
   * feature frame computes it once per player-week. Read it beside
   * `effectiveSample` and never alone — see `lib/core/evidence.ts` for the
   * inversion that makes the share unsafe on its own.
   */
  priorWeight: number | null;
  /** Games of evidence behind the projection. Player-level, like the above. */
  effectiveSample: number | null;

  /** Rank 1 = the BEST defense vs this position. A HIGH rank is the soft one. */
  opponentRankVsPosition: number | null;

  conferenceName: string | null;
  conferenceIsDisplayed: boolean | null;

  /**
   * Alternate-line rungs, ascending by line. SECONDARY DETAIL, like the range.
   *
   * Only populated by the PLAYER-DETAIL query — the board page does not select
   * the column, because it is 5 to 7 objects per row and the board renders none
   * of them. So `null` here means "not asked for" as often as it means "this
   * market has no ladder", and the panel treats both the same way: it does not
   * render. Read it through `lib/core/ladder-view.ts`.
   */
  ladder: LadderRung[] | null;

  /**
   * THE HEADLINE NUMBER, as the card states it. `modelProbOver` for a binary
   * market — the anytime-scorer probability (CLAUDE.md §1) — and `confidence`
   * for every other. Computed in `v_board_rows` so the ordering the database
   * applies and the number the reader sees are the same quantity.
   *
   * Anytime TD is called UNDER on ~97% of picks because most players do not
   * score, so `confidence` on those rows is the probability of NOT scoring and
   * runs highest where the pick is least interesting.
   */
  displayConfidence: number | null;
};

/** One completed game for a player, backing the game log and hit rates. */
export type PlayerGameLogRow = {
  playerId: number;
  gameId: number;
  season: number;
  week: number;
  positionGroup: PositionGroup | null;
  isHome: boolean;
  /** The defense faced. Needed to look up its rank as it stood THAT week. */
  opponentTeamId: number;
  opponentAbbreviation: string | null;
  opponentSchool: string;
  startDate: string | null;
  neutralSite: boolean;

  passAttempts: number | null;
  passCompletions: number | null;
  passYards: number | null;
  passTds: number | null;
  interceptions: number | null;
  rushAttempts: number | null;
  rushYards: number | null;
  rushTds: number | null;
  targets: number | null;
  receptions: number | null;
  recYards: number | null;
  recTds: number | null;
  offensiveTds: number | null;
};

/** A market in the catalogue, with the positions it applies to. */
export type Market = {
  key: string;
  displayName: string;
  shortLabel: string | null;
  emoji: string | null;
  /** The `player_game_stats` column this market grades against. */
  statColumn: string;
  isBinary: boolean;
  defaultLine: number | null;
  unit: string | null;
  sortOrder: number;
  /** Positions this market is offered for, in display order. */
  positions: PositionGroup[];
};

export type Conference = {
  id: number;
  name: string;
  abbreviation: string | null;
  /** Display filter ONLY — ingest always covers all FBS (CLAUDE.md §4). */
  isDisplayed: boolean;
};

/** One book's current quote, for the per-book odds row on a card. */
export type BookQuote = {
  lineId: number;
  gameId: number;
  playerId: number;
  marketKey: string;
  sportsbookKey: string;
  sportsbookName: string;
  line: number;
  overPrice: number | null;
  underPrice: number | null;
  bookProbOver: number | null;
  capturedAt: string;
};

/**
 * What one defense conceded to one position in ONE game.
 *
 * RAW AND OPPONENT-UNADJUSTED. This is the observation; `DefenseRating` is the
 * inference drawn from a set of them. Anywhere the two appear together, each
 * has to say which it is — the adjusted figure is the one the model uses and
 * the one nobody can look up (CLAUDE.md §5).
 */
export type DefenseGameRow = {
  splitId: number;
  gameId: number;
  defenseTeamId: number;
  offenseTeamId: number;
  offenseSchool: string;
  offenseAbbreviation: string | null;
  season: number;
  week: number;
  positionGroup: PositionGroup;
  startDate: string | null;
  neutralSite: boolean;
  defenseIsHome: boolean;

  plays: number | null;
  rushAttempts: number | null;
  rushYardsAllowed: number | null;
  rushTdsAllowed: number | null;
  targets: number | null;
  receptionsAllowed: number | null;
  recYardsAllowed: number | null;
  recTdsAllowed: number | null;
};

/** What one defense has conceded to one position, cumulative to a cutoff. */
export type DefenseSplitRow = {
  defenseTeamId: number;
  positionGroup: PositionGroup;
  gamesIncluded: number;
  rushAttempts: number;
  rushYardsAllowed: number;
  rushTdsAllowed: number;
  targets: number;
  receptionsAllowed: number;
  recYardsAllowed: number;
  recTdsAllowed: number;
  rushYardsAllowedPg: number | null;
  recYardsAllowedPg: number | null;
};

/**
 * One game on the slate — the unit of the Analyze Games view.
 *
 * Also what the board's game selector reads, so the two cannot disagree about
 * which games a week contains. Everything here is a schedule fact, a number a
 * book published, or a count of model output that already exists; nothing
 * predicts an outcome (CLAUDE.md §10).
 */
export type GameSummary = {
  gameId: number;
  season: number;
  week: number;
  startDate: string | null;
  startTimeTbd: boolean;
  neutralSite: boolean;
  completed: boolean;
  homePoints: number | null;
  awayPoints: number | null;

  homeTeamId: number;
  homeSchool: string;
  homeAbbreviation: string | null;
  homeColor: string | null;
  homeAltColor: string | null;

  awayTeamId: number;
  awaySchool: string;
  awayAbbreviation: string | null;
  awayColor: string | null;
  awayAltColor: string | null;

  venueName: string | null;
  venueCity: string | null;
  venueState: string | null;

  /** HOME perspective: negative means the home team is favoured. */
  homeSpread: number | null;
  gameTotal: number | null;
  gameLineProviders: number | null;

  homePollRank: number | null;
  awayPollRank: number | null;

  /** Projections on this game across BOTH teams and all conferences. */
  projections: number;
  players: number;
  /** Projections carrying a pick — not the number of picks. */
  calls: number;
};

/** A week that has model output, for the week selector. */
export type SlateWeek = {
  season: number;
  week: number;
  games: number;
  projections: number;
  players: number;
  firstKickoff: string | null;
  lastKickoff: string | null;
};

/**
 * Conditions for one game, from `v_game_conditions`.
 *
 * Every field but the venue may be null, and that is the normal state rather
 * than a fault: Open-Meteo forecasts about 15 days out, so a game further away
 * than that has no reading yet and gains one as kickoff approaches.
 */
export type GameConditions = {
  gameId: number;
  venueName: string | null;
  venueIsDome: boolean;
  /** Separates "no forecast yet" from "none was ever recorded". */
  completed: boolean;

  /** `"cfbd"` is an observation of a played game; `"open_meteo"` a forecast. */
  source: string | null;
  /** Do not infer this from `source` — see migration 0042. */
  isForecast: boolean | null;
  /** The hour the reading DESCRIBES, not when it was fetched. */
  observedAt: string | null;

  temperatureF: number | null;
  dewPointF: number | null;
  humidity: number | null;
  precipitationIn: number | null;
  snowfallIn: number | null;
  windSpeedMph: number | null;
  windDirectionDeg: number | null;
  pressureMb: number | null;
  condition: string | null;
};

/**
 * Runtime configuration from `app_config`.
 *
 * Read rather than hardcoded because CLAUDE.md §9 leaves two of these open with
 * the client, and both must be changeable without a deploy.
 */
export type AppConfig = {
  edgeThreshold: number;
  edgesOnlyDefault: boolean;
  hitRateBasis: HitRateBasis;
  hitRateWindows: number[];
  minGamesForDefenseRank: number;
};
