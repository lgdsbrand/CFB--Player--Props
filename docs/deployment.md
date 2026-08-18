# Deployment — what the client has to provide, and in what order

**Status 2026-08-09.** The production database is live and loaded. What remains
is Vercel and Render.

- **Supabase production is done.** Project `enpoqrnrbzcoyshstwgf`
  ("CFB- Player- Props"), region **`ca-central-1` — verified**, which is what
  makes the `yul1` pin in step 3 same-region rather than merely close. The
  password the client first sent never authenticated; he reset it and the
  replacement works.
- **All 29 migrations applied and recorded**, via
  `npx --yes supabase@latest db push --db-url "<prod>" --yes`. The claim below
  that there is no `supabase` CLI on this machine is obsolete: Node and npx are
  here, and the CLI installs on first use, which is strictly better than
  hand-applied SQL because it maintains the ledger itself.
- **The data is moved and verified.** 234.5 MB in 276 s; all 27 tables agree on
  row count *and* on a checksum over every column; 22 identity sequences reset.
  Production is **333.2 MB** against the 500 MB free-tier cap.
- **Reads verified from outside** with `npm run check:schema` against
  production: 15 tables/views plus the RPC, all readable **as the anon role**,
  which is what proves RLS permits public reads.
- **Still to do:** the Vercel project (step 3) and the Render worker (step 6).
  The Odds API pool is separately exhausted until 1 September — see §5.

The whole system still runs from one machine, so nothing refreshes on a schedule
until Render is up.

The guiding principle: **every account is created by the client, in the client's
name, with us invited as a collaborator.** Not because of trust — because when
this engagement ends they must still own the thing, and migrating a Supabase
project or a Vercel domain afterwards is avoidable work.

---

## 1. Four decisions only the client can make

These gate everything else and none of them need a technical answer from us.

| Decision | Options | Our recommendation |
|---|---|---|
| **Where the board lives** | a route inside the existing Next.js site, or its own deployment behind a subdomain (`cfb.lgdsanalytics.com`) | **Own deployment, subdomain.** It ships as a separate Vercel project and cannot break the MLB, tennis or WNBA models when it deploys. The design tokens already match the live site (`docs/` theme notes), so it will look native either way. Merging into their repo is possible later; splitting afterwards is not. |
| **Who owns the repo** | client's GitHub org, or ours with them invited | **Client's org.** Costs nothing, and the Vercel integration then points at an account they control. |
| **Hit-rate basis** (CLAUDE.md §9.2) | a fixed threshold applied to past games, or the historical closing line | **Threshold to launch.** It is what most props sites do and it needs no paid backfill. Closing line is a config flag away once the odds plan is known. |
| **Whether to buy historical prop odds** | yes / no | **Answered — bought, weeks 7 and 8 of 2025, ~3,800 credits.** It was the right call: it turned "profitability unknown" into a measured answer (§5) that changes what we may claim. Any further weeks need the monthly allowance to reset; ask the client when that is, since the provider sends no reset header. |

---

## 2. Accounts to create, and what each one costs

Figures are the published list prices at the time of writing and should be
confirmed at signup — they move.

| Service | What it runs | Plan needed | Why not the free tier |
|---|---|---|---|
| **GitHub** | the repo, and the deploy trigger | Free | Nothing here needs paid GitHub. |
| **Vercel** | the Next.js board | Free may do; Pro (~$20/user/mo) if the client wants team seats or password-protected previews | The board is server-rendered on demand and does not need paid features to run. |
| **Supabase** | the database everything reads and writes | **Pro (~$25/mo) — required, not optional** | Two independent reasons. **Storage:** the free tier stops at 500 MB; the development database measured **473 MB** on 2026-08-14 with two seasons of play-by-play, and 2026 adds a third. **Capacity:** the free tier's compute cannot serve the board to a dozen concurrent readers — see below. |
| **Render** | the Python worker's nine cron jobs | A paid plan; cron services are not on Render's free tier | Free static/web services exist there; scheduled jobs do not. |
| **CollegeFootballData** | all football data | Tier 2, already funded (~$5/mo) | The free tier rate-limits too hard for an all-FBS backfill. |

Rough all-in: **$50–75/month**, dominated by Supabase and Render, plus whatever
the odds plan costs.

### The board's concurrency ceiling on the free tier — measured 2026-08-18

The storage cap is the deadline everyone tracks, but it is not the limit that
bites first. **Measured against the live deployment, requesting distinct board
URLs:**

| Concurrent readers | Result |
|---|---|
| 1 (24 distinct URLs, serial) | 24 / 24 OK |
| 2 | 16 / 16 OK |
| 4 | 16 / 16 OK |
| 8 | 24 / 24 OK |
| **12** | **20 / 24 OK — 4 responses were HTTP 500** |

The failure is not a bug in a page. Reproduced locally against the production
database, the error is
`Read failed (v_slate_weeks): canceling statement due to statement timeout`,
and it also hits `v_slate_games` and `v_board_rows`. The board renders from
several views at once; past roughly a dozen simultaneous renders the free
tier's shared compute cannot finish them inside Postgres's `statement_timeout`,
so a page that is completely healthy at rest returns a 500.

**Three things this is NOT**, each checked rather than assumed:

- **Not a slow query in isolation.** `v_slate_weeks` served 16-way concurrency
  on its own at ~1.5s, all 200. It is the whole render competing that fails.
- **Not Supabase's request layer.** The PostgREST API took 80-way concurrency
  with zero errors. It is database compute, not the API in front of it.
- **Not a cold-cache effect.** 24 distinct, never-cached URLs requested one at
  a time were all 200. Concurrency is the variable, not cache misses.

`v_slate_weeks` is nonetheless the most expensive thing on the path: it
aggregates **every row of `projections`** (90,108 at the time of measurement)
with three `count(distinct …)`s and no filter, taking ~3.8s cold and ~0.7s
warm. `getSlateWeeks` caches it for 300s (`lib/data/cache.ts`), so the cost is
paid whenever that window turns over. **This gets worse as the season runs** —
`projections` grows every week — so the ceiling above is the best it will be,
not the worst.

If Pro does not move the number enough, the next step is to stop recomputing
that aggregate per cache-miss: a materialised view refreshed at the end of
`run_projections` would make the week strip a small indexed read. That is a
migration plus a pipeline step, so it is worth measuring on Pro first.

**Reproduce it** with `web/scripts/audit-app.mjs`, which walks 126 URLs across
every page and re-checks any 5xx serially before reporting it, precisely so
load-induced failures are not mistaken for broken pages.

---

## 3. Keys and values to hand over

Everything is read from the environment; nothing is ever committed (CLAUDE.md
§0). The full annotated list is in [.env.example](../.env.example) — this is the
short version of who needs what.

**The Next.js app on Vercel needs exactly two, and both are public by design:**

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

The app only ever reads, there are no write policies anywhere in the schema, and
Row Level Security closes off everything it is not allowed to see. A leaked anon
key exposes public sports data and nothing else.

**The worker on Render needs the credentials that can write.** Note the split
between the two kinds of thing being asked for here: `CFBD_API_KEY`,
`ODDS_API_KEY` and `GEMINI_API_KEY` are keys to the client's **own accounts with
outside providers**, and only they can produce them. Everything beginning
`SUPABASE_` is **generated by the Supabase project itself** the moment it is
created — nobody has to obtain those, they just have to be copied out of the
dashboard once the project in §2 exists.

| Variable | Required? | Notes |
|---|---|---|
| `SUPABASE_DB_URL` | **yes** | Session pooler, port 5432. The direct connection is IPv6-only without a paid add-on and Render's outbound is not reliably IPv6. |
| `CFBD_API_KEY` | **yes** | The funded Tier 2 key. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | **no — do not ask for these** | Declared in `render.yaml` and `.env.example` and read into config, but **nothing consumes them**: every worker write goes through `SUPABASE_DB_URL` with psycopg, which is faster for bulk loading than the REST API they would authenticate. The service-role key bypasses Row Level Security completely, so not holding a copy is strictly better than holding one we never use. Leave both blank until something actually needs them. |
| `ODDS_API_KEY` | when odds start | The client's existing paid key. **Coverage of NCAAF player props is still unconfirmed** — `probe_odds` answers it in one run. |
| `ODDS_API_KEY_FREE` | recommended | The free-tier key, so dry runs cannot spend production credits. That allowance is shared with the client's other models and has been exhausted mid-month before. |
| `GEMINI_API_KEY` | for the AI reads | **Must come from a project with billing enabled.** The key we have resolves to the free tier: 20 requests per day against ~2,000 players, and Google's free tier trains on submitted content while the paid tier does not. These prompts carry the client's model output. |
| `ALERT_WEBHOOK_URL` | optional | Any Slack/Discord/Teams incoming webhook. Without it alerts go to the run log and Render's own cron-failure email is the backstop. The whole URL is the credential. |

---

## 4. The order to do it in

Each step is quick; the waiting is all in step 0.

0. **Client creates the four accounts and invites us.** ✅ **Done 2026-08-05.**
1. **Push the repo to the client's GitHub org.** ✅ **Done 2026-08-05** —
   `github.com/lgdsbrand/CFB--Player--Props`, 74 commits on `main`. Nothing
   secret in any commit: the full history was scanned and the only
   credential-shaped strings are the placeholder in `.env.example` and a
   base64 sha512 integrity hash in `package-lock.json` that happens to contain
   `eyJ`.
2. **Create the Supabase production project in `ca-central-1` (Montréal).**
   ✅ **Done** — `enpoqrnrbzcoyshstwgf`, region verified.
   Not us-east-1. The client's existing Legends infrastructure is in
   ca-central-1, the development project already is too — so the migration in
   step 5 never leaves the region — and Vercel's `yul1` maps to exactly this
   region, so the pairing in step 3 is same-region rather than merely close.
3. **Create the Vercel project from the repo.** Set **Root Directory to `web`** —
   there is no `package.json` at the repo root, so nothing builds without it.
   The function region is pinned in [`web/vercel.json`](../web/vercel.json) as
   `yul1`, so it does not depend on anyone remembering a dashboard setting;
   Hobby allows any single region, and `iad1` is only the default for projects
   that never choose. **The file lives in `web/`, not at the repo root, and that
   is load-bearing:** Vercel reads `vercel.json` from the configured Root
   Directory, so a copy at the repo root is ignored — silently, with the
   deployment succeeding and the functions landing in Washington while the
   database sits in Montréal. It was at the root until 2026-08-09 and would
   have done exactly that. This is worth
   more than every code optimisation in the project
   combined: from a development machine one database round trip costs ~415 ms
   regardless of payload, and a page makes three to six of them in sequence. In
   the same region that is single-digit milliseconds. Same code, same queries —
   the board goes from ~2.5 s to roughly a fifth of a second.
4. **Apply the migrations** (29 of them) and confirm the ledger.
   ✅ **Done 2026-08-09.** Use the Supabase CLI through npx — Node is on this
   machine and the CLI installs on first use, so the older instruction to
   hand-apply SQL through the worker's connection no longer applies:

       npx --yes supabase@latest db push --db-url "<prod DSN>" --yes

   It maintains the ledger itself, which is the whole point: applying without
   recording is how the ledger drifted once already — both halves or neither.
   It prints a Docker warning about caching a local catalog; that is unrelated
   and safe to ignore. See [runbook.md](runbook.md#applying-a-migration) for the
   manual path if npx is ever unavailable. Step 5's
   pre-flight re-checks this and refuses to move data onto a target whose ledger
   is short, so a missed migration surfaces here rather than as missing rows.
5. **MIGRATE the development database — do not re-ingest it.** This step used to
   read "run the backfill". That is now wrong and following it would destroy
   data we cannot get back.

   `player_prop_lines` holds **5,752 real closing lines** for weeks 7 and 8 of
   2025, bought for ~3,800 Odds API credits. The account has ~1,487 spendable
   credits left against a 5,000 reserve, and **historical props can only be
   bought for a date, not re-derived** — a fresh backfill would silently come
   up without them, and every profitability number in
   `grade_vs_book` rests on them.

   This is now one command rather than a procedure —
   [`migrate_database`](runbook.md#migrate_database), which carries the
   keep/skip list, the ordering and the verification:

   ```bash
   python -m worker.jobs.migrate_database              # the plan; writes nothing
   python -m worker.jobs.migrate_database --execute    # move it
   ```

   No `pg_dump` or `psql` on this machine, but psycopg 3 streams binary `COPY`
   from one connection into the other without the rows ever becoming Python
   objects. **~357 MB of the source's 452 MB lands on the target**, leaving
   ~143 MB under the free tier's 500 MB — which is what step 7's date is about.
   (Both figures grew by ~3 MB on 2026-08-08 when the 2026 rosters were
   ingested. Run the job with no arguments for the current numbers rather than
   trusting these.)

   | Table | Size | Move it? |
   |---|---|---|
   | `player_prop_lines` | 13 MB | **Yes — irreplaceable.** The `theoddsapi` rows above all. |
   | `plays`, `play_player_stats` | 176 MB | Yes. Regenerable from CFBD but it is hours and thousands of calls. |
   | `player_game_stats`, `players`, `player_team_seasons`, ratings, splits | ~53 MB | Yes. Same reasoning, cheaper. |
   | `projections`, `picks` | 62 MB | Yes. `run_projections` could rebuild them, but moving them means the board is live the moment the site is. |
   | `backtests`, `backtest_metrics`, `calibration_bins` | 0.6 MB | **Yes, mandatory.** `run_projections` refuses to run without a stored calibration snapshot and reads it from `backtests`. |
   | `backtest_predictions` | 129 MB | **The latest walk only — 46 MB.** See below. |
   | `sportsbooks`, `conferences` | 0.1 MB | **Yes, with their ids.** Neither is fully migration-seeded, and both are parents of data we are moving. |
   | `pipeline_runs` | 0.1 MB | **No.** Development job history; production should start with an honest empty one. |

   **`backtest_predictions` is the one that needed a decision.** The earlier
   plan skipped all 526,565 rows to get under the cap. That is affordable and
   wrong: three `audit_data` checks — "at least one backtest kept its raw
   predictions", "the stored calibration curve reproduces from the raw
   predictions" and "the walk grades the opening weeks the board publishes" —
   all resolve through the most recently persisted `backtest_id`, and an empty
   table makes the first two vacuously false and the third NULL. Step 7 below
   would then fail on a production database that is actually fine. Moving
   exactly the latest walk costs 46 MB and keeps them green. It has to be that
   whole walk: the second check recomputes the reliability diagram from these
   rows and compares bin counts exactly, so a sample fails just as loudly as an
   empty table.
6. **Deploy the worker to Render** from `render.yaml`, which already declares
   every cron and every secret as `sync: false` so nothing is stored in the
   file. A test parses that file to prove every scheduled job has a staleness
   expectation and vice versa.
7. **Verify from outside.** `/health` on the deployed site checks connectivity
   and the RLS posture; `audit_data` runs 168 integrity checks and exits
   non-zero on any failure; `monitor_pipeline --dry-run` prints what it would
   alert on without sending anything.
8. **Point the subdomain at Vercel**, if that is the choice in §1.

---

## 5. What will still be missing on launch day

Worth stating plainly so nobody discovers it in production.

- ~~**2026 rosters do not exist yet.**~~ **RESOLVED 2026-08-08.** CFBD published
  them between the 4th and the 8th: `/roster?year=2026&classification=fbs` now
  returns **15,171 players across 138 FBS teams**, ingested into development the
  same day (718 QB / 999 RB / 1,983 WR / 974 TE). They travel to production in
  the step 5 migration, so nothing further is owed here.

  One trap worth keeping, because it nearly buried the result: `CfbdClient.fetch`
  caches with **no expiry by default**, so re-probing returned the `0 rows`
  entry stored on the 4th and looked unchanged. The tell was the CFBD quota
  counter not moving. Any "has this landed yet" probe must pass `max_age=0` or
  set `CFBD_CACHE=off`. The cache is right for completed seasons, which cannot
  change, and exactly wrong for asking whether something new has appeared.
- **No book has posted an NCAAF player prop yet.** Books post these late; the
  plan's coverage is unconfirmed. Until then the board shows model leans with no
  line beside them, which is the designed behaviour (CLAUDE.md §7), not a
  degraded one.
- **The AI reads are off.** `ai_adapter` is `none` and the job exits cleanly in
  that state. It needs the billed key above.
- **The model does not beat blindly betting UNDER.** It has now been graded
  against real closing lines (2025 weeks 7 and 8, 1,856 bets). Overs landed just
  43.2% of those lines — college props close shaded toward the over — and the
  model, which calls UNDER ~65% of the time, is largely riding that shade:
  +2.9% ROI against +5.3% for betting under blindly, and the gap widens to
  −4.2 points on the edge ≥5% rows the board actually surfaces.

  There is real player-level skill underneath: called-over hits 48.1% against a
  43.4% base rate, called-under 59.1% against 56.6%. Both are genuine
  discrimination and the over side does not clear its 53.6% break-even. So the
  honest claim on launch day is **calibrated, with measured but sub-vig
  selection skill** — not profitable. `grade_vs_book` reports the blind-side
  benchmark next to every model ROI so this cannot be quietly overstated again.

---

## 6. What is ready

- 29 migrations, all applied and recorded in the ledger on the development
  database.
- Two full seasons of all-FBS data plus a third as a prior-year source; 81,198
  projections for 2025 across all 16 weeks.
- A walk-forward backtest over both seasons: Brier skill **+0.196**, ECE
  **0.0162**, with the opening weekends graded separately and coming out the
  best-calibrated stretch of the season.
- 961 Python tests, 227 web tests, 192 data-integrity checks, all passing.
- Nine scheduled jobs with monitoring that alerts when any of them stops
  producing — including the case where a job succeeds and writes nothing.
