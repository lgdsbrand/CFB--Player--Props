/**
 * A head coach's record for the game page, summed from completed seasons.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3); the rows come from CFBD for college
 * (migration 0084) and the NFL simply has none.
 *
 * BEFORE THIS SEASON, ALWAYS. The page already shows this season to date from
 * our own `games`; adding the season in progress here as well would count
 * those games twice the moment CFBD caught up on them, and whether it has is
 * a matter of when it last updated. So the season being played is excluded
 * here by construction, whatever its row holds.
 */

export type WinLoss = { w: number; l: number };

export type CoachSeason = {
  coachId: number;
  season: number;
  school: string;
  teamId: number | null;
  wins: number;
  losses: number;
  /** Null where CFBD has not attributed the season's splits. */
  conference: WinLoss | null;
  home: WinLoss | null;
  away: WinLoss | null;
  neutral: WinLoss | null;
  postseason: WinLoss | null;
};

export type HeadCoach = {
  coachId: number;
  firstName: string;
  lastName: string;
  hireDate: string | null;
};

export type CoachSummary = {
  name: string;
  hireDate: string | null;
  /** 1 in a coach's first season at the school, counting the current one. */
  seasonAtSchool: number;
  atSchool: {
    seasons: number;
    record: WinLoss;
    conference: WinLoss | null;
    postseason: WinLoss | null;
  };
  career: {
    seasons: number;
    schools: number;
    record: WinLoss;
    postseason: WinLoss | null;
  };
};

/**
 * Sum a split across seasons. Null when NO season recorded it — "not
 * recorded" must not print as 0-0 — but a mix sums only the seasons that did.
 */
function sumSplit(
  seasons: CoachSeason[],
  pick: (season: CoachSeason) => WinLoss | null,
): WinLoss | null {
  const recorded = seasons.map(pick).filter((s): s is WinLoss => s !== null);
  if (recorded.length === 0) return null;
  return recorded.reduce((a, b) => ({ w: a.w + b.w, l: a.l + b.l }), { w: 0, l: 0 });
}

function sumRecord(seasons: CoachSeason[]): WinLoss {
  return seasons.reduce((a, s) => ({ w: a.w + s.wins, l: a.l + s.losses }), { w: 0, l: 0 });
}

/**
 * The coach of `teamId` in `season`: the most recent hire when a mid-season
 * change left two. Null when there is none on record.
 */
export function currentCoach(coaches: HeadCoach[]): HeadCoach | null {
  if (coaches.length === 0) return null;
  return [...coaches].sort((a, b) => (b.hireDate ?? "").localeCompare(a.hireDate ?? ""))[0];
}

export function summarizeCoach(
  coach: HeadCoach,
  seasons: CoachSeason[],
  teamId: number,
  season: number,
): CoachSummary {
  const own = seasons.filter((s) => s.coachId === coach.coachId);
  const completed = own.filter((s) => s.season < season);
  const here = completed.filter((s) => s.teamId === teamId);
  const yearsHere = new Set(
    own.filter((s) => s.teamId === teamId && s.season <= season).map((s) => s.season),
  );
  return {
    name: `${coach.firstName} ${coach.lastName}`.trim(),
    hireDate: coach.hireDate,
    // At least 1: the coach is in post this season even if CFBD has no row
    // for it yet.
    seasonAtSchool: Math.max(yearsHere.size + (yearsHere.has(season) ? 0 : 1), 1),
    atSchool: {
      seasons: new Set(here.map((s) => s.season)).size,
      record: sumRecord(here),
      conference: sumSplit(here, (s) => s.conference),
      postseason: sumSplit(here, (s) => s.postseason),
    },
    career: {
      seasons: new Set(completed.map((s) => s.season)).size,
      schools: new Set(completed.map((s) => s.school)).size,
      record: sumRecord(completed),
      postseason: sumSplit(completed, (s) => s.postseason),
    },
  };
}
