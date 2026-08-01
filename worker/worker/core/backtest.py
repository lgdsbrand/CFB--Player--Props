"""Walk-forward backtest and calibration measurement.

SPORT-AGNOSTIC CORE (CLAUDE.md §3). The Phase 3 deliverable, and the client
review gate before any UI work.

THE QUESTION THIS ANSWERS
-------------------------
"When the model says 60%, does it hit ~60%?" (CLAUDE.md §6). Not "is the
projection close" — a well-calibrated probability is worth more than an
impressive point estimate, because every downstream number on the board is a
probability.

WHAT IT DELIBERATELY DOES NOT ANSWER
------------------------------------
Whether the EDGE threshold makes money. Edge is `model probability − de-vigged
book probability`, and we have no historical book prices: the odds probe
established that historical props sit behind a paid plan whose 20k monthly
allowance is shared with the client's other models, making a full-season prop
backfill unaffordable rather than merely unavailable. Those are two different
claims and the report keeps them apart. Edge validation accrues from live 2026
weeks, which is what the `closing_line_when_available` hit-rate basis exists for.

WALK-FORWARD, NOT CROSS-VALIDATION
-----------------------------------
Week N of each season is predicted using only `AsOf(season, N)`, which restricts
every feature to weeks strictly before N. There is no shuffling and no holdout
split, because a random split would let a model see week 12 while predicting
week 5 — the "silent, disqualifying bug" of CLAUDE.md §4 wearing a respectable
name.

CHOOSING LINES WITHOUT A BOOK
------------------------------
Calibration needs a line to ask P(X > line) against, and no historical lines
exist. The line must therefore be (a) knowable before kickoff and (b) independent
of the model, or the exercise is circular.

So lines come from the player's own trailing production through week < N — the
`threshold` hit-rate basis in CLAUDE.md §9.2, and roughly what a book would post.
Each projection is additionally graded at a few offsets from that centre, in
units of its own projected standard deviation, because a single line near the
player's average pins every probability close to 0.5 and would leave the
confident bins — the ones that matter — empty.

That has a cost worth stating: several lines on one player-game are correlated
observations, so the effective sample is smaller than the row count. It inflates
apparent precision, not the calibration estimate itself, and the report says so.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from worker.core.calibration import Calibration
from worker.core.features import AsOf, build_feature_frame
from worker.core.models import (
    Projection,
    position_baselines,
    project,
    rescale,
    shift_mean,
)
from worker.core.probability import distribution_sd
from worker.db import fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

# One graded outcome as the calibration layer needs it:
# (market, position, actual, mean it was graded at, raw mean, raw SD, games).
# Both the raw and the shifted mean travel together because the two corrections
# are estimated against different centres — see `Calibration`.
Observation = tuple[str, str, float, float, float, float, float]

# Offsets from the trailing centre, in projected standard deviations. Chosen to
# spread model probabilities across roughly [0.15, 0.85] so the confident bins
# are populated; without them nearly every prediction sits near a coin flip and
# the reliability curve has nothing to say at the ends.
LINE_OFFSETS_SD = (-1.0, -0.5, 0.0, 0.5, 1.0)

# Books post half-points to avoid pushes. Matching that keeps the graded
# question identical to the one the product will actually ask.
LINE_QUANTUM = 0.5

# Weeks 1 and 2 have almost no within-season history, so a projection there is
# nearly all prior and baseline. They are included deliberately — early-season
# behaviour is exactly what CLAUDE.md §6 warns about and what the report must
# show — but never earlier than 3, where a cutoff of week < 3 still leaves two
# weeks of evidence.
MIN_BACKTEST_WEEK = 3

CALIBRATION_BINS = 10

# A projection must reach this fraction of its position's typical output to be
# graded. Books post props for contributors, not for the fourth-string receiver
# averaging a target every other game, and CLAUDE.md §7 scopes the board to
# projected starters and high-usage skill players.
#
# Expressed against the position baseline rather than as per-market constants so
# it scales with whatever the data says a typical player at that position does,
# instead of encoding a dozen magic numbers that quietly rot.
#
# It is also what keeps the report honest: padding the sample with players nobody
# could bet would improve every aggregate number while telling us nothing about
# the picks the product will actually show.
MIN_USAGE_FRACTION_OF_BASELINE = 0.5

# Below this many games a player has essentially no within-season record and the
# projection is almost entirely prior and baseline.
MIN_GAMES_TO_GRADE = 2


@dataclass
class Prediction:
    """One graded probability."""

    player_id: int
    game_id: int
    market_key: str
    position_group: str
    season: int
    week: int
    as_of_week: int
    line: float
    model_prob_over: float
    side: str
    confidence: float
    actual_value: float
    outcome_over: bool
    hit: bool


@dataclass
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float


@dataclass
class Metrics:
    n: int
    base_rate: float
    brier: float
    brier_skill: float
    log_loss: float
    ece: float
    sharpness: float
    bins: list[CalibrationBin] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"n={self.n:,} base={self.base_rate:.3f} brier={self.brier:.4f} "
            f"skill={self.brier_skill:+.3f} logloss={self.log_loss:.4f} "
            f"ECE={self.ece:.4f} sharpness={self.sharpness:.3f}"
        )


# -----------------------------------------------------------------------------
# Lines
# -----------------------------------------------------------------------------
def quantize_line(value: float) -> float:
    """Round to the nearest half-point, the way a book posts."""
    return round(value / LINE_QUANTUM) * LINE_QUANTUM


def candidate_lines(
    centre: float, sigma: float, *, binary: bool, default_line: float | None
) -> list[float]:
    """Lines to grade one projection at.

    A binary market has exactly one meaningful line — anytime TD is "over 0.5
    offensive touchdowns", so offsetting it would be asking a question no book
    asks and no user cares about.
    """
    if binary:
        return [default_line if default_line is not None else 0.5]

    spread = max(sigma, abs(centre) * 0.15, 0.5)
    lines: list[float] = []
    for offset in LINE_OFFSETS_SD:
        line = quantize_line(centre + offset * spread)
        # A negative line on a non-negative stat is not a question anyone asks,
        # and P(over) there is 1.0 by construction — a free "win" that would
        # flatter the calibration curve at the top end.
        if line <= 0:
            continue
        if line not in lines:
            lines.append(line)
    return lines


# -----------------------------------------------------------------------------
# Actuals
# -----------------------------------------------------------------------------
def load_actuals(season: int, week: int) -> dict[tuple[int, int], dict[str, Any]]:
    """Realised box scores for one week, keyed by (player, game).

    Read ONLY at grading time, never during projection. `player_game_stats` is
    the single home for actuals precisely so this boundary is auditable.
    """
    rows = fetch_all(
        """
        select player_id, game_id, pass_yards, pass_tds, pass_attempts,
               pass_completions, rush_yards, rush_attempts, receptions,
               rec_yards, offensive_tds
          from player_game_stats
         where season = %(season)s and week = %(week)s
        """,
        {"season": season, "week": week},
    )
    return {(int(r["player_id"]), int(r["game_id"])): r for r in rows}


def market_catalogue() -> list[dict[str, Any]]:
    """Markets with their position applicability and resolved family."""
    return fetch_all(
        """
        select mp.market_key,
               mp.position_group::text as position_group,
               m.stat_column,
               m.is_binary,
               m.default_line,
               resolve_distribution_family(mp.market_key, mp.position_group)::text
                 as distribution_family
          from market_positions mp
          join markets m on m.key = mp.market_key
         where m.is_active
        """
    )


# -----------------------------------------------------------------------------
# The walk
# -----------------------------------------------------------------------------
def backtest_week(
    as_of: AsOf,
    catalogue: Sequence[dict[str, Any]],
    *,
    prior_season_weight_max: float = 0.5,
    calibration: Calibration | None = None,
) -> tuple[list[Prediction], list[Observation]]:
    """Project every player-market for one week and grade against the result.

    Returns the graded predictions and the standardized-residual observations
    the caller should fold into `calibration` AFTERWARDS. The split is what keeps
    the correction point-in-time: this week is graded with a width learned from
    earlier weeks only, and only once it is graded does it inform the next.
    """
    frame = build_feature_frame(
        as_of, prior_season_weight_max=prior_season_weight_max
    )
    if frame.is_empty():
        return [], []

    rows = frame.to_dicts()
    baselines = position_baselines(rows)
    actuals = load_actuals(as_of.season, as_of.week)
    if not actuals:
        log.warning("%s: no actuals to grade against", as_of)
        return [], []

    by_position: dict[str, list[dict[str, Any]]] = {}
    for market in catalogue:
        by_position.setdefault(market["position_group"], []).append(market)

    predictions: list[Prediction] = []
    observations: list[Observation] = []
    for row in rows:
        position = str(row.get("position_group") or "")
        if (row.get("games_played") or 0) < MIN_GAMES_TO_GRADE:
            continue
        key = (int(row["player_id"]), int(row["game_id"]))
        actual_row = actuals.get(key)
        if actual_row is None:
            # The player did not appear. Not a miss — there is nothing to grade,
            # and scoring a did-not-play as a loss would punish the model for
            # something it was never asked to predict.
            continue

        for market in by_position.get(position, ()):
            actual = actual_row.get(market["stat_column"])
            if actual is None:
                continue

            projection = _project_safely(row, market, baselines)
            if projection is None:
                continue

            floor = baselines.get(position, {}).get(f"{market['stat_column']}_pg")
            if floor and projection.mean < MIN_USAGE_FRACTION_OF_BASELINE * floor:
                continue

            centre = _trailing_centre(row, market["stat_column"])
            if centre is None and not market["is_binary"]:
                continue

            # Lines are placed using the UNCORRECTED spread. They stand in for
            # what a book would post, which cannot depend on a correction we
            # applied to our own distribution — and holding them fixed is what
            # makes this run comparable to the one before it.
            lines = candidate_lines(
                centre if centre is not None else projection.mean,
                _projection_sigma(projection),
                binary=bool(market["is_binary"]),
                default_line=(
                    float(market["default_line"])
                    if market["default_line"] is not None
                    else None
                ),
            )

            games_played = float(row.get("games_played") or 0)
            raw_mean = projection.mean
            raw_sd = distribution_sd(projection.distribution, projection.params)

            # BIAS FIRST, THEN WIDTH. The order matters and is not arbitrary:
            # E[(actual - mean)^2] is variance plus squared bias, so measuring
            # the width against an uncorrected mean would fold the bias into the
            # width and widen a distribution that is merely misplaced.
            if calibration is not None:
                projection = shift_mean(
                    projection,
                    calibration.mean.multiplier(market["market_key"], games_played),
                )
                projection = rescale(
                    projection,
                    calibration.variance.scale(
                        market["market_key"], position, games_played
                    ),
                )

            # Both are recorded against the RAW projection, so each estimate is
            # read off directly rather than compounding week over week.
            observations.append((
                market["market_key"],
                position,
                float(actual),
                projection.mean,
                raw_mean,
                raw_sd,
                games_played,
            ))

            for line in lines:
                probability = projection.probability_over(line)
                probability = min(max(probability, 1e-6), 1 - 1e-6)
                outcome_over = float(actual) > line
                side = "over" if probability >= 0.5 else "under"
                predictions.append(
                    Prediction(
                        player_id=key[0],
                        game_id=key[1],
                        market_key=market["market_key"],
                        position_group=position,
                        season=as_of.season,
                        week=as_of.week,
                        as_of_week=as_of.week,
                        line=line,
                        model_prob_over=probability,
                        side=side,
                        confidence=max(probability, 1 - probability),
                        actual_value=float(actual),
                        outcome_over=outcome_over,
                        hit=(outcome_over if side == "over" else not outcome_over),
                    )
                )
    return predictions, observations


def _project_safely(
    row: dict[str, Any], market: dict[str, Any], baselines: dict[str, dict[str, float]]
) -> Projection | None:
    position = str(row.get("position_group") or "")
    try:
        return project(
            row,
            market["market_key"],
            market["distribution_family"],
            baselines.get(position, {}),
            baselines,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "projection failed for %s/%s: %s", market["market_key"], position, exc
        )
        return None


def _trailing_centre(row: dict[str, Any], stat_column: str) -> float | None:
    """The player's own trailing average, which is the line's anchor.

    Point-in-time by construction: it comes from the feature frame, whose every
    aggregate was already restricted to weeks before the cutoff.
    """
    value = row.get(f"{stat_column}_pg")
    if value is None:
        return None
    try:
        centre = float(value)
    except (TypeError, ValueError):
        return None
    return centre if math.isfinite(centre) else None


def _projection_sigma(projection: Projection) -> float:
    quantiles = projection.quantiles
    if "p10" in quantiles and "p90" in quantiles:
        # p90 - p10 spans about 2.56 sigma for a normal.
        return max((quantiles["p90"] - quantiles["p10"]) / 2.56, 0.5)
    return max(abs(projection.mean) * 0.35, 0.5)


def walk_forward(
    seasons: Iterable[int],
    *,
    max_week: int | None = None,
    prior_season_weight_max: float = 0.5,
    calibration: Calibration | None = None,
) -> list[Prediction]:
    """Run the whole backtest, one week at a time, in order.

    `calibration` accumulates as the walk proceeds, so each week is graded with
    a width learned only from the weeks before it. Pass a fresh instance to
    learn from scratch; pass None to disable the correction entirely, which is
    how the before/after comparison is made.
    """
    catalogue = market_catalogue()
    if not catalogue:
        raise RuntimeError("no active markets — did the seed migration run?")

    predictions: list[Prediction] = []
    for season in seasons:
        weeks = [
            int(r["week"])
            for r in fetch_all(
                """
                select distinct week from games
                 where season = %s and completed and week >= %s
                 order by week
                """,
                (season, MIN_BACKTEST_WEEK),
            )
        ]
        if max_week is not None:
            weeks = [w for w in weeks if w <= max_week]

        for week in weeks:
            as_of = AsOf(season=season, week=week)
            weekly, observations = backtest_week(
                as_of,
                catalogue,
                prior_season_weight_max=prior_season_weight_max,
                calibration=calibration,
            )
            predictions.extend(weekly)
            # Only AFTER this week is graded does it inform the next one.
            if calibration is not None:
                calibration.observe_many(observations)
            log.info("%s: %d graded predictions", as_of, len(weekly))

    return predictions


# -----------------------------------------------------------------------------
# Metrics
# -----------------------------------------------------------------------------
def compute_metrics(
    predictions: Sequence[Prediction], bins: int = CALIBRATION_BINS
) -> Metrics | None:
    """Calibration and accuracy on P(over) against the realised outcome.

    Measured on P(over) rather than on the confidence of the called side. Both
    describe the same model, but confidence is folded to [0.5, 1] and would hide
    a systematic lean in one direction — which is exactly the failure a
    reliability curve exists to expose.
    """
    if not predictions:
        return None

    probabilities = [p.model_prob_over for p in predictions]
    outcomes = [1.0 if p.outcome_over else 0.0 for p in predictions]
    n = len(predictions)

    base_rate = sum(outcomes) / n
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / n
    reference = sum((base_rate - y) ** 2 for y in outcomes) / n
    brier_skill = 1.0 - brier / reference if reference > 0 else 0.0

    log_loss = -sum(
        y * math.log(p) + (1 - y) * math.log(1 - p)
        for p, y in zip(probabilities, outcomes)
    ) / n

    buckets: list[list[int]] = [[] for _ in range(bins)]
    for index, probability in enumerate(probabilities):
        slot = min(int(probability * bins), bins - 1)
        buckets[slot].append(index)

    calibration: list[CalibrationBin] = []
    ece = 0.0
    for slot, members in enumerate(buckets):
        if not members:
            continue
        mean_predicted = sum(probabilities[i] for i in members) / len(members)
        observed = sum(outcomes[i] for i in members) / len(members)
        calibration.append(
            CalibrationBin(
                lower=slot / bins,
                upper=(slot + 1) / bins,
                count=len(members),
                mean_predicted=mean_predicted,
                observed_rate=observed,
            )
        )
        ece += (len(members) / n) * abs(mean_predicted - observed)

    sharpness = sum(abs(p - 0.5) for p in probabilities) / n

    return Metrics(
        n=n,
        base_rate=base_rate,
        brier=brier,
        brier_skill=brier_skill,
        log_loss=log_loss,
        ece=ece,
        sharpness=sharpness,
        bins=calibration,
    )


def group_metrics(
    predictions: Sequence[Prediction], key: str
) -> dict[str, Metrics]:
    """Metrics split by an attribute of the prediction."""
    grouped: dict[str, list[Prediction]] = {}
    for prediction in predictions:
        grouped.setdefault(str(getattr(prediction, key)), []).append(prediction)
    result: dict[str, Metrics] = {}
    for name, members in sorted(grouped.items()):
        metrics = compute_metrics(members)
        if metrics is not None:
            result[name] = metrics
    return result


def season_phase(week: int, split: int = 7) -> str:
    """Early vs late season.

    Reported separately because CLAUDE.md §6 says early-season college
    projections lean hardest on priors and are least trustworthy. Averaging the
    two together would hide precisely the weakness the client needs to see when
    deciding when to launch.
    """
    return "early (wk<=6)" if week < split else "late (wk>=7)"
