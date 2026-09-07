"""N5a -- NFL snap counts onto `player_game_stats.snaps`.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). nflverse publishes snap counts as a
release asset of their own, separate from `stats_player_week`, which is why N3
left `snaps` null and said so rather than inventing it from carries + targets.
The usage floor the projection step applies needs a real snap number, and a
floor that means "snaps" for one sport and "touches" for the other is a filter
that silently changes definition at the sport seam.

TWO EXACT JOINS, NO NAME MATCHING. The file carries nflverse's own game key
(`2025_01_ARI_NO`) in `game_id`, which is `games.nflverse_id`, and PFR's player
id in `pfr_player_id`, which is `players.pfr_id` (migration 0053). So this
resolves entirely on keys. That also keeps it clear of the week axis: the file
runs to week 22 and carries four postseason `game_type`s, and joining on the
game means the postseason-week question is answered once, in `games`, rather
than re-derived here -- which is how bowl games once landed in week 1.

UPDATE ONLY, NEVER INSERT. A snap row exists for every player who took one,
including the offensive line; a box-score row exists only for players with
stats. Inserting would manufacture `player_game_stats` rows with no stats and
no team, which the table's own constraints reject anyway. Snaps attach to a
line that already exists, or they are counted as unattached and dropped.

`started` STAYS NULL. The file has no such column, and deriving it from a snap
share would be a threshold we invented presented as a fact the source supplied.

EXPECT ROUGHLY A FIFTH OF THE SOURCE ROWS NOT TO RESOLVE, AND DO NOT "FIX" IT.
Measured on 2025 (dev, 2026-09-07): 26,612 source rows, 21,592 resolved, and
**97% of the 5,020 that did not are offensive line and long snappers** -- OL
1,448, T 1,363, G 925, C 668, LS 474. The cause is upstream: only 6 of the 949
linemen in our players table carry a pfr_id at all, because the nflverse roster
supplies one far less often for them than for skill players.

That gap costs this project nothing. It models QB/RB/WR/TE (CLAUDE.md §6), and
coverage on those, measured the same day, is QB 99.9%, RB 99.8%, WR 99.7%,
TE 100.0% -- 14 skill rows of 6,394 without a snap count. A headline resolution
rate is the wrong number to judge this by; the per-position one is the right one.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from worker.adapters.nflverse.client import NflverseClient
from worker.adapters.nflverse.mapping import SPORT, int_or_none, text_or_none
from worker.db import execute, fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

#: The column we take. `defense_snaps` and `st_snaps` are deliberately not
#: stored: every market this project models is an offensive one, and a column
#: nothing reads is a column that goes stale without anyone noticing.
SNAP_COLUMN = "offense_snaps"


def load_pfr_index(counts: Any) -> dict[str, int]:
    """pfr_id -> our player id, with ambiguous ids EXCLUDED rather than picked.

    `players.pfr_id` is indexed, not unique, and migration 0053 records why:
    measured over the 2023-2026 rosters, `YounBy01` covers two different
    players. Resolving that to whichever row the query returned first would
    attach one player's snap counts to another and never say so. An ambiguous
    id resolves to nothing and is counted.
    """
    rows = fetch_all(
        "select id, pfr_id from players where sport = %s and pfr_id is not null",
        (SPORT,),
    )
    index: dict[str, int] = {}
    ambiguous: set[str] = set()
    for row in rows:
        pfr = row["pfr_id"]
        if pfr in index and index[pfr] != row["id"]:
            ambiguous.add(pfr)
        index[pfr] = row["id"]
    for pfr in ambiguous:
        del index[pfr]
        counts.skip("snaps: ambiguous pfr_id")
    if ambiguous:
        log.warning(
            "snaps: %d pfr_id(s) map to more than one player and were "
            "excluded: %s", len(ambiguous), sorted(ambiguous),
        )
    return index


def load_game_ids() -> dict[str, int]:
    """nflverse game key -> our game id."""
    rows = fetch_all(
        "select id, nflverse_id from games "
        " where sport = %s and nflverse_id is not null",
        (SPORT,),
    )
    return {r["nflverse_id"]: r["id"] for r in rows}


def build_pairs(
    frame: pl.DataFrame,
    games: dict[str, int],
    players: dict[str, int],
    counts: Any,
) -> list[tuple[int, int, int]]:
    """(player_id, game_id, snaps) for every row that resolves on both keys."""
    pairs: dict[tuple[int, int], int] = {}
    for row in frame.to_dicts():
        pfr = text_or_none(row.get("pfr_player_id"))
        if pfr is None:
            counts.skip("snaps: no pfr_player_id")
            continue
        player_id = players.get(pfr)
        if player_id is None:
            counts.skip("snaps: player not resolved")
            continue

        key = text_or_none(row.get("game_id"))
        game_id = games.get(key) if key else None
        if game_id is None:
            counts.skip("snaps: game not resolved")
            continue

        snaps = int_or_none(row.get(SNAP_COLUMN))
        if snaps is None:
            counts.skip("snaps: blank offense_snaps")
            continue

        # A player traded mid-game does not exist, but a player can appear on
        # two rows of one game_id in the source when a franchise abbreviation
        # changed. Sum rather than let one silently win: snaps are additive and
        # a lost half would understate usage, which is the one direction the
        # floor must not be wrong in.
        prior = pairs.get((player_id, game_id))
        pairs[(player_id, game_id)] = snaps if prior is None else prior + snaps

    return [(p, g, s) for (p, g), s in pairs.items()]


def run_nfl_snaps_ingest(
    client: NflverseClient, season: int, counts: Any, *, max_age: float | None
) -> int:
    """Load one season of NFL snap counts. Returns box-score rows updated."""
    frame = client.fetch("snap_counts", season, max_age=max_age)

    games = load_game_ids()
    if not games:
        raise ValueError(
            f"no NFL games stored for {season} -- run nfl_ingest_reference first"
        )
    players = load_pfr_index(counts)
    if not players:
        raise ValueError(
            "no players carry a pfr_id -- run nfl_ingest_reference after "
            "migration 0053 so the snap join has a key to resolve on"
        )

    pairs = build_pairs(frame, games, players, counts)
    log.info(
        "snaps %d: %d source row(s) -> %d resolved (player, game) pair(s)",
        season, frame.height, len(pairs),
    )
    if not pairs:
        return 0

    params = {
        "players": [p for p, _, _ in pairs],
        "games": [g for _, g, _ in pairs],
        "snaps": [s for _, _, s in pairs],
    }
    values = """
        select unnest(%(players)s::bigint[])  as player_id,
               unnest(%(games)s::bigint[])    as game_id,
               unnest(%(snaps)s::smallint[])  as snaps
    """

    # MATCHED IS COUNTED SEPARATELY FROM CHANGED, and the first version did not
    # do this. The UPDATE carries `snaps is distinct from` so a re-run does not
    # rewrite rows that are already correct -- which means its rowcount is rows
    # CHANGED. Deriving "pairs with no box-score line" from it read as 21,601 of
    # 21,620 missing on the second pass over 2025, when the true answer was that
    # 21,601 were already right. A log that reports total data loss on a
    # successful idempotent re-run is worse than no log.
    matched = fetch_all(
        f"""
        select count(*) as n
          from player_game_stats s
          join ({values}) v
            on s.player_id = v.player_id and s.game_id = v.game_id
        """,
        params,  # a Mapping: psycopg takes named params here, as `execute` does
    )[0]["n"]

    changed = execute(
        f"""
        update player_game_stats s
           set snaps = v.snaps
          from ({values}) v
         where s.player_id = v.player_id
           and s.game_id   = v.game_id
           and s.snaps is distinct from v.snaps
        """,
        params,
    )
    counts.add("player_game_stats.snaps", changed)
    # The gap between matched and resolved is expected and is not an error: it
    # is the offensive line and the defence, who take snaps and have no
    # box-score line. A SUDDEN change in it is the signal worth seeing.
    log.info(
        "snaps %d: %d changed, %d already current, %d pair(s) with no "
        "box-score line (linemen and defence)",
        season, changed, matched - changed, len(pairs) - matched,
    )
    return changed


__all__ = [
    "SNAP_COLUMN",
    "build_pairs",
    "load_game_ids",
    "load_pfr_index",
    "run_nfl_snaps_ingest",
]
