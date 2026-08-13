"""Forecast conditions for upcoming games, from Open-Meteo.

One API call covers one venue over one date, and every game at that venue that
day reads its own kickoff hour out of the same response. Weeks are ~99 games at
~99 distinct venues, so this is ~99 unauthenticated calls a day against a free
endpoint — cheap enough that the cadence is a product decision rather than a
budget one, which is the opposite of the odds adapter.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from worker.adapters.open_meteo.client import (
    FORECAST_HORIZON_DAYS,
    OpenMeteoClient,
    OpenMeteoError,
    condition_for,
)
from worker.db import fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "open_meteo"

# How far from kickoff an hourly reading may sit and still describe the game.
# The series is hourly, so the worst case for a kickoff on the half hour is 30
# minutes; anything beyond an hour means the series did not cover the kickoff at
# all and the row would be a guess wearing a timestamp.
MAX_HOUR_DISTANCE = timedelta(hours=1)


@dataclass
class WeatherCounts:
    written: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str, n: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + n

    def summary(self) -> str:
        parts = [f"{self.written:,} rows"]
        if self.skipped:
            parts.append(
                "skipped: "
                + ", ".join(f"{k}={v:,}" for k, v in sorted(self.skipped.items()))
            )
        return " | ".join(parts)


def games_needing_forecast(
    season: int, weeks: list[int] | None = None
) -> list[dict[str, Any]]:
    """Upcoming outdoor games with a locatable venue, inside the horizon.

    FOUR EXCLUSIONS, each for its own reason:

    * **Played games** are CFBD's job. An observation beats a forecast and
      `v_game_conditions` prefers it anyway, so re-forecasting the past would
      spend calls to write a row nothing reads.
    * **Domes** have no weather worth storing. `venues.is_dome` drives an
      explicit "indoors" state in the panel, which is a better answer than a
      temperature reading from the car park.
    * **Venues without coordinates** cannot be queried at all. There are none in
      2026, but `venues.latitude` is nullable and a silent skip is better than a
      request for `None,None`.
    * **Games past the horizon** are not knowable yet. Open-Meteo forecasts ~16
      days ahead; asking for day 30 spends a call to be told 400.
    """
    horizon = datetime.now(UTC) + timedelta(days=FORECAST_HORIZON_DAYS)

    rows = fetch_all(
        """
        select g.id            as game_id,
               g.week,
               g.start_date,
               g.venue_id,
               v.latitude,
               v.longitude,
               v.name          as venue_name
          from games g
          join venues v on v.id = g.venue_id
         where g.season = %(season)s
           and not g.completed
           and coalesce(v.is_dome, false) = false
           and v.latitude is not null
           and v.longitude is not null
           and g.start_date is not null
           and g.start_date <= %(horizon)s
           and (%(weeks)s::int[] is null or g.week = any(%(weeks)s::int[]))
         order by g.start_date
        """,
        {"season": season, "horizon": horizon, "weeks": weeks},
    )
    return rows


def _nearest_hour(
    forecast: Any, kickoff: datetime
) -> tuple[int, timedelta] | None:
    """Index of the hourly reading closest to kickoff, and how far off it is."""
    best: tuple[int, timedelta] | None = None
    for index, stamp in enumerate(forecast.times):
        # Open-Meteo returns naive local time and we ask for UTC, so the series
        # is UTC without a designator. Attach it rather than letting a naive
        # datetime meet an aware one and raise.
        try:
            moment = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        except ValueError:
            continue
        distance = abs(moment - kickoff)
        if best is None or distance < best[1]:
            best = (index, distance)
    return best


def run_weather_ingest(
    client: OpenMeteoClient,
    season: int,
    *,
    weeks: list[int] | None = None,
    dry_run: bool = False,
) -> WeatherCounts:
    counts = WeatherCounts()
    games = games_needing_forecast(season, weeks)

    if not games:
        log.info("season %d: no upcoming outdoor games inside the forecast horizon", season)
        return counts

    # One call per venue-day, shared by every game played there that day.
    by_location: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        kickoff = game["start_date"]
        by_location[(game["venue_id"], kickoff.date().isoformat())].append(game)

    log.info(
        "season %d: %d game(s) across %d venue-day(s)%s",
        season, len(games), len(by_location), " [dry run]" if dry_run else "",
    )
    if dry_run:
        counts.skip("dry run", len(games))
        return counts

    payload: list[dict[str, Any]] = []
    for (_venue_id, day), group in sorted(by_location.items(), key=lambda kv: kv[0][1]):
        first = group[0]
        target = first["start_date"].date()

        try:
            # ONE DATE, NOT A WINDOW. `target` is the UTC date of the kickoff
            # and the series runs 00:00-23:00 UTC on that date, so the kickoff
            # hour is always inside it — a 20:00 ET Saturday game is already
            # Sunday 00:00 UTC here, and asks for Sunday. An earlier version
            # widened this to `target + 1` to be safe and got a 400 for its
            # trouble: the extra day fell past the provider's allowed range.
            forecast = client.forecast(
                float(first["latitude"]),
                float(first["longitude"]),
                start=target,
                end=target,
            )
        except OpenMeteoError as exc:
            # ONE VENUE'S FAILURE IS NOT THE RUN'S. A bad coordinate or a
            # provider hiccup on one stadium should not cost the other 98 their
            # forecast, so this is counted and the loop continues.
            log.warning("%s %s: %s", first["venue_name"], day, exc)
            counts.skip("forecast unavailable", len(group))
            continue

        for game in group:
            hit = _nearest_hour(forecast, game["start_date"])
            if hit is None or hit[1] > MAX_HOUR_DISTANCE:
                counts.skip("no hourly reading near kickoff")
                continue

            reading = forecast.at(hit[0])
            payload.append(
                {
                    "game_id": game["game_id"],
                    "source": SOURCE,
                    "is_forecast": True,
                    "temperature_f": reading.get("temperature_2m"),
                    "dew_point_f": reading.get("dew_point_2m"),
                    "humidity": reading.get("relative_humidity_2m"),
                    "precipitation_in": reading.get("precipitation"),
                    "snowfall_in": reading.get("snowfall"),
                    "wind_speed_mph": reading.get("wind_speed_10m"),
                    "wind_direction_deg": reading.get("wind_direction_10m"),
                    "pressure_mb": reading.get("surface_pressure"),
                    "condition": condition_for(reading.get("weather_code")),
                    "is_indoor": False,
                    # The hour the forecast DESCRIBES, not when it was made —
                    # `ingested_at` carries that. Same meaning as the CFBD rows,
                    # which store the game's start time here.
                    "observed_at": game["start_date"],
                }
            )

    if payload:
        counts.written = upsert(
            "game_weather",
            payload,
            conflict_columns=["game_id", "source"],
        )

    log.info("season %d weather: %s", season, counts.summary())
    return counts
