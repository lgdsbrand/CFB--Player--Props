/**
 * Tests for the cheat sheet's inclusion and ordering rules.
 *
 * THE ONES THAT MATTER are the three ways this page could mislead while looking
 * perfectly healthy: a 100% streak over two games, a streak that quietly
 * contradicts the model's own call on the same prop, and "has failed to score
 * in five straight" presented as a play. Each has a test below, because none of
 * them would ever throw.
 *
 * The fixture numbers are real rows from 2025 week 8 on production, taken from
 * `v_cheat_sheet` — including Taylen Green, who is 5-0 to the over on rushing
 * yards while the model calls the under at 53%.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  agreement,
  cheatSections,
  CHEAT_TIERS,
  compareRows,
  emptyReason,
  formatRecord,
  minDecidedFor,
  qualifies,
  sideLabel,
  type CheatSheetRow,
} from "./cheat-sheet.ts";

function row(overrides: Partial<CheatSheetRow> = {}): CheatSheetRow {
  return {
    projectionId: 1,
    season: 2025,
    week: 8,
    playerId: 100,
    playerName: "Taylen Green",
    positionGroup: "QB",
    teamId: 8,
    teamSchool: "Arkansas",
    teamAbbreviation: "ARK",
    teamColor: "#9d2235",
    teamAltColor: "#ffffff",
    opponentSchool: "Auburn",
    opponentAbbreviation: "AUB",
    opponentRankVsPosition: 44,
    gameId: 900,
    startDate: "2025-10-18T16:00:00+00:00",
    isHome: true,
    neutralSite: false,
    marketKey: "rush_yards",
    marketLabel: "RUSH YDS",
    marketEmoji: "🏈",
    isBinary: false,
    line: 52.5,
    modelSide: "under",
    displayConfidence: 0.53,
    edge: null,
    hasCall: true,
    hasBookLine: true,
    sportsbookKey: "betonlineag",
    windowSize: 5,
    decided: 5,
    pushes: 0,
    hits: 5,
    hitSide: "over",
    hitRate: 1,
    ...overrides,
  };
}

test("minDecidedFor puts a real floor under both windows", () => {
  assert.equal(minDecidedFor(5), 4);
  assert.equal(minDecidedFor(10), 6);
});

test("a perfect streak over too few games does not qualify", () => {
  // The failure this rule exists for: in week 3 everyone who has played twice
  // is on a 100% streak, and without a floor the perfect list is the slate.
  const thin = row({ decided: 2, hits: 2, hitRate: 1 });
  assert.equal(qualifies(thin), false);

  const enough = row({ decided: 4, hits: 4, hitRate: 1 });
  assert.equal(qualifies(enough), true);
});

test("below 80% never reaches the sheet", () => {
  assert.equal(qualifies(row({ decided: 5, hits: 3, hitRate: 0.6 })), false);
  assert.equal(qualifies(row({ decided: 5, hits: 4, hitRate: 0.8 })), true);
});

test("a binary market's UNDER side is excluded, its OVER side is not", () => {
  // "Has failed to score in five straight" is true of most of the slate.
  const noScore = row({ isBinary: true, marketKey: "anytime_td", hitSide: "under" });
  assert.equal(qualifies(noScore), false);

  const scored = row({ isBinary: true, marketKey: "anytime_td", hitSide: "over" });
  assert.equal(qualifies(scored), true);

  // The exclusion is about binary markets only — a receiving-yards under is a
  // perfectly ordinary entry and must survive.
  assert.equal(qualifies(row({ hitSide: "under" })), true);
});

test("agreement reports where the model contradicts the streak", () => {
  // The real Taylen Green row: 5-0 over, model calls under.
  assert.equal(agreement(row()), "disagrees");
  assert.equal(agreement(row({ modelSide: "over" })), "agrees");
  assert.equal(
    agreement(row({ hasCall: false, modelSide: null })),
    "no-call",
  );
  // hasCall false with a stale side still reads as no call: the pick is what
  // makes a call, not the presence of a side on the row.
  assert.equal(agreement(row({ hasCall: false })), "no-call");
});

test("tiers are exclusive so the counts mean what they say", () => {
  const sections = cheatSections([
    row({ projectionId: 1, hitRate: 1, hits: 5, decided: 5 }),
    row({ projectionId: 2, hitRate: 0.8, hits: 4, decided: 5 }),
    row({ projectionId: 3, hitRate: 0.6, hits: 3, decided: 5 }),
  ]);

  assert.deepEqual(
    sections.map((section) => [section.tier.key, section.rows.length]),
    [
      ["perfect", 1],
      ["strong", 1],
    ],
  );
  // The 100% row must not be counted twice.
  assert.equal(sections[1].rows[0].projectionId, 2);
  // And the 60% row is on neither list.
  assert.equal(
    sections.flatMap((s) => s.rows).some((r) => r.projectionId === 3),
    false,
  );
});

test("ordering prefers the longer sample before the model's confidence", () => {
  const fiveOfFive = row({ projectionId: 1, decided: 5, hits: 5, displayConfidence: 0.51 });
  const fourOfFour = row({ projectionId: 2, decided: 4, hits: 4, displayConfidence: 0.93 });

  // Both are 100%. The extra game is evidence about THIS question; the
  // confidence is a different claim about a different one.
  assert.deepEqual(
    [fourOfFour, fiveOfFive].sort(compareRows).map((r) => r.projectionId),
    [1, 2],
  );
});

test("ordering is stable when everything else ties", () => {
  const a = row({ projectionId: 7 });
  const b = row({ projectionId: 3 });
  assert.deepEqual([a, b].sort(compareRows).map((r) => r.projectionId), [3, 7]);
});

test("formatRecord prints wins and losses, not the denominator", () => {
  assert.equal(formatRecord(5, 5), "5-0");
  assert.equal(formatRecord(4, 5), "4-1");
});

test("a binary market says SCORED rather than OVER 0.5", () => {
  assert.equal(sideLabel(row({ isBinary: true, hitSide: "over" })), "SCORED");
  assert.equal(sideLabel(row({ isBinary: true, hitSide: "under" })), "NO SCORE");
  assert.equal(sideLabel(row({ hitSide: "over" })), "OVER");
  assert.equal(sideLabel(row({ hitSide: "under" })), "UNDER");
});

test("an empty sheet distinguishes its four causes", () => {
  const why = emptyReason;

  // Week 1, every season: the props exist, nobody has played yet.
  assert.equal(
    why({ pricedProps: 2848, playedGames: 0, weeksPlayed: 0, windowSize: 5 }),
    "no-games",
  );

  // The live slate today — no lines AND no games. "Nobody has played" wins,
  // because it is the answer that resolves on a date the reader can look up.
  assert.equal(
    why({ pricedProps: 0, playedGames: 0, weeksPlayed: 0, windowSize: 5 }),
    "no-games",
  );

  // Weeks 2-4: games have been played, but not four of them. This was
  // reporting "an ordinary week", which is false — it is an impossible one.
  for (const week of [2, 3, 4]) {
    assert.equal(
      why({
        pricedProps: 2848,
        playedGames: 9405,
        weeksPlayed: week - 1,
        windowSize: 5,
      }),
      "too-early",
      `week ${week}`,
    );
  }
  // Week 5 is the first that can produce an entry at L5, so it stops being an
  // excuse and the ordinary answers take over.
  assert.equal(
    why({ pricedProps: 2848, playedGames: 9405, weeksPlayed: 4, windowSize: 5 }),
    "nothing-clears",
  );
  // L10 needs six, so it stays too early two weeks longer.
  assert.equal(
    why({ pricedProps: 2848, playedGames: 9405, weeksPlayed: 4, windowSize: 10 }),
    "too-early",
  );

  // Mid-season, before the books post for the week.
  assert.equal(
    why({ pricedProps: 0, playedGames: 9405, weeksPlayed: 7, windowSize: 5 }),
    "no-lines",
  );

  // The genuinely ordinary empty, and the only one a wider window could fix.
  assert.equal(
    why({ pricedProps: 2848, playedGames: 9405, weeksPlayed: 7, windowSize: 5 }),
    "nothing-clears",
  );
});

test("the tier floors are the two the client asked for", () => {
  assert.deepEqual(CHEAT_TIERS.map((tier) => tier.min), [1, 0.8]);
});
