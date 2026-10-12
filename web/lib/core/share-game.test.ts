import assert from "node:assert/strict";
import { test } from "node:test";

import type { BookOdds, GameProjection } from "./game-lines.ts";
import type { RulePlay } from "./rule-plays.ts";
import { formatShareKickoff, shareBooks, shareGameTitle, shareGameView, shareWeather, weatherEmoji } from "./share-game.ts";
import type { GameConditions, GameSummary } from "./types.ts";

// The client's own example, 2026-10-12: Southern Miss @ Troy, book Troy -10.
function game(overrides: Partial<GameSummary> = {}): GameSummary {
  return {
    gameId: 6870,
    sport: "cfb",
    season: 2026,
    week: 6,
    startDate: "2026-10-07T00:00:00Z",
    startTimeTbd: false,
    neutralSite: false,
    completed: false,
    homePoints: null,
    awayPoints: null,
    homeTeamId: 2653,
    homeSchool: "Troy",
    homeAbbreviation: "TROY",
    homeColor: "#8a2432",
    homeAltColor: "#ffffff",
    awayTeamId: 2572,
    awaySchool: "Southern Miss",
    awayAbbreviation: "USM",
    awayColor: "#ffaa3c",
    awayAltColor: "#000000",
    venueName: null,
    venueCity: null,
    venueState: null,
    homeSpread: -10,
    gameTotal: 51.5,
    gameLineProviders: 3,
    homePollRank: null,
    awayPollRank: null,
    projections: 40,
    players: 20,
    calls: 10,
    ...overrides,
  };
}

function projection(overrides: Partial<GameProjection> = {}): GameProjection {
  const range = (mean: number) => ({ mean, p10: mean - 15, p90: mean + 15 });
  return {
    gameId: 6870,
    modelVersion: "ratings-v1",
    evidencePhase: "later",
    pHomeWin: 0.65,
    madeAt: "2026-10-06T16:30:00Z",
    periods: {
      full: { margin: range(6), total: range(48.4) },
      h1: { margin: range(3), total: range(24) },
      q1: { margin: range(1), total: range(11) },
    },
    spread: { bookKey: "pinnacle", bookName: "Pinnacle", line: -10, firstPrice: -101, secondPrice: 100, modelFirstProb: 0.47 },
    total: { bookKey: "pinnacle", bookName: "Pinnacle", line: 51, firstPrice: -116, secondPrice: 111, modelFirstProb: 0.48 },
    missingPriorSeason: false,
    ...overrides,
  };
}

function odds(key: string, name: string, market: BookOdds["market"], values: Partial<BookOdds>): BookOdds {
  return {
    period: "full",
    sportsbookKey: key,
    sportsbookName: name,
    role: "retail",
    market,
    line: null,
    homePrice: null,
    awayPrice: null,
    overPrice: null,
    underPrice: null,
    firstLine: null,
    capturedAt: "2026-10-06T16:00:00Z",
    firstCapturedAt: "2026-10-05T16:00:00Z",
    ...values,
  };
}

function conditions(overrides: Partial<GameConditions> = {}): GameConditions {
  return {
    gameId: 6870,
    venueName: null,
    venueIsDome: false,
    completed: false,
    source: "open_meteo",
    isForecast: true,
    observedAt: null,
    temperatureF: 72.4,
    dewPointF: null,
    humidity: null,
    precipitationIn: 0,
    snowfallIn: 0,
    windSpeedMph: 8,
    windDirectionDeg: null,
    pressureMb: null,
    condition: "Clear",
    ...overrides,
  };
}

function view(overrides: Partial<Parameters<typeof shareGameView>[0]> = {}) {
  return shareGameView({
    game: game(),
    kickoff: "Tue 10/6, 8:00 PM ET",
    projection: projection(),
    plays: [],
    odds: [],
    conditions: null,
    ...overrides,
  });
}

test("projected score, our lines against the book's, and the side our number points to", () => {
  const v = view();
  assert.equal(v.away.projected, "21.2");
  assert.equal(v.home.projected, "27.2");
  assert.equal(v.winChance, "TROY 65%");
  const [spread, total] = v.lines;
  assert.deepEqual(
    { book: spread.book, ours: spread.ours, lean: spread.lean, bookName: spread.bookName },
    { book: "TROY -10.0", ours: "TROY -6.0", lean: "USM +10.0", bookName: "Pinnacle" },
  );
  assert.equal(total.lean, "Under 51.0");
  assert.equal(total.ours, "48.4");
  assert.match(spread.edge ?? "", /%$/);
});

test("within a point of the line is a pass, with no edge printed", () => {
  const v = view({ projection: projection({ periods: { ...projection().periods, full: { margin: { mean: 9.6, p10: 0, p90: 20 }, total: { mean: 51.4, p10: 40, p90: 60 } } } }) });
  assert.equal(v.lines[0].lean, "Pass");
  assert.equal(v.lines[0].edge, null);
  assert.equal(v.lines[1].lean, "Pass");
});

test("a team new to FBS shows the book only: no model numbers anywhere", () => {
  const v = view({ projection: projection({ missingPriorSeason: true }) });
  assert.equal(v.home.projected, null);
  assert.equal(v.winChance, null);
  assert.equal(v.lines[0].ours, null);
  assert.equal(v.lines[0].lean, null);
  assert.equal(v.lines[0].book, "TROY -10.0");
  assert.equal(v.lines[1].book, "51.5");
});

test("no projection falls back to the consensus line", () => {
  const v = view({ projection: null });
  assert.equal(v.lines[0].book, "TROY -10.0");
  assert.equal(v.lines[0].ours, null);
});

test("rule plays of THIS game only, with the 7+ tier and the result once final", () => {
  const base = {
    id: 1, gameId: 6870, market: "totals", side: "under", line: 51, price: -110, modelProb: 0.55,
    rawProb: null, edge: 0.03, modelLine: 43.5, gap: 7.5, sportsbookName: "Pinnacle", season: 2026,
    week: 6, startDate: "2026-10-07T00:00:00Z", neutralSite: false, homePoints: null, awayPoints: null,
    final: false, home: "TROY", away: "USM", homeSchool: "Troy", awaySchool: "Southern Miss",
    homeColor: null, homeAltColor: null, awayColor: null, awayAltColor: null,
  } satisfies RulePlay;
  const other = { ...base, id: 2, gameId: 1 };
  let v = view({ plays: [base, other] });
  assert.equal(v.plays.length, 1);
  assert.equal(v.plays[0].label, "Under 51.0");
  assert.equal(v.plays[0].topTier, true);
  assert.equal(v.plays[0].result, null);
  assert.equal(v.plays[0].reason, "At lock: ours 43.5 · 7.5 pts off");

  v = view({ plays: [{ ...base, final: true, homePoints: 27, awayPoints: 14 }] });
  assert.equal(v.plays[0].result, "Won");
});

test("final scores print once the game is over", () => {
  const v = view({ game: game({ completed: true, homePoints: 31, awayPoints: 24 }) });
  assert.equal(v.home.final, 31);
  assert.equal(v.away.final, 24);
  assert.equal(v.completed, true);
});

test("books in the share order, at most three, favourite's spread price", () => {
  const rows = [
    odds("bovada", "Bovada", "spreads", { line: -10, homePrice: -110, awayPrice: -110 }),
    odds("fanduel", "FanDuel", "spreads", { line: -10.5, homePrice: -105, awayPrice: -115 }),
    odds("draftkings", "DraftKings", "spreads", { line: 3, homePrice: -110, awayPrice: -108 }),
    odds("draftkings", "DraftKings", "totals", { line: 51.5, overPrice: -112, underPrice: -108 }),
    odds("draftkings", "DraftKings", "h2h", { homePrice: 130, awayPrice: -150 }),
    odds("betmgm", "BetMGM", "totals", { line: 52 }),
    odds("pinnacle", "Pinnacle", "h2h", { homePrice: -400, awayPrice: 320 }),
    odds("draftkings", "DraftKings", "spreads", { line: -3, period: "h1", homePrice: -110, awayPrice: -110 }),
  ];
  const books = shareBooks(rows, "TROY", "USM");
  assert.deepEqual(books.map((b) => b.name), ["DraftKings", "FanDuel", "BetMGM"]);
  assert.equal(books[0].spread, "USM -3.0 (-108)");
  assert.equal(books[0].total, "51.5 (o-112/u-108)");
  assert.equal(books[0].moneyline, "USM -150 · TROY +130");
  assert.equal(books[1].spread, "TROY -10.5 (-105)");
  assert.equal(books[2].spread, null);
});

test("weather with emoji; wind gets one only when it matters; domes say indoors", () => {
  assert.equal(shareWeather(null), null);
  assert.equal(shareWeather(conditions()), "☀️ Clear · 72° · 8 mph");
  assert.equal(
    shareWeather(conditions({ condition: "Rain Shower", windSpeedMph: 18.2, temperatureF: 51 })),
    "🌧️ Rain Shower · 51° · 💨 18 mph",
  );
  assert.equal(shareWeather(conditions({ venueIsDome: true })), "🏟️ Indoors");
  assert.equal(weatherEmoji("Heavy Snowfall"), "🌨️");
  assert.equal(weatherEmoji("Partly cloudy"), "⛅");
  assert.equal(weatherEmoji("Overcast"), "☁️");
  assert.equal(weatherEmoji("Mainly clear"), "☀️");
});

test("file name is the matchup", () => {
  assert.equal(view().fileName, "usm-at-troy-week-6.png");
  assert.equal(shareGameTitle(game()), "Southern Miss @ Troy · Week 6");
});

test("kickoff carries the date and the Eastern time", () => {
  assert.equal(formatShareKickoff("2026-10-07T00:00:00Z"), "Tue 10/6, 8:00 PM ET");
  assert.equal(formatShareKickoff("2026-10-17T16:00:00Z", true), "Sat 10/17, time TBD");
  assert.equal(formatShareKickoff(null), "Time TBD");
});
