"""The storage guard shared by the ingest jobs.

It had no tests until 2026-09-07, the day it refused production twice and the
day an NFL job with no guard at all pushed that database past its cap. These
cover the arithmetic and, more importantly, the two things the guard is for:
that a cap of ours is not a cap of Supabase's, and that a job which cannot
estimate its footprint still refuses when there is no room to start.
"""

from __future__ import annotations

import pytest

from worker import db


@pytest.fixture
def sized(monkeypatch):
    """Pin the database size and the configured cap."""
    def apply(used: float, cap: float | None):
        monkeypatch.setattr(db, "database_size_mb", lambda: used)
        monkeypatch.setattr(db, "get_config_value", lambda key: cap)
    return apply


class TestStorageHeadroom:
    def test_available_is_net_of_the_reserve(self, sized):
        sized(300.0, 500.0)
        used, cap, available = db.storage_headroom()
        assert (used, cap) == (300.0, 500.0)
        assert available == 500.0 - 300.0 - db.SIZE_RESERVE_MB

    def test_an_unset_cap_falls_back_rather_than_meaning_unlimited(self, sized):
        """A database migrated before 20260813140000 has no config row."""
        sized(300.0, None)
        assert db.storage_headroom()[1] == db.DEFAULT_SIZE_CAP_MB

    def test_a_raised_cap_is_honoured(self, sized):
        """Production runs at 600 -- set by hand, and above Supabase's 500."""
        sized(522.3, 600.0)
        _, cap, available = db.storage_headroom()
        assert cap == 600.0
        assert available == pytest.approx(17.7)


class TestCheckStorageHeadroom:
    def test_room_to_spare_passes(self, sized):
        sized(300.0, 500.0)
        assert db.check_storage_headroom("job") is True

    def test_a_zero_cost_caller_passes_while_any_room_remains(self, sized):
        """The floor check asks "is there room", not "is there much"."""
        sized(439.9, 500.0)
        assert db.check_storage_headroom("job") is True

    def test_a_database_inside_the_reserve_is_refused(self, sized):
        """Available goes negative before the cap is reached -- that is the point."""
        sized(455.0, 500.0)
        assert db.storage_headroom()[2] < 0
        assert db.check_storage_headroom("job") is False

    def test_a_known_cost_larger_than_the_headroom_is_refused(self, sized):
        sized(400.0, 500.0)
        assert db.check_storage_headroom("job", needed_mb=41.0) is False
        assert db.check_storage_headroom("job", needed_mb=39.0) is True

    def test_production_today_still_has_room(self, sized):
        """522.3 MB against the cap raised to 600: tight, not refusing."""
        sized(522.3, 600.0)
        assert db.check_storage_headroom("nfl_ingest_plays") is True
        assert db.check_storage_headroom("nfl_ingest_plays", needed_mb=20.0) is False

    def test_the_refusal_names_the_job_and_the_cap(self, sized, caplog):
        import logging

        sized(455.0, 500.0)
        with caplog.at_level(logging.ERROR):
            db.check_storage_headroom("nfl_ingest_plays")
        message = caplog.text
        assert "nfl_ingest_plays" in message
        assert "db_size_cap_mb" in message
