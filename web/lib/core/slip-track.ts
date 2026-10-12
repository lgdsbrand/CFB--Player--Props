/**
 * Saved slips and how each leg is doing (client, 2026-10-12: "submit all
 * their slips and track it live", after nhlpropking.com/bet-slip-check).
 *
 * SPORT-AGNOSTIC CORE. Pure: a saved leg and the facts about its game in, a
 * status out. The facts are read by `/slip-check/track` from tables the site
 * already reads: `games` (the final score of record), `live_scores` (college
 * only, every two minutes, migration 0088) and `player_game_stats` (the box
 * score, the morning after).
 *
 * KEPT IN THIS BROWSER ONLY (the user, 2026-10-12: "browser only, I don't
 * want to deal with account creations"). Nothing is sent to the server but
 * the legs being looked up.
 *
 * NOT GRADING. This tells a reader how their bet is doing; it is not the
 * model's record (the tracker is that) and it never says a leg was voided:
 * only the book can.
 */

import { formatLine } from "./format.ts";
import { liveStateLabel, type LiveScore } from "./live.ts";
import { legKey, parseLegs, type GameLeg, type PropLeg, type SlipLeg } from "./slip.ts";

// ---------------------------------------------------------------- saved slips

export interface SavedSlip {
  id: string;
  savedAt: string;
  /** The book the slip was checked or priced at, when there was one. */
  bookKey: string | null;
  bookName: string | null;
  legs: SlipLeg[];
  /** Each leg's price at that book when saved, in leg order. */
  prices: (number | null)[];
  /** The slip's price at that book when saved (a single leg's own price). */
  price: number | null;
  /** Legs already decided, by leg key, so a finished slip is not looked up again. */
  settled: Record<string, LegTrack>;
}

/** The newest kept; the oldest drops off when one more is saved. */
export const MAX_SAVED = 50;

const sameLegs = (a: SlipLeg[], b: SlipLeg[]) =>
  a.map(legKey).sort().join("|") === b.map(legKey).sort().join("|");

/** The slip saved first in the list, or the list unchanged if it is already there. */
export function addSaved(list: SavedSlip[], slip: SavedSlip): SavedSlip[] {
  if (findSaved(list, slip.legs, slip.bookKey)) return list;
  return [slip, ...list].slice(0, MAX_SAVED);
}

/** The saved slip with exactly these legs at this book, if any. */
export function findSaved(list: SavedSlip[], legs: SlipLeg[], bookKey: string | null): SavedSlip | undefined {
  return list.find((s) => s.bookKey === bookKey && sameLegs(s.legs, legs));
}

const SETTLED: ReadonlySet<TrackState> = new Set(["won", "lost", "push"]);
export const isSettled = (track: LegTrack | undefined): boolean => !!track && SETTLED.has(track.state);

/** Saved slips read back from storage: anything malformed is dropped, not repaired. */
export function parseSavedSlips(raw: unknown): SavedSlip[] {
  if (!Array.isArray(raw)) return [];
  const out: SavedSlip[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const s = item as Record<string, unknown>;
    const legs = parseLegs(s.legs);
    if (typeof s.id !== "string" || typeof s.savedAt !== "string" || legs.length === 0) continue;
    const numOrNull = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
    const str = (v: unknown) => (typeof v === "string" ? v : null);
    const prices = Array.isArray(s.prices) ? legs.map((_, i) => numOrNull((s.prices as unknown[])[i])) : legs.map(() => null);
    const settled: Record<string, LegTrack> = {};
    if (s.settled && typeof s.settled === "object") {
      for (const [key, value] of Object.entries(s.settled as Record<string, unknown>)) {
        const t = value as LegTrack | null;
        if (t && SETTLED.has(t.state) && typeof t.detail === "string") settled[key] = { state: t.state, detail: t.detail };
      }
    }
    out.push({
      id: s.id,
      savedAt: s.savedAt,
      bookKey: str(s.bookKey),
      bookName: str(s.bookName),
      legs,
      prices,
      price: numOrNull(s.price),
      settled,
    });
  }
  return out.slice(0, MAX_SAVED);
}

// ---------------------------------------------------------------- the facts

/** What the server knows about one game, from `/slip-check/track`. */
export interface TrackGame {
  gameId: number;
  startDate: string | null;
  /** The score of record (`games`), once the results ingest has the game as completed. */
  final: Score | null;
  /** The scoreboard right now, for a sport with a live feed. */
  live: LiveScore | null;
  /** Whether this sport has a live feed at all (college does, NFL does not). */
  hasLiveFeed: boolean;
  /** Whether any box-score row exists for this game yet. */
  statsIn: boolean;
}

export interface Score {
  homePoints: number;
  awayPoints: number;
  homeLineScores: number[] | null;
  awayLineScores: number[] | null;
}

export interface TrackFacts {
  games: TrackGame[];
  /**
   * A prop leg's stat, by `propStatKey`: the number, null when the box-score
   * row exists without this stat, absent when there is no row.
   */
  stats: Record<string, number | null>;
}

/** Leg keys per `/slip-check/track` request; the page asks in batches. */
export const TRACK_BATCH = 80;

export const propStatKey = (gameId: number, playerId: number, marketKey: string) =>
  `${gameId}:${playerId}:${marketKey}`;

// ---------------------------------------------------------------- statuses

/**
 * - pending: not started.
 * - live: the game is on; a game leg says how it stands.
 * - awaiting: the game is over, the box score is not in yet (props).
 * - won / lost / push: decided.
 * - check: the facts cannot decide it (no box-score line, no quarter scores).
 */
export type TrackState = "pending" | "live" | "awaiting" | "won" | "lost" | "push" | "check";

export interface LegTrack {
  state: TrackState;
  detail: string;
  /** While live: is the leg ahead of its line right now. */
  trend?: "ahead" | "behind" | "even";
}

/** After this long past kickoff with no final, the game is treated as over. */
const GAME_HOURS = 5;

export function trackLeg(leg: SlipLeg, game: TrackGame | undefined, facts: TrackFacts, now: Date): LegTrack {
  return leg.kind === "game" ? trackGameLeg(leg, game, now) : trackPropLeg(leg, game, facts, now);
}

function kickedOff(startDate: string | null, now: Date): boolean {
  const at = startDate ? Date.parse(startDate) : NaN;
  return Number.isFinite(at) && now.getTime() >= at;
}

function hoursSince(startDate: string | null, now: Date): number {
  const at = startDate ? Date.parse(startDate) : NaN;
  return Number.isFinite(at) ? (now.getTime() - at) / 3_600_000 : 0;
}

/** A count or margin as a reader writes it: "7", "3.5". */
const num = (x: number) => (Number.isInteger(x) ? String(x) : formatLine(x));

const PERIOD_WORD: Record<GameLeg["period"], string> = { full: "Final", h1: "Half", q1: "1Q" };

/** Points in the leg's period, from line scores; null when they do not cover it. */
function periodPoints(lines: number[] | null, period: GameLeg["period"], points: number): number | null {
  if (period === "full") return points;
  const needed = period === "q1" ? 1 : 2;
  if (!lines || lines.length < needed) return null;
  return lines.slice(0, needed).reduce((a, b) => a + b, 0);
}

/** Points so far in the leg's period, while it is being played. */
function pointsSoFar(lines: number[] | null, period: GameLeg["period"], points: number): number {
  if (period === "full" || !lines) return points;
  return lines.slice(0, period === "q1" ? 1 : 2).reduce((a, b) => a + b, 0);
}

/** Is the leg's period over, on the live scoreboard. */
function periodDone(live: LiveScore, period: GameLeg["period"]): boolean {
  if (live.status === "completed") return true;
  if (live.status !== "in_progress" || live.period === null) return false;
  if (period === "q1") return live.period >= 2;
  if (period === "h1") return live.period >= 3;
  return false;
}

/** + when the leg's side is ahead of its line by that much, − behind, 0 level. */
function margin(leg: GameLeg, home: number, away: number): number {
  if (leg.market === "totals") {
    const total = home + away;
    return leg.side === "over" ? total - (leg.line ?? 0) : (leg.line ?? 0) - total;
  }
  const diff = home - away + (leg.market === "spreads" ? (leg.line ?? 0) : 0);
  return leg.side === "home" ? diff : -diff;
}

const scoreText = (leg: GameLeg, home: number, away: number) => `${leg.away} ${away} – ${leg.home} ${home}`;

function decided(leg: GameLeg, home: number, away: number, word: string): LegTrack {
  const m = margin(leg, home, away);
  return { state: m > 0 ? "won" : m < 0 ? "lost" : "push", detail: `${word}: ${scoreText(leg, home, away)}` };
}

function standing(leg: GameLeg, home: number, away: number): { text: string; trend: LegTrack["trend"] } {
  const m = margin(leg, home, away);
  const trend = m > 0 ? "ahead" : m < 0 ? "behind" : "even";
  const by = num(Math.abs(m));
  if (leg.market === "totals") {
    // Only reached with the total at or under its line (past it is decided).
    const total = home + away;
    return {
      text: leg.side === "over" ? `${total} so far, needs ${by} more` : `${total} so far, ${by} to spare`,
      trend,
    };
  }
  if (leg.market === "h2h") {
    if (m === 0) return { text: "Tied", trend };
    return { text: m > 0 ? `Leading by ${by}` : `Trailing by ${by}`, trend };
  }
  if (m === 0) return { text: "Right on the number", trend };
  return { text: m > 0 ? `Covering by ${by}` : `Short by ${by}`, trend };
}

function trackGameLeg(leg: GameLeg, game: TrackGame | undefined, now: Date): LegTrack {
  const word = PERIOD_WORD[leg.period];
  if (game?.final) {
    const f = game.final;
    const home = periodPoints(f.homeLineScores, leg.period, f.homePoints);
    const away = periodPoints(f.awayLineScores, leg.period, f.awayPoints);
    if (home !== null && away !== null) return decided(leg, home, away, word);
    // The final is in but the quarters are not: from the scoreboard if it has them.
  }

  const live = game?.live ?? null;
  if (live && live.homePoints !== null && live.awayPoints !== null) {
    if (periodDone(live, leg.period)) {
      const home = periodPoints(live.homeLineScores, leg.period, live.homePoints);
      const away = periodPoints(live.awayLineScores, leg.period, live.awayPoints);
      if (home !== null && away !== null) return decided(leg, home, away, word);
    } else if (live.status === "in_progress") {
      const home = pointsSoFar(live.homeLineScores, leg.period, live.homePoints);
      const away = pointsSoFar(live.awayLineScores, leg.period, live.awayPoints);
      // A total only climbs: an over past its line has won, an under has lost.
      if (leg.market === "totals" && home + away > (leg.line ?? Infinity)) {
        return {
          state: leg.side === "over" ? "won" : "lost",
          detail: `${home + away} scored, past ${formatLine(leg.line ?? 0)}: ${scoreText(leg, home, away)}`,
        };
      }
      const { text, trend } = standing(leg, home, away);
      return { state: "live", detail: `${liveStateLabel(live)} · ${scoreText(leg, home, away)} · ${text}`, trend };
    }
  }

  if (game?.final) {
    return { state: "check", detail: "No quarter scores for this game: check your book" };
  }
  if (!kickedOff(leg.startDate ?? game?.startDate ?? null, now)) return { state: "pending", detail: "" };
  if (hoursSince(leg.startDate ?? game?.startDate ?? null, now) > GAME_HOURS) {
    return { state: "awaiting", detail: "Over: the final score lands in the morning" };
  }
  return {
    state: "live",
    detail: game?.hasLiveFeed ? "In progress: the score will show shortly" : "In progress: no live scores for this league, settles after the final",
  };
}

function trackPropLeg(leg: PropLeg, game: TrackGame | undefined, facts: TrackFacts, now: Date): LegTrack {
  const key = propStatKey(leg.gameId, leg.playerId, leg.marketKey);
  if (key in facts.stats) {
    const value = facts.stats[key];
    if (value === null) return { state: "check", detail: "No stat for this market in the box score: check your book" };
    const m = leg.side === "over" ? value - leg.line : leg.line - value;
    const text = leg.binary
      ? value >= 1
        ? `Scored (${value} TD${value === 1 ? "" : "s"})`
        : "Did not score"
      : `${num(value)} ${leg.market}`;
    return { state: m > 0 ? "won" : m < 0 ? "lost" : "push", detail: text };
  }

  if (game?.statsIn) {
    return {
      state: "check",
      detail:
        leg.side === "over"
          ? "No stat line: lost if the player played, usually void if not"
          : "No stat line: won if the player played, usually void if not",
    };
  }

  const startDate = leg.startDate ?? game?.startDate ?? null;
  const over = !!game?.final || game?.live?.status === "completed" || hoursSince(startDate, now) > GAME_HOURS;
  if (over) return { state: "awaiting", detail: "Game over: player stats land the next morning" };
  if (!kickedOff(startDate, now)) return { state: "pending", detail: "" };
  const live = game?.live;
  return {
    state: "live",
    detail: `${live && live.status === "in_progress" ? `${liveStateLabel(live)} · ` : ""}In progress: settles when the box score lands`,
  };
}

// ---------------------------------------------------------------- the slip

export type SlipState = "won" | "lost" | "push" | "check" | "open";

export interface SlipOutcome {
  state: SlipState;
  label: string;
}

/** Lost as soon as one leg loses; won once every leg is in and none lost. */
export function slipOutcome(tracks: LegTrack[]): SlipOutcome {
  const count = (s: TrackState) => tracks.filter((t) => t.state === s).length;
  const won = count("won");
  const pushed = count("push");
  if (count("lost") > 0) return { state: "lost", label: "Lost" };
  if (won + pushed === tracks.length) {
    if (won === 0) return { state: "push", label: "Push" };
    return { state: "won", label: pushed > 0 ? `Won · ${pushed} push${pushed === 1 ? "" : "es"}` : "Won" };
  }
  if (count("pending") === tracks.length) return { state: "open", label: "Not started" };
  const check = count("check");
  const parts = [`${won} of ${tracks.length} won`];
  if (count("live") > 0) parts.push(`${count("live")} live`);
  if (count("awaiting") > 0) parts.push(`${count("awaiting")} awaiting stats`);
  if (count("pending") > 0) parts.push(`${count("pending")} to play`);
  if (check > 0) parts.push(`${check} to check`);
  const open = tracks.length - won - pushed - check;
  return { state: open === 0 ? "check" : "open", label: parts.join(" · ") };
}

/** Whether anything on these legs can change in the next few minutes. */
export function needsPolling(legs: SlipLeg[], tracks: LegTrack[], now: Date): boolean {
  return legs.some((leg, i) => {
    const t = tracks[i];
    if (!t || t.state === "live") return true;
    if (t.state !== "pending") return false;
    const at = leg.startDate ? Date.parse(leg.startDate) : NaN;
    return Number.isFinite(at) && at - now.getTime() < 10 * 60_000;
  });
}
