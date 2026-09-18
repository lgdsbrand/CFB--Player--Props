"""Point-in-time defensive tendencies from the per-game charting splits.

SPORT-AGNOSTIC CORE (CLAUDE.md §3). Nothing here knows about nflverse; it reads
`defense_charting_game_splits` and writes `defense_charting_ratings`, and takes a
sport only to scope the pass. Only the NFL has rows today because only the
nflverse adapter writes them, and that is a fact about the data rather than
about this module.

THE CUTOFF IS THE WHOLE POINT. A rating at `as_of_week = N` is computed from
games with `week < N` STRICTLY, the same rule `splits.py` follows and the one
CLAUDE.md §4 calls a silent, disqualifying bug to break. A blitz rate is shown
rather than modelled, which changes nothing: a week-2 board must not carry what
happened in week 2.

RATES ARE SUMS OVER SUMS, NEVER MEANS OF MEANS. A defense that saw 22 dropbacks
in a blowout and 48 in a shootout has not blitzed at the average of its two
game rates. The per-game table stores counts for exactly this reason.

WHY THE RANK EXISTS HERE AND NOT IN `splits.py`. That module ranks what a
defense ALLOWED, after an opponent adjustment. This ranks what a defense DID,
raw, because a scheme choice needs no opponent adjustment -- who you played does
not change how often you sent five. Migration 0072 carries the measurement that
earned the rank (+0.62 half-to-half against +0.18 for the adjusted rank already
published).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from worker.db import execute, fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

#: Dropbacks a defense needs before its blitz rate is published at a cutoff.
#:
#: MEASURED, NOT PICKED. A single game gives a defense about 35 dropbacks, so a
#: floor of 20 admits every team from week 2 onward -- which matters, because
#: week 2 is the first week this can say anything at all and FTN charts the
#: previous week within two to three days. Below 20 the standard error on a 30%
#: rate is over 10 points, which is wider than the gap between the league's
#: median and its most aggressive defense.
MIN_DROPBACKS_TO_RATE = 20

#: Charted plays a defense needs before its heavy-box rate is published. Lower
#: than the dropback floor because the denominator is larger: box counts are
#: charted on runs as well, so one game supplies roughly twice as many.
MIN_BOX_PLAYS_TO_RATE = 40


def compute_ratings(season: int, sport: str, max_week: int | None = None) -> int:
    """Rebuild every (defense, as_of_week) rating for one season. Rows written.

    Returns rows written, which is the whole season -- this replaces rather than
    merges, for the reason `splits.py` gives: a derived table that is only ever
    upserted keeps a stale row forever once its inputs stop producing one.
    """
    games = fetch_all(
        """
        select s.defense_team_id, s.week, s.dropbacks, s.blitz_plays,
               s.pass_rushers_sum, s.box_plays, s.heavy_box_plays
          from defense_charting_game_splits s
          join teams t on t.id = s.defense_team_id
         where s.season = %s and t.sport = %s
         order by s.week
        """,
        (season, sport),
    )
    if not games:
        log.warning(
            "No charting splits for %s %d; run the charting ingest first.",
            sport, season,
        )
        return 0

    weeks = sorted({g["week"] for g in games})
    cutoffs = range(2, (max_week or max(weeks)) + 2)

    payload: list[dict[str, Any]] = []
    for as_of_week in cutoffs:
        # THE CUTOFF. Strictly earlier weeks only.
        prior = [g for g in games if g["week"] < as_of_week]
        if not prior:
            continue

        totals: dict[int, dict[str, int]] = defaultdict(
            lambda: {
                "games": 0, "dropbacks": 0, "blitz": 0,
                "rushers": 0, "box": 0, "heavy": 0,
            }
        )
        for g in prior:
            t = totals[g["defense_team_id"]]
            t["games"] += 1
            t["dropbacks"] += g["dropbacks"]
            t["blitz"] += g["blitz_plays"]
            t["rushers"] += g["pass_rushers_sum"]
            t["box"] += g["box_plays"]
            t["heavy"] += g["heavy_box_plays"]

        rows_this_cut: list[dict[str, Any]] = []
        for defense_id, t in totals.items():
            rateable = t["dropbacks"] >= MIN_DROPBACKS_TO_RATE
            row: dict[str, Any] = {
                "defense_team_id": defense_id,
                "season": season,
                "as_of_week": as_of_week,
                "games_included": t["games"],
                "dropbacks": t["dropbacks"],
                "box_plays": t["box"],
                # A row is written even below the floor, because the SAMPLE is
                # worth showing -- "2 games, not enough dropbacks yet" is a
                # better answer for a reader than no row at all, which a surface
                # cannot distinguish from a defense that has not played.
                "blitz_rate": (
                    round(t["blitz"] / t["dropbacks"], 4) if rateable else None
                ),
                "mean_pass_rushers": (
                    round(t["rushers"] / t["dropbacks"], 2) if rateable else None
                ),
                "heavy_box_rate": (
                    round(t["heavy"] / t["box"], 4)
                    if t["box"] >= MIN_BOX_PLAYS_TO_RATE else None
                ),
                "blitz_rank": None,
            }
            rows_this_cut.append(row)

        # 1 = BLITZES MOST, which is the conventional reading of "first in blitz
        # rate" and is the OPPOSITE of `rank_vs_position` (1 = allows the
        # least). Migration 0072 argues the choice; every surface prints the
        # style label beside the number so it never travels alone.
        #
        # Unrated defenses are excluded from the ordering rather than sorted to
        # the end: a team with 12 dropbacks is not "the least aggressive", it is
        # unmeasured, and giving it rank 32 would state the first thing.
        rateable_rows = [r for r in rows_this_cut if r["blitz_rate"] is not None]
        rateable_rows.sort(key=lambda r: r["blitz_rate"], reverse=True)
        for rank, row in enumerate(rateable_rows, start=1):
            row["blitz_rank"] = rank

        payload.extend(rows_this_cut)

    stale = execute(
        """
        delete from defense_charting_ratings r
         using teams t
         where t.id = r.defense_team_id and t.sport = %s and r.season = %s
        """,
        (sport, season),
    )
    if stale:
        log.info("charting ratings: cleared %d stale row(s)", stale)

    if not payload:
        return 0

    execute(
        """
        insert into defense_charting_ratings
            (defense_team_id, season, as_of_week, games_included, dropbacks,
             box_plays, blitz_rate, blitz_rank, mean_pass_rushers, heavy_box_rate)
        select unnest(%(defense_ids)s::bigint[]),
               unnest(%(seasons)s::smallint[]),
               unnest(%(as_of)s::smallint[]),
               unnest(%(games)s::smallint[]),
               unnest(%(dropbacks)s::integer[]),
               unnest(%(box)s::integer[]),
               unnest(%(blitz_rate)s::numeric[]),
               unnest(%(blitz_rank)s::smallint[]),
               unnest(%(rushers)s::numeric[]),
               unnest(%(heavy_rate)s::numeric[])
        """,
        {
            "defense_ids": [r["defense_team_id"] for r in payload],
            "seasons": [r["season"] for r in payload],
            "as_of": [r["as_of_week"] for r in payload],
            "games": [r["games_included"] for r in payload],
            "dropbacks": [r["dropbacks"] for r in payload],
            "box": [r["box_plays"] for r in payload],
            "blitz_rate": [r["blitz_rate"] for r in payload],
            "blitz_rank": [r["blitz_rank"] for r in payload],
            "rushers": [r["mean_pass_rushers"] for r in payload],
            "heavy_rate": [r["heavy_box_rate"] for r in payload],
        },
    )
    return len(payload)


def season_summary(season: int, sport: str) -> dict[str, Any]:
    """Coverage and spread at the latest cutoff, for the job to log."""
    rows = fetch_all(
        """
        select r.as_of_week, count(*) as defenses,
               count(r.blitz_rate) as rated,
               round(min(r.blitz_rate), 4)  as min_blitz,
               round(avg(r.blitz_rate), 4)  as mean_blitz,
               round(max(r.blitz_rate), 4)  as max_blitz,
               round(avg(r.heavy_box_rate), 4) as mean_heavy_box
          from defense_charting_ratings r
          join teams t on t.id = r.defense_team_id
         where r.season = %s and t.sport = %s
         group by r.as_of_week
         order by r.as_of_week desc
         limit 1
        """,
        (season, sport),
    )
    return dict(rows[0]) if rows else {}


__all__ = [
    "MIN_BOX_PLAYS_TO_RATE",
    "MIN_DROPBACKS_TO_RATE",
    "compute_ratings",
    "season_summary",
]
