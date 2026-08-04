"""Two-stage projection: volume x efficiency.

SPORT-AGNOSTIC CORE (CLAUDE.md §3).

WHY TWO STAGES
--------------
A projection could model the outcome directly — fit receiving yards, done. This
does not, for three reasons, in increasing order of importance.

1. **Interpretability.** "18 fewer yards because we project 1.5 fewer targets"
   is a sentence. "18 fewer yards" is a number. The weekly AI read in Phase 5
   has to explain a pick, and it can only explain what the model separates.

2. **The opponent adjustment lands where it belongs.** A defense suppresses
   volume and efficiency differently — a unit that concedes catches but no yards
   after them is a different matchup from one that concedes neither. Applying a
   single multiplier to a yardage total cannot express that.

3. **It is the shape the data actually has.** Measured within player across
   2024-25, receptions are UNDER-dispersed (median variance/mean 0.53 for RBs,
   0.84 TE, 0.95 WR) while targets are OVER-dispersed (1.20). No single count
   distribution produces that pattern; the composition does. Given n targets,
   receptions are Binomial(n, catch rate), whose variance sits below its mean,
   and

       dispersion(receptions) = (1 - p) + p * dispersion(targets)

   drags the result under 1. Modelling receptions directly with a negative
   binomial — as migration 0009 originally did — is not a poor fit but the wrong
   shape, since a negative binomial's variance always exceeds its mean.

So: project the volume, project the per-unit efficiency, compose.

BLENDING
--------
Every input quantity is an empirical-Bayes blend of three sources:

    current season to date  (weight: games played)
    prior season            (weight: prior games x prior_weight from features)
    position baseline       (weight: a fixed pseudo-count)

The baseline term is what makes week 2 projections behave. With one game played
a raw mean is almost pure noise, and shrinking it toward the position average is
the difference between a usable early-season number and a coin flip dressed as a
projection. It also implements CLAUDE.md §6's requirement that early-season
output lean on priors and sharpen as the season accumulates — here that emerges
from the arithmetic rather than being a special case.

THE TELESCOPING IDENTITY
------------------------
Each rate is blended with weight equal to its own denominator, which makes the
chain collapse exactly:

    blend(targets) x blend(receptions/targets) x blend(rec_yards/receptions)
        == blend(rec_yards)

Reshuffling volume against efficiency while holding the product fixed changes
nothing. This is deliberate and worth understanding before touching the blend
weights, because it is what guarantees the decomposition cannot double-count: it
is a pure re-expression of the same estimate, not a second bite at it.

It also means the split earns its keep in exactly three places, none of them the
mean on its own:

  * the opponent adjustment, applied SEPARATELY to volume and to efficiency,
  * the distribution SHAPE it implies — receptions become beta-binomial through
    targets rather than a negative binomial fitted to receptions directly,
  * the explanation handed to the weekly AI read.

NULLS
-----
`avg()` over a stat returns NULL when every game is NULL, which happens for
players who never recorded that stat. A row in `player_game_stats` means the
player appeared, so a NULL VOLUME is read as a genuine zero — a wide receiver
really did attempt zero passes. A NULL EFFICIENCY is different: yards per
attempt is undefined at zero attempts, not zero, so it falls back to the
baseline. Conflating the two would hand every backup the league average.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from worker.core.probability import (
    distribution_median,
    distribution_quantile,
    distribution_sd,
    prob_over,
    validate_params,
)
from worker.logging_setup import get_logger

log = get_logger(__name__)

# Pseudo-count for shrinkage toward the position baseline, in games. At 4, a
# player with 4 games carries equal weight from their own record and from the
# baseline; by 12 games their own record dominates roughly 3:1.
BASELINE_PSEUDO_GAMES = 4.0

# Bounds on the data-driven pseudo-count. The floor stops a stat that separates
# players enormously from receiving no shrinkage at all, which would leave a
# one-game sample fully trusted; the ceiling stops a very noisy stat from being
# shrunk into the population mean and losing the player entirely.
MIN_PSEUDO_GAMES = 0.5
MAX_PSEUDO_GAMES = 12.0

# Floor on a projected standard deviation, as a fraction of the mean. Guards the
# degenerate case where a player's handful of games happened to be near-identical
# and the sample SD collapses toward zero — which would otherwise produce a
# confidence of 99% off three games.
MIN_RELATIVE_SD = 0.25

# Dispersion floor/ceiling when solving count parameters. Outside this range the
# solved parameters stop being meaningful and the family cannot represent them.
MIN_DISPERSION = 1.05
MAX_DISPERSION = 6.0

QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


@dataclass
class Projection:
    """One outcome distribution, ready to write to `projections`."""

    market_key: str
    distribution: str
    params: dict[str, float]
    mean: float
    quantiles: dict[str, float] = field(default_factory=dict)
    volume: float | None = None
    efficiency: float | None = None
    matchup_multiplier: float | None = None

    def probability_over(self, line: float) -> float:
        return prob_over(self.distribution, self.params, line)


# -----------------------------------------------------------------------------
# Blending
# -----------------------------------------------------------------------------
def blend(
    *,
    current: float | None,
    current_games: float,
    prior: float | None,
    prior_games: float,
    prior_weight: float,
    baseline: float,
    pseudo_games: float = BASELINE_PSEUDO_GAMES,
) -> float:
    """Weighted mean of current season, prior season and position baseline.

    Prior-season evidence is discounted by `prior_weight`, which already carries
    the transfer-portal haircut from `features.prior_weight` — a player's prior
    production says much less about a role they left behind.
    """
    numerator = baseline * pseudo_games
    denominator = pseudo_games

    if current is not None and current_games > 0:
        numerator += current * current_games
        denominator += current_games

    if prior is not None and prior_games > 0 and prior_weight > 0:
        effective = prior_games * prior_weight
        numerator += prior * effective
        denominator += effective

    return numerator / denominator if denominator else baseline


def _value(row: dict[str, Any], key: str, default: float | None = None) -> float | None:
    value = row.get(key)
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _volume(row: dict[str, Any], key: str) -> float:
    """A NULL volume is a real zero: the row's existence means the player played."""
    return _value(row, key, 0.0) or 0.0


# -----------------------------------------------------------------------------
# Parameter solvers
# -----------------------------------------------------------------------------
def negative_binomial_params(mean: float, dispersion: float) -> dict[str, float]:
    """Solve r, p from a mean and a variance/mean ratio.

    scipy's parameterization: mean = r(1-p)/p, variance = r(1-p)/p^2, so the
    dispersion index is exactly 1/p. Requires dispersion > 1 — a negative
    binomial cannot be under-dispersed, which is precisely why receptions use a
    beta-binomial instead.
    """
    mean = max(mean, 1e-6)
    dispersion = min(max(dispersion, MIN_DISPERSION), MAX_DISPERSION)
    p = 1.0 / dispersion
    r = mean * p / (1.0 - p)
    return {"r": max(r, 1e-6), "p": min(max(p, 1e-6), 1 - 1e-6)}


def gamma_params(mean: float, sd: float, loc: float = 0.0) -> dict[str, float]:
    """Solve shape and scale for a gamma whose support starts at `loc`.

    `loc` is what lets a gamma describe QB rushing yards at all: 26% of those
    games are negative because NCAA charges sacks as rushing losses.
    """
    centred = max(mean - loc, 1e-6)
    variance = max(sd, 1e-6) ** 2
    scale = variance / centred
    shape = centred / scale
    return {
        "shape": max(shape, 1e-6),
        "scale": max(scale, 1e-6),
        "loc": loc,
    }


def lognormal_params(mean: float, sd: float, loc: float = 0.0) -> dict[str, float]:
    """Solve mu and sigma from the mean and SD of a shifted lognormal."""
    centred = max(mean - loc, 1e-6)
    variance = max(sd, 1e-6) ** 2
    sigma_squared = math.log1p(variance / (centred * centred))
    sigma = math.sqrt(max(sigma_squared, 1e-9))
    mu = math.log(centred) - 0.5 * sigma_squared
    return {"mu": mu, "sigma": sigma, "loc": loc}


def beta_binomial_params(
    trials: float,
    success_rate: float,
    dispersion: float,
    *,
    trials_sd: float | None = None,
) -> dict[str, float]:
    """Solve n, a, b from expected trials, a success rate and a dispersion target.

    For a beta-binomial, variance/mean = (1-p)(1 + (n-1)rho) with
    rho = 1/(a+b+1) the intra-class correlation. Inverting for rho and pinning
    p = a/(a+b) gives a and b.

    N IS A CEILING, NOT AN EXPECTATION. This is the subtle part, and getting it
    wrong was the single worst defect the first backtest found: receptions
    scored a log loss of 3.08 and a Brier skill of -0.233, far worse than
    predicting the base rate.

    The cause was setting n to the EXPECTED target count. A beta-binomial cannot
    produce more successes than trials, so a receiver projected for 4 targets was
    assigned probability ~0 of exceeding 4 receptions — and receivers who drew 9
    targets and caught 6 turned those near-certainties into losses. Confidently
    wrong is far more expensive than vaguely wrong, and log loss says so loudly.

    Targets are themselves random, so n has to cover the plausible RANGE, not the
    point estimate: three standard deviations above expectation. p is then
    derived from the mean, which keeps the mean exact whatever n turns out to be.

    The cost is honest: a larger n with a smaller p raises the minimum achievable
    dispersion, (1-p), so the strong under-dispersion measured for running backs
    (0.53) is no longer reachable. That is a real loss of fidelity in exchange
    for removing an impossible-outcome ceiling, and it is the right trade —
    under-dispersion mis-states the width of a distribution, while the ceiling
    made outcomes that actually happen unreachable.
    """
    mean = max(trials * success_rate, 1e-6)

    headroom = trials_sd if trials_sd is not None else math.sqrt(max(trials, 1.0) * 2.0)
    n = max(int(math.ceil(trials + 3.0 * max(headroom, 0.0))), 1)
    # Never let the ceiling sit at or below the mean itself.
    n = max(n, int(math.ceil(mean)) + 1)

    p = min(max(mean / n, 1e-4), 1 - 1e-3)

    binomial_dispersion = 1.0 - p
    if n <= 1 or dispersion <= binomial_dispersion:
        concentration = 1e6  # effectively binomial
    else:
        rho = (dispersion / binomial_dispersion - 1.0) / (n - 1)
        rho = min(max(rho, 1e-9), 0.999)
        concentration = 1.0 / rho - 1.0
        concentration = min(max(concentration, 1e-3), 1e6)

    return {"n": n, "a": p * concentration, "b": (1.0 - p) * concentration}


# -----------------------------------------------------------------------------
# Quantiles
# -----------------------------------------------------------------------------
def _quantiles(distribution: str, params: dict[str, float]) -> dict[str, float]:
    """Cached display quantiles, from each family's exact inverse CDF.

    These back only the SECONDARY projected range on the player page — never the
    headline claim (CLAUDE.md §1). They were previously found by bisecting
    `prob_over` 80 times per quantile, which was the single most expensive thing
    the projection run did: 2.5 million survival-function calls per week, 88% of
    the job. `distribution_quantile` returns the same values from `ppf`.
    """
    result: dict[str, float] = {}
    for q in QUANTILES:
        result[f"p{int(q * 100)}"] = distribution_quantile(distribution, params, q)
    return result


def finalize(
    market_key: str,
    distribution: str,
    params: dict[str, float],
    mean: float,
    **extra: Any,
) -> Projection:
    validate_params(distribution, params)
    return Projection(
        market_key=market_key,
        distribution=distribution,
        params=params,
        mean=mean,
        quantiles=_quantiles(distribution, params),
        **extra,
    )


# Families with a width that can be turned independently of the mean. Poisson's
# variance IS its mean and a Bernoulli's is fixed by p, so for those two a
# measured miscalibration is real information but not something a rescale can
# act on — it has to be reported rather than silently dropped.
RESCALABLE_FAMILIES = frozenset(
    {"normal", "gamma", "lognormal", "negative_binomial", "beta_binomial"}
)


def can_rescale(distribution: str) -> bool:
    return distribution in RESCALABLE_FAMILIES


def rescale(projection: Projection, scale: float) -> Projection:
    """Re-solve a projection at `scale` times its width, holding the mean.

    Applies the correction from `worker.core.calibration`: the projected spread
    is the player's own game-to-game variation, which omits the error in the
    projected mean itself, and the backtest measured that omission market by
    market.

    The mean must not move. The over/under call and the confidence both come
    from probability mass either side of a line, so shifting the centre while
    claiming to fix the width would trade a calibration error for a bias.

    Two families cannot express this and are returned untouched. A Poisson's
    variance IS its mean, and a Bernoulli's is fixed by p, so neither has a
    width to turn independently. That is not a silent failure: both are the
    markets the diagnostic found were already close (pass_tds at 5.3% tail mass,
    anytime_td graded at a single line), and changing family to gain a free
    parameter would break the family recorded in `projections.distribution`.
    """
    if scale <= 0 or not math.isfinite(scale) or abs(scale - 1.0) < 1e-9:
        return projection

    distribution = projection.distribution
    params = dict(projection.params)
    mean = projection.mean

    if distribution in ("poisson", "bernoulli"):
        return projection

    if distribution == "normal":
        params["sigma"] = max(float(params["sigma"]) * scale, 1e-6)

    elif distribution in ("gamma", "lognormal"):
        # LOCATION-SCALE, not a re-solve at a bigger SD.
        #
        # Re-solving was the first attempt and it was wrong in a way that only
        # the backtest could show. Holding the mean while inflating the variance
        # of a RIGHT-SKEWED family drags its median down, and P(over) at a line
        # near the centre is governed by the median. rec_yards is gamma and
        # lognormal, and its middle bins went from +0.002 to +0.060 -- a
        # calibration error traded for a bias. pass_yards is normal, symmetric,
        # and had no such problem, which is what identified the cause.
        #
        # Both families are closed under Y = m + s(X - m), which scales every
        # deviation from the mean without touching the shape:
        #
        #   gamma      shape k unchanged, scale -> s*scale,
        #              loc -> s*loc + m*(1-s)
        #   lognormal  sigma unchanged,   mu -> mu + ln(s),
        #              loc -> s*loc + m*(1-s)
        #
        # so the mean is preserved exactly, the SD scales by s, and the median
        # keeps its position relative to the mean.
        #
        # The cost is that `loc` goes negative for s > 1, giving a little
        # probability to impossible negative yardage. That is the honest trade:
        # a small amount of misplaced mass at the floor against a systematic
        # bias through the entire middle of the range, which is where most
        # lines actually sit.
        loc = float(params.get("loc", 0.0))
        shifted_loc = scale * loc + mean * (1.0 - scale)
        if distribution == "gamma":
            params = {
                "shape": float(params["shape"]),
                "scale": max(float(params["scale"]) * scale, 1e-9),
                "loc": shifted_loc,
            }
        else:
            params = {
                "mu": float(params["mu"]) + math.log(scale),
                "sigma": float(params["sigma"]),
                "loc": shifted_loc,
            }
        params = _lift_location_to_positive_median(distribution, params, mean)

    elif distribution == "negative_binomial":
        # variance = mean * dispersion, so the width scales with sqrt(dispersion).
        p = float(params["p"])
        dispersion = 1.0 / p if p > 0 else 2.0
        params = negative_binomial_params(mean, dispersion * scale * scale)

    elif distribution == "beta_binomial":
        # var = n*p*(1-p)*(1 + (n-1)*rho). Hold n and p, move rho.
        n, a, b = float(params["n"]), float(params["a"]), float(params["b"])
        total = a + b
        if n <= 1 or total <= 0:
            return projection
        p = a / total
        rho = 1.0 / (total + 1.0)
        target = (scale * scale * (1.0 + (n - 1.0) * rho) - 1.0) / (n - 1.0)
        # rho -> 0 is the binomial limit and the narrowest this family reaches;
        # receptions is under-dispersed, so that floor is a real constraint
        # rather than a formality.
        rho_new = min(max(target, 1e-6), 1.0 - 1e-6)
        total_new = 1.0 / rho_new - 1.0
        params = {
            "n": n,
            "a": max(p * total_new, 1e-6),
            "b": max((1.0 - p) * total_new, 1e-6),
        }

    else:
        return projection

    return Projection(
        market_key=projection.market_key,
        distribution=distribution,
        params=params,
        mean=mean,
        quantiles=_quantiles(distribution, params),
        volume=projection.volume,
        efficiency=projection.efficiency,
        matchup_multiplier=projection.matchup_multiplier,
    )


def _lift_location_to_positive_median(
    distribution: str, params: dict[str, float], mean: float
) -> dict[str, float]:
    """Raise `loc` just far enough that the median is not negative.

    THE DEFECT THIS CLOSES. `rescale` widens a right-skewed family by the
    location-scale identity, which sends `loc` to `mean * (1 - scale)` — negative
    for any scale above 1. That is normally harmless: the docstring above trades
    a little misplaced mass below zero against a systematic bias through the
    middle of the range, and it is the right trade.

    It stops being harmless when the gamma's SHAPE has collapsed. Measured on the
    live table, rec_yards rows with a negative median averaged shape 0.419 and
    none exceeded 0.617, against 4.305 for the rest — and a shape that low puts
    the median itself below zero, which is not a little misplaced tail mass but a
    nonsense headline number. 163 of 38,616 rows (0.4%). rush_yards never hit it
    because its shape floors near 9.

    `_blended_sd` removes most of the cause by shrinking the sd/mean ratio that
    collapses the shape. This is the backstop for whatever survives that, and it
    is deliberately MINIMAL: it searches for the most negative `loc` that still
    yields a non-negative median, so the negative tail QB rushing genuinely needs
    (23% of those games lose yardage) is preserved as far as it can be.

    Re-solving at each candidate holds the mean and the target SD exactly —
    `gamma_params(m, sd, L)` is algebraically identical to the location-scale
    result when `L` is the unclamped `shifted_loc`, so this is a no-op on every
    row whose median was already fine.
    """
    if distribution_median(distribution, params) >= 0.0:
        return params

    target_sd = distribution_sd(distribution, params)
    solve = gamma_params if distribution == "gamma" else lognormal_params

    low, high = float(params.get("loc", 0.0)), 0.0
    if distribution_median(distribution, solve(mean, target_sd, high)) < 0.0:
        # Cannot be rescued by moving the location alone; a support starting at
        # zero is the most that can be done.
        return solve(mean, target_sd, high)

    for _ in range(40):
        mid = 0.5 * (low + high)
        if distribution_median(distribution, solve(mean, target_sd, mid)) < 0.0:
            low = mid
        else:
            high = mid
    return solve(mean, target_sd, high)


def shift_mean(projection: Projection, multiplier: float) -> Projection:
    """Scale a projection by `multiplier`, keeping its shape and relative width.

    Applies the bias correction from `worker.core.calibration`. Unlike
    `rescale`, which turns the width at a fixed centre, this moves the whole
    distribution: X -> kX. The coefficient of variation is preserved, which is
    the right behaviour for a volume correction — a quarterback who throws 10%
    more than projected is not thereby 10% more predictable.

    Every family supports this, including the two that have no free width
    parameter: scaling a Poisson's lambda or a Bernoulli's p moves the mean,
    which is exactly what is wanted here.
    """
    if (
        multiplier <= 0
        or not math.isfinite(multiplier)
        or abs(multiplier - 1.0) < 1e-9
    ):
        return projection

    distribution = projection.distribution
    params = dict(projection.params)
    mean = projection.mean * multiplier

    if distribution == "normal":
        params = {
            "mu": float(params["mu"]) * multiplier,
            "sigma": max(float(params["sigma"]) * multiplier, 1e-6),
        }
    elif distribution == "gamma":
        params = {
            "shape": float(params["shape"]),
            "scale": max(float(params["scale"]) * multiplier, 1e-9),
            "loc": float(params.get("loc", 0.0)) * multiplier,
        }
    elif distribution == "lognormal":
        params = {
            "mu": float(params["mu"]) + math.log(multiplier),
            "sigma": float(params["sigma"]),
            "loc": float(params.get("loc", 0.0)) * multiplier,
        }
    elif distribution == "poisson":
        params = {"lam": max(float(params["lam"]) * multiplier, 1e-9)}
    elif distribution == "negative_binomial":
        p = float(params["p"])
        dispersion = 1.0 / p if p > 0 else 2.0
        params = negative_binomial_params(mean, dispersion)
    elif distribution == "beta_binomial":
        # n is a ceiling on the trial count, so it scales with the mean too;
        # holding it fixed would reintroduce the impossible-outcome ceiling that
        # the beta-binomial fix removed.
        n, a, b = float(params["n"]), float(params["a"]), float(params["b"])
        total = a + b
        if total <= 0:
            return projection
        scaled_n = max(math.ceil(n * multiplier), 1)
        p = min(max((n * a / total) * multiplier / scaled_n, 1e-4), 1 - 1e-3)
        params = {"n": float(scaled_n), "a": p * total, "b": (1.0 - p) * total}
    elif distribution == "bernoulli":
        params = {"p": min(max(float(params["p"]) * multiplier, 1e-4), 1 - 1e-4)}
        mean = params["p"]
    else:
        return projection

    return Projection(
        market_key=projection.market_key,
        distribution=distribution,
        params=params,
        mean=mean,
        quantiles=_quantiles(distribution, params),
        volume=projection.volume,
        efficiency=projection.efficiency,
        matchup_multiplier=projection.matchup_multiplier,
    )


# -----------------------------------------------------------------------------
# Matchup
# -----------------------------------------------------------------------------
def matchup_multiplier(
    allowed: float | None, baseline: float | None, shrinkage: float | None = None
) -> float:
    """How much this defense inflates or suppresses a position's output.

    `allowed` is the opponent-adjusted per-game allowance to this position;
    `baseline` is the league mean of that same quantity at the same cutoff. The
    ratio is 1.0 for an average defense.

    Clamped to [0.6, 1.4]. The adjustment is fitted on a barely-connected
    schedule graph early in the season and will occasionally report a confident,
    spurious extreme — the clamp bounds the damage without discarding the signal.
    """
    if not allowed or not baseline or baseline <= 0:
        return 1.0
    ratio = allowed / baseline
    if shrinkage is not None:
        # Pull toward neutral in proportion to how little the rating is trusted.
        ratio = 1.0 + (ratio - 1.0) * max(0.0, min(1.0, shrinkage))
    return min(max(ratio, 0.6), 1.4)


def _sd(mean: float, observed_sd: float | None) -> float:
    """Projected SD, floored relative to the mean.

    A player whose three games happened to land near-identically produces a
    sample SD near zero, which would turn into a 99% confidence. The floor is
    what stops a small sample from masquerading as certainty.
    """
    floor = MIN_RELATIVE_SD * max(abs(mean), 1e-6)
    if observed_sd is None or not math.isfinite(observed_sd):
        return floor
    return max(observed_sd, floor)


# -----------------------------------------------------------------------------
# Baselines
# -----------------------------------------------------------------------------
def median_of(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _cv_samples(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[float]]]:
    """Per-player sd/mean ratios, by position and `{stat}_cv` key.

    Only rows carrying BOTH a mean and a positive SD contribute. A player with
    one game has a NULL `stddev_samp` and simply does not vote on the typical
    spread — which is the point: the baseline exists so that a player with no
    usable width of their own can borrow one.
    """
    samples: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        position = row.get("position_group")
        if not position:
            continue
        bucket = samples.setdefault(str(position), {})
        for key in row:
            if not key.endswith("_pg") or key.startswith(("prior_", "team_", "opp_")):
                continue
            mean = _value(row, key)
            sd = _value(row, key.replace("_pg", "_sd"))
            if mean is None or mean <= 0 or sd is None or sd <= 0:
                continue
            bucket.setdefault(f"{key[: -len('_pg')]}_cv", []).append(sd / mean)
    return samples


def position_baselines(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Median of each per-game stat, by position, over the projectable pool.

    Median rather than mean: the pool includes deep-bench players whose zeros
    would drag a mean well below anything a projectable player produces, and the
    baseline exists to be a sensible thing to shrink a thin sample TOWARD.
    """
    by_position: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        position = row.get("position_group")
        if not position:
            continue
        bucket = by_position.setdefault(str(position), {})
        for key in row:
            if not key.endswith("_pg") or key.startswith(("prior_", "team_", "opp_")):
                continue
            number = _value(row, key)
            if number is not None:
                bucket.setdefault(key, []).append(number)

    baselines: dict[str, dict[str, float]] = {}
    for position, stats in by_position.items():
        resolved: dict[str, float] = {}
        for key, values in stats.items():
            if values:
                resolved[key] = median_of(values)
        baselines[position] = resolved

    # Shrinkage strength, estimated per stat rather than fixed.
    #
    # A single pseudo-count for every market was measurably wrong. Pass attempts
    # separate quarterbacks enormously — a starter throws 35 a game, a backup 5 —
    # while varying only moderately week to week for the same player. Shrinking
    # a starter halfway to a median that includes every backup drags the
    # projection down systematically, and the first backtest showed exactly that:
    # pass_attempts scored a Brier skill of -0.079 and pass_completions -0.032,
    # both WORSE than predicting the base rate, while stats without that
    # starter/bench gulf scored well.
    #
    # The empirical-Bayes answer is that shrinkage should be the ratio of
    # within-player variance to between-player variance. When players differ far
    # more than any one player varies, believe the player; when a stat is noisy
    # relative to how much players differ, believe the population. Both variances
    # are already in the frame — the per-player mean and standard deviation of
    # each stat — so this costs one pass and no new data.
    for position, stats in by_position.items():
        resolved = baselines.setdefault(position, {})
        for key in list(stats.keys()):
            means = stats[key]
            if len(means) < 8:
                continue
            average = sum(means) / len(means)
            between = sum((m - average) ** 2 for m in means) / (len(means) - 1)

            sd_key = key.replace("_pg", "_sd")
            within_values = [
                v
                for row in rows
                if str(row.get("position_group") or "") == position
                and (v := _value(row, sd_key)) is not None
                and v > 0
            ]
            if not within_values or between <= 0:
                continue
            within = sum(v * v for v in within_values) / len(within_values)
            resolved[f"{key}__pseudo_games"] = min(
                max(within / between, MIN_PSEUDO_GAMES), MAX_PSEUDO_GAMES
            )

    # Typical game-to-game SPREAD, as a coefficient of variation, per stat.
    #
    # Width is blended and shrunk exactly like every mean is (`_blended_sd`), and
    # this is what it shrinks toward. Held as sd/mean rather than as a raw SD
    # because spread scales with level: a receiver averaging 80 yards and one
    # averaging 20 do not share a standard deviation, but they do share a
    # coefficient of variation closely enough to be a sensible prior.
    #
    # Median rather than mean, for the same reason the level baselines use one —
    # a handful of players with a near-zero mean produce enormous ratios.
    for position, ratios in _cv_samples(rows).items():
        resolved = baselines.setdefault(position, {})
        for key, values in ratios.items():
            if len(values) >= 8:
                resolved[key] = median_of(values)

    # Touchdown conversion rates are POOLED, not per-player medians: they are
    # ratios of counts, and the median of per-player ratios is dominated by
    # players with one or two chances, whose rate is 0 or 1 and neither.
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        position = row.get("position_group")
        if not position:
            continue
        bucket = totals.setdefault(str(position), {})
        for key in (
            "goal_line_opportunities",
            "goal_line_tds",
            "open_field_opportunities",
            "open_field_tds",
        ):
            bucket[key] = bucket.get(key, 0.0) + (_value(row, key, 0.0) or 0.0)
        # Games are accumulated in the same pass so the opportunity rates below
        # divide by the right denominator without a second scan.
        bucket["games"] = bucket.get("games", 0.0) + (
            _value(row, "games_played", 0.0) or 0.0
        )

    for position, counts in totals.items():
        resolved = baselines.setdefault(position, {})
        goal_line_chances = counts.get("goal_line_opportunities", 0.0)
        open_chances = counts.get("open_field_opportunities", 0.0)
        if goal_line_chances > 0:
            resolved["goal_line_conversion"] = (
                counts.get("goal_line_tds", 0.0) / goal_line_chances
            )
        if open_chances > 0:
            resolved["open_field_conversion"] = (
                counts.get("open_field_tds", 0.0) / open_chances
            )
        games = counts.get("games", 0.0)
        if games > 0:
            resolved["goal_line_chances_pg"] = goal_line_chances / games
            resolved["open_field_chances_pg"] = open_chances / games

    return baselines


def _blend_stat(
    row: dict[str, Any], stat: str, baselines: dict[str, float]
) -> float:
    """Blend one per-game volume stat across current, prior and baseline."""
    return blend(
        current=_volume(row, f"{stat}_pg"),
        current_games=_value(row, "games_played", 0.0) or 0.0,
        prior=_value(row, f"prior_{stat}_pg"),
        prior_games=_value(row, "prior_games_played", 0.0) or 0.0,
        prior_weight=_value(row, "prior_weight", 0.0) or 0.0,
        baseline=baselines.get(f"{stat}_pg", 0.0),
        pseudo_games=baselines.get(
            f"{stat}_pg__pseudo_games", BASELINE_PSEUDO_GAMES
        ),
    )


def _rate(row: dict[str, Any], numerator: str, denominator: str) -> float | None:
    """Per-unit efficiency from season totals, or None when undefined.

    Undefined is not zero: yards per attempt at zero attempts says nothing, and
    returning 0.0 would tell the blender this player is terrible rather than
    unmeasured.
    """
    top = _value(row, f"{numerator}_pg")
    bottom = _value(row, f"{denominator}_pg")
    if top is None or bottom is None or bottom <= 0:
        return None
    return top / bottom


def _blend_rate(
    row: dict[str, Any],
    numerator: str,
    denominator: str,
    baselines: dict[str, float],
) -> float:
    baseline_top = baselines.get(f"{numerator}_pg", 0.0)
    baseline_bottom = baselines.get(f"{denominator}_pg", 0.0)
    baseline = baseline_top / baseline_bottom if baseline_bottom > 0 else 0.0

    current = _rate(row, numerator, denominator)
    prior_top = _value(row, f"prior_{numerator}_pg")
    prior_bottom = _value(row, f"prior_{denominator}_pg")
    prior = (
        prior_top / prior_bottom
        if prior_top is not None and prior_bottom and prior_bottom > 0
        else None
    )

    return blend(
        current=current,
        # Weight the rate by its denominator, not by games: a rate from 40
        # attempts is worth more than one from 4.
        current_games=(_value(row, f"{denominator}_pg", 0.0) or 0.0)
        * (_value(row, "games_played", 0.0) or 0.0),
        prior=prior,
        prior_games=(prior_bottom or 0.0)
        * (_value(row, "prior_games_played", 0.0) or 0.0),
        prior_weight=_value(row, "prior_weight", 0.0) or 0.0,
        baseline=baseline,
        pseudo_games=BASELINE_PSEUDO_GAMES * max(baseline_bottom, 1.0),
    )


def _cv(row: dict[str, Any], stat: str, prefix: str = "") -> float | None:
    """One season's spread as a coefficient of variation, or None if undefined."""
    mean = _value(row, f"{prefix}{stat}_pg")
    sd = _value(row, f"{prefix}{stat}_sd")
    if mean is None or mean <= 0 or sd is None or sd <= 0:
        return None
    return sd / mean


def _blended_sd(
    row: dict[str, Any], stat: str, baselines: dict[str, float], mean: float
) -> float:
    """Projected SD, blended across current season, prior season and baseline.

    WHY THIS IS NOT JUST `{stat}_sd`. Reading the current season's sample SD
    directly had two faults that only showed up once the early-season weeks were
    examined (Phase 6a):

      * **It is unreachable early.** `stddev_samp` over one game is NULL, so
        weeks 1 and 2 fell to `MIN_RELATIVE_SD` — the NARROWEST width the model
        can produce — on the thinnest evidence of the season. That inverts
        CLAUDE.md §6, which asks for wider uncertainty early. `prior_{stat}_sd`
        was already materialized by `features.prior_column_names()` and simply
        never read.
      * **It was measured at the wrong level.** The raw SD came from the
        player's own scale while the mean it decorated had been shrunk toward a
        position baseline. A receiver averaging 80 yards who is projected at 45
        was handed the spread of an 80-yard receiver.

    Blending the COEFFICIENT of variation fixes both: it is the scale-free
    quantity, so it survives the shrinkage the mean underwent, and it has a
    position baseline to fall back on when neither season offers one.

    It also removes the cause of the negative medians Phase 5 recorded. Those
    came from `rescale` widening a gamma whose shape had collapsed below ~0.6,
    which requires sd/mean above about 1.3 — an unshrunk per-player ratio.
    Shrinking the ratio toward its position baseline keeps the shape in range.
    """
    weight = _value(row, "prior_weight", 0.0) or 0.0
    cv = blend(
        current=_cv(row, stat),
        current_games=_value(row, "games_played", 0.0) or 0.0,
        prior=_cv(row, stat, prefix="prior_"),
        prior_games=_value(row, "prior_games_played", 0.0) or 0.0,
        prior_weight=weight,
        baseline=baselines.get(f"{stat}_cv", MIN_RELATIVE_SD),
        pseudo_games=baselines.get(
            f"{stat}_pg__pseudo_games", BASELINE_PSEUDO_GAMES
        ),
    )
    return _sd(mean, cv * abs(mean))


def _dispersion(row: dict[str, Any], stat: str, baselines: dict[str, float]) -> float:
    """Variance/mean for a count stat, clamped to a usable range.

    Derived from the same blended CV as `_blended_sd` rather than from the
    current season's sample alone, so a count market has a usable dispersion in
    week 1 instead of falling to the flat 2.0 default. var/mean = (cv*mean)^2 /
    mean = cv^2 * mean.
    """
    mean = _value(row, f"{stat}_pg")
    if mean is None or mean <= 0:
        mean = baselines.get(f"{stat}_pg")
    if not mean or mean <= 0:
        return 2.0
    sd = _blended_sd(row, stat, baselines, mean)
    return min(max((sd * sd) / mean, MIN_DISPERSION), MAX_DISPERSION)


def _opponent(row: dict[str, Any], metric: str, position: str) -> float | None:
    return _value(row, f"opp_{metric}_{position}")


# -----------------------------------------------------------------------------
# Anytime touchdown
# -----------------------------------------------------------------------------
# Measured over 2024-25, on players with at least 6 games in a season. The
# observed P(score) sits BELOW what a Poisson with the same mean predicts:
#
#   pos   TDs/game   observed P(score)   Poisson 1-exp(-lam)   gap
#   RB      0.455          0.335                0.366          -3.1pp
#   QB      0.370          0.285                0.309          -2.4pp
#   WR      0.298          0.251                0.258          -0.7pp
#   TE      0.250          0.225                0.221          +0.4pp
#
# Touchdowns cluster: a player who scores once is likelier to score twice,
# because the same game script and goal-line role produced both. Clustering puts
# MORE mass on zero than a Poisson allows, so assuming Poisson would overstate
# every anytime-TD probability — worst for running backs, by three points.
#
# That matters more here than anywhere else in the model. Anytime TD is the most
# lopsidedly priced market we serve, it is where the de-vig choice already bites
# hardest, and a systematic +3pp would sit right on top of the 5% edge
# threshold. So P(score) comes from a negative-binomial zero,
#
#     P(score) = 1 - (1 + lam/k)^(-k)
#
# with k the clustering parameter solved from the table above. k -> infinity
# recovers Poisson, which is what TE gets since it showed no clustering.
#
# CAVEAT for the calibration report: these four constants were fitted on the same
# seasons the backtest evaluates. They are four scalars describing a structural
# fact rather than a per-player fit, so the leakage is small — but it is not
# zero, and the report should say so rather than let the numbers look cleaner
# than they are.
TD_CLUSTERING_K: dict[str, float] = {
    "RB": 1.9,
    "QB": 1.8,
    "WR": 5.0,
    "TE": math.inf,  # no clustering measured; Poisson
}
DEFAULT_TD_CLUSTERING_K = 3.0

# Share of touchdowns originating inside the goal line, measured 2024-25. Used
# only as a prior for players with no scoring history of their own.
GOAL_LINE_TD_SHARE: dict[str, float] = {
    "RB": 0.603,
    "TE": 0.514,
    "QB": 0.420,
    "WR": 0.285,
}


def score_probability(expected_tds: float, position: str) -> float:
    """P(at least one touchdown) from an expected count.

    Not 1 - exp(-lam): see TD_CLUSTERING_K for why that overstates it.
    """
    lam = max(expected_tds, 0.0)
    if lam <= 0:
        return 0.0
    k = TD_CLUSTERING_K.get(position, DEFAULT_TD_CLUSTERING_K)
    if not math.isfinite(k):
        return 1.0 - math.exp(-lam)
    return 1.0 - (1.0 + lam / k) ** (-k)


def project_anytime_td(
    row: dict[str, Any],
    baselines: dict[str, float],
    league: dict[str, dict[str, float]] | None = None,
) -> Projection | None:
    """Opportunity x finish rate, in two ranges (CLAUDE.md §6).

    Goal-line and open-field chances are modelled separately because they
    convert at completely different rates — an RB scores on 28% of goal-line
    opportunities and 1.6% of the rest — and because the MIX differs sharply by
    position. Only 28.5% of WR touchdowns start inside the ten, against 60% for
    RBs. A single goal-line model, which is the obvious reading of the brief,
    would systematically underrate wide receivers: precisely the players books
    price as longshots, and precisely where a mispriced probability is most
    expensive.

    Both components are shrunk toward the position's conversion rate, heavily so
    early in the season — a player with three goal-line carries and one score
    has not demonstrated a 33% finish rate. This is CLAUDE.md §6's "TD
    probabilities especially should lean on priors early".
    """
    league = league or {}
    position = str(row.get("position_group") or "")
    games = _value(row, "games_played", 0.0) or 0.0
    if games <= 0:
        return None

    raw_goal_line = _value(row, "goal_line_opportunities", 0.0) or 0.0
    raw_open = _value(row, "open_field_opportunities", 0.0) or 0.0
    goal_line_tds = _value(row, "goal_line_tds", 0.0) or 0.0
    open_tds = _value(row, "open_field_tds", 0.0) or 0.0

    if raw_goal_line <= 0 and raw_open <= 0:
        return None

    # OPPORTUNITY RATES ARE SHRUNK, not taken raw. The first backtest showed why:
    # the bottom two bins hold 44.6% of every anytime-TD prediction, and both
    # were badly underconfident — the model said 6% where 16.5% actually scored.
    #
    # The cause was that conversion rates were shrunk toward the position while
    # opportunity COUNTS were not. A player with four games and no goal-line
    # carries got a goal-line rate of exactly zero, which is a confident claim
    # that he will never get one. He will. Measured on 2024-25, players with no
    # goal-line work through week 6 went on to score in 19.6% of games as
    # receivers, 21.1% as tight ends and 17.5% as running backs. For tight ends
    # that is barely different from those WITH goal-line history (23.1%), so
    # treating the distinction as absolute was the single largest error in the
    # market.
    #
    # Same empirical-Bayes shape used everywhere else: observed counts plus a
    # position-typical rate worth OPPORTUNITY_PRIOR_GAMES games of evidence.
    def opportunity_rate(observed: float, baseline_key: str) -> float:
        baseline = (league.get(position) or {}).get(baseline_key)
        if baseline is None:
            baseline = baselines.get(baseline_key, 0.0)
        return (observed + baseline * OPPORTUNITY_PRIOR_GAMES) / (
            games + OPPORTUNITY_PRIOR_GAMES
        )

    goal_line_chances = opportunity_rate(raw_goal_line, "goal_line_chances_pg")
    open_chances = opportunity_rate(raw_open, "open_field_chances_pg")

    # Conversion rates, shrunk toward the position baseline by opportunity count.
    def conversion(scored: float, chances_total: float, fallback: float) -> float:
        if chances_total <= 0:
            return fallback
        return (scored + fallback * TD_RATE_PRIOR_CHANCES) / (
            chances_total + TD_RATE_PRIOR_CHANCES
        )

    position_league = league.get(position) or {}
    goal_line_rate = conversion(
        goal_line_tds,
        _value(row, "goal_line_opportunities", 0.0) or 0.0,
        position_league.get("goal_line_conversion", DEFAULT_GOAL_LINE_CONVERSION),
    )
    open_rate = conversion(
        open_tds,
        _value(row, "open_field_opportunities", 0.0) or 0.0,
        position_league.get("open_field_conversion", DEFAULT_OPEN_FIELD_CONVERSION),
    )

    # Opponent effect. Goal-line scoring is defended by what a unit concedes near
    # its own line; open-field scoring by explosive plays allowed. Both come from
    # the same adjusted TD-allowed rates, which is the closest signal we carry.
    rush_like = position in ("RB", "QB")
    metric = "adj_rush_tds_allowed_pg" if rush_like else "adj_rec_tds_allowed_pg"
    allowed = _opponent(row, metric, position)
    expected_allowed = (league.get(position) or {}).get(metric)
    multiplier = matchup_multiplier(
        allowed, expected_allowed, _value(row, "shrinkage_weight")
    )

    expected_tds = (
        goal_line_chances * goal_line_rate + open_chances * open_rate
    ) * multiplier

    probability = score_probability(expected_tds, position)
    probability = min(max(probability, 1e-4), 1.0 - 1e-4)

    return finalize(
        "anytime_td",
        "bernoulli",
        {"p": probability},
        expected_tds,
        volume=goal_line_chances + open_chances,
        efficiency=goal_line_rate,
        matchup_multiplier=multiplier,
    )


# Pseudo-opportunities of shrinkage on a conversion rate. Three goal-line
# carries and one score is not a 33% finisher, and anytime TD is the market
# where an overconfident rate is most expensive.
TD_RATE_PRIOR_CHANCES = 12.0

# Games of position-typical opportunity worth crediting a player who has not
# generated that opportunity himself yet. Deliberately smaller than
# TD_RATE_PRIOR_CHANCES because opportunity is a role, which is more stable and
# more quickly evident than a conversion rate: three games of goal-line usage
# says considerably more about the next game than three goal-line carries say
# about a finish rate.
OPPORTUNITY_PRIOR_GAMES = 3.0

# League-wide fallbacks, measured 2024-25 across all skill positions.
DEFAULT_GOAL_LINE_CONVERSION = 0.35
DEFAULT_OPEN_FIELD_CONVERSION = 0.03


# -----------------------------------------------------------------------------
# Per-market projection
# -----------------------------------------------------------------------------
def project(
    row: dict[str, Any],
    market_key: str,
    distribution: str,
    baselines: dict[str, float],
    league: dict[str, dict[str, float]] | None = None,
) -> Projection | None:
    """Build one market's distribution for one player-game row.

    `baselines` is this player's position baseline; `league` is every position's,
    needed because a QB's passing markets depend on what the opponent allows to
    RECEIVERS, not to quarterbacks.
    """
    league = league or {}

    def defensive_ratio(metric: str, positions: tuple[str, ...]) -> float:
        """Matchup multiplier against the league mean for the same positions."""
        allowed, expected = 0.0, 0.0
        for position in positions:
            value = _opponent(row, metric, position)
            mean = (league.get(position) or {}).get(f"{metric}")
            if value is None or mean is None:
                continue
            allowed += value
            expected += mean
        if expected <= 0:
            return 1.0
        return matchup_multiplier(
            allowed, expected, _value(row, "shrinkage_weight")
        )

    if market_key == "pass_attempts":
        mean = _blend_stat(row, "pass_attempts", baselines)
        if mean <= 0:
            return None
        return finalize(
            market_key,
            distribution,
            negative_binomial_params(mean, _dispersion(row, "pass_attempts", baselines)),
            mean,
            volume=mean,
        )

    if market_key == "pass_completions":
        attempts = _blend_stat(row, "pass_attempts", baselines)
        rate = _blend_rate(row, "pass_completions", "pass_attempts", baselines)
        mean = attempts * rate
        if mean <= 0:
            return None
        return finalize(
            market_key,
            distribution,
            negative_binomial_params(mean, _dispersion(row, "pass_completions", baselines)),
            mean,
            volume=attempts,
            efficiency=rate,
        )

    if market_key == "pass_yards":
        attempts = _blend_stat(row, "pass_attempts", baselines)
        yards_per_attempt = _blend_rate(row, "pass_yards", "pass_attempts", baselines)
        # Passing is defended by what a team concedes to receivers.
        multiplier = defensive_ratio("adj_rec_yards_allowed_pg", ("WR", "TE"))
        mean = attempts * yards_per_attempt * multiplier
        if mean <= 0:
            return None
        return finalize(
            market_key,
            distribution,
            {"mu": mean, "sigma": _blended_sd(row, "pass_yards", baselines, mean)},
            mean,
            volume=attempts,
            efficiency=yards_per_attempt,
            matchup_multiplier=multiplier,
        )

    if market_key == "pass_tds":
        attempts = _blend_stat(row, "pass_attempts", baselines)
        rate = _blend_rate(row, "pass_tds", "pass_attempts", baselines)
        multiplier = defensive_ratio("adj_rec_tds_allowed_pg", ("WR", "TE"))
        mean = max(attempts * rate * multiplier, 1e-4)
        return finalize(
            market_key,
            distribution,
            {"lam": mean},
            mean,
            volume=attempts,
            efficiency=rate,
            matchup_multiplier=multiplier,
        )

    if market_key == "rush_attempts":
        mean = _blend_stat(row, "rush_attempts", baselines)
        if mean <= 0:
            return None
        return finalize(
            market_key,
            distribution,
            negative_binomial_params(mean, _dispersion(row, "rush_attempts", baselines)),
            mean,
            volume=mean,
        )

    if market_key == "rush_yards":
        carries = _blend_stat(row, "rush_attempts", baselines)
        yards_per_carry = _blend_rate(row, "rush_yards", "rush_attempts", baselines)
        position = str(row.get("position_group") or "")
        multiplier = defensive_ratio("adj_rush_yards_allowed_pg", (position,))
        mean = carries * yards_per_carry * multiplier
        observed_sd = _blended_sd(row, "rush_yards", baselines, mean)

        if distribution == "normal":
            params = {"mu": mean, "sigma": observed_sd}
        else:
            # A gamma anchored at zero cannot describe QB rushing, where 26% of
            # games are negative because sacks are charged as rushing losses.
            # The location is placed a few SDs below the mean so the negative
            # tail has somewhere to live.
            loc = min(0.0, mean - 3.0 * observed_sd)
            params = gamma_params(mean, observed_sd, loc)
        if mean <= 0:
            return None
        return finalize(
            market_key,
            distribution,
            params,
            mean,
            volume=carries,
            efficiency=yards_per_carry,
            matchup_multiplier=multiplier,
        )

    if market_key == "receptions":
        position = str(row.get("position_group") or "")
        targets = _blend_stat(row, "targets", baselines)
        catch_rate = _blend_rate(row, "receptions", "targets", baselines)
        multiplier = defensive_ratio("adj_receptions_allowed_pg", (position,))
        targets *= multiplier
        mean = targets * catch_rate
        if mean <= 0 or targets <= 0:
            return None

        if distribution == "beta_binomial":
            # Spread of the TARGET count, which is what n must cover.
            target_sd = math.sqrt(max(targets, 1.0) * _dispersion(row, "targets", baselines))
            params = beta_binomial_params(
                targets,
                catch_rate,
                _dispersion(row, "receptions", baselines),
                trials_sd=target_sd,
            )
        else:
            params = negative_binomial_params(mean, _dispersion(row, "receptions", baselines))
        return finalize(
            market_key,
            distribution,
            params,
            mean,
            volume=targets,
            efficiency=catch_rate,
            matchup_multiplier=multiplier,
        )

    if market_key == "rec_yards":
        position = str(row.get("position_group") or "")
        targets = _blend_stat(row, "targets", baselines)
        catch_rate = _blend_rate(row, "receptions", "targets", baselines)
        yards_per_reception = _blend_rate(row, "rec_yards", "receptions", baselines)

        # TWO adjustments, not one. A defense that concedes catches but tackles
        # immediately is a different matchup from one that concedes neither, and
        # a single multiplier on the yardage total cannot tell them apart.
        #
        # Applying one combined multiplier here would also make the volume stage
        # dead weight: blended targets multiplied by a blended catch rate
        # collapses algebraically to blended receptions, so without separate
        # adjustments the decomposition does no work at all.
        volume_multiplier = defensive_ratio("adj_receptions_allowed_pg", (position,))
        yards_multiplier = defensive_ratio("adj_rec_yards_allowed_pg", (position,))
        # Yards allowed PER RECEPTION, isolated from the volume already counted.
        efficiency_multiplier = (
            yards_multiplier / volume_multiplier if volume_multiplier > 0 else 1.0
        )
        multiplier = volume_multiplier * efficiency_multiplier

        mean = (
            targets * catch_rate * volume_multiplier
        ) * (yards_per_reception * efficiency_multiplier)
        if mean <= 0:
            return None
        observed_sd = _blended_sd(row, "rec_yards", baselines, mean)

        if distribution == "lognormal":
            params = lognormal_params(mean, observed_sd, 0.0)
        elif distribution == "gamma":
            params = gamma_params(mean, observed_sd, 0.0)
        else:
            params = {"mu": mean, "sigma": observed_sd}
        return finalize(
            market_key,
            distribution,
            params,
            mean,
            volume=targets * catch_rate * volume_multiplier,
            efficiency=yards_per_reception * efficiency_multiplier,
            matchup_multiplier=multiplier,
        )

    if market_key == "anytime_td":
        return project_anytime_td(row, baselines, league)

    raise ValueError(f"No projector for market {market_key!r}")
