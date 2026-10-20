"""The game simulator in production (phase S2): saved engine, replays, stored summaries, live starts."""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_play_sim import synthetic_events
from worker.core import play_sim as ps
from worker.core import sim_runtime as sr
from worker.core.live_state import distance
from worker.jobs.run_game_sims import _slice, live_state


@pytest.fixture(scope="module")
def engine() -> sr.Engine:
    tables = ps.SimTables.fit(synthetic_events(), max_iter=30)
    pool = tables.whole_kick_pool()
    curve = ps.StrengthCurve((22.0, 19.5, -6.0, 3.0, 4.5, -0.5))
    return sr.Engine(tables, curve, 0.1, pool, {"seasons": [2025]})


def test_a_saved_engine_plays_the_same_games_when_loaded(engine):
    loaded = sr.Engine.from_bytes(engine.to_bytes())
    assert (
        loaded.meta == engine.meta and loaded.curve == engine.curve and loaded.sigma == engine.sigma
    )
    runs = []
    for e in (engine, loaded):
        rng = np.random.default_rng(9)
        res = ps.simulate(e.tables, sr.pregame_start(e, 24.0, 20.0, rng, n=300), rng)
        runs.append((res.home.tolist(), res.away.tolist()))
    assert runs[0] == runs[1]


def test_a_recorded_game_ends_on_its_final_score(engine):
    rng = np.random.default_rng(10)
    res = ps.simulate(
        engine.tables, sr.pregame_start(engine, 24.0, 20.0, rng, n=10), rng, record=10
    )
    assert len(res.replays) == 10
    for i, plays in enumerate(res.replays):
        assert plays, "a recorded game has plays"
        assert plays[0]["play"] == "kickoff" and plays[0]["q"] == 1
        assert (plays[-1]["home"], plays[-1]["away"]) == (res.home[i], res.away[i])
        clocks = [(p["q"], -p["clock"]) for p in plays]
        assert clocks == sorted(clocks), "time only runs forward"
        for p in plays:
            assert p["off"] in ("home", "away") and p["end_off"] in ("home", "away")
            if p.get("result") == "td" and p["play"] in ("run", "pass"):
                assert p["gain"] == p["ytg"]


def test_a_touchdown_is_followed_by_its_try(engine):
    rng = np.random.default_rng(11)
    res = ps.simulate(
        engine.tables, sr.pregame_start(engine, 30.0, 27.0, rng, n=10), rng, record=10
    )
    tds = [
        (plays[i], plays[i + 1])
        for plays in res.replays
        for i in range(len(plays) - 1)
        if plays[i].get("result") in ("td", "def_td")
        and plays[i]["play"] not in ("extra_point", "two_point")
    ]
    assert tds
    assert all(after["play"] in ("extra_point", "two_point") for _, after in tds)


def test_summaries_add_up_and_price_any_line(engine):
    rng = np.random.default_rng(12)
    res = ps.simulate(engine.tables, sr.pregame_start(engine, 24.0, 20.0, rng, n=500), rng)
    s = sr.summarize_game(res)
    q = s["quarters"]
    assert s["sims"] == 500 and len(q) == 4
    h1 = s["halves"]["first"]
    assert h1["home"] == pytest.approx(q[0]["home"] + q[1]["home"], abs=0.02)
    assert sum(s["game"]["total"]["n"]) == 500
    p = sr.p_over(s["halves"]["first"]["total"], 20.5)
    direct = ((res.quarters[:, :2, :].sum(axis=(1, 2))) > 20.5).mean()
    assert p == pytest.approx(direct)


def test_a_live_summary_counts_the_points_already_scored(engine):
    state = sr.LiveState(
        period=2,
        clock="7:30",
        home_points=10,
        away_points=7,
        possession=1,
        down=1,
        distance=10,
        yards_to_goal=75,
    )
    rng = np.random.default_rng(13)
    start = sr.live_start(engine, state, 24.0, 20.0, rng, n=400)
    res = ps.simulate(engine.tables, start, rng)
    s = sr.summarize_game(res, [7, 3], [7, 0])
    assert s["quarters"][0]["home"] == 7 and s["quarters"][0]["away"] == 7
    assert min(s["halves"]["first"]["total"]["lo"], 99) >= 17
    assert (res.home >= 10).all() and (res.away >= 7).all()


def test_live_starts_from_a_snap_or_the_next_kickoff(engine):
    rng = np.random.default_rng(14)
    snap = sr.live_start(
        engine, sr.LiveState(3, "12:00", 14, 10, -1, 3, 4, 40), 24.0, 20.0, rng, n=5
    )
    assert (snap.phase == ps.PHASE_SCRIM).all() and (snap.pos == -1).all()
    assert snap.gsr[0] == 900 + 720 and snap.togo[0] == 4 and snap.ytg[0] == 40
    half = sr.live_start(
        engine, sr.LiveState(2, "0:00", 14, 10, 0, None, None, None), 24.0, 20.0, rng, n=5
    )
    assert (half.phase == ps.PHASE_KICK).all() and half.gsr[
        0
    ] == 1800  # halftime: the second-half kickoff
    between = sr.live_start(
        engine, sr.LiveState(1, "6:00", 7, 0, 1, None, None, None), 24.0, 20.0, rng, n=5
    )
    assert (between.phase == ps.PHASE_KICK).all() and (between.kicker == -1).all()
    assert (
        sr.live_start(engine, sr.LiveState(5, "8:00", 20, 20, 1, 1, 10, 75), 24.0, 20.0, rng)
        is None
    )


@pytest.mark.parametrize(
    "text, ytg, expected",
    [
        ("2nd & 7 at KC 34", 66, 7),
        ("1st & Goal at BUF 4", 4, 4),
        ("4th & inches at DAL 45", 45, 1),
        (None, None, None),
    ],
)
def test_distance_to_a_first_down(text, ytg, expected):
    assert distance(text, ytg) == expected


def test_a_scoreboard_row_is_read_with_espn_spellings():
    row = {
        "possession": "away",
        "h_nfl": "KC",
        "a_nfl": "WAS",
        "situation": "3rd & 2 at WSH 30",
        "period": 3,
        "clock": "10:15",
        "home_points": 17,
        "away_points": 14,
    }
    st = live_state(row)
    assert (st.possession, st.down, st.distance, st.yards_to_goal) == (-1, 3, 2, 70)


def test_a_slice_keeps_its_replays(engine):
    rng = np.random.default_rng(15)
    res = ps.simulate(engine.tables, sr.pregame_start(engine, 24.0, 20.0, rng, n=6), rng, record=6)
    part = _slice(res, slice(2, 4), res.replays[2:4])
    assert len(part.home) == 2 and part.replays == res.replays[2:4]
    chosen = sr.pick_replay(part, float(part.home[1]), float(part.away[1]))
    assert (chosen[-1]["home"], chosen[-1]["away"]) == (part.home[1], part.away[1])
