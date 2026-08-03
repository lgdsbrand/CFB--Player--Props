# Web app

Next.js 16 (App Router) + TypeScript + Tailwind v4, deployed on Vercel. The
board, the player detail view and the weekly targets panel.

Project brief: [../CLAUDE.md](../CLAUDE.md). Setup, deployment and the worker:
[../README.md](../README.md). Operations: [../docs/runbook.md](../docs/runbook.md).

```bash
npm install
npm run dev          # http://localhost:3000
```

## The rule this app is built around

**It reads from Supabase and does nothing else.** It never calls CFBD, an odds
provider or an LLM — the Python worker is the only thing that talks to any of
them (CLAUDE.md §2). AI reads are generated weekly and cached in `ai_reads`;
there is no per-page-view LLM call, and adding one would be out of scope by
design (§10).

It holds the **anon key only**, and anything prefixed `NEXT_PUBLIC_` is inlined
into the browser bundle. The service role key and the database URL belong to the
worker's environment. If either reaches this app, rotate it.

## Layout

```
app/                  routes
  health/             a real anon-key read on every request — the wiring proof
  player/[playerId]/  game log, splits, defense detail, the cached AI read
  globals.css         ALL theme tokens; the only file a reskin touches
components/board/     player cards, controls, weekly targets, team chips
components/player/    game log, splits, defense detail, the Recharts hit-rate chart
lib/core/             sport-agnostic: env, formatting, hit-rate maths, types
lib/data/             query modules, one per read concern
lib/supabase/         read-only clients
scripts/              check-schema.mjs
```

`lib/core/` is the sport-agnostic seam CLAUDE.md §3 asks for — nothing in it
should know about conferences, CFBD, or college football. That is what makes the
NFL build a copy rather than a rewrite.

## Verifying

```bash
npm run check:schema     # selects every column the app reads, against the live DB
npm run typecheck
npm run test             # hit-rate maths, via node --test (no test framework)
npm run lint
```

**`check:schema` is the one that matters most.** `lib/core/types.ts` is
hand-written — the Supabase CLI is not installed and the project is not linked,
so there are no generated types, and hand-written types keep compiling perfectly
after the view beneath them changes. TypeScript cannot detect that. This script
asks the database directly, and it also proves the anon role can still read what
the app needs.

`/health` performs a real anon-key read on every request, so passing it proves
the environment is wired, the migrations ran, and the RLS read policies grant
access. It additionally asserts `play_player_stats` is **denied** to anon, so a
too-permissive RLS change fails the check rather than silently exposing
play-level data.

## Two things worth knowing before changing the UI

**PostgREST caps a response at 1,000 rows and says nothing about it.** The read
layer detects truncation rather than trusting the row count. A new query that
does not is a board that quietly stops at 1,000.

**Theme values are measured, not chosen.** This project uses
[`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts)
to load **Inter** for body text and Geist Mono for the odds columns. Inter is not
a preference — it is the family the client's live site uses, read out of its
stylesheet. Theme colours are likewise measured rather than chosen; see the
provenance note at the top of `app/globals.css`. Corrections go in that one file,
never in a component.

## Deploying

Vercel, root directory `web/`, with `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_ANON_KEY` set. Preview deploy per branch so changes are
reviewable on a live URL before merge.
