-- =============================================================================
-- 0079 — NFL games get venues, and so weather
-- =============================================================================
-- Until now every NFL game had venue_id NULL (272 of 272 in 2026). The weather
-- job selects games through `venues` for coordinates and the dome flag, so it
-- skipped the whole NFL and the panel told NFL readers a forecast was coming.
--
-- nflverse's schedule names a stadium per game (`stadium_id`, e.g. KAN00) but
-- carries no coordinates, so `worker/adapters/nflverse/stadiums.py` holds them,
-- and the reference ingest now upserts one venue per stadium keyed on this
-- column and links each game to it.
--
-- ADDITIONS ONLY: a column, an enum value, and the same view with a different
-- ORDER BY inside its lateral join. Safe to apply before the code ships.
-- =============================================================================

alter table venues
  add column if not exists nflverse_stadium_id text unique;

comment on column venues.nflverse_stadium_id is
  'nflverse schedule stadium id (KAN00, LON02, ...). NULL for college venues, which are keyed on cfbd_id.';

-- Observed NFL conditions: nflverse records kickoff temperature and wind for
-- played outdoor games. A played game then reads as observed rather than as
-- the last forecast made before it.
alter type weather_source add value if not exists 'nflverse';

-- ANY OBSERVATION BEFORE ANY FORECAST, then CFBD first among observations.
-- The old order was only "cfbd first", which was right while CFBD was the only
-- observed source. With nflverse observations beside Open-Meteo forecasts it
-- would have kept showing a played NFL game's forecast. For college the result
-- is unchanged: CFBD rows are observations and Open-Meteo rows are forecasts.
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

  g.completed
from games g
left join venues v on v.id = g.venue_id
left join lateral (
  select *
  from game_weather gw
  where gw.game_id = g.id
  order by gw.is_forecast asc, (gw.source = 'cfbd') desc
  limit 1
) w on true;
