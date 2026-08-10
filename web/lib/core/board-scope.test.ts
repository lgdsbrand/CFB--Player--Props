import assert from "node:assert/strict";
import { test } from "node:test";

import { offenseOnBoard, type OffenseScope } from "./board-scope.ts";

const sec: OffenseScope = { conferenceName: "SEC", conferenceIsDisplayed: true };
const sunBelt: OffenseScope = {
  conferenceName: "Sun Belt",
  conferenceIsDisplayed: false,
};
const fcsVisitor: OffenseScope = {
  conferenceName: null,
  conferenceIsDisplayed: false,
};

test("with no filter, the scope is the displayed conferences and not everyone", () => {
  assert.equal(offenseOnBoard(sec, undefined), true);
  // The regression this module exists for. A Sun Belt offense passing here is
  // what put 17 of 20 weekly-target links on an empty board: the panel offered
  // the matchup, the board could never show it.
  assert.equal(offenseOnBoard(sunBelt, undefined), false);
});

test("an explicit conference filter wins over the displayed default", () => {
  assert.equal(offenseOnBoard(sec, "SEC"), true);
  assert.equal(offenseOnBoard(sec, "Big Ten"), false);
  // A reader who deliberately filters to a non-displayed conference gets what
  // they asked for, rather than the displayed default silently overriding it.
  assert.equal(offenseOnBoard(sunBelt, "Sun Belt"), true);
});

test("teams the directory cannot resolve are out of scope, not in it", () => {
  assert.equal(offenseOnBoard(undefined, undefined), false);
  assert.equal(offenseOnBoard(undefined, "SEC"), false);
});

test("a team with no conference that season is excluded", () => {
  assert.equal(offenseOnBoard(fcsVisitor, undefined), false);
  // Guards a plausible refactor: matching null against an absent filter would
  // read as "no conference required" and let every FCS visitor back in.
  assert.equal(offenseOnBoard(fcsVisitor, "SEC"), false);
});
