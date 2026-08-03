# Runbook

Every job, what it writes, when it runs, and what to do when it does not.

Each job is a module under `worker/worker/jobs/` and runs the same way in every
environment:

```bash
cd worker
python -m worker.jobs.<name>
```

There is no other entrypoint. Render's crons in [render.yaml](../render.yaml)
run these exact commands, so anything reproducible on a laptop is reproducible
in production and vice versa.

`worker/tests/test_docs.py` parses this file and fails if it names a job that
does not exist, omits one that does, or disagrees with `render.yaml` about the
schedule. A runbook that has quietly stopped being true is worse than no
runbook, so it is tested rather than trusted.

---

## Contents

- [The rules that apply to every job](#the-rules-that-apply-to-every-job)
- [Scheduled jobs](#scheduled-jobs) — canaries, the weekly pipeline, in-week refreshes
- [Jobs run by hand](#jobs-run-by-hand) — backtest, odds probe
- [Recovery](#recovery) — stuck runs, a failed chain, a wasted projection pass
- [Applying a migration](#applying-a-migration) — there is no `supabase` CLI on PATH
- [Ordinary weekly operation](#ordinary-weekly-operation)

---

## The rules that apply to every job

**Every job records itself in `pipeline_runs`.** `worker.db.pipeline_run` opens
a `running` row, marks it `succeeded` on clean exit, or `failed` with the
exception text and re-raises. It uses its own autocommit connection, so the
bookkeeping row survives even when the job's own transaction rolls back. This is
what `monitor_pipeline` watches.

**A killed process does not record a failure.** The row is only marked `failed`
when Python catches the exception, so an OOM kill, a Render deploy restart or a
hard timeout leaves the row `running` forever — with a *recent* `started_at`.
Any freshness check keyed on when a job last started therefore reads a dead job
as a healthy one. See [Recovery](#recovery).

**A "none" adapter is a supported state, not a failure.** `ingest_odds` and
`generate_ai_reads` exit 0 and do nothing while their adapter is `"none"`, and
`monitor_pipeline` does not expect them. A scheduled canary must not go red
because a deliberate configuration is in force. See
[configuration.md](configuration.md).

**Order matters in two places, and getting it wrong is silent.** Both are
written out under [Recovery](#recovery): the Sunday chain is strictly ordered,
and `run_backtest` must precede `run_projections`.

**Season and week resolve themselves.** `worker/core/schedule.py` answers "what
week is it" from kickoff times in the `games` table, because a cron has nobody
to ask. `ingest_odds` and `generate_ai_reads` both used to *require*
`--season`/`--week`; they now default to the current slate and still accept
explicit values. `Slate.in_season` is what keeps the monitor quiet from January
to August, when every job is legitimately idle.

---

## Scheduled jobs

Schedules are UTC. Saturday's games finish late Saturday night US time, which is
Sunday morning UTC — the ingest schedule is built around that, not around a US
calendar day.

### Canaries

#### `healthcheck` — daily 12:00 UTC

```bash
python -m worker.jobs.healthcheck
```

Deploy proof. Verifies against live services that config loads, Postgres is
reachable, the migrations are applied (27 expected tables), the worker can
**write** — through the same `pipeline_runs` code path every later job uses —
and that the CFBD key authenticates and carries the paid entitlements.

Every check runs even when an earlier one fails, so first-time setup gets a full
readout ("database OK, CFBD key missing") rather than stopping at the first
problem. Exits non-zero if anything failed. Makes exactly one CFBD call.

Run this first when anything looks wrong. It distinguishes "the environment is
broken" from "the data is wrong" in about two seconds.

**Monitored:** critical, `max_age_hours=30`, year-round (no in-season gate).

#### `audit_data` — daily 13:00 UTC

```bash
python -m worker.jobs.audit_data
```

The data-integrity canary: **160 checks** over schema objects and constraints,
RLS posture, the odds math, referential integrity, the anti-lookahead
guarantees, data completeness, value plausibility, cross-source reconciliation
between box scores and play attribution, the distribution-family resolution
layer, Python/SQL agreement on the odds math, the board's display contract, and
the calibration outputs the report is rendered from. Exits non-zero if any check
fails.

Every check states a **pass condition** rather than printing a number for a
human to eyeball. A report that only shows counts cannot fail, and a check that
cannot fail is not a check.

**Standing rule: run this at the end of every phase, and after any ingest.**
Phases 4a–4d skipped it and a real defect sat in `picks` the whole time — every
row stored a de-vigged book probability and none recorded which method produced
it. The defects this job exists for are all silent ones: an endpoint truncating
at 2,000 rows, ids arriving as strings so a join matched nothing, a ratings
endpoint returning post-game values, CFBD swapping passer and receiver labels on
touchdown plays. None of them raised.

**Monitored:** warning, `max_age_hours=30`, year-round.

#### `monitor_pipeline` — every 6 hours

```bash
python -m worker.jobs.monitor_pipeline
python -m worker.jobs.monitor_pipeline --dry-run     # evaluate, send nothing
```

Four checks, in this order for a reason:

1. **Stuck runs** — `running` for more than `STUCK_AFTER_HOURS` (6.0).
2. **Latest run failed** — cheap, and the one people expect.
3. **Staleness** — no *success* within the job's `max_age_hours`. Keys on
   `finished_at where status = 'succeeded'` and nothing else, because a stuck
   run has a recent `started_at`.
4. **Data freshness** — a job can succeed and write nothing. The provider
   returned an empty list, the week resolved to the wrong number, the filter
   excluded everyone. Checks 1–3 all go green on that. This is the check that
   asks whether the board a reader opens actually has anything on it.

Expectations live in `MONITORED_JOBS` in the same module, one per scheduled job.
`tests/test_monitor.py` parses `render.yaml` and asserts the two agree: that
every scheduled job has an expectation, that every expectation names a job that
is scheduled, and that each `max_age_hours` leaves room for one missed run.

Six-hourly rather than hourly **is the throttle** — alerts are deliberately not
deduplicated, because suppression needs state and a suppression bug is silent by
construction. A critical finding exits non-zero while the run itself is recorded
`succeeded`, so Render's own cron-failure notification becomes the backstop
channel for the one thing this job cannot watch: itself.

Where alerts go is `app_config.alert_adapter` — `"log"` by default, which is a
real destination and not an off switch.

**Not monitored** (nothing can monitor the monitor's own liveness from inside).

### Weekly pipeline

#### The Sunday chain — 09:00 UTC

```bash
python -m worker.jobs.ingest_reference &&
python -m worker.jobs.ingest_stats &&
python -m worker.jobs.ingest_ratings &&
python -m worker.jobs.build_splits
```

**Chained with `&&` in one cron, deliberately.** These four are strictly
ordered — splits are built from stats, which need the games and rosters
reference data — and Render has no dependency graph between cron services. Four
separate schedules guessing at each other's runtime would race. The chain stops
at the first failure and each step still writes its own `pipeline_runs` row, so
the monitor reports precisely which one broke.

**`ingest_reference`** — conferences, teams, venues, games, players. Seasons come
from `app_config.backfill_seasons` so scope is a row edit, not a deploy. Refuses
to start if the CFBD account cannot afford the work: a partial backfill leaves
the database in a state no row count honestly describes.

```bash
python -m worker.jobs.ingest_reference --seasons 2024      # override config
python -m worker.jobs.ingest_reference --dry-run           # preflight only
```

**`ingest_stats`** — `player_game_stats`, `plays`, `play_player_stats`. The
highest-volume job in the pipeline, roughly 1M attribution rows per season across
all FBS and ~950 CFBD calls. Runs **one season at a time** by default: a season
is loaded, measured, and only then is the next started, rather than discovering
the storage ceiling a season and a half in with no clean way to say which half is
trustworthy. Two preflights guard it — CFBD quota and database headroom.

```bash
python -m worker.jobs.ingest_stats --seasons 2024
python -m worker.jobs.ingest_stats --seasons 2023 --box-scores-only
python -m worker.jobs.ingest_stats --skip-headroom-check    # bypass the storage guard
```

`--box-scores-only` loads `player_game_stats` and stops — no play-by-play, no
attribution. That is the right mode for a **prior** season whose only job is to
supply prior-year features for the season after it.

**`ingest_ratings`** — `team_rating_snapshots` and `game_weather`. Cheap, about
20 calls per season, because point-in-time Elo is read from already-cached
`/games` responses rather than fetched again.

Note the standing limitation: **only Elo is genuinely weekly.** SP+, SRS and FPI
are season-level from CFBD and cannot serve as point-in-time backtest features.

**`build_splits`** — `defense_position_game_splits` and
`defense_position_ratings`: the position-split engine and the opponent
adjustment (CLAUDE.md §5). **Reads only from the database, no API calls**, so it
can be re-run freely while attribution logic is refined. That is what the
response cache is for.

```bash
python -m worker.jobs.build_splits --seasons 2024
```

**Monitored:** all four at `max_age_hours=200`; `ingest_stats` and `build_splits`
critical.

#### `run_projections` — Tuesday 09:00 UTC

```bash
python -m worker.jobs.run_projections --all-weeks              # what the cron runs
python -m worker.jobs.run_projections --season 2025 --weeks 10
python -m worker.jobs.run_projections --season 2025 --weeks 10 --dry-run
```

Writes `projections` (one distribution per player-market, **always**) and `picks`
(the over/under call, **only where a line exists to call against**). Also writes
a `model_runs` row.

**That split is the late-line behaviour CLAUDE.md §7 requires.** `picks.line` is
`not null`, and a line comes from one of two places: a book that has posted, or
`markets.default_line` for a market whose line is structural rather than priced —
anytime TD is "over 0.5 touchdowns" whether or not anyone is taking bets. So
early in the week a yardage market shows a projection with `has_call = false` and
the board renders the model's lean from p10/p50/p90. When a book posts, this job
runs again and the call fills in on the same row. Nothing about the projection
changes; only the question being asked of it.

**Re-running replaces the week.** Each run writes under a fresh `model_run_id`
and `v_board_rows` does not filter by run, so leaving the old rows would double
every player on the board. The week's projections are deleted and rewritten in
one transaction — picks cascade — which also means a failed run leaves the
previous week's board intact rather than half of a new one.

**It loads calibration from the newest completed backtest and logs which one,
plus the entry count.** If that log line names an old backtest id, the run is
wasted — see [Recovery](#recovery).

`--synthetic-lines` is a **development aid whose edges are meaningless.** It
posts −110/−110 quotes at each player's trailing average under a book named
`DEV (synthetic)`. Those de-vig to exactly 0.500, which makes `edge` a
restatement of the model's own confidence rather than a disagreement with a
market. It refuses to run outside `ENVIRONMENT=development`.

**Monitored:** critical, `max_age_hours=200`. Without this the board has nothing
on it.

### In-week refreshes

#### `ingest_odds` — every 3 hours

```bash
python -m worker.jobs.ingest_odds                     # current slate
python -m worker.jobs.ingest_odds --season 2026 --week 1
python -m worker.jobs.ingest_odds --dry-run           # resolve, write nothing
python -m worker.jobs.ingest_odds --event-limit 10    # bound the spend
```

Attaches book lines to games already projected, writing `player_prop_lines` and
`sportsbooks`. Every three hours because college books post props **late**, often
Thursday or Friday for a Saturday game — this is a poll for a market that has not
appeared yet, not a refresh of one that has.

Three things it refuses to do:

1. **Guess an identity.** Provider strings resolve through
   `worker.core.name_match`, which returns a refusal rather than a best guess. A
   line attached to the wrong player produces a confident, precise, *wrong* edge
   and breaks nothing visible.
2. **Report an impression instead of a rate.** Every unresolved event, player and
   market is counted and logged.
3. **Destroy line history.** `player_prop_lines` is **append-only**; each capture
   is a new row keyed by `captured_at`. The closing-line hit-rate basis is
   reconstructed from that history, so delete-and-replace would quietly foreclose
   an option the client has not chosen yet. Only the synthetic DEV rows are ever
   replaced, and those are fake by construction.

**Quota discipline:** billing is per market **returned**, not requested, so
`--event-limit` is the lever that bounds spend. See [odds.md](odds.md).

**Monitored:** warning, `max_age_hours=12`, gated on `app_config.odds_adapter`
not being `"none"`.

#### `generate_ai_reads` — Wednesday 14:00 UTC

```bash
python -m worker.jobs.generate_ai_reads                          # current slate
python -m worker.jobs.generate_ai_reads --season 2025 --week 11
python -m worker.jobs.generate_ai_reads --dry-run                # prompts + cost, no calls
python -m worker.jobs.generate_ai_reads --limit 3                # below the configured cap
```

Writes `ai_reads`, one row per player per week. Wednesday because it wants
Tuesday's projections and wants to be done before the weekend.

**The cache is the schema.** `ai_reads` is unique on `(player_id, season, week)`
and the application only ever SELECTs from it — there are no per-page-view LLM
calls (CLAUDE.md §2, §10). This job is the only writer.

What it refuses to spend: `app_config.ai_reads_max_per_run` (400) is a hard
ceiling, and a player whose inputs have not moved is **skipped, not
regenerated** — `ai_reads.input_digest` makes that decidable, since the same
facts under the same prompt version mean the stored read is still the right read.
A busy week is ~1,700 players with projections and this runs against the client's
account, so stopping early is recoverable where a surprise invoice is not.

What it refuses to store: a truncated read, an empty read, or one the provider
declined. Each is recorded as a failure the next run retries. The unique key
means a bad row would sit in front of readers for a week, so writing nothing is
strictly better than writing a fragment.

**Monitored:** warning, `max_age_hours=200`, gated on `app_config.ai_adapter`
not being `"none"`.

---

## Jobs run by hand

Neither of these is scheduled, and neither should be.

### `run_backtest`

```bash
python -m worker.jobs.run_backtest
python -m worker.jobs.run_backtest --seasons 2024 --max-week 8
python -m worker.jobs.run_backtest --render-only          # from stored metrics, no walk
python -m worker.jobs.run_backtest --persist-predictions  # hundreds of thousands of rows
```

The walk-forward backtest and the calibration report. Produces
[calibration-report.html](calibration-report.html) — the Phase 3 deliverable and
the client review gate. Writes `backtests`, `backtest_metrics`, `calibration_bins`
and a `model_runs` row.

**`--render-only` rebuilds the report from a finished run without walking two
seasons again.** Every run stores its headline metrics in `backtest_metrics` and
its curves in `calibration_bins` for exactly this reason: before it existed,
changing one sentence of the report cost forty minutes, because the numbers lived
nowhere but inside the HTML.

**Individual `backtest_predictions` rows are not stored by default.** A full walk
generates hundreds of thousands of them and the development database sits on
Supabase's 500 MB free tier. The report renders from the in-memory run either
way, so `--persist-predictions` buys row-level forensics rather than the
deliverable itself.

**Gotcha: the `backtests` row is written *before* the metrics and the report.** A
watcher polling for that row fires roughly three minutes early. Poll
`backtest_metrics` instead.

### `probe_odds`

```bash
python -m worker.jobs.probe_odds
python -m worker.jobs.probe_odds --free                   # spend the free-tier key
python -m worker.jobs.probe_odds --historical-date 2025-11-08T18:00:00Z
python -m worker.jobs.probe_odds --skip-historical        # historical is billed at a premium
```

Measures what The Odds API actually serves, and **overwrites
[odds-coverage-probe.md](odds-coverage-probe.md)** with its findings. That file
is a generated artefact — nothing hand-written survives there. The durable
interpretation lives in [odds.md](odds.md).

**Quota discipline:** defaults to one event; widen deliberately with
`--event-limit`. The paid allowance is a **shared pool across the client's other
models** and has already run out once mid-month, so use `--free` for dry runs.

Re-run this **within 7 days of kickoff** to resolve live prop coverage, which is
the one question the probe has not been able to answer yet.

---

## Recovery

### The monitor says a run is stuck

A `running` row older than 6 hours is a process that was killed without Python
catching anything. **Mark it failed with a note rather than deleting it** — the
row is the evidence that a run was attempted.

```sql
update pipeline_runs
   set status = 'failed',
       finished_at = now(),
       error = 'killed without unwinding; marked failed by hand on <date>'
 where status = 'running'
   and started_at < now() - interval '6 hours';
```

`model_runs` strands the same way and takes the same treatment. Then work out
*why* it died — a projection pass that runs out of memory will do it again next
Tuesday.

### The Sunday chain failed partway

The chain stops at the first failure, so downstream steps did not run on stale
inputs. Find which step broke:

```sql
select job_name, status, started_at, finished_at, error
  from pipeline_runs
 where started_at > now() - interval '2 days'
 order by started_at;
```

Fix it, then **re-run from the failed step onward, in order**. The jobs are
idempotent — reference and ratings upsert, stats reload the season, splits
rebuild from the database — so re-running a step that already succeeded is safe
but wasteful. Re-running one *out of order* is the actual hazard: `build_splits`
on stale stats produces a defensive rating that looks entirely plausible.

### `run_projections` produced a board but the edges look wrong

Check which calibration it loaded. The job logs the backtest id, its date and
the entry count at startup:

```
Calibration from backtest <uuid> (2026-08-02): 74 entries
```

If that id is not the newest completed backtest, the run took stale corrections
and is wasted. **Order is backtest *then* projections.** Re-run `run_backtest`,
confirm `backtest_metrics` has rows for it, then re-run `run_projections` — or
point it at a specific run with `--backtest-id`.

If no backtest has stored a calibration snapshot at all, the job refuses to start
rather than quietly publishing raw distributions. `--no-calibration` is how you
ask for those deliberately; they are the overconfident ones the Phase 3
improvement round corrected, so they are for comparison, not for a board anyone
reads.

A smaller graded population leaves fewer cells with enough data to correct, so
the entry count moving is not by itself a fault: it dropped from 87 to 74 when
the postseason week-axis fix removed 7,880 predictions.

### The board is empty

In order of likelihood:

1. `run_projections` has not run for the current slate — check `pipeline_runs`.
2. It ran against the wrong week — `worker/core/schedule.py` resolves the slate
   from kickoff times, so a `games` table missing the coming week resolves to the
   wrong one.
3. Row limits, not emptiness. PostgREST caps a response at **1,000 rows** and
   says nothing about it; the read layer detects truncation rather than trusting
   the count.

### An alert channel is not delivering

Alerting is the one component that cannot report its own outage. Check
`app_config.alert_adapter` first: `"webhook"` without `ALERT_WEBHOOK_URL` set in
the worker environment fails loudly by design rather than falling back to the
log. `--dry-run` evaluates every check and prints what it would send.

---

## Applying a migration

**There is no `supabase` CLI on PATH in this environment.** `npx supabase db
push` is the documented path in [../README.md](../README.md) and it works where
the CLI is available; where it is not, migrations get applied by running the SQL
through psycopg **and** recording them in the ledger:

```sql
insert into supabase_migrations.schema_migrations (version, name, statements)
values ('20260803120000', 'alert_adapter_config', array[...]);
```

**Do both.** Applying without recording is how the ledger drifted in Phase 5 —
three migrations were live in the database and absent from
`supabase_migrations.schema_migrations`, which makes the next `db push` either
skip them or try to replay them. Both halves or neither.

Confirm afterwards:

```sql
select version, name from supabase_migrations.schema_migrations order by version;
```

The list should match the filenames in `supabase/migrations/` exactly.

---

## Ordinary weekly operation

Nothing here needs a human when it is working. The sequence a normal week runs:

| When (UTC) | What | Result |
|---|---|---|
| Sun 09:00 | reference → stats → ratings → splits | last week's games ingested, splits rebuilt |
| Tue 09:00 | `run_projections --all-weeks` | the board has this week's rows, most without calls |
| Wed 14:00 | `generate_ai_reads` | one cached read per player |
| Thu–Sat, every 3h | `ingest_odds` | calls and edges fill in as books post |
| daily 12:00 / 13:00 | `healthcheck`, `audit_data` | canaries |
| every 6h | `monitor_pipeline` | says something when the above stops |

The one thing a human still does deliberately is re-run `run_backtest` when the
model changes — and then `run_projections`, in that order.
