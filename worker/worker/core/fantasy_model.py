"""The client's NFL fantasy game model, his Google Sheet's formula (phase F2).

SPORT-AGNOSTIC CORE, pure and database-free. A port of the sheet's `Model`
tab, cell for cell, so a test can feed it the sheet's own inputs and get the
sheet's own answer (Bills 27.46, Rams 30.72). Each team's score is

    0.15 x fantasy total            (C9:  five starters' CBS points)
  + 0.15 x Massey matchup           (C18 - D19: own offense - opponent defense)
  + 0.15 x FPI                      (C17; our SRS stands in)
  + 0.10 x adjusted red zone        (C16)
  + 0.10 x adjusted 3rd down        (C15)
  + 0.05 x plays per game / 61      (C28)
  + 0.02 x EPA per play matchup     (C26 - D27, each over a league average)
  + 0.02 x total EPA matchup        (C22 - D23, likewise)
  + home only: Massey home field + field advantage + away travel  (D10, D30, C32)

and the game is total = both scores + weather (C31), spread = their
difference rounded to 0.1 (C41), and home win % = 1 / (1 + e^(-0.23 x the
rounded home margin)) (E44).

TWO VERSIONS (`VERSIONS`). The user approved porting the sheet AS IT IS and
showing a fixed version beside it, the client to choose. `sheet` keeps four
faults; `fixed` corrects exactly those four and nothing else:

  1. The home side's "Massey Def" (D19) reads Massey's HFA column, not Def.
  2. Adjusted 3rd down (C15) is (C11 + (100 - D13)) / 2 with C11 and D13 as
     FRACTIONS (0.50), so it is ~50 for every team: a constant ~5 points a
     side. Fixed: both as percentages, which is what "100 -" implies.
  3. Adjusted red zone (C16) is a TYPED number. Its value (50.0336 in the
     sheet) is exactly the 3rd-down formula applied to the red-zone rates,
     fault 2 included, so `sheet` computes it that way; `fixed` uses
     percentages as in 2.
  4. The EPA terms subtract the opponent's EPA ALLOWED (SumerSports' defense
     table, lower = better defense), so facing a GOOD defense RAISES the
     score. Fixed: added, so a defense that allows more raises it.

KEPT IN BOTH: the league averages typed in the sheet (H1-H4), the 0.23 slope,
the weights, and home field added whatever the venue (the sheet has no
neutral sites). Weather, travel, prime time and field advantage were typed by
hand and all 0 in the sheet; they are inputs here, 0 unless given.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

VERSIONS: tuple[str, ...] = ("sheet", "fixed")

#: The sheet's typed league averages (Model!H1:H4). The EPA "blend" terms are
#: the team's EPA divided by these.
LEAGUE_OFF_EPA_PLAY = 0.012     # H1
LEAGUE_OFF_EPA_TOTAL = 2.175    # H2
LEAGUE_DEF_EPA_PLAY = 0.0109    # H3
LEAGUE_DEF_EPA_TOTAL = 2.176    # H4

#: Plays per game are divided by this (Model!C38's "C28/61").
PLAYS_BASELINE = 61.0
#: Win probability slope per point of home margin (Model!E44).
WIN_SLOPE = 0.23

W_FANTASY = 0.15
W_MASSEY = 0.15
W_FPI = 0.15
W_RED_ZONE = 0.10
W_THIRD_DOWN = 0.10
W_PACE = 0.05
W_EPA_PLAY = 0.02
W_EPA_TOTAL = 0.02


@dataclass(frozen=True)
class Side:
    """One team's inputs. Rates are fractions (0.4615), as the sheet stores them."""

    fantasy_total: float
    third_down_pct: float
    opp_third_down_pct: float
    red_zone_pct: float
    opp_red_zone_pct: float
    fpi: float
    massey_off: float
    massey_def: float
    massey_hfa: float
    off_epa_play: float
    def_epa_play: float
    off_epa_total: float
    def_epa_total: float
    plays_pg: float


@dataclass(frozen=True)
class Extras:
    """The sheet's hand-typed adjustments; all 0 in the client's example."""

    weather: float = 0.0          # C31, added to the total
    away_travel: float = 0.0      # C32, off the away fantasy total, onto the home score
    home_prime_time: float = 0.0  # D33, onto the home fantasy total
    field_advantage: float = 0.0  # D30, onto the home score


@dataclass(frozen=True)
class Projection:
    version: str
    away_score: float
    home_score: float
    total: float
    home_margin: float      # rounded to 0.1, as the sheet rounds it before the win %
    p_home_win: float
    away_terms: dict[str, float]
    home_terms: dict[str, float]


def _adjusted(own: float, opp_allowed: float, fixed: bool) -> float:
    """The sheet's "Adj" rate: own conversion and the opponent's allowed, averaged."""
    if fixed:
        return (own * 100 + (100 - opp_allowed * 100)) / 2
    return (own + (100 - opp_allowed)) / 2


def _terms(own: Side, opp: Side, fantasy_total: float, opp_def_for_massey: float,
           fixed: bool) -> dict[str, float]:
    epa_sign = 1.0 if fixed else -1.0
    epa_play = own.off_epa_play / LEAGUE_OFF_EPA_PLAY + epa_sign * (
        opp.def_epa_play / LEAGUE_DEF_EPA_PLAY
    )
    epa_total = own.off_epa_total / LEAGUE_OFF_EPA_TOTAL + epa_sign * (
        opp.def_epa_total / LEAGUE_DEF_EPA_TOTAL
    )
    return {
        "fantasy": W_FANTASY * fantasy_total,
        "massey": W_MASSEY * (own.massey_off - opp_def_for_massey),
        "fpi": W_FPI * own.fpi,
        "red_zone": W_RED_ZONE * _adjusted(own.red_zone_pct, opp.opp_red_zone_pct, fixed),
        "third_down": W_THIRD_DOWN * _adjusted(own.third_down_pct, opp.opp_third_down_pct, fixed),
        "pace": W_PACE * (own.plays_pg / PLAYS_BASELINE),
        "epa_play": W_EPA_PLAY * epa_play,
        "epa_total": W_EPA_TOTAL * epa_total,
    }


def project(away: Side, home: Side, version: str, extras: Extras | None = None) -> Projection:
    """One game's projection under one version of the formula."""
    if version not in VERSIONS:
        raise ValueError(f"unknown version {version!r}; expected one of {VERSIONS}")
    fixed = version == "fixed"
    extras = extras or Extras()

    away_fantasy = away.fantasy_total - extras.away_travel          # C9
    home_fantasy = home.fantasy_total + extras.home_prime_time      # D9

    # Fault 1: the sheet's D19 is the home team's HFA, not its defense.
    home_def_for_away = home.massey_def if fixed else home.massey_hfa
    away_terms = _terms(away, home, away_fantasy, home_def_for_away, fixed)
    home_terms = _terms(home, away, home_fantasy, away.massey_def, fixed)
    home_terms["home_field"] = home.massey_hfa + extras.field_advantage + extras.away_travel

    away_score = sum(away_terms.values())
    home_score = sum(home_terms.values())
    home_margin = round(home_score - away_score, 1)
    return Projection(
        version=version,
        away_score=away_score,
        home_score=home_score,
        total=away_score + home_score + extras.weather,
        home_margin=home_margin,
        p_home_win=1.0 / (1.0 + math.exp(-WIN_SLOPE * home_margin)),
        away_terms=away_terms,
        home_terms=home_terms,
    )


__all__ = [
    "VERSIONS",
    "Extras",
    "Projection",
    "Side",
    "project",
]
