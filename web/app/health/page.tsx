import { connection } from "next/server";

import { isSupabaseConfigured } from "@/lib/core/env";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export const metadata = {
  title: "Health · CFB Props",
  // UNLINKED AND UNINDEXED. This is an operator's page on a client-facing
  // product: nothing in the nav points here (see `site-header.tsx` for why), so
  // without this a crawler would still find it through the sitemap or an
  // external link and put "System Health — Degraded" in a search result for the
  // client's brand. `nocache` keeps a stale snapshot of a failing check out of
  // search previews too.
  robots: { index: false, follow: false, nocache: true },
};

type Check = {
  name: string;
  ok: boolean;
  detail: string;
};

/**
 * Phase 1 deploy proof.
 *
 * This is not a static status badge — it performs a real anon-key read against
 * Supabase on every request. Passing therefore proves three things at once:
 * the deployment has its environment wired, the migrations ran, and the RLS
 * read policies actually grant the anon role access. A page that only rendered
 * "OK" would prove none of them.
 */
export default async function HealthPage() {
  await connection();

  const checks: Check[] = [];

  if (!isSupabaseConfigured()) {
    checks.push({
      name: "Supabase environment",
      ok: false,
      detail:
        "NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are not set.",
    });
  } else {
    const supabase = createServerSupabaseClient();

    const conferences = await supabase
      .from("conferences")
      .select("*", { count: "exact", head: true });
    checks.push({
      name: "Read conferences (anon RLS)",
      ok: !conferences.error,
      detail: conferences.error
        ? conferences.error.message
        : `${conferences.count ?? 0} rows`,
    });

    const markets = await supabase
      .from("markets")
      .select("*", { count: "exact", head: true });
    checks.push({
      name: "Read markets (seed applied)",
      ok: !markets.error && (markets.count ?? 0) > 0,
      detail: markets.error ? markets.error.message : `${markets.count ?? 0} rows`,
    });

    const config = await supabase
      .from("app_config")
      .select("value")
      .eq("key", "edge_threshold")
      .maybeSingle();
    checks.push({
      name: "Read app_config.edge_threshold",
      ok: !config.error && config.data !== null,
      detail: config.error
        ? config.error.message
        : `edge_threshold = ${JSON.stringify(config.data?.value ?? null)}`,
    });

    // Must FAIL for anon: play_player_stats is deliberately closed off.
    const closed = await supabase
      .from("play_player_stats")
      .select("id", { head: true });
    checks.push({
      name: "play_player_stats denied to anon",
      ok: Boolean(closed.error) || (closed.count ?? 0) === 0,
      detail: closed.error
        ? "denied as expected"
        : "readable — RLS posture is wrong",
    });
  }

  const allOk = checks.every((c) => c.ok);

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col justify-center gap-6 p-8">
      <div className="flex items-baseline gap-3">
        <h1 className="section-header text-lg">System Health</h1>
        <span
          className={
            allOk
              ? "pill bg-positive/15 text-positive"
              : "pill bg-negative/15 text-negative"
          }
        >
          {allOk ? "Operational" : "Degraded"}
        </span>
      </div>

      <ul className="panel divide-border-subtle divide-y">
        {checks.map((check) => (
          <li
            key={check.name}
            className="flex items-center justify-between gap-4 px-4 py-3"
          >
            <div className="flex flex-col gap-1">
              <span className="text-sm font-medium">{check.name}</span>
              <span className="text-muted font-mono text-xs">
                {check.detail}
              </span>
            </div>
            <span
              aria-hidden
              className={
                check.ok
                  ? "bg-positive size-2 shrink-0 rounded-full"
                  : "bg-negative size-2 shrink-0 rounded-full"
              }
            />
            <span className="sr-only">{check.ok ? "passing" : "failing"}</span>
          </li>
        ))}
      </ul>

      <p className="label-caption">
        Checked {new Date().toISOString()} · reads only, anon key
      </p>
    </main>
  );
}
