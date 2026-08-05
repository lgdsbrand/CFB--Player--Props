-- =============================================================================
-- 0028 — the board can see how much evidence is behind a projection
-- =============================================================================
-- Phase 6d. `projections.effective_sample` has been written on every row since
-- Phase 4a and read by nobody: the board's view never selected it, so the one
-- number that says how much the model actually knows about a player stopped at
-- the database. `prior_weight` was already exposed and is rendered on the player
-- page, which is half of the pair and, on its own, the misleading half.
--
-- WHY THE PAIR AND NOT THE SHARE ALONE. `prior_weight` is the share of a
-- projection carried by last season; `effective_sample` is current-season games
-- plus prior-season games discounted by that weight. In the opening weeks the
-- share INVERTS against intuition: a transfer takes CHANGED_TEAM_PRIOR_MULTIPLIER
-- and so carries a LOWER prior weight than a returning starter (0.25 against
-- 0.50 on 2025 week 1) — which reads as "less dependent on last season" when the
-- truth is "we trust the only evidence there is even less". `effective_sample`
-- does not invert: 3.0 games against 6.0, lower always meaning less to go on.
-- So the board leads with the sample and the share qualifies it.
--
-- BOTH ARE PLAYER-LEVEL, not market-level. They are computed once per player-week
-- in the feature frame, so every market on a card carries identical values —
-- which is why the card renders them once in its header rather than per market.
--
-- Appended after `display_confidence` so `create or replace view` stays legal:
-- Postgres permits new columns only at the end of an existing view.
-- =============================================================================

create or replace view v_board_rows
with (security_invoker = true)
as
select
  pr.id                   as projection_id,
  p.id                    as pick_id,
  pr.season,
  pr.week,
  pr.market_key,
  m.display_name          as market_name,
  m.short_label           as market_label,
  m.emoji                 as market_emoji,
  m.is_binary,

  pl.id                   as player_id,
  pl.name                 as player_name,
  pts.position_group,

  t.id                    as team_id,
  t.school                as team_school,
  t.abbreviation          as team_abbreviation,
  t.color                 as team_color,
  t.alt_color             as team_alt_color,

  o.id                    as opponent_team_id,
  o.school                as opponent_school,
  o.abbreviation          as opponent_abbreviation,

  g.id                    as game_id,
  g.start_date,
  g.neutral_site,
  (g.home_team_id = t.id) as is_home,

  p.line,
  p.side,
  p.confidence,
  p.model_prob_over,
  p.book_prob_over,
  p.edge,
  coalesce(p.has_book_line, false) as has_book_line,
  (p.id is not null)      as has_call,
  p.over_price,
  p.under_price,
  p.sportsbook_key,
  p.sportsbook_name,

  -- secondary detail only: never the headline claim (CLAUDE.md §1)
  pr.p50                  as projected_median,
  pr.p10                  as projected_p10,
  pr.p90                  as projected_p90,
  pr.prior_weight,

  dpr.rank_vs_position    as opponent_rank_vs_position,
  c.name                  as conference_name,
  c.is_displayed          as conference_is_displayed,

  -- The headline probability as the CARD states it, on one scale, so ordering
  -- and filtering cannot disagree with the number under the reader's eye.
  case
    when p.id is null then null
    when m.is_binary  then p.model_prob_over
    else p.confidence
  end                     as display_confidence,

  pr.effective_sample
from projections pr
join markets m             on m.key = pr.market_key
join players pl            on pl.id = pr.player_id
join teams t               on t.id = pr.team_id
join teams o               on o.id = pr.opponent_team_id
join games g               on g.id = pr.game_id
left join lateral (
  select
    pk.id,
    pk.line,
    pk.side,
    pk.confidence,
    pk.model_prob_over,
    pk.book_prob_over,
    pk.edge,
    pk.has_book_line,
    pk.over_price,
    pk.under_price,
    bk.key          as sportsbook_key,
    bk.display_name as sportsbook_name
  from picks pk
  left join sportsbooks bk on bk.id = pk.sportsbook_id
  where pk.projection_id = pr.id
  order by coalesce(bk.priority, 32767), pk.id
  limit 1
) p on true
left join player_team_seasons pts
       on pts.player_id = pr.player_id
      and pts.team_id   = pr.team_id
      and pts.season    = pr.season
left join team_seasons ts  on ts.team_id = pr.team_id and ts.season = pr.season
left join conferences c    on c.id = ts.conference_id
left join defense_position_ratings dpr
       on dpr.defense_team_id = pr.opponent_team_id
      and dpr.season          = pr.season
      and dpr.as_of_week      = pr.week
      and dpr.position_group  = pts.position_group;

comment on column v_board_rows.display_confidence is
  'The headline probability as the card renders it: model_prob_over for a binary market (the anytime-scorer probability, CLAUDE.md §1), otherwise picks.confidence. Sort and filter on THIS, never on confidence — for anytime TD the called side is under on ~97% of picks, so confidence there is the probability of NOT scoring and orders the board backwards.';

comment on column v_board_rows.effective_sample is
  'Games of evidence behind this player-week: current-season games plus prior-season games discounted by prior_weight. PLAYER-level, identical across a player''s markets. The honest per-card uncertainty signal — it never inverts, unlike prior_weight, which a transfer carries LESS of than a returning starter because the changed-team discount applies to the only evidence either has.';

comment on column v_board_rows.prior_weight is
  'Share of this projection carried by last season rather than this one. PLAYER-level, identical across a player''s markets. Read it beside effective_sample, never alone: in the opening weeks a low share means the prior was discounted (transfer), not that current-season evidence exists.';
