"""reprice_picks — a stored projection prices a line exactly as the live run did.

Checked against production on 2026-10-19 inside a rolled-back transaction:
re-pricing every upcoming game reproduced all 574 NFL picks and every college
pick outside the 8 games whose lines had moved since the 12:30 UTC run. These
offline tests pin the part that can drift without a database: rebuilding the
distribution from the stored family and parameters.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from worker.core.models import Projection, gamma_params, negative_binomial_params
from worker.jobs.reprice_picks import stored_row


def _record(distribution: str, params: dict[str, float], **extra) -> dict:
    return {
        "player_id": 11, "game_id": 22, "team_id": 33, "opponent_team_id": 44,
        "market_key": "rec_yards", "season": 2026, "week": 8, "as_of_week": 8,
        "distribution": distribution,
        # psycopg returns jsonb numbers as int/float; Decimal is what a numeric
        # column would give, and the rebuild must not care which.
        "params": params, "mean": Decimal("54.2"), **extra,
    }


@pytest.mark.parametrize(
    ("distribution", "params"),
    [
        ("gamma", gamma_params(54.2, 30.0)),
        ("negative_binomial", negative_binomial_params(4.6, 1.8)),
    ],
)
def test_stored_row_prices_every_line_like_the_original(distribution, params):
    original = Projection("rec_yards", distribution, params, mean=54.2)
    row = stored_row(_record(distribution, {k: Decimal(str(v)) for k, v in params.items()}))
    for line in (0.5, 3.5, 39.5, 54.5, 80.5):
        assert row.projection.probability_over(line) == pytest.approx(
            original.probability_over(line), abs=1e-9
        )


def test_stored_row_carries_the_ids_the_pick_is_written_with():
    row = stored_row(_record("gamma", gamma_params(54.2, 30.0)))
    assert (row.player_id, row.game_id, row.team_id, row.opponent_team_id) == (11, 22, 33, 44)
    assert (row.market_key, row.season, row.week) == ("rec_yards", 2026, 8)
