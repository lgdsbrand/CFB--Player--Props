import assert from "node:assert/strict";
import { test } from "node:test";

import {
  booksDisagreeOnLine,
  compareNoVigRows,
  consensusDelta,
  holdBand,
  parseNoVigSort,
  summarise,
  type NoVigRow,
} from "./no-vig.ts";

function row(overrides: Partial<NoVigRow> = {}): NoVigRow {
  return {
    lineId: 1,
    season: 2026,
    week: 1,
    gameId: 10,
    startDate: "2026-09-05T16:00:00Z",
    playerId: 100,
    playerName: "Amareon Blue",
    positionGroup: "RB",
    teamId: 1,
    teamSchool: "Test State",
    teamAbbreviation: "TST",
    teamColor: null,
    teamAltColor: null,
    isHome: true,
    opponentSchool: "Rival Tech",
    opponentAbbreviation: "RIV",
    conferenceName: "SEC",
    marketKey: "rush_yards",
    marketLabel: "RUSH YDS",
    marketEmoji: null,
    line: 52.5,
    sportsbookKey: "fanduel",
    sportsbookName: "FanDuel",
    overPrice: -114,
    underPrice: -114,
    hold: 0.0654,
    fairProbOver: 0.5,
    fairProbUnder: 0.5,
    fairPriceOver: -100,
    fairPriceUnder: -100,
    booksAtLine: 1,
    booksOnMarket: 1,
    linesOnMarket: 1,
    consensusProbOver: 0.5,
    lineProbOverMin: 0.5,
    lineProbOverMax: 0.5,
    isBestOver: true,
    isBestUnder: true,
    capturedAt: "2026-09-04T12:00:00Z",
    ...overrides,
  };
}

test("holdBand splits on the measured range, not a convention", () => {
  // 4.75%-7.94% observed across a full week; the bands have to separate quotes
  // inside that range or every row lands in one bucket.
  assert.equal(holdBand(0.0475), "keen");
  assert.equal(holdBand(0.061), "typical");
  assert.equal(holdBand(0.0794), "dear");
  // Above anything measured, which is the point: a ceiling set to the observed
  // maximum could never fire.
  assert.equal(holdBand(0.12), "wide");
});

test("consensusDelta is null with one book, not zero", () => {
  // The distinction the page depends on: zero means the book agrees with the
  // market, null means there is no market to agree with.
  assert.equal(consensusDelta(row({ booksAtLine: 1 })), null);
  const delta = consensusDelta(
    row({ booksAtLine: 3, fairProbOver: 0.52, consensusProbOver: 0.5 }),
  );
  // A tolerance, not a literal: 0.52 - 0.5 is 0.020000000000000018 in binary
  // floating point, and pinning the exact double tests the arithmetic of IEEE
  // 754 rather than anything this module does.
  assert.ok(Math.abs(delta! - 0.02) < 1e-9, `expected ~0.02, got ${delta}`);
});

test("consensusDelta signs toward the over", () => {
  const shortOfMarket = row({
    booksAtLine: 2,
    fairProbOver: 0.48,
    consensusProbOver: 0.5,
  });
  assert.ok(consensusDelta(shortOfMarket)! < 0);
});

test("booksDisagreeOnLine separates a thin market from a split one", () => {
  assert.equal(booksDisagreeOnLine(row({ booksAtLine: 1, linesOnMarket: 1 })), false);
  // One book at this number, but three books priced the prop at three numbers.
  assert.equal(booksDisagreeOnLine(row({ booksAtLine: 1, linesOnMarket: 3 })), true);
});

test("hold sorts cheapest first", () => {
  const cheap = row({ lineId: 1, hold: 0.048 });
  const dear = row({ lineId: 2, hold: 0.079 });
  assert.ok(compareNoVigRows(cheap, dear, "hold") < 0);
});

test("consensus sort puts the biggest disagreement first and single books last", () => {
  const wayOff = row({
    lineId: 1,
    booksAtLine: 4,
    fairProbOver: 0.56,
    consensusProbOver: 0.5,
  });
  const closeIn = row({
    lineId: 2,
    booksAtLine: 4,
    fairProbOver: 0.505,
    consensusProbOver: 0.5,
  });
  const alone = row({ lineId: 3, booksAtLine: 1 });

  const sorted = [alone, closeIn, wayOff].sort((a, b) =>
    compareNoVigRows(a, b, "consensus"),
  );
  assert.deepEqual(
    sorted.map((r) => r.lineId),
    [1, 2, 3],
  );
});

test("consensus sort ranks by SIZE of the gap, either direction", () => {
  const under = row({
    lineId: 1,
    booksAtLine: 3,
    fairProbOver: 0.44,
    consensusProbOver: 0.5,
  });
  const over = row({
    lineId: 2,
    booksAtLine: 3,
    fairProbOver: 0.52,
    consensusProbOver: 0.5,
  });
  // -0.06 is a bigger disagreement than +0.02 and must sort first.
  assert.ok(compareNoVigRows(under, over, "consensus") < 0);
});

test("every sort is total — rows equal on the key still order by line id", () => {
  // Identical but for the id. The query ends every ORDER BY on line_id, so this
  // must too, or the page reshuffles rows the database considered settled.
  const a = row({ lineId: 1, playerName: "Alpha", hold: 0.06 });
  const b = row({ lineId: 2, playerName: "Alpha", hold: 0.06 });
  for (const sort of ["hold", "player", "market", "consensus"] as const) {
    assert.ok(
      compareNoVigRows(a, b, sort) < 0,
      `${sort} left two distinct rows tied`,
    );
    // `+ 0` normalises -0, which is not strictly equal to 0.
    assert.equal(
      compareNoVigRows(a, b, sort) + 0,
      -compareNoVigRows(b, a, sort) + 0,
      `${sort} is not antisymmetric`,
    );
  }
});

test("parseNoVigSort falls back rather than trusting a hand-edited URL", () => {
  assert.equal(parseNoVigSort("consensus"), "consensus");
  assert.equal(parseNoVigSort("nonsense"), "hold");
  assert.equal(parseNoVigSort(undefined), "hold");
});

test("summarise counts player-markets and books, not rows", () => {
  const summary = summarise([
    row({ lineId: 1, sportsbookKey: "fanduel", hold: 0.05, booksAtLine: 2 }),
    row({ lineId: 2, sportsbookKey: "betmgm", hold: 0.07, booksAtLine: 2 }),
    // Same player, different market: a second player-market, same book.
    row({ lineId: 3, sportsbookKey: "fanduel", marketKey: "receptions", hold: 0.09 }),
  ]);

  assert.equal(summary.quotes, 3);
  assert.equal(summary.playerMarkets, 2);
  assert.equal(summary.books, 2);
  assert.equal(summary.medianHold, 0.07);
  assert.equal(summary.shoppable, 2);
});

test("summarise uses a median so one stale quote cannot move the header", () => {
  const rows = [
    row({ lineId: 1, hold: 0.06 }),
    row({ lineId: 2, hold: 0.065 }),
    row({ lineId: 3, hold: 0.07 }),
    row({ lineId: 4, hold: 0.9 }),
  ];
  // The mean would read 27%; the median describes the slate the reader sees.
  assert.equal(summarise(rows).medianHold, 0.0675);
});

test("summarise on nothing returns no median rather than zero", () => {
  assert.equal(summarise([]).medianHold, null);
});
