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
- [Jobs run by hand](#jobs-run-by-hand) — backtest, odds probe, the migration
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

The data-integrity canary: **182 checks** over schema objects and constraints,
RLS posture, the odds math, referential integrity, the sport dimension and the
four joins where a mislabelled row would mix two sports, the anti-lookahead
guarantees, data completeness, value plausibility, cross-source reconciliation
between box scores and play attribution, the distribution-family resolution
layer, Python/SQL agreement on the odds math, the board's display contract, the
Analyze Games index agreeing with the board about what a game contains, the
evidence figures the board prints beside every card, the opening-weekend
universe rule re-derived from raw rows, and the calibration outputs the report is
rendered from. Exits non-zero if any check fails.

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

   Week 1 is compared against **the same week of the prior season**, not against
   earlier weeks of this one — it has none, which is why an empty opening
   weekend was invisible to every check until Phase 6c. That is not a
   hypothetical: an empty 2026 week 1 fires this alert today, because 2025 week
   1 holds 4,669 projections and 2026 has no roster to build one from.

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
python -m worker.jobs.ingest_reference --current &&
python -m worker.jobs.ingest_stats --current &&
python -m worker.jobs.ingest_ratings --current &&
python -m worker.jobs.ingest_rankings --live --current &&
python -m worker.jobs.build_splits --current
```

**Chained with `&&` in one cron, deliberately.** These are strictly ordered —
splits are built from stats, which need the games and rosters reference data —
and Render has no dependency graph between cron services. Separate schedules
guessing at each other's runtime would race. The chain stops at the first failure
and each step still writes its own `pipeline_runs` row, so the monitor reports
precisely which one broke.

**`--current` on every step, and it is load-bearing.** Without it each job falls
through to `app_config.backfill_seasons`, which scopes the *historical backfill*
and lags a season behind the moment a new one starts. On 2026-08-13 that was
`[2024, 2025]`, sixteen days before the 2026 kickoff: the chain would have run to
completion, reported success, and refreshed last season. See
[configuration.md](configuration.md#ingest-and-cost-control) for the measurement.

**`ingest_reference`** — conferences, teams, venues, games, players. Seasons come
from `app_config.current_season` under `--current`, otherwise
`app_config.backfill_seasons`, so scope is a row edit, not a deploy. Refuses
to start if the CFBD account cannot afford the work: a partial backfill leaves
the database in a state no row count honestly describes.

```bash
python -m worker.jobs.ingest_reference --seasons 2024      # override config
python -m worker.jobs.ingest_reference --current           # the in-season scope
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
python -m worker.jobs.ingest_stats --current                # the in-season scope
python -m worker.jobs.ingest_stats --seasons 2023 --box-scores-only
python -m worker.jobs.ingest_stats --skip-headroom-check    # bypass the storage guard
```

**The storage guard estimates from what is actually missing.** It measures the
real cost of a game of play-by-play from the tables themselves (~0.10 MB on
production, all three tables including indexes) and charges for two things: the
games played but not yet loaded, and the transient cost of rewriting the rows a
season already has, because `ingest_plays` clears a season before reinserting it
and Postgres keeps the old versions until vacuum. The cap is
`app_config.db_size_cap_mb`.

It used to charge a flat 105 MB per season — the cost of a *finished* season —
which is wrong twice over for a weekly in-season run and is why the whole Sunday
chain refused to start on 2026-08-13. **A refusal now writes a `failed`
`pipeline_runs` row.** It previously wrote nothing at all, so the monitor
reported `never-succeeded`, which is also what it reports for a job nobody has
ever run: a hard refusal was indistinguishable from silence.

`--box-scores-only` loads `player_game_stats` and stops — no play-by-play, no
attribution. That is the right mode for a **prior** season whose only job is to
supply prior-year features for the season after it.

**`ingest_ratings`** — `team_rating_snapshots` and `game_weather`. Cheap, about
20 calls per season, because point-in-time Elo is read from already-cached
`/games` responses rather than fetched again.

Note the standing limitation: **only Elo is genuinely weekly.** SP+, SRS and FPI
are season-level from CFBD and cannot serve as point-in-time backtest features.

**`ingest_rankings`** — `team_poll_rankings`: the AP, Coaches and CFP polls
that back the board's Top 25 filter. Two CFBD calls per season, no odds credits.

**Point-in-time with no offset, and that was proven rather than assumed.** CFBD's
week N poll is the poll published *entering* week N, reflecting games through
week N-1. Verified against 2025: every ranked team that LOST in week 1 still
holds its ranking in the week 1 poll and drops in the week 2 poll (Texas #1 to
#7, Alabama #8 to #21, Kansas State #17 to unranked). So a join on `week` is
already free of lookahead. Week 1 is the preseason poll.

**FBS polls only.** `/rankings` returns five polls per week and three are for
other divisions (FCS, D-II, D-III). The adapter keeps an allow-list so an
unrecognised poll is skipped and logged rather than silently ingested into a
filter that claims to mean "Top 25 of college football".

```bash
python -m worker.jobs.ingest_rankings --seasons 2025
python -m worker.jobs.ingest_rankings --live --current   # what the cron runs
```

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

#### `ingest_game_lines` — daily 10:00 UTC

```bash
python -m worker.jobs.ingest_game_lines --live --current    # what the cron runs
python -m worker.jobs.ingest_game_lines --seasons 2025      # backfill a season
python -m worker.jobs.ingest_game_lines --seasons 2026 --weeks 1 2 --live
python -m worker.jobs.ingest_game_lines --dry-run
```

**`--current` matters most here**, because this is the job that ran daily
against the wrong season for as long as the cron existed. Without it the
fallback is `backfill_seasons`, and a `game_lines` row for a settled season is
indistinguishable in the logs from one for the season about to be played.

`game_lines` — the game spread, total and moneylines shown as context on the
player card. One row per game per provider; `v_game_line_consensus` takes the
median across providers so the board's number does not depend on which book
happened to be present.

**This is CFBD and costs ZERO Odds API credits.** Do not confuse it with
`ingest_odds`, which spends the metered quota on PLAYER props. About 16 calls
per season against a 30,000/month CFBD allowance.

`--live` re-fetches instead of serving the permanent response cache. Completed
weeks are immutable and cached forever, but the CURRENT week's spread moves all
week, so without it the job succeeds while writing last Tuesday's number.

Two conventions worth knowing before touching this:

- **The spread is from the HOME team's perspective** — negative means the home
  team is favoured. Verified against CFBD's own `formattedSpread` across the
  whole 2025 week-8 slate, 228 of 228 rows agreeing. `v_board_rows` flips it to
  the player's own team before display.
- **CFBD spells DraftKings two ways.** Across 2025, 805 rows said `DraftKings`
  and 56 said `Draft Kings`, and **56 games carried both** — which made that one
  book vote twice in the median. `canonical_provider` collapses them; unknown
  providers pass through unchanged and every raw spelling is logged, so the next
  alias is visible rather than silently merged.

**Monitored:** `max_age_hours=48`, warning severity — a stale spread degrades
context on the card, it does not empty the board.

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

**It also writes `projections.ladder`** — the alternate-line rungs (migration
0039), computed from the same calibrated distribution the stored quantiles
describe. Nothing extra to run; a normal weekly run fills it.

```bash
python -m worker.jobs.run_projections --backfill-ladders   # rows predating 0039
python -m worker.jobs.run_projections --backfill-ladders --dry-run
```

`--backfill-ladders` exists because the column arrived after 81,198 projections
already had. It fills `ladder` **and nothing else** — no new `model_run`, no new
projection or pick ids — so it can be run against a live board without changing
what anyone is reading, which a re-projection could not. It is idempotent: only
null ladders are selected, so an interrupted run resumes by being run again. It
ignores every other flag and exits.

**Monitored:** critical, `max_age_hours=200`. Without this the board has nothing
on it.

### In-week refreshes

#### `ingest_odds` — every 6 hours

```bash
python -m worker.jobs.ingest_odds                     # current slate
python -m worker.jobs.ingest_odds --season 2026 --week 1
python -m worker.jobs.ingest_odds --dry-run           # resolve, write nothing
python -m worker.jobs.ingest_odds --event-limit 10    # bound the spend
```

Attaches book lines to games already projected, writing `player_prop_lines` and
`sportsbooks`. Polled rather than scheduled once because college books post props
**late**, often Thursday or Friday for a Saturday game — this is a poll for a
market that has not appeared yet, not a refresh of one that has.

**This is the only job that spends metered credits, and its cadence is its
budget.** Billing is per market returned and a capture re-buys the whole slate
every time — ~9 markets on every event carrying props, with no discount for a
line that has not moved. It ran every 3 hours until 2026-08-12; on the
20,000-credit plan the probe measured, a full slate at 8 runs/day would have
consumed the entire monthly allowance in about a week, from a pool shared with
the client's other models.

The production cron passes `--event-limit 120`, which is a **circuit breaker, not
a budget** — keep it above the slate size. The largest week measured is 99 games
(2026 week 1; opening weekends are inflated by FBS-vs-FCS fixtures). Tightening
it below that does not save money safely: the job stops fetching at the limit and
skips the rest of the provider's stably-ordered event list, so the same tail of
the slate goes unpriced every run, all week. The run logs a `TRUNCATED` warning
and counts the skipped events when this happens, because nothing downstream can
tell "we stopped asking" from "no book posted a line".

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

**Monitored:** warning, `max_age_hours=18`, gated on `app_config.odds_adapter`
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

None of these is scheduled, and none should be. Two of them spend money.

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

**Since Phase 6c `--all-weeks` starts at week 1, not week 3.** There is no week
floor left in `projectable_weeks`: eligibility is entirely a per-player question
and lives in `is_projectable`, so a week with nobody eligible produces nothing —
a fact about the roster rather than a rule about the calendar. One consequence
worth knowing: the `season_type = 'regular'` predicate is now the ONLY thing
keeping bowl games off the board. Before 6c a mislabelled postseason game sat at
week 1 and the floor excluded it as well.

**Since Phase 6b.3 the walk starts at week 1, not week 3.** Weeks 1-2 are graded
under their own universe rule — a prior season of at least four games in place of
two current-season ones — and reported as their own phase stratum rather than
folded into "early". Two consequences when comparing runs: the overall figures
now average in two weeks that no earlier run contained, and the `phase` group
keys changed (`wk1-2 opening`, `wk3-6 early`, `wk7+ late`). A `transfer` grouping
was added alongside, because a weeks 1-2 projection is last season's production
and last season's production does not travel evenly across a transfer.

**NEVER quote opening-weekend calibration from a single-season walk.** The
correction layer is fitted point-in-time from earlier data in the same walk, and
its `priors` history bucket only ever fills in weeks 1-2 — which happen once per
season. A walk over one season has nothing to fit that cell on, so the opening
weeks come out carrying their raw bias: a 2025-only walk puts them at ECE 0.0396
with P(over) running 3.4 points low, against 0.0184 for the same weeks in the
2024-2025 walk. Skill is unaffected (+0.2074, still the season's highest), which
is exactly why this is easy to miss. The job logs a warning and the report grows
a caveat when the condition holds; the fix is to include an earlier season. When
2026 runs, walk 2024-2025-2026, not 2026 alone.

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

### `backfill_odds`

```bash
python -m worker.jobs.backfill_odds --season 2025 --weeks 8 --dry-run --adapter theoddsapi
python -m worker.jobs.backfill_odds --season 2025 --weeks 8 --adapter theoddsapi \
    --max-credits 2000 --exclude-markets anytime_td
```

`--adapter theoddsapi` is currently required: `app_config.odds_adapter` is
`none`, and unlike `ingest_odds` this job **refuses** rather than exiting 0.
Nobody schedules a backfill by accident, and a silent no-op would read as
"there were no lines to find".

**Always pass `--exclude-markets anytime_td`.** Measured over 20 games of 2025
week 8: anytime TD was **1,802 of 2,709 prices bought and 0 of them two-way**.
Two thirds of the spend, none of it gradeable. Excluding it takes a priced game
from ~57 credits to ~50.

**Re-running is free for what it already has.** Games with stored closing lines
from this adapter are skipped. This matters because runs routinely stop at a
ceiling or on a network blip, so finishing a week means running again — and the
second week-8 run paid for 20 games it already had before this existed. Nothing
looked wrong afterwards: `captured_at` is part of the unique key, so the
duplicate rows were discarded and only the credits were gone. `--refresh` buys
a second snapshot deliberately.

Buys historical **closing** lines for past weeks and writes them to
`player_prop_lines` with `is_closing = true`. This is what makes edge gradeable:
every backtest number so far was measured against a synthetic line — the
player's own trailing average — which proves the model is **calibrated** but not
that it is **profitable**. See [odds.md](odds.md).

**The measurement is the purchase.** Billing is per market *returned*, so there
is no way to count how many games carried props without paying for the ones
that did. What is bought is kept, so a week paid for once is graded repeatedly.

**Snapshots are per game, not per week.** Each game is asked about at
`kickoff - --lead-minutes` (default 60). A single timestamp for a whole slate is
how the original probe ended up querying a game that had kicked two hours
earlier, getting an empty 200, and recording `historical player props: FAIL` —
a wrong negative that then shaped Phase 3. Games kicking together share one
event-list call, which is the only per-snapshot charge.

**Two independent spend limits, both checked against the provider's reported
cost rather than an estimate:**

- `--max-credits` — this run's ceiling, default 1000 (roughly one week).
- `--min-remaining` — a floor on the shared pool, default 5000. The client's
  MLB, tennis and WNBA models draw on the same allowance, which has run out
  mid-month once already.

Both are evaluated *before* each billable call, and both **reserve the call's
worst case** (10 credits per market asked for) rather than waiting to be
crossed. A first version compared only what had already been spent, which let a
single 60-credit call through a 25-credit ceiling and then reported "reached the
25-credit ceiling (spent 61)". Hitting either limit stops the run and says so in
the report; rows already written stand.

**`--dry-run` is cheap but not free.** It resolves games to provider events and
reports the match rate, and it **never asks for props** — props are where all
the cost is. It still pays 1 credit per kickoff snapshot for the event list, so
a week costs roughly as many credits as it has distinct kickoff times. Use it to
see the slate before committing to a spend.

Measured 2026-08-05: one event list = 1 credit; one game returning 6 markets =
60 credits, i.e. the documented 10 per market returned. A game books did not
price costs 0.

**Read the two-way rate per market, never blended.** A one-sided price cannot be
de-vigged and yields no edge at all. Anytime TD is posted Yes-only by most books
and will dominate any blended figure — one early sample read 94% one-sided and
meant nothing. The report breaks it out per market for that reason.

Re-running a week is **idempotent**: `captured_at` holds the snapshot moment and
is part of the table's unique key.

**Not monitored, by design.** A job that spends the client's money on a schedule
is a job that empties the pool on a schedule.

### `grade_vs_book`

```bash
python -m worker.jobs.grade_vs_book --season 2025 --weeks 8
python -m worker.jobs.grade_vs_book --season 2025 --weeks 6-8 --threshold 0.05
```

Grades existing `projections` against the real closing lines `backfill_odds`
bought, settling them on `player_game_stats`. Free — it makes no provider calls.

**This is the only job that tests whether the model is PROFITABLE.**
[calibration-report.html](calibration-report.html) tests whether it is
*calibrated*, against synthetic lines (each player's trailing average). Those
are different claims: a perfectly calibrated model loses money against a price
better than its own estimate. Every edge % on the board is the second claim.

**Every win rate is reported with its break-even.** At −110 that is 52.4%, so a
51% win rate is a losing model however well calibrated. A win rate quoted alone
is the most misleading number this project can produce.

**ROI at the median price is the headline**; the best-price figure assumes
shopping every book and always getting filled.

It refuses to grade three things, each of which would flatter the result: a
one-sided price (no de-vig, so no edge — comparing against the vig-inclusive
number credits the model with beating the book's hold), a missing box-score row
(scoring an inactive player as 0 makes every one an UNDER hit), and a projection
whose `as_of_week` is later than the week being graded. Exact ties settle as
pushes, not losses. `--adapter synthetic` is refused outright.

**A negative result is a finding, not a failed run.** Report it.

### `migrate_database`

```bash
python -m worker.jobs.migrate_database                # the plan; writes nothing
python -m worker.jobs.migrate_database --self-test    # prove the copy path
python -m worker.jobs.migrate_database --execute      # move the data
python -m worker.jobs.migrate_database --verify-only  # compare the two ends
```

Copies the development database into the production one — deployment step 5.
Source is `SUPABASE_DB_URL`, target is `MIGRATION_TARGET_DB_URL`. Run once, from
a machine that can reach both.

**This is a migration, not a re-ingest, and the distinction is money.**
`player_prop_lines` holds 5,752 real closing lines bought for ~3,800 Odds API
credits, and historical props are sold per date rather than re-derived. A
`backfill_odds` run against a fresh production database would come up silently
without them and every profitability number in `grade_vs_book` would go with
them.

**It truncates each target table before loading it.** That is what makes it
re-runnable, and it is also why it refuses to run when source and target resolve
to the same database. A failed run leaves production partially loaded; re-run
`--execute` and it starts clean.

**How it decides the two ends are different, and why not the obvious way.** It
takes an advisory lock on the source and tries to re-take it on the target;
advisory locks are per-database, so success proves separate databases and it
writes nothing. It does **not** compare `system_identifier` from
`pg_control_system()`, which is the textbook test and is wrong on Supabase:
every project is provisioned from one base image, so unrelated projects report
the *same* identifier — measured 2026-08-09, development and production both
reported `7666007964130682852` (and `pg_database.oid` 5) while one held 28
tables and the other none. That comparison refused the real migration. If the
probe itself cannot run the job stops rather than guessing, because a
same-database run has equal row counts on both ends and sails straight through
the direction check.

**The pre-flight is the useful part.** Before writing anything it confirms the
target's migration ledger is complete, that both ends have identical column
lists for every planned table, and that no table in `public` is missing from the
plan. Any of those failing means stop and fix, not proceed carefully.

**Every table is verified by row count and by a checksum over every column,
generated columns included** — so the target's generated expressions are checked
rather than assumed. `--verify-only` re-runs just that comparison and is the
honest answer to "did it all actually arrive".

Two things it does that are invisible and load-bearing. It **resets all 22
identity sequences** after loading, because COPY writes explicit ids and leaves
every sequence at 1 — without this the first production insert collides on a
primary key days later, nowhere near the migration. And it moves
**`backtest_predictions` for the latest persisted walk only**: all 526k rows
cost 129 MB on a 500 MB tier, but moving none of them fails three `audit_data`
checks that resolve through the most recently persisted run.

**Not scheduled, and never should be.** It is a one-time deployment step.

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

**An opening-weekend board is NOT one of these causes.** Weeks 1 and 2 project
and publish since Phase 6c, and a week 1 that comes out empty is a real fault
that `monitor_pipeline` now alerts on by comparing against the prior season's
same week. If the board is thin rather than empty in the opening weeks, read the
note above the cards: it states how many rows rest on fewer than four effective
games and whether any defense carries a rating yet, both counted from the week on
screen.

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
| daily, every 6h | `ingest_odds` | calls and edges fill in as books post |
| daily 12:00 / 13:00 | `healthcheck`, `audit_data` | canaries |
| every 6h | `monitor_pipeline` | says something when the above stops |

The one thing a human still does deliberately is re-run `run_backtest` when the
model changes — and then `run_projections`, in that order.
