"""NFL live scores: ESPN's public scoreboard -> `live_scores` (live model, phase L2).

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3), the NFL's counterpart of
adapters/cfbd/live_scores.py and written to the same table, so the site and
the live model read both sports one way.

WHY ESPN. The user approved it on 2026-10-19 over the Odds API's scores feed
(about 750 credits a week at a two-minute poll). It is free and keyless, and
one call returns the whole week: status, period, clock, possession, down and
distance, line scores. It is also UNOFFICIAL: no contract and no notice of
change. So every field is read defensively, a failed call is logged and
skipped (the next poll tries again), and nothing here is load-bearing beyond
live scores and the live model. The client's own NFL backend already depends
on the same endpoint.

Called only while one of OUR NFL games is in its window (5 minutes before
kickoff to 5 hours after, not yet final), exactly like the college poll, so a
Tuesday costs nothing. HOME AND AWAY ARE OURS: an event is matched on its two
teams, and if ESPN has them the other way round the sides are swapped.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from worker.adapters.nflverse.mapping import canonical_team
from worker.db import fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

TABLE = "live_scores"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
WINDOW_BEFORE = timedelta(minutes=5)
WINDOW_AFTER = timedelta(hours=5)
TIMEOUT_SECONDS = 20
STATES = {"pre": "scheduled", "in": "in_progress", "post": "completed"}


def games_in_window(now: datetime) -> dict[tuple[str, str], dict[str, Any]]:
    """(home nfl_abbr, away nfl_abbr) -> our game, for NFL games that may be live now."""
    rows = fetch_all(
        """
        select g.id, h.nfl_abbr as home_abbr, a.nfl_abbr as away_abbr
          from games g
          join teams h on h.id = g.home_team_id
          join teams a on a.id = g.away_team_id
          left join live_scores l on l.game_id = g.id
         where g.sport = 'nfl'
           and not g.completed
           and g.start_date between %(earliest)s and %(latest)s
           and (l.status is null or l.status <> 'completed')
        """,
        {"earliest": now - WINDOW_AFTER, "latest": now + WINDOW_BEFORE},
    )
    return {(r["home_abbr"], r["away_abbr"]): r for r in rows}


def fetch_scoreboard() -> dict[str, Any]:
    request = urllib.request.Request(
        SCOREBOARD_URL,
        headers={"User-Agent": "legends-football-live/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _int(value: Any) -> int | None:
    try:
        return None if value is None or value == "" else int(float(value))
    except (TypeError, ValueError):
        return None


def score_row(
    event: dict[str, Any], window: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any] | None:
    """One live_scores row for an ESPN event that is one of our games, else None."""
    competition = (event.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): c for c in competition.get("competitors") or []}
    home, away = sides.get("home"), sides.get("away")
    if not home or not away:
        return None
    home_abbr = canonical_team((home.get("team") or {}).get("abbreviation"))
    away_abbr = canonical_team((away.get("team") or {}).get("abbreviation"))
    game = window.get((home_abbr, away_abbr))
    if game is None and (away_abbr, home_abbr) in window:
        game = window[(away_abbr, home_abbr)]
        home, away = away, home
    if game is None:
        return None

    status = competition.get("status") or event.get("status") or {}
    state = STATES.get(((status.get("type") or {}).get("state") or "").lower())
    if state is None:
        return None
    situation = competition.get("situation") or {}
    owner = str(situation.get("possession") or "")
    possession = (
        "home"
        if owner and owner == str((home.get("team") or {}).get("id"))
        else "away"
        if owner and owner == str((away.get("team") or {}).get("id"))
        else None
    )

    def lines(side: dict[str, Any]) -> list[int] | None:
        values = [_int(s.get("value")) for s in side.get("linescores") or []]
        return [v for v in values if v is not None] or None

    live = state == "in_progress"
    return {
        "game_id": int(game["id"]),
        "status": state,
        "period": _int(status.get("period")) if state != "scheduled" else None,
        "clock": (status.get("displayClock") or None) if live else None,
        "home_points": _int(home.get("score")),
        "away_points": _int(away.get("score")),
        "home_line_scores": lines(home),
        "away_line_scores": lines(away),
        "possession": possession if live else None,
        "situation": (situation.get("downDistanceText") or None) if live else None,
        "last_play": ((situation.get("lastPlay") or {}).get("text") or "").strip() or None,
    }


def poll(now: datetime, *, dry_run: bool = False) -> int | None:
    """Rows written, or None when no NFL game is in its window (and no call was made)."""
    window = games_in_window(now)
    if not window:
        return None
    try:
        payload = fetch_scoreboard()
    except Exception as exc:  # unofficial endpoint: skip this poll, try the next
        log.warning("ESPN scoreboard unavailable: %s", exc)
        return 0
    events = payload.get("events") or []
    rows = []
    for event in events:
        row = score_row(event, window)
        if row is not None:
            row["updated_at"] = now
            rows.append(row)
    log.info(
        "NFL live scores: %d game(s) in window, %d on ESPN's scoreboard, %d matched",
        len(window),
        len(events),
        len(rows),
    )
    if dry_run or not rows:
        return 0
    return upsert(TABLE, rows, conflict_columns=["game_id"])
