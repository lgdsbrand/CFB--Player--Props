"""College live scores: CFBD's scoreboard -> `live_scores` (migration 0088).

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). One `/scoreboard` call returns the
whole FBS week: status, period, clock, possession, situation, last play and
line scores per game. The poller runs every two minutes and calls it only
while one of OUR college games is in its window (`WINDOW_BEFORE` before
kickoff to `WINDOW_AFTER` after, not yet final), so a quiet Monday costs no
calls at all.

HOME AND AWAY ARE OURS. A scoreboard entry is matched to a game on
`games.cfbd_id` and its teams are checked against that game's: if CFBD has
them the other way round, scores and possession are swapped rather than
written backwards.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import cfbd

from worker.adapters.cfbd.client import CfbdClient
from worker.db import fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

TABLE = "live_scores"
WINDOW_BEFORE = timedelta(minutes=5)
WINDOW_AFTER = timedelta(hours=5)
STATUSES = {"scheduled", "in_progress", "completed"}


def games_in_window(now: datetime) -> dict[int, dict[str, Any]]:
    """cfbd_id -> our game, for college games that may be live right now.

    A game already reported final by the poller drops out, so the last game
    of the night stops the calls as soon as it ends rather than five hours
    after kickoff.
    """
    rows = fetch_all(
        """
        select g.id, g.cfbd_id, h.cfbd_id as home_cfbd, a.cfbd_id as away_cfbd
          from games g
          join teams h on h.id = g.home_team_id
          join teams a on a.id = g.away_team_id
          left join live_scores l on l.game_id = g.id
         where g.sport = 'cfb'
           and not g.completed
           and g.cfbd_id is not null
           and g.start_date between %(earliest)s and %(latest)s
           and (l.status is null or l.status <> 'completed')
        """,
        {"earliest": now - WINDOW_AFTER, "latest": now + WINDOW_BEFORE},
    )
    return {int(r["cfbd_id"]): r for r in rows}


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _possession(raw: Any, home: dict[str, Any], away: dict[str, Any]) -> str | None:
    """'home' or 'away' from CFBD's free-text possession, else None."""
    if not raw:
        return None
    text = str(raw).strip().lower()
    if text in ("home", "away"):
        return text
    for side, team in (("home", home), ("away", away)):
        names = {str(team.get(k) or "").strip().lower() for k in ("name", "id")}
        if text in names - {""}:
            return side
    return None


def score_row(entry: dict[str, Any], game: dict[str, Any]) -> dict[str, Any] | None:
    """One live_scores row for a scoreboard entry, oriented to our home team."""
    raw = entry.get("status")
    # The vendor returns an enum, and str() of it is "GameStatus.COMPLETED".
    status = str(getattr(raw, "value", raw) or "").lower()
    if status not in STATUSES:
        return None
    home, away = entry.get("homeTeam") or {}, entry.get("awayTeam") or {}
    if _int(home.get("id")) == _int(game["away_cfbd"]) and _int(away.get("id")) == _int(
        game["home_cfbd"]
    ):
        home, away = away, home
    possession = _possession(entry.get("possession"), home, away)

    def lines(team: dict[str, Any]) -> list[int] | None:
        scores = team.get("lineScores")
        return [int(s) for s in scores if s is not None] if scores else None

    return {
        "game_id": int(game["id"]),
        "status": status,
        "period": _int(entry.get("period")),
        "clock": entry.get("clock") or None,
        "home_points": _int(home.get("points")),
        "away_points": _int(away.get("points")),
        "home_line_scores": lines(home),
        "away_line_scores": lines(away),
        "possession": possession,
        "situation": entry.get("situation") or None,
        "last_play": entry.get("lastPlay") or None,
    }


def poll(client: CfbdClient, now: datetime, *, dry_run: bool = False) -> int | None:
    """Rows written, or None when no game is in its window (and no call was made)."""
    window = games_in_window(now)
    if not window:
        return None
    result = client.call_uncached(
        "/scoreboard", cfbd.GamesApi, "get_scoreboard",
        classification=cfbd.DivisionClassification("fbs"),
    )
    entries = [r.to_dict() for r in result or []]
    rows = []
    for entry in entries:
        game = window.get(_int(entry.get("id")) or -1)
        if game is None:
            continue
        row = score_row(entry, game)
        if row is not None:
            row["updated_at"] = now
            rows.append(row)
    log.info(
        "live scores: %d game(s) in window, %d on the scoreboard, %d matched (%s)",
        len(window), len(entries), len(rows),
        ", ".join(f"{s} {sum(r['status'] == s for r in rows)}" for s in sorted(STATUSES)),
    )
    if dry_run or not rows:
        return 0
    return upsert(TABLE, rows, conflict_columns=["game_id"])
