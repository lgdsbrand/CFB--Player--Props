"""Minimal HTTP client for the Open-Meteo forecast API.

Uses `urllib.request` from the standard library, like the other three transports
in this repo. CLAUDE.md §0 requires a justification for every new dependency and
this needs one thing: an unauthenticated GET returning JSON.

**No credential exists to leak.** The free forecast endpoint takes no API key,
which is why there is no `redact()` here and why URLs are logged whole — the
opposite of `adapters/odds/http.py`, where the key rides in the query string. If
a keyed Open-Meteo plan is ever adopted, that changes and this comment is where
to start.
"""

from __future__ import annotations

import http.client
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any

from worker.logging_setup import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo documents a 16-day forecast, and that COUNTS TODAY AS DAY ONE, so
# the last requestable date is 15 days out. Not a guess — the provider said so
# in a 400 on 2026-08-13: "Parameter 'start_date' is out of allowed range from
# 2026-05-12 to 2026-08-28". A game beyond the horizon is not an error to
# report, it is simply not knowable yet, so the ingest skips those rather than
# spending a call to be told so.
FORECAST_HORIZON_DAYS = 15

# The hourly fields backing `game_weather`'s columns, in that table's order.
HOURLY_VARIABLES = (
    "temperature_2m",
    "dew_point_2m",
    "relative_humidity_2m",
    "precipitation",
    "snowfall",
    "wind_speed_10m",
    "wind_direction_10m",
    "surface_pressure",
    "weather_code",
)

# Ask for the units the schema already stores, rather than converting here.
# `surface_pressure` is hPa, which is millibars by another name, so
# `pressure_mb` needs no conversion.
UNITS = {
    "temperature_unit": "fahrenheit",
    "wind_speed_unit": "mph",
    "precipitation_unit": "inch",
    "timezone": "UTC",
}

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

DEFAULT_MIN_INTERVAL = 0.2
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE = 1.5
MAX_BACKOFF = 30.0
DEFAULT_TIMEOUT = 30.0

# WMO 4677 present-weather codes, as Open-Meteo documents them, collapsed to the
# short phrases CFBD uses in `game_weather.condition` ("Clear", "Rain") so the
# two sources read the same in the panel. Anything unmapped falls through to
# None rather than to a made-up label.
WMO_CONDITIONS: dict[int, str] = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


def condition_for(code: Any) -> str | None:
    """The readable phrase for a WMO weather code, or None if unrecognised."""
    try:
        return WMO_CONDITIONS.get(int(code))
    except (TypeError, ValueError):
        return None


class OpenMeteoError(RuntimeError):
    """A forecast could not be fetched."""


@dataclass(frozen=True)
class HourlyForecast:
    """One location's hourly series, as parallel lists keyed by variable.

    Open-Meteo returns columns, not rows — `time` alongside one list per
    variable, all the same length. Kept in that shape here and zipped by the
    caller, because the caller wants exactly one hour out of a multi-day series
    and building dicts for the other ~380 would be waste.
    """

    times: list[str]
    values: dict[str, list[Any]]

    def at(self, index: int) -> dict[str, Any]:
        return {name: series[index] for name, series in self.values.items()}


class OpenMeteoClient:
    """Paced, retrying JSON GET client for the forecast endpoint."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout

        self.call_count = 0
        self._last_call_at = 0.0

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if self._last_call_at and elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_at = time.monotonic()

    def forecast(
        self,
        latitude: float,
        longitude: float,
        start: date,
        end: date,
    ) -> HourlyForecast:
        """Hourly conditions for one location over an inclusive date range."""
        params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "hourly": ",".join(HOURLY_VARIABLES),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            **UNITS,
        }
        payload = self._get(params)

        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            raise OpenMeteoError(
                f"No hourly series for {latitude},{longitude} "
                f"{start.isoformat()}..{end.isoformat()}"
            )

        return HourlyForecast(
            times=list(times),
            values={name: list(hourly.get(name) or []) for name in HOURLY_VARIABLES},
        )

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"

        attempt = 0
        while True:
            self._pace()
            try:
                request = urllib.request.Request(
                    url, headers={"Accept": "application/json"}
                )
                with urllib.request.urlopen(  # noqa: S310 - fixed https host
                    request, timeout=self.timeout
                ) as response:
                    self.call_count += 1
                    return json.loads(response.read().decode("utf-8"))

            except urllib.error.HTTPError as exc:
                self.call_count += 1
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:  # pragma: no cover - body already consumed
                    pass

                # Open-Meteo returns 400 with a `reason` for a bad request —
                # an out-of-range date, a malformed coordinate. Retrying cannot
                # fix any of those, and the reason is the useful part.
                if exc.code not in RETRYABLE_STATUSES or attempt >= self.max_retries:
                    raise OpenMeteoError(f"HTTP {exc.code} on {url}: {body}") from exc

                attempt += 1
                delay = self._backoff(exc, attempt)
                log.warning(
                    "%s -> HTTP %s; retry %d/%d in %.1fs",
                    url, exc.code, attempt, self.max_retries, delay,
                )
                time.sleep(delay)

            # Catch the transport layer, do not enumerate it — the lesson
            # `adapters/odds/http.py` paid for twice. `OSError` covers URLError,
            # resets and DNS; `HTTPException` covers http.client's own protocol
            # errors, which are not OSErrors.
            except (OSError, http.client.HTTPException) as exc:
                self.call_count += 1
                reason = getattr(exc, "reason", None) or exc
                if attempt >= self.max_retries:
                    raise OpenMeteoError(f"Network error on {url}: {reason}") from exc
                attempt += 1
                delay = self._backoff(None, attempt)
                log.warning(
                    "%s -> %s; retry %d/%d in %.1fs",
                    url, reason, attempt, self.max_retries, delay,
                )
                time.sleep(delay)

    def _backoff(self, exc: Exception | None, attempt: int) -> float:
        retry_after = None
        headers = getattr(exc, "headers", None)
        if headers:
            try:
                retry_after = float(headers.get("Retry-After") or 0) or None
            except (TypeError, ValueError):
                retry_after = None
        if retry_after:
            return min(retry_after, MAX_BACKOFF)
        delay = min(DEFAULT_BACKOFF_BASE * (2 ** (attempt - 1)), MAX_BACKOFF)
        return delay * (0.5 + random.random())  # noqa: S311 - not cryptographic
