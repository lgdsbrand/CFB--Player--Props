# Odds source: findings

CLAUDE.md §9.1 leaves the odds source open — "it's unconfirmed whether the plan
covers NCAAF player props". This is the answer, as far as it has been measured.

The source is **The Odds API** (the-odds-api.com). Ingestion is a pluggable
adapter (`app_config.odds_adapter`) so the provider can change, and the app
degrades to model leans when there is no line to call against.

> **This file is hand-written and durable.**
> [odds-coverage-probe.md](odds-coverage-probe.md) is **generated** — every
> `python -m worker.jobs.probe_odds` run overwrites it. Put nothing there you
> want to keep. That file is the raw measurement; this one is what it means.

---

## Resolved

**Historical player props are served on the paid plan. Edge is backtestable.**

This reverses what Phase 3 assumed, and it **reopens the closing-line hit-rate
basis** (CLAUDE.md §9.2), which had been written off as unreachable. Calibration
— "when the model says 60%, does it hit 60%?" — never needed odds at all. Edge
does, because edge is `model probability − de-vigged book implied probability`
and there is no book probability without a two-way price.

Measured on 2025-10-18 snapshots, not inferred from documentation:

- 4 books returned data: `betonlineag`, `bovada`, `draftkings`, `williamhill_us`.
- 6 of our 9 markets have been observed across probes: `anytime_td`, `pass_tds`,
  `pass_yds`, `reception_yds`, `rush_yds`, `rush_attempts`.

**Billing is per market *returned*, not per market requested.** 10 credits per
market per event on historical. An event carrying no props costs **0**, and
`/sports` and `/events` cost 0. This is why probing many events is nearly free
and only the answer is billed — and it is why `--event-limit` on `ingest_odds`
is the meaningful spend lever.

**Coverage skews hard to marquee games.** Carried props: Purdue @ Northwestern
(Big Ten), FSU @ Stanford (ACC — 4 books, 5 markets, 142 outcomes, 50 credits).
Carried none: Buffalo @ UMass, Troy @ UL Monroe, Nevada @ New Mexico,
Lafayette @ Oregon State.

**Props exist only near kickoff.** Next week's games returned nothing, at 0
credits. The schedule posts a month out; the props do not. This is the same fact
the late-line behaviour in CLAUDE.md §7 is built around, confirmed from the
provider side.

---

## The constraint that actually bites: one-sided prices

**A one-sided price cannot be de-vigged and yields no edge at all.** Not a small
edge — none. `devig_over_prob` returns `NULL` where only one side is priced, and
callers must treat that as "no book probability" rather than as zero edge.

One historical sample ran **94% one-sided**. That number is close to meaningless
as a headline, because it was dominated by anytime TD, which most books post
**Yes-only**. Read it per market or not at all.

This is the real ceiling on edge coverage, and it is a property of how books
price college props rather than anything about the plan or the adapter. A market
we can project perfectly still produces no edge if nobody posts both sides of it.

---

## Still open

**Live prop coverage is unmeasured.** Books have not posted for 2026 — the
soonest game is 2026-08-29, so realistically **~22 August**. Until then a probe
of live events returns an empty response at 0 credits, and *that response says
nothing*. Re-run `probe_odds` within 7 days of kickoff.

**The American may be thin.** Four of the five displayed conferences are P4; the
American is G5. Coverage skewing to marquee games is measured, so verify the
American specifically before promising coverage on it.

**Three markets have never been observed:** `receptions`, `pass_attempts`,
`pass_completions`. **This is a floor, not a measurement.** Absence in a sample
of two events is not evidence of absence on the plan.

**The per-slate carry rate is not measured.** A historical backfill only pays for
events books actually priced, so the cost of one depends entirely on what share
of a slate carries props. Measure that over a full slate before budgeting a
backfill.

---

## Budget

Two keys, both from the environment and never from `app_config`:

| Variable | Tier | Allowance |
|---|---|---|
| `ODDS_API_KEY` | paid | 20,000 credits/month |
| `ODDS_API_KEY_FREE` | free | 500 credits/month |

**The paid allowance is a shared pool across the client's other models** (MLB,
tennis, WNBA) and has already been exhausted once mid-month. `probe_odds --free`
spends the free key instead, so proving the adapter works cannot spend production
credits by accident. The probe logs which key it used, because a coverage finding
means nothing without knowing which tier produced it.

Live refresh estimate: ~40 displayed-conference games × ~6 markets × 2 refreshes
per week ≈ **2,000 credits/month**. Comfortable inside 20,000, but not inside
whatever is left after the other models have taken their share — which is the
number to watch.

The paid key read 15,920 of 20,000 on 2026-08-02. About 115 of that was probing;
the pool had already dropped ~4,000 that month from the client's other models.

---

## The provider quirk that cost us a phase

**HTTP 401 means both "your plan does not include this" and "you are out of
credits".** Only the `error_code` body field (`OUT_OF_USAGE_CREDITS`)
distinguishes them. `OddsQuotaError` separates the two; conflating them reads a
billing state as a coverage finding.

Related, and the more expensive one: **a 200 with an empty body is not a
negative result.** `probe_historical` originally asked `events[0]` for props.
That event had kicked two hours *before* the snapshot timestamp, and books pull
props at kickoff — so it returned nothing, and the probe wrote
`historical player props: FAIL` into the report, where it was believed and shaped
Phase 3.

The probe now only asks games that had not yet kicked, keeps asking past empty
ones (they are free), stops at the first that pays, and reports a
200-with-no-markets as **UNRESOLVED, never FAIL**. Twelve tests in
`worker/tests/test_probe_odds.py`, each replayed against the old rule to prove it
bites.

That failure shape — *the call succeeded, returned nothing, and nothing raised* —
has now appeared three times in this project. For a measurement job it is the one
that matters most, because a wrong FAIL gets acted on.

---

## Market mapping

| our `markets.key` | The Odds API key |
|---|---|
| `anytime_td` | `player_anytime_td` |
| `pass_attempts` | `player_pass_attempts` |
| `pass_completions` | `player_pass_completions` |
| `pass_tds` | `player_pass_tds` |
| `pass_yards` | `player_pass_yds` |
| `rec_yards` | `player_reception_yds` |
| `receptions` | `player_receptions` |
| `rush_attempts` | `player_rush_attempts` |
| `rush_yards` | `player_rush_yds` |

---

## Turning it on

`app_config.odds_adapter` is `"none"` today: `ingest_odds` reports that it is
switched off, writes nothing, exits 0, and the board shows model leans. That is a
supported product state, not a degraded one.

To switch it on, with `ODDS_API_KEY` already set in the worker environment:

```sql
update app_config set value = '"theoddsapi"'::jsonb where key = 'odds_adapter';
```

Then confirm with a dry run before letting the cron spend anything:

```bash
python -m worker.jobs.ingest_odds --dry-run
```

The six-hourly cron picks it up from there. `monitor_pipeline` starts expecting
the job to succeed within 18 hours the moment the adapter stops reading `"none"`,
so switching this on also switches on the alerting for it.

**Flipping this config row is what starts the spending** — the cron itself is free
while the adapter reads `"none"`, because the job exits before constructing an
adapter or making a single call. So the cadence and `--event-limit` in
`render.yaml` want to be right *before* this flip, not after it.

### Opening weekend with an empty paid pool

The situation this was built for, measured 2026-08-24: the paid pool is
**0 of 20,000** and does not reset until **1 September**, while kickoff is
**29 August**. The free key holds ~400 credits and a full priced slate is
roughly 230, so it covers **about one Saturday refresh** — a fallback, not a
budget.

**Why this is an environment variable and not a cron.** `render.yaml` cannot
express a one-shot. `test_monitor.py` parses the blueprint and cross-checks it
against `MONITORED_JOBS`, and two of its guards bite here: it keys jobs by
*module*, so a second cron running `ingest_odds` would silently collapse onto
the first, and `_cron_period_hours` scores **any** `dow`- or `dom`-restricted
schedule as 168h, which would fail `ingest_odds`'s 18h `max_age_hours` and
could only be "fixed" by blinding the monitor for eight days. So the fallback
is flipped at runtime instead, and the blueprint is left alone.

The sequence, all reversible, none of it a deploy:

1. In the Render dashboard, set **`ODDS_PREFER_FREE=1`** on the
   `cfb-props-worker` env group.
2. Flip the adapter on:
   ```sql
   update app_config set value = '"theoddsapi"'::jsonb where key = 'odds_adapter';
   ```
3. Trigger `cfb-props-odds-refresh`, or wait for the next 6-hourly run. Run it
   **as late as possible before kickoff** — books post Thu/Fri, and coverage
   5 days out was one book and a quarter of the slate.
4. **Confirm rows were actually written**, not merely that the job succeeded —
   see the warning below.
5. Set `odds_adapter` back to `"none"` once the board is populated. That stops
   the next 6-hourly run spending the rest of the free key, and re-silences
   the monitor.

**A typo in the variable fails every job, not just this one.** `ODDS_PREFER_FREE`
is read by `get_settings()`, which every job calls, and an unrecognised value
raises rather than defaulting to false — so `ture` exits **2** with a named
error on the healthcheck too, within minutes of being set. That is the intended
trade: the alternative is a silent false that bills the empty paid pool and
leaves the board blank while every job reports success. Verified 2026-08-24.

**Running out of credits exits 0, not 1.** `fetch_props` catches
`OddsQuotaError`, keeps what it already wrote, and returns success — correct,
because a partial slate is worth keeping, and it means an empty pool never
sends Render failure emails. The consequence is that **job success does not
imply lines exist**: with the adapter on and no credits, this job succeeds
every 6 hours while the board shows model leans. `monitor_pipeline` cannot see
that; it only checks that the job ran. Check `rows_written` on the
`pipeline_runs` row, or just look at the board.

`/events` and `/sports` are billed at 0, so the slate list and the credit
balance are always readable even at zero remaining.

### Which allowance a run bills

There are two keys. `ODDS_API_KEY` is the paid pool, **shared with the client's
MLB, tennis and WNBA models**, and it has already reached zero mid-month.
`ODDS_API_KEY_FREE` is a separate 500-credit allowance.

`ingest_odds` bills the paid pool by default. `--free` bills the free one:

```bash
python -m worker.jobs.ingest_odds --free --event-limit 120
```

The job logs the pool by name on every run that constructs an adapter, because
`--free` **falls back to the paid key when `ODDS_API_KEY_FREE` is unset** rather
than refusing to run — so the flag alone never proves which allowance was spent.
That fallback warns.

This exists for one situation: the paid pool is empty and a slate still needs
lines. Measured 2026-08-24, live: **1 credit per market returned per event**.
Events no book has priced cost nothing, and books are free — one market call
returns every book — so cost scales with `markets x events`, never with how many
books post. A full priced slate is roughly 228 credits, so the free key's 500
buys about two refreshes. It is a fallback, not a budget.

See [configuration.md](configuration.md) for the adapter seam and
[runbook.md](runbook.md#ingest_odds--every-6-hours) for what the job does.
