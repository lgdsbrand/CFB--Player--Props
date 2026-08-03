# Configuration reference

Two kinds of configuration, and the split between them is a security boundary,
not a convenience.

| | `app_config` (Postgres) | Environment variables |
|---|---|---|
| Holds | behaviour, thresholds, adapter selection | credentials, connection strings |
| Read by | worker **and** web app | worker only (plus two `NEXT_PUBLIC_` for the web) |
| Changing it | a SQL `update`, no deploy | a deploy / dashboard edit |
| Visibility | **world-readable under RLS** | private to the process |

**No credential ever goes in `app_config`.** That table is world-readable by
design — the web app reads it with the anon key — and `audit_data` has a check
that fails on credential-shaped values in it. This includes things that do not
look like credentials: a Slack incoming-webhook URL carries its secret in the
path, so it is a key that happens to look like an address (CLAUDE.md §0).

Environment variables are documented at the point of use in
[.env.example](../.env.example), with the reasoning for each. This file does not
repeat that; see [Environment variables](#environment-variables) below for the
index and the two rules that matter.

`worker/tests/test_docs.py` asserts that every `app_config` key inserted by a
migration is documented here, that this file documents no key that does not
exist, and that every adapter name quoted here is one the registry actually
knows. A configuration reference drifts silently otherwise.

---

## `app_config`

Defaults come from migrations, which are the source of truth: the seed in
`20260730100900_config_and_seeds.sql` and then later migrations that `update`
specific keys as decisions were settled. **A fresh `db push` reproduces the live
values exactly** — where "seeded" and "current" differ below, a later migration
did it deliberately.

The worker snapshots the resolved values into `model_runs.config` on every run,
so changing a default never silently reinterprets a historical run.

### The three adapter seams

Each is an unconfirmed vendor choice held as configuration rather than code
(CLAUDE.md §9). All three **raise on an unknown name** rather than falling back
to the null adapter — a silent fallback makes a typo look exactly like a
deliberate choice, and those need different responses.

#### `odds_adapter` — currently `"none"`

Which odds provider `ingest_odds` runs. Known values: `"none"`, `"theoddsapi"`.

`"none"` is **a real product state, not an outage**: the model still runs on
every projected starter, the board shows leans, and the over/under call fills in
when a market appears (CLAUDE.md §7, §9.1). The job says it is switched off,
writes nothing and exits 0, and `monitor_pipeline` does not expect it.

Switching it on also switches on the alerting for it. See
[odds.md](odds.md#turning-it-on).

#### `ai_adapter` — currently `"none"`

Which provider writes the weekly cached reads. Known values: `"none"`,
`"gemini"`, `"grok"`.

**Switched to `"gemini"` and back on 2026-08-03** (`20260803140000`, then
`20260803150000`). The client reported enabling billing; the key disagreed. Its
429 body names `GenerateRequestsPerDayPerProjectPerModel-FreeTier` with a value
of **20 requests per day** — an AI Studio key issued from a project without
billing active. A new key from the billed project resolves it; waiting does not.

That matters beyond throughput: Google's free tier trains on submitted content
and the paid tier does not, and these prompts carry the client's model
projections (CLAUDE.md §0). Twenty were submitted before this was understood.

**Before switching it on again, check the tier rather than the key's presence.**
One request is enough — a paid key's 429, if it produces one at all, names a
per-minute quota and never `…FreeTier`.

Not on cost, which is negligible either way: the job is a weekly burst of
~2,000 latency-insensitive calls, which is the shape Gemini's Batch API is
built for. Grok's live X grounding would make identical inputs produce
different reads, breaking `ai_reads.input_digest` and with it any chance of
reproducing or auditing a read. Both adapters are built; `"grok"` is a one-row
change away.

`"none"` remains a supported state, not an outage — the player page keeps
rendering the empty read slot it has had since Phase 4d, and cached rows
survive being switched off. **Which provider runs is decided by this row, not
by which API key happens to be set.**

**Turning this on also arms monitoring.** `monitor_pipeline` gates the
`generate_ai_reads` expectation on this key, so a week with no reads is now a
warning finding at 200 hours where before it was a configuration the monitor
stayed quiet about. That is the point: an off switch nobody watches and a
broken job nobody watches look identical from the board.

#### `alert_adapter` — currently `"log"`

Where `monitor_pipeline` sends findings. Known values: `"log"`, `"webhook"`.

**Unlike the other two, the default here is not an off switch.** There is no
sensible "nobody is told when the pipeline breaks" state — that is an outage
waiting to be discovered by a reader of the board. So the weakest option still
writes the finding to the Render run log, and a critical finding additionally
exits the cron non-zero so Render's own failure notification fires.

Set to `"webhook"` only once `ALERT_WEBHOOK_URL` is in the worker environment.
Any incoming webhook accepting `{"text": …}` works — Slack, Discord, Teams.

### Modelling and board behaviour

| Key | Current | Seeded | What it controls |
|---|---|---|---|
| `edge_threshold` | `0.05` | same | Minimum edge to qualify as an edge, matching the 5% in the client's MLB pitcher model (CLAUDE.md §6). Drives the EDGES ONLY filter. Read by both worker and web. |
| `edges_only_default` | `false` | same | Default state of the board's EDGES ONLY toggle. Web only. |
| `devig_method` | `"shin"` | `"proportional"` | How vig is stripped before comparing to a model probability. Changed by `20260731100000_devig_methods.sql`, which added three methods. **Still unconfirmed against the client's implementation** — see [schema.md](schema.md#on-the-de-vig-method). |
| `hit_rate_basis` | `"threshold"` | same | `"threshold"` grades past games against a fixed line and needs no paid line backfill; `"closing_line"` grades against the historical closing line. Settled on threshold by `20260801120000`, but see below. |
| `hit_rate_windows` | `[5, 10]` | same | Rolling windows offered by the hit-rate filter (L5 / L10). Web reads this rather than hardcoding. |
| `min_games_for_defense_rank` | `2` | same | Games required before a defense gets a published rank vs position; below this the rating shows as provisional. |
| `goal_line_yards_to_goal` | `10` | same | Distance to goal defining a goal-line opportunity for the anytime-TD model (CLAUDE.md §6). |
| `prior_season_weight_max` | `0.5` | same | Ceiling on prior-season contribution in week 1, decaying as the season accumulates. Transfer portal and NIL churn mean prior-year output often happened at another school (CLAUDE.md §6). |

**`hit_rate_basis` is worth revisiting.** It was settled on `"threshold"` when
historical prop odds were believed unreachable. They are not — the probe resolved
that, and `player_prop_lines` is append-only, so the line history the
closing-line basis needs is already being captured. Switching costs an odds
backfill, not a schema change. See [odds.md](odds.md#resolved).

### Ingest and cost control

| Key | Current | Seeded | What it controls |
|---|---|---|---|
| `backfill_seasons` | `[2024, 2025]` | `[2022, 2023, 2024, 2025]` | Seasons the backfill covers, and therefore the scope of `ingest_reference` / `ingest_stats` when no `--seasons` is given. Narrowed by `20260730101200_backfill_two_seasons.sql`: `play_player_stats` runs ~1M rows per season across all FBS, against a 500 MB free tier. |
| `ai_reads_max_per_run` | `400` | same | Hard ceiling on generations in one run of `generate_ai_reads`. A busy week is ~1,700 players and this bills the client's account, so the run stops and reports how many were left. Stopping early is recoverable; a surprise invoice is not. |
| `ai_reads_min_confidence` | `0.0` | same | Only generate reads for picks at least this confident. `0.0` means every projected player. Raising it is the cheapest way to cut spend — the reads nobody opens are the ones on calls the model is least sure about. |

### Documentation-only

| Key | Current | What it is |
|---|---|---|
| `displayed_conference_source` | `"conferences.is_displayed"` | Not read by any code. A reminder in the table itself that the conference filter is driven by data, so a post-realignment change is a row edit — and that it is a **display filter only** and must never restrict ingest (CLAUDE.md §4). |

### Reading and changing values

```sql
select key, value, updated_at from app_config order by key;

update app_config set value = '"theoddsapi"'::jsonb where key = 'odds_adapter';
```

**Write the change as a migration, not as a bare `update`.** The promise at the
top of this section — that a fresh `db push` reproduces the live values exactly
— only holds if every deliberate change is in `supabase/migrations/`. A value
that is live and unreproducible is the same drift that had to be repaired in
`supabase_migrations.schema_migrations` during Phase 5. `20260803140000` is the
worked example. The statement above is what goes *inside* that file.

Values are `jsonb`, so strings need their quotes: `'"gemini"'::jsonb`, not
`'gemini'::jsonb`. Numbers and arrays are bare — `'0.05'::jsonb`, `'[5, 10]'::jsonb`.

The worker reads through `worker.db.config_value(key)`; the web app reads a fixed
key list in [web/lib/data/config.ts](../web/lib/data/config.ts), which falls back
to the seed defaults **only** for a key missing from the table — that means the
seed did not run. A failed read throws instead. The distinction matters: a
missing key is a deployment that is behind, and a failed read is an outage.

---

## Environment variables

Full reasoning per variable is in [.env.example](../.env.example). Copy it to
`.env` for the worker and `web/.env.local` for the app.

| Variable | Required | Used by |
|---|---|---|
| `SUPABASE_DB_URL` | yes | worker — Postgres. Use the **session pooler** (port 5432) |
| `CFBD_API_KEY` | yes for ingest | worker — assumed paid tier |
| `NEXT_PUBLIC_SUPABASE_URL` | yes | web |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | yes | web |
| `SUPABASE_URL` | optional | worker |
| `SUPABASE_SERVICE_ROLE_KEY` | optional | worker — **never** in `web/.env.local` |
| `ODDS_API_KEY` | optional | worker — paid, 20k/month, shared pool |
| `ODDS_API_KEY_FREE` | optional | worker — free tier, for `probe_odds --free` |
| `GEMINI_API_KEY` | optional | worker |
| `GROK_API_KEY` | optional | worker |
| `ALERT_WEBHOOK_URL` | optional | worker — only when `alert_adapter` is `"webhook"` |
| `ENVIRONMENT` | defaults `development` | worker — gates `--synthetic-lines` |
| `LOG_LEVEL` | defaults `INFO` | worker |

Two rules:

**Anything prefixed `NEXT_PUBLIC_` is inlined into the browser bundle.** The app
gets the anon key only; it reads and never writes, and it never calls a data
provider. If the service role key or the database URL ever reaches the browser
bundle, rotate it.

**A missing optional key is not the same as an adapter being off.** Leaving
`GEMINI_API_KEY` blank while `ai_adapter` is `"gemini"` fails loudly, which is
correct. The way to turn a seam off is the `app_config` row, not an absent key.

`Settings.__repr__` in `worker/worker/config.py` redacts every secret field,
because the default dataclass repr would happily print an API key into a log
line. New secrets must be added to `_SECRET_FIELDS` when they are added to
`Settings`.

---

## Where each seam is deployed

Render's [render.yaml](../render.yaml) declares one shared `envVarGroups` with
every secret as `sync: false`, so Render prompts for it in the dashboard and it
is never stored in the file. Do not replace any of those with a literal value.

Vercel needs only the two `NEXT_PUBLIC_` variables, with root directory `web/`.

See [runbook.md](runbook.md) for what each job does with all of this.
