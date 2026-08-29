/**
 * Tests for the board's row grouping, sort chain and line-coverage arithmetic.
 *
 * TWO OF THESE GUARD THINGS THAT WERE WRONG AND LOOKED RIGHT.
 *
 * `lineCoverage` replaces a subtraction the page did inline: "still awaiting a
 * line" was `withCall - withBookLine`, which counts rows called against a
 * STRUCTURAL line (anytime TD's `markets.default_line`) — rows that have a line
 * and are waiting for nothing. It rendered a plausible number on every slate,
 * which is why it survived a phase.
 *
 * `boardSortKeys` guards the state the product spends most of a live week in.
 * Edge and confidence both come from `picks`; with no book line there is no
 * pick and both are null on every row, so the chain has to keep deciding
 * something after they run out.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  barWindow,
  boardSortKeys,
  callFor,
  displayQuantile,
  gradeRow,
  groupIntoCards,
  lineCoverage,
  positionInRange,
  seasonToDate,
  summariseRow,
  type BoardSort,
} from "./board-view.ts";
import { gradeFor } from "./grade.ts";
import { hitRate, hitRateTone } from "./hit-rate.ts";
import type { BoardRow, PlayerGameLogRow } from "./types.ts";

// -----------------------------------------------------------------------------
// Line coverage
// -----------------------------------------------------------------------------

test("a row called against a structural line is not awaiting one", () => {
  // The exact shape of 2025 week 12: 3,878 projections, 3,600 called, 2,621
  // priced by the synthetic book. The 979 difference is the anytime-TD market,
  // called against `default_line` 0.5 — the population the old subtraction
  // reported as "still awaiting a line".
  const coverage = lineCoverage({
    rows: 3878,
    withCall: 3600,
    withBookLine: 2621,
    withDevLine: 2621,
  });

  assert.equal(coverage.structuralLine, 979);
  assert.equal(coverage.awaitingLine, 278);
  assert.notEqual(coverage.awaitingLine, coverage.structuralLine);
});

test("awaiting counts every projection with no call at all", () => {
  const coverage = lineCoverage({
    rows: 100,
    withCall: 0,
    withBookLine: 0,
    withDevLine: 0,
  });
  assert.equal(coverage.awaitingLine, 100);
  assert.equal(coverage.structuralLine, 0);
});

test("synthetic and real book lines are separated", () => {
  const coverage = lineCoverage({
    rows: 100,
    withCall: 80,
    withBookLine: 60,
    withDevLine: 25,
  });
  assert.equal(coverage.developmentLine, 25);
  assert.equal(coverage.bookLine, 35);
  assert.equal(coverage.structuralLine, 20);
  assert.equal(coverage.awaitingLine, 20);
});

test("a slate priced entirely by real books reports no development lines", () => {
  const coverage = lineCoverage({
    rows: 100,
    withCall: 90,
    withBookLine: 90,
    withDevLine: 0,
  });
  assert.equal(coverage.developmentLine, 0);
  assert.equal(coverage.bookLine, 90);
});

test("the four populations always account for every projection", () => {
  const cases = [
    { rows: 3878, withCall: 3600, withBookLine: 2621, withDevLine: 2621 },
    { rows: 100, withCall: 80, withBookLine: 60, withDevLine: 25 },
    { rows: 1, withCall: 1, withBookLine: 1, withDevLine: 1 },
    { rows: 0, withCall: 0, withBookLine: 0, withDevLine: 0 },
  ];
  for (const counts of cases) {
    const c = lineCoverage(counts);
    assert.equal(
      c.awaitingLine + c.structuralLine + c.developmentLine + c.bookLine,
      counts.rows,
      `populations do not sum for ${JSON.stringify(counts)}`,
    );
  }
});

test("inconsistent counts clamp to zero rather than going negative", () => {
  // Counts arrive from five independent queries against a live week. A write
  // landing between them can make them disagree by a row or two, and a
  // negative population would render as "-3 still awaiting a line".
  const coverage = lineCoverage({
    rows: 10,
    withCall: 12,
    withBookLine: 15,
    withDevLine: 20,
  });
  assert.equal(coverage.awaitingLine, 0);
  assert.equal(coverage.structuralLine, 0);
  assert.equal(coverage.bookLine, 0);
  assert.equal(coverage.developmentLine, 15);
});

// -----------------------------------------------------------------------------
// The sort chain
// -----------------------------------------------------------------------------

const SORTS: BoardSort[] = ["edge", "confidence", "opponent_rank"];

/** Columns that are null on every row until a book posts a price. */
const PICK_ONLY = new Set(["edge", "confidence", "display_confidence"]);

test("no sort orders on raw confidence", () => {
  // `picks.confidence` is the mass on the CALLED side. Anytime TD is called
  // UNDER on ~97% of picks, so ordering on it puts the players most certain
  // NOT to score at the top — which is what the board did on the first slate
  // that had no book lines to sort by edge instead.
  for (const sort of SORTS) {
    for (const key of boardSortKeys(sort)) {
      assert.notEqual(
        key.column,
        "confidence",
        `${sort} orders the board by the probability of the called side`,
      );
    }
  }
});

test("the confidence control sorts on the number the card prints", () => {
  assert.equal(boardSortKeys("confidence")[0].column, "display_confidence");
});

test("every sort still decides something on an unpriced slate", () => {
  for (const sort of SORTS) {
    const keys = boardSortKeys(sort);
    const meaningful = keys.filter(
      (key) => !PICK_ONLY.has(key.column) && key.column !== "projection_id",
    );
    assert.ok(
      meaningful.length > 0,
      `${sort} falls through to the id tiebreak with no line attached`,
    );
    assert.equal(meaningful[0].column, "opponent_rank_vs_position");
  }
});

test("no descending term sorts nulls first", () => {
  for (const sort of SORTS) {
    for (const key of boardSortKeys(sort)) {
      if (key.ascending) continue;
      assert.equal(
        key.nullsFirst,
        false,
        `${sort} would float null ${key.column} to the top of the board`,
      );
    }
  }
});

test("opponent rank always sorts softest first", () => {
  for (const sort of SORTS) {
    const rank = boardSortKeys(sort).find(
      (key) => key.column === "opponent_rank_vs_position",
    );
    assert.ok(rank, `${sort} has no opponent-rank term`);
    // Rank 1 is the BEST defense, so softest-first is descending.
    assert.equal(rank.ascending, false);
  }
});

test("every sort ends in a stable ascending id tiebreak", () => {
  for (const sort of SORTS) {
    const keys = boardSortKeys(sort);
    const last = keys[keys.length - 1];
    assert.equal(last.column, "projection_id");
    assert.equal(last.ascending, true);
  }
});

test("no sort names the same column twice", () => {
  for (const sort of SORTS) {
    const columns = boardSortKeys(sort).map((key) => key.column);
    assert.equal(
      new Set(columns).size,
      columns.length,
      `${sort} repeats a column: ${columns.join(", ")}`,
    );
  }
});

test("each sort leads with the column its control names", () => {
  assert.equal(boardSortKeys("edge")[0].column, "edge");
  assert.equal(boardSortKeys("confidence")[0].column, "display_confidence");
  assert.equal(
    boardSortKeys("opponent_rank")[0].column,
    "opponent_rank_vs_position",
  );
  // The default matches the default the URL parser produces.
  assert.deepEqual(boardSortKeys(), boardSortKeys("edge"));
});

// -----------------------------------------------------------------------------
// Grouping
// -----------------------------------------------------------------------------

function row(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    projectionId: 1,
    pickId: null,
    season: 2025,
    week: 12,
    marketKey: "rec_yards",
    marketName: "Receiving yards",
    marketLabel: "REC YDS",
    marketEmoji: null,
    isBinary: false,
    playerId: 10,
    playerName: "A Player",
    positionGroup: "WR",
    teamId: 1,
    teamSchool: "Home U",
    teamAbbreviation: "HOME",
    teamColor: null,
    teamAltColor: null,
    opponentTeamId: 2,
    opponentSchool: "Away U",
    opponentAbbreviation: "AWAY",
    gameId: 100,
    startDate: "2025-11-15T17:00:00Z",
    neutralSite: false,
    isHome: true,
    venueName: "Home Stadium",
    venueCity: "Hometown",
    venueState: "TX",
    teamSpread: -7.5,
    gameTotal: 52.5,
    gameLineProviders: 3,
    teamPollRank: null,
    opponentPollRank: null,
    line: null,
    side: null,
    confidence: null,
    modelProbOver: null,
    bookProbOver: null,
    edge: null,
    hasBookLine: false,
    hasCall: false,
    overPrice: null,
    underPrice: null,
    sportsbookKey: null,
    sportsbookName: null,
    projectedMedian: 50,
    projectedP10: 20,
    projectedP90: 90,
    // Null, as it is on every board row: the ladder is selected only by the
    // player-detail query. Grouping never reads it.
    ladder: null,
    priorWeight: 0.3,
    effectiveSample: 6.5,
    opponentRankVsPosition: 100,
    conferenceName: "SEC",
    conferenceIsDisplayed: true,
    displayConfidence: null,
    ...overrides,
  };
}

test("markets group into one card per player-game", () => {
  const cards = groupIntoCards([
    row({ projectionId: 1, marketKey: "rec_yards" }),
    row({ projectionId: 2, marketKey: "receptions" }),
    row({ projectionId: 3, playerId: 11 }),
  ]);
  assert.equal(cards.length, 2);
  assert.equal(cards[0].markets.length, 2);
});

test("the card carries the evidence its rows agree on", () => {
  // Both are player-level, so every market on a card holds the same pair and
  // the card takes them from the first row. A card that read them per market
  // would print one number five times and imply it varies by market.
  const [card] = groupIntoCards([
    row({ projectionId: 1, priorWeight: 0.5, effectiveSample: 6 }),
    row({ projectionId: 2, priorWeight: 0.5, effectiveSample: 6 }),
  ]);
  assert.equal(card.priorWeight, 0.5);
  assert.equal(card.effectiveSample, 6);
});

test("the same player in two games stays two cards", () => {
  const cards = groupIntoCards([
    row({ projectionId: 1, gameId: 100 }),
    row({ projectionId: 2, gameId: 101 }),
  ]);
  assert.equal(cards.length, 2);
});

test("a card with no priced market has no confidence to grade", () => {
  // Drives the GRADE badge: null means the badge is absent, not that the
  // player graded badly.
  const [card] = groupIntoCards([row(), row({ projectionId: 2 })]);
  assert.equal(card.topConfidence, null);
  assert.equal(card.topEdge, null);
});

test("top confidence and edge take the best across markets", () => {
  const [card] = groupIntoCards([
    row({ projectionId: 1, displayConfidence: 0.61, edge: 0.02 }),
    row({ projectionId: 2, displayConfidence: 0.74, edge: null }),
    row({ projectionId: 3, displayConfidence: null, edge: 0.09 }),
  ]);
  assert.equal(card.topConfidence, 0.74);
  assert.equal(card.topEdge, 0.09);
});

test("a card whose only call is anytime TD is not graded on the under side", () => {
  // The defect exactly as it rendered: a QB with a 5% chance to score carries
  // an anytime-TD pick called UNDER at 95% confidence. Reading `confidence`
  // badged him A+ directly beside the "5% TO SCORE" on the same card.
  const [card] = groupIntoCards([
    row({
      projectionId: 1,
      marketKey: "anytime_td",
      isBinary: true,
      hasCall: true,
      line: 0.5,
      side: "under",
      confidence: 0.95,
      modelProbOver: 0.05,
      displayConfidence: 0.05,
    }),
    row({ projectionId: 2, marketKey: "pass_yards" }),
  ]);

  assert.equal(card.topConfidence, 0.05);
  assert.equal(gradeFor(card.topConfidence), "D");
  assert.notEqual(gradeFor(card.topConfidence), "A+");
});

test("a likely scorer still grades well", () => {
  const [card] = groupIntoCards([
    row({
      marketKey: "anytime_td",
      isBinary: true,
      hasCall: true,
      side: "over",
      confidence: 0.82,
      modelProbOver: 0.82,
      displayConfidence: 0.82,
    }),
  ]);
  assert.equal(gradeFor(card.topConfidence), "A+");
});

test("card order follows the order rows arrived in", () => {
  const cards = groupIntoCards([
    row({ projectionId: 1, playerId: 30, playerName: "Third" }),
    row({ projectionId: 2, playerId: 10, playerName: "First" }),
    row({ projectionId: 3, playerId: 20, playerName: "Second" }),
  ]);
  assert.deepEqual(
    cards.map((card) => card.playerName),
    ["Third", "First", "Second"],
  );
});

// -----------------------------------------------------------------------------
// The projection bar's geometry
// -----------------------------------------------------------------------------

test("a degenerate range yields no bar rather than a zero-width one", () => {
  assert.equal(barWindow(40, 40, null), null);
  assert.equal(barWindow(null, 90, null), null);
  assert.equal(positionInRange(50, 40, 40), null);
});

test("no projected range starts below a value the stat can take", () => {
  // 555 of 747 rush-yards rows on 2025 week 13 stored a negative p10, the
  // worst at -187.1. The bar drew, and labelled, a floor no outcome reaches.
  assert.equal(displayQuantile(-187.1), 0);
  assert.equal(displayQuantile(-9.9e-24), 0);
  assert.equal(displayQuantile(0), 0);
  assert.equal(displayQuantile(12.4), 12.4);
  assert.equal(displayQuantile(null), null);
  assert.equal(displayQuantile(Number.NaN), null);
});

test("the bar's window starts at the floor, not at the stored p10", () => {
  const window = barWindow(-187.1, 61.4, null);
  assert.ok(window);
  // Padding is applied outside the floor, so the low end stays close to zero
  // rather than reopening the negative tail the floor exists to close.
  assert.ok(window.low > -10, `window opened at ${window.low}`);
  assert.ok(window.low <= 0);
});

test("a range entirely below the floor yields no bar", () => {
  // p90 <= 0 leaves nothing to draw once the floor is applied. Better an
  // explicit "unavailable" than a bar of zero width.
  assert.equal(barWindow(-40, -5, null), null);
});

test("the window widens to contain a line outside the projected range", () => {
  // The interesting case: a line far outside p10..p90 is what a high-confidence
  // call looks like, and clipping it would hide the reason the pick exists.
  const window = barWindow(20, 90, 150);
  assert.ok(window);
  assert.ok(window.high > 150);
  assert.ok(window.low <= 20);
});

test("a position outside the window clamps instead of overflowing", () => {
  assert.equal(positionInRange(200, 0, 100), 1);
  assert.equal(positionInRange(-50, 0, 100), 0);
});

test("the bar renders without a line, which is the common state", () => {
  const window = barWindow(20, 90, null);
  assert.ok(window);
  assert.equal(positionInRange(null, window.low, window.high), null);
});

// -----------------------------------------------------------------------------
// The call — the three states the card and the table both render
// -----------------------------------------------------------------------------

test("anytime TD reports the chance the player SCORES, not the called side", () => {
  // THE BUG THIS EXISTS TO PREVENT, and it has shipped on this board before.
  // The called side on anytime TD is `under` on ~97% of picks, so a receiver
  // with a 12% chance to score carries confidence 0.88 on the under. A surface
  // that read `side` and `confidence` would print "UNDER 88%" — true, useless,
  // and indistinguishable from a strong pick. `modelProbOver` is P(more than
  // 0.5 touchdowns), which IS the anytime-scorer probability CLAUDE.md §1 asks
  // for.
  const call = callFor(
    row({
      isBinary: true,
      hasCall: true,
      hasBookLine: true,
      line: 0.5,
      side: "under",
      confidence: 0.88,
      modelProbOver: 0.12,
    }),
  );

  assert.equal(call.kind, "binary");
  assert.equal(call.kind === "binary" ? call.probability : null, 0.12);
});

test("a priced row reports the side and the mass past the line", () => {
  const call = callFor(
    row({ hasCall: true, hasBookLine: true, line: 62.5, side: "over", confidence: 0.71 }),
  );

  assert.equal(call.kind, "call");
  assert.equal(call.kind === "call" ? call.side : null, "over");
  assert.equal(call.kind === "call" ? call.confidence : null, 0.71);
});

test("an unpriced row is a lean, which is the state most of a live week is in", () => {
  // College books post props Thursday or Friday for Saturday games
  // (CLAUDE.md §7), so this is the ordinary case, not an error path.
  assert.equal(callFor(row()).kind, "lean");
});

// -----------------------------------------------------------------------------
// Grading — shared by the card's last-5 row and the table's hit-rate columns
// -----------------------------------------------------------------------------

const REC_YARDS = { statColumn: "rec_yards", isBinary: false };
const ANYTIME_TD = { statColumn: "offensive_tds", isBinary: true };

function log(week: number, values: Partial<PlayerGameLogRow> = {}): PlayerGameLogRow {
  return {
    playerId: 10,
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

test("a row with no line grades nothing, and says so with an empty array", () => {
  // Not a zero. "Never hit" and "no line to grade against" are different
  // claims, and a 0% in a hit-rate column is the one that gets acted on.
  const graded = gradeRow(row({ line: null, side: null }), REC_YARDS, [
    log(1, { recYards: 80 }),
  ]);

  assert.deepEqual(graded, []);
  assert.equal(seasonToDate(graded), null);
});

test("an unmapped stat column grades nothing rather than guessing", () => {
  // The market catalogue is a database table, so a market can be added by
  // INSERT with no deploy. An unmapped column must surface as "no data", never
  // as a coincidentally-named field's number.
  const graded = gradeRow(
    row({ line: 3.5, side: "over" }),
    { statColumn: "tackles_for_loss", isBinary: false },
    [log(1, { recYards: 80 })],
  );

  assert.deepEqual(graded, []);
});

test("a binary market grades on the OVER side whatever the call was", () => {
  // Grading on the call would paint a green dot for every week a player did
  // NOT score — accurate against "under 0.5" and the opposite of what a reader
  // takes from a green dot.
  const graded = gradeRow(
    row({ isBinary: true, line: 0.5, side: "under" }),
    ANYTIME_TD,
    [log(1, { offensiveTds: 1 }), log(2, { offensiveTds: 0 })],
  );

  assert.equal(graded.length, 2);
  assert.equal(graded.find((g) => g.week === 1)?.hit, true);
  assert.equal(graded.find((g) => g.week === 2)?.hit, false);
});

test("the card's summary and the table's columns come off one grading pass", () => {
  // The guarantee that matters: `summariseRow` (the card) and `gradeRow` +
  // `hitRate` (the table) must not be able to disagree about the same row.
  const boardRow = row({ line: 70, side: "over" });
  const games = [
    log(4, { recYards: 90 }),
    log(3, { recYards: 40 }),
    log(2, { recYards: 100 }),
    log(1, { recYards: 60 }),
  ];

  const fromCard = summariseRow(boardRow, REC_YARDS, games, 2);
  const fromTable = hitRate(gradeRow(boardRow, REC_YARDS, games), 2);

  assert.deepEqual(fromCard, fromTable);
  assert.equal(fromCard?.rate, 0.5);
});

test("SZN counts every graded game, and L-windows only the recent ones", () => {
  const boardRow = row({ line: 70, side: "over" });
  const games = [
    log(4, { recYards: 90 }),
    log(3, { recYards: 40 }),
    log(2, { recYards: 30 }),
    log(1, { recYards: 20 }),
  ];
  const graded = gradeRow(boardRow, REC_YARDS, games);

  assert.equal(hitRate(graded, 2).decided, 2);
  assert.equal(seasonToDate(graded)?.decided, 4);
  assert.equal(seasonToDate(graded)?.rate, 0.25);
});

test("a push leaves the denominator rather than counting as a miss", () => {
  // Synthetic development lines are a rounded trailing average and land on
  // whole numbers often, so ties are real here even though a book's
  // half-point makes them impossible on a posted line.
  const graded = gradeRow(row({ line: 70, side: "over" }), REC_YARDS, [
    log(2, { recYards: 70 }),
    log(1, { recYards: 90 }),
  ]);
  const summary = seasonToDate(graded);

  assert.equal(summary?.pushes, 1);
  assert.equal(summary?.decided, 1);
  assert.equal(summary?.rate, 1);
});

// -----------------------------------------------------------------------------
// Hit-rate tone — shared by the last-5 row and the table's columns
// -----------------------------------------------------------------------------

test("a hit rate near even gets no colour, because it is noise", () => {
  // Four of eight is not a signal, and on a board whose whole claim is
  // calibration, tinting it green would be the one lie that matters.
  assert.equal(hitRateTone(0.5), "muted");
  assert.equal(hitRateTone(0.55), "muted");
  assert.equal(hitRateTone(0.45), "muted");
});

test("only rates far enough from even to survive five games get a colour", () => {
  assert.equal(hitRateTone(0.6), "positive");
  assert.equal(hitRateTone(0.8), "positive");
  assert.equal(hitRateTone(0.4), "negative");
  assert.equal(hitRateTone(0), "negative");
});

test("a null rate is uncoloured — nothing was decided, which is not zero", () => {
  assert.equal(hitRateTone(null), "muted");
});

/*
 * EVEN-PRICED REAL BOOK LINES.
 *
 * Found live on the 2026 opening weekend: 33 of the board's 66 edges sat
 * against a real book price of -114/-114, which de-vigs to exactly 0.500 and
 * makes `edge` identical to `confidence - 50%`. The board's existing caveat
 * keys off the synthetic DEV book, so it stayed hidden and the top row
 * advertised a 34% edge that was purely a restatement of its own confidence.
 *
 * This is a SUBSET of `bookLine`, not a fifth population, so it is excluded
 * from the sum the other four satisfy — the invariant test above must keep
 * passing unchanged.
 */

test("even-priced real book lines are reported as a subset of bookLine", () => {
  const coverage = lineCoverage({
    rows: 100,
    withCall: 90,
    withBookLine: 60,
    withDevLine: 0,
    withEvenBookPrice: 33,
  });
  assert.equal(coverage.bookLine, 60);
  assert.equal(coverage.evenPricedBookLine, 33);
  // Still a subset: it must not consume any of the other populations.
  assert.equal(
    coverage.awaitingLine + coverage.structuralLine + coverage.developmentLine + coverage.bookLine,
    100,
  );
});

test("an absent even-price count reads as none, not as unknown", () => {
  // Every existing caller passes a BoardCounts without the field until the
  // query ships; the caveat must stay hidden rather than render "NaN rows".
  const coverage = lineCoverage({
    rows: 10,
    withCall: 8,
    withBookLine: 8,
    withDevLine: 0,
  });
  assert.equal(coverage.evenPricedBookLine, 0);
});

test("even-priced cannot exceed the real book lines it describes", () => {
  // Six independent count queries against a live week; a write landing between
  // them must not make the caveat claim more rows than the board has priced.
  const coverage = lineCoverage({
    rows: 100,
    withCall: 50,
    withBookLine: 40,
    withDevLine: 30,
    withEvenBookPrice: 999,
  });
  assert.equal(coverage.bookLine, 10);
  assert.equal(coverage.evenPricedBookLine, 10);
});

test("a negative even-price count clamps to zero", () => {
  const coverage = lineCoverage({
    rows: 100,
    withCall: 90,
    withBookLine: 60,
    withDevLine: 0,
    withEvenBookPrice: -5,
  });
  assert.equal(coverage.evenPricedBookLine, 0);
});
