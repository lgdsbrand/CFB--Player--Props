"""N4 -- NFL play-by-play into `plays`, and its attribution into
`play_player_stats`.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The mirror of the two play loaders in
`worker/adapters/cfbd/ingest_stats.py`, and far smaller than them. What the
college version has to do and this does not:

  * **Derive who was involved in a play.** CFBD serves attribution through a
    separate per-game `/plays/stats` fan-out -- 916 calls a season, with a
    silent 2,000-row truncation -- which then has to be joined back to the play.
    nflverse ships `rusher_player_id`, `receiver_player_id` and
    `passer_player_id` as pre-resolved columns ON the play row.

  * **Repair swapped passer/receiver labels.** CFBD credits the PASSER with the
    'Reception' on a subset of touchdown plays, so the college build carries
    `fix_swapped_pass_attribution`. There is no equivalent defect here.

  * **Name-match anybody.** Players join on `gsis_id`, games on `nflverse_id`.

---------------------------------------------------------------------------
THE VOCABULARY IS CFBD'S, ON PURPOSE, AND THAT IS NOT AN ACCIDENT OF HISTORY
---------------------------------------------------------------------------
`stat_type` values written here are 'Rush', 'Reception', 'Target', 'Touchdown',
'Completion' and 'Incompletion' -- CFBD's strings, not nflverse's.

The sport-agnostic core reads those literals directly: `core/splits.py` filters
`stat_type = 'Rush'`, `core/features.py` filters
`stat_type in ('Rush', 'Reception', 'Target')`. So this vocabulary is the
project's internal contract that happens to have been borrowed from the first
provider, and the choice here is between translating NFL into it once, in this
file, or teaching the core two dialects. Translating once is obviously right,
and the alternative -- renaming everything to something neutral -- is a
migration over 360,000 existing rows to buy nothing but tidiness.

WHY EACH OF THE SIX IS EMITTED, since three of them are never read by name:

  * `Rush`, `Reception`  -- carry yards as `stat_value`; summed into
    rush_yards_allowed and rec_yards_allowed.
  * `Target`             -- INCOMPLETIONS ONLY. `splits.py` computes targets as
    receptions + Target rows, so emitting one on a completion double-counts.
  * `Touchdown`          -- on the scorer AND on the passer, exactly as CFBD
    does. The core relies on that shape: it requires the same player to also
    have a Rush or Reception on the same play, which is what stops a
    quarterback being credited with every touchdown he throws. Emitting the
    passer row keeps that compensation meaningful rather than dead code.
  * `Completion`,
    `Incompletion`       -- read by nothing by name, but `splits.py` counts one
    `plays` row per (play, position) and a quarterback's pass plays reach that
    count only through these. Omitting them would leave NFL QB splits with a
    denominator built from scrambles alone.

---------------------------------------------------------------------------
A REAL DIVERGENCE FROM COLLEGE THAT THE MODEL LAYER MUST BE TOLD ABOUT
---------------------------------------------------------------------------
`core/features.py` documents a "KNOWN DISTORTION": CFBD emits `Target` rows on
only some incompletions, so college receivers show 386 receptions against 72
targets inside the ten -- an implied 84% catch rate where the truth is nearer
50%. It argues, correctly, that this does not bias the projection because the
two errors cancel.

**That distortion does not exist in this data, and the cancellation therefore
does not apply.** Measured on 2025: 19,737 pass plays, 17,582 carrying a
receiver, 11,749 complete -- an implied catch rate of 66.8%, which is simply the
real one. NFL targets are complete.

This is good, and it is also a trap for N5: the same goal-line decomposition
run over both sports produces numbers that mean different things, and the
college one is the distorted one. Anything that compares a college conversion
rate with an NFL conversion rate is comparing an artifact with a measurement.

---------------------------------------------------------------------------
`plays` IS FILTERED FOR NFL AND COMPLETE FOR COLLEGE
---------------------------------------------------------------------------
Only plays carrying at least one of the three skill ids are stored -- 35,166 of
48,771 in 2025, so 28% of rows are dropped. Kickoffs, punts, field goals, timeouts,
penalties with no snap and end-of-quarter markers have no attribution and no
consumer: every read of `plays` in this project joins it to
`play_player_stats` (`splits.py`, `features.py`), and the audit's checks over
`plays` ask only that a completed game HAS play-by-play and that season and week
agree with the game.

It is a storage decision and it is worth saying plainly rather than discovering
later: production had 63 MB of headroom under the 500 MB tier when this was
written, and three unfiltered seasons did not fit. A future consumer that wants
a complete NFL play log -- special-teams modelling, drive charts -- must reload,
not assume.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from worker.adapters.nflverse.client import NflverseClient
from worker.adapters.nflverse.ingest_stats import (
    load_game_index,
    load_player_index,
    load_team_index,
)
from worker.adapters.nflverse.mapping import (
    SPORT,
    canonical_team,
    int_or_none,
    text_or_none,
)
from worker.db import copy_into, execute, fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

#: Column order for the `plays` COPY. `cfbd_id` is absent -- an NFL play has
#: none, and the column is nullable precisely so this list can omit it.
PLAY_COLUMNS = (
    "nflverse_play_id", "game_id", "season", "week",
    "offense_team_id", "defense_team_id",
    "period", "clock_seconds", "down", "distance",
    "yards_to_goal", "yards_gained", "play_type", "play_text",
    "scoring", "ppa", "offense_score", "defense_score",
)

#: Identical to the college one -- the same table, written the same way.
PLAY_PLAYER_COLUMNS = (
    "play_id", "game_id", "player_id", "team_id", "opponent_team_id",
    "season", "week", "position_group", "stat_type", "stat_value",
)

#: The three roles nflverse resolves on the play row itself.
SKILL_ID_COLUMNS = (
    "rusher_player_id",
    "receiver_player_id",
    "passer_player_id",
)

#: Source columns read per play. Named rather than taking the whole 372-column
#: frame, so a renamed upstream column fails here instead of arriving as null.
SOURCE_COLUMNS = (
    "play_id", "game_id", "season", "week", "posteam", "defteam",
    "qtr", "quarter_seconds_remaining", "down", "ydstogo", "yardline_100",
    "yards_gained", "play_type", "desc", "sp", "epa",
    "posteam_score", "defteam_score",
    "rushing_yards", "receiving_yards",
    "complete_pass", "rush_touchdown", "pass_touchdown",
    *SKILL_ID_COLUMNS,
)


def _int(value: Any) -> int | None:
    return int_or_none(value)


def _float(value: Any) -> float | None:
    """A numeric cell as a float, treating blank and NA as absence.

    `epa` is genuinely null on plays the model does not score (kneels, some
    penalties), and null is the honest value: `splits.py` sums it, and a zero
    would be a measured "this play was exactly neutral".
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # NaN


def load_position_index(season: int) -> dict[int, str]:
    """our player id -> position group as of THIS season.

    Denormalized onto every attribution row for the reason the schema comment
    gives: it pins the position as of the season of the play, so a player who
    changes position later cannot retroactively rewrite historical splits.
    """
    rows = fetch_all(
        """
        select distinct on (pts.player_id) pts.player_id, pts.position_group
          from player_team_seasons pts
          join players p on p.id = pts.player_id and p.sport = %s
         where pts.season = %s
         order by pts.player_id, pts.id
        """,
        (SPORT, season),
    )
    return {r["player_id"]: r["position_group"] for r in rows}


def select_source_rows(frame: pl.DataFrame) -> pl.DataFrame:
    """Plays carrying at least one skill id -- see the module docstring.

    Done in polars rather than row-by-row because it discards 28% of the frame
    before any Python object is built for it.
    """
    present = [c for c in SKILL_ID_COLUMNS if c in frame.columns]
    if not present:
        raise ValueError(
            "play-by-play carries none of "
            f"{SKILL_ID_COLUMNS} -- the upstream schema has changed"
        )
    missing = [c for c in SOURCE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"play-by-play is missing expected column(s): {missing}")

    keep = pl.any_horizontal([pl.col(c).is_not_null() for c in present])
    return frame.filter(keep).select(SOURCE_COLUMNS)


def build_play_rows(
    frame: pl.DataFrame,
    season: int,
    games: dict[str, dict[str, Any]],
    teams: dict[str, int],
    counts: Any,
) -> tuple[list[tuple[Any, ...]], set[int]]:
    """`plays` COPY tuples, and the set of our game ids they cover.

    The game id set is returned rather than recomputed, because it is what the
    scoped DELETE below keys on and the two must not be derived separately --
    the college build's cascade bug came from exactly that kind of drift.
    """
    rows: list[tuple[Any, ...]] = []
    touched: set[int] = set()

    for row in frame.to_dicts():
        game = games.get(text_or_none(row.get("game_id")) or "")
        if game is None:
            counts.skip("play: game not in database")
            continue

        offense = canonical_team(row.get("posteam"))
        defense = canonical_team(row.get("defteam"))
        offense_id = teams.get(offense) if offense else None
        defense_id = teams.get(defense) if defense else None
        if offense_id is None or defense_id is None:
            # A play with no possession team is a timeout or a period marker.
            # It cannot carry a skill id, so reaching here means the filter let
            # something through and that is worth counting, not ignoring.
            counts.skip("play: unresolved team")
            continue

        play_id = _int(row.get("play_id"))
        if play_id is None:
            counts.skip("play: missing play_id")
            continue

        touched.add(game["id"])
        rows.append((
            play_id,
            game["id"],
            season,
            # From our games row, never from this file -- one source for the
            # season axis. See adapters/nflverse/ingest_stats.py.
            game["week"],
            offense_id,
            defense_id,
            _int(row.get("qtr")),
            _int(row.get("quarter_seconds_remaining")),
            _int(row.get("down")),
            _int(row.get("ydstogo")),
            _int(row.get("yardline_100")),
            _int(row.get("yards_gained")),
            text_or_none(row.get("play_type")),
            text_or_none(row.get("desc")),
            bool(_int(row.get("sp")) or 0),
            _float(row.get("epa")),
            _int(row.get("posteam_score")),
            _int(row.get("defteam_score")),
        ))

    return rows, touched


def _attribution_for(row: dict[str, Any]) -> list[tuple[str, str, float | None]]:
    """(gsis_id, stat_type, stat_value) triples for one source play.

    Reads only the pre-resolved role columns. The rules are the module
    docstring's, in the order a play produces them.
    """
    out: list[tuple[str, str, float | None]] = []

    rusher = text_or_none(row.get("rusher_player_id"))
    receiver = text_or_none(row.get("receiver_player_id"))
    passer = text_or_none(row.get("passer_player_id"))
    complete = bool(_int(row.get("complete_pass")) or 0)

    if rusher:
        # Sacks carry a null `rushing_yards` in this source, so a sack never
        # produces a Rush row. That is the same exclusion the college engine
        # makes deliberately -- see the "RUSHING EXCLUDES SACKS" note in
        # core/splits.py -- arriving here for free rather than by a rule.
        yards = _float(row.get("rushing_yards"))
        if yards is not None:
            out.append((rusher, "Rush", yards))
            if _int(row.get("rush_touchdown")):
                out.append((rusher, "Touchdown", None))

    if receiver:
        if complete:
            out.append((receiver, "Reception", _float(row.get("receiving_yards"))))
            if _int(row.get("pass_touchdown")):
                out.append((receiver, "Touchdown", None))
        else:
            # Incompletions only. A Target on a completion would be counted
            # twice by splits.py, which reads targets as receptions + Targets.
            out.append((receiver, "Target", None))

    if passer:
        out.append((passer, "Completion" if complete else "Incompletion", None))
        if complete and _int(row.get("pass_touchdown")):
            # Mirrors CFBD: the passer gets a Touchdown row too. The core
            # excludes it by requiring a Rush or Reception on the same play.
            out.append((passer, "Touchdown", None))

    return out


def build_attribution_rows(
    frame: pl.DataFrame,
    season: int,
    games: dict[str, dict[str, Any]],
    teams: dict[str, int],
    players: dict[str, int],
    positions: dict[int, str],
    play_ids: dict[tuple[int, int], int],
    counts: Any,
) -> list[tuple[Any, ...]]:
    """`play_player_stats` COPY tuples.

    `play_ids` maps (our game id, nflverse play id) -> our play id, read back
    after the plays COPY. That pair is the key because nflverse's play_id is
    unique only within a game -- see migration 0051.
    """
    rows: list[tuple[Any, ...]] = []
    seen: set[tuple[int, int, str]] = set()

    for row in frame.to_dicts():
        game = games.get(text_or_none(row.get("game_id")) or "")
        if game is None:
            continue

        source_play = _int(row.get("play_id"))
        our_play = play_ids.get((game["id"], source_play)) if source_play else None
        if our_play is None:
            counts.skip("attribution: play not stored")
            continue

        offense = canonical_team(row.get("posteam"))
        defense = canonical_team(row.get("defteam"))
        team_id = teams.get(offense) if offense else None
        opponent_id = teams.get(defense) if defense else None
        if team_id is None or opponent_id is None:
            counts.skip("attribution: unresolved team")
            continue

        for gsis_id, stat_type, stat_value in _attribution_for(row):
            player_id = players.get(gsis_id)
            if player_id is None:
                # A player who appears in the play-by-play but on no roster we
                # loaded. Counted rather than dropped silently: a large number
                # means the roster ingest and the play-by-play disagree.
                counts.skip("attribution: player not on any roster")
                continue

            # unique (play_id, player_id, stat_type). COPY has no ON CONFLICT,
            # so duplicates must not reach Postgres. A player can legitimately
            # hold two roles on one play -- a receiver who laterals, a passer on
            # a trick play who is also the rusher -- so this is not defensive.
            key = (our_play, player_id, stat_type)
            if key in seen:
                counts.skip("attribution: duplicate key")
                continue
            seen.add(key)

            rows.append((
                our_play,
                game["id"],
                player_id,
                team_id,
                opponent_id,
                season,
                game["week"],
                positions.get(player_id, "OTHER"),
                stat_type,
                stat_value,
            ))

    return rows


def _read_back_play_ids(game_ids: list[int]) -> dict[tuple[int, int], int]:
    if not game_ids:
        return {}
    rows = fetch_all(
        """
        select id, game_id, nflverse_play_id
          from plays
         where game_id = any(%s) and nflverse_play_id is not null
        """,
        (game_ids,),
    )
    return {(r["game_id"], r["nflverse_play_id"]): r["id"] for r in rows}


def run_nfl_plays_ingest(
    client: NflverseClient, season: int, counts: Any, *, max_age: float | None
) -> dict[str, int]:
    """Load one season of NFL play-by-play and its attribution."""
    frame = client.fetch("play_by_play", season, max_age=max_age)
    log.info("plays %d: %d source row(s), %d column(s)", season, frame.height, frame.width)

    selected = select_source_rows(frame)
    log.info(
        "plays %d: %d row(s) carry a skill id, %d dropped",
        season, selected.height, frame.height - selected.height,
    )

    games = load_game_index(season)
    if not games:
        raise ValueError(
            f"no NFL games stored for {season} -- run nfl_ingest_reference first"
        )
    teams = load_team_index()
    players = load_player_index()
    positions = load_position_index(season)

    play_rows, touched = build_play_rows(selected, season, games, teams, counts)
    game_ids = sorted(touched)

    # DELETE SCOPED TO THE GAMES BEING WRITTEN, never to the season.
    # `plays.id` is `generated always as identity` and `play_player_stats`
    # cascades off it, so a season-wide delete destroys every attribution row
    # for the season and reissues every surviving play a new id. The college
    # build nearly lost a season to exactly this; the scope is the fix, and both
    # deletes below take the SAME game list rather than deriving it twice.
    if game_ids:
        deleted = execute(
            "delete from plays where season = %s and game_id = any(%s)",
            (season, game_ids),
        )
        if deleted:
            log.info("plays %d: cleared %d existing row(s)", season, deleted)

    n_plays = copy_into("plays", PLAY_COLUMNS, play_rows)
    counts.add("plays", n_plays)
    log.info("plays %d: %d rows", season, n_plays)

    play_ids = _read_back_play_ids(game_ids)
    if len(play_ids) != n_plays:
        # Not fatal on its own, but it means the read-back key is not unique the
        # way migration 0051 asserts, and every attribution row depends on it.
        log.warning(
            "plays %d: wrote %d rows but read back %d distinct keys",
            season, n_plays, len(play_ids),
        )

    attribution = build_attribution_rows(
        selected, season, games, teams, players, positions, play_ids, counts
    )
    n_attr = copy_into("play_player_stats", PLAY_PLAYER_COLUMNS, attribution)
    counts.add("play_player_stats", n_attr)
    log.info("play_player_stats %d: %d rows", season, n_attr)

    return {"plays": n_plays, "play_player_stats": n_attr}


__all__ = [
    "PLAY_COLUMNS",
    "PLAY_PLAYER_COLUMNS",
    "SKILL_ID_COLUMNS",
    "SOURCE_COLUMNS",
    "build_attribution_rows",
    "build_play_rows",
    "load_position_index",
    "run_nfl_plays_ingest",
    "select_source_rows",
]
