"""Tests for the first-quarter derivation's SQL.

No network and no database. The failures worth guarding here all RUN: a write
that forgets the quarter stores full-game numbers in `q1_*`, a write missing
the sport filter derives college plays onto NFL rows, and a check with its own
copy of a definition vouches for a write it never examined. None of those
raises; each produces a plausible table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from worker.core import quarter_stats as qs
from worker.jobs import build_quarter_stats

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20260910120000_first_quarter_actuals.sql"
)


def test_the_write_filters_to_the_quarter_and_the_check_does_not() -> None:
    # The check proves the derivation against FULL-GAME box scores, so it must
    # see every quarter; the write must see one.
    assert "p.period = %(quarter)s" in qs.write_sql()
    assert "period" not in qs.reconcile_sql()


def test_the_write_and_the_check_share_every_stat_definition() -> None:
    write, check = qs.write_sql(), qs.reconcile_sql()
    for stat, expression in qs.STAT_EXPRESSIONS.items():
        assert expression in write, stat
        assert expression in check, stat


def test_the_write_is_scoped_to_one_sport_on_both_halves() -> None:
    # Once on the plays it derives from, once on the rows it updates. Either
    # alone lets the other sport in: college plays onto NFL rows, or NFL
    # figures written as zeros over every college player-game.
    assert qs.write_sql().count("g.sport = %(sport)s") == 2


def test_only_rows_whose_values_change_are_rewritten() -> None:
    sql = qs.write_sql()
    for stat in qs.STATS:
        assert f"s.q1_{stat} is distinct from t.{stat}" in sql


def test_games_without_play_by_play_are_left_null() -> None:
    # Not zeros: a zero is "played and did nothing in the quarter", which a hit
    # rate would grade. A game with no plays is simply not derived.
    assert "exists (select 1 from plays p where p.game_id = s.game_id)" in qs.write_sql()


def test_the_columns_written_are_the_columns_the_migration_adds() -> None:
    added = set(re.findall(r"add column (q1_\w+)", MIGRATION.read_text(encoding="utf-8")))
    assert added == {f"q1_{stat}" for stat in qs.STATS}


def test_no_stray_percent_reaches_psycopg() -> None:
    # psycopg reads every bare % as a placeholder. A LIKE pattern added later
    # would fail only at run time, on the cron.
    for sql in (qs.write_sql(), qs.reconcile_sql(), qs.summary_sql()):
        assert "%" not in re.sub(r"%\(\w+\)s", "", sql)


def test_a_touchdown_counts_only_with_the_same_players_touch() -> None:
    # The passer carries a Touchdown row on every touchdown pass. Counting those
    # would credit a quarterback with every score he throws.
    assert "is_rush and is_touchdown" in qs.STAT_EXPRESSIONS["rush_tds"]
    assert "is_reception and is_touchdown" in qs.STAT_EXPRESSIONS["rec_tds"]
    offensive = qs.STAT_EXPRESSIONS["offensive_tds"]
    assert "is_completion" not in offensive
    assert "is_rush and is_touchdown" in offensive
    assert "is_reception and is_touchdown" in offensive


def test_passing_yards_come_from_completed_plays() -> None:
    assert qs.STAT_EXPRESSIONS["pass_yards"] == "sum(yards_gained) filter (where is_completion)"


@pytest.mark.parametrize(
    ("share", "fails"),
    [(0.9954, False), (0.99, False), (0.9899, True), (None, True)],
)
def test_the_reconcile_threshold(share: float | None, fails: bool) -> None:
    rows = [{"stat": "targets", "exact_share": share}]
    assert (qs.failing_stats(rows) == ["targets"]) is fails


def test_the_sport_must_be_named() -> None:
    # A default would make the sport the derivation was never checked on the
    # silent path.
    with pytest.raises(SystemExit) as exc:
        build_quarter_stats.main(["--seasons", "2025"])
    assert exc.value.code == 2
