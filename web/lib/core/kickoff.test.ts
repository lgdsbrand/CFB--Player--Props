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

test("the cutoff is rounded down to the minute", () => {
  const cutoff = kickoffCutoff(new Date("2026-08-31T15:07:42.913Z"));
  assert.equal(cutoff.toISOString(), "2026-08-31T15:07:00.000Z");
});

test("the rounded cutoff still hides a game that kicked earlier this minute", () => {
  // Rounding DOWN is the safe direction: it can only ever keep a game one
  // minute longer, never hide one that has not started.
  const now = new Date("2026-08-31T15:07:42Z");
  assert.equal(hasKickedOff("2026-08-31T15:07:10Z", kickoffCutoff(now)), false);
  assert.equal(hasKickedOff("2026-08-31T15:06:00Z", kickoffCutoff(now)), true);
});
