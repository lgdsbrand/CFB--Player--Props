"use client";

import { useEffect, useState } from "react";

import { LIVE_COLUMNS, LIVE_REFRESH_MS, toLiveScore, type LiveScore } from "@/lib/core/live";
import { getBrowserSupabaseClient } from "@/lib/supabase/client";

/** Within this of kickoff a game may go live, so the page keeps asking. */
const WINDOW_BEFORE_MS = 10 * 60_000;
const WINDOW_AFTER_MS = 5 * 3_600_000;

/**
 * Live rows for these games, refreshed from the database every minute while
 * any of them can still change, straight from the browser.
 *
 * WHY THE BROWSER AND NOT A PAGE REFRESH. `router.refresh()` would re-render
 * the whole server page every minute for every open tab, and the server render
 * is where this site's timeouts have come from. This asks for a few rows of one
 * small table, with the anon key every page already ships.
 *
 * It stops asking once every game is final or outside its window, and asks at
 * once when a hidden tab comes back.
 */
export function useLiveScores(
  games: { gameId: number; startDate: string | null }[],
  initial: LiveScore[],
): LiveScore[] {
  const [scores, setScores] = useState(initial);
  const key = games.map((g) => g.gameId).join(",");
  // A different set of games (the reader moved to another day or week)
  // starts from that page's rows, adopted during render rather than in an
  // effect, as NavSelect does.
  const [seenKey, setSeenKey] = useState(key);
  if (seenKey !== key) {
    setSeenKey(key);
    setScores(initial);
  }

  useEffect(() => {
    if (games.length === 0) return;
    const ids = games.map((g) => g.gameId);
    let cancelled = false;
    let latest = initial;

    const stillChanging = () => {
      const now = Date.now();
      return games.some((g) => {
        const row = latest.find((s) => s.gameId === g.gameId);
        if (row?.status === "completed") return false;
        if (row?.status === "in_progress") return true;
        const at = g.startDate ? Date.parse(g.startDate) : NaN;
        return Number.isFinite(at) && now >= at - WINDOW_BEFORE_MS && now <= at + WINDOW_AFTER_MS;
      });
    };

    const load = async () => {
      if (cancelled || document.hidden || !stillChanging()) return;
      const { data, error } = await getBrowserSupabaseClient()
        .from("live_scores")
        .select(LIVE_COLUMNS)
        .in("game_id", ids);
      if (cancelled || error || !data) return;
      latest = (data as Record<string, unknown>[]).map(toLiveScore);
      setScores(latest);
    };

    const timer = window.setInterval(load, LIVE_REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // `key` stands for `games`: a new array with the same ids is the same set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return scores;
}
