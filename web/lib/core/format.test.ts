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

import { formatVenue } from "./format.ts";

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
