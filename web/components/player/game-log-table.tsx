import { Fragment } from "react";

import type { FormGame } from "@/lib/core/form";
import { formatLine } from "@/lib/core/format";
import type { GradedGame } from "@/lib/core/hit-rate";

/**
 * The game log, in two versions: graded against the line now showing, and —
 * where no book has posted one — the same games as values alone.
 *
 * The chart above it is the same data; this is the version you can read a
 * number off. Both are needed — the chart makes the shape obvious, the table
 * settles "what exactly did he do in week 6".
 *
 * GRADED AGAINST TODAY'S LINE, NOT THAT WEEK'S. That is the `threshold` basis
 * settled in CLAUDE.md §9.2: every past game is measured against the one line
 * on the board now. The alternative — each game against the line it closed at —
 * is truer and needs a paid historical-odds backfill we do not have. The column
 * header says which, because the two answer different questions.
 *
 * LAST SEASON'S GAMES SIT UNDER THEIR OWN HEADING. A topped-up NFL sample
 * (`borrowsPriorSeasonForm`) mixes two seasons, and the week column alone
 * cannot tell 2025 week 17 from a week this season has not reached.
 *
 * WHY ONE TABLE AND TWO ENTRY POINTS, where the chart is two components. Four
 * of the five columns — week, opponent, opponent rank, value — are facts about
 * the game and are identical either way; only the grade is missing. That is the
 * opposite of `FormChart`, where the ungraded version shares nothing but an
 * axis tick because colour, reference line and stepper all encode the line. The
 * ungraded column is DROPPED rather than dashed: a column of em dashes reads as
 * data we failed to load, and it is the reader's eye that has to notice the
 * difference between "no line" and "no result".
 *
 * `GradedGame` IS NOT WIDENED TO CARRY NULLS to make this work — see the note
 * on `FormGame` in `lib/core/form.ts`. Each entry point adapts its own rows at
 * the boundary, so neither type learns about the other's absence.
 */

type LogRow = {
  gameId: number;
  season: number;
  week: number;
  opponentAbbreviation: string | null;
  isHome: boolean;
  neutralSite: boolean;
  value: number;
  /** The "vs line" cell. Null on a market no book has priced. */
  grade: { label: string; hit: boolean | null } | null;
};

export function GameLogTable({
  games,
  unit,
  rankByGameId,
  season,
}: {
  games: GradedGame[];
  unit: string | null;
  /** Opponent rank vs the position AS IT STOOD that week; 1 = best defense. */
  rankByGameId: Map<number, number>;
  /** The season on screen. Earlier games get a season heading. */
  season: number;
}) {
  return (
    <LogTable
      rows={games.map((game) => ({
        gameId: game.gameId,
        season: game.season,
        week: game.week,
        opponentAbbreviation: game.opponentAbbreviation,
        isHome: game.isHome,
        neutralSite: game.neutralSite,
        value: game.value,
        grade: {
          hit: game.hit,
          label:
            game.outcome === "push"
              ? "push"
              : `${game.outcome} ${formatLine(game.line)}`,
        },
      }))}
      unit={unit}
      rankByGameId={rankByGameId}
      season={season}
      graded
    />
  );
}

/**
 * The same log for a market no book has priced.
 *
 * WHAT THIS FIXES. The panel used to render the graded table's empty state —
 * "no line to grade them against" — directly below a chart that was, by then,
 * showing bars for those very games. Accurate and unreadable: the page said it
 * could not show you something it was already showing you.
 *
 * NOTHING HERE IS SCORED, including against the model's own projection. The
 * projection is a mark on the chart and gets no column here; `lib/core/form.ts`
 * has the reasoning.
 */
export function FormLogTable({
  games,
  unit,
  rankByGameId,
  season,
}: {
  games: FormGame[];
  unit: string | null;
  rankByGameId: Map<number, number>;
  season: number;
}) {
  return (
    <LogTable
      rows={games.map((game) => ({
        gameId: game.gameId,
        season: game.season,
        week: game.week,
        opponentAbbreviation: game.opponentAbbreviation,
        isHome: game.isHome,
        neutralSite: game.neutralSite,
        value: game.value,
        grade: null,
      }))}
      unit={unit}
      rankByGameId={rankByGameId}
      season={season}
      graded={false}
    />
  );
}

function LogTable({
  rows,
  unit,
  rankByGameId,
  season,
  graded,
}: {
  rows: LogRow[];
  unit: string | null;
  rankByGameId: Map<number, number>;
  season: number;
  /** Whether the "vs line" column exists at all. Not inferred from the rows,
      which cannot tell an ungraded market from an empty one. */
  graded: boolean;
}) {
  if (rows.length === 0) {
    return (
      <p className="text-dim text-xs">
        No completed games this season before this week.
      </p>
    );
  }

  const columns = graded ? 5 : 4;

  return (
    <div className="overflow-x-auto">
      {/*
        THE MINIMUM WIDTH FOLLOWS THE COLUMN COUNT. 26rem is what the grade
        pill ("under 255.5") needs; carrying it into the four-column version
        pushed the VALUE — the one number the log exists for — off the right of
        a 390px phone and behind a horizontal scroll, leaving a reader with week
        and opponent and nothing else. Measured in a screenshot; nothing in the
        toolchain reports it, since the table scrolls rather than overflowing
        the document.
      */}
      <table
        className={
          "w-full border-collapse text-left text-xs " +
          (graded ? "min-w-[26rem]" : "min-w-[19rem]")
        }
      >
        <thead>
          <tr className="text-dim [&>th]:px-2 [&>th]:py-1.5 [&>th]:font-semibold [&>th]:uppercase [&>th]:tracking-label [&>th]:text-[0.625rem]">
            <th>Wk</th>
            <th>Opp</th>
            <th className="text-right" title="Opponent rank vs this position entering that week — 1 is the best defense">
              Rk
            </th>
            <th className="text-right">{unit ?? "Value"}</th>
            {graded ? <th className="text-right">vs line</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((game, index) => {
            const rank = rankByGameId.get(game.gameId);
            const prior = game.season < season;
            const startsSeason =
              prior && rows[index - 1]?.season !== game.season;
            return (
              <Fragment key={game.gameId}>
                {startsSeason ? (
                  <tr className="border-border-subtle border-t">
                    <td
                      colSpan={columns}
                      className="label-caption px-2 pb-1 pt-2.5"
                    >
                      {game.season} season · filling in while {season} is short
                    </td>
                  </tr>
                ) : null}
                <tr
                  className={
                    "border-border-subtle border-t [&>td]:px-2 [&>td]:py-1.5" +
                    (prior ? " opacity-75" : "")
                  }
                >
                  <td className="text-muted tabular-nums">{game.week}</td>
                  <td>
                    <span className="text-dim mr-1">
                      {game.neutralSite ? "N" : game.isHome ? "vs" : "@"}
                    </span>
                    {game.opponentAbbreviation ?? "—"}
                  </td>
                  <td className="text-muted text-right tabular-nums">
                    {rank ?? "—"}
                  </td>
                  <td className="text-right font-semibold tabular-nums">
                    {game.value}
                  </td>
                  {game.grade ? (
                    <td className="text-right">
                      <span
                        className={
                          "pill " +
                          (game.grade.hit === null
                            ? "bg-panel text-muted"
                            : game.grade.hit
                              ? "bg-positive/15 text-positive"
                              : "bg-negative/15 text-negative")
                        }
                      >
                        {game.grade.label}
                      </span>
                    </td>
                  ) : null}
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
