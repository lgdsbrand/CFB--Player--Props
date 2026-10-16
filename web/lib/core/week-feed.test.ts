import assert from "node:assert/strict";
import { test } from "node:test";

import type { LiveScore } from "./live.ts";
import type { GameSummary } from "./types.ts";
import { espnLogo, feedGame, type TeamKeys } from "./week-feed.ts";

function game(overrides: Partial<GameSummary> = {}): GameSummary {
  return {
    gameId: 7,
    season: 2026,
    week: 7,
    startDate: "2026-10-17T19:30:00+00:00",
    startTimeTbd: false,
    neutralSite: false,
    completed: false,
    homePoints: null,
    awayPoints: null,
    homeTeamId: 1,
    homeSchool: "Georgia",
    homeAbbreviation: "UGA",
    awayTeamId: 2,
    awaySchool: "Auburn",
    awayAbbreviation: "AUB",
    homePollRank: 1,
    awayPollRank: null,
    ...overrides,
  } as GameSummary;
}

const BEFORE = new Date("2026-10-17T12:00:00Z");

const teams: TeamKeys = new Map([
  [1, { cfbdId: 61, abbreviation: "UGA" }],
  [2, { cfbdId: 2, abbreviation: "AUB" }],
]);

function live(overrides: Partial<LiveScore>): LiveScore {
  return {
    gameId: 7, status: "in_progress", period: 3, clock: "4:12", homePoints: 21, awayPoints: 10,
    homeLineScores: null, awayLineScores: null, possession: null, situation: null,
    lastPlay: null, updatedAt: "2026-10-17T21:00:00Z", ...overrides,
  };
}

test("a college logo is ESPN's by CFBD id, an NFL one by code, with ESPN's two odd codes", () => {
  assert.equal(espnLogo("cfb", { cfbdId: 61, code: "UGA" }), "https://a.espncdn.com/i/teamlogos/ncaa/500/61.png");
  assert.equal(espnLogo("cfb", { cfbdId: null, code: "XYZ" }), null);
  assert.equal(espnLogo("nfl", { cfbdId: null, code: "KC" }), "https://a.espncdn.com/i/teamlogos/nfl/500/kc.png");
  assert.equal(espnLogo("nfl", { cfbdId: null, code: "LA" }), "https://a.espncdn.com/i/teamlogos/nfl/500/lar.png");
  assert.equal(espnLogo("nfl", { cfbdId: null, code: "WAS" }), "https://a.espncdn.com/i/teamlogos/nfl/500/wsh.png");
});

test("before kickoff: time, ranks and logos, no score, a link to our game page", () => {
  const g = feedGame("cfb", game(), undefined, teams, BEFORE);
  assert.equal(g.status, "scheduled");
  assert.equal(g.state, null);
  assert.equal(g.kickoff, "2026-10-17T19:30:00+00:00");
  assert.deepEqual(g.home, { code: "UGA", rank: 1, logo: "https://a.espncdn.com/i/teamlogos/ncaa/500/61.png", score: null });
  assert.equal(g.away.rank, null);
  assert.equal(g.href, "/football/games/7");
  assert.equal(feedGame("nfl", game(), undefined, teams, BEFORE).href, "/football/games/7?sport=nfl");
});

test("while the game is on, the live row carries the score and the clock", () => {
  const g = feedGame("cfb", game(), live({}), teams);
  assert.equal(g.status, "live");
  assert.equal(g.state, "3rd 4:12");
  assert.equal(g.home.score, 21);
  assert.equal(g.away.score, 10);
});

test("a final from the official load needs no live row", () => {
  const g = feedGame("cfb", game({ completed: true, homePoints: 31, awayPoints: 17 }), undefined, teams);
  assert.equal(g.status, "final");
  assert.equal(g.state, "Final");
  assert.equal(g.home.score, 31);
});

test("a TBD kickoff is sent as no time rather than a guessed one", () => {
  assert.equal(feedGame("cfb", game({ startTimeTbd: true }), undefined, teams).kickoff, null);
});

test("past kickoff with no live row and no final yet: started, no score", () => {
  const g = feedGame("nfl", game(), undefined, teams, new Date("2026-10-17T21:00:00Z"));
  assert.equal(g.status, "started");
  assert.equal(g.state, "Started");
  assert.equal(g.home.score, null);
});
