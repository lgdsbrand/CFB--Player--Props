/**
 * Tests for picking which of a player's games the page shows.
 *
 * THE FIXTURE IS THE REAL CASE. Tychaun Chapman of Memphis is projected in two
 * 2026 week-1 games on production — at UNLV on 30 Aug and against Arkansas
 * State on 5 Sep — because CFBD labels both `week: 1`. Seventy-one players on
 * that week look like this, and sixty-six did on 2025 week 1.
 *
 * The bug this guards against was not a crash: the page rendered duplicate
 * market tabs and then described the WRONG game in its header, defense panel
 * and conditions panel, while looking entirely healthy.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  defaultGameId,
  orderedGames,
  resolveGameId,
  rowsForGame,
} from "./player-games.ts";
import type { BoardRow } from "./types.ts";

function row(overrides: Partial<BoardRow> & { gameId: number }): BoardRow {
  return {
    projectionId: overrides.gameId * 100,
    pickId: null,
    season: 2026,
    week: 1,
    marketKey: "rec_yards",
    marketName: "Receiving yards",
    marketLabel: null,
    marketEmoji: null,
    isBinary: false,
    playerId: 3205,
    playerName: "Tychaun Chapman",
    positionGroup: "WR",
    teamId: 1,
    teamSchool: "Memphis",
    teamAbbreviation: "MEM",
    teamColor: null,
    teamAltColor: null,
    opponentTeamId: 2,
    opponentSchool: "UNLV",
    opponentAbbreviation: "UNLV",
    startDate: "2026-08-30T02:00:00+00:00",
    neutralSite: false,
    isHome: false,
    line: null,
    side: null,
    confidence: null,
    modelProbOver: null,
    bookProbOver: null,
    edge: null,
    hasBookLine: false,
    hasCall: false,
    overPrice: null,
    underPrice: null,
    sportsbookKey: null,
    sportsbookName: null,
    projectedMedian: null,
    projectedP10: null,
    projectedP90: null,
    priorWeight: null,
    effectiveSample: null,
    ladder: null,
    conferenceName: null,
    venueName: null,
    venueCity: null,
    venueState: null,
    teamSpread: null,
    gameTotal: null,
    ...overrides,
  } as BoardRow;
}

const AT_UNLV = { gameId: 6494, startDate: "2026-08-30T02:00:00+00:00" };
const VS_ARKST = {
  gameId: 6552,
  startDate: "2026-09-05T23:00:00+00:00",
  opponentSchool: "Arkansas State",
  opponentAbbreviation: "ARST",
  isHome: true,
};

const BOTH = [
  row({ ...AT_UNLV, marketKey: "rec_yards" }),
  row({ ...AT_UNLV, marketKey: "receptions" }),
  row({ ...VS_ARKST, marketKey: "rec_yards" }),
  row({ ...VS_ARKST, marketKey: "receptions" }),
];

test("the two games are found once each, in kickoff order", () => {
  const games = orderedGames(BOTH);
  assert.equal(games.length, 2);
  assert.deepEqual(
    games.map((g) => g.gameId),
    [6494, 6552],
  );
  assert.equal(games[1].opponentSchool, "Arkansas State");
  assert.equal(games[1].isHome, true);
});

test("a player with one game yields one game", () => {
  assert.equal(orderedGames([row(AT_UNLV)]).length, 1);
  assert.equal(orderedGames([]).length, 0);
});

test("the page opens on the game that has not been played", () => {
  const games = orderedGames(BOTH);
  // Between the two kickoffs: the first is history, the second is next.
  assert.equal(defaultGameId(games, new Date("2026-09-01T00:00:00Z")), 6552);
  // Before both: the earlier one is next.
  assert.equal(defaultGameId(games, new Date("2026-08-25T00:00:00Z")), 6494);
});

test("once both are played it settles on the later one", () => {
  // Not the earlier one — that would strand the reader on a nine-day-old game
  // for the rest of the week.
  const games = orderedGames(BOTH);
  assert.equal(defaultGameId(games, new Date("2026-09-10T00:00:00Z")), 6552);
});

test("a game with no kickoff time sorts last, not first", () => {
  // `start_time_tbd` means unscheduled, which is the LAST thing to happen. A
  // null-first sort would open the page on an undated game over one today.
  const games = orderedGames([
    row({ gameId: 900, startDate: null }),
    row(AT_UNLV),
  ]);
  assert.deepEqual(
    games.map((g) => g.gameId),
    [6494, 900],
  );
  assert.equal(defaultGameId(games, new Date("2026-09-01T00:00:00Z")), 900);
});

test("no games yields no id rather than throwing", () => {
  assert.equal(defaultGameId([], new Date()), null);
});

test("a requested game is honoured only if the player has it", () => {
  const games = orderedGames(BOTH);
  const now = new Date("2026-08-25T00:00:00Z");
  assert.equal(resolveGameId(games, 6552, now), 6552);
  // A stale or hand-edited URL falls back rather than rendering an empty page.
  assert.equal(resolveGameId(games, 999999, now), 6494);
  assert.equal(resolveGameId(games, undefined, now), 6494);
});

test("rows are scoped to the chosen game", () => {
  const scoped = rowsForGame(BOTH, 6552);
  assert.equal(scoped.length, 2);
  assert.ok(scoped.every((r) => r.gameId === 6552));
  // The duplicate-tab bug in one assertion: unscoped, the same market appears
  // twice.
  assert.equal(BOTH.filter((r) => r.marketKey === "rec_yards").length, 2);
  assert.equal(scoped.filter((r) => r.marketKey === "rec_yards").length, 1);
});
