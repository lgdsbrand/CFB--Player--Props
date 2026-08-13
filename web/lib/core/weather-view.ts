import type { GameConditions } from "@/lib/core/types";

/**
 * Presenting game conditions.
 *
 * FOUR STATES, AND THEY MUST STAY DISTINGUISHABLE. A domed stadium and a game
 * whose forecast has not landed yet both have no weather, and they mean
 * opposite things — one is an answer, the other is "not yet". Collapsing them
 * into one empty card is the tile-with-nothing-behind-it failure this product
 * has already made three times, so `v_game_conditions` is driven from `games`
 * specifically to keep them apart (migration 0042).
 *
 * THIS MODULE DESCRIBES CONDITIONS; IT DOES NOT ADJUST ANYTHING. The projection
 * already carries weather as a model feature. A reader who sees "20 mph wind"
 * beside a passing-yards call must not conclude the call needs discounting
 * again — that would be counting the same fact twice.
 */

export type ConditionsState =
  /** Roof over the field; conditions are not a factor. */
  | "indoors"
  /** A forecast, made before kickoff. */
  | "forecast"
  /** What actually happened, once the game has been played. */
  | "observed"
  /** Outdoors, upcoming, no reading yet. Normal beyond the forecast horizon. */
  | "pending"
  /** Outdoors, already played, and nothing was ever recorded. */
  | "unrecorded";

/**
 * A condition worth calling out, with why.
 *
 * Thresholds are the ones commentary and betting markets conventionally treat
 * as meaningful for the passing game, NOT thresholds the model fits — the model
 * uses the continuous values. They exist so the panel can bold something rather
 * than leaving a reader to judge whether 12 mph is a lot.
 */
export type ConditionFlag = {
  key: "wind" | "precipitation" | "snow" | "cold" | "heat";
  label: string;
};

/** Sustained wind at which passing and kicking are conventionally affected. */
export const WIND_NOTABLE_MPH = 15;
/** Any measurable hourly accumulation. Below this reads as a damp field. */
export const PRECIPITATION_NOTABLE_IN = 0.02;
export const SNOW_NOTABLE_IN = 0.1;
export const COLD_NOTABLE_F = 32;
export const HEAT_NOTABLE_F = 90;

export type WeatherView = {
  state: ConditionsState;
  flags: ConditionFlag[];
  /** True when there is a reading to render. */
  hasReading: boolean;
};

export function weatherState(conditions: GameConditions | null): ConditionsState {
  if (!conditions) return "pending";
  if (conditions.venueIsDome) return "indoors";
  // "Not yet" and "never" are different answers, and the panel says different
  // things for each. A finished game told to wait for a forecast is simply a
  // false statement — and most of the archive is finished games.
  if (!hasAnyReading(conditions)) {
    return conditions.completed ? "unrecorded" : "pending";
  }
  // `isForecast` is nullable in the view, and an unknown provenance is treated
  // as an observation only when the source says so. Anything else is described
  // as a forecast, which is the claim that overstates least.
  return conditions.isForecast === false ? "observed" : "forecast";
}

/**
 * Whether the row carries any usable measurement.
 *
 * A row can exist with every field null — the view is driven from `games`, so a
 * game with no weather row at all still produces one. Temperature and wind are
 * the two the panel leads with; a row with neither has nothing to say.
 */
function hasAnyReading(conditions: GameConditions): boolean {
  return (
    conditions.temperatureF !== null ||
    conditions.windSpeedMph !== null ||
    conditions.condition !== null
  );
}

export function conditionFlags(conditions: GameConditions | null): ConditionFlag[] {
  if (!conditions || conditions.venueIsDome) return [];

  const flags: ConditionFlag[] = [];
  const { windSpeedMph, precipitationIn, snowfallIn, temperatureF } = conditions;

  if (windSpeedMph !== null && windSpeedMph >= WIND_NOTABLE_MPH) {
    flags.push({
      key: "wind",
      label: `${Math.round(windSpeedMph)} mph wind`,
    });
  }
  if (snowfallIn !== null && snowfallIn >= SNOW_NOTABLE_IN) {
    flags.push({ key: "snow", label: "Snow falling" });
  } else if (
    precipitationIn !== null &&
    precipitationIn >= PRECIPITATION_NOTABLE_IN
  ) {
    // Snow already implies precipitation, so the two are exclusive — reporting
    // "Snow falling" and "Rain" together would describe one fact twice.
    flags.push({ key: "precipitation", label: "Rain" });
  }
  if (temperatureF !== null && temperatureF <= COLD_NOTABLE_F) {
    flags.push({ key: "cold", label: `${Math.round(temperatureF)}°F` });
  } else if (temperatureF !== null && temperatureF >= HEAT_NOTABLE_F) {
    flags.push({ key: "heat", label: `${Math.round(temperatureF)}°F` });
  }

  return flags;
}

export function weatherView(conditions: GameConditions | null): WeatherView {
  const state = weatherState(conditions);
  return {
    state,
    flags: conditionFlags(conditions),
    hasReading: state === "forecast" || state === "observed",
  };
}

/** `79.3` -> `"79°"`. Rounded: a tenth of a degree is false precision. */
export function formatTemperature(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)}°`;
}

/** `3.1` -> `"3 mph"`. */
export function formatWind(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)} mph`;
}

/**
 * `4` -> `"N"`. The compass point wind is blowing FROM, which is the
 * meteorological convention Open-Meteo and CFBD both use.
 *
 * `speedMph` is taken into account because a direction on a calm reading is
 * noise dressed as information: the observed Husky Stadium row carries 0 mph
 * and 4°, which rendered as "0 mph N" and invited a reader to wonder which way
 * a wind that is not blowing was going.
 */
export function formatWindDirection(
  degrees: number | null,
  speedMph: number | null = null,
): string | null {
  if (degrees === null || !Number.isFinite(degrees)) return null;
  if (speedMph !== null && Math.round(speedMph) === 0) return null;
  const points = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const index = Math.round((((degrees % 360) + 360) % 360) / 22.5) % 16;
  return points[index];
}

/** `0.008` -> `"0.01 in"`, and a true zero to `"None"` rather than `"0 in"`. */
export function formatPrecipitation(value: number | null): string {
  if (value === null) return "—";
  if (value === 0) return "None";
  return `${value.toFixed(2)} in`;
}
