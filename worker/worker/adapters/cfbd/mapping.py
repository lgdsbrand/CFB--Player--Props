"""Translation from CFBD's vocabulary into this schema's enums.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The NFL build replaces this file; nothing
here should leak into worker/core.

Position normalization is the consequential part. The position-split engine (§5)
groups everything a defense allowed by the position of the player involved, so a
mis-bucketed position does not produce a missing number — it produces a *wrong*
one, quietly, in the primary signal. `players.position_raw` retains the original
string precisely so these decisions can be revised without re-ingesting.
"""

from __future__ import annotations

from collections import Counter

from worker.logging_setup import get_logger

log = get_logger(__name__)

# CFBD position string -> position_group enum value.
#
# Judgment calls worth knowing about:
#   * FB -> RB. Fullbacks take goal-line carries and short receptions. Bucketing
#     them as OTHER would drop that production out of "rush yards allowed to
#     RBs", which is exactly the number the RB market depends on. Slight
#     over-inclusion beats a systematic under-count.
#   * EDGE -> DL. Alignment varies by scheme, but for "what did this defense
#     allow" the relevant thing is that they rush the passer from the line.
#   * ATH -> OTHER. Genuinely ambiguous in recruiting-derived data; the position
#     recorded at the time of a play is what the split engine uses, so leaving
#     ATH unassigned here is safer than guessing.
POSITION_MAP: dict[str, str] = {
    # Offense — skill
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "HB": "RB",
    "TB": "RB",
    "WR": "WR",
    "TE": "TE",
    # Offensive line
    "OL": "OL",
    "OT": "OL",
    "OG": "OL",
    "G": "OL",
    "C": "OL",
    "T": "OL",
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
    # Secondary
    "DB": "DB",
    "CB": "DB",
    "S": "DB",
    "FS": "DB",
    "SS": "DB",
    # Specialists
    "PK": "K",
    "K": "K",
    "P": "P",
}

VALID_POSITION_GROUPS = frozenset(
    {"QB", "RB", "WR", "TE", "OL", "DL", "LB", "DB", "K", "P", "OTHER"}
)

# The positions we actually model markets for (CLAUDE.md §6).
SKILL_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})

CLASSIFICATION_MAP: dict[str, str] = {
    "fbs": "fbs",
    "fcs": "fcs",
    "ii": "ii",
    "iii": "iii",
}

SEASON_TYPE_MAP: dict[str, str] = {
    "regular": "regular",
    "postseason": "postseason",
}


class PositionNormalizer:
    """Maps position strings, counting anything it does not recognise.

    Tracking unmapped values is the point: a new CFBD position string appearing
    silently as OTHER would remove those players from every market they belong
    to, with nothing in the logs to say so.
    """

    def __init__(self) -> None:
        self.unmapped: Counter[str] = Counter()
        self.mapped: Counter[str] = Counter()

    def normalize(self, raw: str | None) -> str:
        if not raw:
            self.unmapped["<missing>"] += 1
            return "OTHER"

        key = raw.strip().upper()
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


def normalize_classification(raw: str | None) -> str:
    """Map CFBD classification to the team_classification enum."""
    if not raw:
        return "other"
    return CLASSIFICATION_MAP.get(str(raw).strip().lower(), "other")


def normalize_season_type(raw: str | None) -> str:
    """Map CFBD seasonType to the season_type enum, defaulting to regular."""
    if not raw:
        return "regular"
    return SEASON_TYPE_MAP.get(str(raw).strip().lower(), "regular")


# -----------------------------------------------------------------------------
# The season time axis
# -----------------------------------------------------------------------------
# CFBD numbers postseason games from 1 under seasonType='postseason', so a bowl
# played on 19 December arrives as "week 1" — the same label as the season
# opener. `games.week` is the time axis every point-in-time cutoff in this
# schema is defined against, and storing CFBD's number verbatim put a December
# result inside week 1. Every "through week N" aggregation for N >= 2 then read
# it: real lookahead, of exactly the kind CLAUDE.md §4 calls disqualifying.
#
# It survived eight lookahead checks in `audit_data` because all of them
# verified that cutoffs were applied correctly AGAINST the week column. None
# asked whether the week column ordered games by time. The guard was perfectly
# consistent with the bug.
#
# A FIXED OFFSET, NOT max(regular week) + n. The maximum regular week is a
# moving target — mid-season it is however much of the schedule has been
# ingested, so a bowl stored against it in September would collide with a real
# week in November. 20 clears any regular season (the longest here is 16) and
# leaves an obvious gap, so a week in the twenties reads as postseason on sight.
POSTSEASON_WEEK_OFFSET = 20


def week_on_season_axis(week: int, season_type: str) -> int:
    """CFBD's week number placed on this schema's monotone season axis."""
    return week + POSTSEASON_WEEK_OFFSET if season_type == "postseason" else week


def week_for_api(week: int, season_type: str) -> int:
    """The inverse: our stored week back to the number CFBD's API expects.

    Every per-week endpoint is queried as (season_type, week) with CFBD's own
    numbering, and those calls are driven from our `games` table. Without this
    the stats ingest would ask for postseason week 21 and get nothing back.
    """
    return week - POSTSEASON_WEEK_OFFSET if season_type == "postseason" else week


def inches_or_none(value: object) -> int | None:
    """CFBD heights are already inches, but arrive as int, str or None."""
    if value in (None, ""):
        return None
    try:
        parsed = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Guard against obvious junk; a 2-inch or 10-foot player is bad data.
    return parsed if 48 <= parsed <= 96 else None


def pounds_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if 100 <= parsed <= 500 else None


def bigint_or_none(value: object) -> int | None:
    """Coerce an identifier to int.

    CFBD is not consistent about id types across endpoints: /teams and /games
    return them as ints, /roster returns athlete ids as STRINGS ('102597').
    Postgres coerces on INSERT either way, so the mismatch does not show up as a
    write error — it shows up when a Python-side dict lookup compares '102597'
    to 102597, misses, and produces zero joined rows with no exception anywhere.
    Normalizing every id through here keeps writes and lookups on the same type.
    """
    if value in (None, ""):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def smallint_or_none(value: object) -> int | None:
    """Coerce to int within Postgres smallint range, else None."""
    if value in (None, ""):
        return None
    try:
        parsed = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if -32768 <= parsed <= 32767 else None
