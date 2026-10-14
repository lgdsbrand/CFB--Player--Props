"""Team numbers for the fantasy game model, point in time (migration 0093).

SPORT-AGNOSTIC CORE, pure and database-free. The client's sheet reads, per
team, season-to-date figures it pasted or scraped by hand: 3rd-down and
red-zone conversion both ways and plays per game (TeamRankings), EPA per play
and in total (SumerSports), Massey's offense/defense/home-field ratings, and
ESPN FPI. This computes all of them from our own data, as of before week N.

RAW, NOT OPPONENT-ADJUSTED. Unlike team_strength_ratings, these are the plain
season-to-date numbers the sheet's sources publish, because the sheet's
weights were set on those. Massey and SRS are the exception by nature: they
are schedule-adjusted ratings, which is what the sheet's Massey and FPI are.

WHAT A PLAY IS. `plays` here are offensive snaps only: passes (sacks and
scrambles included) and runs, no two-point tries, no penalties without a
snap, no special teams. The adapter filters; this module trusts the frame.

RATINGS ARE LEAST SQUARES WITH A LIGHT PRIOR. Massey's method fits
    points scored = offense − opponent defense (+ home field at home)
over every game; SRS fits
    home margin = rating(home) − rating(away) (+ home field)
Early in a season both systems are underdetermined (week 2 has one game a
team), so each team's ratings are pulled toward average with the weight of
`PRIOR_GAMES` games, and the home edge toward `HOME_FIELD_PRIOR` with the
weight of `HOME_FIELD_PRIOR_GAMES` games. By week 6 the pull is three eighths of a
team's evidence; by week 12, a fifth.

WHY THREE GAMES. The sheet's weights were set on ESPN FPI and Massey, whose
own priors keep their spread narrow early on. Measured 2026-10-14 (as of
week 6, 79 games) against FPI read the same day: a 1-game pull gives SRS a
standard deviation of 4.84 points against FPI's 3.73; 3 games gives 3.51,
and the correlation with FPI rises from 0.77 to 0.80. A scale match, not a fit
to results: nothing here has seen a game's outcome after the cutoff.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl

#: Weight of the pull toward an average team, in games (see WHY THREE GAMES).
PRIOR_GAMES = 3.0
#: Home field in points before the season's own games say otherwise. Massey's
#: own NFL home-field figures sat at 1.7-1.9 in the client's sheet.
HOME_FIELD_PRIOR = 1.7
#: Weight of that prior, in games: about one week of the league's games.
HOME_FIELD_PRIOR_GAMES = 16.0
#: A drive is a red-zone trip if it had a snap at or inside this yard line.
RED_ZONE_YARDS = 20
#: Weight that turns "defense ratings average zero" into a near-hard constraint.
_CONSTRAINT_WEIGHT = 1_000.0

#: The normalized play frame this module reads. One row per offensive snap.
PLAY_COLUMNS: tuple[str, ...] = (
    "game_key",          # any per-game identifier
    "week",
    "offense_id",
    "defense_id",
    "down",
    "epa",               # null where the source does not score the play
    "third_down_converted",  # bool
    "drive",             # drive number within the game
    "drive_touchdown",   # bool: the drive ended in an offensive touchdown
    "yardline_100",      # yards to the opponent's goal line
)


@dataclass(frozen=True)
class FinalScore:
    week: int
    home_id: int
    away_id: int
    home_points: int
    away_points: int
    neutral: bool


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def play_stats(plays: pl.DataFrame, as_of_week: int) -> dict[int, dict]:
    """Per team: the play-by-play figures from snaps with week < as_of_week."""
    before = plays.filter(pl.col("week") < as_of_week)
    if before.is_empty():
        return {}

    def side(team_col: str, prefix: str) -> pl.DataFrame:
        third = pl.col("down") == 3
        return before.group_by(team_col).agg(
            pl.col("game_key").n_unique().alias(f"{prefix}games"),
            pl.len().alias(f"{prefix}plays"),
            pl.col("epa").sum().alias(f"{prefix}epa_total"),
            pl.col("epa").mean().alias(f"{prefix}epa_play"),
            third.sum().alias(f"{prefix}third_att"),
            (third & pl.col("third_down_converted")).sum().alias(f"{prefix}third_conv"),
        ).rename({team_col: "team_id"})

    offense = side("offense_id", "off_")
    defense = side("defense_id", "def_")

    trips = (
        before.group_by("game_key", "drive", "offense_id", "defense_id")
        .agg(
            (pl.col("yardline_100").min() <= RED_ZONE_YARDS).alias("red_zone"),
            pl.col("drive_touchdown").any().alias("touchdown"),
        )
        .filter(pl.col("red_zone"))
    )
    rz_off = trips.group_by("offense_id").agg(
        pl.len().alias("rz_trips"), pl.col("touchdown").sum().alias("rz_tds")
    ).rename({"offense_id": "team_id"})
    rz_def = trips.group_by("defense_id").agg(
        pl.len().alias("opp_rz_trips"), pl.col("touchdown").sum().alias("opp_rz_tds")
    ).rename({"defense_id": "team_id"})

    joined = (
        offense.join(defense, on="team_id", how="full", coalesce=True)
        .join(rz_off, on="team_id", how="left")
        .join(rz_def, on="team_id", how="left")
    )

    out: dict[int, dict] = {}
    for r in joined.to_dicts():
        def rate(num: str, den: str, r: dict = r) -> float | None:
            return _rate(int(r[num] or 0), int(r[den] or 0))

        games = int(r["off_games"] or 0)
        out[int(r["team_id"])] = {
            "pbp_games_included": games,
            "third_down_pct": rate("off_third_conv", "off_third_att"),
            "opp_third_down_pct": rate("def_third_conv", "def_third_att"),
            "red_zone_pct": rate("rz_tds", "rz_trips"),
            "opp_red_zone_pct": rate("opp_rz_tds", "opp_rz_trips"),
            "plays_pg": (r["off_plays"] / games) if games else None,
            "off_epa_play": r["off_epa_play"],
            "def_epa_play": r["def_epa_play"],
            "off_epa_total": r["off_epa_total"],
            "def_epa_total": r["def_epa_total"],
        }
    return out


def _solve(rows: list[np.ndarray], rhs: list[float]) -> np.ndarray:
    solution, *_ = np.linalg.lstsq(np.vstack(rows), np.asarray(rhs), rcond=None)
    return solution


def massey(
    games: Sequence[FinalScore], teams: Sequence[int]
) -> tuple[dict[int, float], dict[int, float], float]:
    """(offense, defense, home field). points = off − opp def (+ hfa at home).

    Defense is oriented like Massey's: HIGHER is a better defense, and the
    league's defenses average zero, so an offense rating reads as points
    against an average defense.
    """
    index = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hfa = 2 * n
    width = 2 * n + 1
    rows: list[np.ndarray] = []
    rhs: list[float] = []

    def eq(off_team: int, def_team: int, home: bool, points: float) -> None:
        row = np.zeros(width)
        row[index[off_team]] = 1.0
        row[n + index[def_team]] = -1.0
        if home:
            row[hfa] = 1.0
        rows.append(row)
        rhs.append(points)

    for g in games:
        eq(g.home_id, g.away_id, not g.neutral, g.home_points)
        eq(g.away_id, g.home_id, False, g.away_points)

    league_points = float(np.mean(rhs)) if rhs else 0.0
    prior = np.sqrt(PRIOR_GAMES)
    for i in range(n):
        row = np.zeros(width)
        row[i] = prior
        rows.append(row)
        rhs.append(prior * league_points)
        row = np.zeros(width)
        row[n + i] = prior
        rows.append(row)
        rhs.append(0.0)
    row = np.zeros(width)
    row[hfa] = np.sqrt(HOME_FIELD_PRIOR_GAMES)
    rows.append(row)
    rhs.append(np.sqrt(HOME_FIELD_PRIOR_GAMES) * HOME_FIELD_PRIOR)
    row = np.zeros(width)
    row[n:2 * n] = _CONSTRAINT_WEIGHT
    rows.append(row)
    rhs.append(0.0)

    x = _solve(rows, rhs)
    offense = {t: float(x[index[t]]) for t in teams}
    defense = {t: float(x[n + index[t]]) for t in teams}
    return offense, defense, float(x[hfa])


def srs(games: Sequence[FinalScore], teams: Sequence[int]) -> tuple[dict[int, float], float]:
    """(rating, home field). home margin = r(home) − r(away) (+ hfa); ratings average zero."""
    index = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    width = n + 1
    rows: list[np.ndarray] = []
    rhs: list[float] = []
    for g in games:
        row = np.zeros(width)
        row[index[g.home_id]] = 1.0
        row[index[g.away_id]] = -1.0
        if not g.neutral:
            row[n] = 1.0
        rows.append(row)
        rhs.append(float(g.home_points - g.away_points))
    prior = np.sqrt(PRIOR_GAMES)
    for i in range(n):
        row = np.zeros(width)
        row[i] = prior
        rows.append(row)
        rhs.append(0.0)
    row = np.zeros(width)
    row[n] = np.sqrt(HOME_FIELD_PRIOR_GAMES)
    rows.append(row)
    rhs.append(np.sqrt(HOME_FIELD_PRIOR_GAMES) * HOME_FIELD_PRIOR)
    row = np.zeros(width)
    row[:n] = _CONSTRAINT_WEIGHT
    rows.append(row)
    rhs.append(0.0)
    x = _solve(rows, rhs)
    return {t: float(x[index[t]]) for t in teams}, float(x[n])


def inputs_as_of(
    games: Sequence[FinalScore],
    plays: pl.DataFrame,
    teams: Iterable[int],
    season: int,
    as_of_week: int,
) -> list[dict]:
    """Every team's fantasy_team_inputs row for one week, from data with week < as_of_week ONLY.

    The cutoff is applied to the observations before any fitting. A team with
    no completed game yet gets no row.
    """
    before = [g for g in games if g.week < as_of_week]
    if not before:
        return []
    team_list = sorted(set(teams) | {g.home_id for g in before} | {g.away_id for g in before})
    offense, defense, home_field = massey(before, team_list)
    rating, _ = srs(before, team_list)
    stats = play_stats(plays, as_of_week)

    played: dict[int, int] = {}
    for g in before:
        played[g.home_id] = played.get(g.home_id, 0) + 1
        played[g.away_id] = played.get(g.away_id, 0) + 1

    rows = []
    for team_id in team_list:
        if team_id not in played:
            continue
        figures = stats.get(team_id, {"pbp_games_included": 0})
        rows.append({
            "team_id": team_id,
            "season": season,
            "as_of_week": as_of_week,
            "games_included": played[team_id],
            "pbp_games_included": figures.get("pbp_games_included", 0),
            **{k: v for k, v in figures.items() if k != "pbp_games_included"},
            "massey_off": offense[team_id],
            "massey_def": defense[team_id],
            "home_field": home_field,
            "srs": rating[team_id],
        })
    return rows


def round_row(row: Mapping[str, object]) -> dict[str, object]:
    """Rounded to the table's column scales, so a re-run writes identical numbers."""
    scale = {
        "third_down_pct": 4, "opp_third_down_pct": 4, "red_zone_pct": 4, "opp_red_zone_pct": 4,
        "plays_pg": 2, "off_epa_play": 4, "def_epa_play": 4, "off_epa_total": 2,
        "def_epa_total": 2, "massey_off": 2, "massey_def": 2, "home_field": 2, "srs": 2,
    }
    return {
        k: (round(float(v), scale[k]) if k in scale and v is not None else v)
        for k, v in row.items()
    }


__all__ = [
    "HOME_FIELD_PRIOR",
    "PLAY_COLUMNS",
    "PRIOR_GAMES",
    "RED_ZONE_YARDS",
    "FinalScore",
    "inputs_as_of",
    "massey",
    "play_stats",
    "round_row",
    "srs",
]
