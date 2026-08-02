"""Point-in-time variance calibration.

WHY THIS EXISTS. The first full backtest was well calibrated in the middle of
its range and badly overconfident at the edges: it said 0.96 and hit 0.77.
Running every actual through its own projected CDF located the cause precisely
— the projected distributions are the wrong WIDTH. Measured over six weeks
spanning both seasons, the share of outcomes landing outside a projection's own
5th-to-95th percentile band was:

    rec_yards         26.5%      pass_completions  20.5%
    pass_yards        23.6%      rush_attempts     18.2%
    pass_attempts     22.7%      receptions         6.9%
    rush_yards        21.4%      pass_tds           5.3%

against 10.0% if the widths were right. Six markets are too narrow and two are
too wide, which is why a single global widening would be wrong.

THE CAUSE is structural rather than a bug. A projection's spread comes from the
player's own game-to-game standard deviation, but the residual being measured is
against a PROJECTED mean, and that mean carries its own error — shrinkage toward
a position baseline, an opponent adjustment fitted on a thin schedule graph, and
a role that may simply have changed. The honest predictive variance is

    var(player's game-to-game noise) + var(the projection's own error)

and only the first term was ever modelled. The second is roughly as large as the
first, which is what a 1.3-1.5x correction implies.

WHY IT IS LEARNED RATHER THAN HARDCODED. Fitting eight constants on the seasons
the backtest then scores would make the report flatter itself, and the report
already carries one such caveat for the anytime-touchdown clustering constants.
Instead the scale for week N is estimated only from weeks strictly before it, so
the correction is available at prediction time and the backtest stays honest.
The same accumulator runs live: a season in progress feeds it exactly as the
backtest does.

This module is deliberately free of database and sport (CLAUDE.md §3): it takes
numbers and returns numbers, so the NFL build reuses it unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

# Residuals needed before a market's own measurement is trusted at all. Below
# this the estimate is noisier than the error it corrects, so the projection is
# left alone rather than nudged by whatever forty games happened to do.
MIN_RESIDUALS = 400

# Residuals needed before a narrower cell overrides the level above it. Higher
# than MIN_RESIDUALS because a split only earns its keep when it is measured on
# enough games to beat the pooled estimate it is replacing.
MIN_CELL_RESIDUALS = 1200


def history_bucket(games_played: float) -> str:
    """How much of the player's own record the projection actually rests on.

    The correction needed is NOT constant across a season, and the first
    calibrated walk showed it plainly: week 3 implied x2.04 for rec_yards while
    weeks 6, 10 and 13 measured x1.48. That is not noise, it is the mechanism —
    the extra variance being corrected for is error in the PROJECTED MEAN, and
    early in the season that mean is mostly shrinkage toward a position baseline
    rather than the player's own production.

    Bucketing on games rather than on calendar week is deliberate. A player
    returning from injury in week 12 with two games on the board has the same
    thin evidence as anyone in week 3, and deserves the same widening.
    """
    if games_played <= 3:
        return "thin"
    if games_played <= 6:
        return "building"
    return "established"

# A single absurd game must not set the width for a whole market. Winsorizing at
# five SDs bounds one 300-yard outlier's leverage while leaving genuine tail
# weight — which is the thing being measured — fully intact.
MAX_STANDARDIZED_RESIDUAL = 5.0

# Bounds on the correction. A scale outside this range means something other
# than variance is wrong, and silently applying it would hide that.
MIN_SCALE = 0.6
MAX_SCALE = 2.5


def shrink_toward_one(raw: float, standard_error: float) -> float:
    """Damp a correction by how well it is actually measured.

    WHY THIS EXISTS. The first version applied every measurement at full
    strength. A market measured at x0.96 from a sample whose uncertainty
    comfortably spanned 1.0 still got moved 4%, which is noise applied as
    signal — and it showed: `receptions` (x0.96) and `rush_yards` both came out
    slightly WORSE after correction, while markets with large, well-measured
    deviations like `rec_yards` (x1.66) improved substantially.

    The estimator subtracts the noise variance from the observed deviation,
    which is the standard way to stop measurement error masquerading as effect:

        w = max(0, d^2 - se^2) / d^2,      adjusted = 1 + d * w

    where d is the raw deviation from 1.0. If the deviation is no larger than
    its own standard error, w is 0 and the correction is dropped entirely; if it
    dwarfs the error, w approaches 1 and the full correction survives. Nothing
    in between is arbitrary — it is the share of the observed deviation that the
    noise cannot account for.
    """
    deviation = raw - 1.0
    if deviation == 0.0 or not math.isfinite(deviation):
        return 1.0
    if not math.isfinite(standard_error) or standard_error <= 0:
        return raw
    signal = deviation * deviation - standard_error * standard_error
    if signal <= 0:
        return 1.0
    return 1.0 + deviation * (signal / (deviation * deviation))


def _scale_and_error(cell: list[float]) -> tuple[float, float]:
    """Width multiplier and its standard error, from [sum z^2, sum z^4, n].

    The multiplier is sqrt(mean(z^2)). Its error comes from the variance of that
    mean — var(z^2)/n, computed from the fourth moment — carried through the
    square root by the delta method. Using the observed fourth moment rather
    than the normal-theory value of 2 matters here: these residuals are heavy
    tailed, which is the very thing being corrected, and assuming normality
    would understate the error exactly where the sample is least trustworthy.
    """
    total_sq, total_quad, n = cell
    if n <= 1 or total_sq <= 0:
        return 1.0, 0.0
    mean_sq = total_sq / n
    variance_of_sq = max(total_quad / n - mean_sq * mean_sq, 0.0)
    scale = math.sqrt(mean_sq)
    # d(sqrt(x))/dx = 1/(2 sqrt(x))
    error = math.sqrt(variance_of_sq / n) / (2.0 * scale) if scale > 0 else 0.0
    return scale, error


class VarianceCalibration:
    """Accumulates standardized residuals and reports the width correction.

    Residuals are measured against the mean the projection was actually GRADED
    at — after any bias correction — but divided by the UNCORRECTED standard
    deviation. Both halves of that matter:

      * dividing by the raw SD means the scale is read off directly instead of
        compounding, so an over-correction in one week cannot feed the next;
      * centring on the corrected mean keeps bias out of the width, because
        E[(actual - mean)^2] is variance plus squared bias and a misplaced
        centre would otherwise be treated as a distribution that is too narrow.

    `Calibration` owns that ordering; this class only accumulates what it is
    handed.
    """

    def __init__(self) -> None:
        # key -> [sum z^2, sum z^4, count], at three levels of specificity.
        # The fourth moment is carried so the estimator's own standard error can
        # be computed from the data rather than by assuming the residuals are
        # normal — they are not, which is the entire reason this class exists.
        self._market: dict[str, list[float]] = {}
        self._by_history: dict[tuple[str, str], list[float]] = {}
        self._by_position: dict[tuple[str, str, str], list[float]] = {}

    # -- accumulate -----------------------------------------------------------
    def observe(
        self,
        market_key: str,
        position_group: str,
        actual: float,
        mean: float,
        sd: float,
        games_played: float = 0.0,
    ) -> None:
        """Record one graded outcome against the width it was projected with."""
        if sd <= 0 or not math.isfinite(sd) or not math.isfinite(actual):
            return
        z = (actual - mean) / sd
        if not math.isfinite(z):
            return
        z = max(-MAX_STANDARDIZED_RESIDUAL, min(MAX_STANDARDIZED_RESIDUAL, z))

        squared = z * z
        bucket = history_bucket(games_played)
        for store, key in (
            (self._market, market_key),
            (self._by_history, (market_key, bucket)),
            (self._by_position, (market_key, bucket, position_group)),
        ):
            cell = store.setdefault(key, [0.0, 0.0, 0.0])  # type: ignore[arg-type]
            cell[0] += squared
            cell[1] += squared * squared
            cell[2] += 1.0

    # -- report ---------------------------------------------------------------
    def scale(
        self, market_key: str, position_group: str, games_played: float = 0.0
    ) -> float:
        """Multiplier to apply to a projected SD, or 1.0 while still learning.

        Narrowest cell that has earned its sample wins, widening outwards:

          (market, history, position) -> (market, history) -> market -> 1.0

        Both splits are there because both were measured. HISTORY matters most:
        week 3 implied x2.04 for rec_yards where later weeks measured x1.48,
        because a thin-sample projection is mostly shrinkage and carries more
        error in its mean. POSITION matters too, most clearly for rushing yards,
        where a quarterback's are negative a quarter of the time (sacks are
        charged as rushing losses) and a running back's essentially never are.
        """
        bucket = history_bucket(games_played)
        for store, key, threshold in (
            (self._by_position, (market_key, bucket, position_group), MIN_CELL_RESIDUALS),
            (self._by_history, (market_key, bucket), MIN_CELL_RESIDUALS),
            (self._market, market_key, MIN_RESIDUALS),
        ):
            cell = store.get(key)  # type: ignore[arg-type]
            if cell is not None and cell[2] >= threshold:
                return _clamp(shrink_toward_one(*_scale_and_error(cell)))
        return 1.0

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Learned scales and their sample sizes, for the report and for reuse.

        Written into the backtest's config so a live weekly run can start from a
        measured correction instead of relearning one from an empty season.
        """
        result: dict[str, dict[str, float]] = {}

        def record(label: str, cell: list[float], applied: bool) -> None:
            if cell[2] <= 0:
                return
            raw, error = _scale_and_error(cell)
            result[label] = {
                "scale": round(_clamp(shrink_toward_one(raw, error)), 4),
                "raw": round(raw, 4),
                "n": int(cell[2]),
                "applied": applied,
            }

        for market_key, cell in sorted(self._market.items()):
            record(market_key, cell, cell[2] >= MIN_RESIDUALS)
        for (market_key, bucket), cell in sorted(self._by_history.items()):
            if cell[2] >= MIN_CELL_RESIDUALS:
                record(f"{market_key}@{bucket}", cell, True)
        for (market_key, bucket, position), cell in sorted(
            self._by_position.items()
        ):
            if cell[2] >= MIN_CELL_RESIDUALS:
                record(f"{market_key}@{bucket}:{position}", cell, True)
        return result


def _clamp(scale: float) -> float:
    if not math.isfinite(scale) or scale <= 0:
        return 1.0
    return min(max(scale, MIN_SCALE), MAX_SCALE)


# Bounds on the MEAN correction. Much tighter than the width bounds: a
# projection wrong by more than a fifth is a modelling failure to fix at source,
# not something to paper over with a multiplier.
MIN_MEAN_MULTIPLIER = 0.85
MAX_MEAN_MULTIPLIER = 1.20


class MeanCalibration:
    """Corrects systematic bias in the projected mean.

    WHY. The first full backtest showed `pass_attempts` running low across
    almost its entire reliability curve — it said 0.44 where 0.54 happened, and
    0.25 where 0.36 did. The residuals said the same thing directly: a
    quarterback's actual attempts exceed his own trailing average by +0.5 to
    +2.3, at every level of history. `rush_attempts` and `rec_yards` lean the
    same way.

    The cause is survivorship in the graded population. A player still drawing
    a projectable role in week 10 is one who kept it, and his season-to-date
    average is dragged down by the earlier weeks when he was splitting time.
    Shrinking toward a position median built from the same pool compounds it.

    Correcting this is not lookahead: "players' volume tends to exceed their
    trailing average by x%" is measurable from completed weeks and applied to
    the next one, exactly like the width correction it sits beside.

    A RATIO OF SUMS, not a mean of ratios. Per-row ratios are dominated by
    players with tiny projections, where actual/projected is enormous and means
    nothing. Summing both sides first weights each observation by its size,
    which is what a volume correction should do.
    """

    def __init__(self) -> None:
        # key -> [sum y, sum x, sum y^2, sum xy, sum x^2, n] where y is actual
        # and x projected. The cross-products are what let a RATIO estimator
        # report its own standard error, so a 4% correction measured on noise
        # can be told apart from a 4% correction that is real.
        self._market: dict[str, list[float]] = {}
        self._cells: dict[tuple[str, str], list[float]] = {}

    def observe(
        self,
        market_key: str,
        actual: float,
        projected: float,
        games_played: float = 0.0,
    ) -> None:
        if projected <= 0 or not math.isfinite(projected):
            return
        if not math.isfinite(actual) or actual < 0:
            return
        bucket = history_bucket(games_played)
        for store, key in (
            (self._market, market_key),
            (self._cells, (market_key, bucket)),
        ):
            cell = store.setdefault(  # type: ignore[arg-type]
                key, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            )
            cell[0] += actual
            cell[1] += projected
            cell[2] += actual * actual
            cell[3] += actual * projected
            cell[4] += projected * projected
            cell[5] += 1.0

    def multiplier(self, market_key: str, games_played: float = 0.0) -> float:
        bucket = history_bucket(games_played)
        for store, key, threshold in (
            (self._cells, (market_key, bucket), MIN_CELL_RESIDUALS),
            (self._market, market_key, MIN_RESIDUALS),
        ):
            cell = store.get(key)  # type: ignore[arg-type]
            if cell is not None and cell[5] >= threshold and cell[1] > 0:
                return _clamp_mean(shrink_toward_one(*_ratio_and_error(cell)))
        return 1.0

    def snapshot(self) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}

        def record(label: str, cell: list[float], applied: bool) -> None:
            if cell[1] <= 0:
                return
            raw, error = _ratio_and_error(cell)
            result[label] = {
                "multiplier": round(_clamp_mean(shrink_toward_one(raw, error)), 4),
                "raw": round(raw, 4),
                "n": int(cell[5]),
                "applied": applied,
            }

        for market_key, cell in sorted(self._market.items()):
            record(market_key, cell, cell[5] >= MIN_RESIDUALS)
        for (market_key, bucket), cell in sorted(self._cells.items()):
            if cell[5] >= MIN_CELL_RESIDUALS:
                record(f"{market_key}@{bucket}", cell, True)
        return result


def _ratio_and_error(cell: list[float]) -> tuple[float, float]:
    """Ratio-of-sums multiplier and its standard error.

    R = sum(actual) / sum(projected). The textbook ratio-estimator variance is

        var(R) ~ sum((y_i - R*x_i)^2) / (n (n-1) xbar^2)

    which is expanded here from the stored cross-products rather than a second
    pass over the observations. It correctly reflects that a market whose
    outcomes track their projections closely has a well-determined ratio even
    from a modest sample, while a noisy one does not.
    """
    sum_y, sum_x, sum_yy, sum_xy, sum_xx, n = cell
    if n <= 1 or sum_x <= 0:
        return 1.0, 0.0
    ratio = sum_y / sum_x
    residual_ss = sum_yy - 2.0 * ratio * sum_xy + ratio * ratio * sum_xx
    mean_x = sum_x / n
    if residual_ss <= 0 or mean_x <= 0:
        return ratio, 0.0
    variance = residual_ss / (n * (n - 1.0) * mean_x * mean_x)
    return ratio, math.sqrt(max(variance, 0.0))


def _clamp_mean(multiplier: float) -> float:
    if not math.isfinite(multiplier) or multiplier <= 0:
        return 1.0
    return min(max(multiplier, MIN_MEAN_MULTIPLIER), MAX_MEAN_MULTIPLIER)


@runtime_checkable
class MeanCorrection(Protocol):
    """Anything that can report a bias multiplier."""

    def multiplier(self, market_key: str, games_played: float = 0.0) -> float: ...


@runtime_checkable
class WidthCorrection(Protocol):
    """Anything that can report a width scale."""

    def scale(
        self, market_key: str, position_group: str, games_played: float = 0.0
    ) -> float: ...


@runtime_checkable
class Corrections(Protocol):
    """What a projection run needs in order to correct a distribution.

    Two things satisfy this and they are used in different places. `Calibration`
    LEARNS as it walks and is what the backtest passes, so week N is graded
    against weeks before it. `StoredCalibration` REPLAYS what a finished walk
    learned and is what the live weekly run passes, because a season that has
    not started yet has no residuals to learn from.

    Stating the shared shape as a protocol rather than a base class keeps both
    honest: they have nothing in common except the two questions asked of them.
    """

    mean: MeanCorrection
    variance: WidthCorrection


class Calibration:
    """Both corrections, learned together and applied in the right order.

    Bias is corrected before width because E[(actual - mean)^2] is variance plus
    squared bias: measuring the width against a misplaced centre would widen a
    distribution that only needed moving. So the mean multiplier is estimated
    against the RAW projection, and the width against the SHIFTED one — each
    read off directly, neither compounding across weeks.
    """

    def __init__(self) -> None:
        self.mean = MeanCalibration()
        self.variance = VarianceCalibration()

    def observe_many(
        self,
        observations: Iterable[tuple[str, str, float, float, float, float, float]],
    ) -> None:
        for (
            market_key,
            position,
            actual,
            shifted_mean,
            raw_mean,
            raw_sd,
            games,
        ) in observations:
            self.mean.observe(market_key, actual, raw_mean, games)
            self.variance.observe(
                market_key, position, actual, shifted_mean, raw_sd, games
            )

    def snapshot(self) -> dict[str, dict[str, dict[str, float]]]:
        return {"mean": self.mean.snapshot(), "width": self.variance.snapshot()}


# -----------------------------------------------------------------------------
# Replaying a finished measurement
# -----------------------------------------------------------------------------
class _StoredMean:
    """Bias multipliers read back from a snapshot."""

    def __init__(self, entries: dict[str, Any]) -> None:
        self._entries = entries

    def multiplier(self, market_key: str, games_played: float = 0.0) -> float:
        bucket = history_bucket(games_played)
        for key in (f"{market_key}@{bucket}", market_key):
            value = _applied(self._entries.get(key), "multiplier")
            if value is not None:
                return _clamp_mean(value)
        return 1.0


class _StoredWidth:
    """Width scales read back from a snapshot."""

    def __init__(self, entries: dict[str, Any]) -> None:
        self._entries = entries

    def scale(
        self, market_key: str, position_group: str, games_played: float = 0.0
    ) -> float:
        bucket = history_bucket(games_played)
        for key in (
            f"{market_key}@{bucket}:{position_group}",
            f"{market_key}@{bucket}",
            market_key,
        ):
            value = _applied(self._entries.get(key), "scale")
            if value is not None:
                return _clamp(value)
        return 1.0


def _applied(entry: Any, field: str) -> float | None:
    """The correction in `entry`, or None if it was never fit to be applied.

    `applied` is not decoration. The snapshot records every cell it measured,
    including ones held back — a market under `MIN_RESIDUALS`, or a Poisson or
    Bernoulli whose width is pinned to its mean and cannot be turned. Reading
    the number while ignoring the flag would apply corrections the backtest
    itself declined to apply, so the live board would not be showing the
    distributions the calibration report scored.
    """
    if not isinstance(entry, dict) or not entry.get("applied"):
        return None
    value = entry.get(field)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class StoredCalibration:
    """Corrections a completed backtest measured, applied to a live week.

    WHY A LIVE RUN CANNOT LEARN ITS OWN. `Calibration` estimates from graded
    outcomes, and grading needs results. Entering week 1 of a new season there
    are none, and the corrections are largest exactly there — the `thin` bucket
    measured x2.10 for rec_yards against x1.33 once a player is established.
    Starting from 1.0 would put the board's most uncertain projections on screen
    with the overconfidence Phase 3 exists to have removed.

    So the weekly run replays the snapshot the last walk wrote into
    `backtests.config`. That is not lookahead: every scale in it was estimated
    from weeks strictly before the one it was applied to, and a 2025 season is
    wholly in the past relative to a 2026 game.

    LOOKUP ORDER MIRRORS THE LEARNED ONE — narrowest cell that was actually
    applied, widening outwards. If the two disagreed, a live projection would be
    corrected differently from the identical backtest projection, and the
    calibration report would no longer describe the board.
    """

    def __init__(self, snapshot: dict[str, Any] | None) -> None:
        snapshot = snapshot or {}
        mean_entries = dict(snapshot.get("mean") or {})
        width_entries = dict(snapshot.get("width") or {})
        self.mean = _StoredMean(mean_entries)
        self.variance = _StoredWidth(width_entries)
        self.entry_count = len(mean_entries) + len(width_entries)

    @property
    def is_empty(self) -> bool:
        """True when nothing would be corrected.

        The caller is expected to treat this as a failure rather than a default:
        silently projecting uncorrected is the one outcome that looks like
        success and is not.
        """
        return self.entry_count == 0
