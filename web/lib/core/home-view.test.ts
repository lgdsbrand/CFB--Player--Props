/**
 * Tests for the home page's tiles.
 *
 * These guard claims rather than layout, which is why the logic lives in a
 * testable module at all. Three things must not regress, and none of them would
 * fail a type check:
 *
 *   1. Nothing may be labelled "+EV" before the model has been graded against a
 *      real closing line.
 *   2. The best-plays tile must never imply "most confident" means "best value".
 *   3. A tile with no rows behind it must not be a link. That defect has now
 *      been designed into this product three times.
 *
 * The zero-count fixtures are not hypothetical: they are production's 2026
 * week 1, where the top confidence is 0.574 and no row has an edge at all.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { homeTiles, pricingNote, type HomeCounts } from "./home-view.ts";

/** A mid-season slate: everything populated. */
function counts(overrides: Partial<HomeCounts> = {}): HomeCounts {
  return {
    props: 3848,
    games: 60,
    calls: 1476,
    edges: 246,
    developmentLine: 0,
    bookLine: 715,
    // 2025 week 12 on production, the fullest week in the database.
    cheatSheet: 405,
    ...overrides,
  };
}

/** Production, 2026 week 1. Measured, not invented. */
function openingWeekend(): HomeCounts {
  return {
    props: 2848,
    games: 69,
    calls: 433,
    edges: 0,
    developmentLine: 0,
    bookLine: 0,
    // Zero by arithmetic rather than by accident: no 2026 game has been played,
    // so there is no history to grade against any line.
    cheatSheet: 0,
  };
}

const SCOPE = { season: 2026, week: 1 };
const find = (c: HomeCounts, key: string) =>
  homeTiles(c, SCOPE).find((t) => t.key === key)!;

test("every tile carries the season and week it is describing", () => {
  // Without this a tile links to the board's default week, which is not
  // necessarily the week whose count the tile just printed.
  for (const tile of homeTiles(counts(), SCOPE)) {
    assert.match(tile.href!, /season=2026/, tile.key);
    assert.match(tile.href!, /week=1/, tile.key);
  }
});

test("nothing on the page is labelled +EV", () => {
  const text = JSON.stringify(homeTiles(counts(), SCOPE));
  assert.doesNotMatch(text, /\+EV/i);
  assert.doesNotMatch(text, /expected value/i);
  assert.doesNotMatch(text, /\bprofit/i);
  assert.doesNotMatch(text, /\bsharp\b/i);
});

test("the best-plays tile says confidence is not value", () => {
  const best = find(counts(), "best");
  assert.notEqual(best.caveat, null);
  assert.match(best.caveat!, /not the same as best value/i);
});

test("best plays orders by confidence and does not impose a floor", () => {
  // A 65% floor was tried here and returned ZERO on both live weeks: the top
  // confidence on an opening slate is 0.574. "Top most confident" is an
  // ordering, not a threshold.
  const best = find(counts(), "best");
  assert.match(best.href!, /preset=best/);
  assert.doesNotMatch(best.href!, /conf=/);
});

test("the two shortcut tiles lead to a preset list, not to the filtered board", () => {
  // The client's request: "do away with all the filters and just have all the
  // plays in those sections, just a table view of the plays." A preset is that
  // decision; linking with `sort=` or `edges=` would land him back on the whole
  // filter apparatus with the answer somewhere underneath it.
  for (const [key, expected] of [
    ["best", "preset=best"],
    ["edges", "preset=edges"],
  ] as const) {
    const href = find(counts(), key).href!;
    assert.match(href, new RegExp(expected));
    assert.doesNotMatch(href, /position=|market=|game=|conference=|q=/);
  }
});

test("top edges links to its own preset list", () => {
  assert.match(find(counts(), "edges").href!, /preset=edges/);
});

test("every count travels with a label saying what it counts", () => {
  for (const tile of homeTiles(counts(), SCOPE)) {
    assert.ok(tile.countLabel.length > 0, tile.key);
  }
});

// -----------------------------------------------------------------------------
// A tile with nothing behind it does not link
// -----------------------------------------------------------------------------

test("a tile with no rows is not a link", () => {
  const edges = find(openingWeekend(), "edges");
  assert.equal(edges.href, null, "linking here lands the reader on an empty board");
  assert.notEqual(edges.unavailable, null);
});

test("the empty reason blames the market, not the software", () => {
  // Nothing is broken on an opening slate — the books have not opened these
  // props. A reader told "no results" concludes the data is missing.
  const edges = find(openingWeekend(), "edges");
  assert.match(edges.unavailable!, /no sportsbook has posted/i);
  assert.doesNotMatch(edges.unavailable!, /error|failed|unavailable/i);
});

test("opening weekend still offers props, games and best plays", () => {
  // The regression that matters in the other direction: hiding a tile that DOES
  // have rows. 433 calls exist on 2026 week 1 even with no edges at all.
  const week1 = openingWeekend();
  for (const key of ["props", "games", "best"]) {
    const tile = find(week1, key);
    assert.notEqual(tile.href, null, key);
    assert.equal(tile.unavailable, null, key);
  }
});

test("a tile that links never also carries an unavailable reason", () => {
  for (const c of [counts(), openingWeekend()]) {
    for (const tile of homeTiles(c, SCOPE)) {
      assert.equal(
        tile.href === null,
        tile.unavailable !== null,
        `${tile.key} must be exactly one of linked or explained`,
      );
    }
  }
});

// -----------------------------------------------------------------------------
// The pricing note
// -----------------------------------------------------------------------------

test("a slate with no placeholder lines needs no pricing note", () => {
  assert.equal(pricingNote(counts({ developmentLine: 0 })), null);
});

test("with no book line at all, the note says the two tiles coincide", () => {
  // A synthetic line de-vigs to exactly 0.500, so an edge is confidence minus
  // 50% and Top Edges is Best Plays reordered. Two tiles quietly showing the
  // same rows, one implying the market was beaten, is the worst version of this
  // page.
  const note = pricingNote(counts({ developmentLine: 433, bookLine: 0 }))!;
  assert.match(note, /placeholder/i);
  assert.match(note, /50%/);
  assert.match(note, /Top Edges is Best Plays/i);
});

test("with a mix, the note reports how many are still placeholders", () => {
  const note = pricingNote(counts({ developmentLine: 1200, bookLine: 50 }))!;
  assert.match(note, /1,200/, "the locale is pinned, so never 1.200");
  assert.doesNotMatch(
    note,
    /Top Edges is Best Plays/i,
    "that claim is only true while nothing is priced by a book",
  );
});
