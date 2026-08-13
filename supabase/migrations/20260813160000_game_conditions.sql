-- =============================================================================
-- 0042 -- weather for games that have not been played yet
-- =============================================================================
-- CLAUDE.md §4 names Open-Meteo as the fallback for outdoor venues, and §7 asks
-- for weather-adjusted projections. The table, the ingest and the model feature
-- have existed since Phase 2. What was missing is the only case the board
-- actually needs.
--
-- **CFBD's `/games/weather` serves OBSERVED conditions**, so it returns nothing
-- for a game that has not kicked off. Measured on 2026-08-13 with the response
-- cache bypassed (`max_age=0`, because `ingest_ratings` caches this endpoint as
-- IMMUTABLE and a stale empty response is indistinguishable from a real one):
-- 2026 week 1 returned 0 rows, week 2 returned 0 rows, 2025 week 7 returned 55.
-- `game_weather` on production held 1,813 rows, every one of them 2024-2025 and
-- every one of them `source = 'cfbd'`.
--
-- So a weather panel built on what existed would have been blank on every
-- upcoming game -- which is every game the board is for.
--
-- ---------------------------------------------------------------------------
-- `is_forecast` IS NOT COSMETIC
-- ---------------------------------------------------------------------------
-- `worker/core/features.py` carries an explicit caveat: `game_weather` holds
-- observations, so a backtest that reads it grants the model conditions nobody
-- had at prediction time, and the bias runs in the flattering direction
-- (CLAUDE.md §4 forbids exactly this shape of lookahead). Forecast rows do not
-- have that problem -- a forecast written before kickoff IS what was knowable --
-- but only if the two can be told apart. `source` is a poor proxy: Open-Meteo
-- also serves an archive endpoint, so the day anything reads history from it,
-- source would silently start meaning the opposite.
--
-- Existing rows are all CFBD observations, so the `false` default is correct
-- for every one of them and no backfill is needed.
-- =============================================================================

alter table game_weather
  add column if not exists is_forecast boolean not null default false;

comment on column game_weather.is_forecast is
  'True when this row is a FORECAST made before kickoff rather than an observation of what happened. Load-bearing for point-in-time honesty (CLAUDE.md §4): worker/core/features.py notes that reading observed conditions in a backtest grants the model more than was knowable, and only this column distinguishes the two. Do not infer it from `source` -- Open-Meteo serves an archive endpoint as well as a forecast one.';

comment on table game_weather is
  'CFBD weather where present, Open-Meteo as the fallback for outdoor venues (CLAUDE.md §4). Both sources may coexist for a game; the model and v_game_conditions prefer cfbd and fall back to open_meteo. CFBD serves OBSERVED conditions only and returns nothing for an unplayed game, so every row for an upcoming game comes from Open-Meteo and carries is_forecast = true.';


-- ---------------------------------------------------------------------------
-- v_game_conditions
-- ---------------------------------------------------------------------------
-- DRIVEN FROM `games`, NOT FROM `game_weather`, and that is the whole design.
--
-- A view over the weather table would have a row only where weather exists, so
-- an indoor game and a game whose forecast has not landed yet would both be
-- absent, and the panel could not tell them apart. It would then have to render
-- the same empty state for "the roof is closed, conditions are irrelevant" and
-- "check back tomorrow", which is the tile-with-nothing-behind-it failure this
-- product has now made three times.
--
-- Driven from `games`, every game has exactly one row and the three states are
-- distinguishable: `venue_is_dome` true, or weather columns non-null, or
-- neither. Open-Meteo's forecast horizon is ~16 days, so the third state is
-- normal and expected well before kickoff rather than a fault.
create view v_game_conditions
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
  w.condition
from games g
left join venues v on v.id = g.venue_id
-- Prefers the observation once it exists, matching `features.game_weather`.
-- Before kickoff only the forecast exists, so this resolves to it.
left join lateral (
  select *
  from game_weather gw
  where gw.game_id = g.id
  order by (gw.source = 'cfbd') desc
  limit 1
) w on true;

comment on view v_game_conditions is
  'One row per game with its venue and the best available conditions: the CFBD observation where it exists, otherwise the Open-Meteo forecast. Driven from games rather than from game_weather so that an indoor venue, a game awaiting its forecast, and a game with conditions are three DISTINGUISHABLE states -- a view over the weather table alone would omit the first two identically and force the panel to render one empty state for both.';

comment on column v_game_conditions.venue_is_dome is
  'Coalesced to false, so a game with no venue row reads as outdoors rather than as NULL. That is the safe direction: it shows a forecast-pending state rather than silently claiming the roof is closed.';
