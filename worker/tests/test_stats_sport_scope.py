"""The college stats adapter must never touch another sport's rows.

`plays`, `play_player_stats` and `player_game_stats` carry `season` but not
`sport`, and hold NFL rows from 2023. Until 2026-09-24 the CFBD stats adapter
filtered on season alone: the daily college run overwrote every NFL 2026
target with the player's receptions, a 2023 backfill crashed on NFL postseason
weeks, and a full-season run would have deleted a whole NFL season of plays.

Read off the source rather than exercised against a database, because the
failure is a missing clause and a clause can be checked for directly. Every
SQL string in the module that filters on `season` must also name the sport.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import worker.adapters.cfbd.ingest_stats as ingest_stats

SOURCE = Path(ingest_stats.__file__).read_text(encoding="utf-8")
SEASON_FILTER = re.compile(r"\bseason\s*=\s*%")


def _sql_strings() -> list[str]:
    tree = ast.parse(SOURCE)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and SEASON_FILTER.search(node.value)
        and re.search(r"\b(select|delete|update)\b", node.value, re.IGNORECASE)
    ]


def test_the_parser_finds_the_statements():
    # Guards the guard: an empty list would pass the test below vacuously.
    assert len(_sql_strings()) >= 10


def test_every_season_filter_is_scoped_to_the_sport():
    unscoped = [sql for sql in _sql_strings() if "sport" not in sql]
    assert not unscoped, "SQL filtering on season without the sport:\n\n" + (
        "\n---\n".join(unscoped)
    )


def test_the_adapter_is_the_college_one():
    assert ingest_stats.SPORT == "cfb"
