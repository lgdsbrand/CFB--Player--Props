import assert from "node:assert/strict";
import { test } from "node:test";

import {
  injuryFor,
  injuryReport,
  injuryTone,
  teamStarters,
  type DepthRow,
  type InjuryRow,
} from "./starters.ts";

const KC = 12;
const BUF = 4;

function d(position: string, slot: number, depth: number, name: string, playerId: number | null = null): DepthRow {
  return {
    teamId: KC,
    positionGroup: "3WR 1TE",
    slot,
    depth,
    position,
    playerId,
    playerName: name,
    sourceAsOf: "2026-10-06T14:08:49Z",
  };
}

function inj(name: string, status: string, playerId: number | null = null, teamId = KC): InjuryRow {
  return { teamId, playerId, playerName: name, position: "WR", status, bodyPart: "Knee" };
}

const DEPTH: DepthRow[] = [
  d("QB", 9, 1, "Patrick Mahomes", 1),
  d("QB", 9, 2, "Justin Fields", 2),
  d("RB", 11, 1, "Kenneth Walker III", 3),
  d("RB", 11, 2, "Emmett Johnson", 4),
  d("RB", 11, 3, "Brashard Smith", 5),
  d("WR", 1, 1, "Rashee Rice", 6),
  d("WR", 2, 1, "Rashee Rice", 6), // listed first in two slots
  d("WR", 2, 2, "Xavier Worthy", 7),
  d("WR", 8, 1, "Jalen Royals", null),
  d("TE", 10, 1, "Travis Kelce", 9),
  d("LT", 3, 1, "A Tackle", 10),
  { ...d("QB", 9, 1, "Someone Else", 11), teamId: BUF },
  { ...d("QB", 9, 1, "Defense Name", 12), positionGroup: "Base 3-4 D" },
];

test("starters are QB, RB, RB2, a WR per slot and the TE, in that order", () => {
  const starters = teamStarters(DEPTH, [], KC);
  assert.deepEqual(
    starters.map((s) => `${s.label} ${s.playerName}`),
    [
      "QB Patrick Mahomes",
      "RB Kenneth Walker III",
      "RB2 Emmett Johnson",
      "WR Rashee Rice",
      // Rice is first in slot 2 as well: shown once, and slot 2 falls to Worthy.
      "WR Xavier Worthy",
      "WR Jalen Royals",
      "TE Travis Kelce",
    ],
  );
});

test("a starter carries his designation, by id, or by name when one side has no id", () => {
  const injuries = [inj("Xavier Worthy", "Questionable", 7), inj("Jalen Royals", "Out", 99)];
  const starters = teamStarters(DEPTH, injuries, KC);
  assert.equal(starters.find((s) => s.playerName === "Xavier Worthy")?.injury?.status, "Questionable");
  // Royals has no id on the depth chart, so the name decides.
  assert.equal(starters.find((s) => s.playerName === "Jalen Royals")?.injury?.status, "Out");
  assert.equal(starters.find((s) => s.playerName === "Patrick Mahomes")?.injury, null);
});

test("two rows with different ids are different players whatever the name", () => {
  assert.equal(injuryFor([inj("Mike Smith", "Out", 50)], KC, 51, "Mike Smith"), null);
  // ... and another team's report is never read.
  assert.equal(injuryFor([inj("Mike Smith", "Out", null, BUF)], KC, null, "Mike Smith"), null);
});

test("the report lists game designations worst first, then the long term", () => {
  const report = injuryReport(
    [
      inj("Zed", "Questionable"),
      inj("Amy", "Out"),
      inj("Bo", "IR"),
      inj("Cy", "Doubtful"),
      inj("Al", "PUP"),
      inj("Other Team", "Out", null, BUF),
    ],
    KC,
  );
  assert.deepEqual(report.game.map((i) => i.playerName), ["Amy", "Cy", "Zed"]);
  assert.deepEqual(report.longTerm.map((i) => i.playerName), ["Al", "Bo"]);
});

test("tone: Out is red, Doubtful and Questionable amber, the rest muted", () => {
  assert.equal(injuryTone("Out"), "out");
  assert.equal(injuryTone("Doubtful"), "doubt");
  assert.equal(injuryTone("Questionable"), "doubt");
  assert.equal(injuryTone("IR"), "other");
  assert.equal(injuryTone("COV"), "other");
});
