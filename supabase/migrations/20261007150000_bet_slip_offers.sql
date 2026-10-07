-- 0089: what each book is offering right now, with its bet link (bet slip).
--
-- The client asked for a bet slip (2026-10-06): pick props and game lines,
-- see the best book for each and a parlay price, and open the bet at the
-- book. NO BET IS PLACED HERE (CLAUDE.md §10): the slip only links out.
--
-- The Odds API returns a link per outcome when asked (`includeLinks`), free:
-- FanDuel, DraftKings and BetMGM link to the bet itself, BetRivers too but
-- with fields we cannot fill, and BetOnline, LowVig and Bovada only to the
-- game's page (probed 2026-10-07). A link may carry `{state}`, which the
-- reader fills from the viewer's chosen state.
--
-- CURRENT ONLY, REPLACED BY EACH CAPTURE. History stays where it is
-- (`player_prop_lines`, `game_odds`); these hold one row per book and side,
-- rewritten by the run that saw it, so a price a book has pulled drops out.
-- A link is NOT added to `player_prop_lines`: that table keeps every capture
-- (332k rows), and a URL per row would be most of its size for a value only
-- the newest capture needs. Rows for games over a day past kickoff are
-- deleted by the same runs, so the tables stay a slate in size.

create table prop_offers (
  game_id        bigint not null references games(id) on delete cascade,
  player_id      bigint not null references players(id) on delete cascade,
  market_key     text not null references markets(key),
  sportsbook_id  bigint not null references sportsbooks(id),
  line           numeric(6, 2) not null,
  side           text not null check (side in ('over', 'under')),
  price          integer not null,
  link           text,
  event_link     text,
  captured_at    timestamptz not null,
  primary key (game_id, player_id, market_key, sportsbook_id, line, side)
);

comment on table prop_offers is
  'Each book''s current player-prop prices with their bet links, one row per side (migration 0089). Replaced per game and market by every ingest_odds run that captured them. Read by the bet slip; history lives in player_prop_lines.';
comment on column prop_offers.link is
  'The book''s link to this bet as the Odds API sent it, or NULL. May contain {state}; BetRivers'' also carries {pickType} and {wagerAmount}, which nothing fills, so readers use event_link for it.';
comment on column prop_offers.event_link is
  'The book''s page for the game, or NULL. The fallback when there is no bet link.';

create index prop_offers_player_idx on prop_offers (player_id, market_key);

create table game_offers (
  game_id        bigint not null references games(id) on delete cascade,
  period         text not null check (period in ('full', 'h1', 'q1')),
  market         text not null check (market in ('h2h', 'spreads', 'totals')),
  sportsbook_id  bigint not null references sportsbooks(id),
  side           text not null check (side in ('home', 'away', 'over', 'under')),
  line           numeric,
  price          integer not null,
  link           text,
  event_link     text,
  captured_at    timestamptz not null,
  primary key (game_id, period, market, sportsbook_id, side)
);

comment on table game_offers is
  'Each book''s current game-line prices with their bet links, one row per side (migration 0089). Replaced per game and period by every ingest_game_odds run that captured them. Read by the bet slip; history lives in game_odds.';
comment on column game_offers.line is
  'The spread from OUR home team''s side (as in game_odds), or the total; NULL for a moneyline.';
comment on column game_offers.link is
  'The book''s link to this bet as the Odds API sent it, or NULL. May contain {state}; see prop_offers.link.';

alter table prop_offers enable row level security;
create policy prop_offers_public_read on prop_offers
  for select to anon, authenticated using (true);

alter table game_offers enable row level security;
create policy game_offers_public_read on game_offers
  for select to anon, authenticated using (true);
