-- =============================================================================
-- 0073 — The projection is published; the CALL still is not
-- =============================================================================
-- WHAT CHANGES, AND WHAT DELIBERATELY DOES NOT
-- --------------------------------------------
-- 0068 nulled seven columns for any market with `publishes_call = false`. This
-- returns THREE of them — `projected_median`, `projected_p10`, `projected_p90`
-- — and leaves the other four (`side`, `confidence`, `display_confidence`,
-- `model_prob_over`, `edge`) withheld exactly as they were.
--
-- WHY. The client asked on 2026-09-21 for the opposite of what he asked for on
-- the 17th, having seen it: "even if theres not a book line it would still show
-- their last 5/10 stats and would make the mark on where the model projects
-- them at." A prop no book has priced showed a reader nothing at all, and with
-- the odds pool empty since 12 September that is most of the board. The mark is
-- the answer, and the mark is the median.
--
-- IS THIS THE CALL BY ANOTHER ROUTE? It would be, if a line were on screen
-- beside it: median above line is an OVER, and a reader can do that arithmetic.
-- Two things stop it.
--
--   1. The number surfaces in exactly ONE place — the no-line form chart
--      (`components/player/form-chart.tsx`), which renders only when
--      `line IS NULL`. With no line there is no comparison to make.
--   2. Every surface that would draw the projection AGAINST a line is guarded
--      on `markets.publishes_call`, not on these columns being null, and those
--      guards are unchanged: the "Projected range" panel returns null, both
--      `ProjectionBar` call sites are behind `statesNothing`, the board's PROJ
--      column is behind `showsCalls`, and the game page filters the rows out.
--      Walked one by one before writing this.
--
-- So the distribution is readable where it answers the reader's question and
-- absent where it would restate a withheld opinion. The claim 0068 refused to
-- make — that we know which side of a first-quarter line to take — is still
-- not made anywhere.
--
-- WHAT IS UNCHANGED
-- -----------------
--   picks            still stores the real side, confidence and edge, so
--                    `grade_vs_book` can keep measuring them.
--   v_cheat_sheet    still drops these markets. It is a list of calls.
--   board_counts()   same.
--   ladder           already passed through unwithheld; first-quarter rows
--                    carry no rungs, so nothing renders. Verified against
--                    production before this migration, not assumed.
--
-- The body below is 0072's definition with three CASE blocks replaced by the
-- bare columns. Lifted verbatim by script rather than retyped, which is how a
-- column goes missing from a view nobody reads in full.
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
    pr.p50 AS projected_median,
    pr.p10 AS projected_p10,
    pr.p90 AS projected_p90,
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
    dpr.q1_rec_yards_allowed_pg as opponent_q1_rec_yards_allowed_pg,
    dch.blitz_rate      as opponent_blitz_rate,
    dch.blitz_rank      as opponent_blitz_rank,
    dch.heavy_box_rate  as opponent_heavy_box_rate,
    dch.games_included  as opponent_charting_games
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
     LEFT JOIN defense_position_ratings dpr ON dpr.defense_team_id = pr.opponent_team_id AND dpr.season = pr.season AND dpr.as_of_week = pr.week AND dpr.position_group = pts.position_group
     LEFT JOIN defense_charting_ratings dch ON dch.defense_team_id = pr.opponent_team_id AND dch.season = pr.season AND dch.as_of_week = pr.week;
