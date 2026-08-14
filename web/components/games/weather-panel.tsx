import {
  formatPrecipitation,
  formatTemperature,
  formatWind,
  formatWindDirection,
  weatherView,
} from "@/lib/core/weather-view";
import type { GameConditions } from "@/lib/core/types";

/**
 * Conditions at kickoff (CLAUDE.md §4, §7).
 *
 * SHARED BY THE PLAYER PAGE AND THE GAME PAGE, which is why it lives here and
 * not under `components/player`. Conditions are a fact about a GAME; the player
 * page just happened to need one first. The copy is written to hold on both
 * surfaces — it says "the calls" rather than "the call above", because on the
 * game page there are many and they are below.
 *
 * FOUR STATES, EACH WITH ITS OWN ANSWER. A dome and a game outside the forecast
 * horizon both have no weather and mean opposite things, so this panel never
 * renders one shrug for both — see `lib/core/weather-view.ts` and migration
 * 0042 for why the view is driven from `games` to make that possible.
 *
 * IT DESCRIBES, IT DOES NOT DISCOUNT. Weather is already a feature of the
 * projection, so the calls and their confidence have accounted for it. A reader
 * who sees "22 mph wind" and marks the passing line down again is counting the
 * same fact twice, which is why the footnote says so plainly rather than
 * leaving it to be inferred.
 */
export function WeatherPanel({
  conditions,
}: {
  conditions: GameConditions | null;
}) {
  const view = weatherView(conditions);

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="section-header flex items-center gap-2">
          <span aria-hidden>🌦️</span>
          Conditions
        </h2>
        {view.state === "forecast" ? (
          <span className="pill bg-accent-indigo/15 text-accent-cyan">
            Forecast
          </span>
        ) : view.state === "observed" ? (
          <span className="pill bg-panel text-muted">Observed</span>
        ) : null}
      </header>

      {view.state === "indoors" ? (
        <p className="text-muted text-xs">
          {conditions?.venueName ?? "This venue"} is indoors, so conditions are
          not a factor.
        </p>
      ) : view.state === "pending" ? (
        <p className="text-muted max-w-prose text-xs">
          No forecast yet. Forecasts reach about 15 days ahead and sharpen daily
          as kickoff approaches, so an empty panel this far out is expected
          rather than a fault.
        </p>
      ) : view.state === "unrecorded" ? (
        <p className="text-muted max-w-prose text-xs">
          No conditions were recorded for this game. Nothing further will arrive
          — it has already been played.
        </p>
      ) : (
        <Reading conditions={conditions as GameConditions} view={view} />
      )}
    </section>
  );
}

function Reading({
  conditions,
  view,
}: {
  conditions: GameConditions;
  view: ReturnType<typeof weatherView>;
}) {
  const direction = formatWindDirection(
    conditions.windDirectionDeg,
    conditions.windSpeedMph,
  );

  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-2xl font-extrabold leading-none tabular-nums">
          {formatTemperature(conditions.temperatureF)}
        </span>
        {conditions.condition ? (
          <span className="text-muted text-sm">{conditions.condition}</span>
        ) : null}
      </div>

      {view.flags.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {view.flags.map((flag) => (
            <span key={flag.key} className="pill bg-target/15 text-target">
              {flag.label}
            </span>
          ))}
        </div>
      ) : null}

      {/* `max-w-md` because this panel is now used at two very different
          widths. In the player page's column the four cells fill the space; on
          the full-width game page they were stretched across 950px, which put
          "Wind" and "Precipitation" far enough apart to stop reading as a
          group. Capping the group keeps one layout honest in both places. */}
      <dl className="grid max-w-md grid-cols-2 gap-x-3 gap-y-2">
        <Cell
          label="Wind"
          value={
            formatWind(conditions.windSpeedMph) +
            (direction && conditions.windSpeedMph !== null ? ` ${direction}` : "")
          }
        />
        <Cell
          label="Precipitation"
          value={formatPrecipitation(conditions.precipitationIn)}
        />
        <Cell
          label="Humidity"
          value={conditions.humidity === null ? "—" : `${Math.round(conditions.humidity)}%`}
        />
        <Cell label="Dew point" value={formatTemperature(conditions.dewPointF)} />
      </dl>

      <p className="text-dim text-[0.625rem]">
        {view.state === "forecast"
          ? "Forecast for the kickoff hour."
          : "Measured at the game."}{" "}
        Weather is already an input to the projections, so the calls and their
        confidence have accounted for it — this panel is the detail behind that,
        not a further adjustment to make yourself.
      </p>
    </>
  );
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="label-caption">{label}</dt>
      <dd className="text-sm font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
