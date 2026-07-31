"""Tests for rating payload coercion.

The lookahead behaviour these support is verified against live data (stored Elo
must equal pregame, never postgame). What is unit-testable is the shape
handling that broke the first run: SP+ nests its component ratings inside
objects while SRS returns plain numbers, and passing the object straight to
psycopg fails with "cannot adapt type 'dict'".

No network and no database.
"""

from __future__ import annotations

from worker.adapters.cfbd.ingest_ratings import _nested_rating, _structured_fields


def test_nested_rating_unwraps_sp_plus_objects():
    """SP+ sends {'rating': 39.5, 'ranking': 3, ...} for offense/defense."""
    assert _nested_rating({"rating": 39.5, "ranking": 3}) == 39.5
    assert _nested_rating({"rating": 9.2, "havoc": {"total": None}}) == 9.2


def test_nested_rating_passes_through_plain_numbers():
    """SRS sends a bare number in the same position."""
    assert _nested_rating(24.6) == 24.6
    assert _nested_rating(0) == 0.0


def test_nested_rating_handles_missing_and_null_inner_values():
    assert _nested_rating(None) is None
    assert _nested_rating({}) is None
    assert _nested_rating({"ranking": 3}) is None          # no rating key
    assert _nested_rating({"rating": None}) is None        # explicit null
    assert _nested_rating("not-a-number") is None


def test_structured_fields_captures_only_nested_payload():
    """Scalars have columns; dicts and lists go to `raw` so nothing is lost."""
    row = {
        "team": "Notre Dame",
        "fpi": 25.305,
        "resumeRanks": {"gameControl": 4},
        "efficiencies": {"offense": 78.053},
    }
    captured = _structured_fields(row)
    assert set(captured) == {"resumeRanks", "efficiencies"}
    assert captured["efficiencies"]["offense"] == 78.053


def test_structured_fields_empty_when_all_scalar():
    """SRS has no nested payload; `raw` should end up NULL rather than '{}'."""
    assert _structured_fields({"team": "Ohio State", "rating": 24.6}) == {}
