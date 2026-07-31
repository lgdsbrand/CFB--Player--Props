import Link from "next/link";

/**
 * Placeholder shell.
 *
 * The board itself is Phase 4 and is not built until the schema and the
 * calibration report have been reviewed (CLAUDE.md §8). This page exists to
 * confirm the theme tokens render and to point at the health check.
 */
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col justify-center gap-8 p-8">
      <div className="flex flex-col gap-2">
        <span className="label-caption">Legends Sports</span>
        <h1 className="gradient-text text-4xl font-extrabold tracking-tight">
          CFB Player Props
        </h1>
        <p className="text-muted max-w-prose text-sm">
          Model-derived OVER/UNDER calls with a confidence percentage, from a
          full projected outcome distribution.
        </p>
      </div>

      <div className="panel flex flex-col gap-3 p-5">
        <div className="flex items-center gap-2">
          <span className="section-header">Build status</span>
          <span className="pill bg-accent-cyan/15 text-accent-cyan">
            Phase 1
          </span>
        </div>
        <p className="text-muted text-sm">
          Foundations: schema, migrations, worker skeleton and environment
          wiring. Ingest, modelling and the board follow in later phases.
        </p>
        <Link
          href="/health"
          className="text-accent-cyan text-sm font-semibold hover:underline"
        >
          View system health →
        </Link>
      </div>
    </main>
  );
}
