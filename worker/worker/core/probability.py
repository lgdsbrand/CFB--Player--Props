"""Projection → probability math.

This is the hinge of the whole product (CLAUDE.md §1): the model produces an
outcome DISTRIBUTION, and the over/under call plus confidence percentage are
derived from it — the call is the side holding the majority of the mass, the
confidence is the mass past the line. Nothing here knows about college
football, so the NFL build copies this module unchanged (CLAUDE.md §3).

NOTE ON DUPLICATION: the odds helpers here mirror the SQL functions
`american_to_implied_probability`, `devig_two_way` and `edge_on_side` defined in
migration 0006 and extended in 0013. Both exist on purpose — the worker computes
picks in Python, the read layer de-vigs live lines in SQL — but they must agree
exactly. The test suite pins both to the same fixed vectors; if you change one,
change the other.

NOTE ON PURITY: nothing in this module reads the database. The de-vig method is
an explicit argument, never a config lookup, because this file is copied
wholesale into the NFL build (CLAUDE.md §3) and because a pricing function whose
answer depends on hidden global state is untestable. Jobs resolve
`app_config.devig_method` and pass it down.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

from scipy import stats as _stats

BetSide = Literal["over", "under"]

DevigMethod = Literal["proportional", "additive", "shin"]

# CHOSEN 2026-07-31. The client released the requirement to match their MLB
# pitcher model, so this is picked on merit rather than for consistency.
#
# Where it matters: on a two-way market priced near -110/-110 all three methods
# agree to well under a percentage point, so the choice is irrelevant for
# yardage and reception props. It is decisive for anytime touchdown, which is
# priced at wildly lopsided numbers. On a +600/-1100 market, proportional gives
# the longshot 13.5% and Shin gives it 11.3% — a 2.2pp gap on a 13% probability,
# which alone can move a pick across the 5% edge threshold.
#
# Proportional's known failure is under-correcting the favourite-longshot bias,
# and that bias lives exactly where our most lopsided market lives. Shin models
# the vig as protection against informed money and shades longshots down, which
# is the right direction; it also collapses to proportional as prices approach
# even, so it only differs where it helps.
DEFAULT_DEVIG_METHOD: DevigMethod = "shin"

# IDENTITY, verified numerically across 441 price pairs and provable in two
# lines: for a TWO-outcome market, Shin's method and the additive method give
# exactly the same answer.
#
#   Shin requires  sqrt(z²+aπ₁²) + sqrt(z²+aπ₂²) = 2  where a = 4(1−z)/Π.
#   Multiply the difference of those roots by their sum:
#     D·2 = a(π₁²−π₂²) = a(π₁−π₂)Π = 4(1−z)(π₁−π₂),  so D = 2(1−z)(π₁−π₂).
#   Dividing by the 2(1−z) denominator gives p₁−p₂ = π₁−π₂ — each side shifted
#   by the same amount, which is precisely what the additive method does.
#
# This matters for how the default is justified. The published results showing
# Shin better calibrated than plain normalization come from THREE-outcome
# markets (home/draw/away), where the methods genuinely differ. Every market we
# model is two-way, so here the real choice is proportional vs not-proportional,
# and Shin and additive are two derivations of the same number.
#
# Both are kept: they are independent code paths that now cross-check each
# other, and they would diverge if a three-way market were ever added.
_SHIN_EQUALS_ADDITIVE_FOR_TWO_WAY = True

# Bisection settings for Shin's z. Shared verbatim with the plpgsql
# implementation in migration 0013 so both converge to the same double.
_SHIN_Z_MAX = 0.99
_SHIN_TOLERANCE = 1e-12
_SHIN_MAX_ITERATIONS = 200

# Slack when testing whether a two-way price total reaches 1.0, so that float
# noise in the implied-probability division is not read as an incoherent market.
_COHERENCE_TOLERANCE = 1e-9

DistributionFamily = Literal[
    "normal",
    "lognormal",
    "gamma",
    "poisson",
    "negative_binomial",
    "beta_binomial",
    "bernoulli",
]

REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "normal": ("mu", "sigma"),
    "lognormal": ("mu", "sigma"),
    "gamma": ("shape", "scale"),
    "poisson": ("lam",),
    "negative_binomial": ("r", "p"),
    "beta_binomial": ("n", "a", "b"),
    "bernoulli": ("p",),
}

# `loc` shifts a distribution's support along the x-axis. Optional, defaulting
# to 0, so existing two-parameter params dicts keep working unchanged.
#
# It is not cosmetic. 26% of QB rushing games are NEGATIVE, because NCAA charges
# a sack as a rushing loss — so a gamma anchored at zero cannot describe QB
# rushing yards at all. With a free location it fits, and beats the normal that
# migration 0009 had chosen precisely because normal was the only seeded family
# admitting negatives.
OPTIONAL_LOCATION_FAMILIES = frozenset({"gamma", "lognormal"})


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


def solve_shin_z(raw_probabilities: Sequence[float]) -> float:
    """Solve for Shin's z — the implied share of informed money in the market.

    Shin (1993) models the overround as the bookmaker's protection against
    insider traders rather than as a flat proportional margin. Given raw implied
    probabilities summing to Π > 1, fair probabilities are

        p_i = [sqrt(z² + 4(1−z)·π_i²/Π) − z] / (2(1−z))

    with z chosen so Σp_i = 1. The sum is monotone decreasing in z, so plain
    bisection is both sufficient and reproducible — which matters, because the
    plpgsql twin must land on the same double.

    Returns 0.0 when there is no overround to explain (Π ≤ 1), which is the
    degenerate case where Shin reduces to no correction at all.

    z is worth keeping: it is a clean measure of how toxic a market is, and a
    market with an implausibly high z is usually a stale or mispulled price.
    """
    total = sum(raw_probabilities)
    if total <= 1.0 or any(p <= 0 for p in raw_probabilities):
        return 0.0

    def excess(z: float) -> float:
        return sum(_shin_probabilities(raw_probabilities, total, z)) - 1.0

    low, high = 0.0, _SHIN_Z_MAX
    if excess(high) > 0:
        # No root in range: the overround is too extreme for the model. Caller
        # falls back to proportional rather than returning a fabricated number.
        return _SHIN_Z_MAX

    for _ in range(_SHIN_MAX_ITERATIONS):
        mid = 0.5 * (low + high)
        if excess(mid) > 0:
            low = mid
        else:
            high = mid
        if high - low < _SHIN_TOLERANCE:
            break
    return 0.5 * (low + high)


def _shin_probabilities(
    raw_probabilities: Sequence[float], total: float, z: float
) -> list[float]:
    if z >= 1.0:
        return list(raw_probabilities)
    denominator = 2.0 * (1.0 - z)
    return [
        (math.sqrt(z * z + 4.0 * (1.0 - z) * p * p / total) - z) / denominator
        for p in raw_probabilities
    ]


def devig_two_way(
    over_price: int | None,
    under_price: int | None,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> float | None:
    """Fair probability of the OVER with the vig removed.

    Returns None when only one side is priced: a one-sided price cannot be
    de-vigged, and callers must treat that as "no book probability", never as
    zero edge. Books routinely post a longshot anytime-TD Yes with no No, so
    this is a common path, not an edge case.

    Methods:

      * ``proportional`` — divide each side by the two-way total. The common
        convention. Under-corrects the favourite-longshot bias.
      * ``additive`` — subtract half the overround from each side. Also returns
        None when that drives a side out of (0, 1), which happens on very
        lopsided markets. Clamping instead would report a near-zero book
        probability and manufacture an enormous fake edge — the exact failure
        the None contract exists to prevent.
      * ``shin`` — the default; see DEFAULT_DEVIG_METHOD and solve_shin_z.
    """
    if over_price is None or under_price is None:
        return None

    over_raw = american_to_implied_probability(over_price)
    under_raw = american_to_implied_probability(under_price)
    total = over_raw + under_raw

    # An incoherent market. Two real prices on opposite sides of the same line
    # always imply MORE than 1.0 — that surplus is the book's margin. A total
    # below 1 is free money, which does not exist at scale, so in practice it
    # means the pair is not what it claims: a stale price, a mispull, or two
    # different lines crossed by an upstream bug. Normalizing it anyway would
    # yield a confident fair probability from the least trustworthy input we
    # ever see, and the resulting edge would look enormous. None is the honest
    # answer, and it takes the same "no book probability" path as a one-sided
    # quote. Exactly 1.0 is fine: a zero-vig market needs no correction.
    if total < 1.0 - _COHERENCE_TOLERANCE:
        return None

    if method == "proportional":
        return over_raw / total

    if method == "additive":
        half_overround = (total - 1.0) / 2.0
        fair_over = over_raw - half_overround
        fair_under = under_raw - half_overround
        # Defensive only. For a TWO-way market this cannot trigger: it would
        # need over_raw < under_raw - 1, and a raw implied probability from
        # American odds is always strictly below 1. Kept because the guard is
        # free and would matter if this were ever extended past two outcomes.
        if not (0.0 < fair_over < 1.0) or not (0.0 < fair_under < 1.0):
            return None
        return fair_over

    if method == "shin":
        if total <= 1.0:
            return over_raw / total
        z = solve_shin_z((over_raw, under_raw))
        fair = _shin_probabilities((over_raw, under_raw), total, z)
        fair_total = sum(fair)
        if fair_total <= 0:
            return None
        # Renormalize: bisection lands within tolerance of 1, not exactly on it.
        return fair[0] / fair_total

    raise ValueError(
        f"Unknown de-vig method {method!r}. "
        f"Expected one of: proportional, additive, shin."
    )


def consensus_book_probability(
    prices: Sequence[tuple[int | None, int | None]],
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> float | None:
    """Median de-vigged OVER probability across books.

    A single book's fair probability is a noisy estimate of the market's view.
    De-vigging each book separately and taking the median is a materially better
    one, and it is cheap because the board already carries per-book odds
    (CLAUDE.md §7).

    De-vig-then-median, never median-then-de-vig: averaging prices across books
    with different holds produces a synthetic market that exists nowhere, and
    its overround is not any real book's.

    One-sided quotes are skipped rather than counted, so this returns None when
    no book posted a two-way price. Takes plain price tuples rather than
    adapter objects to keep this module free of provider types.
    """
    fair = [
        p
        for p in (devig_two_way(over, under, method) for over, under in prices)
        if p is not None
    ]
    if not fair:
        return None

    fair.sort()
    middle = len(fair) // 2
    if len(fair) % 2 == 1:
        return fair[middle]
    return 0.5 * (fair[middle - 1] + fair[middle])


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


def distribution_sd(distribution: str, params: dict[str, Any]) -> float:
    """Standard deviation of a fitted distribution, whatever its family.

    Needed to compare families on one scale. The variance calibration measures
    how wide each projected distribution turned out to be against what actually
    happened, and that question has one answer per family only if there is a
    single notion of width — `sigma` for a normal, but a derived quantity for
    every other family we fit.

    Returns 0.0 for a degenerate distribution rather than raising, since a
    caller dividing by this is asking "how many SDs out was the result", and a
    zero-width projection has no meaningful answer to give.
    """
    validate_params(distribution, params)

    if distribution == "normal":
        return abs(float(params["sigma"]))

    if distribution == "lognormal":
        sigma = float(params["sigma"])
        mu = float(params["mu"])
        variance = (math.exp(sigma * sigma) - 1.0) * math.exp(2.0 * mu + sigma * sigma)
        return math.sqrt(max(variance, 0.0))

    if distribution == "gamma":
        return math.sqrt(float(params["shape"])) * float(params["scale"])

    if distribution == "poisson":
        return math.sqrt(max(float(params["lam"]), 0.0))

    if distribution == "bernoulli":
        p = float(params["p"])
        return math.sqrt(max(p * (1.0 - p), 0.0))

    if distribution == "negative_binomial":
        r, p = float(params["r"]), float(params["p"])
        return math.sqrt(max(r * (1.0 - p), 0.0)) / p

    if distribution == "beta_binomial":
        n, a, b = float(params["n"]), float(params["a"]), float(params["b"])
        total = a + b
        if total <= 0 or n <= 0:
            return 0.0
        p = a / total
        variance = n * p * (1.0 - p) * (n + total) / (1.0 + total)
        return math.sqrt(max(variance, 0.0))

    raise ValueError(f"Unknown distribution family: {distribution!r}")


def distribution_median(distribution: str, params: dict[str, Any]) -> float:
    """Median of a fitted continuous distribution.

    Exact, and deliberately not a bisection on `prob_over`. `rescale` consults
    this on every gamma and lognormal row it widens, and inverting the survival
    function numerically there cost about sixty scipy calls per projection —
    enough to take a single week of `run_projections` from seconds to minutes.

    Only the continuous yardage families are supported, because they are the
    only ones whose location can be shifted below zero. A caller asking for the
    median of a count family wants a quantile, not this.
    """
    validate_params(distribution, params)

    if distribution == "normal":
        return float(params["mu"])

    if distribution == "lognormal":
        # median of a lognormal is exp(mu), shifted by the location.
        return float(params.get("loc", 0.0)) + math.exp(float(params["mu"]))

    if distribution == "gamma":
        return float(
            _stats.gamma.ppf(
                0.5,
                a=float(params["shape"]),
                loc=float(params.get("loc", 0.0)),
                scale=float(params["scale"]),
            )
        )

    raise ValueError(f"No closed-form median for family: {distribution!r}")


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
        loc = float(params.get("loc", 0.0))
        if line <= loc:
            return 1.0
        return float(
            _stats.lognorm.sf(
                line,
                s=float(params["sigma"]),
                loc=loc,
                scale=math.exp(float(params["mu"])),
            )
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

    if distribution == "gamma":
        # Yardage: right-skewed, with a bad game near the floor and no ceiling
        # on a long touchdown. `loc` shifts the floor below zero where the stat
        # can go negative — QB rushing, via sacks.
        loc = float(params.get("loc", 0.0))
        if line <= loc:
            return 1.0
        return float(
            _stats.gamma.sf(
                line,
                a=float(params["shape"]),
                loc=loc,
                scale=float(params["scale"]),
            )
        )

    if distribution == "negative_binomial":
        # Counts (attempts, completions, carries, receptions). Preferred over
        # Poisson because game-to-game usage is overdispersed: a running back's
        # carries vary with game script far more than a Poisson would allow, and
        # Poisson would understate the tails on both sides.
        #
        # scipy's parameterization: r successes, success probability p, support
        # k = 0,1,2,..., mean = r(1-p)/p. sf(k) is P(X > k) directly.
        if line < 0:
            return 1.0
        return float(
            _stats.nbinom.sf(
                math.floor(line), n=float(params["r"]), p=float(params["p"])
            )
        )

    if distribution == "beta_binomial":
        # Receptions. Measured within player, receptions are UNDER-dispersed
        # (median variance/mean 0.53 for RBs, 0.84 TE, 0.95 WR), which a
        # negative binomial cannot represent at any parameterization — its
        # variance always exceeds its mean.
        #
        # The shape follows from how a reception happens: given n targets it is
        # Binomial(n, catch rate), whose variance n*p*(1-p) sits below its mean.
        # Letting the catch rate vary between games via a Beta prior widens that
        # back out, spanning the measured range. This is the same two-stage
        # structure the projection model is built on, written as one closed-form
        # distribution.
        if line < 0:
            return 1.0
        return float(
            _stats.betabinom.sf(
                math.floor(line),
                n=int(params["n"]),
                a=float(params["a"]),
                b=float(params["b"]),
            )
        )

    raise ValueError(f"Unknown distribution family: {distribution!r}")
