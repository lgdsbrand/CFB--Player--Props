-- 0092: live player stats for college games in progress (client, 2026-10-12).
--
-- The client asked to "track it live": a saved slip's player props show how
-- the player is doing while the game is on. CFBD's /live/plays (Tier 2,
-- one call per game) is the source. It has no player ids or box score, only
-- each play's text ("#8 C.Boley pass complete ... to #3 R.Harris ... for 18
-- yards"), so the poller (`worker/adapters/cfbd/live_player_stats.py`) adds
-- the plays up per player and resolves each name to our roster by team,
-- jersey and surname. Measured against the official box score on 13 games
-- of 2026 week 6: receptions and receiving yards 99-100% exact, rushing and
-- passing yards 95-97%.
--
-- UNOFFICIAL AND NEVER GRADED. Like `live_scores`, one row per player per
-- game, replaced on every poll. Nothing settles from it: a saved slip's prop
-- settles only from `player_game_stats`, the box score the morning after
-- (the user's decision, 2026-10-12).
--
-- COST: polled at most every five minutes per game, only for college games
-- in progress that have book props, about 10,500 CFBD calls a month.

create table live_player_stats (
  game_id           bigint not null references games(id) on delete cascade,
  player_id         bigint not null references players(id) on delete cascade,
  team_id           bigint not null references teams(id) on delete cascade,
  pass_attempts     smallint not null default 0,
  pass_completions  smallint not null default 0,
  pass_yards        smallint not null default 0,
  pass_tds          smallint not null default 0,
  rush_attempts     smallint not null default 0,
  rush_yards        smallint not null default 0,
  receptions        smallint not null default 0,
  rec_yards         smallint not null default 0,
  offensive_tds     smallint not null default 0,
  updated_at        timestamptz not null default now(),
  primary key (game_id, player_id)
);

comment on table live_player_stats is
  'Unofficial player stats for a college game in progress, added up from CFBD live play text and replaced every poll (migration 0092). Never graded: player_game_stats is the box score of record.';
comment on column live_player_stats.offensive_tds is
  'Rushing plus receiving touchdowns, as player_game_stats.offensive_tds counts them for the anytime-TD market.';

alter table live_player_stats enable row level security;
create policy live_player_stats_public_read on live_player_stats
  for select to anon, authenticated using (true);

alter table live_scores add column plays_fetched_at timestamptz;

comment on column live_scores.plays_fetched_at is
  'When the play-by-play behind live_player_stats was last read for this game; the poller waits five minutes between reads. Set only by that poll, so the scoreboard poll leaves it alone.';
