-- 0094: the client's NFL fantasy game model's projections (phase F2, 2026-10-14).
--
-- One row per game per version of the formula (core/fantasy_model.py):
--   'sheet' = his Google Sheet's formula exactly, faults included;
--   'fixed' = the same with its four faults corrected.
-- The user approved showing both and letting the client choose.
--
-- ITS OWN TABLE, NOT game_projections. That table holds our game model's
-- score DISTRIBUTION (p10/p90 ranges, first half, first quarter), all NOT
-- NULL, and the games table reads it. The sheet makes a point projection and
-- a win % only; faking ranges to fit would be wrong, and mixing the two
-- models in one table would put the client's numbers on our model's columns.
--
-- REWRITTEN UNTIL KICKOFF, NEVER AFTER. The run only writes games that have
-- not started, from the lineups and inputs frozen for that week
-- (fantasy_lineups, fantasy_team_inputs), so a past game keeps the last
-- projection made before it. No sport column (CLAUDE.md §3): sport comes
-- through game_id.

create table fantasy_game_projections (
  game_id            bigint not null references games(id) on delete cascade,
  model_version      text not null check (model_version in ('sheet', 'fixed')),
  season             smallint not null,
  week               smallint not null,
  away_score         numeric(5, 2) not null,
  home_score         numeric(5, 2) not null,
  total              numeric(5, 2) not null,
  home_margin        numeric(5, 1) not null,
  p_home_win         numeric(6, 5) not null check (p_home_win > 0 and p_home_win < 1),
  away_fantasy       numeric(5, 1) not null,
  home_fantasy       numeric(5, 1) not null,
  away_terms         jsonb not null,
  home_terms         jsonb not null,
  inputs_as_of_week  smallint not null,
  made_at            timestamptz not null default now(),
  primary key (game_id, model_version)
);

comment on table fantasy_game_projections is
  'The client''s NFL fantasy game model per game (migration 0094): projected scores, total, home margin and home win % from his sheet''s formula, in two versions: ''sheet'' (exact, faults included) and ''fixed'' (four faults corrected; core/fantasy_model.py). Rewritten until kickoff from that week''s frozen lineups and team inputs, never after.';
comment on column fantasy_game_projections.home_margin is
  'Home score minus away score, rounded to 0.1 as the sheet rounds it (Model!D41) before its win %.';
comment on column fantasy_game_projections.p_home_win is
  'The sheet''s win %: 1 / (1 + e^(-0.23 x home_margin)). Not calibrated.';
comment on column fantasy_game_projections.away_terms is
  'Each term of the away score (fantasy, massey, fpi, red_zone, third_down, pace, epa_play, epa_total), so a number on the page can be explained. home_terms adds home_field.';
comment on column fantasy_game_projections.inputs_as_of_week is
  'The fantasy_team_inputs week used: games before this week only (CLAUDE.md §4).';

create index fantasy_game_projections_week_idx on fantasy_game_projections (season, week);

alter table fantasy_game_projections enable row level security;

create policy fantasy_game_projections_public_read on fantasy_game_projections
  for select to anon, authenticated using (true);
