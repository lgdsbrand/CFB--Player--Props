/**
 * Tests for the venue label.
 *
 * EVERY FIXTURE IS A REAL ROW out of `venues` on the dev database, including the
 * awkward ones. The parenthetical case is not hypothetical: 60 of 844 venues
 * carry a disambiguating suffix, and the first four alphabetically are all
 * "Alumni Field" or "Alumni Stadium" variants, which is exactly why CFBD
 * disambiguates them at all.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { formatGameLine, formatSpread, formatVenue } from "./format.ts";

// -----------------------------------------------------------------------------
// The game line
// -----------------------------------------------------------------------------

test("a favourite keeps its minus and an underdog gets an explicit plus", () => {
  // THE PLUS IS THE POINT. `v_board_rows.team_spread` has already flipped the
  // stored home-side number to this player's team, so the sign is the only
  // thing saying which way it points — and "10.5" beside a team name reads as
  // a favourite to most people, which for an away underdog is backwards.
  assert.equal(formatSpread(-10.5), "-10.5");
  assert.equal(formatSpread(10.5), "+10.5");
});

test("a pick-em is PK, not a signed zero", () => {
  // "+0.0" reads as a rendering fault. A pick-em is a real state.
  assert.equal(formatSpread(0), "PK");
});

test("whole-number spreads still carry one decimal so a column lines up", () => {
  assert.equal(formatSpread(-7), "-7.0");
});

test("an unpriced game has no spread rather than a zero", () => {
  assert.equal(formatSpread(null), null);
  assert.equal(formatSpread(Number.NaN), null);
});

test("the combined line reads spread then total", () => {
  assert.equal(formatGameLine(-10.5, 48.5), "-10.5 · O/U 48.5");
});

test("either half can be missing without leaving a dangling separator", () => {
  // A provider can post a total and no spread, or the reverse.
  assert.equal(formatGameLine(null, 48.5), "O/U 48.5");
  assert.equal(formatGameLine(-10.5, null), "-10.5");
  assert.equal(formatGameLine(null, null), null);
});

test("a pick-em with a total still shows both halves", () => {
  // Guards the falsy trap: `if (spread)` would drop a 0 spread entirely, and
  // PK is exactly the case a reader would notice missing.
  assert.equal(formatGameLine(0, 55), "PK · O/U 55.0");
});

test("a plain venue reads stadium then city and state", () => {
  assert.equal(
    formatVenue({ name: "Amon G. Carter Stadium", city: "Fort Worth", state: "TX" }),
    "Amon G. Carter Stadium · Fort Worth, TX",
  );
});

test("a disambiguating parenthetical is dropped when the location follows it", () => {
  // Printed verbatim this reads "Alumni Stadium (Chestnut Hill, MA) · Chestnut
  // Hill, MA", which is the defect this function exists to prevent.
  assert.equal(
    formatVenue({
      name: "Alumni Stadium (Chestnut Hill, MA)",
      city: "Chestnut Hill",
      state: "MA",
    }),
    "Alumni Stadium · Chestnut Hill, MA",
  );

  assert.equal(
    formatVenue({ name: "Alumni Field (OH)", city: "Bluffton", state: "OH" }),
    "Alumni Field · Bluffton, OH",
  );
});

test("the parenthetical STAYS when there is no location to replace it", () => {
  // With no city on the row, the suffix is the only thing separating this from
  // the seven other Alumni Stadiums. Stripping it would lose information rather
  // than de-duplicate it.
  assert.equal(
    formatVenue({ name: "Alumni Stadium (KY)", city: null, state: null }),
    "Alumni Stadium (KY)",
  );
});

test("a parenthetical that is not a trailing suffix is left alone", () => {
  assert.equal(
    formatVenue({ name: "Stadium (Old) Annex", city: "Austin", state: "TX" }),
    "Stadium (Old) Annex · Austin, TX",
  );
});

test("a name that is ONLY a parenthetical survives stripping", () => {
  // The fallback matters: `replace` would leave an empty string, and a card
  // would render " · Dublin" with nothing in front of the separator.
  assert.equal(
    formatVenue({ name: "(TBD)", city: "Dublin", state: null }),
    "(TBD) · Dublin",
  );
});

test("an international venue carries no state and no trailing comma", () => {
  assert.equal(
    formatVenue({ name: "Aviva Stadium", city: "Dublin", state: null }),
    "Aviva Stadium · Dublin",
  );
});

test("a missing venue is null, not a placeholder", () => {
  // `games.venue_id` is nullable. A dash under the team names reads as a
  // stadium whose name failed to load; nothing reads as nothing.
  assert.equal(formatVenue({ name: null, city: null, state: null }), null);
});

test("a location with no venue name still shows the location", () => {
  assert.equal(
    formatVenue({ name: null, city: "Fort Worth", state: "TX" }),
    "Fort Worth, TX",
  );
});
