"""Usage share (migration 0071).

Scope: the decisions, not the arithmetic. The computation is one SQL statement
and the database is what proves it -- `audit_data` carries the invariants that
need real rows (every share is a fraction, published shares sum to 1 across a
team-game, the withholding rule still holds). What is pinned here is what a
future edit could quietly undo without any of those failing.

No network, no database.
"""

from __future__ import annotations

from worker.core.usage import (
    DERIVED_SHARE_COLUMNS,
    MIN_TARGET_ATTRIBUTION,
    build_usage_shares,
    season_summary,
)


def test_snap_share_is_not_derived_here():
    """It is the provider's `offense_pct`, and it has to stay that way.

    Team offensive snaps are NOT recoverable from `player_game_stats`: a snap
    row exists for every player on the field including the offensive line, a
    box-score row only for players with stats, so the largest count we store is
    a skill player's. Any share derived against it would be too high, in every
    row, with nothing on screen to say so.

    Adding "snap_share" to the derived list is the edit that would do it. This
    fails first.
    """
    assert "snap_share" not in DERIVED_SHARE_COLUMNS
    assert set(DERIVED_SHARE_COLUMNS) == {"target_share", "rush_share"}


def test_the_derived_pass_never_writes_the_snap_column():
    """The same rule, checked against the statement rather than the list.

    Two writers own these four columns -- the snap adapter writes the provider's
    two, this module writes the derived two -- and the split only holds while
    neither reaches into the other's. A `set snap_share = ...` here would
    silently blank every snap share on the first run after an ingest.
    """
    sql = build_usage_shares.__doc__ or ""
    assert "snap_share" not in sql
    # The statement itself, read off the function's code object, is the thing
    # that actually runs.
    statement = next(
        const for const in build_usage_shares.__code__.co_consts
        if isinstance(const, str) and "update player_game_stats" in const
    )
    set_clause = statement.split("update player_game_stats")[1]
    assert "snap_share" not in set_clause


def test_the_target_guard_is_a_measured_threshold_not_a_round_number():
    """0.80 is where the inflation was measured, and moving it needs new data.

    Team targets as a fraction of the same team's pass attempts, on production
    2026-09-17: NFL sits at a 0.96 median and loses one team-game in 570 to
    this; college 2025 sits at 0.91 and loses about one in seven; college 2024
    sits at 0.667 and loses most of the season, which is the honest answer for
    a season whose attribution was that thin.

    Paired within player over cfb 2025 FBS, a receiver's mean target share in
    the team-games under this threshold ran 2.1 points above his own complete
    games -- 2.2 for backs, 0.9 for tight ends. All three inflate, because what
    is missing is whole roster rows rather than a random scatter of targets, so
    the players who do appear absorb the share of those who do not.

    Lowering this publishes that inflation. Raising it throws away sound college
    data. Either is a decision to make against fresh numbers, not in passing.
    """
    assert MIN_TARGET_ATTRIBUTION == 0.80


def test_the_summary_reports_what_was_withheld():
    """The withheld count is the number this module exists to produce.

    A run that quietly stopped withholding -- the guard dropped from the SQL --
    looks like a run with better coverage unless the figure is on the log every
    time. `season_summary` derives it rather than leaving it to be noticed.
    """
    source = season_summary.__code__
    names = set(source.co_names) | set(source.co_consts)
    assert "withheld_targets" in names
