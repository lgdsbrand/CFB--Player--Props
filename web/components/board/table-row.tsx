"use client";

import { useId, useState } from "react";

/**
 * One prop in table view: the summary row, and the detail it expands into.
 *
 * WHY THIS IS THE ONLY CLIENT COMPONENT IN THE TABLE. Every filter on this
 * board is a URL parameter and a database predicate (see `board-params.ts`), so
 * the board needs no client JavaScript to work. Expansion is different: it is
 * pure presentation, it changes no query, and round-tripping it through the URL
 * would put a server render between a reader and a row they wanted to glance
 * at. So the state is local and nothing else moves to the client.
 *
 * `summary` and `detail` ARRIVE ALREADY RENDERED, as props from the server
 * component. This stays a shell that owns one boolean — the cells themselves,
 * their links and their formatting are server-rendered exactly as the card view
 * renders them, and none of that logic crosses the boundary.
 *
 * THE DETAIL IS RENDERED EAGERLY AND HIDDEN, not fetched on open. It is a
 * projection bar and a row of dots built from data the page already holds, so
 * fetching would be a round trip to redraw what was already in memory. The
 * whole table including every collapsed detail is still markedly less markup
 * than the card grid it replaces, which draws all of it expanded.
 */
export function TableRow({
  summary,
  detail,
  label,
  columnCount,
  striped,
}: {
  summary: React.ReactNode;
  detail: React.ReactNode;
  /** Names the row for the toggle's accessible label, e.g. "Drew Allar over 232.5 pass yards". */
  label: string;
  /** Total columns in the table, so the detail cell can span all of them. */
  columnCount: number;
  striped: boolean;
}) {
  const [open, setOpen] = useState(false);
  const detailId = useId();

  return (
    <>
      <tr
        className={
          "border-border-subtle/60 border-t transition-colors " +
          (open
            ? "bg-accent-indigo/8"
            : striped
              ? "bg-panel-inset/40 hover:bg-panel-inset"
              : "hover:bg-panel-inset/60")
        }
      >
        <td className="py-2 pl-3 pr-1 align-middle">
          <button
            type="button"
            onClick={() => setOpen((was) => !was)}
            aria-expanded={open}
            aria-controls={detailId}
            aria-label={`${open ? "Hide" : "Show"} detail for ${label}`}
            className={
              "border-border-subtle text-muted hover:border-border-strong hover:text-ink flex size-5 items-center justify-center rounded-md border text-xs leading-none font-bold transition-colors " +
              (open ? "border-accent-cyan/50 text-accent-cyan" : "")
            }
          >
            <span aria-hidden>{open ? "−" : "+"}</span>
          </button>
        </td>
        {summary}
      </tr>

      {/*
        `hidden` rather than unmounting. A collapsed row that is removed from the
        DOM takes its `aria-controls` target with it, which leaves the button
        pointing at nothing — and re-mounting on every toggle throws away the
        browser's own scroll position inside the horizontal scroller.
      */}
      <tr id={detailId} hidden={!open} className="bg-canvas/40">
        <td colSpan={columnCount} className="px-3 pt-1 pb-3">
          {detail}
        </td>
      </tr>
    </>
  );
}
