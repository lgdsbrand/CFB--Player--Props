"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { GamePicker, type PickableGame } from "@/components/game-picker";

/** Same pause as the board: one reload per settled choice, not per tick. */
const GAME_PICK_DEBOUNCE_MS = 700;

/**
 * The cheat sheet's game selector (client, 2026-10-08).
 *
 * IT FILTERS NOTHING ITSELF, like `NavSelect`: it writes `game=` onto an
 * address the server built (`baseHref`, every other filter kept, no game) and
 * the server does the rest. Before hydration it is a GET form, and the ticked
 * boxes submit as repeated `game` keys, which the board's parser reads.
 */
export function SheetGamePicker({
  games,
  value,
  counts,
  baseHref,
}: {
  games: PickableGame[];
  /** Picked ids, sorted, comma-joined; "" is every game. */
  value: string;
  counts?: Record<number, number>;
  /** This sheet with every filter but the game one. */
  baseHref: string;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [chosen, setChosen] = useState(value);
  // Adopt the URL's value when it changes from elsewhere, during render, as
  // `NavSelect` does.
  const [adopted, setAdopted] = useState(value);
  if (adopted !== value) {
    setAdopted(value);
    setChosen(value);
  }
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const go = (ids: string) => {
    const url = new URL(baseHref, "http://sheet.local");
    if (ids === "") url.searchParams.delete("game");
    else url.searchParams.set("game", ids);
    startTransition(() =>
      router.push(url.pathname + url.search, { scroll: false }),
    );
  };

  const toggle = (id: number) => {
    const ids = new Set(chosen === "" ? [] : chosen.split(",").map(Number));
    if (ids.has(id)) ids.delete(id);
    else ids.add(id);
    const next = [...ids].sort((a, b) => a - b).join(",");
    setChosen(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => go(next), GAME_PICK_DEBOUNCE_MS);
  };

  const clear = () => {
    clearTimeout(timer.current);
    setChosen("");
    go("");
  };

  const action = baseHref.split("?")[0];
  const hidden = [
    ...new URLSearchParams(baseHref.split("?")[1] ?? "").entries(),
  ].filter(([name]) => name !== "game");

  return (
    // NOT `relative`: at phone width the open list spans the page's filter
    // row (which is), not this narrow form, where matchups wrapped mid-name.
    <form method="get" action={action} className="flex items-center gap-2">
      {hidden.map(([name, fieldValue]) => (
        <input key={name} type="hidden" name={name} value={fieldValue} />
      ))}
      <span className="label-caption">Games</span>
      <GamePicker
        busy={isPending}
        games={games}
        value={chosen}
        onToggle={toggle}
        onClear={clear}
        counts={counts}
      />
      <noscript>
        <button
          type="submit"
          className="border-border-subtle text-muted rounded-full border px-3 py-1 text-[0.625rem] font-bold uppercase tracking-label"
        >
          Go
        </button>
      </noscript>
    </form>
  );
}
