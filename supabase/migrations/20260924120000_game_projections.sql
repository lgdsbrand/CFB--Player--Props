-- =============================================================================
-- 0078 — The game model's projections, and the shadow picks made private
-- =============================================================================
-- G4 of the game model (CLAUDE.md §11). The G3 backtest found the ratings model
-- does NOT beat closing lines (docs/game-model-backtest.html), so G4 ships two
-- things and no public pick:
--
--   game_projections  the model's own numbers per game, shown on the site as a
--                     LABELLED FAIR LINE: projected margin and total for the
--                     full game, 1H and 1Q, each with its 80% range, and the
--                     home win probability. No OVER/UNDER, no cover call.
--   game_picks        (0074) the model's picks, written live and frozen at
--                     kickoff as a SHADOW TEST, graded later against our own
--                     closing captures. Nobody sees them until they earn it.
--
-- -----------------------------------------------------------------------------
-- ONE ROW PER GAME PER MODEL VERSION, REWRITTEN UNTIL KICKOFF
-- -----------------------------------------------------------------------------
-- A projection moves as the week's team strength is rebuilt; the site wants
-- the latest one, not its history. The job never touches a game that has
-- started, so the row a finished game keeps is the last one made before
-- kickoff (`made_at` says when). The pick history that grading needs lives in
-- game_picks, which is append-only.
--
-- Signs follow game_odds: margin is HOME minus AWAY, so a fair home spread is
-- -margin.
-- =============================================================================

create table game_projections (
  game_id          bigint not null references games(id) on delete cascade,
  model_version    text not null,

  -- How much current-season evidence the thinner side had ('early' = two
  -- games or fewer). Early projections lean on last season and miss by more.
  evidence_phase   text not null,

  margin_mean      numeric(5, 1) not null,
  margin_p10       numeric(5, 1) not null,
  margin_p90       numeric(5, 1) not null,
  total_mean       numeric(5, 1) not null,
  total_p10        numeric(5, 1) not null,
  total_p90        numeric(5, 1) not null,
  h1_margin_mean   numeric(5, 1) not null,
  h1_margin_p10    numeric(5, 1) not null,
  h1_margin_p90    numeric(5, 1) not null,
  h1_total_mean    numeric(5, 1) not null,
  h1_total_p10     numeric(5, 1) not null,
  h1_total_p90     numeric(5, 1) not null,
  q1_margin_mean   numeric(5, 1) not null,
  q1_margin_p10    numeric(5, 1) not null,
  q1_margin_p90    numeric(5, 1) not null,
  q1_total_mean    numeric(5, 1) not null,
  q1_total_p10     numeric(5, 1) not null,
  q1_total_p90     numeric(5, 1) not null,

  p_home_win       numeric(6, 5) not null,

  made_at          timestamptz not null default now(),

  primary key (game_id, model_version),
  constraint game_projections_phase_known check (evidence_phase in ('early', 'later')),
  constraint game_projections_prob_range check (p_home_win > 0 and p_home_win < 1)
);

comment on table game_projections is
  'The game model''s projection per game (CLAUDE.md §11, G4): margin (home minus away) and total for full game, 1H and 1Q with 10th-90th percentile ranges, and home win probability. SHOWN AS A LABELLED FAIR LINE ONLY — the G3 backtest found the model does not beat closing lines, so no OVER/UNDER or cover call is derived from it on the site. Rewritten until kickoff, never after.';

alter table game_projections enable row level security;

create policy game_projections_public_read
  on game_projections for select
  to anon, authenticated
  using (true);

-- -----------------------------------------------------------------------------
-- The shadow test stays private
-- -----------------------------------------------------------------------------
-- 0074 gave game_picks a public read policy on the assumption the picks would
-- be on screen. They are not: the model has not earned a public pick, and a
-- table any visitor can read through the public API is published whether or
-- not a page renders it. The worker writes and grades as the table owner,
-- which RLS does not restrict. When the picks earn a page, this is the policy
-- to put back.
drop policy game_picks_public_read on game_picks;
