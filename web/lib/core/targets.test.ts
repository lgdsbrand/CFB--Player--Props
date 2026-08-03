/**
 * Tests for the weekly targets list.
 *
 * The failure mode this guards against is a list that looks right. A targets
 * panel showing five plausible team names is indistinguishable, by eye, from one
 * that ranked the wrong side of the matchup, dropped the conference filter, or
 * sorted unrated defenses to the top. So the properties are stated directly.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildWeeklyTargets,
  type TargetGame,
  type TargetRating,
} from "./targets.ts";

const HOME = 1;
const AWAY = 2;

function game(
  gameId: number,
  homeTeamId: number,
  awayTeamId: number,
  neutralSite = false,
): TargetGame {
  return {
    gameId,
    homeTeamId,
    awayTeamId,
    neutralSite,
    startDate: "2025-11-01T17:00:00Z",
  };
}

function rating(
  defenseTeamId: number,
  position: "QB" | "RB" | "WR" | "TE",
  rank: number,
  { rush = 100, rec = 150, games = 8 } = {},
): TargetRating {
  return {
    defenseTeamId,
    positionGroup: position,
    gamesIncluded: games,
    rankVsPosition: rank,
    adjRushYardsAllowedPg: rush,
    adjRecYardsAllowedPg: rec,
  };
}

function positions(result: ReturnType<typeof buildWeeklyTargets>) {
  return new Map(result.map((entry) => [entry.position, entry]));
}

test("both teams in a game appear, each defending against the other", () => {
  const result = positions(
    buildWeeklyTargets(
      [game(10, HOME, AWAY)],
      [rating(HOME, "RB", 1), rating(AWAY, "RB", 2)],
    ),
  ).get("RB")!;

  assert.equal(result.rows.length, 2);

  const homeDefending = result.rows.find((r) => r.defenseTeamId === HOME)!;
  assert.equal(homeDefending.offenseTeamId, AWAY);
  assert.equal(homeDefending.defenseIsHome, true);

  const awayDefending = result.rows.find((r) => r.defenseTeamId === AWAY)!;
  assert.equal(awayDefending.offenseTeamId, HOME);
  assert.equal(awayDefending.defenseIsHome, false);
});

test("the softest defense leads, and softest is the HIGHEST rank", () => {
  const result = positions(
    buildWeeklyTargets(
      [game(10, HOME, AWAY), game(11, 3, 4)],
      [
        rating(HOME, "RB", 40),
        rating(AWAY, "RB", 3),
        rating(3, "RB", 118),
        rating(4, "RB", 12),
      ],
    ),
  ).get("RB")!;

  assert.deepEqual(
    result.rows.map((r) => r.rank),
    [118, 40, 12, 3],
  );
});

test("each position ranks on the metric it is measured on", () => {
  const games = [game(10, HOME, AWAY)];
  const ratings = [
    rating(HOME, "QB", 1, { rush: 71.2, rec: 0.4 }),
    rating(HOME, "RB", 1, { rush: 158.0, rec: 40.0 }),
    rating(HOME, "WR", 1, { rush: 6.0, rec: 212.5 }),
    rating(HOME, "TE", 1, { rush: 0.3, rec: 58.1 }),
  ];
  const result = positions(buildWeeklyTargets(games, ratings));

  // QB is the one that matters: quarterbacks throw, so the receiving column is
  // trick-play noise and a QB row must never surface it. See RANK_METRICS in
  // worker/core/splits.py.
  assert.equal(result.get("QB")!.rows[0].value, 71.2);
  assert.equal(result.get("QB")!.basis.key, "rush");
  assert.ok(result.get("QB")!.basis.caveat);

  assert.equal(result.get("RB")!.rows[0].value, 158.0);
  assert.equal(result.get("WR")!.rows[0].value, 212.5);
  assert.equal(result.get("TE")!.rows[0].value, 58.1);

  // Only QB claims less than its label suggests.
  assert.equal(result.get("RB")!.basis.caveat, null);
  assert.equal(result.get("WR")!.basis.caveat, null);
});

test("the conference filter keeps the offense, not the defense", () => {
  // A reader narrowed to one conference wants THEIR players. The soft defense
  // is a fact about the other team, so filtering the defense would return the
  // exact complement of what was asked for.
  const result = positions(
    buildWeeklyTargets(
      [game(10, HOME, AWAY)],
      [rating(HOME, "RB", 1), rating(AWAY, "RB", 2)],
      { includeOffense: (teamId) => teamId === AWAY },
    ),
  ).get("RB")!;

  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].offenseTeamId, AWAY);
  assert.equal(result.rows[0].defenseTeamId, HOME);
});

test("an unrated defense is counted and dropped, never ranked last", () => {
  const result = positions(
    buildWeeklyTargets(
      [game(10, HOME, AWAY), game(11, 3, 4)],
      [rating(HOME, "RB", 5), rating(AWAY, "RB", 9)],
    ),
  ).get("RB")!;

  assert.equal(result.rows.length, 2);
  assert.equal(result.unrated, 2);
  assert.equal(result.onSlate, 4);
  assert.ok(result.rows.every((row) => row.rank > 0));
});

test("a defense rated but never assigned a rank is treated as unrated", () => {
  const noRank: TargetRating = { ...rating(HOME, "RB", 1), rankVsPosition: null };
  const result = positions(
    buildWeeklyTargets([game(10, HOME, AWAY)], [noRank, rating(AWAY, "RB", 4)]),
  ).get("RB")!;

  assert.equal(result.rows.length, 1);
  assert.equal(result.unrated, 1);
});

test("the national field size comes from every rating, not the slate", () => {
  // Only one game is played, but the rank was assigned among 136 defenses, so
  // "1 of 136" has to survive. Reporting the slate size would turn a national
  // rank into a fraction of two.
  const ratings = [rating(HOME, "RB", 1), rating(AWAY, "RB", 2)];
  for (let teamId = 3; teamId <= 136; teamId += 1) {
    ratings.push(rating(teamId, "RB", teamId));
  }

  const result = positions(
    buildWeeklyTargets([game(10, HOME, AWAY)], ratings),
  ).get("RB")!;

  assert.equal(result.rankedDefenses, 136);
  assert.equal(result.rows.length, 2);
});

test("every position is returned even when nothing is rated", () => {
  // The panel must be able to say "no rating yet" per position rather than
  // silently omit a column, which would read as "no soft defenses".
  const result = buildWeeklyTargets([game(10, HOME, AWAY)], []);

  assert.deepEqual(
    result.map((entry) => entry.position),
    ["QB", "RB", "WR", "TE"],
  );
  assert.ok(result.every((entry) => entry.rows.length === 0));
  assert.ok(result.every((entry) => entry.rankedDefenses === 0));
});

test("an empty slate produces empty lists, not a crash", () => {
  const result = buildWeeklyTargets([], [rating(HOME, "RB", 1)]);
  assert.equal(result.length, 4);
  assert.ok(result.every((entry) => entry.rows.length === 0));
  assert.ok(result.every((entry) => entry.onSlate === 0));
});

test("the limit caps each position independently", () => {
  const games: TargetGame[] = [];
  const ratings: TargetRating[] = [];
  for (let index = 0; index < 8; index += 1) {
    const home = 100 + index * 2;
    const away = home + 1;
    games.push(game(index, home, away));
    ratings.push(rating(home, "RB", index + 1), rating(away, "RB", index + 20));
  }

  const result = positions(buildWeeklyTargets(games, ratings, { limit: 3 }));
  assert.equal(result.get("RB")!.rows.length, 3);
  // Softest first, and softest is the HIGHEST rank: the away side was seeded
  // 20..27, so those lead.
  assert.deepEqual(
    result.get("RB")!.rows.map((row) => row.rank),
    [27, 26, 25],
  );
  // onSlate counts the whole slate, not the truncated list — it is the
  // denominator for "5 of 16 matchups", so truncation must not shrink it.
  assert.equal(result.get("RB")!.onSlate, 16);
});

test("neutral-site games carry the flag through to the row", () => {
  const result = positions(
    buildWeeklyTargets(
      [game(10, HOME, AWAY, true)],
      [rating(HOME, "RB", 1), rating(AWAY, "RB", 2)],
    ),
  ).get("RB")!;

  assert.ok(result.rows.every((row) => row.neutralSite));
});
