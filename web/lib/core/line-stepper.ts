// RELATIVE, WITH THE EXTENSION, and that is not a style choice. `npm test` runs
// node's own test runner over these files, which strips types but does not
// resolve the `@/` alias — every other core module gets away with `@/` because
// its imports are type-only and erased. This one is a RUNTIME import, so it has
// to be a real path node can follow.
import { didHit, outcomeFor } from "./hit-rate.ts";
import type { BetSide } from "@/lib/core/types";

/**
 * Stepping the hit-rate chart's line up and down.
 *
 * THE LADDER'S SIBLING, AND THE OPPOSITE HALF OF THE QUESTION. The alternate-line
 * ladder answers "what does the model think at 80.5?" — forward-looking, from the
 * projected distribution. This answers "what would his last ten games have done
 * at 80.5?" — backward-looking, from what actually happened. The client asked for
 * both and they belong beside each other, but they are not the same claim and
 * must never be presented as one.
 *
 * IT IS A LOOKING GLASS, NOT A SIGNAL. Ten games re-graded at a line nobody
 * posted is a small sample, and stepping until the number looks good is the
 * definition of cherry-picking. The panel says so; see `line-stepper` copy in
 * `hit-rate-chart.tsx`. This module deliberately computes no probability and no
 * edge — a hit count is a fact about the past and stays labelled as one.
 *
 * THE CALLED SIDE DOES NOT MOVE WITH THE LINE. Colour means "did the called side
 * win", and the call was made at the posted line. Re-deciding the side at each
 * step would redraw the chart green wherever you pushed it, which is exactly the
 * flattering-number problem stated above.
 */

/** How far the line may be pushed in either direction. */
export const MAX_OFFSET = 6;

/**
 * The line at `offset` steps from the posted one.
 *
 * Rounded to two decimals because `markets.ladder_step` is `numeric(5,2)` and
 * repeated float addition drifts — six steps of 0.25 lands on 1.5000000000000002
 * without it, which then renders as a line no book would ever post.
 *
 * FLOORED AT ZERO. Every market here is a count or a yardage, so a negative line
 * is not a harder bet, it is a meaningless one: every game clears it and the
 * chart goes uniformly green while saying nothing.
 */
export function steppedLine(base: number, offset: number, step: number): number {
  const raw = base + offset * step;
  return Math.max(0, Math.round(raw * 100) / 100);
}

/**
 * Offsets that actually move the line, given the floor at zero.
 *
 * Returned as a range so the control can disable its own buttons rather than
 * offering a step that silently does nothing — a `-` that stops responding with
 * no explanation reads as a broken button.
 */
export function offsetBounds(
  base: number,
  step: number,
): { min: number; max: number } {
  let min = 0;
  while (min > -MAX_OFFSET && steppedLine(base, min - 1, step) > 0) min -= 1;
  return { min, max: MAX_OFFSET };
}

export type SteppedPoint<T extends { value: number }> = T & {
  hit: boolean | null;
};

export function regrade<T extends { value: number }>(
  points: T[],
  line: number,
  side: BetSide,
): SteppedPoint<T>[] {
  return points.map((point) => ({
    ...point,
    hit: didHit(outcomeFor(point.value, line), side),
  }));
}

export type Tally = {
  hits: number;
  /** Pushes excluded, so this can be below the number of games. */
  decided: number;
  pushes: number;
  /** Null when nothing was decided; never silently 0, which reads as 0%. */
  rate: number | null;
};

export function tally(points: { hit: boolean | null }[]): Tally {
  let hits = 0;
  let pushes = 0;
  for (const point of points) {
    if (point.hit === null) pushes += 1;
    else if (point.hit) hits += 1;
  }
  const decided = points.length - pushes;
  return { hits, decided, pushes, rate: decided === 0 ? null : hits / decided };
}
