"""A scoreboard's words turned into the live model's game state (phase L2).

SPORT-AGNOSTIC CORE, pure. Both live sources describe the moment the same
way: a period, a clock as text, which side has the ball and a down-and-
distance line like "2nd & 7 at TROY 34" (CFBD's scoreboard `situation`,
ESPN's `downDistanceText`). This module reads those into a `GameState`.

ANYTHING IT CANNOT READ IS LEFT OUT, NOT GUESSED. An unreadable situation
drops the field-position term (the model still has the score and the clock);
an unreadable clock between periods counts as the end of that period, which
is what a stopped scoreboard means there. Only a missing period stops a
projection: without it the time left is unknown.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from worker.core.live_model import QUARTER_SECONDS, GameState, elapsed_seconds

_CLOCK = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.\d+)?\s*$")
_SITUATION = re.compile(
    r"^\s*(1st|2nd|3rd|4th)\s*(?:&|and)\s*(goal|inches|\d{1,2})\s+at\s+(?:(.+?)\s+)?(\d{1,2})\s*$",
    re.IGNORECASE,
)
_DOWNS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}


def clock_seconds(text: str | None) -> int | None:
    """Seconds left in the period from "8:42", "08:42" or "00:08:42"."""
    if not text:
        return None
    m = _CLOCK.match(str(text))
    if not m:
        return None
    hours, minutes, seconds = m.group(1), int(m.group(2)), int(m.group(3))
    if hours is not None:
        # "00:08:42": hours, minutes, seconds. A period is 15 minutes.
        minutes += int(hours) * 60
    total = minutes * 60 + seconds
    return total if total <= QUARTER_SECONDS else None


def elapsed_from(period: int | None, clock: str | None) -> int | None:
    """Regulation seconds elapsed; a missing clock is the end of the period."""
    if period is None or period < 1:
        return None
    left = clock_seconds(clock)
    return elapsed_seconds(period, 0 if left is None else left)


def _names(values: Iterable[str | None]) -> set[str]:
    return {v.strip().upper() for v in values if v and v.strip()}


def situation(
    text: str | None, offense_names: Iterable[str | None], defense_names: Iterable[str | None]
) -> tuple[int | None, int | None]:
    """(down, yards to goal) from "2nd & 7 at TROY 34".

    The yard line is on the side of the team named: the offense's own side is
    100 − n yards from goal, the defense's side n. Midfield needs no name. A
    team name matching neither side leaves the yards unknown.
    """
    if not text:
        return None, None
    m = _SITUATION.match(str(text))
    if not m:
        return None, None
    down = _DOWNS[m.group(1).lower()]
    side, line = (m.group(3) or "").strip().upper(), int(m.group(4))
    if line == 50:
        return down, 50
    if not 1 <= line <= 49:
        return down, None
    if side in _names(offense_names):
        return down, 100 - line
    if side in _names(defense_names):
        return down, line
    return down, None


def distance(text: str | None, yards_to_goal: int | None = None) -> int | None:
    """Yards to a first down from "2nd & 7 at TROY 34": 7. "& Goal" is the yards to goal; "& inches" is 1."""
    if not text:
        return None
    m = _SITUATION.match(str(text))
    if not m:
        return None
    word = m.group(2).lower()
    if word == "goal":
        return yards_to_goal
    if word == "inches":
        return 1
    return int(word)


def game_state(
    *,
    period: int | None,
    clock: str | None,
    home_points: int | None,
    away_points: int | None,
    possession: str | None,
    situation_text: str | None,
    home_names: Iterable[str | None],
    away_names: Iterable[str | None],
) -> GameState | None:
    """The model's state from one live_scores row, or None if the time left is unknown."""
    elapsed = elapsed_from(period, clock)
    if elapsed is None or home_points is None or away_points is None:
        return None
    side = {"home": 1, "away": -1}.get(possession or "", 0)
    down = ytg = None
    if side:
        home, away = list(home_names), list(away_names)
        offense, defense = (home, away) if side == 1 else (away, home)
        down, ytg = situation(situation_text, offense, defense)
    return GameState(
        elapsed=elapsed,
        home_points=int(home_points),
        away_points=int(away_points),
        possession=side,
        down=down,
        yards_to_goal=ytg,
    )
