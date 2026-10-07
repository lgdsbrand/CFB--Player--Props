"""Depth charts and injury reports (migration 0086) — offline.

What can go silently wrong here, each pinned with a hand-built case: a stale
snapshot stamped onto this week, a row written into a week that has already
kicked off, an unresolved player dropped (a missing row reads as a healthy
or missing player), a guessed match between two players with one name, and a
broken Sleeper response clearing every team's report.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from worker.adapters.nflverse.client import NflverseError
from worker.adapters.nflverse.ingest_depth_charts import build_rows as depth_rows
from worker.adapters.nflverse.ingest_depth_charts import latest_snapshot
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.adapters.sleeper.client import SleeperError
from worker.adapters.sleeper.ingest_injuries import (
    MIN_ACTIVE,
    RosterBridge,
    check_response,
)
from worker.adapters.sleeper.ingest_injuries import build_rows as injury_rows
from worker.core.team_weeks import replace_team_weeks

NOW = datetime(2026, 10, 7, 8, 20, tzinfo=UTC)


def _depth(dt: str, team: str, gsis: str | None, name: str, abb: str, slot: int, rank: int):
    return {"dt": dt, "team": team, "player_name": name, "gsis_id": gsis,
            "pos_grp": "3WR 1TE", "pos_abb": abb, "pos_slot": slot, "pos_rank": rank}


# --- depth charts -------------------------------------------------------------

def test_only_the_newest_snapshot_is_taken():
    frame = pl.DataFrame([
        _depth("2026-10-06T06:02:22Z", "KC", "00-1", "Old QB", "QB", 9, 1),
        _depth("2026-10-06T14:08:49Z", "KC", "00-2", "New QB", "QB", 9, 1),
    ])
    snap, at = latest_snapshot(frame, NOW)
    assert snap["player_name"].to_list() == ["New QB"]
    assert at == datetime(2026, 10, 6, 14, 8, 49, tzinfo=UTC)


def test_a_stale_file_fails_rather_than_becoming_this_week():
    frame = pl.DataFrame([_depth("2026-10-03T14:08:49Z", "KC", "00-1", "QB", "QB", 9, 1)])
    with pytest.raises(NflverseError, match="older than"):
        latest_snapshot(frame, NOW)


def test_depth_rows_land_in_each_team_s_open_week_and_keep_unresolved_names():
    frame = pl.DataFrame([
        _depth("t", "KC", "00-1", "Patrick Mahomes", "QB", 9, 1),
        _depth("t", "KC", None, "Unknown Backup", "QB", 9, 2),
        _depth("t", "BUF", "00-3", "Josh Allen", "QB", 9, 1),   # no game left
        _depth("t", "XXX", "00-4", "Nobody", "QB", 9, 1),       # unknown team
    ])
    counts = NflReferenceCounts()
    rows = depth_rows(frame, NOW, 2026, {10: 6}, {"KC": 10, "BUF": 11},
                      {"00-1": 100}, counts)
    assert [(r["player_name"], r["player_id"], r["week"], r["depth"]) for r in rows] == [
        ("Patrick Mahomes", 100, 6, 1),
        ("Unknown Backup", None, 6, 2),
    ]
    assert counts.skipped == {
        "depth: player not resolved (kept by name)": 1,
        "depth: team has no game left": 1,
        "depth: team not resolved": 1,
    }


def test_a_write_into_a_week_that_is_not_open_is_refused():
    row = {"season": 2026, "week": 5, "team_id": 10}
    with pytest.raises(ValueError, match="outside the open weeks"):
        replace_team_weeks("depth_charts", 2026, {10: 6}, [row])


# --- injuries ----------------------------------------------------------------

ROSTER = pl.DataFrame({
    "full_name": ["Ja'Marr Chase", "Mike Smith", "Mike Smith", "Kenneth Walker III"],
    "team": ["CIN", "LA", "LA", "KC"],
    "gsis_id": ["00-CHASE", "00-SMITH1", "00-SMITH2", "00-WALKER"],
    "sleeper_id": [7564, None, None, None],
    "espn_id": [None, None, None, 4567048],
})


def test_the_bridge_tries_own_id_then_sleeper_then_espn_then_a_unique_name():
    bridge = RosterBridge.from_roster(ROSTER)
    assert bridge.gsis("1", {"gsis_id": " 00-OWN "}, "CIN") == "00-OWN"
    assert bridge.gsis("7564", {}, "CIN") == "00-CHASE"
    assert bridge.gsis("2", {"espn_id": 4567048}, "KC") == "00-WALKER"
    assert bridge.gsis("3", {"full_name": "Kenneth Walker"}, "KC") == "00-WALKER"
    # Two Mike Smiths on one team: neither is guessed.
    assert bridge.gsis("4", {"full_name": "Mike Smith"}, "LA") is None


def _player(team, status, **extra):
    return {"active": True, "team": team, "injury_status": status,
            "full_name": extra.pop("full_name", "Some Player"), "position": "WR", **extra}


def test_injury_rows_alias_the_rams_and_skip_what_is_not_a_designated_player():
    players = {
        "7564": _player("CIN", "Out", full_name="Ja'Marr Chase", injury_body_part="Concussion",
                        news_updated=1791335401728),
        "10": _player("LAR", "Questionable"),
        "11": _player("OAK", "IR"),                      # stale franchise
        "12": _player("KC", None),                       # healthy
        "14": _player("KC", "Out", injury_body_part="Coach's Decision"),  # a scratch
        "13": {**_player("KC", "Out"), "active": False},  # not active
        "KC": {**_player("KC", "Out"), "position": "DEF"},  # team defense
    }
    counts = NflReferenceCounts()
    rows = injury_rows(players, 2026, {20: 6, 30: 6, 40: 6}, {"CIN": 20, "LA": 30, "KC": 40},
                       {"00-CHASE": 500}, RosterBridge.from_roster(ROSTER), counts)
    got = {(r["source_player_id"], r["team_id"], r["status"], r["player_id"]) for r in rows}
    assert got == {("7564", 20, "Out", 500), ("10", 30, "Questionable", None)}
    chase = next(r for r in rows if r["source_player_id"] == "7564")
    assert chase["body_part"] == "Concussion" and chase["week"] == 6
    assert chase["source_updated_at"].year == 2026
    assert counts.skipped["injuries: team not resolved"] == 1
    assert counts.skipped["injuries: coach's decision (last game's scratch, not an injury)"] == 1


def test_a_thin_or_stale_response_is_refused_before_it_can_clear_every_report():
    fresh = int(NOW.timestamp() * 1000)
    healthy = {str(i): _player("KC", None, news_updated=fresh) for i in range(MIN_ACTIVE)}
    with pytest.raises(SleeperError, match="designated"):
        check_response(healthy, NOW)

    hurt = {str(i): _player("KC", "Out", news_updated=fresh) for i in range(MIN_ACTIVE)}
    check_response(hurt, NOW)  # passes

    old = int((NOW - timedelta(days=10)).timestamp() * 1000)
    stale = {k: {**v, "news_updated": old} for k, v in hurt.items()}
    with pytest.raises(SleeperError, match="stale"):
        check_response(stale, NOW)
