-- The game simulator against the book (phase S3, 2026-10-21).
--
-- The client asked to see each quarter's and half's score "compared to the
-- odds we bring in" (2026-10-20). S2 stored the simulator's distribution of
-- every quarter and half (game_sims); `capture_game_periods --sport nfl` now
-- captures the books' first-quarter and first-half lines into game_odds. This
-- table holds the comparison, one row per game, period and market, written by
-- `price_sim_edges` after every hourly capture:
--
--   model_prob   our probability of the FIRST side (home, or over), a push
--                excluded from both sides, at the BOOK'S line;
--   book_prob    the same side's vig-free probability (proportional de-vig of
--                one book's two-way price, the book chosen as the game table
--                chooses: Pinnacle, then DraftKings, then FanDuel);
--   side, edge   the side our number beats the book's on, and model minus
--                book on it (CLAUDE.md §6), so edge >= 0 (as game_picks).
--
-- WHICH NUMBER: `source` says where model_prob came from. Each market uses the
-- method that scored better in the S1 backtest (docs/nfl-sim-backtest.html):
-- the simulator, or the pregame projection times the league's period share
-- ('baseline'). Neither reads a price (CLAUDE.md §11); the book only sits
-- beside it.
--
-- FROZEN AT KICKOFF by trigger, as game_sims' pregame rows and game_picks are:
-- the last row before kickoff is the call that is graded. The grading view
-- reads the scoreboard's line scores (live_scores), so it needs no job.
--
-- No sport column (CLAUDE.md §3: sport comes through game_id).

create table game_sim_edges (
  game_id        bigint not null references games(id) on delete cascade,
  period         text not null check (period in ('q1', 'h1')),
  market         text not null check (market in ('h2h', 'spreads', 'totals')),
  source         text not null check (source in ('simulator', 'baseline')),
  -- spreads: OUR home team's handicap (negative = home favoured), as game_odds.
  -- totals: the total. h2h: NULL.
  line           numeric(5, 1),
  sportsbook_id  bigint not null references sportsbooks(id),
  first_price    integer not null,
  second_price   integer not null,
  model_prob     numeric(6, 5) not null check (model_prob > 0 and model_prob < 1),
  book_prob      numeric(6, 5) not null check (book_prob > 0 and book_prob < 1),
  side           text not null check (side in ('home', 'away', 'over', 'under')),
  edge           numeric(6, 5) not null,
  -- Our own number in the market's terms: the period's projected total, or the
  -- home team's projected margin (for spreads and moneylines).
  model_mean     numeric(5, 1) not null,
  sims_made_at   timestamptz not null,
  made_at        timestamptz not null default now(),
  primary key (game_id, period, market),
  constraint game_sim_edges_line_matches_market
    check ((market = 'h2h') = (line is null)),
  constraint game_sim_edges_side_matches_market
    check ((market = 'totals') = (side in ('over', 'under')))
);

comment on table game_sim_edges is
  'NFL first-quarter and first-half markets: our probability (game simulator or period baseline, see source) against one book''s vig-free price (migration 0100, phase S3). Written hourly by price_sim_edges; frozen at kickoff by trigger. Graded in v_game_sim_edge_results.';
comment on column game_sim_edges.model_prob is
  'Our probability of the FIRST side (home for h2h and spreads, over for totals) at the book''s line, a push excluded from both sides. No price is read to make it.';
comment on column game_sim_edges.book_prob is
  'The first side''s vig-free probability from first_price/second_price (proportional de-vig).';
comment on column game_sim_edges.edge is
  'Our probability minus the book''s vig-free probability on `side`, the side where ours is higher, so never negative (CLAUDE.md §6, as game_picks). Not calibrated against results yet: the period markets have no graded history of their own.';
comment on column game_sim_edges.sims_made_at is
  'made_at of the pregame game_sims row priced (or of the engine''s baseline run), so a row can be traced to the simulation it read.';

create or replace function game_sim_edges_freeze()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  kickoff timestamptz;
  target  bigint := coalesce(new.game_id, old.game_id);
begin
  select start_date into kickoff from games where id = target;
  if kickoff is null then
    raise exception 'game_sim_edges: game % has no start_date; a pregame edge cannot be shown to precede it', target;
  end if;
  if now() >= kickoff then
    raise exception 'game_sim_edges: game % kicked off at %; its edges are frozen', target, kickoff;
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  if tg_op = 'UPDATE' and (
    new.game_id <> old.game_id or new.period <> old.period or new.market <> old.market
  ) then
    raise exception 'game_sim_edges: an edge cannot be moved to another game or market';
  end if;
  new.made_at := now();
  return new;
end;
$$;

comment on function game_sim_edges_freeze() is
  'Refuses any write to game_sim_edges at or after the game''s kickoff, and stamps made_at with the server clock.';

create trigger game_sim_edges_frozen_at_kickoff
  before insert or update or delete on game_sim_edges
  for each row execute function game_sim_edges_freeze();

alter table game_sim_edges enable row level security;

create policy game_sim_edges_public_read on game_sim_edges
  for select to anon, authenticated using (true);

-- Grading, from the scoreboard: the period's points are the first one (q1) or
-- two (h1) entries of the line scores. Ungraded (NULL result) until the
-- scoreboard calls the game completed. A tie on a moneyline, or a margin or
-- total landing on the line, is a push.
create view v_game_sim_edge_results
with (security_invoker = true)
as
with scored as (
  select e.*,
         case when e.period = 'q1' then ls.home_line_scores[1]
              else ls.home_line_scores[1] + ls.home_line_scores[2] end as home_period,
         case when e.period = 'q1' then ls.away_line_scores[1]
              else ls.away_line_scores[1] + ls.away_line_scores[2] end as away_period,
         ls.status = 'completed' as final
    from game_sim_edges e
    left join live_scores ls on ls.game_id = e.game_id
)
select s.game_id, s.period, s.market, s.source, s.line, s.sportsbook_id,
       s.first_price, s.second_price, s.model_prob, s.book_prob, s.side, s.edge,
       s.model_mean, s.made_at, s.home_period, s.away_period,
       case
         when not coalesce(s.final, false)
              or s.home_period is null or s.away_period is null then null
         else (
           with v(x) as (
             select case s.market
                      when 'totals' then (s.home_period + s.away_period) - s.line
                      when 'spreads' then (s.home_period - s.away_period) + s.line
                      else (s.home_period - s.away_period)::numeric
                    end
           )
           select case
                    when x = 0 then 'push'
                    when (x > 0) = (s.side in ('home', 'over')) then 'won'
                    else 'lost'
                  end
             from v
         )
       end as result
  from scored s;

comment on view v_game_sim_edge_results is
  'Each frozen game_sim_edges row with the period''s actual points from live_scores and won/lost/push for the favoured side; NULL until the game is completed (migration 0100).';

grant select on v_game_sim_edge_results to anon, authenticated;
