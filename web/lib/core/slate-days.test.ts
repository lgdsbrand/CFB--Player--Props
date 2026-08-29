/**
 * Tests for splitting a slate week into days.
 *
 * THE ONE THAT MATTERS is the late kickoff. Memphis at UNLV kicks
 * 2026-08-30T02:00:00Z, which is Saturday night in the US. A UTC-based grouping
 * files it under Sunday and splits one game night across two days — the exact
 * failure a day filter is meant to remove, and one that looks perfectly correct
 * in every test written with a lunchtime kickoff.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

// Relative, not aliased: these are VALUES, so they survive type stripping and
// the test runner has to resolve them for real. The type-only import below
// keeps the alias because it is erased before Node sees it.
import { findSlateDay, slateDayKey, slateDays } from "./slate-days.ts";
import type { GameSummary } from "./types.ts";

function game(gameId: number, startDate: string | null): GameSummary {
  return {
    gameId,
    season: 2026,
    week: 1,
    startDate,
    startTimeTbd: startDate === null,
    neutralSite: false,
    completed: false,
    homePoints: null,
    awayPoints: null,
    homeTeamId: 1,
    homeSchool: "Home",
    homeAbbreviation: "HOM",
    homeColor: null,
    homeAltColor: null,
    awayTeamId: 2,
    awaySchool: "Away",
    awayAbbreviation: "AWY",
    awayColor: null,
    awayAltColor: null,
  } as GameSummary;
}

test("a kickoff after midnight UTC stays on the US day it is played", () => {
  // 02:00Z on the 30th is 22:00 Eastern on the 29th.
  assert.equal(slateDayKey("2026-08-30T02:00:00Z"), "2026-08-29");
  // The noon kickoff the same day, for contrast.
  assert.equal(slateDayKey("2026-08-29T16:00:00Z"), "2026-08-29");
});

test("the whole opening Saturday lands on one day", () => {
  const days = slateDays([
    game(1, "2026-08-29T16:00:00Z"),
    game(2, "2026-08-29T19:00:00Z"),
    game(3, "2026-08-29T23:00:00Z"),
    game(4, "2026-08-30T02:00:00Z"),
  ]);
  assert.equal(days.length, 1);
  assert.equal(days[0].key, "2026-08-29");
  assert.deepEqual(days[0].gameIds, [1, 2, 3, 4]);
});

test("standard time is handled, not a fixed offset", () => {
  // November: Eastern is UTC-5, so 02:00Z on the 8th is 21:00 on the 7th.
  assert.equal(slateDayKey("2026-11-08T02:00:00Z"), "2026-11-07");
  // In August the same wall clock is UTC-4. A fixed offset cannot do both.
  assert.equal(slateDayKey("2026-08-08T02:00:00Z"), "2026-08-07");
});

test("days come back chronologically whatever order the games arrive in", () => {
  const days = slateDays([
    game(3, "2026-09-05T16:00:00Z"),
    game(1, "2026-08-29T16:00:00Z"),
    game(2, "2026-09-03T22:00:00Z"),
  ]);
  assert.deepEqual(
    days.map((d) => d.key),
    ["2026-08-29", "2026-09-03", "2026-09-05"],
  );
});

test("a TBD kickoff belongs to no day rather than being guessed into one", () => {
  assert.equal(slateDayKey(null), undefined);
  const days = slateDays([game(1, "2026-08-29T16:00:00Z"), game(2, null)]);
  assert.equal(days.length, 1);
  assert.deepEqual(days[0].gameIds, [1]);
});

test("an unparseable date is dropped, not turned into Invalid Date", () => {
  assert.equal(slateDayKey("not a date"), undefined);
  assert.deepEqual(slateDays([game(1, "not a date")]), []);
});

test("labels name the weekday, because that is how a slate is discussed", () => {
  const [day] = slateDays([game(1, "2026-08-29T16:00:00Z")]);
  assert.equal(day.label, "Sat Aug 29");
  assert.equal(day.weekday, "Sat");
});

test("an unknown day resolves to all days, not to an empty board", () => {
  const days = slateDays([game(1, "2026-08-29T16:00:00Z")]);
  // A stale link carrying last week's Saturday must not strand the reader.
  assert.equal(findSlateDay(days, "2026-09-05"), undefined);
  assert.equal(findSlateDay(days, undefined), undefined);
  assert.equal(findSlateDay(days, "2026-08-29")?.key, "2026-08-29");
});
