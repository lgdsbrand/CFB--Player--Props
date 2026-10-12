/**
 * The cheat sheet as a graphic to post (client, 2026-10-12; his reference was
 * Linemate's "100% Alternate Lines": "S. Preston: 10+ Receiving Yards ·
 * 18/L18 Games").
 *
 * SPORT-AGNOSTIC CORE. Pure: the sheet's rows (already filtered and in sheet
 * order, `compareRows`) and the page's filters in, every string the image
 * prints out.
 *
 * THE MODEL'S CALL TRAVELS WITH THE STREAK, here as on the page (rule 2 in
 * `cheat-sheet.ts`): a row the model calls the other way says so. A streak is
 * not an edge, and a graphic that dropped the disagreement would be the page
 * with its caveat cut off.
 */

import { agreement, type Agreement, type CheatSheetRow, type CheatTierKey } from "./cheat-sheet.ts";
import { formatLine } from "./format.ts";

export interface ShareSheetRow {
  player: string;
  abbreviation: string;
  color: string | null;
  altColor: string | null;
  /** "10+ Rec Yds", "Under 62.5 Rush Yds", "Anytime TD". */
  label: string;
  /** "5/5". */
  record: string;
  agreement: Agreement;
}

export interface ShareSheetView {
  /** "100% Hit Rates" / "80%+ Hit Rates". */
  title: string;
  /** "NCAAF Week 7 · Last 5 games · WR · Overs". */
  subtitle: string;
  rows: ShareSheetRow[];
  /** Rows on the sheet that did not fit. */
  more: number;
}

/** What fits a 1080 x 1350 graphic with its header and footer. */
export const SHARE_SHEET_ROWS = 10;

/**
 * The bet a streak describes, the way a bettor would read it: an over at 9.5
 * is "10+", the alternate line books sell; an under keeps its line.
 */
export function streakLabel(
  row: Pick<CheatSheetRow, "isBinary" | "hitSide" | "line" | "marketLabel" | "marketKey">,
): string {
  // Spelled out on a graphic: "ATD" is board shorthand a follower may not know.
  if (row.marketKey === "anytime_td") return "Anytime TD";
  const market = row.marketLabel ?? row.marketKey;
  if (row.isBinary) return market;
  if (row.line === null) return market;
  if (row.hitSide === "over") return `${Math.floor(row.line) + 1}+ ${market}`;
  return `Under ${formatLine(row.line)} ${market}`;
}

export function shareSheetView({
  rows,
  sportLabel,
  week,
  windowSize,
  tier,
  position,
  marketLabel,
  side,
  limit = SHARE_SHEET_ROWS,
}: {
  rows: CheatSheetRow[];
  sportLabel: string;
  week: number;
  windowSize: number;
  tier: CheatTierKey | undefined;
  position?: string;
  marketLabel?: string;
  side?: "over" | "under";
  limit?: number;
}): ShareSheetView {
  const parts = [`${sportLabel} Week ${week}`, `Last ${windowSize} games`];
  if (position) parts.push(position);
  if (marketLabel) parts.push(marketLabel);
  if (side) parts.push(side === "over" ? "Overs" : "Unders");
  return {
    title: tier === "perfect" ? "100% Hit Rates" : "80%+ Hit Rates",
    subtitle: parts.join(" · "),
    rows: rows.slice(0, limit).map((row) => ({
      player: row.playerName,
      abbreviation: row.teamAbbreviation ?? row.teamSchool,
      color: row.teamColor,
      altColor: row.teamAltColor,
      label: streakLabel(row),
      record: `${row.hits}/${row.decided}`,
      agreement: agreement(row),
    })),
    more: Math.max(0, rows.length - limit),
  };
}
