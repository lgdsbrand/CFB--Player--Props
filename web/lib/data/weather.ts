/**
 * Conditions for a game — the read behind the player page's weather panel.
 *
 * `v_game_conditions` is driven from `games`, not from `game_weather`, so this
 * returns a row for every game whether or not weather exists for it. That is
 * what lets the panel tell a domed stadium apart from a forecast that has not
 * landed yet; see migration 0042.
 *
 * The view already prefers the CFBD observation over the Open-Meteo forecast
 * where both exist, matching `worker/core/features.py`, so there is no
 * precedence logic here to drift from the model's.
 */

import type { GameConditions } from "@/lib/core/types";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";

const COLUMNS =
  "game_id, venue_name, venue_is_dome, completed, source, is_forecast, " +
  "observed_at, temperature_f, dew_point_f, humidity, precipitation_in, " +
  "snowfall_in, wind_speed_mph, wind_direction_deg, pressure_mb, condition";

function toConditions(row: DbRow): GameConditions {
  return {
    gameId: row.game_id as number,
    venueName: (row.venue_name as string | null) ?? null,
    venueIsDome: (row.venue_is_dome as boolean | null) ?? false,
    completed: (row.completed as boolean | null) ?? false,

    source: (row.source as string | null) ?? null,
    isForecast: (row.is_forecast as boolean | null) ?? null,
    observedAt: (row.observed_at as string | null) ?? null,

    // Postgres `numeric` arrives over PostgREST as a string, so every one of
    // these is coerced rather than cast. A cast would typecheck and then
    // compare "3.1" >= 15 as a string at runtime.
    temperatureF: toNumber(row.temperature_f),
    dewPointF: toNumber(row.dew_point_f),
    humidity: toNumber(row.humidity),
    precipitationIn: toNumber(row.precipitation_in),
    snowfallIn: toNumber(row.snowfall_in),
    windSpeedMph: toNumber(row.wind_speed_mph),
    windDirectionDeg: toNumber(row.wind_direction_deg),
    pressureMb: toNumber(row.pressure_mb),
    condition: (row.condition as string | null) ?? null,
  };
}

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Conditions for one game, or null if the game does not exist. */
export async function getGameConditions(
  gameId: number,
): Promise<GameConditions | null> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_game_conditions")
      .select(COLUMNS)
      .eq("game_id", gameId)
      .limit(1),
    "v_game_conditions",
  );
  return rows.length > 0 ? toConditions(rows[0]) : null;
}
