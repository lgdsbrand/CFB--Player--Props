"""Translating nflverse values into this project's own vocabulary.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The mirror image of
`worker/adapters/cfbd/mapping.py`, and it is meant to be read beside it: a
reader comparing the two should be able to see the sport seam without diffing a
schema.
"""

from __future__ import annotations

from collections import Counter

from worker.logging_setup import get_logger

log = get_logger(__name__)

# SET EXPLICITLY, NOT LEFT TO THE COLUMN DEFAULT, and this is the whole point of
# the constant. `sport` defaults to 'cfb' (migration 0035) so that adding the
# dimension did not require touching every insert in the project. That default
# is safe rather than lucky: an NFL row that forgets to set `sport` has no
# cfbd_id and is rejected by `teams_cfb_requires_cfbd_id` instead of quietly
# joining the college board. Saying 'nfl' here means never relying on that
# tripwire firing.
SPORT = "nfl"

# Completed seasons are immutable, so their assets never expire. The caller
# overrides this for the season being played — see `NflverseClient.fetch`,
# where `max_age` is required precisely so this choice is visible.
IMMUTABLE = None

# Fifteen minutes. What the in-season reads pass: long enough that re-running
# the split engine a few times costs one download, short enough that a Sunday
# afternoon refresh sees Sunday afternoon's games.
LIVE_MAX_AGE = 900.0


# -----------------------------------------------------------------------------
# positions
# -----------------------------------------------------------------------------
# nflverse carries both `position` (QB, RB, FB, WR, TE, ...) and
# `position_group` (QB, RB, WR, TE, SPEC, DB, OL, LB, DL). The group column is
# ALMOST what we want and is not used directly: it buckets fullbacks under RB
# the way we do, but it also emits 'SPEC' for specialists, which our enum does
# not have, and it is absent from the play-by-play entirely. Mapping the raw
# position ourselves means one rule that works on every asset.
POSITION_MAP: dict[str, str] = {
    # Offense — skill. FB/HB collapse into RB exactly as they do for college.
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "HB": "RB",
    "WR": "WR",
    "TE": "TE",
    # Offensive line
    "OL": "OL",
    "T": "OL",
    "OT": "OL",
    "G": "OL",
    "OG": "OL",
    "C": "OL",
    "LS": "OL",
    # Defensive line
    "DL": "DL",
    "DE": "DL",
    "DT": "DL",
    "NT": "DL",
    "EDGE": "DL",
    # Linebackers
    "LB": "LB",
    "ILB": "LB",
    "OLB": "LB",
    "MLB": "LB",
    # Secondary. `SAF` is the weekly-stats spelling and `S` the roster one --
    # the two assets disagree, and only enumerating every string across both
    # found it. It was 3,920 rows silently bucketed as OTHER.
    "DB": "DB",
    "CB": "DB",
    "S": "DB",
    "SAF": "DB",
    "SS": "DB",
    "FS": "DB",
    # Specialists
    "K": "K",
    "P": "P",
}

#: The four this project actually models (CLAUDE.md §6). Everything else is
#: ingested and never projected — a defensive lineman has no prop market.
MODELLED_POSITIONS: frozenset[str] = frozenset({"QB", "RB", "WR", "TE"})


class PositionMapper:
    """Maps raw position strings, counting what it could not place.

    Counts rather than raises, for the reason the college one does: an
    unrecognised position is a data-quality signal, not a stop condition, and
    burying it in a log line nobody reads is how 'OTHER' quietly becomes the
    biggest group.
    """

    def __init__(self) -> None:
        self.mapped: Counter[str] = Counter()
        self.unmapped: Counter[str] = Counter()

    def group_for(self, raw: str | None) -> str:
        key = (raw or "").strip().upper()
        if not key:
            self.unmapped["<missing>"] += 1
            return "OTHER"

        group = POSITION_MAP.get(key)
        if group is None:
            self.unmapped[key] += 1
            return "OTHER"

        self.mapped[group] += 1
        return group

    def report(self) -> None:
        if self.mapped:
            log.info(
                "Positions mapped: %s",
                ", ".join(f"{k}={v}" for k, v in sorted(self.mapped.items())),
            )
        if self.unmapped:
            log.warning(
                "Unmapped position strings (bucketed as OTHER): %s",
                ", ".join(f"{k}={v}" for k, v in self.unmapped.most_common()),
            )


# -----------------------------------------------------------------------------
# teams
# -----------------------------------------------------------------------------
# nflverse uses stable abbreviations, but not the same ones every source uses,
# and two of them move: the Rams are 'LA' here and 'LAR' almost everywhere else,
# and historical rows carry the pre-relocation codes. These are the aliases that
# must resolve to the same franchise, so a join on abbreviation does not
# silently drop a team.
#
# Kept as a table rather than inferred, because the failure mode of getting one
# wrong is not an error — it is a team quietly missing from a slate.
TEAM_ALIASES: dict[str, str] = {
    "LAR": "LA",     # Rams: nflverse says LA, The Odds API and ESPN say LAR
    "STL": "LA",     # Rams pre-2016
    "SD": "LAC",     # Chargers pre-2017
    "OAK": "LV",     # Raiders pre-2020
    "SL": "LA",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "JAC": "JAX",
    "WSH": "WAS",
    "WFT": "WAS",
}

#: The 32 current franchises, as nflverse spells them. Measured from
#: roster_2026.csv and stats_player_week_2025.csv on 2026-09-05.
CURRENT_TEAMS: frozenset[str] = frozenset({
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
})


def canonical_team(abbr: str | None) -> str | None:
    """The nflverse spelling of a team abbreviation, or None if unrecognised.

    Returning None rather than the input is deliberate: an unrecognised
    abbreviation must not become a team row of its own. `teams.nfl_abbr` is
    unique (migration 0049), so a typo that got through would either collide or
    create a 33rd franchise.
    """
    if not abbr:
        return None
    key = abbr.strip().upper()
    key = TEAM_ALIASES.get(key, key)
    return key if key in CURRENT_TEAMS else None


# -----------------------------------------------------------------------------
# scalars
# -----------------------------------------------------------------------------
def text_or_none(value: object) -> str | None:
    """Empty string and whitespace are absence, not a value.

    nflverse CSVs use '' and 'NA' for missing. `NflverseClient.fetch` already
    maps both to null, so this is for values that reach here another way.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def int_or_none(value: object) -> int | None:
    """A numeric that may be blank, 'NA', or a float-shaped integer.

    nflverse writes whole numbers through a float column often enough that
    `int('17.0')` would raise on real data.
    """
    text = text_or_none(value)
    if text is None or text.upper() == "NA":
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None
