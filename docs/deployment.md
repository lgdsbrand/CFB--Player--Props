# Deployment — what the client has to provide, and in what order

Nothing is deployed today. There is no git remote, no Vercel project and no
Render service; the whole system runs from one machine against a development
Supabase instance. Everything below is blocked on **accounts the client owns**,
which is why it has lead time and should start before the data blockers clear.

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
| **Whether to buy historical prop odds** | yes / no | **Worth pricing.** The model has never been graded against a real book line, only synthetic ones — calibration is proven, profitability is not. This is the single largest open question about the product and it is closeable with data we can buy. |

---

## 2. Accounts to create, and what each one costs

Figures are the published list prices at the time of writing and should be
confirmed at signup — they move.

| Service | What it runs | Plan needed | Why not the free tier |
|---|---|---|---|
| **GitHub** | the repo, and the deploy trigger | Free | Nothing here needs paid GitHub. |
| **Vercel** | the Next.js board | Free may do; Pro (~$20/user/mo) if the client wants team seats or password-protected previews | The board is server-rendered on demand and does not need paid features to run. |
| **Supabase** | the database everything reads and writes | **Pro (~$25/mo) — required, not optional** | The free tier stops at 500 MB. The development database is at **448 MB** today with two seasons of play-by-play, and 2026 adds a third. It will not fit. |
| **Render** | the Python worker's seven cron jobs | A paid plan; cron services are not on Render's free tier | Free static/web services exist there; scheduled jobs do not. |
| **CollegeFootballData** | all football data | Tier 2, already funded (~$5/mo) | The free tier rate-limits too hard for an all-FBS backfill. |

Rough all-in: **$50–75/month**, dominated by Supabase and Render, plus whatever
the odds plan costs.

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

**The worker on Render needs the credentials that can write:**

| Variable | Required? | Notes |
|---|---|---|
| `SUPABASE_DB_URL` | **yes** | Session pooler, port 5432. The direct connection is IPv6-only without a paid add-on and Render's outbound is not reliably IPv6. |
| `CFBD_API_KEY` | **yes** | The funded Tier 2 key. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | yes | Never goes near the browser bundle. If it ever does, rotate it. |
| `ODDS_API_KEY` | when odds start | The client's existing paid key. **Coverage of NCAAF player props is still unconfirmed** — `probe_odds` answers it in one run. |
| `ODDS_API_KEY_FREE` | recommended | The free-tier key, so dry runs cannot spend production credits. That allowance is shared with the client's other models and has been exhausted mid-month before. |
| `GEMINI_API_KEY` | for the AI reads | **Must come from a project with billing enabled.** The key we have resolves to the free tier: 20 requests per day against ~2,000 players, and Google's free tier trains on submitted content while the paid tier does not. These prompts carry the client's model output. |
| `ALERT_WEBHOOK_URL` | optional | Any Slack/Discord/Teams incoming webhook. Without it alerts go to the run log and Render's own cron-failure email is the backstop. The whole URL is the credential. |

---

## 4. The order to do it in

Each step is quick; the waiting is all in step 0.

0. **Client creates the four accounts and invites us.** Everything else is
   minutes; this is days.
1. **Push the repo to the client's GitHub org.** Nothing secret in any commit — verified, the only credential-shaped string in tracked files is
   the placeholder in `.env.example`.
2. **Create the Supabase production project.** Choose the region first and write
   it down; step 3 must match it.
3. **Create the Vercel project from the repo, and pin its function region to
   Supabase's.** This is worth more than every code optimisation in the project
   combined: from a development machine one database round trip costs ~415 ms
   regardless of payload, and a page makes three to six of them in sequence. In
   the same region that is single-digit milliseconds. Same code, same queries —
   the board goes from ~2.5 s to roughly a fifth of a second.
4. **Apply the migrations** (29 of them) and confirm the ledger. There is no
   `supabase` CLI on our machine, so this runs through the worker's connection;
   see [runbook.md](runbook.md#applying-a-migration).
5. **Run the backfill** — reference, stats, ratings, splits — for 2023 through
   2025, then `run_projections`. Hours, not minutes, and it costs CFBD calls.
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

- **2026 rosters do not exist yet.** `/roster?year=2026` returned 0 rows on
  2026-08-04 against 15,601 for 2025. No roster, no player-team mapping, no
  board — regardless of how good the model is. This is CFBD's to publish and
  ours to ingest the day it lands, which is one command.
- **No book has posted an NCAAF player prop yet.** Books post these late; the
  plan's coverage is unconfirmed. Until then the board shows model leans with no
  line beside them, which is the designed behaviour (CLAUDE.md §7), not a
  degraded one.
- **The AI reads are off.** `ai_adapter` is `none` and the job exits cleanly in
  that state. It needs the billed key above.
- **The model has never been graded against a real book line.** Calibration is
  measured and good; edge against a live market is not yet measurable. See §1.

---

## 6. What is ready

- 29 migrations, all applied and recorded in the ledger on the development
  database.
- Two full seasons of all-FBS data plus a third as a prior-year source; 81,198
  projections for 2025 across all 16 weeks.
- A walk-forward backtest over both seasons: Brier skill **+0.196**, ECE
  **0.0162**, with the opening weekends graded separately and coming out the
  best-calibrated stretch of the season.
- 713 Python tests, 98 web tests, 168 data-integrity checks, all passing.
- Seven scheduled jobs with monitoring that alerts when any of them stops
  producing — including the case where a job succeeds and writes nothing.
