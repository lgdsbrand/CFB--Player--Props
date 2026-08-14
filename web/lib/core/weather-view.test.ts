/**
 * Tests for the conditions panel's presentation logic.
 *
 * THE ONE THAT MATTERS MOST is that a dome and a missing forecast stay
 * distinguishable. Both have no weather to show and they mean opposite things —
 * "conditions are not a factor" versus "not known yet" — and collapsing them
 * into one empty card is the failure this product has already made three times.
 * `v_game_conditions` is driven from `games` rather than from `game_weather`
 * specifically so this module can tell them apart (migration 0042).
 *
 * The fixture is the real 23:00 reading Open-Meteo returned for Scott Stadium
 * on 2026-08-20, the same one the worker's `test_weather.py` uses, so both ends
 * of the pipeline are asserting against the same measured values.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { GameConditions } from "./types.ts";
import {
  conditionFlags,
  conditionsSummary,
  formatPrecipitation,
  formatTemperature,
  formatWind,
  formatWindDirection,
  weatherState,
  weatherView,
} from "./weather-view.ts";

function conditions(overrides: Partial<GameConditions> = {}): GameConditions {
  return {
    gameId: 1,
    venueName: "Scott Stadium",
    venueIsDome: false,
    completed: false,
    source: "open_meteo",
    isForecast: true,
    observedAt: "2026-08-20T23:00:00+00:00",
    temperatureF: 79.3,
    dewPointF: 71.8,
    humidity: 78,
    precipitationIn: 0.008,
    snowfallIn: 0,
    windSpeedMph: 3.1,
    windDirectionDeg: 4,
    pressureMb: 991.2,
    condition: "Light drizzle",
    ...overrides,
  };
}

const NO_READING = {
  temperatureF: null,
  windSpeedMph: null,
  condition: null,
  source: null,
  isForecast: null,
} as const;

test("a dome and a missing forecast are different states", () => {
  assert.equal(weatherState(conditions({ venueIsDome: true })), "indoors");
  assert.equal(weatherState(conditions(NO_READING)), "pending");
});

test("a played game with no reading is unrecorded, not pending", () => {
  // Caught by looking at a rendered 2025 page, which told the reader a
  // forecast was on its way for a game played months earlier. Most of the
  // archive is finished games, so this is the common case, not a corner.
  assert.equal(
    weatherState(conditions({ ...NO_READING, completed: true })),
    "unrecorded",
  );
  assert.equal(
    weatherState(conditions({ ...NO_READING, completed: false })),
    "pending",
  );
});

test("neither empty state claims to have a reading", () => {
  assert.equal(weatherView(conditions({ ...NO_READING, completed: true })).hasReading, false);
  assert.equal(weatherView(conditions({ ...NO_READING, completed: false })).hasReading, false);
});

test("a dome stays indoors even if a reading somehow exists", () => {
  // The ingest skips domes, but a CFBD observation can still land on one.
  // Conditions under a roof are not a factor whatever the number says.
  const domed = conditions({ venueIsDome: true, windSpeedMph: 40 });
  assert.equal(weatherState(domed), "indoors");
  assert.deepEqual(conditionFlags(domed), []);
});

test("an observation is distinguished from a forecast", () => {
  assert.equal(
    weatherState(conditions({ isForecast: false, source: "cfbd" })),
    "observed",
  );
  assert.equal(weatherState(conditions({ isForecast: true })), "forecast");
});

test("an unknown provenance reads as a forecast, which overstates least", () => {
  assert.equal(weatherState(conditions({ isForecast: null })), "forecast");
});

test("no conditions at all is pending", () => {
  assert.equal(weatherState(null), "pending");
});

test("a mild evening raises no flags", () => {
  assert.deepEqual(conditionFlags(conditions()), []);
});

test("wind flags at the threshold, not below it", () => {
  assert.deepEqual(conditionFlags(conditions({ windSpeedMph: 14.4 })), []);
  assert.deepEqual(conditionFlags(conditions({ windSpeedMph: 15 })), [
    { key: "wind", label: "15 mph wind" },
  ]);
});

test("snow is reported instead of rain, never both", () => {
  // Snow implies precipitation, so flagging both describes one fact twice and
  // reads as two separate problems.
  const flags = conditionFlags(
    conditions({ snowfallIn: 0.4, precipitationIn: 0.3 }),
  );
  assert.deepEqual(
    flags.map((f) => f.key),
    ["snow"],
  );
});

test("cold and heat flag at their own ends", () => {
  assert.deepEqual(conditionFlags(conditions({ temperatureF: 21 })), [
    { key: "cold", label: "21°F" },
  ]);
  assert.deepEqual(conditionFlags(conditions({ temperatureF: 96 })), [
    { key: "heat", label: "96°F" },
  ]);
});

test("several conditions can flag at once", () => {
  const flags = conditionFlags(
    conditions({ windSpeedMph: 22, temperatureF: 18, snowfallIn: 0.5 }),
  );
  assert.deepEqual(flags.map((f) => f.key).sort(), ["cold", "snow", "wind"]);
});

test("a null measurement is ignored rather than treated as zero", () => {
  // A missing temperature must not read as 0°F and flag "cold".
  assert.deepEqual(conditionFlags(conditions({ temperatureF: null })), []);
});

test("weatherView reports whether there is anything to render", () => {
  assert.equal(weatherView(conditions()).hasReading, true);
  assert.equal(weatherView(null).hasReading, false);
  assert.equal(weatherView(conditions({ venueIsDome: true })).hasReading, false);
});

test("formatting rounds away false precision", () => {
  assert.equal(formatTemperature(79.3), "79°");
  assert.equal(formatWind(3.1), "3 mph");
});

test("a missing value formats as a dash, not a zero", () => {
  assert.equal(formatTemperature(null), "—");
  assert.equal(formatWind(null), "—");
  assert.equal(formatPrecipitation(null), "—");
});

test("a true zero of rain reads as None", () => {
  // "0.00 in" reads as a measurement that happened to round down.
  assert.equal(formatPrecipitation(0), "None");
  assert.equal(formatPrecipitation(0.008), "0.01 in");
});

test("degrees convert to the compass point the wind blows from", () => {
  assert.equal(formatWindDirection(4), "N");
  assert.equal(formatWindDirection(90), "E");
  assert.equal(formatWindDirection(181), "S");
  assert.equal(formatWindDirection(350), "N");
  assert.equal(formatWindDirection(null), null);
});

test("a calm reading gets no direction", () => {
  // The real Husky Stadium row carries 0 mph and 4 degrees, which rendered as
  // "0 mph N" — a heading for a wind that is not blowing.
  assert.equal(formatWindDirection(4, 0), null);
  assert.equal(formatWindDirection(4, 0.3), null);
  assert.equal(formatWindDirection(4, 3.1), "N");
});

/* ---------------------------------------------------------------------------
 * The one-line summary on a games-index card.
 *
 * THE POINT OF THESE IS WHAT IT DOES NOT SAY. The panel is a section a reader
 * opened; the card is one of sixty in a grid. "No forecast yet" is a useful
 * answer in the first place and sixty identical apologies in the second, so
 * the summary returns null where the panel returns a sentence.
 * ------------------------------------------------------------------------ */

test("a card with no forecast gets no conditions line at all", () => {
  assert.equal(conditionsSummary(null), null);
  assert.equal(
    conditionsSummary(
      conditions({ temperatureF: null, windSpeedMph: null, condition: null }),
    ),
    null,
  );
  // A finished game that never had conditions recorded is equally silent — the
  // card cannot act on either, and they differ only in the explanation the
  // game page gives.
  assert.equal(
    conditionsSummary(
      conditions({
        completed: true,
        temperatureF: null,
        windSpeedMph: null,
        condition: null,
      }),
    ),
    null,
  );
});

test("a dome says so, because that IS the answer", () => {
  assert.deepEqual(conditionsSummary(conditions({ venueIsDome: true })), {
    text: "Indoors",
    notable: false,
  });
});

test("the summary leads with temperature and adds wind when it is blowing", () => {
  // The real Scott Stadium forecast: 79.3°F, 3.1 mph.
  assert.deepEqual(conditionsSummary(conditions()), {
    text: "79° · 3 mph",
    notable: false,
  });

  // A calm reading drops the wind rather than printing "0 mph".
  assert.deepEqual(conditionsSummary(conditions({ windSpeedMph: 0.2 })), {
    text: "79°",
    notable: false,
  });
});

test("the card highlights on the same thresholds the panel flags", () => {
  // A card calling 12 mph notable while the page it links to calls it
  // unremarkable would be two answers to one question.
  assert.equal(conditionsSummary(conditions({ windSpeedMph: 12 }))?.notable, false);
  assert.equal(conditionsSummary(conditions({ windSpeedMph: 15 }))?.notable, true);
});

test("rain earns a word, wind does not repeat itself", () => {
  const wet = conditionsSummary(
    conditions({ windSpeedMph: 18, precipitationIn: 0.2 }),
  );
  // "18 mph" appears once as a number; the wind flag must not also print
  // "18 mph wind" beside it.
  assert.equal(wet?.text, "79° · 18 mph · Rain");
  assert.equal(wet?.notable, true);

  const snowy = conditionsSummary(
    conditions({ temperatureF: 28, snowfallIn: 0.5, precipitationIn: 0.2 }),
  );
  assert.equal(snowy?.text, "28° · 3 mph · Snow falling");
});
