"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import {
  BOARD_PATH,
  boardHref,
  hiddenFields,
  resetBoardHref,
  type BoardParams,
} from "@/lib/core/board-params";
import { formatKickoff } from "@/lib/core/format";
import type { Conference } from "@/lib/core/types";
import type { GameSummary } from "@/lib/core/types";

/**
 * The board's field filters — search, game, conference, confidence, opponent
 * rank — applying on change instead of behind an Apply button.
 *
 * WHY THIS IS THE ONE CLIENT COMPONENT ON THE BOARD. Everything else here is a
 * server-rendered link, and the filters deliberately live in the URL because a
 * week exceeds PostgREST's row cap — filtering in the browser would show the
 * wrong answer, not merely a slow one (see `lib/core/board-params.ts`). That
 * constraint is unchanged. This component does not filter anything itself: it
 * only writes the same URL the links write, and the server still runs every
 * predicate. Typing narrows the whole slate, not the 25 cards on screen.
 *
 * THE BUG THIS ALSO FIXES. These fields were uncontrolled, set with
 * `defaultValue`. React applies that on mount only, and Reset navigated
 * client-side without remounting the form — so the URL cleared, the rows
 * cleared, and the typed name and chosen dropdowns stayed visibly in place.
 * The board was right and looked broken. Controlled values keyed off the URL
 * cannot drift from it that way.
 */

/** Long enough that a typed name is one query, short enough to feel live. */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * Ticking games waits for the reader to finish. Each settled choice is one
 * board render and one history entry, not one per checkbox.
 */
const GAME_PICK_DEBOUNCE_MS = 700;

type Fields = {
  search: string;
  /** Picked game ids, sorted, comma-joined — the URL's own form. "" is all. */
  game: string;
  conference: string;
  conf: string;
  rank: string;
};

function fieldsFromParams(params: BoardParams): Fields {
  return {
    search: params.search ?? "",
    game: params.games?.join(",") ?? "",
    conference: params.conference ?? "",
    conf: params.minConfidence?.toString() ?? "",
    rank: params.minOpponentRank?.toString() ?? "",
  };
}

function sameFields(a: Fields, b: Fields): boolean {
  return (
    a.search === b.search &&
    a.game === b.game &&
    a.conference === b.conference &&
    a.conf === b.conf &&
    a.rank === b.rank
  );
}

/** Empty string means "no filter", and the URL builder drops undefined. */
function orUndefined(value: string): string | undefined {
  return value === "" ? undefined : value;
}

function idList(value: string): number[] {
  return value === "" ? [] : value.split(",").map(Number).filter(Number.isFinite);
}

function numberOrUndefined(value: string): number | undefined {
  if (value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function FilterFields({
  params,
  conferences,
  games,
  showsCalls = true,
}: {
  params: BoardParams;
  conferences: Conference[];
  games: GameSummary[];
  /** See `BoardControls` — a filter that cannot apply is not rendered. */
  showsCalls?: boolean;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [fields, setFields] = useState(() => fieldsFromParams(params));

  // What we last wrote to the URL. The sync effect below compares against this
  // rather than against `fields`, so our own debounced write does not bounce
  // back and overwrite whatever has been typed since.
  const written = useRef(fields);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Adopt the URL when it changed from somewhere else: a position pill, the
  // back button, a link someone was sent. Without this the fields would keep
  // showing state the board no longer has.
  useEffect(() => {
    const incoming = fieldsFromParams(params);
    if (!sameFields(incoming, written.current)) {
      written.current = incoming;
      setFields(incoming);
    }
  }, [params]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const navigate = (next: Fields, { replace }: { replace: boolean }) => {
    written.current = next;
    const href = boardHref(params, {
      search: orUndefined(next.search),
      games: next.game === "" ? undefined : idList(next.game),
      conference: orUndefined(next.conference),
      minConfidence: numberOrUndefined(next.conf),
      minOpponentRank: numberOrUndefined(next.rank),
    });
    startTransition(() => {
      // `scroll: false` throughout — re-running a filter must not throw the
      // reader back to the top of the board while they are still typing.
      if (replace) router.replace(href, { scroll: false });
      else router.push(href, { scroll: false });
    });
  };

  /** Dropdowns are discrete choices: apply at once, and keep them undoable. */
  const setChoice = (patch: Partial<Fields>) => {
    clearTimeout(timer.current);
    const next = { ...fields, ...patch };
    setFields(next);
    navigate(next, { replace: false });
  };

  /** A tick or untick: shown at once, applied once the reader pauses. */
  const toggleGame = (id: number) => {
    const ids = new Set(idList(fields.game));
    if (ids.has(id)) ids.delete(id);
    else ids.add(id);
    const next = {
      ...fields,
      game: [...ids].sort((a, b) => a - b).join(","),
    };
    setFields(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(
      () => navigate(next, { replace: false }),
      GAME_PICK_DEBOUNCE_MS,
    );
  };

  /**
   * Typing is different. Every keystroke would be a database query and a
   * history entry, so the write is debounced and REPLACES rather than pushes —
   * otherwise Back would walk letter by letter out of a name.
   */
  const setSearch = (value: string) => {
    const next = { ...fields, search: value };
    setFields(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(
      () => navigate(next, { replace: true }),
      SEARCH_DEBOUNCE_MS,
    );
  };

  const flushSearch = () => {
    clearTimeout(timer.current);
    if (fields.search !== written.current.search) {
      navigate(fields, { replace: true });
    }
  };

  const reset = () => {
    clearTimeout(timer.current);
    const cleared: Fields = {
      search: "",
      game: "",
      conference: "",
      conf: "",
      rank: "",
    };
    // Set both, because Reset clears the pill groups too — they are not this
    // component's state, but they are part of what the button promises.
    written.current = cleared;
    setFields(cleared);
    startTransition(() =>
      router.push(resetBoardHref(params), { scroll: false }),
    );
  };

  return (
    <form
      method="GET"
      // The board itself, not `/`. Posting to the home page only reached the
      // board through its legacy-link redirect -- an extra round trip for every
      // pre-hydration search, and one more hop for the sport to be lost on.
      action={BOARD_PATH}
      // Pre-hydration this is a real GET and the hidden fields carry the rest
      // of the state. Once interactive, Enter flushes the pending keystroke
      // instead of reloading the page.
      onSubmit={(event) => {
        event.preventDefault();
        flushSearch();
      }}
      aria-busy={isPending}
      // The fields dim while the board reloads, but not the Games picker:
      // opacity on a parent applies to the open list too, which then showed
      // the board through it for the length of every reload. The picker dims
      // its own button instead (`busy`).
      className={
        "border-border-subtle relative flex flex-wrap items-end gap-2 border-t pt-3 *:transition-opacity " +
        (isPending ? "[&>*:not([data-keep-solid])]:opacity-60" : "")
      }
    >
      {hiddenFields(params, ["q", "game", "conference", "conf", "rank", "page"]).map(
        (field) => (
          <input
            key={field.name}
            type="hidden"
            name={field.name}
            value={field.value}
          />
        ),
      )}

      <Field label="Search player" className="min-w-44 flex-1">
        <input
          type="search"
          name="q"
          value={fields.search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Name…"
          autoComplete="off"
          className="bg-panel-inset border-border-subtle text-ink placeholder:text-dim focus:border-accent-cyan/60 w-full rounded-lg border px-2.5 py-1.5 text-sm outline-none"
        />
      </Field>

      {/* Not a <Field>: that is a <label>, and a label may hold one control,
          not a list of checkboxes. */}
      <div data-keep-solid className="flex flex-col gap-1">
        <span className="label-caption">Games</span>
        <GamePicker
          busy={isPending}
          games={games}
          value={fields.game}
          onToggle={toggleGame}
          onClear={() => setChoice({ game: "" })}
        />
      </div>

      <Field label="Conference">
        <Select
          name="conference"
          value={fields.conference}
          onChange={(value) => setChoice({ conference: value })}
        >
          <option value="">All displayed</option>
          {conferences.map((conference) => (
            <option key={conference.id} value={conference.name}>
              {conference.abbreviation ?? conference.name}
            </option>
          ))}
        </Select>
      </Field>

      {/* Removed, not disabled, on a board whose markets publish no confidence
          — see `showsCalls`. A dropdown offering 55% through 80% where every
          row's confidence is NULL would empty the board at the first choice. */}
      {showsCalls ? (
        <Field label="Min confidence">
          <Select
            name="conf"
            value={fields.conf}
            onChange={(value) => setChoice({ conf: value })}
          >
            <option value="">Any</option>
            <option value="0.55">55%</option>
            <option value="0.6">60%</option>
            <option value="0.65">65%</option>
            <option value="0.7">70%</option>
            <option value="0.8">80%</option>
          </Select>
        </Field>
      ) : null}

      {/*
        Rank 1 is the BEST defense, so the soft matchups this filter exists
        to find are the HIGH ranks. Stated as "≥" rather than dressed up as
        "top N softest": the number in the control is the number on the card,
        and a filter whose label disagrees with the value beside it is how a
        reader stops trusting both.
      */}
      <Field label="Opp rank ≥">
        <Select
          name="rank"
          value={fields.rank}
          onChange={(value) => setChoice({ rank: value })}
        >
          <option value="">Any</option>
          <option value="90">90+ (soft)</option>
          <option value="110">110+</option>
          <option value="125">125+ (softest)</option>
        </Select>
      </Field>

      <button
        type="button"
        onClick={reset}
        className="text-dim hover:text-muted ml-auto px-2 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-label"
      >
        Reset
      </button>
    </form>
  );
}

function Field({
  label,
  className = "",
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={"flex flex-col gap-1 " + className}>
      <span className="label-caption">{label}</span>
      {children}
    </label>
  );
}

/**
 * Several games at once (client, 2026-10-06).
 *
 * A NATIVE <details>, so it opens and its checkboxes submit with the GET form
 * before the script has loaded — the same fallback every other field here
 * keeps. The checkboxes are named `game`, and the parser reads repeated keys.
 *
 * Closes on a click outside it, as a native select does; it stays open while
 * games are being ticked, which is the point of a multi-select.
 */
function GamePicker({
  busy,
  games,
  value,
  onToggle,
  onClear,
}: {
  /** The board is reloading: dim the button, never the open list. */
  busy: boolean;
  games: GameSummary[];
  value: string;
  onToggle: (id: number) => void;
  onClear: () => void;
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
    // the panel spans the whole filter form (which is `relative`) instead.
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
        <button
          type="button"
          onClick={onClear}
          disabled={selected.size === 0}
          className="text-accent-cyan hover:bg-panel-inset rounded px-2 py-1.5 text-left text-xs font-semibold uppercase tracking-label disabled:text-dim disabled:hover:bg-transparent"
        >
          All games
        </button>
        {games.map((game) => (
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
            <span className="text-ink font-semibold">
              {game.awayAbbreviation} @ {game.homeAbbreviation}
            </span>
            <span className="text-dim ml-auto shrink-0 text-xs">
              {formatKickoff(game.startDate)}
            </span>
          </label>
        ))}
      </div>
    </details>
  );
}

function Select({
  name,
  value,
  onChange,
  children,
}: {
  name: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <select
      name={name}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="bg-panel-inset border-border-subtle text-ink focus:border-accent-cyan/60 rounded-lg border px-2.5 py-1.5 text-sm outline-none"
    >
      {children}
    </select>
  );
}
