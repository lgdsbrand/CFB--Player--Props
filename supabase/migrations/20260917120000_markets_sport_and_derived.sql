-- =============================================================================
-- 0066 — markets gains a sport, a parent, and whether it publishes a call
-- =============================================================================
-- THREE COLUMNS, THREE DIFFERENT JOBS. They arrive together because the
-- first-quarter markets need all three at once, but none is a restatement of
-- another and each is read by different code.
--
-- ADDITIVE ONLY, AND THE FIRST-QUARTER MARKETS STAY INACTIVE. Flipping
-- is_active is migration 0067, which must not be applied until the web code
-- that filters on `sport` is DEPLOYED. The currently deployed readMarkets()
-- selects every active market with no sport predicate, so activating these
-- here would put Q1 PASS YDS tabs on the college board the moment this runs.
-- Migration-before-push is safe for additions and wrong for anything a live
-- reader can already see.
--
-- 1. sport — WHICH BOARD A MARKET APPEARS ON
-- ------------------------------------------
-- NULL MEANS EVERY SPORT, and that is the common case: pass_yards is the same
-- market in both leagues and shares one row, one stat column and one
-- distribution family. Only a market that genuinely exists in one league names
-- it. The first-quarter three are NFL-only by force rather than by choice --
-- probed 2026-09-09, college books post zero player quarter or half markets at
-- any book, so there is nothing for a college row to point at.
--
-- Nullable rather than NOT NULL with a default, because "applies to both" is a
-- real state and 'cfb' would be a lie about nine rows. A reader filters
-- `sport is null or sport = $1`.
--
-- 2. parent_market_key — WHAT A DERIVED MARKET IS DERIVED FROM
-- -----------------------------------------------------------
-- A first-quarter market is its parent narrowed to one period: it applies to
-- exactly the positions its parent does and grades against the parent's column
-- prefixed `q1_`. The worker has always known this (projections.
-- first_quarter_catalogue derives the rows rather than listing them, so the
-- position mapping cannot drift from the full-game one). This column is that
-- same fact where the WEB can read it, which is what lets catalogue.ts fill a
-- Q1 market's positions from its parent instead of needing market_positions
-- rows of its own.
--
-- NO market_positions ROWS FOR THE Q1 MARKETS, on purpose, and that is why.
-- Adding them would be a second definition of which positions get which
-- first-quarter market, free to drift from the parent's. It is also what keeps
-- `market_catalogue()` returning exactly the nine full-game rows it always has:
-- that query starts FROM market_positions, so a market with no positions rows
-- is invisible to it whatever is_active says. The weekly run opts into the
-- first-quarter markets explicitly instead.
--
-- 3. publishes_call — WHETHER A SURFACE STATES AN OVER/UNDER AND A CONFIDENCE
-- --------------------------------------------------------------------------
-- The client's decision, 2026-09-17, in his words: "Idk if I'd need a proj but
-- maybe just the books line. Cause I'm going to look at books line, their last
-- 5/10 hit rate, what the defense might give up in 1st quarter and then play
-- whatever."
--
-- So a first-quarter row shows the book's line and the player's own history
-- against it, and states nothing. That is not a display shortcut, it is the
-- honest reading of what we measured: the Q1 walk (2026-09-11) put
-- q1_pass_yards' top bin at 0.94 predicted against 0.71 observed, and the
-- full-game markets it scales from lose to a coin flip against real closing
-- lines ([[model-probabilities-lose-to-a-coin-flip]]). A market that publishes
-- no probability cannot publish a badly calibrated one.
--
-- EXPLICIT RATHER THAN DERIVED FROM parent_market_key IS NOT AN OVERSIGHT.
-- "This is a segment of another market" and "we do not stand behind a number
-- on it" are different claims that happen to coincide today. The day a Q1
-- model earns its call, this is one UPDATE and not a hunt through the readers.
--
-- The projection is still COMPUTED and still stored -- the board is one row per
-- projection (v_board_rows), so the row carrying a book line to the reader has
-- to exist. What changes is that no surface reads its confidence.
-- =============================================================================

alter table markets
  add column if not exists sport sport,
  add column if not exists parent_market_key text references markets(key),
  add column if not exists publishes_call boolean not null default true;

comment on column markets.sport is
  'Restricts this market to one sport. NULL means every sport, which is the '
  'common case -- only a market that exists in one league names it.';

comment on column markets.parent_market_key is
  'The full-game market this one is a segment of. NULL for a market in its own '
  'right. A derived market inherits its parent''s positions rather than '
  'carrying market_positions rows of its own.';

comment on column markets.publishes_call is
  'Whether a surface may state an OVER/UNDER and a confidence for this market. '
  'False shows the book line and the player''s history against it and nothing '
  'else. The projection is still computed and stored either way.';

-- A market cannot be its own parent, and a parent cannot itself be derived:
-- one level only, so `positions` resolves in a single hop and no reader needs
-- to walk a chain that a cycle could make infinite.
alter table markets
  add constraint markets_parent_is_not_self
  check (parent_market_key is null or parent_market_key <> key);

create index if not exists markets_parent_market_key_idx
  on markets (parent_market_key)
  where parent_market_key is not null;

update markets set
  sport = 'nfl',
  publishes_call = false,
  parent_market_key = case key
    when 'q1_pass_yards' then 'pass_yards'
    when 'q1_rush_yards' then 'rush_yards'
    when 'q1_rec_yards'  then 'rec_yards'
  end
where key in ('q1_pass_yards', 'q1_rush_yards', 'q1_rec_yards');

-- One level only, enforced after the rows are set rather than as a CHECK:
-- a check constraint cannot query another row of the same table.
do $$
declare
  bad int;
begin
  select count(*) into bad
    from markets child
    join markets parent on parent.key = child.parent_market_key
   where parent.parent_market_key is not null;
  if bad > 0 then
    raise exception 'markets: % derived market(s) point at a derived parent', bad;
  end if;
end $$;

-- =============================================================================
-- v_player_game_log gains the first-quarter actuals
-- =============================================================================
-- The hit-rate chart, the L5/L10 splits and the game log all grade
-- `markets.stat_column` against a column on this view. A first-quarter market's
-- stat column is `q1_rec_yards`, so without these the Q1 markets would render
-- as "no data" on every surface -- which is what the reader is actually here
-- for, since the call is deliberately absent.
--
-- The columns have been populated since migration 0063 (`build_quarter_stats`,
-- NFL only, reconciled at >= 99.47% against the box score for every stat).
-- College rows are NULL and `statValue` already returns null for a missing
-- value, so a college game log is unchanged.
--
-- q1_pass_attempts DOES NOT EXIST and is not an oversight: an NFL sack carries
-- the passer's Incompletion row, so attempts cannot be summed from
-- play_player_stats. No book posts a first-quarter attempts market either.
-- =============================================================================

create or replace view v_player_game_log as
select s.player_id,
       s.game_id,
       s.season,
       s.week,
       s.position_group,
       s.is_home,
       o.abbreviation as opponent_abbreviation,
       o.school       as opponent_school,
       g.start_date,
       g.neutral_site,
       s.pass_attempts,
       s.pass_completions,
       s.pass_yards,
       s.pass_tds,
       s.interceptions,
       s.rush_attempts,
       s.rush_yards,
       s.rush_tds,
       s.targets,
       s.receptions,
       s.rec_yards,
       s.rec_tds,
       s.offensive_tds,
       s.opponent_team_id,
       s.q1_pass_yards,
       s.q1_rush_attempts,
       s.q1_rush_yards,
       s.q1_rush_tds,
       s.q1_targets,
       s.q1_receptions,
       s.q1_rec_yards,
       s.q1_rec_tds,
       s.q1_offensive_tds
  from player_game_stats s
  join games g on g.id = s.game_id
  join teams o on o.id = s.opponent_team_id;

-- =============================================================================
-- Everything downstream of a projection learns that some markets state nothing
-- =============================================================================
-- THE ROW HAS TO EXIST AND MOST SURFACES MUST NOT COUNT IT. A first-quarter
-- projection is written because the board is one row per PROJECTION, and that
-- row is what carries DraftKings' line and the player's Q1 history to the
-- reader. But `picks` is built for every projection, so each of those rows also
-- gets a side, a confidence and an edge computed from a distribution we have
-- decided not to stand behind -- and two surfaces read those columns without
-- ever asking which market they came from:
--
--   v_cheat_sheet   would list Q1 rows with a model_side and a confidence, and
--                   with a NULL hit rate, because its stat_column CASE has no
--                   q1_ branches. A cheat sheet IS a list of calls.
--   board_counts()  would add them to rows_all and to the edge count behind the
--                   board's banner, so a full-game board would describe a slate
--                   a third larger than the one under it.
--
-- Both are fixed by one column rather than by each re-deriving the rule. It is
-- exposed on `v_board_rows` because the `markets` row is ALREADY joined there,
-- so it costs nothing, and because this repo has shipped the same class of bug
-- five times: two surfaces answering one question independently and drifting.
--
-- It is appended as the LAST column of the view and not beside `is_binary`
-- where it belongs alphabetically. `create or replace view` may only add
-- columns at the end; moving one renames every column after it.
--
-- WHAT DELIBERATELY DOES NOT FILTER ON IT:
--   mv_no_vig_rows  — a captured Q1 price is a real two-way price and that page
--                     is for prices, not for our opinion. Settled 2026-09-11.
--   the board read  — the board shows first-quarter rows on purpose. It scopes
--                     them with an explicit control instead, so a reader is
--                     never shown a market with no call mixed into one with.
--
-- The three definitions below are the live ones as of migration 0065, dumped
-- with pg_get_viewdef and pg_get_functiondef and changed only where marked, so
-- that nothing is silently lost in retyping them.
-- =============================================================================

-- ===== v_board_rows =====
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
    p.side,
    p.confidence,
    p.model_prob_over,
    p.book_prob_over,
    p.edge,
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

-- ===== v_cheat_sheet =====
create or replace view v_cheat_sheet as
SELECT b.projection_id,
    b.pick_id,
    b.sport,
    b.season,
    b.week,
    b.player_id,
    b.player_name,
    b.position_group,
    b.team_id,
    b.team_school,
    b.team_abbreviation,
    b.team_color,
    b.team_alt_color,
    b.opponent_team_id,
    b.opponent_school,
    b.opponent_abbreviation,
    b.opponent_rank_vs_position,
    b.game_id,
    b.start_date,
    b.is_home,
    b.neutral_site,
    b.market_key,
    b.market_label,
    b.market_emoji,
    b.is_binary,
    b.line,
    b.side AS model_side,
    b.display_confidence,
    b.edge,
    b.has_call,
    b.has_book_line,
    b.sportsbook_key,
    b.conference_name,
    b.conference_is_displayed,
    w.window_size,
    g.decided,
    g.pushes,
    GREATEST(g.overs, g.unders) AS hits,
        CASE
            WHEN g.overs >= g.unders THEN 'over'::text
            ELSE 'under'::text
        END AS hit_side,
    GREATEST(g.overs, g.unders)::numeric / NULLIF(g.decided, 0)::numeric AS hit_rate
   FROM v_board_rows b
     JOIN markets m ON m.key = b.market_key
     CROSS JOIN ( VALUES (5), (10)) w(window_size)
     CROSS JOIN LATERAL ( SELECT count(*) FILTER (WHERE r.value <> b.line) AS decided,
            count(*) FILTER (WHERE r.value > b.line) AS overs,
            count(*) FILTER (WHERE r.value < b.line) AS unders,
            count(*) FILTER (WHERE r.value = b.line) AS pushes
           FROM ( SELECT
                        CASE m.stat_column
                            WHEN 'pass_yards'::text THEN l.pass_yards
                            WHEN 'pass_tds'::text THEN l.pass_tds
                            WHEN 'pass_attempts'::text THEN l.pass_attempts
                            WHEN 'pass_completions'::text THEN l.pass_completions
                            WHEN 'interceptions'::text THEN l.interceptions
                            WHEN 'rush_yards'::text THEN l.rush_yards
                            WHEN 'rush_attempts'::text THEN l.rush_attempts
                            WHEN 'rush_tds'::text THEN l.rush_tds
                            WHEN 'targets'::text THEN l.targets
                            WHEN 'receptions'::text THEN l.receptions
                            WHEN 'rec_yards'::text THEN l.rec_yards
                            WHEN 'rec_tds'::text THEN l.rec_tds
                            WHEN 'offensive_tds'::text THEN l.offensive_tds
                            ELSE NULL::smallint
                        END::numeric AS value
                   FROM v_player_game_log l
                  WHERE l.player_id = b.player_id AND l.season = b.season AND l.week < b.week AND
                        CASE m.stat_column
                            WHEN 'pass_yards'::text THEN l.pass_yards
                            WHEN 'pass_tds'::text THEN l.pass_tds
                            WHEN 'pass_attempts'::text THEN l.pass_attempts
                            WHEN 'pass_completions'::text THEN l.pass_completions
                            WHEN 'interceptions'::text THEN l.interceptions
                            WHEN 'rush_yards'::text THEN l.rush_yards
                            WHEN 'rush_attempts'::text THEN l.rush_attempts
                            WHEN 'rush_tds'::text THEN l.rush_tds
                            WHEN 'targets'::text THEN l.targets
                            WHEN 'receptions'::text THEN l.receptions
                            WHEN 'rec_yards'::text THEN l.rec_yards
                            WHEN 'rec_tds'::text THEN l.rec_tds
                            WHEN 'offensive_tds'::text THEN l.offensive_tds
                            ELSE NULL::smallint
                        END IS NOT NULL
                  ORDER BY l.week DESC
                 LIMIT w.window_size) r) g
  WHERE b.line IS NOT NULL AND b.publishes_call;

-- ===== board_counts =====
CREATE OR REPLACE FUNCTION public.board_counts(p_sport sport, p_season integer, p_week integer, p_edge_threshold numeric, p_synthetic_book_key text, p_displayed_only boolean DEFAULT true, p_kickoff_cutoff timestamp with time zone DEFAULT NULL::timestamp with time zone)
 RETURNS TABLE(rows_all bigint, with_call bigint, with_book_line bigint, with_dev_line bigint, with_even_book_price bigint, over_threshold bigint)
 LANGUAGE sql
 STABLE
AS $function$
  select
    count(*),
    count(*) filter (where b.has_call),
    count(*) filter (where b.has_book_line),
    count(*) filter (where b.sportsbook_key = p_synthetic_book_key),
    -- Exactly 0.500, not "close to it". The caveat this feeds claims edge IS
    -- confidence minus 50%, which is only exactly true at the midpoint, so a
    -- tolerance here would put rows under a notice that misdescribes them.
    count(*) filter (where b.book_prob_over = 0.5
                       and b.sportsbook_key <> p_synthetic_book_key),
    count(*) filter (where b.edge >= p_edge_threshold)
  from v_board_rows b
  where b.publishes_call
    and b.sport  = p_sport
    and b.season = p_season
    and b.week   = p_week
    and (not p_displayed_only or b.conference_is_displayed)
    -- A NULL cutoff means "count everything", matching the caller that omits
    -- it. A NULL start_date is a TBD kickoff and counts as upcoming, the same
    -- call `upcomingOnly` and `lib/core/kickoff.ts` make.
    and (p_kickoff_cutoff is null
         or b.start_date is null
         or b.start_date >= p_kickoff_cutoff);
$function$

