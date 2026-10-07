/**
 * Starters and injury reports for a game page (migration 0086).
 *
 * SPORT-AGNOSTIC CORE: rows in, display lists out. Today only the NFL has
 * rows (ESPN depth charts via nflverse, Sleeper injury designations); a
 * college game has none and its page renders no panel.
 *
 * Both tables hold one set per team per WEEK, as it stood going into that
 * week's game, so a finished game shows its own week's starters, not today's.
 */

export interface DepthRow {
  teamId: number;
  positionGroup: string;
  slot: number;
  depth: number;
  position: string;
  playerId: number | null;
  playerName: string;
  sourceAsOf: string;
}

export interface InjuryRow {
  teamId: number;
  playerId: number | null;
  playerName: string;
  position: string | null;
  status: string;
  bodyPart: string | null;
}

export interface Starter {
  label: string;
  playerId: number | null;
  playerName: string;
  injury: InjuryRow | null;
}

/** The source's offensive personnel group; the starters are drawn from it. */
export const OFFENSE_GROUP = "3WR 1TE";

/** The skill positions shown, and how many deep each goes. */
const STARTER_SLOTS: { position: string; labels: string[] }[] = [
  { position: "QB", labels: ["QB"] },
  { position: "RB", labels: ["RB", "RB2"] },
  { position: "WR", labels: ["WR", "WR", "WR"] },
  { position: "TE", labels: ["TE"] },
];

/** Designations that are about THIS game, worst first. */
export const GAME_STATUSES = ["Out", "Doubtful", "Questionable"] as const;

const nameKey = (name: string) => name.toLowerCase().replace(/[^a-z]/g, "");

/** The player's designation, matched on our id where both have one, else by name. */
export function injuryFor(
  injuries: InjuryRow[],
  teamId: number,
  playerId: number | null,
  playerName: string,
): InjuryRow | null {
  const team = injuries.filter((i) => i.teamId === teamId);
  if (playerId !== null) {
    const byId = team.find((i) => i.playerId === playerId);
    if (byId) return byId;
  }
  // By name only where one side has no id: two rows that both carry ids and
  // differ are two different players, whatever their names.
  const key = nameKey(playerName);
  const nameMatchAllowed = (i: InjuryRow) => i.playerId === null || playerId === null;
  return team.find((i) => nameMatchAllowed(i) && nameKey(i.playerName) === key) ?? null;
}

/**
 * QB, RB, RB2, three WRs and the TE, from the offense group.
 *
 * WR is three SLOTS in the source, each with its own depth order, so the
 * three receivers are depth 1 of each slot (in slot order). A player listed
 * first in two slots is shown once, and the second slot falls to its next man.
 */
export function teamStarters(
  depth: DepthRow[],
  injuries: InjuryRow[],
  teamId: number,
): Starter[] {
  const offense = depth.filter(
    (d) => d.teamId === teamId && d.positionGroup === OFFENSE_GROUP,
  );
  const shown = new Set<string>();
  const starters: Starter[] = [];
  const who = (d: DepthRow) => (d.playerId !== null ? `id:${d.playerId}` : `n:${nameKey(d.playerName)}`);

  for (const { position, labels } of STARTER_SLOTS) {
    const rows = offense.filter((d) => d.position === position);
    const slots = [...new Set(rows.map((d) => d.slot))].sort((a, b) => a - b);
    const picks: DepthRow[] = [];
    if (slots.length > 1) {
      // One player per slot, depth first.
      for (const slot of slots) {
        const next = rows
          .filter((d) => d.slot === slot && !shown.has(who(d)))
          .sort((a, b) => a.depth - b.depth)[0];
        if (next) {
          picks.push(next);
          shown.add(who(next));
        }
      }
    } else {
      // One slot: its depth order is the order (RB, RB2).
      for (const d of rows.sort((a, b) => a.depth - b.depth)) {
        if (!shown.has(who(d))) {
          picks.push(d);
          shown.add(who(d));
        }
      }
    }
    picks.slice(0, labels.length).forEach((d, i) => {
      starters.push({
        label: labels[i],
        playerId: d.playerId,
        playerName: d.playerName,
        injury: injuryFor(injuries, teamId, d.playerId, d.playerName),
      });
    });
  }
  return starters;
}

/**
 * A team's report: game designations worst first, then everyone longer term
 * (IR, PUP, suspended...) as a separate list.
 */
export function injuryReport(
  injuries: InjuryRow[],
  teamId: number,
): { game: InjuryRow[]; longTerm: InjuryRow[] } {
  const team = injuries.filter((i) => i.teamId === teamId);
  const rank = (s: string) => {
    const i = (GAME_STATUSES as readonly string[]).indexOf(s);
    return i === -1 ? GAME_STATUSES.length : i;
  };
  const byName = (a: InjuryRow, b: InjuryRow) => a.playerName.localeCompare(b.playerName);
  return {
    game: team
      .filter((i) => rank(i.status) < GAME_STATUSES.length)
      .sort((a, b) => rank(a.status) - rank(b.status) || byName(a, b)),
    longTerm: team.filter((i) => rank(i.status) === GAME_STATUSES.length).sort(byName),
  };
}

export type InjuryTone = "out" | "doubt" | "other";

export function injuryTone(status: string): InjuryTone {
  if (status === "Out") return "out";
  if (status === "Doubtful" || status === "Questionable") return "doubt";
  return "other";
}
