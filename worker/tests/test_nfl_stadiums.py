"""NFL stadiums and observed weather (migration 0079). No network, no database."""

from __future__ import annotations

from worker.adapters.nflverse.ingest_reference import observed_weather, venue_row
from worker.adapters.nflverse.stadiums import STADIUMS, resolve_stadium

# Every stadium_id on the 2026 nflverse schedule, as measured 2026-10-03.
SCHEDULE_2026_IDS = {
    "ATL97", "BAL00", "BOS00", "BUF00", "CAR00", "CHI98", "CIN00", "CLE00",
    "DAL00", "DEN00", "DET00", "GNB00", "HOU00", "IND00", "JAX00", "KAN00",
    "LAX01", "LON00", "LON02", "MAD01", "MEL00", "MEX00", "MIA00", "MIN01",
    "MUN01", "NAS00", "NOR00", "NYC01", "PAR00", "PHI00", "PHO00", "PIT00",
    "RIO00", "SEA00", "SFO01", "TAM00", "VEG00", "WAS00",
}


def test_every_2026_stadium_is_known():
    assert SCHEDULE_2026_IDS <= {s.stadium_id for s in STADIUMS}


def test_ids_are_unique_and_coordinates_are_plausible():
    ids = [s.stadium_id for s in STADIUMS]
    assert len(ids) == len(set(ids))
    for s in STADIUMS:
        assert -90 <= s.latitude <= 90 and -180 <= s.longitude <= 180, s
        if s.country_code == "US":
            assert 24 < s.latitude < 49 and -125 < s.longitude < -66, s


def test_a_name_that_belongs_elsewhere_beats_the_id():
    """The 2026 schedule files a Tottenham game under JAX00: London, not Florida."""
    s = resolve_stadium("JAX00", "Tottenham Hotspur Stadium")
    assert s is not None and s.stadium_id == "LON02"


def test_the_id_resolves_when_the_name_is_an_old_one():
    s = resolve_stadium("HOU00", "Reliant Stadium")
    assert s is not None and s.stadium_id == "HOU00"
    assert resolve_stadium("KAN00", "Some New Sponsor Field").stadium_id == "KAN00"


def test_unknown_stadiums_resolve_to_none_rather_than_a_guess():
    assert resolve_stadium("XYZ99", "Nowhere Park") is None
    assert resolve_stadium(None, None) is None


def test_only_fixed_roofs_count_as_domes():
    """nflverse labels the open-air MCG and Stade de France 'dome'; ours does not."""
    domes = {s.stadium_id for s in STADIUMS if s.is_dome}
    assert domes == {"DET00", "LAX01", "MIN01", "NOR00", "VEG00"}


def test_venue_row_carries_what_the_weather_job_selects_on():
    row = venue_row(resolve_stadium("GNB00", "Lambeau Field"))
    assert row["nflverse_stadium_id"] == "GNB00"
    assert row["latitude"] and row["longitude"] and row["is_dome"] is False


def _sched(**kw):
    base = {"home_score": "24", "temp": "41", "wind": "12", "roof": "outdoors",
            "gameday": "2026-09-27", "gametime": "13:00"}
    base.update(kw)
    return base


def test_a_played_outdoor_game_is_an_observation():
    w = observed_weather(_sched(), resolve_stadium("GNB00", None), 7)
    assert w["source"] == "nflverse" and w["is_forecast"] is False
    assert w["temperature_f"] == 41.0 and w["wind_speed_mph"] == 12.0
    assert w["is_indoor"] is False and w["game_id"] == 7


def test_no_observation_for_an_unplayed_game_or_an_empty_reading():
    assert observed_weather(_sched(home_score=None), None, 7) is None
    assert observed_weather(_sched(temp="NA", wind=None), None, 7) is None


def test_a_closed_roof_is_indoors():
    w = observed_weather(_sched(roof="closed"), resolve_stadium("DAL00", None), 7)
    assert w["is_indoor"] is True
