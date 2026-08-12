/**
 * Tests for the Analyze Games view.
 *
 * The failure mode here is a page that reads correctly and says the wrong
 * thing. A matchup grid with plausible numbers is indistinguishable by eye from
 * one that put each defense against its own offense; a spread rendered as
 * "TCU -3.5" looks equally right whichever team is actually favoured. So the
 * properties are asserted rather than eyeballed — particularly the two
 * inversions this codebase has already been bitten by: a NEGATIVE spread means
 * the home team is favoured, and a HIGH rank means a SOFT defense.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  favourite,
  gameMatchups,
  groupPropsByTeam,
  MATCHUP_POSITIONS,
  softestMatchup,
  type ViewGame,
  type ViewProp,
  type ViewRating,
} from "./game-view.ts";

const HOME = 10;
const AWAY = 20;

function game(overrides: Partial<ViewGame> = {}): ViewGame {
  return {
    gameId: 1,
    homeTeamId: HOME,
    awayTeamId: AWAY,
    homeAbbreviation: "TCU",
    awayAbbreviation: "UNC",
    homeSchool: "TCU",
    awaySchool: "North Carolina",
    neutralSite: false,
    homeSpread: null,
    ...overrides,
  };
}

function rating(
  defenseTeamId: number,
  position: "QB" | "RB" | "WR" | "TE",
  rank: number | null,
  { rush = 100, rec = 150, games = 8 } = {},
): ViewRating {
  return {
    defenseTeamId,
    positionGroup: position,
    gamesIncluded: games,
    rankVsPosition: rank,
    adjRushYardsAllowedPg: rush,
    adjRecYardsAllowedPg: rec,
  };
}

function prop(overrides: Partial<ViewProp> = {}): ViewProp {
  return {
    projectionId: 1,
    playerId: 1,
    playerName: "A Player",
    positionGroup: "QB",
    teamId: HOME,
    marketKey: "rush_yards",
    displayConfidence: 0.6,
    edge: 0.05,
    ...overrides,
  };
}

// -----------------------------------------------------------------------------
// The line
// -----------------------------------------------------------------------------

test("a negative spread means the HOME team is favoured", () => {
  const result = favourite(game({ homeSpread: -3.5 }));
  assert.equal(result?.teamId, HOME);
  assert.equal(result?.abbreviation, "TCU");
  assert.equal(result?.points, 3.5, "points laid are reported positive");
  assert.equal(result?.isHome, true);
});

test("a positive spread means the AWAY team is favoured", () => {
  const result = favourite(game({ homeSpread: 17.5 }));
  assert.equal(result?.teamId, AWAY);
  assert.equal(result?.abbreviation, "UNC");
  assert.equal(result?.points, 17.5);
  assert.equal(result?.isHome, false);
});

test("an unpriced game has no favourite, which is not a pick'em", () => {
  assert.equal(favourite(game({ homeSpread: null })), null);

  const pickEm = favourite(game({ homeSpread: 0 }));
  assert.notEqual(pickEm, null, "0 is a real market judgement, not missing data");
  assert.equal(pickEm?.points, 0);
});

test("a non-finite spread is treated as unpriced rather than rendered", () => {
  assert.equal(favourite(game({ homeSpread: Number.NaN })), null);
});

// -----------------------------------------------------------------------------
// The matchup grid
// -----------------------------------------------------------------------------

test("each defense is paired with the offense that FACES it", () => {
  const matchups = gameMatchups(game(), [
    rating(HOME, "RB", 130),
    rating(AWAY, "RB", 4),
  ]);
  const rb = matchups.find((m) => m.position === "RB")!;

  assert.equal(rb.homeDefense.defenseTeamId, HOME);
  assert.equal(
    rb.homeDefense.offenseTeamId,
    AWAY,
    "the home defense is what the away offense has to run against",
  );
  assert.equal(rb.awayDefense.defenseTeamId, AWAY);
  assert.equal(rb.awayDefense.offenseTeamId, HOME);
});

test("every board position gets a row even when nothing is rated", () => {
  const matchups = gameMatchups(game(), []);
  assert.deepEqual(
    matchups.map((m) => m.position),
    [...MATCHUP_POSITIONS],
  );
  for (const matchup of matchups) {
    assert.equal(matchup.homeDefense.rank, null);
    assert.equal(matchup.awayDefense.rank, null);
    assert.equal(matchup.homeDefense.gamesRated, 0);
  }
});

test("the value shown is the column the rank was built from", () => {
  const matchups = gameMatchups(game(), [
    rating(HOME, "RB", 130, { rush: 188, rec: 42 }),
    rating(HOME, "WR", 12, { rush: 188, rec: 42 }),
  ]);

  // RB ranks on rushing, WR on receiving. Reading the wrong column would give a
  // number that looks plausible and contradicts the rank beside it.
  assert.equal(matchups.find((m) => m.position === "RB")!.homeDefense.value, 188);
  assert.equal(matchups.find((m) => m.position === "WR")!.homeDefense.value, 42);
});

test("the QB basis carries its rushing-only caveat", () => {
  const qb = gameMatchups(game(), [])[0];
  assert.equal(qb.position, "QB");
  assert.match(qb.basis.caveat ?? "", /rushing only/);
});

test("the softest matchup is the HIGHEST rank, not the lowest", () => {
  const matchups = gameMatchups(game(), [
    rating(HOME, "RB", 3),
    rating(AWAY, "WR", 131),
    rating(HOME, "TE", 88),
  ]);
  const softest = softestMatchup(matchups)!;

  assert.equal(softest.position, "WR");
  assert.equal(softest.side.rank, 131);
  assert.equal(
    softest.side.offenseTeamId,
    HOME,
    "the away defense is soft, so it is the HOME offense worth looking at",
  );
  assert.equal(
    softest.rankedDefenses,
    1,
    "the field travels with the rank — 131 out of what is the whole question",
  );
});

test("the field size is counted per position, not shared across the grid", () => {
  const matchups = gameMatchups(game(), [
    rating(HOME, "RB", 3),
    rating(AWAY, "RB", 9),
    rating(HOME, "TE", 40),
    // Unranked ratings exist early in a season and must not inflate the field.
    rating(AWAY, "TE", null),
  ]);

  assert.equal(matchups.find((m) => m.position === "RB")!.rankedDefenses, 2);
  assert.equal(matchups.find((m) => m.position === "TE")!.rankedDefenses, 1);
  assert.equal(matchups.find((m) => m.position === "QB")!.rankedDefenses, 0);
});

test("a game with nothing rated has no softest matchup", () => {
  assert.equal(softestMatchup(gameMatchups(game(), [])), null);
});

// -----------------------------------------------------------------------------
// The props
// -----------------------------------------------------------------------------

test("both teams get a group, in away-then-home order, even when empty", () => {
  const groups = groupPropsByTeam(game(), []);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].teamId, AWAY);
  assert.equal(groups[0].isHome, false);
  assert.equal(groups[1].teamId, HOME);
  assert.equal(groups[1].propCount, 0);
});

test("a player's markets stay together instead of scattering by confidence", () => {
  const groups = groupPropsByTeam(game(), [
    prop({ projectionId: 1, playerId: 1, playerName: "QB One", displayConfidence: 0.7 }),
    prop({
      projectionId: 2,
      playerId: 2,
      playerName: "RB Two",
      positionGroup: "RB",
      displayConfidence: 0.9,
    }),
    prop({ projectionId: 3, playerId: 1, playerName: "QB One", displayConfidence: 0.52 }),
  ]);

  const home = groups[1];
  assert.equal(home.players.length, 2);
  assert.equal(home.players[0].playerId, 1, "QB sorts before RB by depth chart");
  assert.equal(home.players[0].props.length, 2);
  assert.equal(
    home.players[0].topConfidence,
    0.7,
    "the player's strongest call, not the last row seen",
  );
  assert.equal(home.propCount, 3);
});

test("within a position, the stronger call leads", () => {
  const groups = groupPropsByTeam(game(), [
    prop({ projectionId: 1, playerId: 1, playerName: "Weaker", positionGroup: "WR", displayConfidence: 0.55 }),
    prop({ projectionId: 2, playerId: 2, playerName: "Stronger", positionGroup: "WR", displayConfidence: 0.81 }),
  ]);
  assert.deepEqual(
    groups[1].players.map((p) => p.playerName),
    ["Stronger", "Weaker"],
  );
});

test("a player with no call sorts after one that has a call, not as 0%", () => {
  const groups = groupPropsByTeam(game(), [
    prop({ projectionId: 1, playerId: 1, playerName: "No line", positionGroup: "WR", displayConfidence: null }),
    prop({ projectionId: 2, playerId: 2, playerName: "Weak call", positionGroup: "WR", displayConfidence: 0.51 }),
  ]);
  assert.deepEqual(
    groups[1].players.map((p) => p.playerName),
    ["Weak call", "No line"],
  );
});

test("players are split by team, not merged", () => {
  const groups = groupPropsByTeam(game(), [
    prop({ projectionId: 1, playerId: 1, teamId: HOME, playerName: "Home guy" }),
    prop({ projectionId: 2, playerId: 2, teamId: AWAY, playerName: "Away guy" }),
  ]);
  assert.deepEqual(groups[0].players.map((p) => p.playerName), ["Away guy"]);
  assert.deepEqual(groups[1].players.map((p) => p.playerName), ["Home guy"]);
});

test("a player with no known position is kept and sorted last", () => {
  // `positionGroup` is nullable on the board row: it comes from a left join to
  // player_team_seasons, and a player with no roster row for that season has
  // none. A prop that exists must stay visible rather than being dropped for
  // want of a label.
  const groups = groupPropsByTeam(game(), [
    prop({ projectionId: 1, playerId: 1, playerName: "Unrostered", positionGroup: null, displayConfidence: 0.99 }),
    prop({ projectionId: 2, playerId: 2, playerName: "Back", positionGroup: "RB", displayConfidence: 0.5 }),
  ]);
  assert.deepEqual(
    groups[1].players.map((p) => p.playerName),
    ["Back", "Unrostered"],
  );
});

test("a player's PRICED markets lead, and the leans follow", () => {
  // The common case through most of a live week: college books post props on
  // Thursday or Friday, so four of a quarterback's five markets have no line.
  // Market order alone would bury the one call the reader came for.
  const groups = groupPropsByTeam(game(), [
    prop({ projectionId: 1, playerId: 1, marketKey: "pass_attempts", displayConfidence: null }),
    prop({ projectionId: 2, playerId: 1, marketKey: "pass_yards", displayConfidence: 0.63 }),
    prop({ projectionId: 3, playerId: 1, marketKey: "completions", displayConfidence: null }),
    prop({ projectionId: 4, playerId: 1, marketKey: "pass_tds", displayConfidence: 0.71 }),
  ]);

  const player = groups[1].players[0];
  assert.deepEqual(
    player.props.map((p) => p.marketKey),
    ["pass_tds", "pass_yards", "completions", "pass_attempts"],
    "priced first by confidence, then the unpriced by market key",
  );
  assert.equal(player.unpricedCount, 2);
});

test("the market catalogue's order breaks ties among the unpriced", () => {
  const marketOrder = new Map([
    ["pass_yards", 1],
    ["pass_attempts", 2],
    ["completions", 3],
  ]);
  const groups = groupPropsByTeam(
    game(),
    [
      prop({ projectionId: 1, playerId: 1, marketKey: "completions", displayConfidence: null }),
      prop({ projectionId: 2, playerId: 1, marketKey: "pass_yards", displayConfidence: null }),
      prop({ projectionId: 3, playerId: 1, marketKey: "pass_attempts", displayConfidence: null }),
    ],
    { marketOrder },
  );

  assert.deepEqual(
    groups[1].players[0].props.map((p) => p.marketKey),
    ["pass_yards", "pass_attempts", "completions"],
    "without this the fallback is alphabetical, which is deterministic but arbitrary",
  );
});

test("a market missing from the catalogue sorts last rather than first", () => {
  // `?? MAX_SAFE_INTEGER` and not `?? 0`: a market the catalogue has not been
  // told about should not jump to the top of every player's block.
  const groups = groupPropsByTeam(
    game(),
    [
      prop({ projectionId: 1, playerId: 1, marketKey: "unknown_market", displayConfidence: null }),
      prop({ projectionId: 2, playerId: 1, marketKey: "pass_yards", displayConfidence: null }),
    ],
    { marketOrder: new Map([["pass_yards", 1]]) },
  );

  assert.deepEqual(
    groups[1].players[0].props.map((p) => p.marketKey),
    ["pass_yards", "unknown_market"],
  );
});

test("ordering is stable when confidence ties", () => {
  const groups = groupPropsByTeam(game(), [
    prop({ projectionId: 1, playerId: 2, playerName: "Beta", positionGroup: "WR", displayConfidence: 0.6 }),
    prop({ projectionId: 2, playerId: 1, playerName: "Alpha", positionGroup: "WR", displayConfidence: 0.6 }),
  ]);
  assert.deepEqual(
    groups[1].players.map((p) => p.playerName),
    ["Alpha", "Beta"],
  );
});
