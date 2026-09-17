-- =============================================================================
-- 0068 — A market that publishes no call publishes no NUMBER either
-- =============================================================================
-- WHAT WENT WRONG, CAUGHT IN A SCREENSHOT BEFORE IT SHIPPED
-- ---------------------------------------------------------
-- 0066 added `publishes_call` and the board's card and table both honoured it:
-- no OVER/UNDER pill, no confidence, no edge, no projected-vs-line bar. The
-- first-quarter card still printed a **GRADE badge** in its header — a B, next
-- to a line and a hit rate and nothing else.
--
-- The grade is `gradeFor(card.topConfidence)`, and `topConfidence` is the
-- maximum `display_confidence` across the card's markets. It carries no new
-- information; it is the withheld confidence, rounded to a letter. Three
-- components had been taught the rule and a fourth, one level up, restated the
-- number anyway.
--
-- THE FIX IS NOT A FOURTH GUARD. This repo has shipped the same class of bug
-- five times — two surfaces answering one question independently and drifting —
-- and the pattern that ends it is one source, read by everyone. `v_board_rows`
-- is that source: every board surface, the card grouping, the cheat sheet and
-- board_counts() all read it, and it already joins `markets`.
--
-- So the VIEW withholds. A market with `publishes_call = false` returns NULL
-- for every column that is this product's opinion:
--
--   side, confidence, display_confidence   the call
--   model_prob_over                        the probability behind it
--   edge                                   the disagreement with the book
--   projected_median, _p10, _p90           the distribution it all comes from
--
-- and returns everything that is FACT unchanged: the line, the book, both
-- prices, the kickoff, the opponent, the rank. A reader of this view cannot
-- print a first-quarter call by forgetting to ask, because there is nothing to
-- print. The component-level guards stay as they are — they now decide LAYOUT
-- (a column that is absent rather than empty), which is a display decision, and
-- they no longer carry the correctness.
--
-- WHAT IS DELIBERATELY LEFT ALONE
-- -------------------------------
--   picks             stores the real side, confidence and edge. They are
--                     computed the same way for every market and `grade_vs_book`
--                     reads that table directly — a Q1 grade against DraftKings
--                     is how these markets earn a call later, and nulling the
--                     source would make that ungradeable.
--   book_prob_over    the de-vigged BOOK price. It is the market's number, not
--                     ours. Nothing renders it today; it is left readable
--                     because withholding someone else's opinion is not what
--                     this column is for.
--   has_call          a pick row exists, which is how the line is attached. It
--                     is plumbing, not a claim.
--
-- Everything else about the view is the 0066 definition unchanged, dumped with
-- pg_get_viewdef so nothing is lost in retyping it.
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
    case when m.publishes_call then p.side end as side,
    case when m.publishes_call then p.confidence end as confidence,
    case when m.publishes_call then p.model_prob_over end as model_prob_over,
    p.book_prob_over,
    case when m.publishes_call then p.edge end as edge,
    COALESCE(p.has_book_line, false) AS has_book_line,
    p.id IS NOT NULL AS has_call,
    p.over_price,
    p.under_price,
    p.sportsbook_key,
    p.sportsbook_name,
    case when m.publishes_call then pr.p50 end as projected_median,
    case when m.publishes_call then pr.p10 end as projected_p10,
    case when m.publishes_call then pr.p90 end as projected_p90,
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
    m.publishes_call
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
