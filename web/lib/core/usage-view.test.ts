/**
 * Usage share (migration 0071).
 *
 * Scope: which share a surface shows and what it does with a missing one. The
 * arithmetic is a mean; the decisions worth pinning are that a market picks the
 * share rather than the position alone, that absence never renders as zero, and
 * that the denominator travels with every figure.
 */

import assert from "node:assert/strict";
import { test, describe } from "node:test";

import type { PlayerGameLogRow } from "./types.ts";
import {
  formatUsageShare,
  summariseUsage,
  usageForRow,
  usageStatForMarket,
  usageStatsFor,
} from "./usage-view.ts";

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
    q1PassYards: null,
    q1RushAttempts: null,
    q1RushYards: null,
    q1RushTds: null,
    q1Targets: null,
    q1Receptions: null,
    q1RecYards: null,
    q1RecTds: null,
    q1OffensiveTds: null,
    targetShare: null,
    rushShare: null,
    snapShare: null,
    ...values,
  };
}

const TARGET = usageStatForMarket("rec_yards")!;
const RUSH = usageStatForMarket("rush_yards")!;

describe("which share a market is read against", () => {
  test("receiving markets read the target share", () => {
    for (const column of ["targets", "receptions", "rec_yards", "rec_tds"]) {
      assert.equal(usageStatForMarket(column)?.kind, "target", column);
    }
  });

  test("rushing markets read the rush share", () => {
    for (const column of ["rush_attempts", "rush_yards", "rush_tds"]) {
      assert.equal(usageStatForMarket(column)?.kind, "rush", column);
    }
  });

  test("a back's two markets get two different shares", () => {
    // The whole reason this maps from the MARKET and not the position: one RB,
    // one board, two rows, and a single "RB usage" number would be wrong on one
    // of them.
    assert.equal(usageStatForMarket("rush_yards")?.kind, "rush");
    assert.equal(usageStatForMarket("receptions")?.kind, "target");
  });

  test("passing markets have no usage share rather than a near-miss", () => {
    // A quarterback's attempts ARE the team's, so there is no denominator. A
    // snap share printed under "pass yards" would answer a different question
    // while looking like an answer to that one.
    for (const column of ["pass_yards", "pass_attempts", "pass_completions"]) {
      assert.equal(usageStatForMarket(column), null, column);
    }
  });

  test("anytime TD takes the share the position scores from", () => {
    // The one market every position plays, so the one whose denominator depends
    // on who is being read. Routed through `rankBasis`, which already owns
    // "rushing for QB and RB, receiving for WR and TE" here and mirrors
    // `RANK_METRICS` in the worker.
    assert.equal(usageStatForMarket("offensive_tds", "RB")?.kind, "rush");
    assert.equal(usageStatForMarket("offensive_tds", "QB")?.kind, "rush");
    assert.equal(usageStatForMarket("offensive_tds", "WR")?.kind, "target");
    assert.equal(usageStatForMarket("offensive_tds", "TE")?.kind, "target");
  });

  test("anytime TD without a position is null rather than a guess", () => {
    // Half the board would be wrong on whichever share were picked blind, and
    // this is the market that draws the most attention of any.
    assert.equal(usageStatForMarket("offensive_tds"), null);
    assert.equal(usageStatForMarket("offensive_tds", null), null);
  });

  test("the position never overrides a market that names its own share", () => {
    // A back's rushing prop is read on carries whatever else he does.
    assert.equal(usageStatForMarket("rec_yards", "RB")?.kind, "target");
    assert.equal(usageStatForMarket("rush_yards", "WR")?.kind, "rush");
  });

  test("a first-quarter market reads its parent's share", () => {
    // Whole-game, because no first-quarter TEAM total exists to divide by.
    assert.equal(usageStatForMarket("q1_rec_yards")?.kind, "target");
    assert.equal(usageStatForMarket("q1_rush_yards")?.kind, "rush");
    assert.equal(usageStatForMarket("q1_pass_yards"), null);
  });
});

describe("which shares a position's page offers", () => {
  test("a quarterback is never offered a target share", () => {
    // It is zero by construction, and a column of noughts presented as usage is
    // worse than no column.
    const kinds = usageStatsFor("QB").map((stat) => stat.kind);
    assert.ok(!kinds.includes("target"));
    assert.deepEqual(kinds, ["snap", "rush"]);
  });

  test("a back gets carries first, then targets", () => {
    assert.deepEqual(usageStatsFor("RB").map((s) => s.kind), [
      "rush",
      "target",
      "snap",
    ]);
  });

  test("receivers and tight ends get targets and snaps", () => {
    for (const position of ["WR", "TE"] as const) {
      assert.deepEqual(usageStatsFor(position).map((s) => s.kind), [
        "target",
        "snap",
      ]);
    }
  });

  test("snap share is offered to college too, so its absence is visible", () => {
    // College has no snap source at all. Dropping the column for that sport
    // would hide the fact that the figure exists; showing it empty says so in
    // the place the reader is already looking.
    assert.ok(usageStatsFor("WR").some((stat) => stat.kind === "snap"));
  });

  test("every stat states its denominator", () => {
    for (const position of ["QB", "RB", "WR", "TE"] as const) {
      for (const stat of usageStatsFor(position)) {
        assert.ok(stat.hint.length > 0, `${position} ${stat.kind}`);
      }
    }
  });
});

describe("summarising a window", () => {
  test("the mean of the window's shares, with its sample", () => {
    const games = [
      game(5, { targetShare: 0.3 }),
      game(4, { targetShare: 0.2 }),
      game(3, { targetShare: 0.25 }),
    ];
    const summary = summariseUsage(games, TARGET, 5)!;
    assert.equal(summary.share, 0.25);
    assert.equal(summary.games, 3);
    assert.equal(summary.missing, 0);
  });

  test("the window cuts the oldest games, not the newest", () => {
    const games = [
      game(5, { targetShare: 0.4 }),
      game(4, { targetShare: 0.4 }),
      game(1, { targetShare: 0.1 }),
    ];
    const summary = summariseUsage(games, TARGET, 2)!;
    assert.equal(summary.share, 0.4);
    assert.equal(summary.games, 2);
  });

  test("a withheld share shrinks the denominator rather than counting as zero", () => {
    // College target attribution is incomplete on about one team-game in seven,
    // and those games store NULL. Averaging them in as 0 would halve a
    // receiver's share and read as a benching.
    const games = [
      game(5, { targetShare: 0.3 }),
      game(4, { targetShare: null }),
      game(3, { targetShare: 0.3 }),
    ];
    const summary = summariseUsage(games, TARGET, 5)!;
    assert.equal(summary.share, 0.3);
    assert.equal(summary.games, 2);
    assert.equal(summary.missing, 1);
  });

  test("a window with no share at all is null, not zero", () => {
    const games = [game(5), game(4)];
    assert.equal(summariseUsage(games, TARGET, 5), null);
  });

  test("an empty log is null", () => {
    assert.equal(summariseUsage([], TARGET, 5), null);
  });

  test("a real zero is kept — no carries is a usage fact", () => {
    // Distinct from absence: a receiver with 0 of his team's 27 carries has a
    // rush share, and it is zero.
    const summary = summariseUsage([game(5, { rushShare: 0 })], RUSH, 5)!;
    assert.equal(summary.share, 0);
    assert.equal(summary.games, 1);
  });
});

describe("one row, market and all", () => {
  const games = [game(5, { targetShare: 0.3 }), game(4, { targetShare: 0.2 })];

  test("the card and the table resolve a row the same way", () => {
    // Both surfaces call this. Two independent mappings from market to share is
    // the bug class this repo has shipped five times.
    const summary = usageForRow({ statColumn: "rec_yards" }, games, 5)!;
    assert.equal(summary.stat.kind, "target");
    assert.equal(summary.share, 0.25);
  });

  test("a market with no denominator is null, not a near-miss", () => {
    assert.equal(usageForRow({ statColumn: "pass_yards" }, games, 5), null);
  });

  test("the position reaches the market mapping through this", () => {
    const rushing = [game(5, { rushShare: 0.5 }), game(4, { rushShare: 0.4 })];
    assert.equal(
      usageForRow({ statColumn: "offensive_tds" }, rushing, 5, "RB")?.stat.kind,
      "rush",
    );
    assert.equal(
      usageForRow({ statColumn: "offensive_tds" }, rushing, 5),
      null,
    );
  });

  test("a row whose market the catalogue does not carry is null", () => {
    assert.equal(usageForRow(undefined, games, 5), null);
  });
});

describe("formatting", () => {
  test("whole percents only", () => {
    // Snap share arrives from nflverse rounded to two decimals, so a tenth of a
    // percent would be precision the source never had.
    assert.equal(formatUsageShare(0.3567), "36%");
    assert.equal(formatUsageShare(0), "0%");
    assert.equal(formatUsageShare(1), "100%");
  });

  test("absence is a dash, never 0%", () => {
    assert.equal(formatUsageShare(null), "—");
  });
});
