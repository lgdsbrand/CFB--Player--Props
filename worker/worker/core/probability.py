"""Projection → probability math.

This is the hinge of the whole product (CLAUDE.md §1): the model produces an
outcome DISTRIBUTION, and the over/under call plus confidence percentage are
derived from it — the call is the side holding the majority of the mass, the
confidence is the mass past the line. Nothing here knows about college
football, so the NFL build copies this module unchanged (CLAUDE.md §3).

NOTE ON DUPLICATION: the odds helpers here mirror the SQL functions
`american_to_implied_probability`, `devig_two_way` and `edge_on_side` defined in
migration 0006. Both exist on purpose — the worker computes picks in Python, the
read layer de-vigs live lines in SQL — but they must agree exactly. The test
suite pins both to the same fixed vectors; if you change one, change the other.
"""

from __future__ import annotations

import math
from typing import Any, Literal

BetSide = Literal["over", "under"]

DistributionFamily = Literal[
    "normal",
    "lognormal",
    "gamma",
    "poisson",
    "negative_binomial",
    "bernoulli",
]

REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "normal": ("mu", "sigma"),
    "lognormal": ("mu", "sigma"),
    "gamma": ("shape", "scale"),
    "poisson": ("lam",),
    "negative_binomial": ("r", "p"),
    "bernoulli": ("p",),
}


# -----------------------------------------------------------------------------
# Odds
# -----------------------------------------------------------------------------
def american_to_implied_probability(price: int) -> float:
    """Raw (vigged) implied probability from American odds.

    Not comparable to a model probability on its own — the vig has to come off
    first. See `devig_two_way`.
    """
    if price < 0:
        return (-price) / ((-price) + 100.0)
    return 100.0 / (price + 100.0)


def devig_two_way(over_price: int | None, under_price: int | None) -> float | None:
    """Fair probability of the OVER, vig removed proportionally.

    Returns None when only one side is priced: a one-sided price cannot be
    de-vigged, and callers must treat that as "no book probability", never as
    zero edge.

    The proportional (multiplicative) method divides each side's raw implied
    probability by the two-way total. CLAUDE.md §6 requires this to match the
    client's existing MLB pitcher model exactly; proportional is the common
    convention but their implementation is UNCONFIRMED — see
    app_config.devig_method.
    """
    if over_price is None or under_price is None:
        return None
    over_raw = american_to_implied_probability(over_price)
    under_raw = american_to_implied_probability(under_price)
    total = over_raw + under_raw
    if total <= 0:
        return None
    return over_raw / total


def edge_on_side(
    model_prob_over: float,
    book_prob_over: float | None,
    side: BetSide,
) -> float | None:
    """THE edge definition (CLAUDE.md §6).

    `model probability − de-vigged book implied probability`, evaluated on the
    side actually being taken. Explicitly NOT (projection − line) / line — that
    would give numbers incomparable with the client's pitcher-props board.
    """
    if book_prob_over is None:
        return None
    if side == "over":
        return model_prob_over - book_prob_over
    return (1.0 - model_prob_over) - (1.0 - book_prob_over)


def side_and_confidence(model_prob_over: float) -> tuple[BetSide, float]:
    """Derive the displayed call and confidence from the distribution.

    The call is the side the majority of the distribution falls on; the
    confidence is that side's probability mass. Mirrors the `picks.side` check
    constraint and the `picks.confidence` generated column, so Python and the
    database cannot disagree about what the card should say.
    """
    if model_prob_over >= 0.5:
        return "over", model_prob_over
    return "under", 1.0 - model_prob_over


# -----------------------------------------------------------------------------
# Distributions
# -----------------------------------------------------------------------------
def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam), computed iteratively."""
    if lam < 0:
        raise ValueError("lam must be non-negative")
    if k < 0:
        return 0.0
    term = math.exp(-lam)
    total = term
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return min(total, 1.0)


def validate_params(distribution: str, params: dict[str, Any]) -> None:
    """Check that `params` carries the keys the declared family needs.

    projections.params is jsonb, so this is the guard that keeps a malformed
    parameter dict from silently producing a nonsense probability.
    """
    required = REQUIRED_PARAMS.get(distribution)
    if required is None:
        raise ValueError(f"Unknown distribution family: {distribution!r}")
    missing = [key for key in required if key not in params]
    if missing:
        raise ValueError(
            f"Distribution {distribution!r} requires {required}; missing {missing}"
        )


def prob_over(distribution: str, params: dict[str, Any], line: float) -> float:
    """P(outcome > line) — the number every card is built from.

    Count markets are posted at half-integer lines (24.5), so P(X > 24.5) is
    P(X >= 25) with no push to worry about. An integer line is handled as a
    strict inequality, i.e. the push is excluded from the over rather than
    silently credited to it.
    """
    validate_params(distribution, params)

    if distribution == "normal":
        return 1.0 - _normal_cdf(line, float(params["mu"]), float(params["sigma"]))

    if distribution == "lognormal":
        if line <= 0:
            return 1.0
        return 1.0 - _normal_cdf(
            math.log(line), float(params["mu"]), float(params["sigma"])
        )

    if distribution == "poisson":
        return 1.0 - _poisson_cdf(math.floor(line), float(params["lam"]))

    if distribution == "bernoulli":
        p = float(params["p"])
        if line >= 1:
            return 0.0
        if line < 0:
            return 1.0
        # 0 <= line < 1: the over is "at least one", i.e. p itself.
        return p

    if distribution in ("gamma", "negative_binomial"):
        raise NotImplementedError(
            f"{distribution} CDF needs scipy, which arrives with statsmodels in "
            f"Phase 3. Until then use normal or poisson."
        )

    raise ValueError(f"Unknown distribution family: {distribution!r}")
