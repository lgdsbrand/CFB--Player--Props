# Schema reference

Companion to the migrations in [supabase/migrations/](../supabase/migrations/).
Read this first, then the SQL — every table and most non-obvious columns carry a
`COMMENT`, so `\d+ <table>` in psql or the Supabase Studio table view is
self-documenting once the migrations are applied.

## The two rules the schema is built to enforce

### 1. No lookahead bias

CLAUDE.md §4 calls applying end-of-season data to earlier games "a silent,
disqualifying bug" and asks for the schema to enforce this rather than hope for
it. Three mechanisms do that:

**Observation is separated from inference.**
`defense_position_game_splits` holds what one defense allowed to one position in
one game. That is an observation — true forever, no cutoff needed. The
opponent-*adjusted* figure is an inference fitted across games, so it lives in a
different table (`defense_position_ratings`) keyed by `as_of_week`. Because the
raw table is atomic per game, "cumulative through week N" is an aggregation over
`week < N`, which cannot reach forward.

**Season-final ratings cannot masquerade as in-season knowledge.**
CFBD serves end-of-season SP+/FPI for historical seasons. Those are useful for
retrospective sanity checks and catastrophic as backtest features.
`team_rating_snapshots` stores both, discriminated by `snapshot_kind`, under a
CHECK constraint:

```sql
(snapshot_kind = 'point_in_time' and as_of_week is not null and as_of_week >= 1)
or
(snapshot_kind = 'season_final'  and as_of_week is null)
```

A season-final row therefore has no `as_of_week`, so any feature query that
joins on `as_of_week` physically cannot return one. The mistake is
unrepresentable, not merely discouraged.

**The backtest harness has a tripwire.**
`backtest_predictions` carries `check (as_of_week <= week)`. An off-by-one in
the harness fails the INSERT instead of quietly producing a flattering number.

Reads follow the same discipline: `defense_position_splits_through(season, week)`
takes a cutoff as an argument and uses a strict `week <` inequality. There is
deliberately **no** convenience view returning "current splits" with no cutoff —
that is the shape of query that produces lookahead.

### 2. The surface is derived from the distribution

CLAUDE.md §1: the projection is the engine, the over/under call plus confidence
is the surface.

`projections` stores a **distribution** — `distribution_family` plus a `params`
JSONB blob — not a point estimate. Two consequences:

- The over/under probability for *any* line can be recomputed later without
  re-projecting. That is what makes the late-line behaviour work: project every
  projected starter on Tuesday, derive picks when books post on Thursday.
- `picks.confidence` and `picks.edge` are **generated columns**, and
  `picks.side` is bound by a CHECK to agree with `model_prob_over`. The
  displayed call cannot drift from the probability it supposedly came from.

Edge is defined once, in SQL, as `edge_on_side()`:

```
edge = model probability − de-vigged book implied probability   (on the called side)
```

Not `(projection − line) / line`. This matches the client's existing MLB pitcher
model so both boards report comparable numbers (CLAUDE.md §6).

## Table map

### Reference
| Table | Notes |
|---|---|
| `conferences` | `is_displayed` is a **display filter only** — ingest always covers all FBS |
| `teams` | Identity + chip colours. No logo assets anywhere (trademarked) |
| `team_seasons` | Conference/classification per season — realignment makes these season-scoped |
| `venues` | `is_dome`, lat/lon for the Open-Meteo weather fallback |
| `games` | `week` is the time axis for every cutoff in the schema |
| `players` | Identity across schools |
| `player_team_seasons` | Roster membership per season — the transfer-portal table |

### Facts
| Table | Notes |
|---|---|
| `player_game_stats` | **The only home for actuals.** `offensive_tds` generated (rush+rec, excludes passing and return TDs) |
| `plays` | PBP trimmed to what the split engine and goal-line model need |
| `play_player_stats` | Per-play attribution. ~1M rows/season. `position_group` denormalized |

### Point-in-time
| Table | Notes |
|---|---|
| `defense_position_game_splits` | Raw, atomic per game, opponent-**un**adjusted |
| `defense_position_ratings` | Opponent-adjusted, keyed by `as_of_week`, append-only |
| `team_rating_snapshots` | SP+/SRS/Elo/FPI, `snapshot_kind`-guarded |
| `game_weather` | CFBD with Open-Meteo fallback |

### Markets and odds
| Table | Notes |
|---|---|
| `markets` | Catalogue. `stat_column` maps a market to the column it grades against |
| `market_positions` | Drives the position tabs and stat selector |
| `sportsbooks` | |
| `player_prop_lines` | **Append-only** line history; a moved line is a new row |

### Model output
| Table | Notes |
|---|---|
| `model_runs` | Version + git sha + config, for reproducibility |
| `projections` | Distribution family + params + quantiles |
| `picks` | The board row. `confidence`/`edge` generated, `side` CHECK-bound |
| `ai_reads` | Unique on (player, season, week) — the uniqueness **is** the cache |
| `backtests`, `backtest_predictions`, `calibration_bins` | Phase 3 |
| `pipeline_runs` | Job log; backs Phase 5 monitoring |
| `app_config` | Runtime config. **Never secrets** — world-readable |

### Views and functions
| Object | Purpose |
|---|---|
| `defense_position_splits_through(season, week)` | Cumulative splits, strict `week <` cutoff |
| `v_latest_prop_lines` | Newest line per player/market/book + de-vigged probability |
| `v_board_rows` | Main board, one row per **projection** (see below) |
| `v_player_game_log` | Game log for the player detail chart |
| `american_to_implied_probability`, `devig_two_way`, `edge_on_side` | Odds math |

### Why the board is driven by projections, not picks

College books post props late — often Thursday or Friday for Saturday games —
and CLAUDE.md §7 requires the tool to be useful before that. So `v_board_rows`
selects `from projections` and left-joins the pick.

Early in the week a row exists with `has_call = false`, showing the model's lean
via `projected_median` / `p10` / `p90`. When a book posts, a `picks` row appears
and the OVER/UNDER call, confidence and edge fill in on the same board row.
Filtering to `has_call` or `has_book_line` gives the market-attached subset.

Driving the view from `picks` instead would have silently hidden every player
without a posted line — the exact behaviour §7 rules out.

## Anytime touchdown is not a special case

Books price it Yes/No, but the schema represents it as the market whose outcome
column is `offensive_tds` with a line fixed at `0.5`. "Over 0.5" means "scored".
So every market — yards, counts, touchdowns — flows through one code path and
one probability definition, which is what CLAUDE.md §1 means by *every market
speaks the same language*. `markets.is_binary` is only a **display** hint telling
the UI to render a bare probability instead of a line-and-price pair.

## Security posture

RLS is enabled on every table. A table with RLS on and no policy denies
everything, so anything not explicitly granted is closed.

- **Public read** (anon + authenticated): reference data, actuals, splits,
  ratings, markets, lines, projections, picks, AI reads, config. All public
  sports data.
- **Closed**: `plays`, `play_player_stats`, `model_runs`, `pipeline_runs`,
  `backtests`, `backtest_predictions`, `calibration_bins` — large and internal,
  with no UI consumer.
- **No write policies exist anywhere.** The worker writes via the service role,
  which bypasses RLS. A leaked anon key cannot mutate anything.

## Open items carried in the schema

Both unresolved decisions in CLAUDE.md §9 are configuration, not hardcoded:

| `app_config` key | Default | Status |
|---|---|---|
| `hit_rate_basis` | `"threshold"` | §9.2 — open. `player_prop_lines` already keeps full history, so switching to `closing_line` needs no re-ingest of anything but odds |
| `odds_adapter` | `"none"` | §9.1 — open. Board shows model leans only until a source is configured |
| `devig_method` | `"proportional"` | **Unconfirmed.** Must match the client's MLB model; their implementation has not been inspected |
| `edge_threshold` | `0.05` | Matches their pitcher model |
