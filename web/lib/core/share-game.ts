/**
 * The share graphic for one game (client, 2026-10-12: "a share button ... that
 * would come up to a graphic that we could share to social media").
 *
 * SPORT-AGNOSTIC CORE. Pure: the game, its projection, its rule plays, the
 * books' current prices and the conditions in; every string the image prints
 * out. The image route only lays these out, so what the graphic SAYS is tested
 * here and cannot drift from the game page.
 *
 * ONLY WHAT THE SITE ALREADY SHOWS IN PUBLIC. The model's fair line and its
 * calibrated edge are on the games table; rule plays are public before kickoff
 * (migration 0091). The V1/V2 shadow picks stay private until kickoff
 * (migration 0078) and are not read here, so a graphic shared on Friday cannot
 * leak them.
 *
 * Team marks are the abbreviation on team colours, as everywhere else: real
 * logos are trademarked (CLAUDE.md §7, §10).
 */

import { DISPLAY_TIME_ZONE, formatAmericanOdds, formatEdge } from "./format.ts";
import {
  formatFair,
  formatPoints,
  modelFavourite,
  modelSpreadLabel,
  projectedScore,
  spreadLabel,
  spreadSideLabel,
  tableSide,
  totalSideLabel,
  type BookOdds,
  type GameProjection,
} from "./game-lines.ts";
import { isTopTier, playLabel, playResult, reasonLabel, type RulePlay } from "./rule-plays.ts";
import type { GameConditions, GameSummary } from "./types.ts";
import { formatTemperature, formatWind, weatherView, WIND_NOTABLE_MPH } from "./weather-view.ts";

export interface ShareTeam {
  school: string;
  abbreviation: string;
  color: string | null;
  altColor: string | null;
  rank: number | null;
  /** The model's points, one decimal, or null when the model is hidden. */
  projected: string | null;
  /** Final points once the game is over. */
  final: number | null;
}

export interface ShareLine {
  label: "Spread" | "Total";
  /** The book's number: "TROY -10.0", "51.5". */
  book: string | null;
  bookName: string | null;
  /** Ours: "TROY -14.6", "48.4". */
  ours: string | null;
  /** The side our number points to, "USM +10.0" / "Under 51.5", or "Pass". */
  lean: string | null;
  /** The calibrated edge of that side, "+2.1%". Null on a pass. */
  edge: string | null;
}

export interface SharePlay {
  label: string;
  reason: string;
  topTier: boolean;
  /** "Won" / "Lost" / "Push" once the game is final, else null. */
  result: string | null;
}

export interface ShareBook {
  name: string;
  spread: string | null;
  total: string | null;
  moneyline: string | null;
}

export interface ShareGameView {
  sportLabel: string;
  weekLabel: string;
  kickoff: string;
  neutralSite: boolean;
  away: ShareTeam;
  home: ShareTeam;
  completed: boolean;
  /** "Win chance TROY 65%". */
  winChance: string | null;
  lines: ShareLine[];
  plays: SharePlay[];
  books: ShareBook[];
  weather: string | null;
  fileName: string;
}

/** The books an audience of US bettors holds accounts at, then the sharp one. */
export const SHARE_BOOK_ORDER = ["draftkings", "fanduel", "betmgm", "pinnacle", "betrivers"];
export const SHARE_MAX_BOOKS = 3;

const SPORT_LABEL: Record<string, string> = { cfb: "NCAAF", nfl: "NFL" };

export function shareGameView({
  game,
  kickoff,
  projection,
  plays,
  odds,
  conditions,
}: {
  game: GameSummary;
  /** Already formatted by the caller, in the site's time zone. */
  kickoff: string;
  projection: GameProjection | null;
  plays: RulePlay[];
  odds: BookOdds[];
  conditions: GameConditions | null;
}): ShareGameView {
  const home = game.homeAbbreviation ?? game.homeSchool;
  const away = game.awayAbbreviation ?? game.awaySchool;
  const score = projectedScore(projection);
  // A team new to FBS hides every model number (migration 0083).
  const model = projection && !projection.missingPriorSeason ? projection : null;

  return {
    sportLabel: SPORT_LABEL[game.sport] ?? game.sport.toUpperCase(),
    weekLabel: `Week ${game.week}`,
    kickoff,
    neutralSite: game.neutralSite,
    away: team(game.awaySchool, away, game.awayColor, game.awayAltColor, game.awayPollRank,
      score?.away ?? null, game.completed ? game.awayPoints : null),
    home: team(game.homeSchool, home, game.homeColor, game.homeAltColor, game.homePollRank,
      score?.home ?? null, game.completed ? game.homePoints : null),
    completed: game.completed,
    winChance: model
      ? (() => {
          const fav = modelFavourite(model.pHomeWin, home, away);
          return `${fav.team} ${formatFair(fav.p)}`;
        })()
      : null,
    lines: [spreadLine(game, model, home, away), totalLine(game, model)],
    plays: plays
      .filter((p) => p.gameId === game.gameId)
      .map((p) => {
        const result = playResult(p);
        return {
          label: playLabel(p),
          // Frozen when the play was made; the model has moved since, so the
          // graphic says which number this is rather than contradict the table.
          reason: `At lock: ${lowerFirst(reasonLabel(p))}`,
          topTier: isTopTier(p),
          result: result === "pending" ? null : RESULT_LABEL[result],
        };
      }),
    books: shareBooks(odds, home, away),
    weather: shareWeather(conditions),
    fileName: shareGameFileName(game),
  };
}

type ShareNameGame = Pick<GameSummary, "week" | "homeAbbreviation" | "homeSchool" | "awayAbbreviation" | "awaySchool">;

/** "usm-at-troy-week-6.png": what the downloaded file is called. */
export function shareGameFileName(game: ShareNameGame): string {
  const home = game.homeAbbreviation ?? game.homeSchool;
  const away = game.awayAbbreviation ?? game.awaySchool;
  return `${slug(away)}-at-${slug(home)}-week-${game.week}.png`;
}

/** "Southern Miss @ Troy · Week 6": the share sheet's title. */
export function shareGameTitle(game: ShareNameGame): string {
  return `${game.awaySchool} @ ${game.homeSchool} · Week ${game.week}`;
}

const RESULT_LABEL = { win: "Won", loss: "Lost", push: "Push" } as const;

function team(
  school: string,
  abbreviation: string,
  color: string | null,
  altColor: string | null,
  rank: number | null,
  projected: number | null,
  final: number | null,
): ShareTeam {
  return {
    school,
    abbreviation,
    color,
    altColor,
    rank,
    projected: projected === null ? null : projected.toFixed(1),
    final,
  };
}

function spreadLine(
  game: GameSummary,
  model: GameProjection | null,
  home: string,
  away: string,
): ShareLine {
  const market = model?.spread ?? null;
  const bookLine = market ? market.line : game.homeSpread;
  const ours = model ? modelSpreadLabel(model.periods.full.margin.mean, home, away) : null;
  if (!market || !model) {
    return { label: "Spread", book: spreadLabel(bookLine, home, away), bookName: null, ours, lean: null, edge: null };
  }
  const side = tableSide(market, "spreads", model.periods.full.margin.mean);
  return {
    label: "Spread",
    book: spreadLabel(market.line, home, away),
    bookName: market.bookName,
    ours,
    lean: side.pass ? "Pass" : spreadSideLabel(market.line, side.side, home, away),
    edge: side.pass ? null : formatEdge(side.edge),
  };
}

function totalLine(game: GameSummary, model: GameProjection | null): ShareLine {
  const market = model?.total ?? null;
  const ours = model ? model.periods.full.total.mean.toFixed(1) : null;
  if (!market || !model) {
    return {
      label: "Total",
      book: game.gameTotal === null ? null : formatPoints(game.gameTotal),
      bookName: null,
      ours,
      lean: null,
      edge: null,
    };
  }
  const side = tableSide(market, "totals", model.periods.full.total.mean);
  return {
    label: "Total",
    book: formatPoints(market.line),
    bookName: market.bookName,
    ours,
    lean: side.pass ? "Pass" : totalSideLabel(market.line, side.side),
    edge: side.pass ? null : formatEdge(side.edge),
  };
}

/**
 * Up to three books' full-game prices, in `SHARE_BOOK_ORDER`. A book is listed
 * only if it prices the spread, the total or the moneyline.
 */
export function shareBooks(odds: BookOdds[], home: string, away: string): ShareBook[] {
  const full = odds.filter((o) => o.period === "full");
  const books: ShareBook[] = [];
  for (const key of SHARE_BOOK_ORDER) {
    if (books.length >= SHARE_MAX_BOOKS) break;
    const rows = full.filter((o) => o.sportsbookKey === key);
    const spread = rows.find((o) => o.market === "spreads" && o.line !== null);
    const total = rows.find((o) => o.market === "totals" && o.line !== null);
    const h2h = rows.find((o) => o.market === "h2h" && o.homePrice !== null && o.awayPrice !== null);
    if (!spread && !total && !h2h) continue;
    books.push({
      name: rows[0].sportsbookName,
      spread: spread
        ? `${spreadLabel(spread.line, home, away)}${price(favouritePrice(spread))}`
        : null,
      total: total
        ? `${formatPoints(total.line as number)} (o${formatAmericanOdds(total.overPrice)}/u${formatAmericanOdds(total.underPrice)})`
        : null,
      moneyline: h2h
        ? `${away} ${formatAmericanOdds(h2h.awayPrice)} · ${home} ${formatAmericanOdds(h2h.homePrice)}`
        : null,
    });
  }
  return books;
}

/** The price of the side `spreadLabel` names: the favourite (home on a PK). */
function favouritePrice(spread: BookOdds): number | null {
  return (spread.line as number) <= 0 ? spread.homePrice : spread.awayPrice;
}

function price(value: number | null): string {
  return value === null ? "" : ` (${formatAmericanOdds(value)})`;
}

/**
 * "☀️ Clear · 72° · 💨 18 mph", "🏟️ Indoors", or null with no reading yet.
 * Wind earns its emoji only at the speed the site already flags
 * (WIND_NOTABLE_MPH); below it, it is just a number.
 */
export function shareWeather(conditions: GameConditions | null): string | null {
  const view = weatherView(conditions);
  if (view.state === "indoors") return "🏟️ Indoors";
  if (!view.hasReading || !conditions) return null;
  const parts: string[] = [];
  const sky = conditions.condition;
  parts.push(sky ? `${weatherEmoji(sky)} ${sky}` : "🌡️");
  if (conditions.temperatureF !== null) parts.push(formatTemperature(conditions.temperatureF));
  if (conditions.windSpeedMph !== null && Math.round(conditions.windSpeedMph) > 0) {
    const windy = conditions.windSpeedMph >= WIND_NOTABLE_MPH;
    parts.push(`${windy ? "💨 " : ""}${formatWind(conditions.windSpeedMph)}`);
  }
  return parts.join(" · ");
}

/** The condition strings CFBD and Open-Meteo actually send, to one emoji. */
export function weatherEmoji(condition: string): string {
  const c = condition.toLowerCase();
  if (c.includes("thunder")) return "⛈️";
  if (c.includes("snow") || c.includes("sleet")) return "🌨️";
  if (c.includes("rain") || c.includes("drizzle") || c.includes("shower")) return "🌧️";
  if (c.includes("fog")) return "🌫️";
  if (c.includes("partly")) return "⛅";
  if (c.includes("cloud") || c.includes("overcast")) return "☁️";
  if (c.includes("clear") || c.includes("fair") || c.includes("sun")) return "☀️";
  return "🌡️";
}

const SHARE_KICKOFF = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: DISPLAY_TIME_ZONE,
});

/**
 * "Tue 10/6, 8:00 PM ET". The board's kickoff omits the date because the day
 * strip above it carries one; a shared image has no strip.
 */
export function formatShareKickoff(iso: string | null, timeTbd = false): string {
  if (!iso) return "Time TBD";
  const parts = Object.fromEntries(
    SHARE_KICKOFF.formatToParts(new Date(iso)).map((part) => [part.type, part.value]),
  );
  const day = `${parts.weekday} ${parts.month}/${parts.day}`;
  return timeTbd ? `${day}, time TBD` : `${day}, ${parts.hour}:${parts.minute} ${parts.dayPeriod} ET`;
}

function lowerFirst(value: string): string {
  return value.charAt(0).toLowerCase() + value.slice(1);
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
