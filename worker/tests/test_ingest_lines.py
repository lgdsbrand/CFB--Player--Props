"""Tests for the CFBD game-lines adapter.

THE PAYLOAD FIXTURES ARE REAL. Every line row below was returned by CFBD for
2025 week 8 (Notre Dame vs USC, three providers) rather than invented, because
the two things most likely to be wrong here are conventions rather than logic:
the sign of the spread, and the camelCase key names. A hand-written fixture
would have agreed with whatever the code assumed.

All offline. No database, no network — `fetch_all` and `upsert` are patched.
"""

from __future__ import annotations

from typing import Any

import pytest

from worker.adapters.cfbd import ingest_lines


# The real week-8 response for game 401752871, trimmed to the fields used.
NOTRE_DAME_USC: dict[str, Any] = {
    "id": 401752871,
    "season": 2025,
    "seasonType": "regular",
    "week": 8,
    "homeTeam": "Notre Dame",
    "awayTeam": "USC",
    "lines": [
        {
            "provider": "DraftKings",
            "spread": -10.5,
            "formattedSpread": "Notre Dame -10.5",
            "spreadOpen": -10.5,
            "overUnder": 60.5,
            "overUnderOpen": None,
            "homeMoneyline": -395,
            "awayMoneyline": 310,
        },
        {
            "provider": "ESPN Bet",
            "spread": -10.5,
            "formattedSpread": "Notre Dame -10.5",
            "spreadOpen": -10,
            "overUnder": 60.5,
            "overUnderOpen": 53.5,
            "homeMoneyline": -400,
            "awayMoneyline": 300,
        },
        {
            "provider": "Bovada",
            "spread": -11,
            "formattedSpread": "Notre Dame -11.0",
            "spreadOpen": -8,
            "overUnder": 60.5,
            "overUnderOpen": 61.5,
            "homeMoneyline": -310,
            "awayMoneyline": 255,
        },
    ],
}


class FakeClient:
    """Returns a canned payload and records what was asked for."""

    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def fetch(self, endpoint, api_class, method, **params):  # noqa: ANN001
        self.calls.append({"endpoint": endpoint, **params})
        return self.payload


@pytest.fixture
def patched(monkeypatch):
    """Patch the database seam and capture what would have been written."""
    written: dict[str, Any] = {}

    def fake_fetch_all(sql: str, params=None):  # noqa: ANN001
        if "cfbd_id from games" in sql:
            return [{"id": 55, "cfbd_id": 401752871}]
        if "season_type" in sql:
            return [{"season_type": "regular", "week": 8}]
        return []

    def fake_upsert(table, rows, **kwargs):  # noqa: ANN001
        written["table"] = table
        written["rows"] = list(rows)
        written["kwargs"] = kwargs
        return len(written["rows"])

    monkeypatch.setattr(ingest_lines, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(ingest_lines, "upsert", fake_upsert)
    return written


def test_every_provider_is_kept_not_just_one(patched):
    client = FakeClient([NOTRE_DAME_USC])
    counts = ingest_lines.ingest_game_lines(client, 2025)

    assert counts.rows == 3
    providers = {r["provider"] for r in patched["rows"]}
    assert providers == {"DraftKings", "ESPN Bet", "Bovada"}


def test_the_spread_sign_is_carried_through_unchanged(patched):
    # THE TEST THAT MATTERS. Negative means the HOME team is favoured, verified
    # against formattedSpread across the whole week-8 slate (228 of 228). If
    # anything here ever "helpfully" flips the sign, every card silently names
    # the wrong favourite while still rendering a plausible number.
    client = FakeClient([NOTRE_DAME_USC])
    ingest_lines.ingest_game_lines(client, 2025)

    by_provider = {r["provider"]: r for r in patched["rows"]}
    assert by_provider["DraftKings"]["spread"] == -10.5
    assert by_provider["Bovada"]["spread"] == -11.0

    # And the string that lets the convention be re-checked against live data
    # is stored beside it rather than dropped.
    assert by_provider["DraftKings"]["formatted_spread"] == "Notre Dame -10.5"


def test_a_null_open_is_null_and_not_zero(patched):
    # DraftKings sent overUnderOpen: null. Coercing that to 0.0 would render a
    # game as having opened at a total of zero.
    client = FakeClient([NOTRE_DAME_USC])
    ingest_lines.ingest_game_lines(client, 2025)

    dk = next(r for r in patched["rows"] if r["provider"] == "DraftKings")
    assert dk["over_under_open"] is None
    assert dk["over_under"] == 60.5


def test_a_game_we_do_not_ingest_is_skipped_not_crashed(patched):
    # /lines covers games we never ingest, e.g. an FBS team hosting FCS.
    unknown = {**NOTRE_DAME_USC, "id": 999999999}
    client = FakeClient([unknown])
    counts = ingest_lines.ingest_game_lines(client, 2025)

    assert counts.rows == 0
    assert counts.skipped["unknown game"] == 1


def test_a_game_with_no_lines_is_counted_separately_from_a_missing_game(patched):
    bare = {**NOTRE_DAME_USC, "lines": []}
    client = FakeClient([bare])
    counts = ingest_lines.ingest_game_lines(client, 2025)

    assert counts.rows == 0
    assert counts.skipped["game carried no line"] == 1
    assert "unknown game" not in counts.skipped
    assert counts.games_with_lines == 0


def test_a_duplicated_provider_does_not_abort_the_batch(patched):
    # Postgres refuses an ON CONFLICT batch that hits the same key twice
    # ("cannot affect row a second time"). One provider listed twice for a game
    # is the provider's business, not a reason to fail an ingest.
    doubled = {
        **NOTRE_DAME_USC,
        "lines": [NOTRE_DAME_USC["lines"][0], NOTRE_DAME_USC["lines"][0]],
    }
    client = FakeClient([doubled])
    counts = ingest_lines.ingest_game_lines(client, 2025)

    assert counts.rows == 1


def test_the_two_draftkings_spellings_collapse_to_one_row(patched):
    # MEASURED, NOT HYPOTHETICAL. Across 2025 CFBD sent 805 rows as
    # "DraftKings" and 56 as "Draft Kings", and 56 GAMES carried both. Left
    # alone that is two rows for one book, and v_game_line_consensus takes a
    # median ACROSS ROWS — so DraftKings voted twice on those games and pulled
    # the consensus toward itself, while the card still showed a plausible
    # number. This is the test that keeps that from coming back.
    both = {
        **NOTRE_DAME_USC,
        "lines": [
            {**NOTRE_DAME_USC["lines"][0], "provider": "DraftKings"},
            {**NOTRE_DAME_USC["lines"][0], "provider": "Draft Kings"},
            NOTRE_DAME_USC["lines"][1],
        ],
    }
    client = FakeClient([both])
    counts = ingest_lines.ingest_game_lines(client, 2025)

    providers = sorted(r["provider"] for r in patched["rows"])
    assert providers == ["DraftKings", "ESPN Bet"]
    assert counts.rows == 2

    # The raw spellings are still reported, which is how the NEXT alias gets
    # noticed instead of silently merging or silently splitting.
    assert counts.providers_seen == {"DraftKings", "Draft Kings", "ESPN Bet"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DraftKings", "DraftKings"),
        ("Draft Kings", "DraftKings"),
        ("draftkings", "DraftKings"),
        ("  DraftKings  ", "DraftKings"),
        ("ESPN Bet", "ESPN Bet"),
        ("ESPNBet", "ESPN Bet"),
        ("Bovada", "Bovada"),
        # An unknown book passes through rather than being dropped: a new
        # provider must still be stored, just not silently renamed.
        ("Caesars", "Caesars"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_provider_canonicalisation(raw, expected):
    assert ingest_lines.canonical_provider(raw) == expected


def test_a_line_with_no_provider_is_dropped(patched):
    # `provider` is half the conflict key, so a null one cannot be written.
    anonymous = {**NOTRE_DAME_USC, "lines": [{"spread": -3, "provider": None}]}
    client = FakeClient([anonymous])
    counts = ingest_lines.ingest_game_lines(client, 2025)

    assert counts.rows == 0
    assert counts.skipped["line with no provider"] == 1


def test_a_non_numeric_spread_becomes_null_rather_than_raising(patched):
    junk = {**NOTRE_DAME_USC, "lines": [{"provider": "X", "spread": "off"}]}
    client = FakeClient([junk])
    ingest_lines.ingest_game_lines(client, 2025)

    assert patched["rows"][0]["spread"] is None


def test_the_upsert_targets_the_game_and_provider_pair(patched):
    client = FakeClient([NOTRE_DAME_USC])
    ingest_lines.ingest_game_lines(client, 2025)

    assert patched["table"] == "game_lines"
    assert patched["kwargs"]["conflict_columns"] == ["game_id", "provider"]


def test_live_mode_passes_a_max_age_through_to_the_fetch(patched):
    # Without this the job serves the permanent cache and quietly writes last
    # week's spread while reporting success.
    client = FakeClient([NOTRE_DAME_USC])
    ingest_lines.ingest_game_lines(client, 2025, max_age=900.0)

    assert client.calls[0]["max_age"] == 900.0

    fresh = FakeClient([NOTRE_DAME_USC])
    ingest_lines.ingest_game_lines(fresh, 2025)
    assert fresh.calls[0]["max_age"] is None


def test_weeks_can_be_narrowed_and_a_miss_fetches_nothing(patched):
    client = FakeClient([NOTRE_DAME_USC])
    ingest_lines.ingest_game_lines(client, 2025, weeks=[8])
    assert len(client.calls) == 1

    other = FakeClient([NOTRE_DAME_USC])
    ingest_lines.ingest_game_lines(other, 2025, weeks=[9])
    assert other.calls == []


def test_a_season_with_no_games_returns_early_without_calling_the_api(monkeypatch):
    monkeypatch.setattr(ingest_lines, "fetch_all", lambda *a, **k: [])
    client = FakeClient([NOTRE_DAME_USC])
    counts = ingest_lines.ingest_game_lines(client, 2099)

    assert counts.rows == 0
    assert client.calls == []
