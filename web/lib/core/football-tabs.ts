/**
 * Our tab row, per league, in the client's own names and order (client,
 * 2026-10-15): the same tabs his CFB and NFL pages carry, so moving between
 * his pages and ours reads as one site.
 *
 *   CFB  Games · Records · Player Props · Sheets · No Vig · Team Analytics
 *   NFL  Game Cards · Records · Player Props · Fantasy Model · Sheets · No Vig
 *
 * Team Analytics, Game Cards and the NFL Records are HIS views, so those tabs
 * open his site. "Records" on NFL is his model's record; our tracker of the
 * fantasy model's picks is linked from the Fantasy Model page instead. Our
 * Overview page is no longer a tab; it stays at its address.
 */

import { BOARD_PATH } from "./board-params.ts";
import { hisSiteHref } from "./client-site.ts";
import type { Sport } from "./sport.ts";

export type FootballTab = {
  label: string;
  icon: string;
  /** Our path (the header adds the sport), or an absolute URL on his site. */
  href: string;
  external: boolean;
};

const ours = (label: string, icon: string, href: string): FootballTab => ({
  label,
  icon,
  href,
  external: false,
});
const his = (label: string, icon: string, href: string): FootballTab => ({
  label,
  icon,
  href,
  external: true,
});

// Until the Tools menu takes them (the client's next ask), the three tools
// keep their own tabs at the end of the row.
const TOOLS: FootballTab[] = [
  ours("Builder", "🧱", "/builder"),
  ours("Check", "✅", "/slip-check"),
  ours("Arb", "🔀", "/arbitrage"),
];

export function footballTabs(sport: Sport): FootballTab[] {
  if (sport === "nfl") {
    return [
      his("Game Cards", "🏈", hisSiteHref("nfl")),
      his("Records", "📈", hisSiteHref("nfl", "records")),
      ours("Player Props", "🎯", BOARD_PATH),
      ours("Fantasy Model", "🧮", "/games"),
      ours("Sheets", "📋", "/cheat-sheets"),
      ours("No Vig", "⚖️", "/no-vig"),
      ...TOOLS,
    ];
  }
  return [
    ours("Games", "🏟️", "/games"),
    ours("Records", "📈", "/tracker"),
    ours("Player Props", "🎯", BOARD_PATH),
    ours("Sheets", "📋", "/cheat-sheets"),
    ours("No Vig", "⚖️", "/no-vig"),
    his("Team Analytics", "📊", hisSiteHref("cfb")),
    ...TOOLS,
  ];
}
