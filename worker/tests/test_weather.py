"""Tests for the Open-Meteo forecast adapter.

THE PAYLOAD FIXTURE IS REAL. The hourly slice below was returned by Open-Meteo
on 2026-08-13 for Scott Stadium's coordinates on 2026-08-20, rather than
invented, because what is most likely to be wrong here is a convention rather
than logic: whether temperature arrives in Fahrenheit or Celsius, whether
pressure is hPa or kPa, and what the hourly key names are. A hand-written
fixture would have agreed with whatever the code assumed.

All offline. No database, no network — `fetch_all` and `upsert` are patched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from worker.adapters.open_meteo import ingest_weather as iw
from worker.adapters.open_meteo.client import HourlyForecast, OpenMeteoError, condition_for

# Real response, trimmed to the last four hours of the day.
REAL_TIMES = [
    "2026-08-20T20:00",
    "2026-08-20T21:00",
    "2026-08-20T22:00",
    "2026-08-20T23:00",
]
REAL_VALUES: dict[str, list[Any]] = {
    "temperature_2m": [90.4, 86.5, 82.9, 79.3],
    "dew_point_2m": [66.9, 68.4, 70.4, 71.8],
    "relative_humidity_2m": [46, 55, 66, 78],
    "precipitation": [0.0, 0.0, 0.008, 0.008],
    "snowfall": [0.0, 0.0, 0.0, 0.0],
    "wind_speed_10m": [4.2, 7.2, 6.1, 3.1],
    "wind_direction_10m": [25, 14, 10, 4],
    "surface_pressure": [991.5, 991.2, 991.1, 991.2],
    "weather_code": [2, 2, 51, 51],
}


def a_forecast() -> HourlyForecast:
    return HourlyForecast(
        times=list(REAL_TIMES),
        values={k: list(v) for k, v in REAL_VALUES.items()},
    )


def a_game(game_id: int, hour: int, *, venue_id: int = 7, minute: int = 0) -> dict[str, Any]:
    return {
        "game_id": game_id,
        "week": 1,
        "start_date": datetime(2026, 8, 20, hour, minute, tzinfo=UTC),
        "venue_id": venue_id,
        "latitude": 38.03118,
        "longitude": -78.51379,
        "venue_name": f"Venue {venue_id}",
    }


class StubClient:
    """Records the calls it was asked to make, and can fail chosen venues."""

    def __init__(self, fail_for: set[tuple[float, float]] | None = None) -> None:
        self.calls: list[tuple[float, float, str, str]] = []
        self.fail_for = fail_for or set()
        self.call_count = 0

    def forecast(self, latitude, longitude, start, end):  # noqa: ANN001
        self.call_count += 1
        self.calls.append((latitude, longitude, start.isoformat(), end.isoformat()))
        if (latitude, longitude) in self.fail_for:
            raise OpenMeteoError("stub failure")
        return a_forecast()


@pytest.fixture
def captured(monkeypatch):
    """Patch the write path and hand back whatever the ingest tried to upsert."""
    written: list[list[dict[str, Any]]] = []

    def fake_upsert(table, rows, **kwargs):  # noqa: ANN001
        assert table == "game_weather"
        assert kwargs["conflict_columns"] == ["game_id", "source"]
        written.append(list(rows))
        return len(rows)

    monkeypatch.setattr(iw, "upsert", fake_upsert)
    return written


def patch_games(monkeypatch, games: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(iw, "fetch_all", lambda *a, **k: games)


# -- the hour that describes the game -----------------------------------------
def test_the_reading_nearest_kickoff_is_the_one_used():
    hit = iw._nearest_hour(a_forecast(), datetime(2026, 8, 20, 22, 40, tzinfo=UTC))
    assert hit is not None
    index, distance = hit
    assert REAL_TIMES[index] == "2026-08-20T23:00"
    assert distance.total_seconds() == 20 * 60


def test_a_kickoff_the_series_does_not_cover_is_skipped(monkeypatch, captured):
    """A reading two hours away describes a different game, not this one."""
    patch_games(monkeypatch, [a_game(1, hour=3)])
    counts = iw.run_weather_ingest(StubClient(), 2026)

    assert counts.written == 0
    assert counts.skipped == {"no hourly reading near kickoff": 1}
    assert captured == []


# -- units and column mapping --------------------------------------------------
def test_the_real_payload_maps_onto_the_table_with_its_units_intact(
    monkeypatch, captured
):
    patch_games(monkeypatch, [a_game(101, hour=23)])
    counts = iw.run_weather_ingest(StubClient(), 2026)

    assert counts.written == 1
    (row,) = captured[0]

    assert row["game_id"] == 101
    assert row["source"] == "open_meteo"
    assert row["temperature_f"] == 79.3
    assert row["dew_point_f"] == 71.8
    assert row["humidity"] == 78
    assert row["precipitation_in"] == 0.008
    assert row["snowfall_in"] == 0.0
    assert row["wind_speed_mph"] == 3.1
    assert row["wind_direction_deg"] == 4
    assert row["pressure_mb"] == 991.2
    assert row["condition"] == "Light drizzle"


def test_a_forecast_is_labelled_a_forecast(monkeypatch, captured):
    """`is_forecast` is what keeps a prediction out of a backtest's evidence.

    features.py warns that reading observed conditions grants the model more
    than was knowable. That caveat is only actionable if the two are separable.
    """
    patch_games(monkeypatch, [a_game(1, hour=23)])
    iw.run_weather_ingest(StubClient(), 2026)

    (row,) = captured[0]
    assert row["is_forecast"] is True
    assert row["is_indoor"] is False
    # The hour described, not the hour fetched.
    assert row["observed_at"] == datetime(2026, 8, 20, 23, tzinfo=UTC)


# -- call economy --------------------------------------------------------------
def test_games_sharing_a_venue_and_a_day_share_one_call(monkeypatch, captured):
    patch_games(
        monkeypatch,
        [a_game(1, hour=20, venue_id=7), a_game(2, hour=23, venue_id=7)],
    )
    client = StubClient()
    counts = iw.run_weather_ingest(client, 2026)

    assert client.call_count == 1
    assert counts.written == 2
    assert {r["game_id"] for r in captured[0]} == {1, 2}
    # Each read its OWN hour out of the shared response.
    by_game = {r["game_id"]: r["temperature_f"] for r in captured[0]}
    assert by_game == {1: 90.4, 2: 79.3}


def test_one_date_is_requested_not_a_window(monkeypatch, captured):
    """A widened window fell outside the provider's allowed range and 400'd."""
    patch_games(monkeypatch, [a_game(1, hour=23)])
    client = StubClient()
    iw.run_weather_ingest(client, 2026)

    (_lat, _lon, start, end) = client.calls[0]
    assert start == end == "2026-08-20"


# -- partial failure -----------------------------------------------------------
def test_one_venue_failing_does_not_cost_the_others_their_forecast(
    monkeypatch, captured
):
    patch_games(
        monkeypatch,
        [
            a_game(1, hour=23, venue_id=7),
            {**a_game(2, hour=23, venue_id=9), "latitude": 40.0, "longitude": -80.0},
        ],
    )
    client = StubClient(fail_for={(40.0, -80.0)})
    counts = iw.run_weather_ingest(client, 2026)

    assert counts.written == 1
    assert counts.skipped == {"forecast unavailable": 1}
    assert [r["game_id"] for r in captured[0]] == [1]


def test_a_dry_run_writes_nothing_and_calls_nothing(monkeypatch, captured):
    patch_games(monkeypatch, [a_game(1, hour=23)])
    client = StubClient()
    counts = iw.run_weather_ingest(client, 2026, dry_run=True)

    assert client.call_count == 0
    assert counts.written == 0
    assert captured == []


# -- WMO codes -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("code", "expected"),
    [(0, "Clear"), (51, "Light drizzle"), (95, "Thunderstorm"), (75, "Heavy snow")],
)
def test_known_wmo_codes_read_like_the_cfbd_phrases(code, expected):
    assert condition_for(code) == expected


@pytest.mark.parametrize("code", [None, "", 4242, "banana"])
def test_an_unrecognised_code_is_none_rather_than_invented(code):
    assert condition_for(code) is None
