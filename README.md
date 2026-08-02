# College Football Player Prop Model

A weekly board that states, for each relevant college football player, whether
the model favours the **OVER or UNDER** on that player's book prop line, with a
**confidence percentage**.

The model projects a full outcome **distribution** internally; the over/under
call and the confidence figure are derived from it — the call is the side
holding the majority of the mass, the confidence is the mass past the line. The
projected range stays available as secondary detail, never as the headline
claim.

The project brief and working agreement is [CLAUDE.md](CLAUDE.md). Read it
before contributing. The schema design notes are in [docs/schema.md](docs/schema.md).

## Status

**Phase 3 (Model + backtest) — complete.** The deliverable is
[docs/calibration-report.html](docs/calibration-report.html): Brier skill
**+0.186**, ECE **0.019** over 328,005 graded predictions across 2024–25.

| Phase | Scope | State |
|---|---|---|
| 1 | Foundations: schema, migrations, scaffolding, env | done |
| 2 | CFBD ingest, position-split engine, opponent adjustment | done |
| 3 | Per-market distribution models, anytime-TD model, **calibration report** | done |
| 4 | Board, filters, player detail, defense detail, weekly targets | in progress |
| 5 | Odds ingestion, weekly AI reads, reskin, monitoring | not started |

Phase 4 begins with the worker, not the UI. Phase 3 validated a model and
persisted no projections — `backtest_predictions` holds graded probabilities,
which is not a board row — so `projections` was empty and `v_board_rows`
returned nothing. `run_projections` is what fills it:

```bash
python -m worker.jobs.run_projections --season 2025 --weeks 10
```

It writes one distribution per player-market, and a pick wherever there is a
line to call against. Yardage markets show the model's lean until a book posts;
anytime TD is callable immediately because its line is structural (over 0.5).
That split is the late-line behaviour CLAUDE.md §7 requires.

**`--synthetic-lines` is a development aid and its edges are meaningless.** No
real prop line exists yet, so the flag posts −110/−110 quotes at each player's
trailing average under a book named `DEV (synthetic)`. Those de-vig to exactly
0.500, which makes `edge` a restatement of the model's own confidence rather
than a disagreement with a market. It exists so the OVER/UNDER card can be
built and reviewed before books post in late August; it refuses to run outside
`ENVIRONMENT=development`.

## Layout

```
web/                  Next.js 16 (App Router) + TypeScript + Tailwind v4 → Vercel
  app/                routes; /health is the live read-path proof
  lib/core/           sport-agnostic: env, formatting, types
  lib/supabase/       read-only clients (anon key)
  app/globals.css     ALL theme tokens — the only file the reskin touches

worker/               Python 3.11 → Render cron
  worker/core/        sport-agnostic: projection → probability math
  worker/adapters/    CFBD adapter; the layer the NFL build replaces
  worker/jobs/        entrypoints, `python -m worker.jobs.<name>`
  tests/              pytest

supabase/migrations/  numbered SQL, the source of truth for the schema
docs/schema.md        schema reference and design rationale
render.yaml           worker blueprint; every secret is `sync: false`
```

The seam between sport-agnostic core and sport-specific adapter is deliberate
(CLAUDE.md §3) so the core can be copied into the NFL repo cleanly. There is no
shared package — just a disciplined boundary.

## Setup

### Prerequisites

- Node.js 20.9+ (Next 16 minimum) — developed on 24.18
- Python 3.11
- Either Docker Desktop (for a local Supabase stack) **or** a Supabase project

### 1. Database

The migrations are the source of truth, so the database is a build artefact:
either target reproduces it exactly.

**Option A — local stack** (needs Docker running):

```bash
npx supabase start
npx supabase db reset       # applies every migration + seeds
```

**Option B — a Supabase cloud project** (no Docker needed):

```bash
npx supabase login
npx supabase link --project-ref <your-project-ref>
npx supabase db push
```

A free project is fine for development. Production should be a project in the
**client's** Supabase organisation, on their billing, with you added as an
admin — see the note at the end of this file.

> **Connection string:** use the **session pooler** (port 5432,
> `aws-N-<region>.pooler.supabase.com`). The direct connection is IPv6-only
> without the paid IPv4 add-on, and the transaction pooler on 6543 breaks
> psycopg3's prepared statements partway through a bulk load. See
> [.env.example](.env.example) for the full reasoning.

### 2. Environment

```bash
cp .env.example .env                    # worker
cp .env.example web/.env.local          # web (only the two NEXT_PUBLIC_ values)
```

Fill in `SUPABASE_DB_URL` and `CFBD_API_KEY` for the worker, and
`NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` for the web app.

**The service role key must never appear in `web/.env.local`.** Anything
prefixed `NEXT_PUBLIC_` is inlined into the browser bundle.

### 3. Web

```bash
cd web
npm install
npm run dev          # http://localhost:3000
```

### 4. Worker

```bash
cd worker
python -m venv .venv
.venv/Scripts/activate           # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements-dev.txt
pytest
python -m worker.jobs.healthcheck
```

## Verifying

Three layers, in increasing strength.

### 1. Does it apply and run?

- **Worker:** `python -m worker.jobs.healthcheck` verifies config loads,
  Postgres is reachable, all 27 expected tables exist, the seed landed, the
  worker can **write** (via the same `pipeline_runs` code path every later job
  uses), and the CFBD key authenticates. Every check runs even if an earlier one
  fails, so you get a full readout during setup. Exactly one CFBD call.
- **Web:** `/health` performs a real anon-key read on every request. Passing
  proves the environment is wired, the migrations ran, and the RLS read policies
  grant access. It also asserts `play_player_stats` is *denied* to anon, so a
  too-permissive RLS change fails the check.

### 2. Does the schema enforce what it claims?

```bash
cd worker
pytest tests/test_schema_constraints.py -v      # needs SUPABASE_DB_URL
```

Applying a migration only proves the DDL runs. These tests prove the design:
that season-final ratings cannot be read as in-season knowledge, that the
backtest tripwire rejects future knowledge, that the splits function excludes
the prediction week, that `picks.side`/`confidence`/`edge` derive correctly from
the distribution, that a player with **no** book line still reaches the board,
and that anon can neither read play-level data nor write anything.

Everything runs in one rolled-back transaction, so no row survives. The suite
skips automatically when `SUPABASE_DB_URL` is unset.

### 3. Does the maths hold?

```bash
cd worker && pytest tests/test_probability.py
```

Pure unit tests, no database needed.

### 4. Does the web read layer still match the database?

```bash
cd web
npm run check:schema     # selects every column the app reads, against the live DB
npm run typecheck
npm run test             # hit-rate maths, via node --test (no test framework)
```

`lib/core/types.ts` is hand-written — the Supabase CLI is not installed and the
project is not linked, so there are no generated types. Hand-written types keep
compiling perfectly after the view beneath them changes, which TypeScript cannot
detect. `check:schema` closes that gap by asking the database directly, and it
also proves the anon role can still read what the app needs.

## Rules that are not negotiable

These are enforced in the schema or the tests, not left to memory. Full
rationale in [docs/schema.md](docs/schema.md).

1. **No lookahead.** Derived team/defense metrics are point-in-time weekly
   snapshots. `team_rating_snapshots` makes season-final ratings structurally
   incapable of being read as in-season knowledge; `backtest_predictions` has a
   `as_of_week <= week` tripwire. Never add a read path that returns "current"
   derived metrics without a cutoff argument.
2. **Edge is `model probability − de-vigged book implied probability`**, on the
   side being taken. Not `(projection − line) / line`. Defined once in SQL
   (`edge_on_side`) and mirrored in `worker/core/probability.py`; the tests pin
   both to the same vectors. This matches the client's existing MLB pitcher
   model so the two boards report comparable numbers.
3. **Ingest all FBS.** The conference list is a display filter
   (`conferences.is_displayed`) and must never restrict data collection —
   cross-conference games cannot be opponent-adjusted with one side missing.
4. **Secrets come from the environment.** Nothing but `.env.example` belongs in
   git, `render.yaml` uses `sync: false` throughout, and `app_config` is
   world-readable so it must never hold a credential.
5. **The app never calls a data provider.** Next.js reads Supabase; the worker
   is the only thing that talks to CFBD, odds providers or an LLM.
6. **AI reads are weekly and cached.** One row per player per week, enforced by
   a unique constraint. No per-page-view LLM calls.

## Deployment

- **Web → Vercel.** Root directory `web/`. Set the two `NEXT_PUBLIC_` variables.
  Preview deploy per branch so changes are reviewable on a live URL before merge.
- **Worker → Render.** Uses [render.yaml](render.yaml). Secrets are entered in
  the Render dashboard, never committed.
- **Database → Supabase.** Development can run against a local stack or a free
  project you own. **Production should live in the client's Supabase
  organisation** on their billing, with you as an admin member: this ships
  inside their site, so their production database should not depend on a
  personal account. Because the migrations are the source of truth, moving to
  their project is `supabase db push` against a fresh project.
