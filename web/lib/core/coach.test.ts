import assert from "node:assert/strict";
import { test } from "node:test";

import { currentCoach, summarizeCoach, type CoachSeason, type HeadCoach } from "./coach.ts";

const TROY = 77;
const PARKER: HeadCoach = { coachId: 628, firstName: "Gerad", lastName: "Parker", hireDate: "2023-12-18" };

function season(overrides: Partial<CoachSeason>): CoachSeason {
  return {
    coachId: 628,
    season: 2025,
    school: "Troy",
    teamId: TROY,
    wins: 0,
    losses: 0,
    conference: null,
    home: null,
    away: null,
    neutral: null,
    postseason: null,
    ...overrides,
  };
}

// Gerad Parker as CFBD recorded him on 2026-10-07.
const SEASONS = [
  season({ season: 2016, school: "Purdue", teamId: 12, wins: 0, losses: 6 }),
  season({ season: 2024, wins: 4, losses: 8, conference: { w: 3, l: 5 }, postseason: { w: 0, l: 0 } }),
  season({ season: 2025, wins: 8, losses: 6, conference: { w: 6, l: 2 }, postseason: { w: 0, l: 1 } }),
  // The season in progress, which CFBD had not updated: excluded regardless.
  season({ season: 2026, wins: 3, losses: 1 }),
];

test("records are summed from completed seasons only", () => {
  const s = summarizeCoach(PARKER, SEASONS, TROY, 2026);
  assert.deepEqual(s.atSchool.record, { w: 12, l: 14 });
  assert.equal(s.atSchool.seasons, 2);
  assert.deepEqual(s.career.record, { w: 12, l: 20 });
  assert.equal(s.career.seasons, 3);
  assert.equal(s.career.schools, 2);
});

test("this is his third season at the school, counting the current one", () => {
  assert.equal(summarizeCoach(PARKER, SEASONS, TROY, 2026).seasonAtSchool, 3);
  // No 2026 row yet: still the third.
  assert.equal(summarizeCoach(PARKER, SEASONS.slice(0, 3), TROY, 2026).seasonAtSchool, 3);
});

test("a split recorded in no season is null, not 0-0; a partial mix sums what exists", () => {
  const s = summarizeCoach(PARKER, SEASONS, TROY, 2026);
  assert.deepEqual(s.atSchool.conference, { w: 9, l: 7 });
  assert.deepEqual(s.atSchool.postseason, { w: 0, l: 1 });
  // Purdue 2016 had no splits; the Troy seasons did.
  assert.deepEqual(s.career.postseason, { w: 0, l: 1 });
  const onlyPurdue = summarizeCoach(PARKER, SEASONS.slice(0, 1), 12, 2017);
  assert.equal(onlyPurdue.atSchool.conference, null);
});

test("a first-year coach has an empty record at the school, not a missing one", () => {
  const s = summarizeCoach(PARKER, SEASONS.slice(0, 1), TROY, 2026);
  assert.equal(s.seasonAtSchool, 1);
  assert.deepEqual(s.atSchool.record, { w: 0, l: 0 });
  assert.equal(s.atSchool.seasons, 0);
});

test("a mid-season change shows the most recent hire", () => {
  const interim: HeadCoach = { coachId: 9, firstName: "Interim", lastName: "Coach", hireDate: "2026-10-01" };
  assert.equal(currentCoach([PARKER, interim])?.coachId, 9);
  assert.equal(currentCoach([]), null);
});
