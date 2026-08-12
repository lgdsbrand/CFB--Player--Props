/**
 * Sport-agnostic display formatting.
 *
 * Part of the core layer (CLAUDE.md §3): nothing here may reference college
 * football. The NFL build copies this file unchanged.
 */

export type BetSide = "over" | "under";

/**
 * The headline number on every card: confidence as a whole-number percentage.
 *
 * Note this formats a probability that was DERIVED from the projected
 * distribution — the mass past the line. The projection itself is secondary
 * detail (CLAUDE.md §1).
 */
export function formatConfidence(probability: number): string {
  return `${Math.round(probability * 100)}%`;
}

/**
 * Edge as a signed percentage-point figure.
 *
 * Edge is `model probability − de-vigged book implied probability`, computed in
 * the database (see the `edge_on_side` SQL function). This only formats it.
 */
export function formatEdge(edge: number | null): string {
  if (edge === null) return "—";
  const points = edge * 100;
  const sign = points > 0 ? "+" : "";
  return `${sign}${points.toFixed(1)}%`;
}

/** American odds with an explicit sign, e.g. -115, +140. */
export function formatAmericanOdds(price: number | null): string {
  if (price === null) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

/**
 * A line or projected value as books display it, e.g. 62.5, 0.5.
 *
 * NEGATIVE ZERO IS RENDERED AS ZERO. Quantiles are found by bisecting the
 * survival function, which for a discrete count lands a hair below zero — so a
 * quarterback projected for no touchdowns displayed "proj -0.0", which reads as
 * a broken number rather than as the zero it is. `toFixed` preserves the sign
 * of -0.04; this does not.
 */
export function formatLine(line: number): string {
  const rounded = line.toFixed(1);
  return rounded === "-0.0" ? "0.0" : rounded;
}

export function formatSide(side: BetSide): string {
  return side.toUpperCase();
}

/**
 * Semantic colour token for a side. Returns a token name, not a hex value —
 * the actual colours live in app/globals.css so the reskin is one file.
 */
export function sideColorToken(side: BetSide): "positive" | "negative" {
  return side === "over" ? "positive" : "negative";
}

/**
 * Whether an edge clears the configured threshold. The threshold is not
 * hardcoded here: it comes from app_config.edge_threshold (default 0.05, the
 * value the client's pitcher model uses).
 */
export function meetsEdgeThreshold(
  edge: number | null,
  threshold: number,
): boolean {
  return edge !== null && edge >= threshold;
}

/**
 * Display timezone for kickoffs.
 *
 * NOT UTC, and not the viewer's locale. Kickoffs are stored in UTC, where a
 * Tuesday 8pm Eastern game is already Wednesday — so formatting in UTC prints
 * the wrong DAY for every night game, which is most of them. Formatting in the
 * viewer's zone instead would make the server and client disagree and produce a
 * hydration mismatch.
 *
 * US Eastern is the convention American sportsbooks and broadcasters use, so it
 * is the least surprising fixed choice. Stated once here because it is a
 * product decision, not an implementation detail.
 */
export const DISPLAY_TIME_ZONE = "America/New_York";

const DATE_SHORT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: DISPLAY_TIME_ZONE,
});

const WEEKDAY_TIME = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  hour: "numeric",
  minute: "2-digit",
  timeZone: DISPLAY_TIME_ZONE,
});

/**
 * A whole-number count with thousands separators, e.g. "3,788".
 *
 * The locale is pinned rather than left to the runtime. Bare `toLocaleString()`
 * follows the SERVER's locale, which on a non-US machine renders 3788 as
 * "3.788" — read by an American audience as three point seven eight eight. It
 * also makes the server and client disagree, which is a hydration mismatch
 * waiting to happen.
 */
const COUNT = new Intl.NumberFormat("en-US");

export function formatCount(value: number): string {
  return COUNT.format(value);
}

/** "Oct 28" */
export function formatDateShort(iso: string | null): string {
  if (!iso) return "—";
  return DATE_SHORT.format(new Date(iso));
}

/** "Sat 3:30 PM" */
export function formatKickoff(iso: string | null): string {
  if (!iso) return "TBD";
  return WEEKDAY_TIME.format(new Date(iso));
}

/**
 * "Alumni Stadium · Chestnut Hill, MA" — where the game is being played.
 *
 * THE PARENTHETICAL IS STRIPPED, and that is the whole reason this is a function
 * rather than a template string at the call site. 60 of the 844 venues on record
 * disambiguate their name with the location they are in — "Alumni Stadium
 * (Chestnut Hill, MA)", "Alumni Field (OH)" — because a dozen schools share a
 * stadium name. Printed beside the city and state we already show, that renders
 * "Alumni Stadium (Chestnut Hill, MA) · Chestnut Hill, MA".
 *
 * ONLY WHEN THE LOCATION IS ACTUALLY SHOWN. With no city on the row the
 * parenthetical is the only thing distinguishing one Alumni Stadium from
 * another, so it stays. The suffix is what is redundant, not what is wrong.
 *
 * Returns null rather than a placeholder when there is no venue. `games.venue_id`
 * is nullable and a card should then say nothing, not "—": an em dash under the
 * team names reads as a stadium whose name failed to load.
 */
export function formatVenue(venue: {
  name: string | null;
  city: string | null;
  state: string | null;
}): string | null {
  const place = [venue.city, venue.state].filter(Boolean).join(", ");

  if (!venue.name) return place || null;

  // Trailing parenthetical only. A venue legitimately named "Stadium (Old)"
  // mid-string keeps it, and so does one whose parenthetical is not at the end.
  const name = place
    ? venue.name.replace(/\s*\([^()]*\)\s*$/, "").trim() || venue.name
    : venue.name;

  return place ? `${name} · ${place}` : name;
}

/**
 * A point spread with an explicit sign, from the named team's perspective.
 *
 * THE SIGN IS ALWAYS SHOWN, including the plus. A spread without one is
 * ambiguous in the only place it matters: "10.5" beside a team name reads as
 * a favourite to most people, and for the away side of a game it is exactly
 * backwards. The database already flips the stored home-side number to the
 * player's own team (`v_board_rows.team_spread`), so all this has to do is
 * refuse to hide which way it points.
 *
 * ZERO IS "PK", not "+0.0". A pick-em is a real state and a signed zero reads
 * as a rendering bug.
 */
export function formatSpread(spread: number | null): string | null {
  if (spread === null || !Number.isFinite(spread)) return null;
  if (spread === 0) return "PK";
  return `${spread > 0 ? "+" : ""}${spread.toFixed(1)}`;
}

/** "−10.5 · O/U 48.5", or just one half when only one is priced. */
export function formatGameLine(
  spread: number | null,
  total: number | null,
): string | null {
  const parts: string[] = [];
  const s = formatSpread(spread);
  if (s) parts.push(s);
  if (total !== null && Number.isFinite(total)) parts.push(`O/U ${total.toFixed(1)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** "Oct 28 – Nov 1", collapsing to one date when the span is a single day. */
export function formatDateRange(
  first: string | null,
  last: string | null,
): string {
  if (!first && !last) return "—";
  const start = formatDateShort(first);
  const end = formatDateShort(last);
  return start === end ? start : `${start} – ${end}`;
}
