"""Phase 4a: project a week and write the rows the board reads.

    python -m worker.jobs.run_projections --season 2025 --weeks 10
    python -m worker.jobs.run_projections --season 2025 --all-weeks
    python -m worker.jobs.run_projections --season 2025 --weeks 10 --dry-run

Phase 3 produced a validated model and never persisted a single projection —
`backtest_predictions` holds graded probabilities, which is a different thing
from a board row. `projections` was empty, so `v_board_rows` returned nothing
and there was no dashboard to build. This job is what fills it.

WHAT IT WRITES
--------------
`projections` — one distribution per player-market, always.
`picks`       — the over/under call, only where a line exists to call against.

THE SPLIT IS THE LATE-LINE BEHAVIOUR (CLAUDE.md §7). College books post props
on Thursday or Friday for Saturday games, and the tool has to be useful before
that. `picks.line` is NOT NULL, so a pick needs a line, and a line comes from
one of two places: a book that has posted, or `markets.default_line` for a
market whose line is structural rather than priced — anytime TD is "over 0.5
touchdowns" whether or not anyone is taking bets on it.

So early in the week a yardage market shows a projection with `has_call =
false` and the board renders the model's lean from p10/p50/p90. When a book
posts, this job runs again and the call fills in on the same row. Nothing about
the projection changes; only the question being asked of it.

RE-RUNNING REPLACES THE WEEK. Each run writes under a fresh `model_run_id` and
`v_board_rows` does not filter by run, so leaving the old rows would double
every player on the board. The week's projections are therefore deleted and
rewritten in one transaction — picks cascade — which also makes a failed run
leave the previous week's board intact rather than half of a new one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from worker.adapters.odds import SYNTHETIC_ADAPTER
from worker.config import ConfigError, get_settings
from worker.core.calibration import StoredCalibration
from worker.core.features import AsOf
from worker.core.ladder import build_ladder, ladder_json
from worker.core.probability import side_and_confidence
from worker.core.projections import (
    LAST_OPENING_WEEK,
    MIN_GAMES_TO_PROJECT,
    MIN_PRIOR_GAMES_TO_PROJECT,
    MIN_USAGE_FRACTION_OF_BASELINE,
    ProjectedRow,
    market_catalogue,
    project_slate,
)
from worker.db import connect, fetch_all, fetch_one, get_config_value, pipeline_run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "run_projections"
MODEL_VERSION = "4a.1"

REPO_ROOT = Path(__file__).resolve().parents[3]

# Rows per INSERT statement. Well under the parameter ceiling at 19 columns.
BATCH = 500

# A probability of exactly 0 or 1 is a claim no model earns. The backtest
# clamps identically, so a stored pick and a graded prediction cannot disagree
# about what the model said.
PROBABILITY_EPSILON = 1e-6

# Two-way price written on a synthetic development line. -110/-110 de-vigs to
# exactly 0.500, which is the point: see `_write_synthetic_lines`.
SYNTHETIC_PRICE = -110
SYNTHETIC_BOOK_KEY = "dev"
# Imported rather than redeclared: `ingest_odds` evicts rows carrying this label
# when a real quote arrives for the same player and market, and that eviction
# silently stops working the moment the two definitions drift apart.


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:  # noqa: BLE001 - absent git is not a failure
        return None


# -----------------------------------------------------------------------------
# What to project
# -----------------------------------------------------------------------------
def resolve_season(explicit: int | None) -> int:
    if explicit:
        return explicit
    row = fetch_one("select max(season) as season from games")
    if row is None or row["season"] is None:
        raise ConfigError("No games ingested — nothing to project.")
    return int(row["season"])


def projectable_weeks(season: int) -> list[int]:
    """Every regular-season week with a schedule. THE SEASON STARTS AT WEEK 1.

    There is no week floor here any more, and its removal is the deliverable of
    Phase 6c rather than a tidy-up. The floor used to be arithmetic: a player
    needed `MIN_GAMES_TO_PROJECT` completed games, features may only read weeks
    strictly before the target, so nobody qualified before week 3 and asking for
    week 1 returned nothing. `is_projectable` replaced that with a rule the
    opening weeks can satisfy — a prior season in place of a current one — and
    the walk then graded those weeks rather than assuming them: weeks 1-2 came
    out the best-calibrated stratum of the season, ECE 0.0184 against 0.0191
    and 0.0215 for the rest (docs/phase-6b-opening-weekend.md).

    Eligibility is now entirely a per-player question and lives in
    `is_projectable`. A week with nobody eligible simply produces nothing, which
    is a fact about the roster rather than a rule about the calendar.
    """
    return [
        int(r["week"])
        for r in fetch_all(
            # REGULAR SEASON ONLY. Postseason weeks are stored offset past the
            # regular season (migration 0020), which makes them ordinary weeks
            # on the time axis and therefore projectable. Bowls are a different
            # regime — month-long layoffs and opt-outs — and the backtest
            # excludes them for the same reason, so the published population
            # stays the graded one. Whether to project them is a product
            # decision; dropping this predicate is all it takes.
            #
            # This predicate now carries weight it did not before: until Phase
            # 6c a mislabelled postseason game sat at week 1 and was excluded by
            # the floor as well. The floor is gone, so this is the only thing
            # keeping bowls off the board.
            "select distinct week from games "
            " where season = %s and season_type = 'regular' "
            " order by week",
            (season,),
        )
    ]


# -----------------------------------------------------------------------------
# Calibration
# -----------------------------------------------------------------------------
def load_calibration(backtest_id: uuid.UUID | None = None) -> StoredCalibration:
    """The corrections measured by the most recent completed walk.

    A live run cannot learn its own — see `StoredCalibration`. Missing
    corrections are an ERROR rather than a fallback to 1.0, because uncorrected
    projections look completely normal and are exactly the overconfident
    distributions the Phase 3 improvement round removed: the extreme bin said
    0.962 and hit 0.774. Publishing those to the board silently would undo the
    deliverable the client is reviewing.
    """
    row = fetch_one(
        """
        select id, created_at, config->'calibration' as calibration
          from backtests
         where (%s::uuid is null or id = %s::uuid)
           and config ? 'calibration'
         order by created_at desc
         limit 1
        """,
        (backtest_id, backtest_id),
    )
    if row is None:
        raise ConfigError(
            "No backtest has stored a calibration snapshot. Run "
            "`python -m worker.jobs.run_backtest` first, or pass "
            "--no-calibration to publish raw distributions deliberately."
        )

    calibration = StoredCalibration(row["calibration"])
    if calibration.is_empty:
        raise ConfigError(f"Backtest {row['id']} stored an empty calibration snapshot.")

    log.info(
        "Calibration from backtest %s (%s): %d entries",
        row["id"],
        row["created_at"].date(),
        calibration.entry_count,
    )
    return calibration


# -----------------------------------------------------------------------------
# Lines
# -----------------------------------------------------------------------------
def latest_lines(
    conn: psycopg.Connection, season: int, week: int
) -> dict[tuple[int, int, str], list[dict[str, Any]]]:
    """Current book lines for the week, keyed by (game, player, market).

    Reads `v_latest_prop_lines`, which already collapses the append-only history
    to the newest quote per book. One entry per book is kept rather than one per
    player-market: `picks` is unique on (projection, book), the board laterals to
    the highest-priority book, and the player card shows per-book odds
    (CLAUDE.md §7).

    ON THE CONNECTION ARGUMENT. This must read inside the caller's transaction,
    not open its own. `--synthetic-lines` writes quotes earlier in that same
    transaction, and a second connection cannot see uncommitted rows — the first
    run wrote 4,327 lines and derived a call against none of them, silently, and
    looked exactly like a run in a week where no book had posted.
    """
    lines: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            select line_id, game_id, player_id, market_key, sportsbook_id,
                   line, over_price, under_price
              from v_latest_prop_lines
             where season = %s and week = %s
            """,
            (season, week),
        )
        for row in cur.fetchall():
            key = (int(row["game_id"]), int(row["player_id"]), str(row["market_key"]))
            lines.setdefault(key, []).append(row)
    return lines


@dataclass(frozen=True)
class MarketMeta:
    """The two market facts the write path needs, resolved once per run."""

    default_line: float | None
    stat_column: str


def _market_meta(catalogue: list[dict[str, Any]]) -> dict[str, MarketMeta]:
    """One entry per market. `market_positions` lists a market once per
    position, so the same market arrives several times carrying identical
    values."""
    return {
        str(m["market_key"]): MarketMeta(
            default_line=(
                float(m["default_line"]) if m["default_line"] is not None else None
            ),
            stat_column=str(m["stat_column"]),
        )
        for m in catalogue
    }


def _write_synthetic_lines(
    conn: psycopg.Connection,
    season: int,
    week: int,
    projected: list[ProjectedRow],
    markets: dict[str, MarketMeta],
) -> int:
    """DEVELOPMENT ONLY. Post fake lines so the call path can be exercised.

    No real line exists anywhere: `player_prop_lines` is empty, The Odds API's
    NCAAF prop coverage cannot be established until books post in late August,
    and a historical prop backfill is unaffordable (see the odds probe). Without
    something here, `picks` stays empty for every market except anytime TD and
    the OVER/UNDER pill — the product's headline claim — cannot be built or
    reviewed at all.

    THE LINES ARE THE PLAYER'S OWN TRAILING AVERAGE, not the model's projection.
    Deriving them from our own output would make every edge a tautology. This is
    the same `threshold` construction the backtest used and the same one
    `app_config.hit_rate_basis` settles on, so it is at least the shape of
    question a book asks.

    THE EDGES ARE STILL MEANINGLESS, and deliberately visibly so. -110/-110
    de-vigs to exactly 0.500, so `edge = model probability - 0.5`, which is
    `confidence - 0.5` on the called side. That is a straight restatement of the
    model's own confidence, not a disagreement with a market. The book is named
    "DEV (synthetic)" on the card for the same reason: nothing about these rows
    should ever be mistaken for a priced market.
    """
    # Only markets that need a synthetic line: a market with a structural line
    # already has one, and a second contradictory "over 0.5" is not a test of
    # anything.
    priced = {
        key: meta
        for key, meta in markets.items()
        if meta.default_line is None
    }
    stat_columns = sorted({meta.stat_column for meta in priced.values()})
    if not stat_columns:
        return 0

    averages: dict[int, dict[str, float]] = {}
    positions: dict[int, str] = {}
    with conn.cursor() as cur:
        cur.execute(
            "insert into sportsbooks (key, display_name, priority) "
            "values (%s, %s, %s) on conflict (key) do nothing",
            (SYNTHETIC_BOOK_KEY, "DEV (synthetic)", 999),
        )
        cur.execute(
            "select id from sportsbooks where key = %s", (SYNTHETIC_BOOK_KEY,)
        )
        book = cur.fetchone()
        assert book is not None
        sportsbook_id = int(book["id"])

        # Trailing average through week < N — point-in-time by the same rule
        # every feature obeys.
        selects = ", ".join(f"avg({c}::numeric) as {c}" for c in stat_columns)
        cur.execute(
            f"select player_id, {selects}, "
            "       (array_agg(position_group order by week desc))[1]::text as position_group "
            "  from player_game_stats "
            " where season = %s and week < %s group by player_id",
            (season, week),
        )
        for row in cur.fetchall():
            player_id = int(row["player_id"])
            averages[player_id] = {
                c: float(row[c]) for c in stat_columns if row[c] is not None
            }
            positions[player_id] = str(row["position_group"] or "")

        # Synthetic rows are fake, so replacing them is right where replacing a
        # real quote would destroy the line history the closing-line hit-rate
        # basis depends on.
        cur.execute(
            "delete from player_prop_lines where season = %s and week = %s "
            "and source_adapter = %s",
            (season, week, SYNTHETIC_ADAPTER),
        )

    floors = _anchor_floors(averages, positions, stat_columns)

    rows: list[tuple[Any, ...]] = []
    for projected_row in projected:
        meta = priced.get(projected_row.market_key)
        if meta is None:
            continue
        centre = averages.get(projected_row.player_id, {}).get(meta.stat_column)
        if centre is None:
            continue

        # THE ANCHOR GETS THE SAME USAGE FLOOR AS THE PROJECTION. Without this
        # the dev board's top rows were all one artifact: a backup quarterback
        # whose trailing average is 4.5 passing yards is projected near 75 after
        # shrinkage toward the position baseline, so a 4.5 line reads as 99.5%
        # OVER and outranks every real pick on an edge-sorted board. No book
        # posts a passing prop on that player at all — a line implies a role,
        # and the model already has a rule for what a role looks like.
        floor = floors.get((positions.get(projected_row.player_id, ""), meta.stat_column))
        if floor is not None and centre < floor:
            continue

        line = round(centre * 2.0) / 2.0
        if line <= 0:
            continue
        rows.append(
            (
                projected_row.game_id,
                projected_row.player_id,
                projected_row.market_key,
                sportsbook_id,
                season,
                week,
                line,
                SYNTHETIC_PRICE,
                SYNTHETIC_PRICE,
                SYNTHETIC_ADAPTER,
            )
        )

    if not rows:
        return 0

    with conn.cursor() as cur:
        for start in range(0, len(rows), BATCH):
            chunk = rows[start : start + BATCH]
            values = ",".join(["(" + ",".join(["%s"] * 10) + ")"] * len(chunk))
            params: list[Any] = []
            for row in chunk:
                params.extend(row)
            cur.execute(
                "insert into player_prop_lines "
                "(game_id, player_id, market_key, sportsbook_id, season, week, "
                " line, over_price, under_price, source_adapter) values " + values,
                tuple(params),
            )
    return len(rows)


def _anchor_floors(
    averages: dict[int, dict[str, float]],
    positions: dict[int, str],
    stat_columns: list[str],
) -> dict[tuple[str, str], float]:
    """Minimum trailing average for a synthetic line to be plausible.

    `MIN_USAGE_FRACTION_OF_BASELINE` of the position's median, mirroring the
    rule `project_slate` applies to a projection. Median over players with a
    positive figure, for the same reason `position_baselines` uses one: the pool
    is full of players who never touch the ball in that market, and their zeros
    would drag a mean below anything a real contributor produces.
    """
    pools: dict[tuple[str, str], list[float]] = {}
    for player_id, stats in averages.items():
        position = positions.get(player_id, "")
        if not position:
            continue
        for column in stat_columns:
            value = stats.get(column)
            if value is not None and value > 0:
                pools.setdefault((position, column), []).append(value)

    floors: dict[tuple[str, str], float] = {}
    for key, values in pools.items():
        values.sort()
        middle = len(values) // 2
        median = (
            values[middle]
            if len(values) % 2
            else 0.5 * (values[middle - 1] + values[middle])
        )
        floors[key] = MIN_USAGE_FRACTION_OF_BASELINE * median
    return floors


# -----------------------------------------------------------------------------
# Writing
# -----------------------------------------------------------------------------
def write_week(
    conn: psycopg.Connection,
    model_run_id: uuid.UUID,
    season: int,
    week: int,
    projected: list[ProjectedRow],
    catalogue: list[dict[str, Any]],
    *,
    synthetic_lines: bool = False,
) -> dict[str, int]:
    """Replace one week's projections and picks. Caller owns the transaction."""
    markets = _market_meta(catalogue)

    with conn.cursor() as cur:
        # Picks cascade from projections, so this clears both.
        cur.execute(
            "delete from projections where season = %s and week = %s", (season, week)
        )

    synthetic = 0
    if synthetic_lines:
        synthetic = _write_synthetic_lines(conn, season, week, projected, markets)

    projection_ids = _insert_projections(conn, model_run_id, projected)
    picks = _insert_picks(conn, season, week, projected, projection_ids, markets)

    return {
        "projections": len(projection_ids),
        "picks": picks["total"],
        "picks_with_book_line": picks["with_book_line"],
        "synthetic_lines": synthetic,
    }


def _insert_projections(
    conn: psycopg.Connection,
    model_run_id: uuid.UUID,
    projected: list[ProjectedRow],
) -> dict[tuple[int, int, str], int]:
    """Insert distributions and return their ids keyed by natural key.

    The id comes back keyed rather than positionally. Postgres happens to return
    multi-row INSERT rows in order, but nothing guarantees it, and a silent
    off-by-one would attach every call to the wrong player.
    """
    ids: dict[tuple[int, int, str], int] = {}
    placeholder = (
        "(" + ",".join(["%s"] * 9) + ",%s::distribution_family,%s::jsonb,"
        + ",".join(["%s"] * 8) + ",%s::jsonb)"
    )

    with conn.cursor() as cur:
        for start in range(0, len(projected), BATCH):
            chunk = projected[start : start + BATCH]
            params: list[Any] = []
            for row in chunk:
                quantiles = row.projection.quantiles
                params.extend(
                    [
                        model_run_id,
                        row.player_id,
                        row.game_id,
                        row.team_id,
                        row.opponent_team_id,
                        row.market_key,
                        row.season,
                        row.week,
                        row.as_of_week,
                        row.projection.distribution,
                        json.dumps(row.projection.params),
                        row.projection.mean,
                        quantiles.get("p10"),
                        quantiles.get("p25"),
                        quantiles.get("p50"),
                        quantiles.get("p75"),
                        quantiles.get("p90"),
                        row.prior_weight,
                        row.effective_sample,
                        json.dumps(row.ladder) if row.ladder else None,
                    ]
                )
            cur.execute(
                "insert into projections "
                "(model_run_id, player_id, game_id, team_id, opponent_team_id, "
                " market_key, season, week, as_of_week, distribution, params, "
                " mean, p10, p25, p50, p75, p90, prior_weight, effective_sample, "
                " ladder) "
                "values " + ",".join([placeholder] * len(chunk))
                + " returning id, player_id, game_id, market_key",
                tuple(params),
            )
            for row in cur.fetchall():
                ids[
                    (int(row["player_id"]), int(row["game_id"]), str(row["market_key"]))
                ] = int(row["id"])
    return ids


def _insert_picks(
    conn: psycopg.Connection,
    season: int,
    week: int,
    projected: list[ProjectedRow],
    projection_ids: dict[tuple[int, int, str], int],
    markets: dict[str, MarketMeta],
) -> dict[str, int]:
    """Derive the over/under call wherever there is a line to call against."""
    lines = latest_lines(conn, season, week)

    rows: list[tuple[Any, ...]] = []
    with_book_line = 0
    for row in projected:
        key = (row.player_id, row.game_id, row.market_key)
        projection_id = projection_ids.get(key)
        if projection_id is None:
            continue

        book_lines = lines.get((row.game_id, row.player_id, row.market_key), [])
        if book_lines:
            for book in book_lines:
                rows.append(
                    _pick_values(
                        row,
                        projection_id,
                        float(book["line"]),
                        line_id=book["line_id"],
                        sportsbook_id=book["sportsbook_id"],
                        over_price=book["over_price"],
                        under_price=book["under_price"],
                    )
                )
                with_book_line += 1
            continue

        # No book. A market with a structural line is still callable; a yardage
        # market is not, and shows the projected range until a book posts.
        meta = markets.get(row.market_key)
        if meta is None or meta.default_line is None:
            continue
        default_line = meta.default_line
        rows.append(
            _pick_values(
                row,
                projection_id,
                float(default_line),
                line_id=None,
                sportsbook_id=None,
                over_price=None,
                under_price=None,
            )
        )

    if rows:
        # book_prob_over is de-vigged IN SQL so app_config.devig_method governs
        # it. Recomputing the de-vig in Python would be a second definition of
        # the number `picks.edge` is generated from, and the two would drift.
        #
        # The METHOD is stamped alongside the number for the same reason the
        # schema gives it a column: a de-vigged probability is not self-
        # describing. `app_config.devig_method` is meant to be changed, and a
        # pick that records 0.54 without saying it came from Shin would be
        # silently reinterpreted as multiplicative the next time someone
        # compared old edges to new ones. Read once per week, not per row, so
        # every pick in a run agrees.
        devig_method = str(get_config_value("devig_method") or "shin")

        placeholder = (
            "(" + ",".join(["%s"] * 11) + ",%s::bet_side,%s,"
            "devig_two_way(%s::integer, %s::integer),%s,%s,%s)"
        )
        with conn.cursor() as cur:
            for start in range(0, len(rows), BATCH):
                chunk = rows[start : start + BATCH]
                params: list[Any] = []
                for row_values in chunk:
                    params.extend(row_values)
                    # NULL where there is no two-sided price, matching the
                    # column's contract: a method name beside a NULL
                    # probability would describe a de-vig that never happened.
                    # Indices 13/14 are over_price / under_price.
                    two_sided = (
                        row_values[13] is not None and row_values[14] is not None
                    )
                    params.append(devig_method if two_sided else None)
                cur.execute(
                    "insert into picks "
                    "(projection_id, line_id, sportsbook_id, player_id, game_id, "
                    " team_id, opponent_team_id, market_key, season, week, line, "
                    " side, model_prob_over, book_prob_over, over_price, "
                    " under_price, devig_method) values "
                    + ",".join([placeholder] * len(chunk)),
                    tuple(params),
                )

    return {"total": len(rows), "with_book_line": with_book_line}


def _pick_values(
    row: ProjectedRow,
    projection_id: int,
    line: float,
    *,
    line_id: int | None,
    sportsbook_id: int | None,
    over_price: int | None,
    under_price: int | None,
) -> tuple[Any, ...]:
    probability = row.projection.probability_over(line)
    probability = min(max(probability, PROBABILITY_EPSILON), 1 - PROBABILITY_EPSILON)
    side, _confidence = side_and_confidence(probability)
    return (
        projection_id,
        line_id,
        sportsbook_id,
        row.player_id,
        row.game_id,
        row.team_id,
        row.opponent_team_id,
        row.market_key,
        row.season,
        row.week,
        line,
        side,
        probability,
        # de-vig arguments, consumed by devig_two_way in the statement
        over_price,
        under_price,
        over_price,
        under_price,
    )


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
LADDER_BACKFILL_BATCH = 2000


def _backfill_ladders(*, dry_run: bool = False) -> int:
    """Fill `projections.ladder` on rows written before migration 0039.

    WHY THIS EXISTS RATHER THAN A RE-RUN. Re-projecting would produce a new
    model_run and a fresh set of projection and pick ids, which changes what the
    board is reading while it is being read, and would rewrite picks whose lines
    and edges are already published. The ladder is derived entirely from the
    stored family and params, so it can be added to the rows that are already
    there — and deriving it from the stored distribution is not a shortcut, it is
    the definition: a rung computed from anything else could disagree with the
    p50 sitting beside it.

    Idempotent. Only rows with a null ladder are touched, so an interrupted run
    resumes by being run again.
    """
    steps = {
        row["key"]: row["ladder_step"]
        for row in fetch_all("select key, ladder_step from markets")
    }

    pending = fetch_one(
        """
        select count(*) as n from projections pr
          join markets m on m.key = pr.market_key
         where pr.ladder is null and m.ladder_step is not null
        """
    )
    total = int((pending or {}).get("n") or 0)
    log.info("Ladder backfill: %d row(s) to fill", total)
    if not total:
        return 0
    if dry_run:
        log.info("--dry-run: computing nothing, writing nothing.")
        return 0

    filled = 0
    # Ids, not a counter. An unfillable row keeps a null ladder, so it is selected
    # again on every pass, and a plain counter would report it once per pass.
    skipped: set[int] = set()
    while True:
        rows = fetch_all(
            """
            select pr.id, pr.market_key, pr.distribution::text as distribution,
                   pr.params, pr.mean, pr.p10, pr.p50, pr.p90
              from projections pr
              join markets m on m.key = pr.market_key
             where pr.ladder is null and m.ladder_step is not null
             limit %s
            """,
            (LADDER_BACKFILL_BATCH,),
        )
        if not rows:
            break

        updates: list[tuple[str, int]] = []
        for row in rows:
            centre = row["p50"] if row["p50"] is not None else row["mean"]
            low = row["p10"] if row["p10"] is not None else centre
            high = row["p90"] if row["p90"] is not None else centre
            if low is None or high is None:
                skipped.add(int(row["id"]))
                continue
            rungs = ladder_json(
                build_ladder(
                    row["distribution"],
                    {k: float(v) for k, v in row["params"].items()},
                    float(steps[row["market_key"]]),
                    low=float(low),
                    high=float(high),
                )
            )
            if rungs is None:
                skipped.add(int(row["id"]))
                continue
            updates.append((json.dumps(rungs), int(row["id"])))

        if not updates:
            # Every remaining row is unfillable. Stop rather than loop forever on
            # the same page — the query selects on `ladder is null`, so rows we
            # cannot fill would be returned again indefinitely.
            log.warning(
                "Ladder backfill stopping: %d row(s) left that cannot be "
                "filled (no quantiles and no mean).",
                len(rows),
            )
            break

        with connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "update projections set ladder = %s::jsonb where id = %s",
                    updates,
                )
            conn.commit()
        filled += len(updates)
        log.info("Ladder backfill: %d/%d", filled, total)

    log.info("Ladder backfill complete: %d filled, %d skipped", filled, len(skipped))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int)
    parser.add_argument("--weeks", type=int, nargs="+")
    parser.add_argument(
        "--all-weeks", action="store_true", help="Every projectable week of the season."
    )
    parser.add_argument(
        "--backtest-id",
        help="Take calibration from a specific backtest instead of the latest.",
    )
    parser.add_argument(
        "--no-calibration",
        action="store_true",
        help="Publish RAW distributions. These are the overconfident ones the "
             "Phase 3 improvement round corrected — for comparison, not for a "
             "board anyone reads.",
    )
    parser.add_argument(
        "--synthetic-lines",
        action="store_true",
        help="DEVELOPMENT ONLY. Post fake -110/-110 lines at each player's "
             "trailing average so the OVER/UNDER call path can be exercised "
             "before real books post. Edges from these are meaningless.",
    )
    parser.add_argument(
        "--backfill-ladders",
        action="store_true",
        help="Fill projections.ladder on rows that predate migration 0039 and "
             "exit. Touches ONLY that column — no new model_run, no new "
             "projection or pick ids, so the live board is undisturbed.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Project, write nothing.")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)

    if args.backfill_ladders:
        return _backfill_ladders(dry_run=args.dry_run)

    if args.synthetic_lines and settings.environment != "development":
        log.error(
            "--synthetic-lines refused outside development (environment=%r). "
            "Fake quotes must never reach a database anyone reads as real.",
            settings.environment,
        )
        return 2

    # SECOND GUARD, and it is not redundant with the one above.
    #
    # The environment check asks "is this a real deployment". This one asks a
    # different question: "is a real odds source configured". Development is
    # exactly where both can be true at once — the moment ingest_odds is
    # pointed at a live provider, synthetic rows stop being a harmless stand-in
    # and start competing with real quotes for the same player and market.
    #
    # Nothing downstream distinguishes them. `v_board_rows` picks a book by
    # priority, picks are keyed per book, and a synthetic -110/-110 de-vigs to
    # exactly 0.500 — so a fake row sitting beside a real one yields an edge
    # that is just the model's own confidence restated, presented identically
    # to a genuine disagreement with a market. That is silent and wrong, which
    # is the combination this project keeps getting caught by.
    if args.synthetic_lines:
        try:
            configured_adapter = get_config_value("odds_adapter")
        except Exception as exc:  # pragma: no cover - config must be readable
            log.error("Could not read app_config.odds_adapter: %s", exc)
            return 2
        if configured_adapter and str(configured_adapter) != "none":
            log.error(
                "--synthetic-lines refused: app_config.odds_adapter is %r, so "
                "real book lines are being ingested. Fake quotes would compete "
                "with real ones for the same player and market, and nothing "
                "downstream tells them apart. Set the adapter to 'none' if you "
                "genuinely want a synthetic board.",
                str(configured_adapter),
            )
            return 2

    try:
        season = resolve_season(args.season)
        if args.all_weeks:
            weeks = projectable_weeks(season)
        elif args.weeks:
            weeks = sorted(args.weeks)
        else:
            log.error("Give --weeks N [N ...] or --all-weeks.")
            return 2
        if not weeks:
            log.error("No projectable weeks in %s.", season)
            return 2

        calibration = None if args.no_calibration else load_calibration(
            uuid.UUID(args.backtest_id) if args.backtest_id else None
        )
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    if calibration is None:
        log.warning(
            "RUNNING UNCALIBRATED — every published probability will be the raw "
            "model's, which measured 0.962 stated against 0.774 observed in its "
            "top bin."
        )

    prior_ceiling = float(get_config_value("prior_season_weight_max") or 0.5)
    catalogue = market_catalogue()
    if not catalogue:
        log.error("No active markets — did the seed migration run?")
        return 1

    config = {
        "season": season,
        "weeks": weeks,
        "model_version": MODEL_VERSION,
        "git_sha": _git_sha(),
        "prior_season_weight_max": prior_ceiling,
        "devig_method": str(get_config_value("devig_method") or "shin"),
        "odds_adapter": str(get_config_value("odds_adapter") or "none"),
        "usage_filter": f"{MIN_USAGE_FRACTION_OF_BASELINE:.0%} of position baseline",
        # Both halves of the universe rule, because a stored run whose
        # population is only implied cannot be compared with the one before it.
        "min_games_to_project": MIN_GAMES_TO_PROJECT,
        "opening_weeks_rule": (
            f"weeks 1-{LAST_OPENING_WEEK}: {MIN_PRIOR_GAMES_TO_PROJECT}+ "
            "prior-season games"
        ),
        "calibrated": calibration is not None,
        "synthetic_lines": bool(args.synthetic_lines),
    }

    model_run_id = uuid.uuid4()
    totals = {"projections": 0, "picks": 0, "picks_with_book_line": 0, "synthetic_lines": 0}

    try:
        with pipeline_run(JOB_NAME, metadata={"season": season, "weeks": weeks}):
            if not args.dry_run:
                _record_model_run(model_run_id, config)

            for week in weeks:
                projected = project_slate(
                    AsOf(season=season, week=week),
                    catalogue,
                    prior_season_weight_max=prior_ceiling,
                    calibration=calibration,
                )
                if not projected:
                    log.warning("%s w%d: nothing projected", season, week)
                    continue

                if args.dry_run:
                    log.info(
                        "%s w%d: %d projections (dry run, nothing written)",
                        season, week, len(projected),
                    )
                    totals["projections"] += len(projected)
                    continue

                # One transaction per week: a failure leaves the previous
                # board standing rather than half of a new one.
                with connect() as conn:
                    counts = write_week(
                        conn,
                        model_run_id,
                        season,
                        week,
                        projected,
                        catalogue,
                        synthetic_lines=args.synthetic_lines,
                    )
                    conn.commit()

                for key, value in counts.items():
                    totals[key] += value
                log.info(
                    "%s w%d: %d projections, %d picks (%d against a book line)",
                    season, week,
                    counts["projections"],
                    counts["picks"],
                    counts["picks_with_book_line"],
                )

            if not args.dry_run:
                _finish_model_run(model_run_id, "succeeded")
    except Exception as exc:
        log.error("Projection run failed: %s", exc, exc_info=True)
        if not args.dry_run:
            _finish_model_run(model_run_id, "failed")
        return 1

    log.info(
        "Complete. %d projections, %d picks (%d against a book line)%s",
        totals["projections"],
        totals["picks"],
        totals["picks_with_book_line"],
        f", {totals['synthetic_lines']} synthetic lines" if args.synthetic_lines else "",
    )
    return 0


def _record_model_run(model_run_id: uuid.UUID, config: dict[str, Any]) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into model_runs
                  (id, run_type, model_version, git_sha, config, status)
                values (%s, 'weekly', %s, %s, %s::jsonb, 'running')
                """,
                (
                    model_run_id,
                    config["model_version"],
                    config["git_sha"],
                    json.dumps(config, default=str),
                ),
            )
        conn.commit()


def _finish_model_run(model_run_id: uuid.UUID, status: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update model_runs set status = %s, finished_at = now() where id = %s",
                (status, model_run_id),
            )
        conn.commit()


if __name__ == "__main__":
    sys.exit(main())
