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

/** A line as books display it, e.g. 62.5, 0.5. */
export function formatLine(line: number): string {
  return line.toFixed(1).replace(/\.0$/, ".0");
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
