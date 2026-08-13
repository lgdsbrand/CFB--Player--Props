"""Alternate-line ladders: P(over) at a spread of lines, not just the book's.

The client's ask, verbatim: "RB line 60, model says 90." A single probability
against a single line does not answer what he is really asking, which is how far
the line can be pushed before the model stops agreeing. A ladder answers it
directly, and it is nearly free because `projections` stores family + params
rather than a point estimate.

Nothing here knows about college football, so the NFL build copies this module
unchanged (CLAUDE.md §3). Nothing here reads the database either, for the same
reason `core/probability.py` does not: the step is an argument, never a config
lookup, so a rung set is reproducible from its inputs alone.

WHAT THIS DELIBERATELY DOES NOT DO. It produces model probabilities and no book
comparison. A rung is not a priced market: books post one line, and quoting an
edge against a line nobody offered would invent a market. The edge belongs to the
pick, on the line the book actually posted.

READ THIS BEFORE THE OUTPUT IS PRESENTED AS ADVICE. A ladder asserts the book is
mispriced at several lines instead of one, which is a stronger claim than the
board makes, not a weaker one. The model is well calibrated but has never been
shown to beat a real closing line -- it rides a systematic over-shade and did not
beat blindly betting under across the two graded weeks. Rungs are the model's
read. They are not a green light, and the surface that shows them has to say so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from worker.core.probability import prob_over, validate_params

# Rungs land on half-integers (60.5, not 60), because that is how books post a
# line and because it removes the push case outright -- `prob_over` treats an
# integer line as a strict inequality, so a whole-number rung would quietly
# exclude the tie from both sides and the ladder would not sum the way a reader
# expects two adjacent rungs to.
#
# THIS HOLDS ONLY FOR A WHOLE-NUMBER STEP. The grid is `k * step + 0.5`, so a step
# of 2.5 walks 0.5, 3.0, 5.5, 8.0 and lands on a whole number every other rung.
# That is enforced upstream, by `markets_ladder_step_matches_binary` in migration
# 0039, rather than re-checked here: the step always arrives from that column, and
# a constraint makes the bad configuration impossible instead of merely detected.
_RUNG_OFFSET = 0.5

# Upper bound on rungs per projection. A display-density choice, not a modelling
# one: seven fits a card without scrolling. It is a cap and not a target -- a
# narrow distribution gets fewer rather than being padded with certainties.
MAX_RUNGS = 7

# Floor on rungs, where the distribution allows it. Two rungs read as an accident
# rather than a ladder, so a tight distribution is extended UPWARD -- the
# direction the client's question points, since "how high can I push it" is the
# thing he wants answered.
MIN_RUNGS = 3

# No book posts "over -5.5 rushing yards". Gamma and lognormal carry a free
# `loc` that legitimately goes negative -- 26% of QB rushing games are, because
# NCAA charges a sack as a rushing loss -- so the distribution may extend below
# zero even though the market cannot.
_LOWEST_RUNG = _RUNG_OFFSET


@dataclass(frozen=True)
class Rung:
    """One alternate line and the model's probability of clearing it."""

    line: float
    prob_over: float

    def as_dict(self) -> dict[str, float]:
        """The stored jsonb shape. Rounded, because the sixteenth decimal place
        of a probability is noise being persisted 90,000 times over."""
        return {"line": round(self.line, 2), "prob_over": round(self.prob_over, 4)}


def rung_lines(
    step: float,
    low: float,
    high: float,
    *,
    max_rungs: int = MAX_RUNGS,
    min_rungs: int = MIN_RUNGS,
) -> list[float]:
    """Ladder lines covering [low, high], on the market's own grid.

    The grid is `k * step + 0.5`, so every rung is a line a book could plausibly
    post and no rung sits on a whole number.

    WHEN THE WINDOW IS TOO WIDE the step is multiplied by the smallest integer
    factor that fits inside `max_rungs`, rather than the range being truncated.
    Coverage beats nominal spacing: a ladder that stops short of the projection
    fails at the one job it has, whereas a coarser rung is still a real line on
    the same grid. Passing yards need this routinely -- the measured p10-to-p90
    spread averages 262 yards against a 25-yard step.

    Raises on a non-positive step rather than looping forever, which is what a
    zero step would do.
    """
    if step <= 0:
        raise ValueError(f"step must be positive, got {step}")
    if high < low:
        low, high = high, low

    effective = step
    lines = _grid_between(effective, low, high)
    while len(lines) > max_rungs:
        # Multiply by the factor actually needed rather than stepping up one at a
        # time. `max(factor, 2)` guarantees the step at least doubles, so the rung
        # count at least halves and this cannot spin. Every widened step stays an
        # integer multiple of the original, so a coarse rung is still a point on
        # the market's own grid rather than an arbitrary number.
        factor = max(math.ceil(len(lines) / max_rungs), 2)
        effective *= factor
        lines = _grid_between(effective, low, high)

    if not lines:
        # The window fell between two grid points. Give the reader the nearest
        # rung rather than nothing, so a very tight distribution still renders.
        nearest = round((0.5 * (low + high) - _RUNG_OFFSET) / effective) * effective
        lines = [max(nearest + _RUNG_OFFSET, _LOWEST_RUNG)]

    # Extend upward to reach the floor. Upward and not outward: the low end of a
    # skill-position distribution is usually already at the market's floor, and
    # padding downward would add rungs at 99% that tell the reader nothing.
    while len(lines) < min_rungs:
        # Rounded like `_grid_between` does, so an extended rung is not the one
        # value in a ladder carrying float noise in its second decimal place.
        lines.append(round(lines[-1] + effective, 2))

    return lines[:max_rungs]


def _grid_between(step: float, low: float, high: float) -> list[float]:
    """Grid points `k*step + 0.5` inside [low, high], never below the floor."""
    first_k = math.ceil((max(low, _LOWEST_RUNG) - _RUNG_OFFSET) / step)
    last_k = math.floor((high - _RUNG_OFFSET) / step)
    first_k = max(first_k, 0)
    return [
        round(k * step + _RUNG_OFFSET, 2) for k in range(first_k, last_k + 1)
    ]


def build_ladder(
    distribution: str,
    params: dict[str, Any],
    step: float | None,
    *,
    low: float,
    high: float,
    max_rungs: int = MAX_RUNGS,
) -> list[Rung]:
    """The ladder for one projection, or empty when the market has no step.

    `low`/`high` are the window the rungs should cover -- p10 and p90 of this
    same distribution in normal use, so the ladder spans where the outcome
    plausibly lands instead of a fixed range that would be mostly 0% or 100% for
    any individual player.

    Returns [] rather than raising for a market with no `ladder_step`, because
    that is a legitimate state (anytime_td) and not an error.
    """
    if step is None:
        return []
    validate_params(distribution, params)
    return [
        Rung(line=line, prob_over=prob_over(distribution, params, line))
        for line in rung_lines(step, low, high, max_rungs=max_rungs)
    ]


def ladder_json(rungs: list[Rung]) -> list[dict[str, float]] | None:
    """Storage form: the array `projections.ladder` holds, or None if empty.

    None and not `[]`: the column means "no ladder for this market", and an empty
    array would read as "a ladder was computed and came out empty", which is a
    different and more alarming statement.
    """
    return [r.as_dict() for r in rungs] or None
