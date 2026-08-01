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
        # key -> [sum of z squared, count], at three levels of specificity
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
            cell = store.setdefault(key, [0.0, 0.0])  # type: ignore[arg-type]
            cell[0] += squared
            cell[1] += 1.0

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
            if cell is not None and cell[1] >= threshold:
                return _clamp(math.sqrt(cell[0] / cell[1]))
        return 1.0

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Learned scales and their sample sizes, for the report and for reuse.

        Written into the backtest's config so a live weekly run can start from a
        measured correction instead of relearning one from an empty season.
        """
        result: dict[str, dict[str, float]] = {}

        def record(label: str, total: float, count: float, applied: bool) -> None:
            if count <= 0:
                return
            result[label] = {
                "scale": round(_clamp(math.sqrt(total / count)), 4),
                "n": int(count),
                "applied": applied,
            }

        for market_key, (total, count) in sorted(self._market.items()):
            record(market_key, total, count, count >= MIN_RESIDUALS)
        for (market_key, bucket), (total, count) in sorted(self._by_history.items()):
            if count >= MIN_CELL_RESIDUALS:
                record(f"{market_key}@{bucket}", total, count, True)
        for (market_key, bucket, position), (total, count) in sorted(
            self._by_position.items()
        ):
            if count >= MIN_CELL_RESIDUALS:
                record(f"{market_key}@{bucket}:{position}", total, count, True)
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
        # key -> [sum actual, sum projected, count]
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
            cell = store.setdefault(key, [0.0, 0.0, 0.0])  # type: ignore[arg-type]
            cell[0] += actual
            cell[1] += projected
            cell[2] += 1.0

    def multiplier(self, market_key: str, games_played: float = 0.0) -> float:
        bucket = history_bucket(games_played)
        for store, key, threshold in (
            (self._cells, (market_key, bucket), MIN_CELL_RESIDUALS),
            (self._market, market_key, MIN_RESIDUALS),
        ):
            cell = store.get(key)  # type: ignore[arg-type]
            if cell is not None and cell[2] >= threshold and cell[1] > 0:
                return _clamp_mean(cell[0] / cell[1])
        return 1.0

    def snapshot(self) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for market_key, (actual, projected, count) in sorted(self._market.items()):
            if projected <= 0:
                continue
            result[market_key] = {
                "multiplier": round(_clamp_mean(actual / projected), 4),
                "n": int(count),
                "applied": count >= MIN_RESIDUALS,
            }
        for (market_key, bucket), (actual, projected, count) in sorted(
            self._cells.items()
        ):
            if count < MIN_CELL_RESIDUALS or projected <= 0:
                continue
            result[f"{market_key}@{bucket}"] = {
                "multiplier": round(_clamp_mean(actual / projected), 4),
                "n": int(count),
                "applied": True,
            }
        return result


def _clamp_mean(multiplier: float) -> float:
    if not math.isfinite(multiplier) or multiplier <= 0:
        return 1.0
    return min(max(multiplier, MIN_MEAN_MULTIPLIER), MAX_MEAN_MULTIPLIER)


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
