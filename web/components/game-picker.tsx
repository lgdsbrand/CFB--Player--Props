"use client";

import { useEffect, useRef } from "react";

import { formatKickoff } from "@/lib/core/format";
import type { GameSummary } from "@/lib/core/types";

/** The fields of a game the picker shows. */
export type PickableGame = Pick<
  GameSummary,
  "gameId" | "awayAbbreviation" | "homeAbbreviation" | "startDate"
>;

/** `"3,7"` -> `[3, 7]`; `""` -> `[]`. The URL's own form of a game list. */
export function idList(value: string): number[] {
  return value === "" ? [] : value.split(",").map(Number).filter(Number.isFinite);
}

/**
 * Several games at once (client, 2026-10-06). Shared by the props board and
 * the cheat sheets.
 *
 * A NATIVE <details>, so it opens and its checkboxes submit with a GET form
 * before the script has loaded — the same fallback every other field on the
 * board keeps. The checkboxes are named `game`, and the parser reads repeated
 * keys.
 *
 * Closes on a click outside it, as a native select does; it stays open while
 * games are being ticked, which is the point of a multi-select.
 *
 * `counts`, where given, prints how many entries each game holds, so a reader
 * can see which games have any before picking one (cheat sheets, client
 * 2026-10-08: "to see if there's any 80 or 100% props"). A game missing from
 * the record shows 0; no record at all shows no numbers.
 */
export function GamePicker({
  busy,
  games,
  value,
  onToggle,
  onClear,
  counts,
}: {
  /** The page is reloading: dim the button, never the open list. */
  busy: boolean;
  games: PickableGame[];
  value: string;
  onToggle: (id: number) => void;
  onClear: () => void;
  counts?: Record<number, number>;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  const selected = new Set(idList(value));

  useEffect(() => {
    const close = (event: MouseEvent) => {
      const element = ref.current;
      if (element?.open && !element.contains(event.target as Node)) {
        element.open = false;
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const only =
    selected.size === 1 ? games.find((game) => selected.has(game.gameId)) : undefined;
  const summary =
    selected.size === 0
      ? "All games"
      : only
        ? `${only.awayAbbreviation} @ ${only.homeAbbreviation}`
        : `${selected.size} game${selected.size === 1 ? "" : "s"}`;

  return (
    // Positioned from sm up only. At phone width the Games field sits on the
    // right of the row, and a panel opened from it ran off the screen; there
    // the panel spans the nearest positioned ancestor instead.
    <details ref={ref} className="sm:relative">
      <summary
        className={
          "bg-panel-inset border-border-subtle text-ink focus-visible:border-accent-cyan/60 flex min-w-40 cursor-pointer list-none items-center justify-between gap-3 rounded-lg border px-2.5 py-1.5 text-sm outline-none transition-opacity [&::-webkit-details-marker]:hidden" +
          (busy ? " opacity-60" : "")
        }
      >
        <span className="truncate">{summary}</span>
        <span aria-hidden className="text-dim text-xs">
          ▾
        </span>
      </summary>
      <div className="bg-panel border-border-subtle absolute inset-x-0 z-20 mt-1 flex max-h-80 flex-col sm:right-auto sm:w-72 overflow-y-auto rounded-lg border p-1 shadow-xl">
        <div className="flex items-center">
          <button
            type="button"
            onClick={onClear}
            disabled={selected.size === 0}
            className="text-accent-cyan hover:bg-panel-inset flex-1 rounded px-2 py-1.5 text-left text-xs font-semibold uppercase tracking-label disabled:text-dim disabled:hover:bg-transparent"
          >
            All games
          </button>
          {counts ? (
            <span className="label-caption shrink-0 px-2">Entries</span>
          ) : null}
        </div>
        {games.length === 0 ? (
          <p className="text-dim px-2 py-1.5 text-xs">No games left to kick off.</p>
        ) : null}
        {games.map((game) => {
          const count = counts ? (counts[game.gameId] ?? 0) : undefined;
          return (
            <label
              key={game.gameId}
              className="hover:bg-panel-inset flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm"
            >
              <input
                type="checkbox"
                name="game"
                value={game.gameId}
                checked={selected.has(game.gameId)}
                onChange={() => onToggle(game.gameId)}
                className="accent-accent-cyan"
              />
              <span className="text-ink whitespace-nowrap font-semibold">
                {game.awayAbbreviation} @ {game.homeAbbreviation}
              </span>
              <span className="text-dim ml-auto shrink-0 text-xs">
                {formatKickoff(game.startDate)}
              </span>
              {count !== undefined ? (
                <span
                  className={
                    "w-6 shrink-0 text-right text-xs font-bold tabular-nums " +
                    (count > 0 ? "text-accent-cyan" : "text-dim")
                  }
                  title={`${count} entr${count === 1 ? "y" : "ies"} on this sheet`}
                >
                  {count}
                </span>
              ) : null}
            </label>
          );
        })}
      </div>
    </details>
  );
}
