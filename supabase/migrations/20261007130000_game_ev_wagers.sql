-- 0087: price-based +EV wagers on game lines (client, 2026-10-06).
--
-- The client's "+EV wagers": a book paying more than the market's fair price.
-- The user chose the PRICE-BASED version (no model involved): the fair price
-- is Pinnacle's, de-vigged by the power method, at the same line. See
-- worker/core/ev.py for why each choice, measured on 2026-10-03.
--
-- WRITTEN BY THE CAPTURE, FROM THE QUOTES IT JUST SAW. `game_odds` writes a
-- row only when a price moves, so its newest row cannot tell "unchanged" from
-- "the book pulled the game", and a +EV list read from it would keep offering
-- prices no longer on the board. The capture run knows exactly what is posted
-- right now, so it computes the wagers from that and REPLACES each captured
-- game's rows. A game it no longer captures (kicked off) keeps its last set,
-- and readers bound by kickoff.
--
-- Only sides with positive EV are stored: a few hundred rows a slate.

create table game_ev_wagers (
  game_id        bigint not null references games(id) on delete cascade,
  period         text not null,
  market         text not null check (market in ('h2h', 'spreads', 'totals')),
  sportsbook_id  bigint not null references sportsbooks(id),
  side           text not null check (side in ('home', 'away', 'over', 'under')),
  line           numeric,
  price          integer not null,
  fair_prob      numeric(6, 5) not null check (fair_prob > 0 and fair_prob < 1),
  ev             numeric(7, 5) not null,
  captured_at    timestamptz not null,
  primary key (game_id, period, market, sportsbook_id, side)
);

comment on table game_ev_wagers is
  'Book prices beating Pinnacle''s power-de-vigged fair price at the same line, per game market and side (migration 0087). Rewritten by every ingest_game_odds run for the games it captured, from the quotes that run saw, so a price a book has pulled drops out. Positive EV only. Price-based: no model input.';
comment on column game_ev_wagers.line is
  'The spread from OUR home team''s side, or the total; NULL for a moneyline. Always Pinnacle''s line: a book on a different line is not compared.';
comment on column game_ev_wagers.fair_prob is
  'Pinnacle''s power-de-vigged probability of this side.';
comment on column game_ev_wagers.ev is
  'Expected return per unit staked at `price` if fair_prob is the true chance: fair_prob x decimal odds - 1. An exchange''s is before its fees.';

create index game_ev_wagers_ev_idx on game_ev_wagers (ev desc);

alter table game_ev_wagers enable row level security;
create policy game_ev_wagers_public_read on game_ev_wagers
  for select to anon, authenticated using (true);

-- Polymarket is a peer-to-peer prediction market: its posted price leaves its
-- fees out, exactly like the exchanges 0075 named, so a +EV figure on it must
-- carry the same "before fees" label. It was captured after 0075 and landed as
-- `other` by default.
update sportsbooks set market_role = 'exchange' where key = 'polymarket';
