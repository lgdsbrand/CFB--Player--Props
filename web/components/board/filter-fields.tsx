"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import {
  boardHref,
  hiddenFields,
  resetBoardHref,
  type BoardParams,
} from "@/lib/core/board-params";
import { formatKickoff } from "@/lib/core/format";
import type { Conference } from "@/lib/core/types";
import type { SlateGame } from "@/lib/data/slate";

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

type Fields = {
  search: string;
  game: string;
  conference: string;
  conf: string;
  rank: string;
};

function fieldsFromParams(params: BoardParams): Fields {
  return {
    search: params.search ?? "",
    game: params.game?.toString() ?? "",
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

function numberOrUndefined(value: string): number | undefined {
  if (value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function FilterFields({
  params,
  conferences,
  games,
}: {
  params: BoardParams;
  conferences: Conference[];
  games: SlateGame[];
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
      game: numberOrUndefined(next.game),
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
      action="/"
      // Pre-hydration this is a real GET and the hidden fields carry the rest
      // of the state. Once interactive, Enter flushes the pending keystroke
      // instead of reloading the page.
      onSubmit={(event) => {
        event.preventDefault();
        flushSearch();
      }}
      aria-busy={isPending}
      className={
        "border-border-subtle flex flex-wrap items-end gap-2 border-t pt-3 transition-opacity " +
        (isPending ? "opacity-60" : "opacity-100")
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

      <Field label="Game">
        <Select
          name="game"
          value={fields.game}
          onChange={(value) => setChoice({ game: value })}
        >
          <option value="">All games</option>
          {games.map((game) => (
            <option key={game.gameId} value={game.gameId}>
              {game.awayAbbreviation} @ {game.homeAbbreviation} ·{" "}
              {formatKickoff(game.startDate)}
            </option>
          ))}
        </Select>
      </Field>

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
