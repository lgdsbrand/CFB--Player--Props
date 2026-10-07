/**
 * The bet slip (client, 2026-10-06): legs a reader picks from the board, the
 * game page and the +EV lists, each book's current price for them, and a
 * combined parlay price per book.
 *
 * SPORT-AGNOSTIC CORE. Pure: legs and offer rows in, labels and prices out.
 * The offers come from `prop_offers` and `game_offers` (migration 0089),
 * which every odds capture replaces.
 *
 * NO BET IS PLACED (CLAUDE.md §10). The slip links out to each book's own
 * bet slip, one bet per link. Links that add several legs at once exist for
 * some books but are unverified from here, so none are built.
 */

import { formatLine } from "./format.ts";
import {
  spreadSideLabel,
  totalSideLabel,
  type MarketRole,
  type ModelPeriod,
  type OddsMarket,
} from "./game-lines.ts";
import type { EvWager } from "./ev.ts";
import type { BoardRow } from "./types.ts";

export interface PropLeg {
  kind: "prop";
  gameId: number;
  playerId: number;
  marketKey: string;
  side: "over" | "under";
  line: number;
  /** A yes/no market (anytime TD): Over 0.5 is "Yes". */
  binary: boolean;
  player: string;
  market: string;
  matchup: string;
  startDate: string | null;
}

export interface GameLeg {
  kind: "game";
  gameId: number;
  period: ModelPeriod;
  market: OddsMarket;
  side: "home" | "away" | "over" | "under";
  /** Home-perspective spread or the total, as stored; null for a moneyline. */
  line: number | null;
  home: string;
  away: string;
  startDate: string | null;
}

export type SlipLeg = PropLeg | GameLeg;

/** One book's current price on one leg's side. */
export interface Offer {
  bookKey: string;
  bookName: string;
  role: MarketRole;
  /** The offer's own line: the same as the leg's, or another the book posts. */
  line: number | null;
  price: number;
  link: string | null;
  eventLink: string | null;
  capturedAt: string;
}

export const MAX_LEGS = 12;

/**
 * A board row as a slip leg, on the side the board calls (Over, i.e. "Yes",
 * where it calls none). Null without a book line or once the game has started:
 * there is nothing to bet.
 */
export function propLegFromRow(
  row: Pick<
    BoardRow,
    | "gameId" | "playerId" | "marketKey" | "line" | "side" | "hasCall" | "isBinary"
    | "playerName" | "marketLabel" | "marketName" | "teamAbbreviation" | "teamSchool"
    | "opponentAbbreviation" | "opponentSchool" | "isHome" | "startDate"
    | "hasBookLine" | "hasKickedOff"
  >,
): PropLeg | null {
  if (!row.hasBookLine || row.line === null || row.hasKickedOff) return null;
  const team = row.teamAbbreviation ?? row.teamSchool;
  const opponent = row.opponentAbbreviation ?? row.opponentSchool;
  return {
    kind: "prop",
    gameId: row.gameId,
    playerId: row.playerId,
    marketKey: row.marketKey,
    side: !row.isBinary && row.hasCall && row.side ? row.side : "over",
    line: row.line,
    binary: row.isBinary,
    player: row.playerName,
    market: row.marketLabel ?? row.marketName,
    matchup: row.isHome ? `${opponent} @ ${team}` : `${team} @ ${opponent}`,
    startDate: row.startDate,
  };
}

/** A full-game +EV wager as a slip leg, at the wager's line. */
export function legFromWager(
  wager: Pick<EvWager, "gameId" | "market" | "side" | "line">,
  home: string,
  away: string,
  startDate: string | null,
): GameLeg {
  return {
    kind: "game",
    gameId: wager.gameId,
    period: "full",
    market: wager.market,
    side: wager.side,
    line: wager.line,
    home,
    away,
    startDate,
  };
}

/** Identity of a leg. A different side or line is a different leg. */
export function legKey(leg: SlipLeg): string {
  return leg.kind === "prop"
    ? `p:${leg.gameId}:${leg.playerId}:${leg.marketKey}:${leg.side}:${leg.line}`
    : `g:${leg.gameId}:${leg.period}:${leg.market}:${leg.side}:${leg.line ?? ""}`;
}

/** "Over", "Under", or for a yes/no market "Yes" / "No". */
function propSideWord(leg: PropLeg): string {
  if (leg.binary) return leg.side === "over" ? "Yes" : "No";
  return leg.side === "over" ? "Over" : "Under";
}

const PERIOD_PREFIX: Record<ModelPeriod, string> = { full: "", h1: "1H ", q1: "1Q " };

/** "Drew Allar Over 232.5 Pass Yards", "1H UGA -3.5", "Over 51.5", "UGA ML". */
export function legTitle(leg: SlipLeg): string {
  if (leg.kind === "prop") {
    if (leg.binary) return `${leg.player} ${leg.market} · ${propSideWord(leg)}`;
    return `${leg.player} ${propSideWord(leg)} ${formatLine(leg.line)} ${leg.market}`;
  }
  const prefix = PERIOD_PREFIX[leg.period];
  if (leg.market === "h2h") return `${prefix}${leg.side === "home" ? leg.home : leg.away} ML`;
  if (leg.line === null) return `${prefix}${leg.side}`;
  if (leg.market === "totals") {
    return prefix + totalSideLabel(leg.line, leg.side === "over" ? "first" : "second");
  }
  return prefix + spreadSideLabel(leg.line, leg.side === "home" ? "first" : "second", leg.home, leg.away);
}

/** "JXST @ KENN" */
export function legMatchup(leg: SlipLeg): string {
  return leg.kind === "prop" ? leg.matchup : `${leg.away} @ ${leg.home}`;
}

/** Add a leg; one on the same bet's other side or line is replaced in place. */
export function addLeg(legs: SlipLeg[], leg: SlipLeg): SlipLeg[] {
  const at = legs.findIndex((l) => betKey(l) === betKey(leg));
  if (at >= 0) return legs.map((l, i) => (i === at ? leg : l));
  return [...legs, leg].slice(-MAX_LEGS);
}

/** The bet without its side or line: one player's market, one game market. */
function betKey(leg: SlipLeg): string {
  return leg.kind === "prop"
    ? `p:${leg.gameId}:${leg.playerId}:${leg.marketKey}`
    : `g:${leg.gameId}:${leg.period}:${leg.market}`;
}

/** Legs read back from storage: anything malformed is dropped, not repaired. */
export function parseLegs(raw: unknown): SlipLeg[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is SlipLeg => {
    if (!item || typeof item !== "object") return false;
    const leg = item as Record<string, unknown>;
    if (typeof leg.gameId !== "number" || typeof leg.side !== "string") return false;
    if (leg.kind === "prop") {
      return (
        typeof leg.playerId === "number" &&
        typeof leg.marketKey === "string" &&
        typeof leg.line === "number" &&
        (leg.side === "over" || leg.side === "under")
      );
    }
    if (leg.kind === "game") {
      return (
        ["full", "h1", "q1"].includes(leg.period as string) &&
        ["h2h", "spreads", "totals"].includes(leg.market as string) &&
        ["home", "away", "over", "under"].includes(leg.side) &&
        (leg.line === null || typeof leg.line === "number")
      );
    }
    return false;
  });
}

// ---------------------------------------------------------------- prices

export function americanToDecimal(price: number): number {
  return price > 0 ? 1 + price / 100 : 1 + 100 / -price;
}

/** Rounded to the whole number books print; +100 for an even price. */
export function decimalToAmerican(decimal: number): number {
  if (decimal >= 2) return Math.round((decimal - 1) * 100);
  return -Math.round(100 / (decimal - 1));
}

const sameLine = (a: number | null, b: number | null) =>
  a === null || b === null ? a === b : Math.abs(a - b) < 1e-6;

/**
 * Books a US reader cannot use: European-region books captured for the
 * game model's sharp and exchange comparison (Pinnacle among them). The slip
 * is for placing bets, so they are left out of it.
 */
export const SLIP_HIDDEN_BOOKS: ReadonlySet<string> = new Set([
  "pinnacle",
  "betfair_ex_eu",
  "betsson",
  "coolbet",
  "everygame",
  "leovegas_se",
  "marathonbet",
  "matchbook",
  "nordicbet",
  "onexbet",
  "unibet_nl",
  "unibet_se",
  "williamhill",
]);

/**
 * Exchanges (Kalshi, Novig, ProphetX, Polymarket...) post prices before their
 * fees and take no parlays, so a sportsbook's price is never ranked below
 * one of theirs; they are listed after, labelled.
 */
export function isExchangeOffer(offer: Pick<Offer, "role">): boolean {
  return offer.role === "exchange";
}

/** A leg's offers at its own line, best first, and at other lines. */
export function splitOffers(
  leg: SlipLeg,
  offers: Offer[],
): { atLine: Offer[]; otherLines: Offer[] } {
  const usable = offers.filter((o) => !SLIP_HIDDEN_BOOKS.has(o.bookKey));
  const byPrice = (a: Offer, b: Offer) =>
    Number(isExchangeOffer(a)) - Number(isExchangeOffer(b)) ||
    americanToDecimal(b.price) - americanToDecimal(a.price) ||
    a.bookName.localeCompare(b.bookName);
  return {
    atLine: usable.filter((o) => sameLine(o.line, leg.line)).sort(byPrice),
    otherLines: usable.filter((o) => !sameLine(o.line, leg.line)).sort(byPrice),
  };
}

/** The best offer at each other line, best first, with how many books post it. */
export function otherLineChoices(otherLines: Offer[]): { offer: Offer; books: number }[] {
  const byLine = new Map<string, { offer: Offer; books: number }>();
  for (const offer of otherLines) {
    const key = String(offer.line);
    const seen = byLine.get(key);
    // `otherLines` arrives best first, so the first offer per line is its best.
    if (seen) seen.books += 1;
    else byLine.set(key, { offer, books: 1 });
  }
  return [...byLine.values()];
}

export interface BookParlay {
  bookKey: string;
  bookName: string;
  decimal: number;
  american: number;
  /** This book's offer for each leg, in leg order. */
  offers: Offer[];
}

/**
 * The combined price at each sportsbook that offers EVERY leg at its line,
 * best first. Multiplied as if the legs were independent, which is how a
 * parlay of separate games is priced; see `hasSameGameLegs`. Exchanges take
 * no parlays and are left out.
 */
export function parlayByBook(legOffers: Offer[][]): BookParlay[] {
  if (legOffers.length < 2) return [];
  const [first, ...rest] = legOffers.map((list) => list.filter((o) => !isExchangeOffer(o)));
  const out: BookParlay[] = [];
  for (const offer of first) {
    const offers = [offer];
    for (const list of rest) {
      const match = list.find((o) => o.bookKey === offer.bookKey);
      if (!match) break;
      offers.push(match);
    }
    if (offers.length !== legOffers.length) continue;
    const decimal = offers.reduce((acc, o) => acc * americanToDecimal(o.price), 1);
    out.push({
      bookKey: offer.bookKey,
      bookName: offer.bookName,
      decimal,
      american: decimalToAmerican(decimal),
      offers,
    });
  }
  return out.sort((a, b) => b.decimal - a.decimal);
}

/**
 * Two legs on one game are correlated, and a book prices a same-game parlay
 * itself, often refusing combinations, so the multiplied price is only a guide.
 */
export function hasSameGameLegs(legs: SlipLeg[]): boolean {
  const games = legs.map((l) => l.gameId);
  return new Set(games).size !== games.length;
}

// ---------------------------------------------------------------- links

/**
 * The link to open for an offer: the bet itself where the book gives one,
 * else the game's page, with `{state}` filled from the reader's state.
 *
 * A link left with any other placeholder (BetRivers' `{pickType}`, Novig's
 * `{wager}`) is not usable as sent, so the game's page is used instead. A
 * link that needs a state the reader has not chosen is skipped the same way.
 */
export function offerLink(
  offer: Pick<Offer, "link" | "eventLink">,
  state: string | null,
): { href: string; isBet: boolean } | null {
  for (const [raw, isBet] of [
    [offer.link, true],
    [offer.eventLink, false],
  ] as const) {
    if (!raw) continue;
    if (raw.includes("{state}") && !state) continue;
    const href = state ? raw.replaceAll("{state}", state.toLowerCase()) : raw;
    if (/\{[^}]*\}/.test(href)) continue;
    return { href, isBet };
  }
  return null;
}

/** Whether an offer has a link that only a chosen state would unlock. */
export function needsState(offer: Pick<Offer, "link" | "eventLink">): boolean {
  return [offer.link, offer.eventLink].some((l) => l?.includes("{state}") ?? false);
}

/** States for the books whose links name one (BetMGM, BetRivers). */
export const US_STATES: readonly (readonly [string, string])[] = [
  ["AZ", "Arizona"], ["AR", "Arkansas"], ["CO", "Colorado"], ["CT", "Connecticut"],
  ["DC", "District of Columbia"], ["DE", "Delaware"], ["IL", "Illinois"], ["IN", "Indiana"],
  ["IA", "Iowa"], ["KS", "Kansas"], ["KY", "Kentucky"], ["LA", "Louisiana"],
  ["ME", "Maine"], ["MD", "Maryland"], ["MA", "Massachusetts"], ["MI", "Michigan"],
  ["MS", "Mississippi"], ["MO", "Missouri"], ["NV", "Nevada"], ["NH", "New Hampshire"],
  ["NJ", "New Jersey"], ["NY", "New York"], ["NC", "North Carolina"], ["OH", "Ohio"],
  ["OR", "Oregon"], ["PA", "Pennsylvania"], ["RI", "Rhode Island"], ["TN", "Tennessee"],
  ["VT", "Vermont"], ["VA", "Virginia"], ["WA", "Washington"], ["WV", "West Virginia"],
  ["WI", "Wisconsin"], ["WY", "Wyoming"],
];
