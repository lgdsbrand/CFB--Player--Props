"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { SiteHeader } from "@/components/site-header";
import { BOARD_PATH, scopedHref } from "@/lib/core/board-params";
import { resolveSport, SPORT_SHORT } from "@/lib/core/sport";

/**
 * The 404, in the reader's league.
 *
 * A missing game or player id used to land on Next's bare default page: no
 * header, no toggle, no link anywhere — a dead end, found by `scripts/crawl.mjs
 * --edges` on 2026-09-11. Old shared links are exactly how a reader reaches one.
 *
 * A CLIENT COMPONENT FOR ONE REASON. `not-found.tsx` is not handed the search
 * params, but the address that 404'd still carries `?sport=`, and a reader who
 * followed an NFL link should be offered the NFL board back, not college.
 */
export function NotFoundView() {
  const sport = resolveSport(useSearchParams().get("sport") ?? undefined);
  const league = SPORT_SHORT[sport];

  return (
    <>
      <SiteHeader sport={sport} />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-8 sm:px-6">
        <div className="panel flex flex-col gap-3 p-6">
          <span className="label-caption">404</span>
          <h1 className="text-2xl font-extrabold tracking-tight">
            This page doesn&rsquo;t exist
          </h1>
          <p className="text-muted max-w-prose text-sm">
            The link may be old, or the address mistyped. A player or game that
            is no longer on a slate still has a page, so this one was never here.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Link href={scopedHref(BOARD_PATH, { sport })} className="cta w-fit px-3 py-2">
              Open the {league} board →
            </Link>
            <Link
              href={scopedHref("/games", { sport })}
              className="text-accent-cyan text-sm hover:underline"
            >
              All {league} games
            </Link>
          </div>
        </div>
      </main>
    </>
  );
}
