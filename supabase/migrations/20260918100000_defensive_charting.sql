-- =============================================================================
-- 0072 — What kind of defense this is: blitz rate and box counts (NFL only)
-- =============================================================================
-- The client's third ask, in his words: "what type of defense and then their
-- rank and color coded to good and worse", for NFL wide receivers and tight
-- ends. What he originally meant was man vs zone coverage. That is not
-- buildable as a live product and the reason is recorded in
-- `worker/adapters/nflverse/ingest_charting.py`: nflverse publishes coverage in
-- `pbp_participation`, which appears once, AFTER a season ends -- the 2025 file
-- went up in February 2026 -- so a man/zone panel would show last season's
-- numbers all year and never update.
--
-- FTN charting is the live alternative and is published WEEKLY in season. It
-- carries no coverage shell, but it carries what the defense actually did on
-- each snap: how many rushed, how many blitzed, how many stood in the box.
--
-- =============================================================================
-- THE RANK WAS MEASURED BEFORE IT WAS BUILT, AND THIS TIME IT EARNED ONE
-- =============================================================================
-- Migration 0070 refused a first-quarter rank because a first-quarter rate does
-- not predict its own next half-season. The same test was run here before a
-- line of this was written -- split each regular season at week 9, rank the 32
-- defenses on each half, Spearman between the halves:
--
--   metric            2023    2024    2025    mean
--   blitz rate       +0.73   +0.58   +0.56   +0.62
--   5+ pass rushers  +0.57   +0.74   +0.50   +0.60
--   heavy box (7+)   +0.23   +0.12   +0.35   +0.23
--   light box (5-)   +0.21   +0.18   +0.40   +0.26
--
-- For scale: the first-quarter rate this project REFUSED to rank came in at
-- +0.05, and the opponent-adjusted `rank_vs_position` it already publishes sits
-- at about +0.18. Blitz rate is roughly three and a half times more
-- self-predictive than anything ranked here today, and positive in every season
-- rather than in five cells of nine.
--
-- That is the difference between a scheme choice and a rate of production. How
-- often a coordinator sends pressure is a decision he makes every week; how
-- many yards his defense gave up is an outcome he only partly controls.
--
-- It also separates a real field. Over 2025: 21.0% at the bottom, 29.1% median,
-- 51.4% at the top. The most aggressive defense blitzes nearly two and a half
-- times as often as the least.
--
-- =============================================================================
-- HEAVY BOX GETS A RATE AND NO RANK, DELIBERATELY
-- =============================================================================
-- +0.23 is weak. It is, admittedly, above the +0.18 of the rank already on the
-- board, and ranking it could be argued for on that basis. It is not ranked
-- because of where it would appear: beside the blitz rank, in the same panel,
-- in the same colour ramp. Two orderings printed identically, one nearly three
-- times firmer than the other, is an invitation to read them as equally solid —
-- and nothing on screen could tell them apart. The rate is published because it
-- is a real observation about run defense; the ordering is not, because this
-- panel cannot say how much less it means.
--
-- If the client later asks for a box rank specifically, the number is here and
-- the measurement above is what the decision should be argued from.
--
-- =============================================================================
-- COLOURED BY STYLE, NEVER BY GOOD AND BAD
-- =============================================================================
-- The client asked for "color coded to good and worse". The blitz rank is
-- coloured on a single style ramp -- blitz-light through blitz-heavy -- and not
-- on a good/bad scale, because the data does not support a direction. A heavy
-- blitz means more single coverage AND faster throws; it is good for some
-- receivers on some routes and bad for others, and this project has measured
-- neither. The board already carries a genuine good/bad axis in
-- `opponent_rank_vs_position`, which is opponent-adjusted and is about what a
-- defense CONCEDES. This adds the axis that was missing -- what it DOES -- and
-- says so rather than restating a description as a recommendation.
--
-- =============================================================================
-- NFL ONLY, AND STRUCTURALLY SO
-- =============================================================================
-- There is no college equivalent of FTN charting at any price. These tables
-- reference `teams`, which carries the sport, so a college row is not forbidden
-- by a constraint so much as impossible to produce: the only writer is the
-- nflverse adapter. College surfaces read null and show nothing, the same way
-- they do for snap share.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Per defense, per game. The observation layer.
-- -----------------------------------------------------------------------------
-- COUNTS, NOT RATES, and the denominators are stored beside the numerators so
-- every rate downstream is computed from a sum rather than an average of
-- averages. A defense that faced 22 dropbacks in a blowout and 48 in a shootout
-- must not have those two games weighted equally.
--
-- TWO SEPARATE DENOMINATORS, because the two things are charted on different
-- plays. `dropbacks` counts plays FTN recorded at least one pass rusher on --
-- 46.5% of its rows, the passing downs. `box_plays` counts plays it recorded
-- anyone in the box on, which includes runs. Dividing blitzes by every charted
-- play would halve every blitz rate in the league.
-- -----------------------------------------------------------------------------
create table defense_charting_game_splits (
  id                  bigint generated always as identity primary key,
  defense_team_id     bigint   not null references teams(id),
  game_id             bigint   not null references games(id) on delete cascade,
  season              smallint not null,
  week                smallint not null,

  dropbacks           smallint not null,
  blitz_plays         smallint not null,
  pass_rushers_sum    smallint not null,

  box_plays           smallint not null,
  heavy_box_plays     smallint not null,

  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (defense_team_id, game_id),
  constraint defense_charting_blitz_within_dropbacks
    check (blitz_plays <= dropbacks),
  constraint defense_charting_heavy_box_within_box
    check (heavy_box_plays <= box_plays)
);

comment on table defense_charting_game_splits is
  'What a defense DID on each charted snap, from nflverse ftn_charting: how '
  'often it blitzed and how many it put in the box. NFL only -- no college '
  'source exists. Distinct from defense_position_game_splits, which is what a '
  'defense ALLOWED.';

comment on column defense_charting_game_splits.dropbacks is
  'Charted plays with at least one pass rusher -- the denominator for blitz '
  'rate. About 46% of FTN rows; the rest are runs and special teams, where a '
  'blitz is not defined.';

create index defense_charting_game_splits_season_idx
  on defense_charting_game_splits (season, week, defense_team_id);

create trigger defense_charting_game_splits_set_updated_at
  before update on defense_charting_game_splits
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- Point-in-time, per as-of week. The layer a board row may read.
-- -----------------------------------------------------------------------------
-- CLAUDE.md §4: every row carries `as_of_week` and is computed from games with
-- `week < as_of_week` STRICTLY. A blitz rate is a display figure rather than a
-- model input, but the rule is not about what consumes a number -- it is about
-- a week-2 board never showing what happened in week 2.
--
-- `blitz_rank` IS ORIENTED 1 = BLITZES MOST, which is the opposite of
-- `defense_position_ratings.rank_vs_position` (1 = allows the least). That is
-- deliberate and it is the conventional reading of the phrase: "first in blitz
-- rate" means blitzes most, everywhere the phrase is used. The lesson recorded
-- in `splits.py` -- a convention that has to be explained every time it appears
-- is the wrong convention -- points this way rather than toward consistency
-- with a rank of a different quantity. Every surface still prints the style
-- label beside it, so the number never travels alone.
-- -----------------------------------------------------------------------------
create table defense_charting_ratings (
  id                  bigint generated always as identity primary key,
  defense_team_id     bigint   not null references teams(id),
  season              smallint not null,
  as_of_week          smallint not null,

  games_included      smallint not null,
  dropbacks           integer  not null,
  box_plays           integer  not null,

  blitz_rate          numeric(5,4),
  blitz_rank          smallint,
  mean_pass_rushers   numeric(4,2),

  -- NO `heavy_box_rank`. See the header: +0.23 against blitz's +0.62, and two
  -- orderings in one panel that a reader cannot tell apart is worse than one.
  heavy_box_rate      numeric(5,4),

  created_at          timestamptz not null default now(),
  unique (defense_team_id, season, as_of_week)
);

comment on table defense_charting_ratings is
  'Point-in-time defensive tendencies: what this defense had done BEFORE '
  'as_of_week. Computed from games with week < as_of_week strictly (CLAUDE.md '
  'section 4).';

comment on column defense_charting_ratings.blitz_rank is
  'ORIENTED 1 = BLITZES MOST, opposite to defense_position_ratings.'
  'rank_vs_position, because that is the conventional reading of "first in '
  'blitz rate". Earned a rank on measurement: a defense''s first-half blitz '
  'rate predicts its second half at Spearman +0.62 over 2023-25, against +0.18 '
  'for the rank already published and +0.05 for the first-quarter rate that '
  'was refused one (migration 0070).';

comment on column defense_charting_ratings.heavy_box_rate is
  'Share of charted plays with 7+ defenders in the box. NO RANK on purpose -- '
  'it measured +0.23, and printing a soft ordering beside a firm one in the '
  'same colour ramp would make them look equally solid.';

create index defense_charting_ratings_lookup_idx
  on defense_charting_ratings (season, as_of_week, defense_team_id);

-- -----------------------------------------------------------------------------
-- RLS, in the shape migration 0011 established
-- -----------------------------------------------------------------------------
-- Named `<table>_public_read` and granted explicitly `to anon, authenticated`
-- rather than left to PUBLIC. The role list is the documented intent and the
-- thing a reader greps for; a policy with no role reads as an oversight even
-- when it happens to behave the same.
--
-- BOTH TABLES PUBLIC, matching `defense_position_game_splits` and
-- `defense_position_ratings` beside them. The app reads only the ratings today,
-- but the observation layer is the same kind of derived, non-sensitive data as
-- its sibling and splitting the pair would make the exception the thing needing
-- explanation.
-- -----------------------------------------------------------------------------
alter table defense_charting_game_splits enable row level security;
alter table defense_charting_ratings     enable row level security;

create policy defense_charting_game_splits_public_read
  on public.defense_charting_game_splits
  for select to anon, authenticated using (true);

create policy defense_charting_ratings_public_read
  on public.defense_charting_ratings
  for select to anon, authenticated using (true);

-- =============================================================================
-- The board carries the opponent's tendencies
-- =============================================================================
-- A THIRD LEFT JOIN on a view whose ceiling is CPU, so it is worth saying why
-- it is cheap: `defense_charting_ratings` holds one row per defense per as-of
-- week -- 32 x 19 per season, against `defense_position_ratings`' 32 x 19 x 4 --
-- and it is joined on the same three keys already in hand for that table, minus
-- the position. No extra scan of anything large.
--
-- College rows read null on all three, because nothing writes them.
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
