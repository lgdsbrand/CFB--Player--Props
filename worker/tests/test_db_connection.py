"""Every worker connection carries timeouts, so a dead one fails instead of hanging.

2026-10-21: a connection dropped mid-job and reprice_picks waited on it for
nine hours; Render skipped every later run of the NFL odds chain meanwhile.
"""

from __future__ import annotations

import psycopg
import pytest

from worker import db


def test_open_connection_passes_the_timeouts(monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}

    def fake_connect(url, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    db.open_connection("postgresql://u:p@h:5432/d")
    for key, value in db.CONNECTION_TIMEOUTS.items():
        assert seen[key] == value


def test_a_dead_connection_is_noticed_within_a_few_minutes():
    t = db.CONNECTION_TIMEOUTS
    keepalive_seconds = t["keepalives_idle"] + t["keepalives_interval"] * t["keepalives_count"]
    assert t["keepalives"] == 1
    assert keepalive_seconds <= 180
    assert t["tcp_user_timeout"] <= 180_000
    assert 0 < t["connect_timeout"] <= 30


def test_libpq_accepts_every_option():
    # A misspelt key would make every connection fail with "invalid connection
    # option"; parsing the conninfo catches it without a server.
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    parsed = conninfo_to_dict(make_conninfo("host=h dbname=d", **db.CONNECTION_TIMEOUTS))
    assert set(db.CONNECTION_TIMEOUTS) <= set(parsed)
