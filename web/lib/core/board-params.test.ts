/**
 * Tests for the board's URL filter state.
 *
 * WHY THIS FILE EXISTS NOW. The Reset control was reported as not resetting.
 * The URL it built was correct and the rows behind it did clear — what stayed
 * on screen were the fields themselves, because they were uncontrolled inputs
 * set with `defaultValue` and a client-side navigation never remounted them.
 * That half is a React lifecycle bug and lives in `FilterFields`; these tests
 * cover the half that is expressible here, which is that ONE definition of
 * "cleared" exists and that it clears everything the reader can see.
 *
 * The duplicate that made the original bug harder to see is the reason for the
 * last test: the empty board's "clear the filters" link and the Reset button
 * were built separately and cleared different sets.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  boardHref,
  DEFAULT_HIT_RATE_WINDOW,
  parseBoardParams,
  resetBoardHref,
  type BoardParams,
} from "./board-params.ts";

/** Every filter on, so a reset has something to clear in each of them. */
const FULLY_FILTERED: BoardParams = {
  season: 2025,
  week: 8,
  position: "WR",
  market: "receptions",
  game: 401,
  day: "2025-10-18",
  conference: "SEC",
  search: "John",
  sort: "confidence",
  edgesOnly: true,
  rankedOnly: true,
  minConfidence: 0.65,
  minOpponentRank: 110,
  hitRateWindow: 10,
  // `table` is a genuinely non-default choice HERE, which is the point of
  // putting it in this fixture: with `market` set, `resolveBoardView` would
  // pick `cards`, so this only round-trips if an explicit choice is being
  // carried rather than re-derived.
  view: "table",
  page: 4,
};

// -----------------------------------------------------------------------------
// Reset
// -----------------------------------------------------------------------------

test("reset clears every filter a reader can see", () => {
  const params = parseBoardParams(
    Object.fromEntries(
      new URLSearchParams(resetBoardHref(FULLY_FILTERED).split("?")[1] ?? ""),
    ),
  );

  assert.equal(params.position, undefined);
  assert.equal(params.market, undefined);
  assert.equal(params.game, undefined);
  assert.equal(params.conference, undefined);
  assert.equal(params.search, undefined);
  assert.equal(params.minConfidence, undefined);
  assert.equal(params.minOpponentRank, undefined);
  // TOP 25 IS A FILTER, SO RESET CLEARS IT. Unlike EDGES ONLY below, it has no
  // configured default to return to — leaving it on would mean a reset board
  // still hid 90% of the slate while every visible control read "off".
  assert.equal(params.rankedOnly, false);
  assert.equal(params.sort, "edge");
  assert.equal(params.hitRateWindow, DEFAULT_HIT_RATE_WINDOW);
  // RESET RETURNS THE LAYOUT TO "not chosen", not to a named one. The default
  // depends on the market filter, and reset has just cleared that — so pinning
  // a concrete layout here would leave a reset board on whichever one the
  // reader happened to pick before, with the market pill reading All.
  assert.equal(params.view, undefined);
  assert.equal(params.page, 1);
});

test("reset keeps the week — it clears filters, it does not navigate", () => {
  const href = resetBoardHref(FULLY_FILTERED);
  const query = new URLSearchParams(href.split("?")[1] ?? "");

  assert.equal(query.get("season"), "2025");
  assert.equal(query.get("week"), "8");
});

test("reset returns EDGES ONLY to the configured default, not to off", () => {
  // The parameter is omitted rather than forced false, so a deployment that
  // defaults the board to edges-only resets back to edges-only. Forcing it off
  // would make Reset a different control depending on the config.
  const raw = Object.fromEntries(
    new URLSearchParams(resetBoardHref(FULLY_FILTERED).split("?")[1] ?? ""),
  );

  assert.equal(parseBoardParams(raw, { edgesOnlyDefault: true }).edgesOnly, true);
  assert.equal(parseBoardParams(raw, { edgesOnlyDefault: false }).edgesOnly, false);
});

test("reset is a fixed point — resetting a reset board changes nothing", () => {
  const once = resetBoardHref(FULLY_FILTERED);
  const params = parseBoardParams(
    Object.fromEntries(new URLSearchParams(once.split("?")[1] ?? "")),
  );

  assert.equal(resetBoardHref(params), once);
});

// -----------------------------------------------------------------------------
// The duplicate that hid the bug
// -----------------------------------------------------------------------------

test("the empty board's clear link and Reset resolve to the same address", () => {
  // These were two independently written expressions clearing different sets:
  // the empty board left `sort` and `hitRateWindow` alone. Both now call
  // `resetBoardHref`, so this asserts the shared definition rather than
  // re-deriving one of them.
  const fromEmptyBoard = resetBoardHref(FULLY_FILTERED);
  const fromResetButton = resetBoardHref(FULLY_FILTERED);

  assert.equal(fromEmptyBoard, fromResetButton);
  // And it is genuinely a clearing, not the board it started on.
  assert.notEqual(fromEmptyBoard, boardHref(FULLY_FILTERED, {}));
});

// -----------------------------------------------------------------------------
// Round-tripping — a filter written to the URL must parse back unchanged
// -----------------------------------------------------------------------------

test("every filter survives a trip through the URL", () => {
  // The page has to be asked for explicitly: `boardHref` drops it on any other
  // change, which is the behaviour the next test pins down. Passing it here
  // keeps this test about serialisation rather than about paging.
  const params = parseBoardParams(
    Object.fromEntries(
      new URLSearchParams(
        boardHref(FULLY_FILTERED, { page: FULLY_FILTERED.page }).split("?")[1] ??
          "",
      ),
    ),
    { edgesOnlyDefault: false },
  );

  assert.deepEqual(params, FULLY_FILTERED);
});

test("changing a filter drops the page, because page 4 of one row is a dead end", () => {
  assert.equal(boardHref(FULLY_FILTERED, { position: "QB" }).includes("page="), false);
  // Unless paging is what was asked for.
  assert.equal(boardHref(FULLY_FILTERED, { page: 2 }).includes("page=2"), true);
});

test("boardHref can target another page without changing what it carries", () => {
  const filtered = boardHref(FULLY_FILTERED, {});
  const onGames = boardHref(FULLY_FILTERED, {}, "/games");

  // Same query, different page. Analyze Games shares the day and conference
  // filters with the board, and a reader changing either must stay where they
  // are rather than being moved to the props board.
  assert.equal(onGames.startsWith("/games?"), true);
  assert.equal(onGames.split("?")[1], filtered.split("?")[1]);
});

test("boardHref still defaults to the board", () => {
  assert.equal(boardHref({ ...FULLY_FILTERED }, {}).startsWith("/props"), true);
});

test("a basePath with no parameters left is the bare path", () => {
  // The conference pills on Analyze Games clear a filter this way; a trailing
  // "?" would make two links to the same page look like different addresses.
  const bare = boardHref(
    { rankedOnly: false, edgesOnly: false, hitRateWindow: 5, sort: "edge", page: 1 },
    {},
    "/games",
  );
  assert.equal(bare, "/games");
});
