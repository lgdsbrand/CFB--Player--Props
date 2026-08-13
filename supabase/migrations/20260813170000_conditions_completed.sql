-- =============================================================================
-- 0043 -- v_game_conditions.completed: "not yet" and "never" are different
-- =============================================================================
-- Caught by looking at the rendered panel rather than by a test. A game with no
-- weather row rendered "No forecast yet. Forecasts reach about 15 days ahead
-- and sharpen daily as kickoff approaches" -- which is true for an upcoming
-- game and false for a 2025 game that finished months ago and simply never had
-- conditions recorded. Production holds 1,813 weather rows against more games
-- than that, so this is not a corner case; it is most of the archive.
--
-- Appended rather than folded into 0042 because 0042 is already applied to
-- development. Rewriting an applied migration leaves the ledger recording a
-- version whose content no longer matches the file, which is the drift the
-- migrate_database pre-flight exists to catch. `create or replace view` may
-- only APPEND a column, which is what this does.
-- =============================================================================

create or replace view v_game_conditions
with (security_invoker = true)
as
select
  g.id                  as game_id,
  g.season,
  g.week,
  g.start_date,

  v.name                as venue_name,
  v.city                as venue_city,
  v.state               as venue_state,
  coalesce(v.is_dome, false) as venue_is_dome,

  w.source,
  w.is_forecast,
  w.observed_at,
  w.ingested_at,
  w.temperature_f,
  w.dew_point_f,
  w.humidity,
  w.precipitation_in,
  w.snowfall_in,
  w.wind_speed_mph,
  w.wind_direction_deg,
  w.pressure_mb,
  w.condition,

  -- The appended column.
  g.completed
from games g
left join venues v on v.id = g.venue_id
left join lateral (
  select *
  from game_weather gw
  where gw.game_id = g.id
  order by (gw.source = 'cfbd') desc
  limit 1
) w on true;

comment on column v_game_conditions.completed is
  'Whether the game has been played. Separates the two reasons a game can have no conditions: an upcoming game beyond the ~15-day forecast horizon, which will get one, and a finished game that never had any recorded, which will not. The panel says different things for each.';
