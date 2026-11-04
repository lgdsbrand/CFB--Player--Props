"""The published probability's per-market calibration (user-approved 2026-10-08)."""

from __future__ import annotations

import pytest

from worker.core.ladder import build_ladder
from worker.core.probability import (
    PROP_CALIBRATION_SLOPE,
    calibrate_prop_probability,
    prob_over,
)


def test_calibrated_markets_shrink_toward_half_and_keep_the_side() -> None:
    for market, slope in PROP_CALIBRATION_SLOPE.items():
        assert 0 < slope < 1
        for p in (0.1, 0.3, 0.62, 0.95):
            shrunk = calibrate_prop_probability(market, p)
            assert shrunk == pytest.approx(0.5 + slope * (p - 0.5))
            # Same side, never past the raw probability.
            assert (shrunk >= 0.5) == (p >= 0.5)
            assert abs(shrunk - 0.5) <= abs(p - 0.5)


def test_pass_td_eighty_percent_becomes_believable() -> None:
    assert calibrate_prop_probability("pass_tds", 0.80) == pytest.approx(0.563)


def test_uncalibrated_markets_are_untouched() -> None:
    for market in ("anytime_td", "q1_rec_yards", "q1_pass_yards"):
        assert calibrate_prop_probability(market, 0.83) == 0.83


def test_no_skill_markets_publish_near_half_and_keep_the_side() -> None:
    """Amended 2026-11-04: the volume markets showed no skill against the book."""
    for market in ("pass_attempts", "pass_completions", "rush_attempts"):
        assert calibrate_prop_probability(market, 0.80) == pytest.approx(0.515)
        assert calibrate_prop_probability(market, 0.20) == pytest.approx(0.485)


def test_rush_yards_eighty_percent_is_barely_a_lean() -> None:
    assert calibrate_prop_probability("rush_yards", 0.80) == pytest.approx(0.521)


def test_ladder_rungs_carry_the_same_calibration() -> None:
    params = {"lam": 1.8}
    raw = build_ladder("poisson", params, 1.0, low=0.0, high=3.0)
    published = build_ladder("poisson", params, 1.0, low=0.0, high=3.0, market_key="pass_tds")
    assert [r.line for r in raw] == [r.line for r in published]
    for a, b in zip(raw, published, strict=True):
        assert b.prob_over == pytest.approx(calibrate_prop_probability("pass_tds", a.prob_over))
        assert a.prob_over == pytest.approx(prob_over("poisson", params, a.line))
