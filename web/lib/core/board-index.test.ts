/**
 * The board's filters, sort and counts on the in-memory week index.
 *
 * Each of these predicates replaced a SQL one (`buildBoardQuery`, `board_counts`,
 * the home head-counts), so every test pins the SQL behaviour, NULLs included:
 * a comparison against NULL is not true in SQL, and a JS `null >= 0.6` is false
 * too, but `null !== "x"` is true — which is where a port goes quietly wrong.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  INDEX_COLUMNS,
  type IndexRow,
  countHomeBoard,
  countIndex,
  decodeIndex,
  matchesBoardFilters,
  matchesCardFilters,
  sortIndexRows,
} from "./board-index.ts";

const NOW = new Date("2026-10-24T18:00:00Z");
const LATER = Date.parse("2026-10-24T20:00:00Z");
const EARLIER = Date.parse("2026-10-24T16:00:00Z");

function row(overrides: Partial<IndexRow> = {}): IndexRow {
  return {
    projectionId: 1,
    playerId: 10,
    gameId: 100,
    marketKey: "rec_yards",
    positionGroup: "WR",
    startMs: LATER,
    conferenceName: "SEC",
    conferenceIsDisplayed: true,
    playerName: "Ryan Williams",
    hasBookLine: true,
    hasCall: true,
    sportsbookKey: "fanduel",
    teamPollRank: 5,
    side: "over",
    isBinary: false,
    edge: 0.06,
    displayConfidence: 0.61,
    opponentRankVsPosition: 80,
    publishesCall: true,
    bookProbOver: 0.52,
    ...overrides,
  };
}

test("decodeIndex reads the positional contract with migration 0104", () => {
  const raw = [
    [
      1346409, 107, 7005, "anytime_td", "TE", "2026-10-23T23:00:00+00:00", "ACC", true,
      "Jake Taylor", true, true, "draftkings", null, "under", true, null, "0.0022", 60,
      true, null,
    ],
  ];
  assert.equal(raw[0].length, INDEX_COLUMNS.length);
  const [decoded] = decodeIndex(raw);
  assert.equal(decoded.projectionId, 1346409);
  assert.equal(decoded.startMs, Date.parse("2026-10-23T23:00:00Z"));
  assert.equal(decoded.teamPollRank, null);
  assert.equal(decoded.edge, null);
  // numeric can arrive as a string; it must become a number, not stay text.
  assert.equal(decoded.displayConfidence, 0.0022);
  assert.equal(decoded.side, "under");
});

test("a TBD kickoff decodes to null and counts as upcoming", () => {
  const raw = [[1, 2, 3, "m", null, null, null, null, "x", false, false, null, null, null, false, null, null, null, false, null]];
  const [decoded] = decodeIndex(raw);
  assert.equal(decoded.startMs, null);
  assert.ok(matchesBoardFilters(decoded, { kickoffCutoff: NOW, displayedConferencesOnly: false }));
});

test("kickoff cutoff keeps start >= cutoff and drops started games", () => {
  assert.ok(matchesBoardFilters(row({ startMs: LATER }), { kickoffCutoff: NOW }));
  assert.ok(matchesBoardFilters(row({ startMs: NOW.getTime() }), { kickoffCutoff: NOW }));
  assert.ok(!matchesBoardFilters(row({ startMs: EARLIER }), { kickoffCutoff: NOW }));
});

test("displayed conferences by default; a NULL flag is not displayed", () => {
  assert.ok(!matchesBoardFilters(row({ conferenceIsDisplayed: false }), {}));
  assert.ok(!matchesBoardFilters(row({ conferenceIsDisplayed: null }), {}));
  assert.ok(matchesBoardFilters(row({ conferenceIsDisplayed: null }), { displayedConferencesOnly: false }));
  // A named conference replaces the displayed-only rule, as in the SQL.
  assert.ok(matchesBoardFilters(row({ conferenceIsDisplayed: false, conferenceName: "MAC" }), { conferenceName: "MAC" }));
  assert.ok(!matchesBoardFilters(row(), { conferenceName: "MAC" }));
});

test("an empty game or market list means nothing, not everything", () => {
  assert.ok(!matchesBoardFilters(row(), { gameIds: [] }));
  assert.ok(!matchesBoardFilters(row(), { marketKeys: [] }));
  assert.ok(matchesBoardFilters(row(), { gameIds: [100], marketKeys: ["rec_yards"] }));
});

test("search is a case-insensitive substring on the name", () => {
  assert.ok(matchesBoardFilters(row(), { search: "williAMS" }));
  assert.ok(!matchesBoardFilters(row(), { search: "smith" }));
  // A % is literal, as the escaped ilike made it.
  assert.ok(!matchesBoardFilters(row(), { search: "%" }));
});

test("thresholds exclude NULLs, as gte on a NULL column does", () => {
  assert.ok(!matchesBoardFilters(row({ edge: null }), { edgesOnly: true, edgeThreshold: 0.05 }));
  assert.ok(matchesBoardFilters(row({ edge: 0.05 }), { edgesOnly: true, edgeThreshold: 0.05 }));
  assert.ok(!matchesBoardFilters(row({ edge: 0.049 }), { edgesOnly: true }));
  assert.ok(!matchesBoardFilters(row({ displayConfidence: null }), { minConfidence: 0.5 }));
  assert.ok(!matchesBoardFilters(row({ opponentRankVsPosition: null }), { minOpponentRank: 1 }));
  assert.ok(matchesBoardFilters(row({ opponentRankVsPosition: 100 }), { minOpponentRank: 100 }));
});

test("the side switch drops no-call rows, and Under drops yes/no markets", () => {
  assert.ok(!matchesBoardFilters(row({ side: null }), { side: "over" }));
  assert.ok(matchesBoardFilters(row({ side: "under", isBinary: false }), { side: "under" }));
  assert.ok(!matchesBoardFilters(row({ side: "under", isBinary: true }), { side: "under" }));
  assert.ok(matchesBoardFilters(row({ side: "over", isBinary: true }), { side: "over" }));
});

test("ranked, book-line, call and major-book filters", () => {
  assert.ok(!matchesBoardFilters(row({ teamPollRank: null }), { rankedOnly: true }));
  assert.ok(!matchesBoardFilters(row({ hasBookLine: false }), { withBookLineOnly: true }));
  assert.ok(!matchesBoardFilters(row({ hasCall: false }), { withCallOnly: true }));
  assert.ok(!matchesBoardFilters(row({ sportsbookKey: "betmgm" }), { majorBooksOnly: true }));
  assert.ok(!matchesBoardFilters(row({ sportsbookKey: null }), { majorBooksOnly: true }));
  assert.ok(matchesBoardFilters(row({ sportsbookKey: "draftkings" }), { majorBooksOnly: true }));
});

test("card filters keep sub-card filters and ignore player-picking ones", () => {
  const weak = row({ edge: null, displayConfidence: 0.2, opponentRankVsPosition: 1 });
  assert.ok(matchesCardFilters(weak, { edgesOnly: true, minConfidence: 0.9, minOpponentRank: 120 }));
  assert.ok(!matchesCardFilters(row({ marketKey: "receptions" }), { marketKey: "rec_yards" }));
  assert.ok(!matchesCardFilters(row({ side: "under", isBinary: true }), { side: "under" }));
  assert.ok(!matchesCardFilters(row({ sportsbookKey: "betmgm" }), { majorBooksOnly: true }));
});

test("the sort demotes started games, then edge DESC NULLS LAST, then the id", () => {
  const rows = [
    row({ projectionId: 1, edge: null, displayConfidence: 0.9 }),
    row({ projectionId: 2, edge: 0.02 }),
    row({ projectionId: 3, edge: 0.1, startMs: EARLIER }),
    row({ projectionId: 4, edge: 0.08 }),
    row({ projectionId: 5, edge: 0.08 }),
  ];
  const order = sortIndexRows(rows, "edge", NOW).map((r) => r.projectionId);
  // 0.08 ties break on confidence (equal) then opponent rank (equal) then id.
  assert.deepEqual(order, [4, 5, 2, 1, 3]);
});

test("a TBD kickoff has not kicked off", () => {
  const rows = [row({ projectionId: 1, startMs: EARLIER }), row({ projectionId: 2, startMs: null })];
  assert.deepEqual(sortIndexRows(rows, "edge", NOW).map((r) => r.projectionId), [2, 1]);
});

test("opponent-rank sort is softest first with unrated last", () => {
  const rows = [
    row({ projectionId: 1, opponentRankVsPosition: null }),
    row({ projectionId: 2, opponentRankVsPosition: 10 }),
    row({ projectionId: 3, opponentRankVsPosition: 120 }),
  ];
  assert.deepEqual(sortIndexRows(rows, "opponent_rank", NOW).map((r) => r.projectionId), [3, 2, 1]);
});

test("countIndex reproduces board_counts, NULL book included", () => {
  const rows = [
    row({ projectionId: 1 }),
    row({ projectionId: 2, publishesCall: false }),
    row({ projectionId: 3, conferenceIsDisplayed: false }),
    row({ projectionId: 4, startMs: EARLIER }),
    row({ projectionId: 5, sportsbookKey: "synthetic", bookProbOver: 0.5, edge: null }),
    row({ projectionId: 6, sportsbookKey: null, bookProbOver: 0.5, hasBookLine: false, hasCall: false, edge: null }),
    row({ projectionId: 7, bookProbOver: 0.5, edge: 0.01 }),
  ];
  const counts = countIndex(rows, { edgeThreshold: 0.05, syntheticBookKey: "synthetic", kickoffCutoff: NOW });
  assert.deepEqual(counts, {
    rows: 4, // 1, 5, 6, 7
    withCall: 3,
    withBookLine: 3,
    withDevLine: 1,
    withEvenBookPrice: 1, // 7 only: 5 is synthetic, 6 has no book
    overThreshold: 1,
  });
  assert.equal(countIndex(rows, { edgeThreshold: 0.05, syntheticBookKey: "synthetic" }).rows, 5);
});

test("countHomeBoard counts what each tile links to, publishes_call or not", () => {
  const rows = [
    row({ projectionId: 1 }),
    row({ projectionId: 2, publishesCall: false, hasCall: false, edge: null, hasBookLine: false, sportsbookKey: null }),
    row({ projectionId: 3, sportsbookKey: "synthetic" }),
    row({ projectionId: 4, startMs: EARLIER }),
  ];
  assert.deepEqual(
    countHomeBoard(rows, { edgeThreshold: 0.05, syntheticBookKey: "synthetic", kickoffCutoff: NOW }),
    { props: 3, calls: 2, edges: 2, developmentLine: 1, bookLine: 1 },
  );
});
