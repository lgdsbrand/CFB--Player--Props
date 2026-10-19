import assert from "node:assert/strict";
import { test } from "node:test";

import type { BookOdds } from "./game-lines.ts";
import {
  chooseBook,
  moneylineEdge,
  pointsEdge,
  teamLineup,
  type FantasyProjection,
  withBookLines,
  type LineupEntry,
} from "./fantasy-view.ts";

const projection: FantasyProjection = {
  version: "fixed",
  awayScore: 27.46,
  homeScore: 30.72,
  total: 58.18,
  homeMargin: 3.3,
  pHomeWin: 0.68114,
  awayFantasy: 76.5,
  homeFantasy: 100.5,
  inputsAsOfWeek: 6,
  madeAt: "2026-10-14T15:40:00Z",
};

function quote(overrides: Partial<BookOdds>): BookOdds {
  return {
    period: "full",
    sportsbookKey: "pinnacle",
    sportsbookName: "Pinnacle",
    role: "sharp",
    market: "spreads",
    line: -2.5,
    homePrice: -110,
    awayPrice: -110,
    overPrice: null,
    underPrice: null,
    firstLine: null,
    capturedAt: "2026-10-14T15:10:00Z",
    firstCapturedAt: "2026-10-14T15:10:00Z",
    ...overrides,
  };
}

test("the book is Pinnacle, then DraftKings, then FanDuel, skipping a non-price", () => {
  const odds = [
    quote({ sportsbookKey: "fanduel", sportsbookName: "FanDuel" }),
    quote({ sportsbookKey: "draftkings", sportsbookName: "DraftKings" }),
    quote({ sportsbookKey: "betmgm", sportsbookName: "BetMGM" }),
  ];
  assert.equal(chooseBook(odds, "spreads")?.sportsbookKey, "draftkings");
  // FanDuel's -100000 both sides when it pulls a market is not a price.
  const pulled = [
    quote({ sportsbookKey: "draftkings", homePrice: -100000, awayPrice: -100000 }),
    quote({ sportsbookKey: "fanduel", sportsbookName: "FanDuel" }),
  ];
  assert.equal(chooseBook(pulled, "spreads")?.sportsbookKey, "fanduel");
  assert.equal(chooseBook([quote({ period: "h1" })], "spreads"), null);
});

test("an NFL card takes the chosen book's spread and total, or none", () => {
  const game = { gameId: 1, homeSpread: null, gameTotal: null };
  const odds = [
    quote({ sportsbookKey: "fanduel", line: -3 }),
    quote({ sportsbookKey: "pinnacle", line: -2.5 }),
    quote({
      sportsbookKey: "draftkings",
      market: "totals",
      line: 44.5,
      homePrice: null,
      awayPrice: null,
      overPrice: -110,
      underPrice: -110,
    }),
  ];
  assert.deepEqual(withBookLines(game, odds), { gameId: 1, homeSpread: -2.5, gameTotal: 44.5 });
  assert.deepEqual(withBookLines(game, []), { gameId: 1, homeSpread: null, gameTotal: null });
});

test("the spread edge is in points, on the model's side, and a play at 4+", () => {
  // Model: home by 3.3, so home -3.3. Book home +1.5: 4.8 points apart, home side.
  const edge = pointsEdge(projection, quote({ line: 1.5, homePrice: -105, awayPrice: -115 }), "spreads");
  assert.deepEqual(edge && { ...edge }, {
    modelLine: -3.3,
    bookLine: 1.5,
    gap: 4.8,
    side: "first",
    price: -105,
    isPlay: true,
    bookName: "Pinnacle",
  });
  const close = pointsEdge(projection, quote({ line: -2.5 }), "spreads");
  assert.equal(close?.gap, 0.8);
  assert.equal(close?.side, "first"); // home -3.3 is a bigger favourite than -2.5
  assert.equal(close?.isPlay, false);
});

test("the total edge needs 6 points", () => {
  const totals = (line: number) =>
    quote({ market: "totals", line, homePrice: null, awayPrice: null, overPrice: -110, underPrice: -110 });
  assert.equal(pointsEdge(projection, totals(52.5), "totals")?.isPlay, false); // 5.7
  const over = pointsEdge(projection, totals(52.0), "totals");
  assert.equal(over?.isPlay, true);
  assert.equal(over?.side, "first");
  assert.equal(pointsEdge(projection, null, "totals"), null);
});

test("the moneyline edge is his raw win % minus the book's vig-free price", () => {
  const edge = moneylineEdge(projection, quote({ market: "h2h", line: null, homePrice: -150, awayPrice: 130 }));
  assert.ok(edge);
  assert.equal(edge.side, "home");
  assert.equal(edge.price, -150);
  const book = 0.6 / (0.6 + 100 / 230);
  assert.ok(Math.abs(edge.bookProb - book) < 1e-9);
  assert.ok(Math.abs(edge.edge - (0.68114 - book)) < 1e-9);
  const away = moneylineEdge({ ...projection, pHomeWin: 0.3 }, quote({ market: "h2h", line: null }));
  assert.equal(away?.side, "away");
  assert.ok(Math.abs((away?.modelProb ?? 0) - 0.7) < 1e-9);
});

test("a lineup is in slot order and sums like his sheet", () => {
  const entry = (slot: LineupEntry["slot"], points: number, teamId = 1): LineupEntry => ({
    teamId,
    slot,
    playerName: slot,
    fantasyPoints: points,
    pointsSource: "cbs",
    pickedBy: "projection",
    skipped: null,
  });
  const lineup = teamLineup(
    [entry("TE", 9.1), entry("QB", 22.4), entry("WR2", 11.0), entry("RB", 15.2), entry("WR1", 18.8), entry("QB", 30, 2)],
    1,
  );
  assert.deepEqual(lineup.slots.map((e) => e.slot), ["QB", "RB", "WR1", "WR2", "TE"]);
  assert.equal(lineup.total, 76.5);
});
