/**
 * The NFL game simulator on the page (phase S4; worker phases S1-S3).
 *
 * SPORT-AGNOSTIC CORE. The worker plays each game 2,000 times and stores, per
 * game, the distribution of every quarter and half and ONE of those games play
 * by play (`game_sims`, migration 0099); it then prices the first quarter and
 * first half against one book (`game_sim_edges`, 0100), and the view
 * `v_game_sim_edge_results` (0101) says what the site shows. This module reads
 * those rows and words them. It computes nothing the worker did not, except
 * where a play puts the ball on the field.
 *
 * THE EDGE SHOWN IS SHRUNK (CLAUDE.md §11, 2026-10-21): a fraction of our
 * disagreement with the book, on our side, never the raw number. The full-game
 * win % stays the live model's; the simulator never claims it.
 */

export type SimStage = "pregame" | "live";
export type SimPeriod = "q1" | "h1";
export type SimMarket = "h2h" | "spreads" | "totals";

export interface Histogram {
  lo: number;
  n: number[];
}

export interface SimSegment {
  home: number;
  away: number;
  homeRange: [number, number];
  awayRange: [number, number];
  pHomeLead: number;
  pTie: number;
  total: Histogram;
  margin: Histogram;
}

export interface SimPlay {
  q: number;
  clock: number;
  play: string;
  home: number;
  away: number;
  off: "home" | "away";
  endOff: "home" | "away";
  endYtg: number;
  ytg: number | null;
  down: number | null;
  togo: number | null;
  gain: number | null;
  result: string | null;
}

export interface GameSim {
  gameId: number;
  stage: SimStage;
  sims: number;
  period: number | null;
  clock: string | null;
  homePoints: number;
  awayPoints: number;
  priorHome: number;
  priorAway: number;
  quarters: SimSegment[];
  firstHalf: SimSegment;
  game: SimSegment;
  replay: SimPlay[];
  madeAt: string;
}

export interface SimEdge {
  gameId: number;
  period: SimPeriod;
  market: SimMarket;
  line: number | null;
  sportsbookId: number;
  firstPrice: number;
  secondPrice: number;
  /** Book's vig-free probability of the first side (home, or over). */
  bookProb: number;
  /** What the site shows as our probability of the first side (shrunk toward the book). */
  shownProb: number;
  /** Our side: where our number beats the book's. */
  side: "home" | "away" | "over" | "under";
  shownEdge: number;
  /** Our period total, or home margin. */
  modelMean: number;
  result: "won" | "lost" | "push" | null;
  madeAt: string;
}

export const GAME_SIM_COLUMNS =
  "game_id, stage, sims, period, clock, home_points, away_points, prior_home, prior_away, " +
  "summary, made_at";
export const GAME_SIM_REPLAY_COLUMNS = `${GAME_SIM_COLUMNS}, replay`;
export const SIM_EDGE_COLUMNS =
  "game_id, period, market, line, sportsbook_id, first_price, second_price, book_prob, " +
  "shown_prob, side, shown_edge, model_mean, result, made_at";

const n = (v: unknown) => Number(v);
const optN = (v: unknown) => (v === null || v === undefined ? null : Number(v));

function toSegment(raw: Record<string, unknown>): SimSegment {
  return {
    home: n(raw.home),
    away: n(raw.away),
    homeRange: (raw.home_range as [number, number]) ?? [0, 0],
    awayRange: (raw.away_range as [number, number]) ?? [0, 0],
    pHomeLead: n(raw.p_home_lead),
    pTie: n(raw.p_tie),
    total: raw.total as Histogram,
    margin: raw.margin as Histogram,
  };
}

export function toSimPlay(raw: Record<string, unknown>): SimPlay {
  return {
    q: n(raw.q),
    clock: n(raw.clock),
    play: String(raw.play),
    home: n(raw.home),
    away: n(raw.away),
    off: raw.off as SimPlay["off"],
    endOff: (raw.end_off as SimPlay["endOff"]) ?? (raw.off as SimPlay["off"]),
    endYtg: n(raw.end_ytg),
    ytg: optN(raw.ytg),
    down: optN(raw.down),
    togo: optN(raw.togo),
    gain: optN(raw.gain),
    result: (raw.result as string | undefined) ?? null,
  };
}

export function toGameSim(row: Record<string, unknown>): GameSim {
  const summary = row.summary as Record<string, unknown>;
  const halves = summary.halves as Record<string, Record<string, unknown>>;
  const replay = (row.replay as Record<string, unknown>[] | null | undefined) ?? [];
  return {
    gameId: n(row.game_id),
    stage: row.stage as SimStage,
    sims: n(row.sims),
    period: optN(row.period),
    clock: (row.clock as string | null) ?? null,
    homePoints: n(row.home_points),
    awayPoints: n(row.away_points),
    priorHome: n(row.prior_home),
    priorAway: n(row.prior_away),
    quarters: (summary.quarters as Record<string, unknown>[]).map(toSegment),
    firstHalf: toSegment(halves.first),
    game: toSegment(summary.game as Record<string, unknown>),
    replay: replay.map(toSimPlay),
    madeAt: row.made_at as string,
  };
}

export function toSimEdge(row: Record<string, unknown>): SimEdge {
  return {
    gameId: n(row.game_id),
    period: row.period as SimPeriod,
    market: row.market as SimMarket,
    line: optN(row.line),
    sportsbookId: n(row.sportsbook_id),
    firstPrice: n(row.first_price),
    secondPrice: n(row.second_price),
    bookProb: n(row.book_prob),
    shownProb: n(row.shown_prob),
    side: row.side as SimEdge["side"],
    shownEdge: n(row.shown_edge),
    modelMean: n(row.model_mean),
    result: (row.result as SimEdge["result"]) ?? null,
    madeAt: row.made_at as string,
  };
}

// =============================================================================
// The field
// =============================================================================

/**
 * Where the ball is, in yards from the LEFT goal line (0-100). The home team
 * attacks to the right all game, so its own end zone is on the left; the
 * replay keeps the teams on one side each rather than swapping every quarter,
 * which reads more easily at a glance than a broadcast does.
 */
export function fieldX(side: "home" | "away", ytg: number): number {
  const y = Math.min(100, Math.max(0, ytg));
  return side === "home" ? 100 - y : y;
}

/** Ball position before the snap (or the kick), and after the play. */
export function ballPath(p: SimPlay): { from: number; to: number } {
  let fromYtg: number;
  if (p.play === "kickoff") fromYtg = 65; // kicked from the other team's 35
  else if (p.play === "extra_point") fromYtg = 15;
  else if (p.play === "two_point") fromYtg = 2;
  else fromYtg = p.ytg ?? 75;
  const from = fieldX(p.off, fromYtg);
  let to: number;
  if (p.result === "td") to = fieldX(p.off, 0);
  else if (p.result === "def_td" || p.result === "safety") to = fieldX(p.off, 100);
  else if (p.play === "extra_point" || p.play === "two_point" || (p.play === "field_goal" && p.result === "good"))
    to = fieldX(p.off, 0);
  else to = fieldX(p.endOff, p.endYtg);
  return { from, to };
}

/** "1st & 10", "3rd & Goal". */
export function downLabel(down: number | null, togo: number | null, ytg: number | null): string | null {
  if (down === null || togo === null) return null;
  const ord = ["", "1st", "2nd", "3rd", "4th"][down] ?? `${down}th`;
  const dist = ytg !== null && togo >= ytg ? "Goal" : String(Math.max(1, Math.round(togo)));
  return `${ord} & ${dist}`;
}

/** "KC 35", "50", "BUF 20": the yard line in the team-side form a broadcast uses. */
export function spotLabel(
  off: "home" | "away",
  ytg: number,
  names: { home: string; away: string },
): string {
  if (ytg === 50) return "50";
  const own = ytg > 50;
  const team = own ? names[off] : names[off === "home" ? "away" : "home"];
  return `${team} ${own ? 100 - ytg : ytg}`;
}

export function clockLabel(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function quarterLabel(q: number): string {
  return q >= 5 ? "OT" : `Q${q}`;
}

/** One line of play-by-play, in the order a broadcast would say it. */
export function describePlay(p: SimPlay, names: { home: string; away: string }): string {
  const team = names[p.off];
  const other = names[p.off === "home" ? "away" : "home"];
  const yards = (g: number) => `${Math.abs(g)} yard${Math.abs(g) === 1 ? "" : "s"}`;
  switch (p.play) {
    case "kickoff":
      if (p.result === "td") return `${team} return the kickoff for a TOUCHDOWN`;
      if (p.result === "onside") return `${other} recover the onside kick`;
      return `Kickoff, ${team} start at ${spotLabel(p.endOff, p.endYtg, names)}`;
    case "extra_point":
      return p.result === "good" ? `${team} extra point is good` : `${team} extra point is no good`;
    case "two_point":
      return p.result === "good" ? `${team} convert the two-point try` : `${team} two-point try fails`;
    case "field_goal": {
      const dist = (p.ytg ?? 0) + 17;
      return p.result === "good"
        ? `${team} ${dist}-yard field goal is GOOD`
        : `${team} ${dist}-yard field goal is no good`;
    }
    case "punt":
      if (p.result === "td") return `${other} return the punt for a TOUCHDOWN`;
      return `${team} punt, ${names[p.endOff]} take over at ${spotLabel(p.endOff, p.endYtg, names)}`;
    case "kneel":
      return `${team} kneel`;
    case "spike":
      return `${team} spike the ball`;
    default: {
      const kind = p.play === "pass" ? "Pass" : "Run";
      if (p.result === "td") return `${team} ${kind.toLowerCase()} for a ${p.gain ?? p.ytg}-yard TOUCHDOWN`;
      if (p.result === "def_td") return `${other} return a turnover for a TOUCHDOWN`;
      if (p.result === "safety") return `${team} tackled in the end zone: SAFETY`;
      if (p.result === "turnover") return `${team} turn it over, ${names[p.endOff]} ball`;
      const g = p.gain ?? 0;
      const base =
        g === 0
          ? `${kind}, no gain`
          : g < 0
            ? `${kind} for a loss of ${yards(g)}`
            : `${kind} for ${yards(g)}`;
      if (p.result === "first_down") return `${team} ${base.toLowerCase()}, FIRST DOWN`;
      if (p.result === "downs") return `${team} ${base.toLowerCase()}, turnover on downs`;
      return `${team} ${base.toLowerCase()}`;
    }
  }
}

/** A play that changes the score, for the highlight colour and a longer pause. */
export function isScoring(p: SimPlay, before: { home: number; away: number }): boolean {
  return p.home !== before.home || p.away !== before.away;
}

// =============================================================================
// Quarters, the half, and the book
// =============================================================================

export interface Checkpoint {
  period: SimPeriod;
  /** Index of the last play of the period in the replay. */
  index: number;
  home: number;
  away: number;
}

/** The end of the first quarter and of the first half in a replay, where it pauses. */
export function checkpoints(replay: SimPlay[]): Checkpoint[] {
  const out: Checkpoint[] = [];
  for (const [period, lastQ] of [
    ["q1", 1],
    ["h1", 2],
  ] as const) {
    let index = -1;
    for (let i = 0; i < replay.length; i++) if (replay[i].q <= lastQ) index = i;
    if (index >= 0 && index < replay.length - 1) {
      out.push({ period, index, home: replay[index].home, away: replay[index].away });
    }
  }
  return out;
}

/** First play of each quarter, for "skip to". */
export function quarterStarts(replay: SimPlay[]): Map<number, number> {
  const out = new Map<number, number>();
  replay.forEach((p, i) => {
    if (!out.has(p.q)) out.set(p.q, i);
  });
  return out;
}

export function segmentFor(sim: GameSim, period: SimPeriod): SimSegment {
  return period === "q1" ? sim.quarters[0] : sim.firstHalf;
}

/** P(value > line) from a stored histogram, a push excluded from both sides. */
export function pAbove(hist: Histogram, line: number): number | null {
  let over = 0;
  let under = 0;
  hist.n.forEach((count, i) => {
    const v = hist.lo + i;
    if (v > line) over += count;
    else if (v < line) under += count;
  });
  return over + under === 0 ? null : over / (over + under);
}

/** The 10th and 90th percentile of a histogram: the 80% range. */
export function histRange(hist: Histogram): [number, number] {
  const total = hist.n.reduce((a, b) => a + b, 0);
  const at = (q: number) => {
    let seen = 0;
    for (let i = 0; i < hist.n.length; i++) {
      seen += hist.n[i];
      if (seen >= q * total) return hist.lo + i;
    }
    return hist.lo + hist.n.length - 1;
  };
  return [at(0.1), at(0.9)];
}

/** Our side and the book's in words: "Over 9.5", "KC -2.5", "BUF ML". */
export function edgeSideLabel(e: SimEdge, names: { home: string; away: string }): string {
  if (e.market === "totals") return `${e.side === "over" ? "Over" : "Under"} ${e.line?.toFixed(1)}`;
  const team = names[e.side as "home" | "away"];
  if (e.market === "h2h") return `${team} ML`;
  const line = e.side === "home" ? (e.line ?? 0) : -(e.line ?? 0);
  return `${team} ${line > 0 ? "+" : line === 0 ? "" : "-"}${Math.abs(line).toFixed(1)}`;
}

/** Our probability and the book's of OUR side, and the price on it. */
export function sideNumbers(e: SimEdge): { ours: number; book: number; price: number } {
  const first = e.side === "home" || e.side === "over";
  return {
    ours: first ? e.shownProb : 1 - e.shownProb,
    book: first ? e.bookProb : 1 - e.bookProb,
    price: first ? e.firstPrice : e.secondPrice,
  };
}

/** How the simulated game's period went against the book's line, for the pause card. */
export function replayVsLine(
  e: SimEdge,
  score: { home: number; away: number },
): "first" | "second" | "push" | null {
  const x =
    e.market === "totals"
      ? score.home + score.away - (e.line ?? 0)
      : e.market === "spreads"
        ? score.home - score.away + (e.line ?? 0)
        : score.home - score.away;
  if (e.market !== "h2h" && e.line === null) return null;
  return x > 0 ? "first" : x < 0 ? "second" : "push";
}

/** The replayed game's score in a period, from its checkpoint. */
export function periodScore(
  cp: Checkpoint | undefined,
): { home: number; away: number } | null {
  return cp ? { home: cp.home, away: cp.away } : null;
}
