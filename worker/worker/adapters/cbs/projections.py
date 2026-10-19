"""CBS weekly PPR projections -> `fantasy_projections` (migration 0093).

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). Each run reads the four position pages
for every week a team's next game falls in (usually one week, two when some
teams are on a bye), and writes each team's players into that team's open
week, replacing it until the game kicks off (core/team_weeks.py). CBS keeps no
history (client.py), so the last capture before kickoff is the record.

THE NAME CELL CARRIES THE TEAM. A row's first cell reads
"J. Allen QB BUF Josh Allen QB BUF": short name, position, team, then the full
name, position and team again. The team code is what makes the match to our
players exact; the full name is what is stored and matched.

A PAGE THAT IS NOT THE WEEK ASKED FOR IS AN ERROR. The page title names the
week shown ("Week 7 Proj Fantasy Football Stats - QB Points"), and CBS answers
a week it no longer serves with the current week's table. `check_week` turns
that silent substitution into a failure.

ONLY TEAMS THE PAGES COVER ARE REPLACED. A team missing from every page this
run (a bye week asked of the wrong week, or CBS dropping a team) keeps what an
earlier run wrote, as depth charts do.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from worker.adapters.cbs.client import CbsClient, CbsError
from worker.adapters.nflverse.mapping import canonical_team
from worker.core.name_match import normalize, strip_suffix
from worker.logging_setup import get_logger

log = get_logger(__name__)

TABLE = "fantasy_projections"
SOURCE = "cbs"
POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")

#: Rows per page below which the page has lost its table. Measured 2026-10-14
#: with four teams on a bye: QB 57, RB 98 (incl. fullbacks), WR 100, TE 100.
MIN_ROWS: dict[str, int] = {"QB": 20, "RB": 40, "WR": 40, "TE": 40}

_TITLE_WEEK = re.compile(r"Week\s+(\d+)\s+Proj", re.IGNORECASE)
_NAME_CELL = re.compile(
    r"^(?P<short>.+?) (?P<pos>[A-Z]{1,3}) (?P<team>[A-Z]{2,3}) (?P<full>.+) (?P=pos) (?P=team)$"
)


@dataclass(frozen=True)
class CbsProjection:
    position: str
    team: str  # nflverse abbreviation (teams.nfl_abbr)
    full_name: str
    fantasy_points: float


def name_key(name: str | None) -> str:
    """The comparison form of a player name: folded, suffix-free."""
    return " ".join(strip_suffix(normalize(name).split()))


class _Tables(HTMLParser):
    """Every <table> as a list of rows of cell text, plus the first <title>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "title" and self.title is None:
            self._in_title = True
        elif tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._title_parts).split())
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._depth:
                self.tables[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._cell is not None:
            self._cell.append(data)


class CbsWeekNotServed(CbsError):
    """CBS answered with another week's table: the week asked is not served.

    Raised for a week CBS has not published yet (a team on a bye next week
    has its open week two ahead) as much as for one it has dropped. The page
    itself is fine, so `fetch_weeks` skips that week rather than failing.
    """

    def __init__(self, asked: int, shown: int) -> None:
        super().__init__(
            f"asked CBS for week {asked}, the page shows week {shown}: "
            "CBS serves only the current weeks and substitutes silently"
        )
        self.asked = asked
        self.shown = shown


def check_week(title: str | None, week: int) -> None:
    """Raise unless the page title says it shows `week`."""
    match = _TITLE_WEEK.search(title or "")
    if match is None:
        raise CbsError(f"projection page has no week in its title ({title!r})")
    shown = int(match.group(1))
    if shown != week:
        raise CbsWeekNotServed(week, shown)


def parse_page(html: str, position: str, week: int, counts: Any) -> list[CbsProjection]:
    """The projections on one position page; raises if it is the wrong page."""
    parser = _Tables()
    parser.feed(html)
    check_week(parser.title, week)

    table = next(
        (t for t in parser.tables if any(any(c.startswith("fppg") for c in r) for r in t)),
        None,
    )
    if table is None:
        raise CbsError(f"{position} week {week}: no projection table (no fppg column)")
    header_at = next(i for i, r in enumerate(table) if any(c.startswith("fppg") for c in r))
    header = table[header_at]
    fppg = next(i for i, c in enumerate(header) if c.startswith("fppg"))

    out: list[CbsProjection] = []
    for row in table[header_at + 1:]:
        if len(row) <= fppg:
            counts.skip("cbs: short row")
            continue
        match = _NAME_CELL.match(row[0])
        if match is None:
            counts.skip("cbs: unreadable name cell")
            continue
        if match.group("pos") != position:
            # Fullbacks are listed on the RB page.
            counts.skip(f"cbs: {match.group('pos')} on the {position} page")
            continue
        team = canonical_team(match.group("team"))
        if team is None:
            counts.skip("cbs: unknown team code")
            continue
        try:
            points = float(row[fppg])
        except ValueError:
            counts.skip("cbs: blank projection")
            continue
        out.append(CbsProjection(position, team, match.group("full").strip(), points))

    if len(out) < MIN_ROWS[position]:
        raise CbsError(
            f"{position} week {week}: only {len(out)} projection(s), expected at "
            f"least {MIN_ROWS[position]}; the page has changed shape"
        )
    return out


def build_rows(
    projections: Iterable[tuple[int, CbsProjection]],
    season: int,
    team_weeks: Mapping[int, int],
    team_ids: Mapping[str, int],
    players: Mapping[tuple[str, str], int],
    at: datetime,
    counts: Any,
) -> list[dict[str, Any]]:
    """fantasy_projections rows for each team's open week.

    `projections` are (week, projection) pairs; a projection lands only if its
    week is its team's open week. `players` maps (name key, nfl_abbr) to our
    player id, unique names only.
    """
    rows: dict[tuple, dict[str, Any]] = {}
    for week, p in projections:
        team_id = team_ids.get(p.team)
        if team_id is None:
            counts.skip("cbs: team not in database")
            continue
        if team_weeks.get(team_id) != week:
            # The other week's page lists this team too (CBS shows next week's
            # opponents early); only its own open week is written.
            continue
        player_id = players.get((name_key(p.full_name), p.team))
        if player_id is None:
            counts.skip("cbs: player not resolved (kept by name)")
        key = (team_id, p.position, p.full_name)
        if key in rows:
            counts.skip("cbs: same name twice on one team")
            continue
        rows[key] = {
            "season": season,
            "week": week,
            "team_id": team_id,
            "source": SOURCE,
            "position": p.position,
            "player_name": p.full_name,
            "player_id": player_id,
            "fantasy_points": round(p.fantasy_points, 1),
            "source_as_of": at,
        }
    return list(rows.values())


def fetch_weeks(
    client: CbsClient, season: int, weeks: Iterable[int], counts: Any
) -> list[tuple[int, CbsProjection]]:
    """Every position page for every week asked, as (week, projection) pairs.

    A week CBS does not serve is skipped whole, and its teams keep what an
    earlier run wrote (`build_rows` only replaces teams it has rows for).
    2026-10-18: with teams on a week-7 bye, their open week was 8, CBS showed
    week 6 for it, and the whole capture failed until Thursday's teams had
    nothing. If no week asked is served at all, that is still an error.
    """
    out: list[tuple[int, CbsProjection]] = []
    asked = sorted(set(weeks))
    skipped: list[int] = []
    for week in asked:
        try:
            pages = [
                (week, p)
                for position in POSITIONS
                for p in parse_page(
                    client.projection_page(position, season, week), position, week, counts
                )
            ]
        except CbsWeekNotServed as exc:
            log.warning("skipping week %d: %s", week, exc)
            counts.skip(f"cbs: week {week} not served")
            skipped.append(week)
            continue
        out.extend(pages)
    if asked and len(skipped) == len(asked):
        raise CbsError(f"CBS serves none of the weeks asked ({asked})")
    return out


__all__ = [
    "MIN_ROWS",
    "POSITIONS",
    "SOURCE",
    "TABLE",
    "CbsProjection",
    "CbsWeekNotServed",
    "build_rows",
    "check_week",
    "fetch_weeks",
    "name_key",
    "parse_page",
]
