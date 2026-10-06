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
  MATCHUP_POSITIONS,
  softestMatchup,
  type ViewGame,
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
