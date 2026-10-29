"""Power ratings and game score, point in time (client, 2026-10-29).

SPORT-AGNOSTIC CORE, NOT SPORT-BLIND. Reads `games` and `team_seasons`, every
read scoped by `games.sport`; the per-sport numbers live in POWER_SETTINGS.

POWER RATING: points per game better than an average team on a neutral field.
A simple rating system (each team's average margin plus the average rating of
the teams it played, solved by iteration and centred on zero) with home field
taken out of every margin and each margin capped at MARGIN_CAP, so running up
the score on a weak opponent buys nothing past four touchdowns. The rating
for week N uses ONLY games before week N (CLAUDE.md §4): this season's games
weigh 1, last season's together count as `last_season_games` games per team,
so week 1 is last season's order and it fades as the season fills in. The
NBA and NHL pages use the same formula.

GAME SCORE, 0-100: GAME_SCORE_STRENGTH from how good both teams are (their
power ranks) and the rest from how close the game is, the home side's chance
from the two ratings and home field, Φ(margin / margin_sd). Display only:
nothing here feeds the game model, the props model or a pick.

Ranks are among the sport's ranked league (FBS in college); a team outside it
(FCS) is rated, so the games against it count, but has no rank and is scored
as the league's last.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from worker.db import execute, fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

MARGIN_CAP = 28
GAME_SCORE_STRENGTH = 0.6


@dataclass(frozen=True)
class PowerSettings:
    #: Last season's games together count as this many games per team.
    last_season_games: float
    #: Spread of the final margin around the ratings' projected margin
    #: (measured on the season before last; see POWER_SETTINGS).
    margin_sd: float


# margin_sd measured 2026-10-29: the standard deviation of (actual margin -
# projected margin) over the 2024 and 2025 regular seasons from week 4 on,
# projected point in time with these settings: college 16.2 and 15.9 (FBS
# games with ratings on both sides, about 635 a season), NFL 12.9 and 13.2.
# The same check: the ratings picked the winner 70-72% (college) and 60-71%
# (NFL), with the mean miss within a point of zero.
POWER_SETTINGS: dict[str, PowerSettings] = {
    "cfb": PowerSettings(last_season_games=3, margin_sd=16.0),
    "nfl": PowerSettings(last_season_games=3, margin_sd=13.0),
}


@dataclass(frozen=True)
class Final:
    season: int
    week: int
    home_id: int
    away_id: int
    margin: float  # home points - away points
    neutral: bool


def _capped(margin: float) -> float:
    return max(-MARGIN_CAP, min(MARGIN_CAP, margin))


def home_field(finals: list[Final], ranked: set[int]) -> float:
    """The average capped home margin over games at a home field between two
    ranked teams. FBS teams host FCS teams and beat them by thirty, which
    would read as home field (it put college projections 3-4 points too
    high on the home side in 2024 and 2025)."""
    margins = [_capped(f.margin) for f in finals
               if not f.neutral and f.home_id in ranked and f.away_id in ranked]
    return sum(margins) / len(margins) if margins else 0.0


def ratings(this_season: list[Final], last_season: list[Final],
            last_season_games: float, ranked: set[int]) -> tuple[dict[int, float], float]:
    """(rating per team, home field) from the games given, which the caller has
    already cut to those before the week being rated."""
    used = this_season + last_season
    if not used:
        return {}, 0.0
    hfa = home_field(used, ranked)
    last_count: dict[int, int] = defaultdict(int)
    for f in last_season:
        last_count[f.home_id] += 1
        last_count[f.away_id] += 1
    games: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for f, is_last in [(f, False) for f in this_season] + [(f, True) for f in last_season]:
        margin = _capped(f.margin) - (0.0 if f.neutral else hfa)
        for team, opp, m in ((f.home_id, f.away_id, margin), (f.away_id, f.home_id, -margin)):
            w = last_season_games / last_count[team] if is_last else 1.0
            games[team].append((opp, m, w))
    # Each step is averaged with the last: the plain iteration can swing back
    # and forth for ever (two teams that only meet each other); the damped one
    # settles on the same answer.
    rating = {team: 0.0 for team in games}
    for _ in range(500):
        nxt = {
            team: sum(w * (m + rating[o]) for o, m, w in played) / sum(w for _, _, w in played)
            for team, played in games.items()
        }
        mean = sum(nxt.values()) / len(nxt)
        rating = {team: (rating[team] + value - mean) / 2 for team, value in nxt.items()}
    return rating, hfa


def ranks(rating: dict[int, float], ranked: set[int]) -> dict[int, int]:
    order = sorted((t for t in rating if t in ranked), key=lambda t: -rating[t])
    return {team: i + 1 for i, team in enumerate(order)}


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def game_score(home_rank: int, away_rank: int, teams: int, p_home_win: float) -> int:
    """0-100: two highly ranked teams in a close game score highest."""
    strength = 100 * (1 - ((home_rank + away_rank) / 2 - 1) / max(teams - 1, 1))
    closeness = 100 * (1 - abs(2 * p_home_win - 1))
    return round(GAME_SCORE_STRENGTH * strength + (1 - GAME_SCORE_STRENGTH) * closeness)


# ── Reads and writes ──────────────────────────────────────────────────────────

def load_games(seasons: list[int], sport: str) -> list[dict]:
    return fetch_all(
        """
        select id, season, week, season_type::text as season_type, neutral_site,
               home_team_id, away_team_id, home_points, away_points, completed
          from games
         where sport = %s and season = any(%s)
        """,
        (sport, seasons),
    )


def ranked_teams(season: int, sport: str) -> set[int]:
    """The sport's ranked league that season: FBS in college, everyone in the NFL."""
    return {
        r["team_id"]
        for r in fetch_all(
            """
            select ts.team_id
              from team_seasons ts
              join teams t on t.id = ts.team_id
             where ts.season = %s and t.sport = %s
               and (t.sport <> 'cfb' or ts.classification = 'fbs')
            """,
            (season, sport),
        )
    }


def _final(g: dict) -> Final | None:
    if not g["completed"] or g["home_points"] is None or g["away_points"] is None:
        return None
    return Final(int(g["season"]), int(g["week"]), int(g["home_team_id"]),
                 int(g["away_team_id"]), float(g["home_points"] - g["away_points"]),
                 bool(g["neutral_site"]))


def season_rows(season: int, games: list[dict], ranked: set[int],
                settings: PowerSettings) -> tuple[list[dict], list[dict]]:
    """(team_power_ratings rows, game_power rows) for one season.

    A regular-season week N is rated on this season's regular-season games
    before week N; the postseason on the whole regular season.
    """
    last = [f for g in games if g["season"] == season - 1 and (f := _final(g))]
    this_regular = [f for g in games if g["season"] == season
                    and g["season_type"] == "regular" and (f := _final(g))]
    this_games = [g for g in games if g["season"] == season]
    weeks = sorted({int(g["week"]) for g in this_games if g["season_type"] == "regular"})
    if not weeks:
        return [], []
    postseason_week = max(weeks) + 1

    team_rows: list[dict] = []
    game_rows: list[dict] = []
    for as_of in weeks + [postseason_week]:
        before = [f for f in this_regular if f.week < as_of]
        rating, hfa = ratings(before, last, settings.last_season_games, ranked)
        if not rating:
            continue
        rank = ranks(rating, ranked)
        played: dict[int, int] = defaultdict(int)
        for f in before:
            played[f.home_id] += 1
            played[f.away_id] += 1
        team_rows.extend(
            {"team_id": t, "season": season, "as_of_week": as_of,
             "rating": round(r, 2), "power_rank": rank.get(t), "games_included": played[t]}
            for t, r in rating.items()
        )
        for g in this_games:
            postseason = g["season_type"] != "regular"
            if (postseason and as_of != postseason_week) or (not postseason and g["week"] != as_of):
                continue
            home, away = int(g["home_team_id"]), int(g["away_team_id"])
            margin = rating.get(home, 0.0) - rating.get(away, 0.0)
            if not g["neutral_site"]:
                margin += hfa
            p_home = normal_cdf(margin / settings.margin_sd)
            last_rank = len(rank)
            game_rows.append({
                "game_id": int(g["id"]),
                "as_of_week": as_of,
                "home_rank": rank.get(home),
                "away_rank": rank.get(away),
                "home_rating": round(rating[home], 2) if home in rating else None,
                "away_rating": round(rating[away], 2) if away in rating else None,
                "p_home_win": round(min(max(p_home, 1e-5), 1 - 1e-5), 5),
                "game_score": game_score(rank.get(home, last_rank), rank.get(away, last_rank),
                                         last_rank, p_home),
            })
    return team_rows, game_rows


def build_season(season: int, sport: str) -> int:
    """Rebuild one season's power ratings and game scores. Returns rows written."""
    settings = POWER_SETTINGS[sport]
    games = load_games([season - 1, season], sport)
    team_rows, game_rows = season_rows(season, games, ranked_teams(season, sport), settings)
    if not team_rows:
        log.warning("power %s %d: no games to rate", sport, season)
        return 0
    execute(
        """
        delete from team_power_ratings r using teams t
         where t.id = r.team_id and t.sport = %s and r.season = %s
        """,
        (sport, season),
    )
    execute(
        """
        delete from game_power p using games g
         where g.id = p.game_id and g.sport = %s and g.season = %s
        """,
        (sport, season),
    )
    n = upsert("team_power_ratings", team_rows,
               conflict_columns=["team_id", "season", "as_of_week"])
    n += upsert("game_power", game_rows, conflict_columns=["game_id"])
    log.info("power %s %d: %d team-weeks, %d games", sport, season, len(team_rows), len(game_rows))
    return n
