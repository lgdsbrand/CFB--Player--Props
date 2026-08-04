"""The weekly projection run — what the board actually reads.

SPORT-AGNOSTIC CORE (CLAUDE.md §3).

WHY THIS IS NOT THE BACKTEST
----------------------------
`backtest.py` projects a week and then GRADES it. This projects a week and
stops. They share the model, the features and the calibration, and they differ
in one way that matters more than the shared parts: the backtest knows who
played.

`backtest_week` skips any player absent from `player_game_stats`, because there
is nothing to grade — a did-not-play is not a miss. Carrying that rule into a
live run would be lookahead of the plainest kind: on Tuesday nobody knows who
will dress on Saturday, and a board built from the players who turned out to
appear is a board built from the future.

So the population here is defined only by what was knowable before kickoff:
the player has a role (`is_projectable`), and the role is large enough that a
book would price it (`MIN_USAGE_FRACTION_OF_BASELINE`). Some of those players
will not play. Their projections will sit on the board unresolved, and that is
correct — it is what a projection is.

WHY THE UNIVERSE RULES LIVE HERE AND THE BACKTEST IMPORTS THEM
--------------------------------------------------------------
They describe the projectable population, which is a modelling decision rather
than an evaluation one. Defining them twice would let the board drift away from
the population the calibration report scored, and that drift would be invisible:
every number would still look reasonable while describing a different set of
players from the one the +0.186 skill was measured on.

LINES ARE NOT THIS MODULE'S BUSINESS
------------------------------------
Nothing here decides what line to project against. `projections` stores a
distribution, and P(over) for any line is recoverable from it later — which is
exactly what the late-line behaviour in CLAUDE.md §7 needs, since college books
post props on Thursday or Friday for Saturday games. Attaching lines and
deriving picks belongs to the job, against the database, once books have posted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from worker.core.calibration import Corrections
from worker.core.features import AsOf, build_feature_frame
from worker.core.models import (
    Projection,
    position_baselines,
    project,
    rescale,
    shift_mean,
)
from worker.db import fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

# A projection must reach this fraction of its position's typical output to be
# published. Books post props for contributors, not for the fourth-string
# receiver averaging a target every other game, and CLAUDE.md §7 scopes the
# board to projected starters and high-usage skill players.
#
# Expressed against the position baseline rather than as per-market constants so
# it scales with whatever the data says a typical player at that position does,
# instead of encoding a dozen magic numbers that quietly rot.
MIN_USAGE_FRACTION_OF_BASELINE = 0.5

# Below this many games a player has essentially no within-season record and the
# projection is almost entirely prior and baseline. The backtest graded on the
# same threshold, so a player below it is one the calibration report says
# nothing about — publishing them would put unvalidated probabilities on the
# screen next to validated ones, indistinguishable.
MIN_GAMES_TO_PROJECT = 2

# Weeks 1 and 2, where no player can satisfy `MIN_GAMES_TO_PROJECT` because the
# season has barely started. They get their own rule below rather than a relaxed
# version of this one.
LAST_OPENING_WEEK = 2

# Prior-season games required of a player with no current-season record. NOT a
# round number picked for tidiness — Phase 6a measured what each candidate rule
# does to the one thing `MIN_GAMES_TO_PROJECT` is really buying in weeks 1-2,
# which is guessing who will dress (`docs/phase-6a-early-season-ceiling.md` §4):
#
#   prior usage + on a roster          1,816-1,937 candidates, 60-63% played
#   + at least this many prior games   1,161-1,280 candidates, 70-73% played
#   the shipped week-3 rule            1,108-1,182 candidates, 71.4% played
#
# So an opening-weekend board built this way is as good at picking who plays as
# the week-3 board already shipped, on a pool of comparable size. Without the
# prior-games filter it is ten points worse and a third larger.
MIN_PRIOR_GAMES_TO_PROJECT = 4


def is_projectable(row: dict[str, Any], week: int) -> bool:
    """Whether this player-week has enough record behind it to be projected.

    WHY THE OPENING WEEKS GET A SEPARATE RULE RATHER THAN A LOWER THRESHOLD.
    Lowering `MIN_GAMES_TO_PROJECT` to admit weeks 1-2 would also admit, in
    every later week, players whose only qualification is one game — changing
    the population the calibration report was measured on as a side effect of a
    change that was supposed to be about opening weekend. Keyed on the week
    instead, weeks 3+ admit exactly the population they admitted before.

    The two rules ask the same question of different evidence. From week 3 the
    player's own current season answers it. In weeks 1-2 there is no current
    season, so a full prior one has to — and `roster_universe` has already
    established that the player is on a roster now, which is what makes last
    year's production a claim about this week rather than a fact about last year.
    """
    if float(row.get("games_played") or 0) >= MIN_GAMES_TO_PROJECT:
        return True
    if week > LAST_OPENING_WEEK:
        return False
    return float(row.get("prior_games_played") or 0) >= MIN_PRIOR_GAMES_TO_PROJECT


def market_catalogue() -> list[dict[str, Any]]:
    """Markets with their position applicability and resolved family.

    Part of the universe definition rather than a job detail: `market_positions`
    is what decides that a tight end gets receptions and receiving yards and
    nothing else, and it drives the position tabs and the stat selector on the
    board from the same rows (CLAUDE.md §7). One source, so the UI cannot offer
    a market the model does not produce.
    """
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


@dataclass
class ProjectedRow:
    """One distribution, with everything `projections` needs to store it.

    Deliberately not a database row. This module reads, and the job writes —
    which is what lets the same projection be graded by the backtest, persisted
    by the weekly run, or thrown away by a dry run, without three code paths.
    """

    player_id: int
    game_id: int
    team_id: int
    opponent_team_id: int
    market_key: str
    position_group: str
    season: int
    week: int
    as_of_week: int
    projection: Projection
    prior_weight: float | None = None
    effective_sample: float | None = None


def project_slate(
    as_of: AsOf,
    catalogue: Sequence[dict[str, Any]],
    *,
    prior_season_weight_max: float = 0.5,
    calibration: Corrections | None = None,
) -> list[ProjectedRow]:
    """Every publishable player-market distribution for one week.

    `calibration` is the correction learned by a completed backtest. Passing
    None projects raw, which is the shape the first Phase 3 walk showed to be
    overconfident at the extremes — useful for comparison, wrong for the board.
    """
    frame = build_feature_frame(
        as_of, prior_season_weight_max=prior_season_weight_max
    )
    if frame.is_empty():
        log.warning("%s: empty feature frame — nothing to project", as_of)
        return []

    rows = frame.to_dicts()
    baselines = position_baselines(rows)

    by_position: dict[str, list[dict[str, Any]]] = {}
    for market in catalogue:
        by_position.setdefault(market["position_group"], []).append(market)

    projected: list[ProjectedRow] = []
    skipped_thin = 0
    skipped_usage = 0
    for row in rows:
        position = str(row.get("position_group") or "")
        games_played = float(row.get("games_played") or 0)
        if not is_projectable(row, as_of.week):
            skipped_thin += 1
            continue

        for market in by_position.get(position, ()):
            projection = project_row(row, market, baselines)
            if projection is None:
                continue

            floor = baselines.get(position, {}).get(f"{market['stat_column']}_pg")
            if floor and projection.mean < MIN_USAGE_FRACTION_OF_BASELINE * floor:
                skipped_usage += 1
                continue

            # BIAS FIRST, THEN WIDTH — the same order the backtest applied, and
            # not arbitrary: E[(actual - mean)^2] is variance plus squared bias,
            # so widening around a misplaced centre inflates a distribution that
            # only needed moving. Reversing it here would publish distributions
            # subtly different from the ones the report scored.
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

            projected.append(
                ProjectedRow(
                    player_id=int(row["player_id"]),
                    game_id=int(row["game_id"]),
                    team_id=int(row["team_id"]),
                    opponent_team_id=int(row["opponent_team_id"]),
                    market_key=str(market["market_key"]),
                    position_group=position,
                    season=as_of.season,
                    week=as_of.week,
                    as_of_week=as_of.week,
                    projection=projection,
                    prior_weight=_optional_float(row.get("prior_weight")),
                    effective_sample=_optional_float(row.get("effective_sample")),
                )
            )

    rule = (
        f"{MIN_PRIOR_GAMES_TO_PROJECT} prior-season games"
        if as_of.week <= LAST_OPENING_WEEK
        else f"{MIN_GAMES_TO_PROJECT} games"
    )
    log.info(
        "%s: %d projections from %d player-games (%d players below "
        "%s, %d markets below the usage floor)",
        as_of,
        len(projected),
        len(rows),
        skipped_thin,
        rule,
        skipped_usage,
    )
    return projected


def project_row(
    row: dict[str, Any],
    market: dict[str, Any],
    baselines: dict[str, dict[str, float]],
) -> Projection | None:
    """Project one market, or None if this player cannot support it.

    `project` returns None for a market the player has no volume in — a wide
    receiver has no pass attempts — and raises for genuinely malformed input.
    Both mean "no row on the board", and neither should stop the other 3,000
    projections in the week.
    """
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


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
