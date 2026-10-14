"""Inputs of the NFL fantasy game model (migration 0093) — offline.

What can go silently wrong, each pinned with a hand-built case: CBS answering
a week it no longer serves with this week's table, a fullback taken for a
running back, a projection written into a week that is not its team's open
week, an injured starter left in the lineup, a 3rd down or red-zone trip
counted from the wrong side or the wrong week, and ratings fitted on a game
from the week they are meant to predict.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import permutations

import polars as pl
import pytest

from worker.adapters.cbs.client import CbsError
from worker.adapters.cbs.projections import (
    CbsProjection,
    build_rows,
    check_week,
    name_key,
    parse_page,
)
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.core.fantasy_inputs import FinalScore, inputs_as_of, massey, play_stats, srs
from worker.core.fantasy_lineup import DEFAULT_POINTS, DepthQB, Projected, pick_lineup

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


# --- CBS pages ----------------------------------------------------------------

def _page(week: int, rows: list[tuple[str, str]], title_week: int | None = None) -> str:
    shown = week if title_week is None else title_week
    body = "".join(
        f"<tr><td>{name}</td><td>1</td><td>{pts}</td><td>{pts}</td></tr>" for name, pts in rows
    )
    return (
        f"<html><head><title>Week {shown} Proj Fantasy Football Stats - QB Points"
        " - CBS Sports</title></head>"
        "<body><svg><title>CBS Logo</title></svg><table>"
        "<tr><th></th><th></th><th>Misc</th></tr>"
        "<tr><th>Player</th><th>gp Games Played</th><th>fpts Fantasy Points</th>"
        "<th>fppg Fantasy Points Per Game</th></tr>"
        f"{body}</table></body></html>"
    )


@pytest.fixture
def small_pages(monkeypatch):
    monkeypatch.setattr(
        "worker.adapters.cbs.projections.MIN_ROWS", {"QB": 1, "RB": 1, "WR": 1, "TE": 1}
    )


def test_a_page_showing_another_week_is_refused():
    check_week("Week 7 Proj Fantasy Football Stats - QB Points - CBS Sports", 7)
    with pytest.raises(CbsError, match="page shows week 6"):
        check_week("Week 6 Proj Fantasy Football Stats - QB Points - CBS Sports", 7)
    with pytest.raises(CbsError, match="no week"):
        check_week("Fantasy Football Stats", 7)


def test_parse_reads_team_full_name_and_points_and_drops_fullbacks(small_pages):
    html = _page(7, [
        ("J. Allen QB BUF Josh Allen QB BUF", "29.9"),
        ("T. Lawrence QB JAC Trevor Lawrence QB JAC", "18.2"),
        ("M. Stafford QB LAR Matthew Stafford QB LAR", "17.0"),
    ])
    counts = NflReferenceCounts()
    rows = parse_page(html, "QB", 7, counts)
    assert rows == [
        CbsProjection("QB", "BUF", "Josh Allen", 29.9),
        CbsProjection("QB", "JAX", "Trevor Lawrence", 18.2),   # CBS JAC -> nflverse JAX
        CbsProjection("QB", "LA", "Matthew Stafford", 17.0),   # CBS LAR -> nflverse LA
    ]

    rb = _page(7, [
        ("B. Robinson RB ATL Bijan Robinson RB ATL", "21.0"),
        ("K. Juszczyk FB SF Kyle Juszczyk FB SF", "2.1"),
    ])
    rows = parse_page(rb, "RB", 7, counts)
    assert [r.full_name for r in rows] == ["Bijan Robinson"]
    assert counts.skipped == {"cbs: FB on the RB page": 1}


def test_parse_refuses_the_substituted_week_even_with_a_full_table(small_pages):
    html = _page(10, [("J. Allen QB BUF Josh Allen QB BUF", "29.9")], title_week=6)
    with pytest.raises(CbsError, match="asked CBS for week 10"):
        parse_page(html, "QB", 10, NflReferenceCounts())


def test_a_page_that_lost_its_table_fails_rather_than_clearing_the_week():
    html = _page(7, [("J. Allen QB BUF Josh Allen QB BUF", "29.9")])
    with pytest.raises(CbsError, match="only 1 projection"):
        parse_page(html, "QB", 7, NflReferenceCounts())


def test_rows_land_only_in_their_team_s_open_week_and_keep_unresolved_names():
    pages = [
        (7, CbsProjection("WR", "BUF", "Keon Coleman", 11.0)),
        (7, CbsProjection("WR", "LA", "Puka Nacua", 25.1)),
        (8, CbsProjection("WR", "LA", "Puka Nacua", 22.0)),   # next week's page lists LA too
        (7, CbsProjection("WR", "ARI", "Marvin Harrison Jr.", 14.0)),
    ]
    team_ids = {"BUF": 1, "LA": 2, "ARI": 3}
    team_weeks = {1: 7, 2: 7, 3: 7}
    players = {(name_key("Puka Nacua"), "LA"): 200, (name_key("Marvin Harrison"), "ARI"): 300}
    counts = NflReferenceCounts()
    rows = build_rows(pages, 2026, team_weeks, team_ids, players, NOW, counts)
    got = {
        (r["team_id"], r["player_name"]): (r["week"], r["player_id"], r["fantasy_points"])
        for r in rows
    }
    assert got == {
        (1, "Keon Coleman"): (7, None, 11.0),          # kept by name
        (2, "Puka Nacua"): (7, 200, 25.1),             # week 8's row not written
        (3, "Marvin Harrison Jr."): (7, 300, 14.0),    # suffix does not block the match
    }
    assert counts.skipped == {"cbs: player not resolved (kept by name)": 1}


# --- lineups --------------------------------------------------------------------

def _status(table: dict[str, str]):
    return lambda player_id, name: table.get(name)


POOL = [
    Projected(1, "Starter QB", "QB", 20.0),
    Projected(2, "Backup QB", "QB", 6.0),
    Projected(10, "Lead Back", "RB", 15.0),
    Projected(11, "Second Back", "RB", 8.0),
    Projected(20, "Alpha WR", "WR", 18.0),
    Projected(21, "Beta WR", "WR", 12.0),
    Projected(22, "Gamma WR", "WR", 9.0),
    Projected(30, "Top TE", "TE", 10.0),
]
DEPTH = [DepthQB(1, "Starter QB", 1), DepthQB(2, "Backup QB", 2)]


def test_a_healthy_team_takes_the_top_projection_in_every_slot():
    lineup = pick_lineup(POOL, DEPTH, _status({"Beta WR": "Questionable"}), name_key)
    assert [(s.slot, s.player_name, s.fantasy_points, s.picked_by) for s in lineup] == [
        ("QB", "Starter QB", 20.0, "projection"),
        ("RB", "Lead Back", 15.0, "projection"),
        ("WR1", "Alpha WR", 18.0, "projection"),
        ("WR2", "Beta WR", 12.0, "projection"),   # Questionable plays
        ("TE", "Top TE", 10.0, "projection"),
    ]
    assert all(s.skipped is None for s in lineup)


def test_out_players_are_passed_over_and_reported():
    lineup = pick_lineup(
        POOL, DEPTH,
        _status({"Starter QB": "Out", "Alpha WR": "Doubtful", "Lead Back": "IR"}),
        name_key,
    )
    by_slot = {s.slot: s for s in lineup}
    assert (by_slot["QB"].player_name, by_slot["QB"].fantasy_points) == ("Backup QB", 6.0)
    assert by_slot["QB"].skipped == "Starter QB (Out)"
    assert by_slot["RB"].player_name == "Second Back"
    assert by_slot["RB"].skipped == "Lead Back (IR)"
    assert (by_slot["WR1"].player_name, by_slot["WR2"].player_name) == ("Beta WR", "Gamma WR")
    assert by_slot["WR1"].skipped == "Alpha WR (Doubtful)"
    assert by_slot["WR2"].skipped is None


def test_a_questionable_qb1_cbs_projects_below_his_backup_loses_the_slot():
    # 2026-10-14, Baltimore: Lamar Jackson Questionable and still QB1 on the
    # depth chart, CBS 8.9 against Tyler Huntley's 12.3.
    pool = [Projected(1, "Lamar Jackson", "QB", 8.9), Projected(2, "Tyler Huntley", "QB", 12.3)]
    depth = [DepthQB(1, "Lamar Jackson", 1), DepthQB(2, "Tyler Huntley", 2)]
    qb = pick_lineup(pool, depth, _status({"Lamar Jackson": "Questionable"}), name_key)[0]
    assert (qb.player_name, qb.fantasy_points, qb.picked_by) == (
        "Tyler Huntley", 12.3, "projection"
    )


def test_a_team_cbs_lists_no_available_qb_for_takes_the_depth_chart_s_on_default_points():
    pool = [p for p in POOL if p.position != "QB"] + [Projected(1, "Starter QB", "QB", 20.0)]
    depth = [DepthQB(1, "Starter QB", 1), DepthQB(99, "Unlisted Rookie", 2)]
    qb = pick_lineup(pool, depth, _status({"Starter QB": "Out"}), name_key)[0]
    assert (qb.player_name, qb.fantasy_points, qb.points_source, qb.picked_by) == (
        "Unlisted Rookie", DEFAULT_POINTS["QB"], "default", "depth_chart"
    )
    assert qb.skipped == "Starter QB (Out)"   # reported once, not once per source


def test_no_depth_chart_falls_back_to_projections_and_an_empty_position_to_none():
    pool = [p for p in POOL if p.position != "TE"]
    lineup = pick_lineup(pool, [], _status({"Starter QB": "Out"}), name_key)
    qb, te = lineup[0], lineup[-1]
    assert (qb.player_name, qb.picked_by, qb.skipped) == (
        "Backup QB", "projection", "Starter QB (Out)"
    )
    assert (te.player_name, te.picked_by, te.fantasy_points) == (None, "none", DEFAULT_POINTS["TE"])


def test_no_qb_anywhere_falls_to_none_on_default_points():
    pool = [p for p in POOL if p.position != "QB"]
    qb = pick_lineup(pool, [], _status({}), name_key)[0]
    assert (qb.player_name, qb.picked_by, qb.fantasy_points) == (None, "none", DEFAULT_POINTS["QB"])


# --- team inputs ------------------------------------------------------------------

def _snap(game, week, off, de, down=1, epa=0.1, conv=False, drive=1, td=False, yl=50):
    return {"game_key": game, "week": week, "offense_id": off, "defense_id": de, "down": down,
            "epa": epa, "third_down_converted": conv, "drive": drive, "drive_touchdown": td,
            "yardline_100": yl}


def test_play_stats_count_each_side_and_respect_the_cutoff():
    plays = pl.DataFrame([
        # week 1: team 1 on offense, two drives, one red-zone trip that scores
        _snap("g1", 1, 1, 2, down=3, conv=True, drive=1, yl=45, epa=0.5),
        _snap("g1", 1, 1, 2, down=1, drive=1, yl=15, td=True, epa=1.0),
        _snap("g1", 1, 1, 2, down=3, conv=False, drive=2, yl=60, epa=-0.5),
        # week 1: team 2 on offense, a red-zone trip that does not score
        _snap("g1", 1, 2, 1, down=3, conv=False, drive=3, yl=18, epa=-1.0),
        # week 2: must not count as of week 2
        _snap("g2", 2, 1, 3, down=3, conv=True, drive=1, yl=5, td=True, epa=2.0),
    ])
    stats = play_stats(plays, as_of_week=2)
    one, two = stats[1], stats[2]
    assert one["third_down_pct"] == pytest.approx(0.5)
    assert one["opp_third_down_pct"] == pytest.approx(0.0)
    assert one["red_zone_pct"] == 1.0
    assert one["opp_red_zone_pct"] == 0.0
    assert one["plays_pg"] == 3.0
    assert one["off_epa_total"] == pytest.approx(1.0)
    assert one["def_epa_total"] == pytest.approx(-1.0)   # EPA ALLOWED
    assert two["red_zone_pct"] == 0.0
    assert two["opp_third_down_pct"] == pytest.approx(0.5)
    assert 3 not in stats   # its only game is week 2


def _games_from(
    off: dict[int, float], de: dict[int, float], hfa: float, rounds: int
) -> list[FinalScore]:
    games = []
    for _ in range(rounds):
        for home, away in permutations(off, 2):
            games.append(FinalScore(
                week=1, home_id=home, away_id=away,
                home_points=round(off[home] - de[away] + hfa),
                away_points=round(off[away] - de[home]),
                neutral=False,
            ))
    return games


def test_massey_recovers_offense_defense_and_home_field():
    off = {1: 30.0, 2: 24.0, 3: 20.0, 4: 18.0}
    de = {1: 3.0, 2: 0.0, 3: -1.0, 4: -2.0}   # higher is better; averages zero
    games = _games_from(off, de, 2.0, rounds=40)
    got_off, got_def, hfa = massey(games, sorted(off))
    for t in off:
        assert got_off[t] == pytest.approx(off[t], abs=0.15)
        assert got_def[t] == pytest.approx(de[t], abs=0.15)
    assert hfa == pytest.approx(2.0, abs=0.15)
    assert sum(got_def.values()) == pytest.approx(0.0, abs=1e-3)


def test_srs_recovers_margin_ratings_that_average_zero():
    off = {1: 30.0, 2: 24.0, 3: 20.0, 4: 18.0}
    de = {1: 3.0, 2: 0.0, 3: -1.0, 4: -2.0}
    games = _games_from(off, de, 2.0, rounds=40)
    rating, hfa = srs(games, sorted(off))
    # margin rating = (off - avg off) + def, here 11.0 / 2.0 / -2.0 / -5.0 ... centred
    expected = {t: off[t] + de[t] for t in off}
    centre = sum(expected.values()) / 4
    for t in off:
        assert rating[t] == pytest.approx(expected[t] - centre, abs=0.15)
    assert hfa == pytest.approx(2.0, abs=0.15)


def test_early_season_ratings_are_pulled_toward_average():
    one_game = [FinalScore(1, 1, 2, 40, 0, neutral=True)]
    rating, _ = srs(one_game, [1, 2])
    # A 40-point win is not a 20-point-per-side team after one game.
    assert 0 < rating[1] < 20


def test_inputs_use_only_games_before_the_week_and_skip_teams_yet_to_play():
    games = [
        FinalScore(1, 1, 2, 24, 17, neutral=False),
        FinalScore(2, 3, 1, 10, 31, neutral=False),
    ]
    plays = pl.DataFrame([_snap("g1", 1, 1, 2), _snap("g2", 2, 1, 3)])
    week2 = {r["team_id"]: r for r in inputs_as_of(games, plays, [1, 2, 3, 4], 2026, 2)}
    assert set(week2) == {1, 2}
    assert week2[1]["games_included"] == 1 and week2[1]["pbp_games_included"] == 1
    week3 = {r["team_id"]: r for r in inputs_as_of(games, plays, [1, 2, 3, 4], 2026, 3)}
    assert set(week3) == {1, 2, 3}
    assert week3[1]["games_included"] == 2
    assert inputs_as_of(games, plays, [1, 2], 2026, 1) == []
