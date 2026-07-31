"""Tests for the walk-forward backtest and its metrics.

Pure logic. The calibration numbers themselves come from the real run; what
these pin is that the machinery computing them is correct — a metric that is
subtly wrong would make the report confidently misleading, which is worse than
having no report.
"""

from __future__ import annotations

import pytest

from worker.core.backtest import (
    LINE_QUANTUM,
    Prediction,
    candidate_lines,
    compute_metrics,
    group_metrics,
    quantize_line,
    season_phase,
)


def _prediction(prob: float, over: bool, **overrides) -> Prediction:
    base = {
        "player_id": 1,
        "game_id": 1,
        "market_key": "rec_yards",
        "position_group": "WR",
        "season": 2024,
        "week": 8,
        "as_of_week": 8,
        "line": 50.5,
        "model_prob_over": prob,
        "side": "over" if prob >= 0.5 else "under",
        "confidence": max(prob, 1 - prob),
        "actual_value": 60.0,
        "outcome_over": over,
        "hit": over if prob >= 0.5 else not over,
    }
    base.update(overrides)
    return Prediction(**base)


class TestLines:
    def test_quantized_to_half_points(self):
        # Books post half-points to avoid pushes.
        assert quantize_line(50.3) == 50.5
        assert quantize_line(50.1) == 50.0
        assert quantize_line(4.4) % LINE_QUANTUM == 0

    def test_binary_market_gets_exactly_one_line(self):
        lines = candidate_lines(0.4, 0.5, binary=True, default_line=0.5)
        assert lines == [0.5]

    def test_continuous_market_spans_the_centre(self):
        lines = candidate_lines(50.0, 20.0, binary=False, default_line=None)
        assert len(lines) >= 3
        assert min(lines) < 50.0 < max(lines)

    def test_non_positive_lines_are_dropped(self):
        """P(over) at a line below zero is 1.0 by construction — a free win that
        would flatter the top of the reliability curve."""
        lines = candidate_lines(1.0, 5.0, binary=False, default_line=None)
        assert all(line > 0 for line in lines)

    def test_lines_are_unique(self):
        lines = candidate_lines(2.0, 0.1, binary=False, default_line=None)
        assert len(lines) == len(set(lines))

    def test_a_tiny_sigma_still_produces_a_usable_spread(self):
        # A player whose games happened to land identically must not collapse
        # every line onto one number.
        lines = candidate_lines(40.0, 0.0, binary=False, default_line=None)
        assert len(lines) >= 3


class TestMetrics:
    def test_empty_input_returns_none(self):
        assert compute_metrics([]) is None

    def test_perfect_predictions_score_zero_brier(self):
        predictions = [
            _prediction(1 - 1e-9, True),
            _prediction(1e-9, False),
        ]
        metrics = compute_metrics(predictions)
        assert metrics is not None
        assert metrics.brier == pytest.approx(0.0, abs=1e-6)

    def test_base_rate_predictor_scores_zero_skill(self):
        """Brier skill is measured against always predicting the base rate."""
        predictions = [_prediction(0.5, i % 2 == 0) for i in range(200)]
        metrics = compute_metrics(predictions)
        assert metrics is not None
        assert metrics.base_rate == pytest.approx(0.5)
        assert metrics.brier_skill == pytest.approx(0.0, abs=1e-9)

    def test_confidently_wrong_scores_negative_skill(self):
        predictions = [_prediction(0.9, False) for _ in range(50)]
        predictions += [_prediction(0.1, True) for _ in range(50)]
        metrics = compute_metrics(predictions)
        assert metrics is not None
        assert metrics.brier_skill < 0

    def test_log_loss_punishes_confident_errors_hardest(self):
        """The metric that exposed the beta-binomial ceiling bug.

        Brier moves linearly with the error; log loss explodes. A model that is
        confidently wrong is far more expensive than one that is vaguely wrong,
        and only log loss says so loudly.
        """
        vague = compute_metrics([_prediction(0.55, False) for _ in range(100)])
        confident = compute_metrics([_prediction(0.999, False) for _ in range(100)])
        assert vague is not None and confident is not None

        # The claim is relative: going from vaguely to confidently wrong costs
        # far more in log loss than in Brier. Brier is bounded at 1 and can only
        # ever roughly triple here; log loss is unbounded.
        brier_ratio = confident.brier / vague.brier
        log_loss_ratio = confident.log_loss / vague.log_loss
        assert log_loss_ratio > 2 * brier_ratio

    def test_perfect_calibration_gives_zero_ece(self):
        # 70% of a 0.7-probability group must come true.
        predictions = [_prediction(0.7, True) for _ in range(70)]
        predictions += [_prediction(0.7, False) for _ in range(30)]
        metrics = compute_metrics(predictions)
        assert metrics is not None
        assert metrics.ece == pytest.approx(0.0, abs=1e-9)

    def test_miscalibration_shows_up_in_ece(self):
        # Says 90%, happens 50% of the time.
        predictions = [_prediction(0.9, i % 2 == 0) for i in range(100)]
        metrics = compute_metrics(predictions)
        assert metrics is not None
        assert metrics.ece == pytest.approx(0.4, abs=0.01)

    def test_sharpness_measures_willingness_to_commit(self):
        """A perfectly calibrated model that always says 50% is useless, so
        calibration has to be read alongside sharpness."""
        timid = compute_metrics([_prediction(0.5, i % 2 == 0) for i in range(100)])
        bold = compute_metrics(
            [_prediction(0.9, True) for _ in range(90)]
            + [_prediction(0.9, False) for _ in range(10)]
        )
        assert timid is not None and bold is not None
        assert timid.sharpness == pytest.approx(0.0)
        assert bold.sharpness > timid.sharpness

    def test_bins_partition_the_predictions(self):
        predictions = [
            _prediction(i / 100, i % 3 == 0) for i in range(1, 100)
        ]
        metrics = compute_metrics(predictions)
        assert metrics is not None
        assert sum(b.count for b in metrics.bins) == len(predictions)

    def test_probability_of_one_is_binned_not_dropped(self):
        metrics = compute_metrics([_prediction(1.0 - 1e-12, True)])
        assert metrics is not None
        assert sum(b.count for b in metrics.bins) == 1


class TestGrouping:
    def test_splits_by_attribute(self):
        predictions = [
            _prediction(0.6, True, market_key="rec_yards"),
            _prediction(0.6, True, market_key="rec_yards"),
            _prediction(0.4, False, market_key="receptions"),
        ]
        grouped = group_metrics(predictions, "market_key")
        assert set(grouped) == {"rec_yards", "receptions"}
        assert grouped["rec_yards"].n == 2

    def test_season_phase_splits_early_from_late(self):
        # Reported separately because early-season college projections lean
        # hardest on priors and are least trustworthy.
        assert season_phase(3) != season_phase(11)
        assert season_phase(3) == season_phase(4)


class TestPredictionGrading:
    def test_hit_is_not_the_same_as_outcome_over(self):
        """An under call on a result under the line is a hit."""
        under = _prediction(0.3, False)
        assert under.side == "under"
        assert under.outcome_over is False
        assert under.hit is True

    def test_over_call_on_an_under_result_is_a_miss(self):
        over = _prediction(0.8, False)
        assert over.side == "over"
        assert over.hit is False

    def test_confidence_is_never_below_half(self):
        for prob in (0.01, 0.3, 0.5, 0.7, 0.99):
            assert _prediction(prob, True).confidence >= 0.5
