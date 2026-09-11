/**
 * Tests for the player page's URL state.
 *
 * REPORTED FROM THE LIVE SITE, 2026-09-11: open an NFL player from the props
 * board, go back to the board, and it is EMPTY and on NCAAF. `playerHref` never
 * wrote the sport, so every link into a player page -- the board's table and
 * cards, the cheat sheet, the no-vig table, the market and game tabs -- called
 * an NFL player a college one. The page then read the week list, the defense
 * ratings and the header toggle for college, and its back link carried no sport
 * either, landing on a college board for a week college had no rows in.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { parsePlayerParams, playerHref } from "./player-params.ts";

test("playerHref carries the sport, and puts it first", () => {
  assert.equal(
    playerHref({ playerId: 42, sport: "nfl", season: 2026, week: 2, market: "rec_yards" }),
    "/player/42?sport=nfl&season=2026&week=2&market=rec_yards",
  );
});

test("college stays out of the URL, like every other link builder", () => {
  // Every existing shared college link omits it and must keep meaning college.
  assert.equal(
    playerHref({ playerId: 7, sport: "cfb", season: 2025, week: 9 }),
    "/player/7?season=2025&week=9",
  );
});

test("a market or game tab on an NFL player page keeps the sport", () => {
  // The tabs rebuild the link from the page's own params, so a sport parsed
  // back out of the URL has to be written straight back in.
  const href = playerHref({ playerId: 42, sport: "nfl", season: 2026, week: 2 });
  const parsed = parsePlayerParams(
    42,
    Object.fromEntries(new URLSearchParams(href.split("?")[1] ?? "")),
  );
  assert.equal(parsed.sport, "nfl");
  assert.equal(
    playerHref(parsed, { market: "rush_yards" }),
    "/player/42?sport=nfl&season=2026&week=2&market=rush_yards",
  );
  assert.equal(
    playerHref(parsed, { game: 16408 }),
    "/player/42?sport=nfl&season=2026&week=2&game=16408",
  );
});

test("a missing or unknown sport parses as college rather than throwing", () => {
  assert.equal(parsePlayerParams(1, {}).sport, "cfb");
  assert.equal(parsePlayerParams(1, { sport: "xfl" }).sport, "cfb");
});
