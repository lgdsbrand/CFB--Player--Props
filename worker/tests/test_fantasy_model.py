"""The client's fantasy game model formula (phase F2) — offline.

The port is pinned to the client's own sheet: fed the inputs on its Model tab
(Bills at Rams, CBS week 6 projections), it must return the sheet's own
numbers to the sheet's own precision. Each of the four fixes is then pinned on
its own, so a later edit cannot quietly move one version toward the other.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from worker.core.fantasy_model import Extras, Side, project

# Model!C (away) and Model!D (home), exactly as the sheet holds them on 2026-10-14.
BILLS = Side(
    fantasy_total=76.5,          # C4:C8 summed
    third_down_pct=0.5,          # C11
    opp_third_down_pct=0.4265,   # C13
    red_zone_pct=0.7143,         # C12
    opp_red_zone_pct=0.7619,     # C14
    fpi=4.8,                     # C17
    massey_off=29.26,            # C18
    massey_def=0.34,             # C19
    massey_hfa=1.89,             # mass!O, Bills (unused away)
    off_epa_play=0.17,           # C24
    def_epa_play=0.11,           # C25
    off_epa_total=48.73,         # C20
    def_epa_total=39.71,         # C21
    plays_pg=59.0,               # C28
)
RAMS = Side(
    fantasy_total=100.5,         # D4:D8 summed
    third_down_pct=0.4462,       # D11
    opp_third_down_pct=0.3276,   # D13
    red_zone_pct=0.65,           # D12
    opp_red_zone_pct=0.6471,     # D14
    fpi=4.5,                     # D17
    massey_off=23.72,            # D18
    massey_def=3.48,             # mass!N, Rams (the sheet's D19 reads O instead)
    massey_hfa=1.78,             # D10 = mass!O, Rams
    off_epa_play=0.04,           # D24
    def_epa_play=-0.08,          # D25
    off_epa_total=13.89,         # D20
    def_epa_total=-22.41,        # D21
    plays_pg=70.8,               # D28
)


def test_the_sheet_version_reproduces_the_client_s_sheet():
    p = project(BILLS, RAMS, "sheet")
    assert p.away_score == pytest.approx(27.4615292, abs=1e-6)    # J31 / C38
    assert p.home_score == pytest.approx(30.71799711, abs=1e-6)   # M31 / D38
    assert p.total == pytest.approx(58.17952631, abs=1e-6)        # K31 / C39
    assert p.home_margin == 3.3                                   # D41
    assert p.p_home_win == pytest.approx(0.6811365836, abs=1e-9)  # E44 / M34


def test_the_sheet_s_typed_red_zone_is_its_own_formula():
    # Model!C16 and D16 are typed values; they equal the 3rd-down formula on
    # the red-zone rates, which is how the port computes them.
    p = project(BILLS, RAMS, "sheet")
    assert p.away_terms["red_zone"] == pytest.approx(0.1 * 50.0336, abs=1e-4)
    assert p.home_terms["red_zone"] == pytest.approx(0.1 * 49.94405, abs=1e-5)


def test_fix_1_the_away_massey_matchup_reads_the_home_defense():
    sheet = project(BILLS, RAMS, "sheet")
    fixed = project(BILLS, RAMS, "fixed")
    assert sheet.away_terms["massey"] == pytest.approx(0.15 * (29.26 - 1.78))
    assert fixed.away_terms["massey"] == pytest.approx(0.15 * (29.26 - 3.48))
    # The home side was always right.
    assert sheet.home_terms["massey"] == fixed.home_terms["massey"]


def test_fix_2_and_3_conversion_rates_vary_by_team_instead_of_sitting_at_50():
    sheet = project(BILLS, RAMS, "sheet")
    fixed = project(BILLS, RAMS, "fixed")
    # Sheet: ~50 for anyone, whatever the rates.
    assert sheet.away_terms["third_down"] == pytest.approx(0.1 * (0.5 + 100 - 0.3276) / 2)
    # Fixed: Bills convert 50%, the Rams allow 32.76%.
    assert fixed.away_terms["third_down"] == pytest.approx(0.1 * (50 + 100 - 32.76) / 2)
    better = replace(BILLS, third_down_pct=0.6)
    assert project(better, RAMS, "fixed").away_terms["third_down"] > fixed.away_terms["third_down"]
    assert project(better, RAMS, "sheet").away_terms["third_down"] == pytest.approx(
        sheet.away_terms["third_down"], abs=0.02
    )


def test_fix_4_a_defense_that_allows_more_raises_the_opponent_s_score():
    leaky = replace(RAMS, def_epa_play=0.20, def_epa_total=60.0)
    assert project(BILLS, leaky, "fixed").away_score > project(BILLS, RAMS, "fixed").away_score
    # The sheet does the opposite.
    assert project(BILLS, leaky, "sheet").away_score < project(BILLS, RAMS, "sheet").away_score


def test_the_fixed_version_changes_only_the_four_faults():
    sheet = project(BILLS, RAMS, "sheet")
    fixed = project(BILLS, RAMS, "fixed")
    for side in ("away_terms", "home_terms"):
        a, b = getattr(sheet, side), getattr(fixed, side)
        for term in ("fantasy", "fpi", "pace"):
            assert a[term] == b[term]
    assert sheet.home_terms["home_field"] == fixed.home_terms["home_field"]


def test_hand_typed_extras_land_where_the_sheet_puts_them():
    base = project(BILLS, RAMS, "sheet")
    p = project(BILLS, RAMS, "sheet", Extras(weather=-3.0, away_travel=2.0,
                                             home_prime_time=1.0, field_advantage=0.5))
    assert p.away_terms["fantasy"] == pytest.approx(0.15 * 74.5)
    assert p.home_terms["fantasy"] == pytest.approx(0.15 * 101.5)
    assert p.home_terms["home_field"] == pytest.approx(1.78 + 0.5 + 2.0)
    assert p.total == pytest.approx(p.away_score + p.home_score - 3.0)
    assert p.total != base.total


def _inputs_row(team: int, week: int, side: Side) -> dict:
    return {
        "team_id": team, "season": 2026, "as_of_week": week,
        "third_down_pct": side.third_down_pct, "opp_third_down_pct": side.opp_third_down_pct,
        "red_zone_pct": side.red_zone_pct, "opp_red_zone_pct": side.opp_red_zone_pct,
        "srs": side.fpi, "massey_off": side.massey_off, "massey_def": side.massey_def,
        "home_field": side.massey_hfa, "off_epa_play": side.off_epa_play,
        "def_epa_play": side.def_epa_play, "off_epa_total": side.off_epa_total,
        "def_epa_total": side.def_epa_total, "plays_pg": side.plays_pg,
    }


def test_a_run_writes_both_versions_and_skips_a_game_it_cannot_fill():
    from worker.adapters.nflverse.fantasy_model import game_projection_rows
    from worker.adapters.nflverse.ingest_reference import NflReferenceCounts

    games = [
        {"id": 10, "season": 2026, "week": 6, "away_team_id": 1, "home_team_id": 2},
        {"id": 11, "season": 2026, "week": 6, "away_team_id": 3, "home_team_id": 4},
    ]
    inputs = {(1, 6): _inputs_row(1, 6, BILLS), (2, 6): _inputs_row(2, 6, RAMS),
              (3, 6): {**_inputs_row(3, 6, BILLS), "red_zone_pct": None},
              (4, 6): _inputs_row(4, 6, RAMS)}
    fantasy = {(1, 6): 76.5, (2, 6): 100.5, (3, 6): 70.0, (4, 6): 70.0}
    counts = NflReferenceCounts()
    rows = game_projection_rows(games, inputs, fantasy, counts)

    assert [(r["game_id"], r["model_version"]) for r in rows] == [(10, "sheet"), (10, "fixed")]
    sheet = rows[0]
    assert (sheet["away_score"], sheet["home_score"], sheet["home_margin"]) == (27.46, 30.72, 3.3)
    assert sheet["p_home_win"] == pytest.approx(0.68114, abs=1e-5)
    assert counts.skipped == {"model: a team figure is missing": 1}


def test_an_unknown_version_is_refused():
    with pytest.raises(ValueError, match="unknown version"):
        project(BILLS, RAMS, "v3")
