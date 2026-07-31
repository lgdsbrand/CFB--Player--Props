-- =============================================================================
-- 0007 — Projections, picks, cached AI reads
-- =============================================================================
-- CLAUDE.md §1: the projection is the engine, the over/under call plus
-- confidence is the surface. That relationship is enforced here rather than
-- assumed:
--   * projections stores a DISTRIBUTION (family + parameters), not a point.
--   * picks derives side, confidence and edge FROM that distribution using
--     generated columns and a check constraint, so the displayed call can never
--     disagree with the probability it was supposedly derived from.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- projections — the model's actual output
-- -----------------------------------------------------------------------------
create table projections (
  id                  bigint generated always as identity primary key,
  model_run_id        uuid not null references model_runs(id) on delete cascade,
  player_id           bigint not null references players(id) on delete cascade,
  game_id             bigint not null references games(id) on delete cascade,
  team_id             bigint not null references teams(id),
  opponent_team_id    bigint not null references teams(id),
  market_key          text not null references markets(key),
  season              smallint not null,
  week                smallint not null,
  as_of_week          smallint not null,

  distribution        distribution_family not null,
  params              jsonb not null,

  mean                numeric,
  p10                 numeric,
  p25                 numeric,
  p50                 numeric,
  p75                 numeric,
  p90                 numeric,

  prior_weight        numeric,
  effective_sample    numeric,

  created_at          timestamptz not null default now(),
  unique (model_run_id, player_id, game_id, market_key),
  constraint projections_as_of_week_positive check (as_of_week >= 1)
);

comment on table projections is
  'One outcome DISTRIBUTION per player, game and market. Storing family + params rather than a point estimate is what lets the over/under probability for any line be recomputed later without re-projecting — which is exactly what the late-line behaviour in CLAUDE.md §7 needs: project every projected starter and high-usage skill player early in the week, then derive picks as books post lines on Thursday or Friday.';

comment on column projections.params is
  'Distribution parameters keyed by family, e.g. {"mu": 247.3, "sigma": 58.1} for normal, {"r": 4.2, "p": 0.31} for negative_binomial, {"p": 0.42} for bernoulli. Validated in the worker against the declared family.';

comment on column projections.as_of_week is
  'KNOWLEDGE CUTOFF (inherited from model_runs). Features used were restricted to week < as_of_week. For a live weekly run this equals week; a backtest of week 6 must carry as_of_week = 6.';

comment on column projections.prior_weight is
  'Share of this projection carried by priors rather than current-season production. Transfer portal and NIL churn mean prior-year output often happened at another school, so early-season projections lean on priors and sharpen as the season accumulates (CLAUDE.md §6). Surfaced so the UI can widen the displayed range honestly instead of implying false precision.';

comment on column projections.p10 is
  'Distribution quantiles, cached for display. These back the SECONDARY projected-range detail on the player page — never the headline claim (CLAUDE.md §1).';

create index projections_week_market_idx
  on projections (season, week, market_key);
create index projections_player_idx
  on projections (player_id, season, week);
create index projections_game_idx
  on projections (game_id, market_key);

-- -----------------------------------------------------------------------------
-- picks — the surface the board reads
-- -----------------------------------------------------------------------------
create table picks (
  id                  bigint generated always as identity primary key,
  projection_id       bigint not null references projections(id) on delete cascade,
  line_id             bigint references player_prop_lines(id) on delete set null,
  sportsbook_id       bigint references sportsbooks(id),

  -- denormalized so the board can be read without joining back through
  -- projections on every row
  player_id           bigint not null references players(id) on delete cascade,
  game_id             bigint not null references games(id) on delete cascade,
  team_id             bigint not null references teams(id),
  opponent_team_id    bigint not null references teams(id),
  market_key          text not null references markets(key),
  season              smallint not null,
  week                smallint not null,

  line                numeric(6, 2) not null,
  side                bet_side not null,
  model_prob_over     numeric not null,
  book_prob_over      numeric,
  over_price          integer,
  under_price         integer,

  confidence          numeric generated always as (
                        greatest(model_prob_over, 1 - model_prob_over)
                      ) stored,

  edge                numeric generated always as (
                        edge_on_side(model_prob_over, book_prob_over, side)
                      ) stored,

  has_book_line       boolean generated always as (line_id is not null) stored,

  created_at          timestamptz not null default now(),

  constraint picks_model_prob_is_a_probability
    check (model_prob_over >= 0 and model_prob_over <= 1),
  constraint picks_book_prob_is_a_probability
    check (book_prob_over is null or (book_prob_over >= 0 and book_prob_over <= 1)),
  -- The call must be the side the majority of the distribution falls on.
  constraint picks_side_matches_probability check (
    (side = 'over' and model_prob_over >= 0.5)
    or
    (side = 'under' and model_prob_over < 0.5)
  )
);

comment on table picks is
  'The over/under call and confidence for one projection against one line — the row the main board renders. A pick exists as soon as there is a projection: line_id and the book fields stay NULL until a book posts, so a player with no line yet still shows the model''s lean and the confidence-vs-line call fills in when the market appears (CLAUDE.md §7).';

comment on column picks.confidence is
  'THE HEADLINE NUMBER. Probability mass of the projected distribution on the called side. Generated from model_prob_over so the displayed confidence can never drift from the probability it derives from (CLAUDE.md §1).';

comment on column picks.edge is
  'Generated via edge_on_side(): model probability minus DE-VIGGED book implied probability on the called side. NULL when no two-way book price exists — meaning "no edge computable", not "zero edge". The edges-only filter and the threshold in app_config.edge_threshold (their pitcher model uses 5%) both read this column.';

comment on column picks.side is
  'Constrained to agree with model_prob_over. The call is the side the majority of the distribution falls on — that is the definition, not a convention.';

comment on column picks.book_prob_over is
  'De-vigged fair probability of the over, from devig_two_way(). Never the raw implied probability.';

comment on column picks.line is
  'The line the call was made against: the book line when one exists, otherwise markets.default_line (e.g. 0.5 for anytime_td).';

-- One pick per projection per book; the no-book-line case is a single row.
create unique index picks_projection_book_key
  on picks (projection_id, coalesce(sportsbook_id, 0));

create index picks_board_idx
  on picks (season, week, market_key, edge desc nulls last);
create index picks_player_idx
  on picks (player_id, season, week);
create index picks_edges_only_idx
  on picks (season, week, edge desc)
  where edge is not null;

-- -----------------------------------------------------------------------------
-- ai_reads — weekly cached LLM analysis
-- -----------------------------------------------------------------------------
create table ai_reads (
  id              bigint generated always as identity primary key,
  player_id       bigint not null references players(id) on delete cascade,
  season          smallint not null,
  week            smallint not null,
  content         text not null,
  model           text not null,
  prompt_version  text not null,
  input_digest    text,
  tokens_in       integer,
  tokens_out      integer,
  generated_at    timestamptz not null default now(),
  unique (player_id, season, week)
);

comment on table ai_reads is
  'Short LLM read per player per week. The unique constraint IS the cache: generation is a weekly worker job and the app only ever selects from this table. Per-page-view LLM calls are explicitly out of scope (CLAUDE.md §2, §10).';

comment on column ai_reads.input_digest is
  'Hash of the inputs the read was generated from, so a stale read can be detected and regenerated when the underlying projection changes materially within a week.';
