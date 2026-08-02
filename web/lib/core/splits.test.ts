/**
 * Tests for the hit-rate splits and the defense-view mapping.
 *
 *   npm run test
 *
 * WHAT IS WORTH TESTING HERE. Not that a list can be partitioned. The failures
 * that matter are the ones that still render as a confident number: a rank band
 * silently swallowing an unrated opponent, an empty bucket showing as 0%, a
 * tercile boundary that stops meaning "a third" when the FBS count moves, and a
 * market whose stat has no defensive counterpart quietly borrowing a
 * neighbouring column.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { DefenseGameRow, PlayerGameLogRow } from "@/lib/core/types";

import {
  defenseStatForMarket,
  defenseStatsFor,
  perGame,
  rankFraction,
} from "./defense-view.ts";
import { gradeGames, type GradedGame } from "./hit-rate.ts";
import {
  rankBands,
  rankSplits,
  summariseAll,
  venueSplits,
  windowSplits,
} from "./splits.ts";

function game(
  week: number,
  values: Partial<PlayerGameLogRow> = {},
): PlayerGameLogRow {
  return {
    playerId: 1,
    gameId: 1000 + week,
    season: 2025,
    week,
    positionGroup: "WR",
    isHome: true,
    opponentTeamId: 500 + week,
    opponentAbbreviation: "OPP",
    opponentSchool: "Opponent",
    startDate: null,
    neutralSite: false,
    passAttempts: null,
    passCompletions: null,
    passYards: null,
    passTds: null,
    interceptions: null,
    rushAttempts: null,
    rushYards: null,
    rushTds: null,
    targets: null,
    receptions: null,
    recYards: null,
    recTds: null,
    offensiveTds: null,
    ...values,
  };
}

function graded(...games: PlayerGameLogRow[]): GradedGame[] {
  return gradeGames(games, "rec_yards", 50, "over");
}

// -----------------------------------------------------------------------------
// Windows and venue
// -----------------------------------------------------------------------------
test("window splits are labelled by their window, not by what they found", () => {
  const rows = graded(
    game(4, { recYards: 60 }),
    game(3, { recYards: 60 }),
    game(2, { recYards: 20 }),
    game(1, { recYards: 60 }),
  );
  const splits = windowSplits(rows, [5, 10]);

  assert.deepEqual(
    splits.map((s) => s.label),
    ["L5", "L10"],
  );
  // Both windows reach past the log, so both see all four games. The label
  // still says L5 / L10 — the denominator is what says how many there were.
  assert.equal(splits[0].summary.decided, 4);
  assert.equal(splits[1].summary.decided, 4);
});

test("an empty venue bucket is dropped rather than shown as 0%", () => {
  // Every game at home: there is no away split, and rendering one would put a
  // bold 0% next to "Away" for a player who has not played away yet.
  const rows = graded(
    game(2, { recYards: 60, isHome: true }),
    game(1, { recYards: 60, isHome: true }),
  );
  const splits = venueSplits(rows);

  assert.deepEqual(
    splits.map((s) => s.key),
    ["home"],
  );
});

test("neutral-site games get their own bucket, not folded into home", () => {
  const rows = graded(
    game(3, { recYards: 60, isHome: true }),
    game(2, { recYards: 20, isHome: false }),
    game(1, { recYards: 60, isHome: true, neutralSite: true }),
  );
  const splits = venueSplits(rows);

  assert.deepEqual(
    splits.map((s) => s.key),
    ["home", "away", "neutral"],
  );
  assert.equal(splits[0].summary.decided, 1);
  assert.equal(splits[2].summary.decided, 1);
});

test("summariseAll counts every game in the bucket, not a fixed window", () => {
  const rows = graded(
    ...Array.from({ length: 8 }, (_, i) => game(i + 1, { recYards: 60 })),
  );
  assert.equal(summariseAll(rows).decided, 8);
  assert.equal(summariseAll(rows).rate, 1);
});

// -----------------------------------------------------------------------------
// Rank bands
// -----------------------------------------------------------------------------
test("bands are thirds of the field, whatever the field size is", () => {
  // FBS membership moves with realignment, so a hardcoded "top 32" would stop
  // meaning a third. 134 defenses → 45 / 45 / the rest.
  const bands = rankBands(134);

  assert.deepEqual(
    bands.map((b) => [b.minRank, b.maxRank]),
    [
      [1, 45],
      [46, 90],
      [91, Number.POSITIVE_INFINITY],
    ],
  );
});

test("the bands cover every rank with no gap and no overlap", () => {
  for (const size of [3, 10, 130, 134, 136]) {
    const bands = rankBands(size);
    for (let rank = 1; rank <= size; rank += 1) {
      const matched = bands.filter(
        (b) => rank >= b.minRank && rank <= b.maxRank,
      );
      assert.equal(matched.length, 1, `rank ${rank} of ${size}`);
    }
  }
});

test("rank 1 is the SOFT band — the scale is inverted", () => {
  // The single most common way to get this backwards. Rank 1 allows the MOST.
  assert.equal(rankBands(134)[0].key, "soft");
  assert.equal(rankBands(134)[2].key, "tough");
});

test("a degenerate field does not produce an empty band", () => {
  const bands = rankBands(0);
  assert.equal(bands[0].minRank, 1);
  assert.equal(bands[0].maxRank, 1);
});

test("games split into the band their opponent held THAT week", () => {
  const rows = graded(
    game(3, { recYards: 60 }), // rank 5 → soft
    game(2, { recYards: 20 }), // rank 120 → tough
    game(1, { recYards: 60 }), // rank 70 → middle
  );
  const ranks = new Map([
    [1003, 5],
    [1002, 120],
    [1001, 70],
  ]);
  const { splits, unranked } = rankSplits(rows, ranks, rankBands(134));

  assert.equal(unranked, 0);
  assert.deepEqual(
    splits.map((s) => [s.key, s.summary.hits, s.summary.decided]),
    [
      ["soft", 1, 1],
      ["middle", 1, 1],
      ["tough", 0, 1],
    ],
  );
});

test("an opponent with no rank is set aside, never bucketed", () => {
  // A defense goes unranked early in the season or when it is not FBS.
  // Filing it under "tough" or "soft" would invent a matchup difficulty.
  const rows = graded(game(2, { recYards: 60 }), game(1, { recYards: 60 }));
  const { splits, unranked } = rankSplits(
    rows,
    new Map([[1002, 5]]),
    rankBands(134),
  );

  assert.equal(unranked, 1);
  assert.equal(splits.length, 1);
  assert.equal(splits[0].summary.decided, 1);
});

test("bands with no games are dropped, so no band renders as 0%", () => {
  const rows = graded(game(1, { recYards: 60 }));
  const { splits } = rankSplits(rows, new Map([[1001, 5]]), rankBands(134));

  assert.deepEqual(
    splits.map((s) => s.key),
    ["soft"],
  );
});

// -----------------------------------------------------------------------------
// Defense view
// -----------------------------------------------------------------------------
function defenseGame(week: number, values: Partial<DefenseGameRow> = {}) {
  return {
    splitId: week,
    gameId: 2000 + week,
    defenseTeamId: 1,
    offenseTeamId: 2,
    offenseSchool: "Offense",
    offenseAbbreviation: "OFF",
    season: 2025,
    week,
    positionGroup: "WR" as const,
    startDate: null,
    neutralSite: false,
    defenseIsHome: true,
    plays: null,
    rushAttempts: null,
    rushYardsAllowed: null,
    rushTdsAllowed: null,
    targets: null,
    receptionsAllowed: null,
    recYardsAllowed: null,
    recTdsAllowed: null,
    ...values,
  } satisfies DefenseGameRow;
}

test("a passing market has no defensive counterpart, and says so", () => {
  // The position split disaggregates a defense by who it conceded to. The QB is
  // the only passer, so there is no "pass yards allowed to QBs" split — and
  // quietly returning rushing yards under that heading would be worse than
  // returning nothing.
  assert.equal(defenseStatForMarket("pass_yards"), null);
  assert.equal(defenseStatForMarket("pass_tds"), null);
  assert.equal(defenseStatForMarket("pass_attempts"), null);
  assert.equal(defenseStatForMarket("completions"), null);

  assert.equal(defenseStatForMarket("rec_yards")?.key, "rec_yards");
  assert.equal(defenseStatForMarket("rush_yards")?.key, "rush_yards");
  assert.equal(defenseStatForMarket("offensive_tds")?.key, "total_tds");
});

test("QB defense columns are rushing only", () => {
  assert.deepEqual(
    defenseStatsFor("QB").map((s) => s.key),
    ["rush_attempts", "rush_yards", "rush_tds"],
  );
  assert.ok(defenseStatsFor("RB").some((s) => s.key === "rec_yards"));
  assert.ok(defenseStatsFor("TE").some((s) => s.key === "targets"));
});

test("a per-game mean ignores games missing the column, and is null if all are", () => {
  const stat = defenseStatsFor("WR").find((s) => s.key === "rec_yards")!;
  const rows = [
    defenseGame(1, { recYardsAllowed: 100 }),
    defenseGame(2, { recYardsAllowed: 200 }),
    defenseGame(3),
  ];

  assert.equal(perGame(rows, stat), 150);
  assert.equal(perGame([defenseGame(1)], stat), null);
  assert.equal(perGame([], stat), null);
});

test("a zero allowance is a real value and pulls the mean down", () => {
  const stat = defenseStatsFor("WR").find((s) => s.key === "rec_yards")!;
  const rows = [
    defenseGame(1, { recYardsAllowed: 100 }),
    defenseGame(2, { recYardsAllowed: 0 }),
  ];
  assert.equal(perGame(rows, stat), 50);
});

test("total TDs sums rushing and receiving without treating a null as absent", () => {
  const stat = defenseStatsFor("RB").find((s) => s.key === "total_tds")!;
  assert.equal(stat.value(defenseGame(1, { rushTdsAllowed: 2, recTdsAllowed: 1 })), 3);
  assert.equal(stat.value(defenseGame(1, { rushTdsAllowed: 2 })), 2);
  assert.equal(stat.value(defenseGame(1)), null);
});

test("rank fraction runs soft to tough, and refuses a degenerate field", () => {
  assert.equal(rankFraction(1, 134), 0);
  assert.equal(rankFraction(134, 134), 1);
  assert.equal(rankFraction(1, 1), null);
  assert.equal(rankFraction(1, 0), null);
});
