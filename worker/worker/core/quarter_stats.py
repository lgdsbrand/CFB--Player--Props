"""First-quarter actuals, derived from play attribution.

SPORT-AGNOSTIC CORE (CLAUDE.md §3). Reads `plays` and `play_player_stats`
through the shared attribution vocabulary -- 'Rush', 'Reception', 'Target',
'Touchdown', 'Completion' -- and writes the `q1_*` columns of
`player_game_stats` (migration 20260910120000). No provider serves per-player
quarter stats, and every play already carries its quarter, so they are built
here.

ONE DERIVATION, TWO USES. `derived_sql` is the only place a stat is defined.
Filtered to a quarter it is what `write_sql` stores; unfiltered it is what
`reconcile_sql` compares with the box score. A write and a check that each
spelled out "receiving yards" would be free to disagree, and the check would
then prove nothing about the write.

MEASURED BEFORE ANY COLUMN EXISTED, on 2025 NFL player-games (19,400 on
production), all quarters against the box score: completions, pass TDs,
carries, rush TDs, receptions and receiving TDs match on every row; passing
and rushing yards on 99.99%; receiving yards on 99.90% (worst gap 33, a
lateral); targets on 99.54%, every row within 2.

VERIFIED ON THE NFL ONLY. CFBD's attribution has documented defects -- the
passer credited with the reception on some touchdowns, Target rows on only some
incompletions -- so a college season must pass `reconcile_sql` before its
figures are trusted. `build_quarter_stats` makes the sport an explicit choice
for that reason.

TWO DEFINITIONS WORTH KNOWING.

  * PASSING YARDS COME FROM THE PLAY, not the attribution row. NFL `Completion`
    rows carry no `stat_value`, so a passer's yards are `plays.yards_gained` on
    the plays he completed. A sack is not a completion, so it never reaches it.
  * A TOUCHDOWN NEEDS A TOUCH. The passer also gets a `Touchdown` row on a
    touchdown pass, so a rushing or receiving TD requires the same player's
    Rush or Reception on that play -- the rule `core/features.py` applies for
    the anytime-TD model, and the `offensive_tds` definition in migration 0004.
"""

from __future__ import annotations

from typing import Any

from worker.db import execute, fetch_all, fetch_one

#: The quarter the `q1_*` columns hold.
FIRST_QUARTER = 1

#: Stat -> how it aggregates one player's plays in one game. Each name is a
#: `player_game_stats` column and, prefixed `q1_`, its first-quarter twin. The
#: flags and yard sums it reads are per play; see `derived_sql`.
STAT_EXPRESSIONS: dict[str, str] = {
    "pass_yards": "sum(yards_gained) filter (where is_completion)",
    "rush_attempts": "count(*) filter (where is_rush)",
    "rush_yards": "sum(rush_yards) filter (where is_rush)",
    "rush_tds": "count(*) filter (where is_rush and is_touchdown)",
    "targets": "count(*) filter (where is_reception or is_target)",
    "receptions": "count(*) filter (where is_reception)",
    "rec_yards": "sum(rec_yards) filter (where is_reception)",
    "rec_tds": "count(*) filter (where is_reception and is_touchdown)",
    # Rushing TDs plus receiving TDs, the way `offensive_tds` is generated.
    # Two filters summed rather than one, so a play carrying both a Rush and a
    # Reception counts exactly as the box-score column would.
    "offensive_tds": (
        "count(*) filter (where is_rush and is_touchdown)"
        " + count(*) filter (where is_reception and is_touchdown)"
    ),
}

STATS: tuple[str, ...] = tuple(STAT_EXPRESSIONS)

#: A stat must match the box score on at least this share of player-games for
#: a reconcile to pass. Targets, the loosest, measured 99.54% on the NFL.
RECONCILE_MIN_EXACT_SHARE = 0.99

#: The player-games a run may touch: one sport, one season, and only games
#: with play-by-play. A game without it is left NULL -- "not derived" -- rather
#: than written as a row of zeros that a hit rate would grade as a quiet game.
_SCOPED_PLAYER_GAMES = """
    select s.*
      from player_game_stats s
      join games g on g.id = s.game_id and g.sport = %(sport)s
     where s.season = %(season)s
       and exists (select 1 from plays p where p.game_id = s.game_id)
"""


def derived_sql(*, quarter: bool) -> str:
    """Per (player, game) stats built from attribution, optionally for one quarter.

    Aggregated per PLAY first, then per player-game. A player can hold several
    rows on one play (a Reception and a Touchdown), and flags taken per play are
    what let "a touchdown needs a touch" be a condition on the same play rather
    than a count across the game.
    """
    period = "and p.period = %(quarter)s" if quarter else ""
    columns = ",\n           ".join(
        f"{expression} as {stat}" for stat, expression in STAT_EXPRESSIONS.items()
    )
    return f"""
    with per_play as (
      select pps.player_id,
             pps.game_id,
             pps.play_id,
             p.yards_gained,
             bool_or(pps.stat_type = 'Completion') as is_completion,
             bool_or(pps.stat_type = 'Rush')       as is_rush,
             bool_or(pps.stat_type = 'Reception')  as is_reception,
             bool_or(pps.stat_type = 'Target')     as is_target,
             bool_or(pps.stat_type = 'Touchdown')  as is_touchdown,
             sum(pps.stat_value) filter (where pps.stat_type = 'Rush')      as rush_yards,
             sum(pps.stat_value) filter (where pps.stat_type = 'Reception') as rec_yards
        from play_player_stats pps
        join plays p on p.id = pps.play_id
        join games g on g.id = pps.game_id and g.sport = %(sport)s
       where pps.season = %(season)s
         {period}
       group by pps.player_id, pps.game_id, pps.play_id, p.yards_gained
    )
    select player_id,
           game_id,
           {columns}
      from per_play
     group by player_id, game_id
    """


def write_sql() -> str:
    """The UPDATE that stores one season's first-quarter stats.

    ONLY ROWS WHOSE VALUES CHANGE ARE REWRITTEN. The NFL chain runs daily and
    re-derives the whole season each time; an unconditional UPDATE would leave
    ~19,000 dead tuples a day for autovacuum, against a storage guard that
    already watches `nfl_ingest_plays`'s churn.

    A player-game in scope with no plays in the quarter gets zeros: he played
    the game and did nothing in the first quarter, which is a real outcome.
    """
    targets = ",\n             ".join(
        f"coalesce(d.{stat}, 0)::smallint as {stat}" for stat in STATS
    )
    assignments = ",\n           ".join(f"q1_{stat} = t.{stat}" for stat in STATS)
    changed = "\n        or ".join(
        f"s.q1_{stat} is distinct from t.{stat}" for stat in STATS
    )
    return f"""
    with derived as ({derived_sql(quarter=True)}),
    scope as ({_SCOPED_PLAYER_GAMES}),
    target as (
      select sc.id,
             {targets}
        from scope sc
        left join derived d
          on d.player_id = sc.player_id and d.game_id = sc.game_id
    )
    update player_game_stats s
       set {assignments}
      from target t
     where s.id = t.id
       and (   {changed})
    """


def reconcile_sql() -> str:
    """Every stat derived over ALL quarters, set against the box score.

    One row per stat: player-games compared, both totals, the share matching
    exactly and the worst gap. This is the evidence that the quarter figures
    can be trusted, and the thing to run before trusting them for a new sport.
    """
    selects = "\n    union all\n".join(
        f"""
    select '{stat}' as stat,
           count(*) as player_games,
           sum(coalesce(b.{stat}, 0)) as box_total,
           sum(coalesce(d.{stat}, 0)) as derived_total,
           avg((coalesce(b.{stat}, 0) = coalesce(d.{stat}, 0))::int) as exact_share,
           max(abs(coalesce(b.{stat}, 0) - coalesce(d.{stat}, 0))) as worst_gap
      from box b
      left join derived d
        on d.player_id = b.player_id and d.game_id = b.game_id"""
        for stat in STATS
    )
    return f"""
    with derived as ({derived_sql(quarter=False)}),
    box as ({_SCOPED_PLAYER_GAMES})
    {selects}
    """


def summary_sql() -> str:
    """Stored first-quarter totals beside the full-game totals of the same rows."""
    columns = ",\n           ".join(
        f"sum(s.q1_{stat}) as q1_{stat}, "
        f"sum(s.{stat}) filter (where s.q1_{stat} is not null) as full_{stat}"
        for stat in STATS
    )
    return f"""
    select count(*) as player_games,
           count(*) filter (where s.q1_receptions is not null) as derived,
           {columns}
      from player_game_stats s
      join games g on g.id = s.game_id and g.sport = %(sport)s
     where s.season = %(season)s
    """


def build_first_quarter_stats(season: int, sport: str) -> int:
    """Store one season's first-quarter stats. Returns rows changed."""
    return execute(
        write_sql(), {"season": season, "sport": sport, "quarter": FIRST_QUARTER}
    )


def reconcile(season: int, sport: str) -> list[dict[str, Any]]:
    """The all-quarter derivation against the box score, one row per stat."""
    return fetch_all(reconcile_sql(), {"season": season, "sport": sport})


def season_summary(season: int, sport: str) -> dict[str, Any]:
    return fetch_one(summary_sql(), {"season": season, "sport": sport}) or {}


def failing_stats(rows: list[dict[str, Any]]) -> list[str]:
    """Stats a reconcile does not vouch for.

    A stat with nothing to compare fails too. "No player-games in scope" cannot
    show a derivation is right, and a check that passes on no evidence reads
    exactly like one that passed on some.
    """
    return [
        row["stat"]
        for row in rows
        if row.get("exact_share") is None
        or float(row["exact_share"]) < RECONCILE_MIN_EXACT_SHARE
    ]


__all__ = [
    "FIRST_QUARTER",
    "RECONCILE_MIN_EXACT_SHARE",
    "STATS",
    "STAT_EXPRESSIONS",
    "build_first_quarter_stats",
    "derived_sql",
    "failing_stats",
    "reconcile",
    "reconcile_sql",
    "season_summary",
    "summary_sql",
    "write_sql",
]
