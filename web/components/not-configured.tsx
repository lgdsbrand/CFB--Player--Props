/**
 * The fallback panel shown when the Supabase values are missing.
 *
 * This is the first thing a person sees when a deployment is misconfigured, so
 * it has to name the fix for the environment it is actually running in — the
 * two are not interchangeable. `NEXT_PUBLIC_*` values are compiled into the
 * bundle at BUILD time, so on Vercel the dashboard can show both variables set
 * correctly while the live site keeps serving a build that never saw them. The
 * fix there is a redeploy; telling that person to "reload" sends them in a loop
 * past a dashboard that already looks right.
 *
 * Not hypothetical: the client hit exactly this on 2026-08-10 and the panel
 * pointed him at `web/.env.local`, a file he has no way to edit.
 */
export function NotConfigured() {
  const onVercel = Boolean(process.env.VERCEL);

  return (
    <div className="panel p-6">
      <h1 className="section-header mb-2">Not configured</h1>
      <p className="text-muted text-sm">
        This page needs <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code>{" "}
        and <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>.
      </p>
      {onVercel ? (
        <p className="text-muted mt-2 text-sm">
          Add them under <strong>Settings → Environment Variables</strong>, then{" "}
          <strong>redeploy</strong>. They are read when the site is built, so
          saving them alone changes nothing and reloading will not pick them up.
        </p>
      ) : (
        <p className="text-muted mt-2 text-sm">
          Set them in <code className="font-mono">web/.env.local</code>, then
          restart the dev server.
        </p>
      )}
    </div>
  );
}
