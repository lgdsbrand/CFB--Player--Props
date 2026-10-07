-- 0088: live scores for college games in progress (client, 2026-10-06).
--
-- The client asked for live scores. College only, by the user's decision of
-- 2026-10-06: CFBD's /scoreboard serves the whole FBS week in one call at no
-- odds-API cost, while NFL scores would be bought from the Odds API.
--
-- ONE ROW PER GAME, OVERWRITTEN. This is a live state, not a history: the
-- poller (`poll_live_scores`, every two minutes while a college game is in
-- its window) replaces each game's row with what CFBD reports now. The final
-- score of record still comes from the daily results ingest into `games`;
-- this table only says what the board looks like at this minute.

create table live_scores (
  game_id           bigint primary key references games(id) on delete cascade,
  status            text not null check (status in ('scheduled', 'in_progress', 'completed')),
  period            smallint,
  clock             text,
  home_points       smallint,
  away_points       smallint,
  home_line_scores  smallint[],
  away_line_scores  smallint[],
  possession        text check (possession in ('home', 'away')),
  situation         text,
  last_play         text,
  updated_at        timestamptz not null default now()
);

comment on table live_scores is
  'What CFBD''s scoreboard says about each college game in or near its window, overwritten every poll (migration 0088). Not the score of record: `games` keeps that, from the daily results ingest.';
comment on column live_scores.clock is
  'The game clock as CFBD prints it (e.g. ''08:42''); NULL between periods and before kickoff.';
comment on column live_scores.possession is
  'Which of OUR home and away teams has the ball, resolved by the poller; NULL when CFBD does not say or says something it cannot place.';
comment on column live_scores.situation is
  'Down, distance and field position as CFBD words it, e.g. ''2nd & 7 at TROY 34''.';

alter table live_scores enable row level security;
create policy live_scores_public_read on live_scores
  for select to anon, authenticated using (true);
