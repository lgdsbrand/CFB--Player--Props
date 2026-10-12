"use client";

import Link from "next/link";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { savedSlips, useSavedSlips } from "@/components/slip/saved-slips-store";
import { scopedHref } from "@/lib/core/board-params";
import { formatAmericanOdds, formatKickoff } from "@/lib/core/format";
import { LIVE_REFRESH_MS } from "@/lib/core/live";
import { legKey, legMatchup, legTitle } from "@/lib/core/slip";
import {
  isSettled,
  needsPolling,
  slipOutcome,
  trackLeg,
  TRACK_BATCH,
  type LegTrack,
  type SavedSlip,
  type SlipState,
  type TrackFacts,
  type TrackState,
} from "@/lib/core/slip-track";

/**
 * My slips (client, 2026-10-12): the slips saved in this browser, each leg's
 * status, and the slip's. Facts come from `/slip-check/track` for the legs
 * not yet decided, asked again every minute while one is live or about to
 * start; decided legs are kept on the slip and never asked about again.
 */

const STATE_LABEL: Record<TrackState, string> = {
  pending: "Not started",
  live: "Live",
  awaiting: "Awaiting stats",
  won: "Won",
  lost: "Lost",
  push: "Push",
  check: "Check book",
};

const STATE_TONE: Record<TrackState, string> = {
  pending: "border-border-subtle text-muted",
  live: "border-accent-cyan/60 text-accent-cyan",
  awaiting: "border-border-strong text-muted",
  won: "border-positive/50 text-positive",
  lost: "border-negative/50 text-negative",
  push: "border-border-strong text-muted",
  check: "border-target/50 text-target",
};

const SLIP_TONE: Record<SlipState, string> = {
  won: "border-positive/50 text-positive",
  lost: "border-negative/50 text-negative",
  push: "border-border-strong text-muted",
  check: "border-target/50 text-target",
  open: "border-accent-cyan/60 text-accent-cyan",
};

type Loaded =
  | { status: "idle" | "loading" | "error" }
  | { status: "ready"; facts: TrackFacts; at: Date };

const subscribeNothing = () => () => {};

export function SavedSlips({ checkHref }: { checkHref: string }) {
  const slips = useSavedSlips();
  // The list lives in this browser: until hydrated, render nothing rather
  // than flash "no saved slips".
  const hydrated = useSyncExternalStore(
    subscribeNothing,
    () => true,
    () => false,
  );
  const [loaded, setLoaded] = useState<Loaded>({ status: "idle" });

  const open = useMemo(
    () => [
      ...new Set(
        slips.flatMap((s) =>
          s.legs.filter((l) => !s.settled[legKey(l)]).map(legKey),
        ),
      ),
    ],
    [slips],
  );
  const openKey = open.join("~");

  const facts = loaded.status === "ready" ? loaded.facts : null;
  const now = loaded.status === "ready" ? loaded.at : null;
  const tracks = useMemo(() => {
    const games = new Map((facts?.games ?? []).map((g) => [g.gameId, g]));
    return new Map(
      slips.map((s) => [
        s.id,
        s.legs.map((leg): LegTrack | null => {
          const kept = s.settled[legKey(leg)];
          if (kept) return kept;
          return facts && now
            ? trackLeg(leg, games.get(leg.gameId), facts, now)
            : null;
        }),
      ]),
    );
  }, [slips, facts, now]);

  // Keep what has been decided, so it is not asked about again.
  useEffect(() => {
    if (!facts) return;
    const decided: Record<string, Record<string, LegTrack>> = {};
    for (const s of slips) {
      s.legs.forEach((leg, i) => {
        const t = tracks.get(s.id)?.[i];
        if (t && isSettled(t) && !s.settled[legKey(leg)]) {
          (decided[s.id] ??= {})[legKey(leg)] = t;
        }
      });
    }
    if (Object.keys(decided).length > 0) savedSlips.settle(decided);
  }, [facts, slips, tracks]);

  // Whether the next minute can change anything: read by the timer below.
  // Asked at each tick with the clock then, so a leg about to kick off
  // starts the polling without a reload.
  const shouldPoll = useRef<(at: Date) => boolean>(() => false);
  useEffect(() => {
    shouldPoll.current = (at) =>
      slips.some((s) => {
        const t = tracks.get(s.id) ?? [];
        return (
          t.some((x) => x === null) || needsPolling(s.legs, t as LegTrack[], at)
        );
      });
  }, [slips, tracks]);

  useEffect(() => {
    if (open.length === 0) return;
    let cancelled = false;
    const load = async () => {
      if (cancelled || document.hidden) return;
      setLoaded((was) =>
        was.status === "ready" ? was : { status: "loading" },
      );
      try {
        const batches: string[][] = [];
        for (let i = 0; i < open.length; i += TRACK_BATCH)
          batches.push(open.slice(i, i + TRACK_BATCH));
        const parts = await Promise.all(
          batches.map(async (keys) => {
            const response = await fetch(
              scopedHref("/slip-check/track", {}, { legs: keys.join("~") }),
            );
            if (!response.ok) throw new Error(String(response.status));
            return (await response.json()) as TrackFacts;
          }),
        );
        if (cancelled) return;
        setLoaded({
          status: "ready",
          facts: {
            games: parts.flatMap((p) => p.games),
            stats: Object.assign({}, ...parts.map((p) => p.stats)),
          },
          at: new Date(),
        });
      } catch {
        if (!cancelled)
          setLoaded((was) =>
            was.status === "ready" ? was : { status: "error" },
          );
      }
    };
    void load();
    const timer = window.setInterval(() => {
      if (shouldPoll.current(new Date())) void load();
    }, LIVE_REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // `openKey` stands for `open`: the same legs in a new array are the same ask.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openKey]);

  if (!hydrated) return null;

  if (slips.length === 0) {
    return (
      <div className="panel flex flex-col gap-2 p-6">
        <h2 className="section-header">No saved slips yet</h2>
        <p className="text-muted max-w-prose text-sm">
          Build a slip on{" "}
          <Link
            href={checkHref}
            className="text-accent-cyan hover:text-ink font-bold"
          >
            Check a slip
          </Link>{" "}
          or in the bet slip drawer, then press Save slip. Saved slips are kept
          in this browser only.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {loaded.status === "error" ? (
        <p className="text-negative text-sm">
          The scores could not be loaded. Refresh to try again.
        </p>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {slips.map((s) => (
          <SlipCard key={s.id} slip={s} tracks={tracks.get(s.id) ?? []} />
        ))}
      </div>
      <p className="text-dim max-w-prose text-[0.6875rem] leading-relaxed">
        Saved slips are kept in this browser only, up to 50. College moneylines,
        spreads and totals follow the live scoreboard, updated every two
        minutes; first-quarter and first-half legs settle when that period ends.
        Player props settle the morning after the game, when the box scores
        arrive. NFL games have no live scores here, so NFL legs settle after the
        final, and NFL first-quarter and first-half legs cannot be settled here
        at all. A leg marked Check book is one these numbers cannot decide; your
        book has the final word on every bet.
      </p>
    </div>
  );
}

function SlipCard({
  slip: s,
  tracks,
}: {
  slip: SavedSlip;
  tracks: (LegTrack | null)[];
}) {
  const ready = tracks.every((t) => t !== null);
  const outcome = ready ? slipOutcome(tracks as LegTrack[]) : null;
  const meta = [
    `${s.legs.length} leg${s.legs.length === 1 ? "" : "s"}`,
    s.bookName,
    s.price !== null ? formatAmericanOdds(s.price) : null,
  ].filter(Boolean);

  return (
    <section className="panel flex min-w-0 flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            {outcome ? (
              <span className={"pill border " + SLIP_TONE[outcome.state]}>
                {outcome.label}
              </span>
            ) : (
              <span className="pill border-border-subtle text-dim border">
                Loading…
              </span>
            )}
          </div>
          <span className="text-ink text-sm font-extrabold">
            {meta.join(" · ")}
          </span>
          <span className="text-dim text-[0.6875rem]">
            Saved {formatKickoff(s.savedAt)}
          </span>
        </div>
        <button
          type="button"
          onClick={() => {
            if (window.confirm("Remove this saved slip?"))
              savedSlips.remove(s.id);
          }}
          aria-label="Remove this saved slip"
          className="text-muted hover:text-negative shrink-0 text-lg leading-none transition-colors"
        >
          ×
        </button>
      </div>

      <ul className="flex flex-col gap-2">
        {s.legs.map((leg, i) => {
          const t = tracks[i];
          return (
            <li
              key={legKey(leg)}
              className="bg-panel-inset border-border-subtle flex flex-col gap-1.5 rounded-xl border p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 flex-col">
                  <span className="text-ink text-sm font-extrabold">
                    {legTitle(leg)}
                  </span>
                  <span className="text-dim text-[0.6875rem]">
                    {`${legMatchup(leg)} · ${formatKickoff(leg.startDate)}`}
                  </span>
                </div>
                {s.prices[i] !== null && s.prices[i] !== undefined ? (
                  <span className="text-muted shrink-0 font-mono text-xs font-bold tabular-nums">
                    {formatAmericanOdds(s.prices[i]!)}
                  </span>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                {t ? (
                  <>
                    <span className={"pill border " + STATE_TONE[t.state]}>
                      {STATE_LABEL[t.state]}
                    </span>
                    {t.detail ? (
                      <span
                        className={
                          "text-xs tabular-nums " +
                          (t.state === "live" && t.trend === "ahead"
                            ? "text-positive"
                            : t.state === "live" && t.trend === "behind"
                              ? "text-negative"
                              : "text-muted")
                        }
                      >
                        {t.detail}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span className="text-dim text-xs">Loading…</span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
