/**
 * The client's own site, as our header mirrors it (2026-10-14).
 *
 * The football app moves under /football on his site (lgdsanalytics.vercel.app),
 * and at his request its menu looks like his baseball page's: his brand line,
 * his row of sports, then this sport's own tabs. The row of sports is a copy of
 * his `SPORTS_CONFIG` (frontend/src/utils/sports_config.js in his repo,
 * lgdsbrand/mlb_predictor_engine_v2), in his order with his statuses: ACTIVE and
 * BETA sit in the bar, COMING_SOON under "More". KEEP THEM IN STEP: a sport he
 * adds there must be added here.
 *
 * EVERY ENTRY OPENS HIS SITE, NFL and CFB included (client, 2026-10-14: he
 * keeps his own NFL and CFB pages; this app is their Player Props tab, plus a
 * Fantasy Model tab on NFL). NFL or CFB is highlighted to say which of his
 * pages the reader came from; switching league inside this app is the small
 * NFL/CFB switch in our own tab row. Links are `/?sport=<id>`, which his app
 * reads on load (added in his repo with this change; before it, his menu had
 * no addresses at all, only in-page state).
 *
 * League icons are his emoji fallbacks, not the ESPN league logos his own bar
 * hotlinks: this repo does not embed official marks (CLAUDE.md §7).
 */

import type { Sport } from "./sport.ts";

export const CLIENT_SITE_ORIGIN = "https://lgdsanalytics.vercel.app";

export type HisSportStatus = "ACTIVE" | "BETA" | "COMING_SOON";

export interface HisSport {
  id: string;
  name: string;
  icon: string;
  status: HisSportStatus;
  /** NFL and CFB: the sport of ours that highlights this entry. */
  football?: Sport;
}

export const HIS_SPORTS: readonly HisSport[] = [
  { id: "home", name: "Home", icon: "🏠", status: "ACTIVE" },
  { id: "mlb", name: "MLB", icon: "⚾", status: "ACTIVE" },
  { id: "tennis", name: "Tennis", icon: "🎾", status: "ACTIVE" },
  { id: "nba", name: "NBA", icon: "🏀", status: "BETA" },
  { id: "soccer", name: "Soccer", icon: "⚽", status: "COMING_SOON" },
  { id: "ufc", name: "UFC", icon: "🥊", status: "COMING_SOON" },
  { id: "nfl", name: "NFL", icon: "🏈", status: "BETA", football: "nfl" },
  { id: "fantasy", name: "Fantasy", icon: "🏈", status: "ACTIVE" },
  { id: "wnba", name: "WNBA", icon: "🏀", status: "BETA" },
  { id: "nhl", name: "NHL", icon: "🏒", status: "COMING_SOON" },
  { id: "cbb", name: "CBB", icon: "🏀", status: "COMING_SOON" },
  { id: "cfb", name: "CFB", icon: "🏈", status: "BETA", football: "cfb" },
  { id: "pga", name: "PGA Tour", icon: "⛳", status: "COMING_SOON" },
];

/** His bar: everything live or in beta. */
export const BAR_SPORTS = HIS_SPORTS.filter((s) => s.status !== "COMING_SOON");
/** His "More" menu: what is coming. */
export const SOON_SPORTS = HIS_SPORTS.filter((s) => s.status === "COMING_SOON");

/**
 * A page of his site: a sport's view, or his home. `view` opens a tab of that
 * sport's page (his NFL page reads `?view=records`).
 */
export function hisSiteHref(sportId?: string, view?: string): string {
  if (!sportId || sportId === "home") return `${CLIENT_SITE_ORIGIN}/`;
  const page = `${CLIENT_SITE_ORIGIN}/?sport=${encodeURIComponent(sportId)}`;
  return view ? `${page}&view=${encodeURIComponent(view)}` : page;
}

/** His About and Contact are dialogs on his site, opened by `/?open=`. */
export function hisInfoHref(dialog: "about" | "contact"): string {
  return `${CLIENT_SITE_ORIGIN}/?open=${dialog}`;
}
