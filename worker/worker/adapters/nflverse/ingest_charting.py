"""FTN charting -> `defense_charting_game_splits`: what a defense DOES.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). Every other defensive table in this
project measures what a defense ALLOWED; this one measures what it chose to do.

---------------------------------------------------------------------------
WHY THIS ASSET AND NOT THE COVERAGE ONE, WHICH IS WHAT WAS ASKED FOR
---------------------------------------------------------------------------
The client asked for man vs zone. nflverse does publish it, in
`pbp_participation` -- and that asset appears ONCE, AFTER a season has ended.
The 2025 file was created 2026-02-10; there is no 2026 file and on that pattern
there will not be one until around February 2027. A man/zone panel built on it
would show last season's numbers in week 1 and the same numbers in week 18,
never updating, with nothing on screen to say so.

The sample is thin as well as stale. Across all of 2025 the most-targeted
receiver in the league saw 36 targets against man, and exactly one player
cleared 30. A per-player man/zone split is built on about twenty targets.

`ftn_charting` is the live alternative: published weekly in season, two to three
days behind. It has no coverage shell at all, but it has what the front seven
did on every snap, which answers "what type of defense is this" with a number
that is current.

---------------------------------------------------------------------------
TWO DENOMINATORS, BECAUSE TWO THINGS ARE CHARTED ON DIFFERENT PLAYS
---------------------------------------------------------------------------
Measured on 2025 (47,316 charted plays):

  * 22,002 rows (46.5%) carry at least one pass rusher. Those are the dropbacks,
    and they are the only plays on which a blitz is defined. On them the mean is
    4.32 rushers and 29.8% carry a blitzer.
  * 36,081 rows carry at least one defender in the box, runs included.

Dividing blitzes by ALL charted plays would report the league blitzing on 14% of
snaps instead of 30%. The two denominators are stored separately for that
reason, and `n_pass_rushers >= 1` is the definition of a dropback here rather
than anything read off `plays.play_type`, so the numerator and the denominator
come from the same row of the same file.

---------------------------------------------------------------------------
THE JOIN, AND THE 27% THAT DOES NOT RESOLVE
---------------------------------------------------------------------------
Two exact keys, no name matching: `nflverse_game_id` is `games.nflverse_id` and
`nflverse_play_id` is `plays.nflverse_play_id` (migration 0059's unique index on
the pair). Measured against production for 2026 week 1: 1,948 of 2,675 charted
rows resolve, which is 72.8%.

THAT GAP IS OURS AND IT IS CORRECT. `plays` is trimmed to what the split engine
and the goal-line model consume; FTN charts kickoffs, punts and field goals
too. The number that matters is the one under the denominator this job actually
uses, and on dropbacks it is **94.2%**. A fall in THAT is worth investigating; a
fall in the headline rate usually just means more special teams.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from worker.adapters.nflverse.client import NflverseClient
from worker.adapters.nflverse.mapping import SPORT, int_or_none, text_or_none
from worker.db import execute, fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

#: A charted play counts as a dropback when FTN recorded anyone rushing the
#: passer. See the module docstring: this is the blitz-rate denominator and it
#: is taken from the charting file rather than from `plays.play_type`, so that
#: numerator and denominator can never come from two different judgements about
#: what a passing play is.
DROPBACK_MIN_RUSHERS = 1

#: A blitz is a fifth rusher or more, which FTN expresses directly: `n_blitzers`
#: counts defenders who rushed BEYOND the standard four, so >= 1 is the
#: definition and no arithmetic on `n_pass_rushers` is needed.
BLITZ_MIN = 1

#: "Heavy box" is seven or more. Seven is the conventional line -- six is the
#: base look against most personnel, so seven is the first count that means the
#: defense committed an extra man to the run.
HEAVY_BOX_MIN = 7

#: Columns read from the file. Named so that a schema change upstream fails here
#: with a clear error rather than producing silently empty aggregates.
REQUIRED_COLUMNS = (
    "nflverse_game_id",
    "nflverse_play_id",
    "week",
    "n_blitzers",
    "n_pass_rushers",
    "n_defense_box",
)


def load_play_index(season: int) -> dict[tuple[str, int], tuple[int, int, int]]:
    """(nflverse game key, nflverse play id) -> (defense_team_id, game_id, week).

    Read once per season rather than per row. The defense is OURS, not FTN's:
    the charting file names no team at all, which is why this join is required
    rather than a convenience.
    """
    rows = fetch_all(
        """
        select g.nflverse_id, p.nflverse_play_id, p.defense_team_id,
               p.game_id, g.week
          from plays p
          join games g on g.id = p.game_id
         where g.sport = %s
           and g.season = %s
           and g.nflverse_id is not null
           and p.nflverse_play_id is not null
           and p.defense_team_id is not null
        """,
        (SPORT, season),
    )
    return {
        (r["nflverse_id"], r["nflverse_play_id"]):
            (r["defense_team_id"], r["game_id"], r["week"])
        for r in rows
    }


def aggregate(
    frame: pl.DataFrame,
    index: dict[tuple[str, int], tuple[int, int, int]],
    counts: Any,
) -> list[dict[str, Any]]:
    """Per (defense, game) counts from the charted plays that resolve.

    Returns one row per defense per game, with both denominators and both
    numerators, so every rate downstream divides a sum by a sum.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(
            f"ftn_charting is missing {missing}; the upstream schema has "
            f"changed and the aggregates would be silently empty. Columns "
            f"present: {sorted(frame.columns)}"
        )

    totals: dict[tuple[int, int], dict[str, Any]] = {}
    dropbacks_seen = 0
    dropbacks_resolved = 0

    for row in frame.to_dicts():
        game_key = text_or_none(row.get("nflverse_game_id"))
        play_id = int_or_none(row.get("nflverse_play_id"))
        rushers = int_or_none(row.get("n_pass_rushers")) or 0
        box = int_or_none(row.get("n_defense_box")) or 0
        blitzers = int_or_none(row.get("n_blitzers")) or 0

        is_dropback = rushers >= DROPBACK_MIN_RUSHERS
        if is_dropback:
            dropbacks_seen += 1

        if game_key is None or play_id is None:
            counts.skip("charting: no play key")
            continue

        resolved = index.get((game_key, play_id))
        if resolved is None:
            # Expected in bulk: `plays` does not store special teams. Counted
            # rather than logged per row, and judged on the dropback rate below.
            counts.skip("charting: play not resolved")
            continue

        defense_id, game_id, week = resolved
        if is_dropback:
            dropbacks_resolved += 1

        key = (defense_id, game_id)
        entry = totals.get(key)
        if entry is None:
            entry = totals[key] = {
                "defense_team_id": defense_id,
                "game_id": game_id,
                "week": week,
                "dropbacks": 0,
                "blitz_plays": 0,
                "pass_rushers_sum": 0,
                "box_plays": 0,
                "heavy_box_plays": 0,
            }

        if is_dropback:
            entry["dropbacks"] += 1
            entry["pass_rushers_sum"] += rushers
            if blitzers >= BLITZ_MIN:
                entry["blitz_plays"] += 1
        if box >= 1:
            entry["box_plays"] += 1
            if box >= HEAVY_BOX_MIN:
                entry["heavy_box_plays"] += 1

    # THE RATE WORTH WATCHING. The headline resolution rate falls whenever the
    # file carries more special teams; this one falling means the play key has
    # drifted, and every blitz rate would then rest on a partial game.
    if dropbacks_seen:
        share = dropbacks_resolved / dropbacks_seen
        log.info(
            "charting: %d of %d charted dropbacks resolved to a stored play "
            "(%.1f%%)", dropbacks_resolved, dropbacks_seen, share * 100,
        )
        if share < 0.85:
            log.warning(
                "charting: only %.1f%% of charted dropbacks resolved -- expected "
                "about 94%%. The play key may have drifted; blitz rates from "
                "this run rest on partial games.", share * 100,
            )

    return list(totals.values())


def write(season: int, rows: list[dict[str, Any]]) -> int:
    """Replace this season's charting splits with `rows`.

    REPLACE, NOT MERGE, for the reason `splits.py` records: a derived table that
    is only ever upserted keeps orphans forever, and a game whose charting is
    withdrawn upstream would otherwise keep its old counts with nothing to say
    they are stale. Scoped to the season and, through `games`, to the sport.
    """
    if not rows:
        return 0

    params = {
        "defense_ids": [r["defense_team_id"] for r in rows],
        "game_ids": [r["game_id"] for r in rows],
        "weeks": [r["week"] for r in rows],
        "dropbacks": [r["dropbacks"] for r in rows],
        "blitz": [r["blitz_plays"] for r in rows],
        "rushers": [r["pass_rushers_sum"] for r in rows],
        "box": [r["box_plays"] for r in rows],
        "heavy": [r["heavy_box_plays"] for r in rows],
        "season": season,
    }

    execute(
        """
        delete from defense_charting_game_splits s
         using games g
         where g.id = s.game_id and g.sport = %(sport)s and s.season = %(season)s
        """,
        {"sport": SPORT, "season": season},
    )

    return execute(
        """
        insert into defense_charting_game_splits
            (defense_team_id, game_id, season, week, dropbacks, blitz_plays,
             pass_rushers_sum, box_plays, heavy_box_plays)
        select unnest(%(defense_ids)s::bigint[]),
               unnest(%(game_ids)s::bigint[]),
               %(season)s,
               unnest(%(weeks)s::smallint[]),
               unnest(%(dropbacks)s::smallint[]),
               unnest(%(blitz)s::smallint[]),
               unnest(%(rushers)s::smallint[]),
               unnest(%(box)s::smallint[]),
               unnest(%(heavy)s::smallint[])
        """,
        params,
    )


def run_nfl_charting_ingest(
    client: NflverseClient, season: int, counts: Any, *, max_age: float | None
) -> int:
    """Load one season of FTN charting into the per-game splits. Rows written."""
    frame = client.fetch("ftn_charting", season, max_age=max_age)

    index = load_play_index(season)
    if not index:
        raise ValueError(
            f"no NFL plays with an nflverse play id stored for {season} -- run "
            f"nfl_ingest_plays first; charting has nothing to attach to"
        )

    rows = aggregate(frame, index, counts)

    # A FULL FILE THAT RESOLVES TO NOTHING IS A FAULT, NOT AN EMPTY SEASON.
    # `write` leaves the existing rows alone when handed nothing, which is the
    # right call for a season nflverse has not published yet -- but it makes a
    # BROKEN JOIN look like a clean run: zero rows written, exit 0, last week's
    # blitz rates still on the board and nothing saying they are frozen. The
    # distinction is whether the file had anything to resolve.
    if frame.height and not rows:
        raise ValueError(
            f"{season}: {frame.height} charted play(s) resolved to no stored "
            f"play at all. The play key has broken rather than the season being "
            f"empty -- existing charting has been left untouched. Check that "
            f"nfl_ingest_plays has run for {season} and that "
            f"plays.nflverse_play_id is populated."
        )

    written = write(season, rows)
    counts.add("defense_charting_game_splits", written)
    log.info(
        "charting %d: %d charted row(s) -> %d (defense, game) row(s)",
        season, frame.height, written,
    )
    return written


__all__ = [
    "BLITZ_MIN",
    "DROPBACK_MIN_RUSHERS",
    "HEAVY_BOX_MIN",
    "REQUIRED_COLUMNS",
    "aggregate",
    "load_play_index",
    "run_nfl_charting_ingest",
    "write",
]
