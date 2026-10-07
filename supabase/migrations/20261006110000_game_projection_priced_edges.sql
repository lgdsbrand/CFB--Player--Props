-- =============================================================================
-- 0082 — The game model's comparison with one book's price, per game
-- =============================================================================
-- THE CLIENT'S ASK (2026-10-06): the games table shows book spread, our
-- spread, spread edge, book total, our total, total edge. The user instructed
-- that the edge be shown publicly, overriding the G4 decision to show the fair
-- line only (CLAUDE.md §11, amended the same day).
--
-- WHY THE MODEL WRITES THIS AND THE SITE DOES NOT COMPUTE IT. The edge is the
-- house definition (CLAUDE.md §6): the model's probability of a side minus the
-- book's vig-free probability of the same side. For a spread or total the
-- model's probability is taken AT THE BOOK'S LINE from the simulated scores,
-- which exist only while `run_game_model` runs. Storing the mean and the
-- 10th-90th range is not enough to reproduce it. So each run records, next to
-- the projection, the quote it compared against and its probability of that
-- quote's first side (home cover, or over). The site removes the vig and takes
-- the difference; it never prices anything itself.
--
-- THE SAME QUOTE THE SHADOW PICKS USE. `choose_quote` in
-- `worker/core/game_picks.py`: Pinnacle, then DraftKings, then FanDuel, with
-- an implausible two-way price skipped. A table edge and a graded pick on the
-- same game therefore never disagree about the line or the book.
--
-- NULL MEANS NOT PRICED: no listed book had a usable price, or the last odds
-- capture was older than the job's freshness limit, in which case the run
-- clears these rather than leaving an edge against a stale price.
--
-- REWRITTEN EVERY RUN UNTIL KICKOFF, like the rest of the row. The model runs
-- 30 minutes after each game-odds capture (render.yaml), so the stored line is
-- the latest captured one at most half an hour after it lands.
--
-- An addition, so applying it before the code that writes it is safe: the
-- columns sit NULL until the next run.
-- =============================================================================

alter table game_projections
  add column spread_sportsbook_id   bigint references sportsbooks(id),
  add column spread_line            numeric(5, 1),
  add column spread_home_price      integer,
  add column spread_away_price      integer,
  add column spread_model_home_prob numeric(6, 5),
  add column total_sportsbook_id    bigint references sportsbooks(id),
  add column total_line             numeric(5, 1),
  add column total_over_price       integer,
  add column total_under_price      integer,
  add column total_model_over_prob  numeric(6, 5),
  add constraint game_projections_spread_prob_range check (
    spread_model_home_prob is null
    or (spread_model_home_prob > 0 and spread_model_home_prob < 1)
  ),
  add constraint game_projections_total_prob_range check (
    total_model_over_prob is null
    or (total_model_over_prob > 0 and total_model_over_prob < 1)
  ),
  -- All of a market's columns or none: an edge needs the line, both prices
  -- and the model's probability together.
  add constraint game_projections_spread_complete check (
    num_nulls(spread_sportsbook_id, spread_line, spread_home_price,
              spread_away_price, spread_model_home_prob) in (0, 5)
  ),
  add constraint game_projections_total_complete check (
    num_nulls(total_sportsbook_id, total_line, total_over_price,
              total_under_price, total_model_over_prob) in (0, 5)
  );

comment on column game_projections.spread_line is
  'The full-game spread the model was compared against, from OUR HOME team''s side (negative = home favoured), at the book in spread_sportsbook_id (Pinnacle, then DraftKings, then FanDuel). NULL = not priced. See migration 0082.';
comment on column game_projections.spread_model_home_prob is
  'The model''s probability that the home team covers spread_line, from the simulated margins with pushes excluded. Edge = this minus the vig-free home price, or its mirror for the away side (CLAUDE.md §6).';
comment on column game_projections.total_line is
  'The full-game total the model was compared against, at total_sportsbook_id. NULL = not priced.';
comment on column game_projections.total_model_over_prob is
  'The model''s probability the game goes over total_line, from the simulated totals with pushes excluded.';

comment on table game_projections is
  'The game model''s projection per game (CLAUDE.md §11): margin (home minus away) and total for full game, 1H and 1Q with 10th-90th percentile ranges, home win probability, and since 0082 the full-game spread and total it was compared against with its probability of each, from which the site shows the edge. Rewritten until kickoff, never after.';
