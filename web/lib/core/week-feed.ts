/**
 * The week's games as the client's site draws them: the compact game strip at
 * the top of his CFB and NFL pages (served at /football/api/week).
 *
 * His own CFB feed is a snapshot frozen at week 3, so the strip reads ours.
 * Logos are ESPN's, which his pages already show: a college team's CFBD id IS
 * its ESPN id, and NFL teams are keyed by code. This app itself still draws no
 * logos (CLAUDE.md §7); only his pages render these URLs.
 */

import { scopedHref } from "./board-params.ts";
import { liveStateLabel, type LiveScore } from "./live.ts";
import { withBasePath } from "./site.ts";
import type { Sport } from "./sport.ts";
import type { GameSummary } from "./types.ts";

export type FeedTeam = {
  code: string;
  rank: number | null;
  logo: string | null;
  score: number | null;
};

export type FeedGame = {
  id: number;
  kickoff: string | null;
  /**
   * "started": past kickoff with neither a live row nor a final score yet. NFL
   * has no live feed, and its finals load the next morning.
   */
  status: "scheduled" | "started" | "live" | "final";
  /** "Final", "3rd 4:12", "Half", "Started"; null before kickoff. */
  state: string | null;
  away: FeedTeam;
  home: FeedTeam;
  href: string;
};

/** Where our NFL codes differ from ESPN's logo paths. */
const ESPN_NFL_CODE: Record<string, string> = { LA: "lar", WAS: "wsh" };

export function espnLogo(sport: Sport, team: { cfbdId: number | null; code: string }): string | null {
  if (sport === "cfb") {
    return team.cfbdId === null ? null : `https://a.espncdn.com/i/teamlogos/ncaa/500/${team.cfbdId}.png`;
  }
  const code = ESPN_NFL_CODE[team.code] ?? team.code.toLowerCase();
  return `https://a.espncdn.com/i/teamlogos/nfl/500/${code}.png`;
}

export type TeamKeys = Map<number, { cfbdId: number | null; abbreviation: string | null }>;

/**
 * One game for the strip. The live row wins over the game row for score and
 * state while the game is on: `games` gets its final score only from the
 * morning's official load.
 */
export function feedGame(
  sport: Sport,
  game: GameSummary,
  live: LiveScore | undefined,
  teams: TeamKeys,
  now: Date = new Date(),
): FeedGame {
  const kickedOff =
    !game.startTimeTbd && game.startDate !== null && new Date(game.startDate) <= now;
  const status: FeedGame["status"] =
    live?.status === "in_progress"
      ? "live"
      : game.completed || live?.status === "completed"
        ? "final"
        : kickedOff
          ? "started"
          : "scheduled";
  const fromLive = live && live.status !== "scheduled";

  const team = (side: "home" | "away"): FeedTeam => {
    const id = side === "home" ? game.homeTeamId : game.awayTeamId;
    const keys = teams.get(id);
    const code =
      (side === "home" ? game.homeAbbreviation : game.awayAbbreviation) ??
      keys?.abbreviation ??
      (side === "home" ? game.homeSchool : game.awaySchool);
    const points = fromLive
      ? side === "home" ? live.homePoints : live.awayPoints
      : side === "home" ? game.homePoints : game.awayPoints;
    return {
      code,
      rank: (side === "home" ? game.homePollRank : game.awayPollRank) ?? null,
      logo: espnLogo(sport, { cfbdId: keys?.cfbdId ?? null, code }),
      score: status === "scheduled" || status === "started" ? null : (points ?? null),
    };
  };

  return {
    id: game.gameId,
    kickoff: game.startTimeTbd ? null : game.startDate,
    status,
    state:
      status === "scheduled"
        ? null
        : status === "started"
          ? "Started"
          : fromLive
            ? liveStateLabel(live)
            : "Final",
    away: team("away"),
    home: team("home"),
    href: withBasePath(scopedHref(`/games/${game.gameId}`, { sport })),
  };
}
