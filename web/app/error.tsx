"use client";

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { SiteHeader } from "@/components/site-header";
import { BOARD_PATH, scopedHref } from "@/lib/core/board-params";
import { resolveSport, SPORT_SHORT } from "@/lib/core/sport";

/**
 * Every uncaught render error, in place of Next's bare error screen.
 *
 * WHY IT EXISTS. On 2026-09-11 Supabase's "Unresponsive Projects" incident took
 * the production database off the network: every read failed with `fetch
 * failed` or a 522, and every page on the live site answered with Next's
 * default screen — no header, no explanation, no way to try again, nothing to
 * tell a reader the site had not simply broken. The failure was upstream and
 * temporary, which is exactly the case this page is for.
 *
 * `unstable_retry`, not `reset`: the Next 16.2 docs are explicit that retry
 * re-fetches and re-renders the segment, and a database that has come back is
 * only visible to a render that asks it again.
 *
 * The header is read from the URL for the same reason the 404 does it — an NFL
 * reader should be offered the NFL board, not college.
 */
export default function ErrorPage({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <Suspense fallback={<SiteHeader />}>
      <ErrorView digest={error.digest} retry={unstable_retry} />
    </Suspense>
  );
}

function ErrorView({ digest, retry }: { digest?: string; retry: () => void }) {
  const sport = resolveSport(useSearchParams().get("sport") ?? undefined);
  // The page that failed, so the nav lights the section the reader was in
  // (it lit HOME on a failed /props) and the toggle offers the same page in
  // the other league. A player page reached in the wrong league redirects to
  // the right one, so this is safe on every route.
  const pathname = usePathname();

  return (
    <>
      <SiteHeader sport={sport} activeHref={pathname} />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-8 sm:px-6">
        <div className="panel flex flex-col gap-3 p-6">
          <span className="label-caption">Temporarily unavailable</span>
          <h1 className="text-2xl font-extrabold tracking-tight">
            This page didn&rsquo;t load
          </h1>
          <p className="text-muted max-w-prose text-sm">
            The data behind it couldn&rsquo;t be read just now. That is almost
            always a brief interruption between the site and its database rather
            than a problem with the page, so trying again in a moment usually
            works.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => retry()}
              className="cta w-fit px-3 py-2"
            >
              Try again
            </button>
            <Link
              href={scopedHref(BOARD_PATH, { sport })}
              className="text-accent-cyan text-sm hover:underline"
            >
              Open the {SPORT_SHORT[sport]} board
            </Link>
          </div>
          {/* The server log is keyed by this, so a reader who reports the page
              can be matched to the failure without the message leaking. */}
          {digest ? (
            <p className="text-dim text-[0.6875rem]">Reference {digest}</p>
          ) : null}
        </div>
      </main>
    </>
  );
}
