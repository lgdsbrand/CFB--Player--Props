"""College live player stats: CFBD /live/plays -> `live_player_stats` (migration 0092).

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). For a saved slip's player props while
the game is on (client, 2026-10-12). UNOFFICIAL: nothing is graded from these
rows; a prop settles only from `player_game_stats`, the box score.

THE FEED HAS NO PLAYER IDS AND NO BOX SCORE. One call returns the whole game
as drives of plays, and a play's only account of who did what is its text.
Three formats were seen in one week (2026 week 6):

    (14:55) Shotgun #8 C.Boley pass complete short right to #3 R.Harris
        caught at ASU35, for 47 yards to the ASU28 (#1 A.Stamps), 1ST DOWN
    Bryson Rodgers 42 Yd pass from Michael Van Buren Jr. (Nico Gramatica Kick)
    T. Owens pass to H. Summers for 20 yds, for a TD (C. Van Andel KICK)

So the stats are added up from the text, and these are the traps measured
against the box score on 13 games:

  * PLAY TYPES LIE. A touchdown pass can be typed "Pass Reception"; a run
    that stood with a penalty after it is typed "Penalty"; a catch and fumble
    is "Fumble Recovery (Opponent)". The TEXT decides what a play was.
  * `yardsGained` IS WRONG ON SACKS ("sacked for loss of 6 yards" carried
    -3). Yards come from the text too.
  * "NO PLAY" in the text means the play was wiped out by a penalty.
  * A sack is a rushing attempt and rushing yards in college box scores.
  * The conversion after a touchdown follows ", clock": only the text before
    it says whether THIS play scored.

Result on those 13 games: receptions 100%, receiving yards 99%, passing
yards 97%, touchdowns 97%, rushing attempts 97%, rushing yards 95% exact.
What remains is kneel-downs (often credited to the team, not a player) and
the odd play the feed words differently, a few yards here and there.

NAMES ARE RESOLVED TO OUR ROSTER, never guessed (worker/core/name_match.py).
The plain format carries the jersey: within the offense's roster, a jersey
whose player has the same surname is the match (college reuses numbers on
offense and defense, so the surname check is not optional). Otherwise, and
for the two summary formats, the shared PlayerResolver, which refuses an
ambiguous name. A name that resolves to no one is counted and dropped.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Any

import cfbd

from worker.adapters.cfbd.client import CfbdClient
from worker.core.name_match import PlayerMatch, PlayerResolver, normalize, strip_suffix
from worker.db import connect, fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

TABLE = "live_player_stats"
#: The user's choice (2026-10-12): fresh enough to follow a prop, about
#: 10,500 calls a month on ~58 games a week with book props.
POLL_EVERY = timedelta(minutes=5)
#: Below this many calls left in CFBD's month, stop: the daily ingest jobs
#: come first, and a live number is a nicety.
QUOTA_RESERVE = 5_000
#: Games read per run, oldest read first. A read is ~3-4 s (one call, the two
#: rosters, the write); 15 keeps a run near a minute, inside the two-minute
#: schedule, and spreads a 40-game Saturday so each is read every ~6 minutes.
MAX_GAMES_PER_RUN = 15
#: A game the scoreboard has just called final gets one last read within this.
FINAL_GRACE = timedelta(hours=1)


@dataclass
class StatLine:
    pass_attempts: int = 0
    pass_completions: int = 0
    pass_yards: int = 0
    pass_tds: int = 0
    rush_attempts: int = 0
    rush_yards: int = 0
    receptions: int = 0
    rec_yards: int = 0
    offensive_tds: int = 0

    def add(self, other: StatLine) -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))


#: (CFBD team id of the offense, jersey or None, the name as written)
PlayerRef = tuple[int, int | None, str]

_NAME = r"[A-Z][A-Za-z'.\-]*(?:\s[A-Z][A-Za-z'\-]+\.?)*"
_PLAIN = rf"#(\d+)\s+({_NAME})"
_RUSH = re.compile(_PLAIN + r" rush")
_PASS = re.compile(_PLAIN + r" pass (complete|incomplete|intercepted)")
_TO = re.compile(r" to " + _PLAIN)
_SACK = re.compile(_PLAIN + r" sacked")
_RUSH_YDS = re.compile(r" rush [^,(]*?for (?:(\d+) yards? (gain|loss)|no gain|0 yards)")
_PASS_YDS = re.compile(r"caught at [^,]*?, for (?:(\d+) yards?( loss)?|no gain)")
_SACK_YDS = re.compile(r"sacked for (?:loss of (\d+) yards?|no gain)")
_SUMMARY_PASS = re.compile(r"^(.+?) (\d+) Yd pass from (.+?)\s*(?:\(|$)")
_SUMMARY_RUN = re.compile(r"^(.+?) (\d+) Yd Run")
_SHORT = r"[A-Z][A-Za-z'\-]*\. ?[A-Z][A-Za-z' \-.]*?"
_SHORT_PASS = re.compile(rf"^({_SHORT}) pass to ({_SHORT}) for (-?\d+) yds?")
_SHORT_RUN = re.compile(rf"^({_SHORT}) run for (-?\d+) yds?")
#: A play's player is named at its start; a name later on is a tackler.
_LEAD = 45


def _signed(match: re.Match[str] | None) -> int:
    if not match or match.group(1) is None:
        return 0
    value = int(match.group(1))
    return -value if match.group(2) and "loss" in match.group(2) else value


def parse_live_game(game: dict[str, Any]) -> dict[PlayerRef, StatLine]:
    """Each named player's stats so far, keyed by how the feed named them."""
    stats: dict[PlayerRef, StatLine] = defaultdict(StatLine)

    def plain(team: int, match: re.Match[str], group: int = 1) -> StatLine:
        return stats[(team, int(match.group(group)), match.group(group + 1))]

    for drive in game.get("drives") or []:
        for play in drive.get("plays") or []:
            text = str(play.get("playText") or "")
            team = play.get("teamId")
            if not text or team is None or "NO PLAY" in text:
                continue
            team = int(team)
            scored = int("TOUCHDOWN" in text.split(", clock")[0])

            if m := _SUMMARY_PASS.match(text):
                yards = int(m.group(2))
                receiver = stats[(team, None, m.group(1))]
                passer = stats[(team, None, m.group(3))]
                receiver.receptions += 1
                receiver.rec_yards += yards
                receiver.offensive_tds += 1
                passer.pass_attempts += 1
                passer.pass_completions += 1
                passer.pass_yards += yards
                passer.pass_tds += 1
            elif m := _SUMMARY_RUN.match(text):
                runner = stats[(team, None, m.group(1))]
                runner.rush_attempts += 1
                runner.rush_yards += int(m.group(2))
                runner.offensive_tds += 1
            elif m := _SHORT_PASS.match(text):
                yards = int(m.group(3))
                td = int("for a TD" in text)
                passer = stats[(team, None, m.group(1))]
                receiver = stats[(team, None, m.group(2))]
                passer.pass_attempts += 1
                passer.pass_completions += 1
                passer.pass_yards += yards
                passer.pass_tds += td
                receiver.receptions += 1
                receiver.rec_yards += yards
                receiver.offensive_tds += td
            elif m := _SHORT_RUN.match(text):
                runner = stats[(team, None, m.group(1))]
                runner.rush_attempts += 1
                runner.rush_yards += int(m.group(2))
                runner.offensive_tds += int("for a TD" in text)
            elif (m := _RUSH.search(text)) and m.start() < _LEAD:
                runner = plain(team, m)
                runner.rush_attempts += 1
                runner.rush_yards += _signed(_RUSH_YDS.search(text))
                runner.offensive_tds += scored
            elif (m := _PASS.search(text)) and m.start() < _LEAD:
                passer = plain(team, m)
                passer.pass_attempts += 1
                if m.group(3) != "complete":
                    continue
                yards = _signed(_PASS_YDS.search(text))
                passer.pass_completions += 1
                passer.pass_yards += yards
                passer.pass_tds += scored
                if to := _TO.search(text, m.end()):
                    receiver = plain(team, to)
                    receiver.receptions += 1
                    receiver.rec_yards += yards
                    receiver.offensive_tds += scored
            elif (m := _SACK.search(text)) and m.start() < _LEAD:
                passer = plain(team, m)
                passer.rush_attempts += 1
                sack = _SACK_YDS.search(text)
                passer.rush_yards -= int(sack.group(1)) if sack and sack.group(1) else 0
    return dict(stats)


def _surname(name: str) -> str:
    tokens = strip_suffix(normalize(name).split())
    return tokens[-1] if tokens else ""


class RosterResolver:
    """One team's roster: a feed name (and jersey, when given) -> player id."""

    def __init__(self, roster: list[dict[str, Any]]) -> None:
        self._by_jersey: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for player in roster:
            if player.get("jersey") is not None:
                self._by_jersey[int(player["jersey"])].append(player)
        self._names = PlayerResolver(roster)

    def resolve(self, jersey: int | None, name: str) -> int | None:
        if jersey is not None:
            surname = _surname(name)
            found = [
                p for p in self._by_jersey.get(jersey, []) if _surname(str(p["name"])) == surname
            ]
            if len(found) == 1:
                return int(found[0]["id"])
        outcome = self._names.resolve(name)
        return outcome.player_id if isinstance(outcome, PlayerMatch) else None


def stat_rows(
    parsed: dict[PlayerRef, StatLine],
    rosters: dict[int, tuple[int, RosterResolver]],
    game_id: int,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows per resolved player, summed across the formats that named them;
    and the names that resolved to no one."""
    totals: dict[tuple[int, int], StatLine] = defaultdict(StatLine)
    unresolved: list[str] = []
    for (cfbd_team, jersey, name), line in parsed.items():
        team = rosters.get(cfbd_team)
        player_id = team[1].resolve(jersey, name) if team else None
        if team is None or player_id is None:
            unresolved.append(name)
            continue
        totals[(team[0], player_id)].add(line)
    rows = [
        {
            "game_id": game_id,
            "player_id": player_id,
            "team_id": team_id,
            **vars(line),
            "updated_at": now,
        }
        for (team_id, player_id), line in totals.items()
    ]
    return rows, unresolved


def games_due(now: datetime) -> list[dict[str, Any]]:
    """College games to read now: in progress (or just final, once more),
    with book props, not read in the last five minutes."""
    return fetch_all(
        """
        select g.id, g.cfbd_id, g.season, g.home_team_id, g.away_team_id,
               h.cfbd_id as home_cfbd, a.cfbd_id as away_cfbd
          from live_scores l
          join games g on g.id = l.game_id
          join teams h on h.id = g.home_team_id
          join teams a on a.id = g.away_team_id
         where g.sport = 'cfb'
           and g.cfbd_id is not null
           and (
                 (l.status = 'in_progress'
                  and (l.plays_fetched_at is null or l.plays_fetched_at <= %(due)s))
              or (l.status = 'completed'
                  and l.updated_at >= %(grace)s
                  and (l.plays_fetched_at is null or l.plays_fetched_at < l.updated_at))
           )
           and exists (select 1 from player_prop_lines p where p.game_id = g.id)
         order by l.plays_fetched_at nulls first, g.start_date, g.id
         limit %(limit)s
        """,
        {"due": now - POLL_EVERY, "grace": now - FINAL_GRACE, "limit": MAX_GAMES_PER_RUN},
    )


def _rosters(game: dict[str, Any]) -> dict[int, tuple[int, RosterResolver]]:
    rows = fetch_all(
        """
        select pts.team_id, p.id, p.name, pts.jersey,
               (select count(*) from player_game_stats s
                 where s.player_id = p.id and s.season = pts.season) as games
          from player_team_seasons pts
          join players p on p.id = pts.player_id
         where pts.season = %(season)s and pts.team_id in (%(home)s, %(away)s)
        """,
        {"season": game["season"], "home": game["home_team_id"], "away": game["away_team_id"]},
    )
    out: dict[int, tuple[int, RosterResolver]] = {}
    for cfbd_team, team_id in (
        (game["home_cfbd"], game["home_team_id"]),
        (game["away_cfbd"], game["away_team_id"]),
    ):
        if cfbd_team is not None:
            roster = [r for r in rows if r["team_id"] == team_id]
            out[int(cfbd_team)] = (int(team_id), RosterResolver(roster))
    return out


def _write(game_id: int, rows: list[dict[str, Any]], now: datetime) -> None:
    """Replace this game's rows and stamp the read, in one transaction."""
    columns = ["game_id", "player_id", "team_id", *(f.name for f in fields(StatLine)), "updated_at"]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"delete from {TABLE} where game_id = %s", (game_id,))
        if rows:
            placeholders = ", ".join(["%s"] * len(columns))
            cur.executemany(
                f"insert into {TABLE} ({', '.join(columns)}) values ({placeholders})",
                [tuple(r[c] for c in columns) for r in rows],
            )
        cur.execute(
            "update live_scores set plays_fetched_at = %s where game_id = %s", (now, game_id)
        )


def poll(client: CfbdClient, now: datetime, *, dry_run: bool = False) -> int:
    """Rows written across the games due now (0 when none is)."""
    due = games_due(now)
    written = 0
    for game in due:
        response = client.call_uncached(
            "/live/plays",
            cfbd.PlaysApi,
            "get_live_plays_with_http_info",
            game_id=int(game["cfbd_id"]),
            _preload_content=False,
        )
        remaining = response.headers.get("X-CallLimit-Remaining") if response.headers else None
        payload = json.loads(response.raw_data)
        rows, unresolved = stat_rows(parse_live_game(payload), _rosters(game), int(game["id"]), now)
        log.info(
            "live plays: game %s (%s) -> %d player(s)%s; %s CFBD calls left",
            game["id"],
            payload.get("status"),
            len(rows),
            f", unresolved {sorted(set(unresolved))}" if unresolved else "",
            remaining,
        )
        if not dry_run:
            _write(int(game["id"]), rows, now)
            written += len(rows)
        if remaining is not None and int(remaining) < QUOTA_RESERVE:
            log.warning(
                "CFBD has %s calls left this month, under the %d reserve: live player "
                "stats stop here so the daily ingest keeps its calls.",
                remaining,
                QUOTA_RESERVE,
            )
            break
    return written
