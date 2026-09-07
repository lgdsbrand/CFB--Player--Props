/**
 * Tests for the "has it kicked off yet" rule.
 *
 * THE TWO THAT MATTER are the null kickoff and the game in progress. A TBD
 * kickoff must stay on the board — hiding it would drop a real game on the
 * strength of a missing field — and a game that started an hour ago must be
 * gone, which is the case `completed` gets wrong for up to a day.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  hasKickedOff,
  kickoffCutoff,
  playedCount,
  upcomingGames,
} from "./kickoff.ts";

const NOW = new Date("2026-08-31T15:00:00Z");

function game(gameId: number, startDate: string | null) {
  return { gameId, startDate };
}

test("a kickoff in the past has kicked off", () => {
  assert.equal(hasKickedOff("2026-08-29T16:00:00Z", NOW), true);
});

test("a kickoff in the future has not", () => {
  assert.equal(hasKickedOff("2026-09-05T16:00:00Z", NOW), false);
});

test("a game in progress counts as kicked off", () => {
  // Kicked an hour ago and nowhere near finished, so `completed` is still
  // false. The pre-game number stopped being actionable at kickoff regardless.
  assert.equal(hasKickedOff("2026-08-31T14:00:00Z", NOW), true);
});

test("kickoff is inclusive — the moment it starts, it is gone", () => {
  assert.equal(hasKickedOff("2026-08-31T15:00:00Z", NOW), true);
});

test("a TBD kickoff is upcoming, not hidden", () => {
  // The failure this prevents is silent: a null start_date is an early-week
  // schedule state, not evidence the game was played.
  assert.equal(hasKickedOff(null, NOW), false);
});

test("an unparseable kickoff is shown rather than dropped", () => {
  assert.equal(hasKickedOff("not a date", NOW), false);
});

test("upcomingGames drops the played ones and keeps the order", () => {
  const games = [
    game(1, "2026-08-29T16:00:00Z"),
    game(2, "2026-09-05T16:00:00Z"),
    game(3, null),
    game(4, "2026-08-30T02:00:00Z"),
    game(5, "2026-09-07T23:00:00Z"),
  ];

  assert.deepEqual(
    upcomingGames(games, NOW).map((g) => g.gameId),
    [2, 3, 5],
  );
  assert.equal(playedCount(games, NOW), 2);
});

test("opening weekend drops off the 2026 week 1 slate", () => {
  // The real shape of the complaint: week 1 spans ten days, and on 31 Aug the
  // eight opening-Saturday games are settled while the rest of the week is not.
  const opening = Array.from({ length: 8 }, (_, i) =>
    game(i + 1, "2026-08-29T20:00:00Z"),
  );
  const rest = Array.from({ length: 91 }, (_, i) =>
    game(100 + i, "2026-09-05T16:00:00Z"),
  );

  const games = [...opening, ...rest];
  assert.equal(upcomingGames(games, NOW).length, 91);
  assert.equal(playedCount(games, NOW), 8);
});

// -----------------------------------------------------------------------------
// The cutoff — the start of the slate day, not "now"
// -----------------------------------------------------------------------------

test("the cutoff is 04:00 Eastern on the day being played", () => {
  // Sunday 14:00 EDT. 04:00 EDT is 08:00 UTC.
  const cutoff = kickoffCutoff(new Date("2026-09-06T18:00:00Z"));
  assert.equal(cutoff.toISOString(), "2026-09-06T08:00:00.000Z");
});

test("before 04:00 Eastern the slate is still yesterday's", () => {
  // Sunday 01:00 EDT — the tail of Saturday night's games.
  const cutoff = kickoffCutoff(new Date("2026-09-06T05:00:00Z"));
  assert.equal(cutoff.toISOString(), "2026-09-05T08:00:00.000Z");
});

test("the cutoff follows the DST shift rather than a fixed offset", () => {
  // Mid-November is EST, so 04:00 local is 09:00 UTC, not 08:00. The season
  // crosses this boundary, so a hardcoded -4 would be wrong for a third of it.
  const cutoff = kickoffCutoff(new Date("2026-11-15T18:00:00Z"));
  assert.equal(cutoff.toISOString(), "2026-11-15T09:00:00.000Z");
});

test("a game still being played at 1am is not dropped from the board", () => {
  // THE CASE THE 04:00 ROLLOVER EXISTS FOR. UNLV at Hawai'i kicked
  // 2026-09-06T02:00:00Z — Friday 22:00 Eastern — and runs past midnight. At
  // 01:00 Eastern it is still in progress, and a midnight rollover would have
  // hidden it mid-game, which is the exact complaint this change answers.
  const duringTheGame = new Date("2026-09-06T05:00:00Z");
  const kickoff = "2026-09-06T02:00:00Z";

  assert.equal(hasKickedOff(kickoff, kickoffCutoff(duringTheGame)), false);

  // And it does drop once the slate genuinely rolls over.
  const nextAfternoon = new Date("2026-09-06T18:00:00Z");
  assert.equal(hasKickedOff(kickoff, kickoffCutoff(nextAfternoon)), true);
});

test("yesterday's games drop but today's started ones stay", () => {
  // Saturday 20:00 EDT: the noon kickoff is over, the evening one is running,
  // Friday night's is gone.
  const saturdayEvening = new Date("2026-09-06T00:00:00Z");
  const cutoff = kickoffCutoff(saturdayEvening);

  const games = [
    game(1, "2026-09-04T23:00:00Z"), // Friday night — earlier slate day
    game(2, "2026-09-05T16:00:00Z"), // Saturday noon — played, kept
    game(3, "2026-09-05T23:30:00Z"), // Saturday night — still to come
    game(4, null), // TBD — never hidden
  ];

  assert.deepEqual(
    upcomingGames(games, cutoff).map((g) => g.gameId),
    [2, 3, 4],
  );
  assert.equal(playedCount(games, cutoff), 1);
});
