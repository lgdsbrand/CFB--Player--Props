import assert from "node:assert/strict";
import { test } from "node:test";

import {
  addLeg,
  americanToDecimal,
  decimalToAmerican,
  flipLeg,
  hasSameGameLegs,
  legKey,
  legTitle,
  offerLink,
  otherLineChoices,
  parlayByBook,
  parseLegs,
  propLegFromRow,
  splitOffers,
  type GameLeg,
  type Offer,
  type PropLeg,
} from "./slip.ts";

const prop: PropLeg = {
  kind: "prop",
  gameId: 1,
  playerId: 7,
  marketKey: "pass_yards",
  side: "over",
  line: 193.5,
  binary: false,
  player: "Rickie Collins",
  market: "Pass Yards",
  matchup: "JXST @ KENN",
  startDate: "2026-10-07T23:00:00Z",
};

const spread: GameLeg = {
  kind: "game",
  gameId: 2,
  period: "full",
  market: "spreads",
  side: "away",
  line: -3.5,
  home: "UGA",
  away: "CLEM",
  startDate: null,
};

function offer(bookKey: string, price: number, extra: Partial<Offer> = {}): Offer {
  return {
    bookKey,
    bookName: bookKey,
    role: "retail",
    line: 193.5,
    price,
    link: null,
    eventLink: null,
    capturedAt: "2026-10-07T10:50:00Z",
    ...extra,
  };
}

test("titles read the way a book prints the bet", () => {
  assert.equal(legTitle(prop), "Rickie Collins Over 193.5 Pass Yards");
  // The stored line is the home team's: UGA -3.5 makes the away side CLEM +3.5.
  assert.equal(legTitle(spread), "CLEM +3.5");
  assert.equal(legTitle({ ...spread, period: "h1", market: "h2h", side: "home", line: null }), "1H UGA ML");
  assert.equal(legTitle({ ...spread, market: "totals", side: "under", line: 51.5 }), "Under 51.5");
  assert.equal(
    legTitle({ ...prop, binary: true, market: "Anytime TD", line: 0.5 }),
    "Rickie Collins Anytime TD · Yes",
  );
});

test("flipping a spread keeps the home-perspective line", () => {
  const flipped = flipLeg(spread) as GameLeg;
  assert.equal(flipped.side, "home");
  assert.equal(flipped.line, -3.5);
  assert.equal(legTitle(flipped), "UGA -3.5");
  assert.equal((flipLeg(prop) as PropLeg).side, "under");
});

test("adding the other side of a bet replaces it rather than holding both", () => {
  const legs = addLeg(addLeg([prop], spread), flipLeg(prop));
  assert.equal(legs.length, 2);
  assert.equal(legs.filter((l) => l.kind === "prop")[0].side, "under");
  assert.notEqual(legKey(prop), legKey(flipLeg(prop)));
});

test("stored legs that are malformed are dropped", () => {
  const parsed = parseLegs([prop, spread, { kind: "prop", gameId: "1" }, null, { kind: "x" }]);
  assert.deepEqual(parsed, [prop, spread]);
  assert.deepEqual(parseLegs("nope"), []);
});

test("prices convert both ways", () => {
  assert.equal(americanToDecimal(-110).toFixed(4), "1.9091");
  assert.equal(americanToDecimal(150), 2.5);
  assert.equal(decimalToAmerican(2.5), 150);
  assert.equal(decimalToAmerican(1.9091), -110);
  assert.equal(decimalToAmerican(2), 100);
});

test("offers split by line, best price first, European books left out", () => {
  const { atLine, otherLines } = splitOffers(prop, [
    offer("fanduel", -114),
    offer("betonlineag", -105),
    offer("pinnacle", +101),
    offer("betrivers", -121, { line: 195.5 }),
  ]);
  assert.deepEqual(atLine.map((o) => o.bookKey), ["betonlineag", "fanduel"]);
  assert.deepEqual(otherLines.map((o) => o.bookKey), ["betrivers"]);
});

test("a sportsbook ranks above an exchange's better price, which is before fees", () => {
  const { atLine } = splitOffers(prop, [
    offer("kalshi", +113, { role: "exchange" }),
    offer("fanduel", -110),
  ]);
  assert.deepEqual(atLine.map((o) => o.bookKey), ["fanduel", "kalshi"]);
  assert.deepEqual(
    parlayByBook([[offer("kalshi", 100, { role: "exchange" })], [offer("kalshi", 100, { role: "exchange" })]]),
    [],
  );
});

test("other lines collapse to the best offer per line", () => {
  const { otherLines } = splitOffers(prop, [
    offer("a", -110, { line: 195.5 }),
    offer("b", -105, { line: 195.5 }),
    offer("c", +100, { line: 191.5 }),
  ]);
  const choices = otherLineChoices(otherLines);
  assert.deepEqual(
    choices.map((c) => [c.offer.line, c.offer.bookKey, c.books]),
    [[191.5, "c", 1], [195.5, "b", 2]],
  );
});

test("a moneyline leg matches offers with no line", () => {
  const ml: GameLeg = { ...spread, market: "h2h", line: null };
  const { atLine } = splitOffers(ml, [offer("draftkings", 120, { line: null })]);
  assert.equal(atLine.length, 1);
});

test("a parlay is priced only at books holding every leg", () => {
  const parlays = parlayByBook([
    [offer("fanduel", -110), offer("draftkings", -105)],
    [offer("draftkings", +150), offer("betmgm", +160)],
  ]);
  assert.equal(parlays.length, 1);
  assert.equal(parlays[0].bookKey, "draftkings");
  // 1.9524 x 2.5 = 4.881 -> +388
  assert.equal(parlays[0].american, 388);
  assert.deepEqual(parlayByBook([[offer("fanduel", -110)]]), []);
});

test("same-game legs are flagged", () => {
  assert.equal(hasSameGameLegs([prop, spread]), false);
  assert.equal(hasSameGameLegs([prop, { ...spread, gameId: 1 }]), true);
});

test("a board row becomes a leg on its called side, or none without a line", () => {
  const row = {
    gameId: 1, playerId: 7, marketKey: "pass_yards", line: 193.5, side: "under" as const,
    hasCall: true, isBinary: false, playerName: "Rickie Collins", marketLabel: "Pass Yards",
    marketName: "Passing yards", teamAbbreviation: "JXST", teamSchool: "Jacksonville State",
    opponentAbbreviation: "KENN", opponentSchool: "Kennesaw State", isHome: false,
    startDate: null, hasBookLine: true, hasKickedOff: false,
  };
  const leg = propLegFromRow(row)!;
  assert.equal(leg.side, "under");
  assert.equal(leg.matchup, "JXST @ KENN");
  // Anytime TD always goes on as "Yes", whatever the stored side.
  assert.equal(propLegFromRow({ ...row, isBinary: true })!.side, "over");
  assert.equal(propLegFromRow({ ...row, hasBookLine: false }), null);
  assert.equal(propLegFromRow({ ...row, hasKickedOff: true }), null);
});

test("links: the bet first, the game page as fallback, the state filled in", () => {
  const fd = { link: "https://sportsbook.fanduel.com/addToBetslip?x=1", eventLink: "https://fd/event" };
  assert.deepEqual(offerLink(fd, null), { href: fd.link, isBet: true });

  const mgm = {
    link: "https://sports.{state}.betmgm.com/en/sports?options=6:1-2-3&type=Single",
    eventLink: "https://sports.{state}.betmgm.com/en/sports/events/x",
  };
  assert.equal(offerLink(mgm, null), null);
  assert.deepEqual(offerLink(mgm, "NJ"), {
    href: "https://sports.nj.betmgm.com/en/sports?options=6:1-2-3&type=Single",
    isBet: true,
  });

  // BetRivers' bet link carries fields nothing fills: its game page is used.
  const rivers = {
    link: "https://{state}.betrivers.com/?page=sportsbook#event/1?coupon={pickType}|9|{wagerAmount}",
    eventLink: "https://{state}.betrivers.com/?page=sportsbook#event/1",
  };
  assert.deepEqual(offerLink(rivers, "pa"), {
    href: "https://pa.betrivers.com/?page=sportsbook#event/1",
    isBet: false,
  });
  assert.equal(offerLink({ link: null, eventLink: null }, "pa"), null);
});
