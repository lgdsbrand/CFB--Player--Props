-- =============================================================================
-- 0070 — First-quarter allowed per game, point-in-time. RAW ONLY.
-- =============================================================================
-- The client reads the board in one sweep: "I'm going to look at books line,
-- their last 5/10 hit rate, what the defense might give up in 1st quarter and
-- then play whatever." Migration 0069 put the first-quarter observations where
-- the player page can show them game by game. This puts the cumulative figure
-- where the BOARD can show it, so the third thing he scans is a column rather
-- than a click.
--
-- RAW, NOT ADJUSTED, AND DELIBERATELY NOT RANKED
-- ----------------------------------------------
-- Every other per-game column on this table has an `adj_` sibling and feeds
-- `rank_vs_position`. These three do not, and that is the finding rather than
-- an omission. Measured on NFL 2023-25, splitting each season at week 9 and
-- asking whether a defense's first-half rate predicts its second-half rate
-- (Spearman, 32 defenses):
--
--   metric                        whole game        first quarter
--   WR receiving yards allowed    +0.35 / -0.11 / +0.37    +0.10 / -0.22 / -0.08
--   TE receiving yards allowed    -0.07 / +0.28 / +0.15    +0.22 / -0.09 / +0.09
--   RB rushing yards allowed      +0.16 / +0.31 / +0.10    -0.05 / +0.43 / +0.03
--
-- Mean about +0.17 whole-game against about +0.05 for the first quarter, and
-- NEGATIVE in four of the nine first-quarter cells. The same test against the
-- rank this table already publishes (as_of_week 10, fitted on weeks < 10, vs
-- what was actually allowed in weeks 10-18) gives +0.18 whole-game and +0.10
-- for a raw first-quarter rate.
--
-- So a first-quarter RANK would be an ordering the data does not contain: a
-- "3rd toughest vs WR in the 1st quarter" badge, colour-coded, carrying about
-- a tenth of the information a reader would take from it. An opponent
-- adjustment fitted on a quarter of the sample at roughly twice the noise would
-- add confidence, not accuracy -- `splits.py` already warns that the fit
-- reports a confident spurious gap wherever the schedule graph is thin.
--
-- What IS defensible is the average itself. "This defense has allowed 27.8
-- receiving yards to wide receivers per first quarter" is an observation with a
-- stated denominator, not a claim about next Sunday, and it is exactly what the
-- client asked to look at. The board labels it as allowed-per-game and never as
-- a rank.
--
-- POINT-IN-TIME LIKE EVERY ROW HERE. These are written by the same loop, under
-- the same `as_of_week`, from games with `week < as_of_week` only. CLAUDE.md §4
-- calls applying end-of-season knowledge to an earlier week a silent,
-- disqualifying bug; nothing about a raw average exempts it.
--
-- BOTH SPORTS, because 0069 computes the splits for both and this is their
-- average. College has no first-quarter market to show them against.
-- =============================================================================

alter table defense_position_ratings
  add column if not exists q1_rush_yards_allowed_pg  numeric(6,3),
  add column if not exists q1_rec_yards_allowed_pg   numeric(6,3),
  add column if not exists q1_receptions_allowed_pg  numeric(6,3),
  add column if not exists q1_targets_pg             numeric(6,3);

comment on column defense_position_ratings.q1_rec_yards_allowed_pg is
  'Receiving yards allowed to this position PER GAME in the first quarter, over '
  'games before as_of_week. RAW -- no opponent adjustment and no rank, because a '
  'first-quarter rate was measured not to predict itself (see migration 0070).';

-- =============================================================================
-- The board carries the one that matches the position
-- =============================================================================
-- `v_board_rows` already joins `defense_position_ratings` for
-- `opponent_rank_vs_position`, so this is two more columns off a row that is
-- already in hand -- no extra join, no extra scan on a read whose ceiling is
-- CPU rather than query shape.
--
-- BOTH COLUMNS, and the app picks. Which one a position is read on is decided
-- by `RANK_METRICS` in `worker/core/splits.py` and mirrored by `rankBasis` in
-- `lib/core/defense-view.ts` -- rushing for QB and RB, receiving for WR and TE.
-- Encoding that rule a third time here, in SQL, is how three copies of one
-- decision drift; the view hands over both numbers and the module that already
-- owns the rule chooses.
--
-- Appended last: create-or-replace may only add columns at the end.
-- =============================================================================

create or replace view v_board_rows as
SELECT pr.id AS projection_id,
    p.id AS pick_id,
    pr.season,
    pr.week,
    pr.market_key,
    m.display_name AS market_name,
    m.short_label AS market_label,
    m.emoji AS market_emoji,
    m.is_binary,
    pl.id AS player_id,
    pl.name AS player_name,
    pts.position_group,
    t.id AS team_id,
    t.school AS team_school,
    t.abbreviation AS team_abbreviation,
    t.color AS team_color,
    t.alt_color AS team_alt_color,
    o.id AS opponent_team_id,
    o.school AS opponent_school,
    o.abbreviation AS opponent_abbreviation,
    g.id AS game_id,
    g.start_date,
    g.neutral_site,
    g.home_team_id = t.id AS is_home,
    p.line,
        CASE
            WHEN m.publishes_call THEN p.side
            ELSE NULL::bet_side
        END AS side,
        CASE
            WHEN m.publishes_call THEN p.confidence
            ELSE NULL::numeric
        END AS confidence,
        CASE
            WHEN m.publishes_call THEN p.model_prob_over
            ELSE NULL::numeric
        END AS model_prob_over,
    p.book_prob_over,
        CASE
            WHEN m.publishes_call THEN p.edge
            ELSE NULL::numeric
        END AS edge,
    COALESCE(p.has_book_line, false) AS has_book_line,
    p.id IS NOT NULL AS has_call,
    p.over_price,
    p.under_price,
    p.sportsbook_key,
    p.sportsbook_name,
        CASE
            WHEN m.publishes_call THEN pr.p50
            ELSE NULL::numeric
        END AS projected_median,
        CASE
            WHEN m.publishes_call THEN pr.p10
            ELSE NULL::numeric
        END AS projected_p10,
        CASE
            WHEN m.publishes_call THEN pr.p90
            ELSE NULL::numeric
        END AS projected_p90,
    pr.prior_weight,
    dpr.rank_vs_position AS opponent_rank_vs_position,
    c.name AS conference_name,
    c.is_displayed AS conference_is_displayed,
        CASE
            WHEN NOT m.publishes_call THEN NULL::numeric
            WHEN p.id IS NULL THEN NULL::numeric
            WHEN m.is_binary THEN p.model_prob_over
            ELSE p.confidence
        END AS display_confidence,
    pr.effective_sample,
    v.name AS venue_name,
    v.city AS venue_city,
    v.state AS venue_state,
        CASE
            WHEN gl.spread IS NULL THEN NULL::double precision
            WHEN g.home_team_id = pr.team_id THEN gl.spread
            ELSE - gl.spread
        END AS team_spread,
    gl.over_under AS game_total,
    gl.providers AS game_line_providers,
    tr.rank AS team_poll_rank,
    orank.rank AS opponent_poll_rank,
    g.sport,
    pr.ladder,
    COALESCE(g.start_date <= now(), false) AS has_kicked_off,
    m.publishes_call,
    dpr.q1_rush_yards_allowed_pg as opponent_q1_rush_yards_allowed_pg,
    dpr.q1_rec_yards_allowed_pg as opponent_q1_rec_yards_allowed_pg
   FROM projections pr
     JOIN markets m ON m.key = pr.market_key
     JOIN players pl ON pl.id = pr.player_id
     JOIN teams t ON t.id = pr.team_id
     JOIN teams o ON o.id = pr.opponent_team_id
     JOIN games g ON g.id = pr.game_id
     LEFT JOIN LATERAL ( SELECT pk.id,
            pk.line,
            pk.side,
            pk.confidence,
            pk.model_prob_over,
            pk.book_prob_over,
            pk.edge,
            pk.has_book_line,
            pk.over_price,
            pk.under_price,
            bk.key AS sportsbook_key,
            bk.display_name AS sportsbook_name
           FROM picks pk
             LEFT JOIN sportsbooks bk ON bk.id = pk.sportsbook_id
          WHERE pk.projection_id = pr.id
          ORDER BY (COALESCE(bk.priority::integer, 32767)), pk.id
         LIMIT 1) p ON true
     LEFT JOIN player_team_seasons pts ON pts.player_id = pr.player_id AND pts.team_id = pr.team_id AND pts.season = pr.season
     LEFT JOIN team_seasons ts ON ts.team_id = pr.team_id AND ts.season = pr.season
     LEFT JOIN conferences c ON c.id = ts.conference_id
     LEFT JOIN venues v ON v.id = g.venue_id
     LEFT JOIN v_game_line_consensus gl ON gl.game_id = g.id
     LEFT JOIN LATERAL ( SELECT r.rank
           FROM team_poll_rankings r
          WHERE r.team_id = pr.team_id AND r.season = pr.season AND r.week = pr.week
          ORDER BY (poll_priority(r.poll))
         LIMIT 1) tr ON true
     LEFT JOIN LATERAL ( SELECT r.rank
           FROM team_poll_rankings r
          WHERE r.team_id = pr.opponent_team_id AND r.season = pr.season AND r.week = pr.week
          ORDER BY (poll_priority(r.poll))
         LIMIT 1) orank ON true
     LEFT JOIN defense_position_ratings dpr ON dpr.defense_team_id = pr.opponent_team_id AND dpr.season = pr.season AND dpr.as_of_week = pr.week AND dpr.position_group = pts.position_group;
