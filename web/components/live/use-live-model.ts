"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { LIVE_REFRESH_MS } from "@/lib/core/live";
import {
  LIVE_HISTORY_COLUMNS,
  LIVE_PROJECTION_COLUMNS,
  toLivePoint,
  toLiveProjection,
  type LivePoint,
  type LiveProjection,
} from "@/lib/core/live-model";
import { getBrowserSupabaseClient } from "@/lib/supabase/client";

/** Within this of kickoff a game may go live, so the page keeps asking. */
const WINDOW_BEFORE_MS = 10 * 60_000;
const WINDOW_AFTER_MS = 5 * 3_600_000;

type Game = { gameId: number; startDate: string | null };

function mayChange(game: Game, row: LiveProjection | undefined, now: number): boolean {
  if (row?.status === "completed") return false;
  if (row?.status === "in_progress") return true;
  const at = game.startDate ? Date.parse(game.startDate) : NaN;
  return Number.isFinite(at) && now >= at - WINDOW_BEFORE_MS && now <= at + WINDOW_AFTER_MS;
}

/**
 * The live model's rows for these games (migration 0098), refreshed from the
 * browser every minute while any of them can still change, the same way and
 * for the same reason as `useLiveScores`: a few rows of one small table, not
 * a server re-render. With `historyFor`, that one game's chart points come
 * along on each refresh.
 *
 * `refresh()` asks at once, for the "Refresh now" button: a live bettor should
 * not wait out the minute. It reads what the worker last wrote; the worker
 * itself reruns the NFL model about every minute while a game is on.
 */
export function useLiveProjections(
  games: Game[],
  initial: LiveProjection[],
  options: { historyFor?: number; initialHistory?: LivePoint[] } = {},
): {
  projections: LiveProjection[];
  history: LivePoint[];
  refresh: () => Promise<void>;
  refreshing: boolean;
} {
  const [projections, setProjections] = useState(initial);
  const [history, setHistory] = useState(options.initialHistory ?? []);
  const [refreshing, setRefreshing] = useState(false);
  const loadRef = useRef<((force: boolean) => Promise<void>) | null>(null);
  const key = games.map((g) => g.gameId).join(",");
  const [seenKey, setSeenKey] = useState(key);
  if (seenKey !== key) {
    setSeenKey(key);
    setProjections(initial);
    setHistory(options.initialHistory ?? []);
  }

  useEffect(() => {
    if (games.length === 0) return;
    const ids = games.map((g) => g.gameId);
    let cancelled = false;
    let latest = initial;

    const load = async (force = false) => {
      const now = Date.now();
      if (cancelled || (!force && document.hidden)) return;
      const changing = games.some((g) => mayChange(g, latest.find((p) => p.gameId === g.gameId), now));
      if (!force && !changing) return;
      const supabase = getBrowserSupabaseClient();
      const [rows, points] = await Promise.all([
        supabase.from("live_projections").select(LIVE_PROJECTION_COLUMNS).in("game_id", ids),
        options.historyFor === undefined
          ? Promise.resolve(null)
          : supabase
              .from("live_projection_history")
              .select(LIVE_HISTORY_COLUMNS)
              .eq("game_id", options.historyFor)
              .order("elapsed", { ascending: true }),
      ]);
      if (cancelled) return;
      if (!rows.error && rows.data) {
        latest = (rows.data as unknown as Record<string, unknown>[]).map(toLiveProjection);
        setProjections(latest);
      }
      if (points && !points.error && points.data) {
        setHistory((points.data as unknown as Record<string, unknown>[]).map(toLivePoint));
      }
    };

    loadRef.current = load;
    const timer = window.setInterval(() => void load(), LIVE_REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      loadRef.current = null;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // `key` stands for `games`: a new array with the same ids is the same set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const refresh = useCallback(async () => {
    if (!loadRef.current) return;
    setRefreshing(true);
    try {
      await loadRef.current(true);
    } finally {
      setRefreshing(false);
    }
  }, []);

  return { projections, history, refresh, refreshing };
}
