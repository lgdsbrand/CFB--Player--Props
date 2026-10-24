/**
 * One-off check: the index path returns exactly what the old SQL path did.
 *
 * Input is a JSON dump made against production (old SQL filters + ORDER BY,
 * and `board_index` created and called inside a rolled-back transaction).
 * Usage: node scripts/board-index-equivalence.mts <dump.json>
 */
import { readFileSync } from "node:fs";

import {
  countHomeBoard,
  countIndex,
  decodeIndex,
  matchesBoardFilters,
  sortIndexRows,
} from "../lib/core/board-index.ts";

type Combo = { filters: Record<string, unknown>; expected: number[] };
const dump = JSON.parse(readFileSync(process.argv[2], "utf8"));
const now = new Date(dump.now);
let failures = 0;

for (const { filters, expected } of dump.combos as Combo[]) {
  const index = decodeIndex(dump.index[`${filters.sport}-${filters.week}`]);
  const f = { ...filters, kickoffCutoff: filters.cutoff ? now : undefined } as never;
  const got = sortIndexRows(
    index.filter((row) => matchesBoardFilters(row, f)),
    filters.sort as never,
    now,
  ).map((row) => row.projectionId);
  const same = got.length === expected.length && got.every((id, i) => id === expected[i]);
  if (!same) failures += 1;
  const firstDiff = got.findIndex((id, i) => id !== expected[i]);
  console.log(
    same ? "MATCH" : "DIFF ",
    JSON.stringify(filters),
    `sql=${expected.length} js=${got.length}`,
    same ? "" : `first difference at ${firstDiff}`,
  );
}

const index8 = decodeIndex(dump.index["cfb-8"]);
const counts = countIndex(index8, {
  edgeThreshold: 0.05,
  syntheticBookKey: "synthetic",
  kickoffCutoff: now,
});
const sql = dump.counts.board_counts;
const js = [counts.rows, counts.withCall, counts.withBookLine, counts.withDevLine, counts.withEvenBookPrice, counts.overThreshold];
const countsSame = js.every((v, i) => v === Number(sql[i]));
if (!countsSame) failures += 1;
console.log(countsSame ? "MATCH" : "DIFF ", "board_counts", `sql=${sql} js=${js}`);

const home = countHomeBoard(index8, { edgeThreshold: 0.05, syntheticBookKey: "synthetic", kickoffCutoff: now });
const homeSame = JSON.stringify(home) === JSON.stringify(dump.counts.home);
if (!homeSame) failures += 1;
console.log(homeSame ? "MATCH" : "DIFF ", "home counts", JSON.stringify(dump.counts.home), JSON.stringify(home));

process.exit(failures ? 1 : 0);
