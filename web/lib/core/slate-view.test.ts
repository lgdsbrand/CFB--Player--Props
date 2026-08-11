/**
 * Tests for which week the board opens on.
 *
 * THE FIXTURES ARE THE REAL 2026 SLATE, because the bug these cover was found
 * in production rather than in review: publishing 2026 week 2 moved the live
 * board off the opening weekend and hid week 1 behind the week strip's
 * expander. The first two tests would both have failed against the old
 * "latest week with output" rule.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { defaultWeek, findWeek } from "./slate-view.ts";
import type { SlateWeek } from "./types.ts";

function week(
  season: number,
  weekNumber: number,
  first: string | null,
  last: string | null,
): SlateWeek {
  return {
    season,
    week: weekNumber,
    games: 90,
    projections: 4000,
    players: 1200,
    firstKickoff: first,
    lastKickoff: last,
  };
}

// The two weeks that were live in production when this was written, plus the
// 2025 archive that sits behind them in the same list.
const ARCHIVE = week(2025, 16, "2025-12-13T17:00:00Z", "2025-12-13T23:00:00Z");
const W1 = week(2026, 1, "2026-08-29T16:00:00Z", "2026-09-07T03:00:00Z");
const W2 = week(2026, 2, "2026-09-10T23:00:00Z", "2026-09-13T03:00:00Z");
const SLATE = [ARCHIVE, W1, W2];

test("before kickoff the board opens on the opening weekend, not the furthest week", () => {
  // 2026-08-11, the day this broke. The old rule returned W2 because it was
  // last in the list, which is how a board three weeks out became the landing
  // page eighteen days before the season started.
  const chosen = defaultWeek(SLATE, new Date("2026-08-11T12:00:00Z"));
  assert.equal(chosen?.week, 1);
});

test("the season's LAST week is never the default just because it is last", () => {
  // The Tuesday cron runs `--all-weeks`, so by September every remaining week
  // exists. Under the old rule the board would have opened on week 16 in
  // December, every day, for the whole season.
  const full = [
    ARCHIVE,
    W1,
    W2,
    week(2026, 15, "2026-12-05T17:00:00Z", "2026-12-06T04:00:00Z"),
    week(2026, 16, "2026-12-13T17:00:00Z", "2026-12-13T23:00:00Z"),
  ];
  assert.equal(defaultWeek(full, new Date("2026-08-11T12:00:00Z"))?.week, 1);
});

test("a week stays current until its OWN last game has kicked", () => {
  // Saturday afternoon of week 1, with games still to play. Keying on the
  // FIRST kickoff would have flipped the board to week 2 days earlier, in the
  // middle of the slate the reader is actually watching.
  const midWeek = defaultWeek(SLATE, new Date("2026-09-05T20:00:00Z"));
  assert.equal(midWeek?.week, 1);
});

test("once a week is fully played the board moves on", () => {
  const after = defaultWeek(SLATE, new Date("2026-09-08T12:00:00Z"));
  assert.equal(after?.week, 2);
});

test("in the off-season it falls back to the most recent slate", () => {
  // The behaviour the original rule was written to protect, and the reason the
  // fallback is not simply "the first week in the list". Everything is in the
  // past here; an empty board would be literally correct and useless.
  const past = defaultWeek([ARCHIVE], new Date("2026-08-11T12:00:00Z"));
  assert.equal(past?.season, 2025);
  assert.equal(past?.week, 16);
});

test("a week with no kickoff time is never chosen as the upcoming one", () => {
  // `start_time_tbd` is a real state in the schedule feed, so an unparseable
  // or missing kickoff is a case rather than a defect. It must not win the
  // comparison, and it must not crash it.
  const tbd = week(2026, 3, null, null);
  const chosen = defaultWeek([W1, tbd, W2], new Date("2026-08-11T12:00:00Z"));
  assert.equal(chosen?.week, 1);

  const garbage = week(2026, 4, "not a date", "not a date");
  assert.equal(
    defaultWeek([garbage, W2], new Date("2026-08-11T12:00:00Z"))?.week,
    2,
  );
});

test("an empty slate has no default", () => {
  assert.equal(defaultWeek([], new Date("2026-08-11T12:00:00Z")), null);
});

test("an explicit week is honoured even when it is not the upcoming one", () => {
  // The whole archive has to stay reachable by URL; that is what the week strip
  // and every shared link depend on.
  const chosen = findWeek(SLATE, 2025, 16, new Date("2026-08-11T12:00:00Z"));
  assert.equal(chosen?.season, 2025);
});

test("an unknown week falls back to the default rather than 404ing", () => {
  const chosen = findWeek(SLATE, 2026, 99, new Date("2026-08-11T12:00:00Z"));
  assert.equal(chosen?.week, 1);
});
