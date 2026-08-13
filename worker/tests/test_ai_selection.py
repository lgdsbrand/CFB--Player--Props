"""Which players get an AI read when the cap cannot cover the slate.

THE BUG THIS GUARDS AGAINST PRODUCED NO SYMPTOM. `generate_ai_reads` ordered
players with `sorted(board)` — the player id — and stopped at
`ai_reads_max_per_run`. That is ~400 against ~1,200 projected players, so every
read went to the 400 lowest ids and the same two-thirds of the slate were never
reached, week after week, while the run reported a healthy 400 generated and
exited 0.

The order now mirrors the board's own default sort (edge desc, then confidence
desc — `web/lib/core/board-view.ts::boardSortKeys`), because the reads worth
paying for are the ones a reader will open.

All offline: `selection_rank` takes rows and returns a sort key.
"""

from __future__ import annotations

from typing import Any

from worker.jobs.generate_ai_reads import _describe_rank, selection_rank


def rows(**kwargs: Any) -> list[dict]:
    base = {
        "has_call": True,
        "edge": 0.06,
        "confidence": 0.62,
    }
    base.update(kwargs)
    return [base]


def order(board: dict[int, list[dict]]) -> list[int]:
    return sorted(board, key=lambda pid: selection_rank(pid, board[pid]))


def test_higher_edge_is_read_first():
    board = {
        10: rows(edge=0.02),
        20: rows(edge=0.11),
        30: rows(edge=0.07),
    }
    assert order(board) == [20, 30, 10]


def test_the_player_id_no_longer_decides():
    # The exact failure: ids ascending, value descending. Ordering by id would
    # return [1, 2, 3] and give the cap to the worst three rows on the board.
    board = {
        1: rows(edge=0.01, confidence=0.51),
        2: rows(edge=0.05, confidence=0.58),
        3: rows(edge=0.20, confidence=0.71),
    }
    assert order(board) == [3, 2, 1]


def test_calls_come_before_bare_leans():
    # A lean has no market to disagree with, so its read is both the least
    # likely to be opened and the least costly to miss.
    board = {
        10: rows(has_call=False, edge=None, confidence=0.95),
        20: rows(has_call=True, edge=-0.30, confidence=0.51),
    }
    assert order(board) == [20, 10]


def test_a_missing_edge_sorts_last_within_its_group_not_as_zero():
    # Treating "no book line" as zero edge would rank it above every genuinely
    # negative edge, which is a disagreement the model actually expressed.
    board = {
        10: rows(edge=None, confidence=0.90),
        20: rows(edge=-0.04, confidence=0.55),
    }
    assert order(board) == [20, 10]


def test_confidence_breaks_an_edge_tie():
    board = {
        10: rows(edge=0.05, confidence=0.55),
        20: rows(edge=0.05, confidence=0.80),
    }
    assert order(board) == [20, 10]


def test_the_id_breaks_a_total_tie_so_reruns_resume_where_they_stopped():
    board = {30: rows(), 10: rows(), 20: rows()}
    assert order(board) == [10, 20, 30]


def test_a_players_best_market_represents_him():
    # A player is one row on the page but several on the board; the strongest
    # market is the reason anyone opens him.
    board = {
        10: [
            {"has_call": True, "edge": 0.01, "confidence": 0.52},
            {"has_call": True, "edge": 0.14, "confidence": 0.66},
        ],
        20: rows(edge=0.09),
    }
    assert order(board) == [10, 20]


def test_a_player_with_no_numbers_at_all_sorts_last_without_raising():
    board = {
        10: [{"has_call": False, "edge": None, "confidence": None}],
        20: rows(),
    }
    assert order(board) == [20, 10]


def test_the_cutoff_is_reported_in_words():
    described = _describe_rank(selection_rank(7, rows(edge=0.062, confidence=0.61)))
    assert "call" in described
    assert "+6.2%" in described
    assert "61.0%" in described


def test_the_cutoff_names_a_missing_line_rather_than_printing_infinity():
    described = _describe_rank(
        selection_rank(7, rows(has_call=False, edge=None, confidence=0.61))
    )
    assert "no book line" in described
    assert "inf" not in described
    assert "lean" in described
