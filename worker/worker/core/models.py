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

from worker.core.probability import prob_over, validate_params
from worker.logging_setup import get_logger

log = get_logger(__name__)

# Pseudo-count for shrinkage toward the position baseline, in games. At 4, a
# player with 4 games carries equal weight from their own record and from the
# baseline; by 12 games their own record dominates roughly 3:1.
BASELINE_PSEUDO_GAMES = 4.0

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
    trials: float, success_rate: float, dispersion: float
) -> dict[str, float]:
    """Solve n, a, b from expected trials, a success rate and a dispersion target.

    For a beta-binomial, variance/mean = (1-p)(1 + (n-1)rho) with
    rho = 1/(a+b+1) the intra-class correlation. Inverting for rho and pinning
    p = a/(a+b) gives a and b.

    rho collapses to ~0 when the requested dispersion is at or below the pure
    binomial value (1-p); that is the binomial limit and a large a+b represents
    it faithfully.
    """
    n = max(int(round(trials)), 1)

    # p must be derived from the mean AFTER n is rounded, not taken as the raw
    # success rate. A beta-binomial's mean is n*p, so pairing a rounded n with an
    # unrounded rate silently moves the mean — and for the small n typical of a
    # low-usage receiver it moves it a lot. Rounding 0.78 targets to n=1 while
    # keeping a catch rate of 1.0 produced a distribution that was certain of
    # exactly one reception.
    mean = max(trials * success_rate, 1e-6)
    p = min(max(mean / n, 1e-3), 1 - 1e-3)

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
def _quantiles(distribution: str, params: dict[str, float], mean: float) -> dict[str, float]:
    """Cached display quantiles, found by inverting prob_over numerically.

    Bisection rather than a per-family ppf: it works for every family including
    the discrete ones without special-casing, and it runs once per projection.
    These back only the SECONDARY projected range on the player page — never the
    headline claim (CLAUDE.md §1) — so robustness beats precision.
    """
    spread = max(abs(mean), 1.0)
    low, high = mean - 12 * spread, mean + 12 * spread

    result: dict[str, float] = {}
    for q in QUANTILES:
        target = 1.0 - q  # prob_over is a survival function
        lo, hi = low, high
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if prob_over(distribution, params, mid) > target:
                lo = mid
            else:
                hi = mid
        result[f"p{int(q * 100)}"] = 0.5 * (lo + hi)
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
        quantiles=_quantiles(distribution, params, mean),
        **extra,
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
        for key, value in row.items():
            if not key.endswith("_pg") or key.startswith(("prior_", "team_", "opp_")):
                continue
            number = _value(row, key)
            if number is not None:
                bucket.setdefault(key, []).append(number)

    baselines: dict[str, dict[str, float]] = {}
    for position, stats in by_position.items():
        resolved: dict[str, float] = {}
        for key, values in stats.items():
            if not values:
                continue
            ordered = sorted(values)
            middle = len(ordered) // 2
            resolved[key] = (
                ordered[middle]
                if len(ordered) % 2
                else 0.5 * (ordered[middle - 1] + ordered[middle])
            )
        baselines[position] = resolved
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


def _dispersion(row: dict[str, Any], stat: str) -> float:
    """Observed variance/mean for a count stat, clamped to a usable range."""
    mean = _value(row, f"{stat}_pg")
    sd = _value(row, f"{stat}_sd")
    if not mean or mean <= 0 or sd is None or sd <= 0:
        return 2.0
    return min(max((sd * sd) / mean, MIN_DISPERSION), MAX_DISPERSION)


def _opponent(row: dict[str, Any], metric: str, position: str) -> float | None:
    return _value(row, f"opp_{metric}_{position}")


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
            negative_binomial_params(mean, _dispersion(row, "pass_attempts")),
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
            negative_binomial_params(mean, _dispersion(row, "pass_completions")),
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
            {"mu": mean, "sigma": _sd(mean, _value(row, "pass_yards_sd"))},
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
            negative_binomial_params(mean, _dispersion(row, "rush_attempts")),
            mean,
            volume=mean,
        )

    if market_key == "rush_yards":
        carries = _blend_stat(row, "rush_attempts", baselines)
        yards_per_carry = _blend_rate(row, "rush_yards", "rush_attempts", baselines)
        position = str(row.get("position_group") or "")
        multiplier = defensive_ratio("adj_rush_yards_allowed_pg", (position,))
        mean = carries * yards_per_carry * multiplier
        observed_sd = _sd(mean, _value(row, "rush_yards_sd"))

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
            params = beta_binomial_params(
                targets, catch_rate, _dispersion(row, "receptions")
            )
        else:
            params = negative_binomial_params(mean, _dispersion(row, "receptions"))
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
        observed_sd = _sd(mean, _value(row, "rec_yards_sd"))

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
        # Built in Phase 3e, which models it as goal-line opportunity x finish
        # rate rather than as a per-game average (CLAUDE.md §6).
        return None

    raise ValueError(f"No projector for market {market_key!r}")
