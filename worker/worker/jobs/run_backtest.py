"""Phase 3f: run the walk-forward backtest and write the calibration report.

    python -m worker.jobs.run_backtest
    python -m worker.jobs.run_backtest --seasons 2024 --max-week 8
    python -m worker.jobs.run_backtest --persist-predictions

This is the Phase 3 deliverable and the client review gate (CLAUDE.md §8). It
produces docs/calibration-report.html.

ON PERSISTENCE. `calibration_bins` and the `backtests` header are always stored:
they are small and they are what a later run needs to compare against. Individual
`backtest_predictions` rows are NOT stored by default — a full walk generates
hundreds of thousands of them, and the development database sits on Supabase's
500 MB free tier alongside 328k plays and 345k attribution rows. The report is
rendered from the in-memory run either way, so persisting them buys row-level
forensics rather than the deliverable itself. Ask for it with
--persist-predictions when that is what you want.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from worker.core import report as report_module
from worker.core.backtest import (
    MIN_USAGE_FRACTION_OF_BASELINE,
    Metrics,
    Prediction,
    compute_metrics,
    group_metrics,
    season_phase,
    walk_forward,
)
from worker.config import ConfigError, get_settings
from worker.db import execute, fetch_all, get_config_value, pipeline_run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "run_backtest"
MODEL_VERSION = "3f.1"

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO_ROOT / "docs" / "calibration-report.html"

# Rows written per statement when persisting predictions.
BATCH = 2000


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


def resolve_seasons(explicit: list[int] | None) -> list[int]:
    """Seasons that can actually be backtested: those with play-by-play.

    A prior-season backfill carries box scores only, so it has no defensive
    ratings, no Elo and no weather. It exists to supply prior-year features to
    the season after it, not to be predicted.
    """
    if explicit:
        return sorted(explicit)
    rows = fetch_all("select distinct season from plays order by season")
    return [int(r["season"]) for r in rows]


def _caveats(predictions: list[Prediction], seasons: list[int]) -> list[str]:
    """Everything a reader needs in order not to over-read the numbers."""
    per_game = {}
    for prediction in predictions:
        per_game.setdefault(
            (prediction.player_id, prediction.game_id, prediction.market_key), 0
        )
        per_game[
            (prediction.player_id, prediction.game_id, prediction.market_key)
        ] += 1
    lines_per_projection = (
        sum(per_game.values()) / len(per_game) if per_game else 0.0
    )

    return [
        "<strong>Correlated observations.</strong> Each projection is graded at "
        f"about {lines_per_projection:.1f} lines, and those share one outcome. "
        "The effective sample is therefore smaller than the row count: treat "
        "the point estimates as sound and any implied precision as optimistic.",

        "<strong>Lines are synthetic.</strong> No historical book lines exist, "
        "so each line is the player's own trailing average through the previous "
        "week, offset in units of the projected standard deviation and rounded "
        "to a half-point. That is knowable before kickoff and independent of the "
        "model, which is what calibration requires — but it is not what a book "
        "would have posted, and a real line carries information a trailing "
        "average does not.",

        "<strong>Anytime-TD clustering constants were fitted in-sample.</strong> "
        "The per-position parameters mapping expected touchdowns to P(scored) "
        "were measured on these same seasons. They are four scalars describing a "
        "structural fact rather than a per-player fit, so the leakage is small — "
        "but it is not zero, and anytime-TD calibration here will look slightly "
        "better than it would out of sample.",

        f"<strong>Two seasons ({', '.join(str(s) for s in seasons)}).</strong> "
        "Cells split by market and position get thin at the extremes; bins under "
        "30 predictions are excluded from the worst-bin column for that reason. "
        "A third full season is affordable if any cell needs it.",

        "<strong>Observed weather, not forecast.</strong> Entering a week you "
        "would have had a forecast; the model reads what actually happened. The "
        "bias runs in the flattering direction. Temperature and wind forecast "
        "well a day or two out so the effect should be small, and "
        "<code>include_weather=False</code> exists to measure rather than assume "
        "it.",

        "<strong>Usage filter.</strong> Only players reaching "
        f"{MIN_USAGE_FRACTION_OF_BASELINE:.0%} of their position's typical output "
        "are graded, matching the population a book would post lines for. "
        "Including everyone would improve every aggregate while saying nothing "
        "about the picks the product actually shows.",
    ]


def _persist(
    backtest_id: uuid.UUID,
    predictions: list[Prediction],
    devig_method: str,
) -> int:
    written = 0
    for start in range(0, len(predictions), BATCH):
        chunk = predictions[start : start + BATCH]
        values = []
        params: list[Any] = []
        for p in chunk:
            values.append("(" + ",".join(["%s"] * 16) + ")")
            params.extend(
                [
                    backtest_id, p.player_id, p.game_id, p.market_key,
                    p.position_group, p.season, p.week, p.as_of_week,
                    p.line, p.side, p.model_prob_over, p.confidence,
                    p.actual_value, p.outcome_over, p.hit, devig_method,
                ]
            )
        execute(
            "insert into backtest_predictions "
            "(backtest_id, player_id, game_id, market_key, position_group, "
            " season, week, as_of_week, line, side, model_prob_over, "
            " confidence, actual_value, outcome_over, hit, devig_method) "
            "values " + ",".join(values),
            tuple(params),
        )
        written += len(chunk)
    return written


def _store_bins(backtest_id: uuid.UUID, label: str, metrics: Metrics) -> None:
    market_key = label if label.startswith("market:") else None
    for bucket in metrics.bins:
        execute(
            """
            insert into calibration_bins
              (backtest_id, market_key, position_group, bin_lower, bin_upper,
               n, mean_predicted_probability, observed_rate)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                backtest_id,
                market_key.removeprefix("market:") if market_key else None,
                None,
                bucket.lower,
                bucket.upper,
                bucket.count,
                bucket.mean_predicted,
                bucket.observed_rate,
            ),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument("--max-week", type=int)
    parser.add_argument(
        "--persist-predictions", action="store_true",
        help="Also write every graded prediction to backtest_predictions. "
             "Hundreds of thousands of rows on a 500 MB tier — off by default.",
    )
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)

    seasons = resolve_seasons(args.seasons)
    if not seasons:
        log.error("No backtestable seasons — ingest play-by-play first.")
        return 2

    devig_method = str(get_config_value("devig_method") or "shin")
    hit_rate_basis = str(get_config_value("hit_rate_basis") or "threshold")
    prior_ceiling = float(get_config_value("prior_season_weight_max") or 0.5)

    config = {
        "seasons": seasons,
        "max_week": args.max_week,
        "devig_method": devig_method,
        "hit_rate_basis": hit_rate_basis,
        "prior_season_weight_max": prior_ceiling,
        "usage_filter": f"{MIN_USAGE_FRACTION_OF_BASELINE:.0%} of position baseline",
        "model_version": MODEL_VERSION,
        "git_sha": _git_sha(),
    }

    model_run_id = uuid.uuid4()
    backtest_id = uuid.uuid4()

    try:
        with pipeline_run(JOB_NAME, metadata={"seasons": seasons}):
            execute(
                """
                insert into model_runs
                  (id, run_type, model_version, git_sha, config, status)
                values (%s, 'backtest', %s, %s, %s::jsonb, 'running')
                """,
                (model_run_id, MODEL_VERSION, config["git_sha"], _json(config)),
            )

            log.info("Walking forward across %s...", seasons)
            predictions = walk_forward(
                seasons,
                max_week=args.max_week,
                prior_season_weight_max=prior_ceiling,
            )
            if not predictions:
                log.error("No predictions produced.")
                return 1

            overall = compute_metrics(predictions)
            assert overall is not None
            log.info("OVERALL: %s", overall.summary())

            by_market = group_metrics(predictions, "market_key")
            by_position = group_metrics(predictions, "position_group")
            by_season = group_metrics(predictions, "season")
            by_phase_groups: dict[str, list[Prediction]] = {}
            for p in predictions:
                by_phase_groups.setdefault(season_phase(p.week), []).append(p)
            by_phase = {
                k: m
                for k, v in sorted(by_phase_groups.items())
                if (m := compute_metrics(v)) is not None
            }

            for name, metrics in by_market.items():
                log.info("  %-18s %s", name, metrics.summary())

            execute(
                """
                insert into backtests
                  (id, model_run_id, name, seasons, hit_rate_basis, config)
                values (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    backtest_id,
                    model_run_id,
                    f"walk-forward {'/'.join(str(s) for s in seasons)}",
                    seasons,
                    hit_rate_basis,
                    _json(config),
                ),
            )
            _store_bins(backtest_id, "overall", overall)
            for market, metrics in by_market.items():
                _store_bins(backtest_id, f"market:{market}", metrics)

            if args.persist_predictions:
                written = _persist(backtest_id, predictions, devig_method)
                log.info("persisted %d predictions", written)

            execute(
                "update model_runs set status='succeeded', finished_at=now() "
                "where id=%s",
                (model_run_id,),
            )

            if not args.no_report:
                REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
                REPORT_PATH.write_text(
                    report_module.render(
                        overall=overall,
                        by_market=by_market,
                        by_position=by_position,
                        by_phase=by_phase,
                        by_season=by_season,
                        seasons=seasons,
                        config=config,
                        caveats=_caveats(predictions, seasons),
                    ),
                    encoding="utf-8",
                )
                log.info("Wrote %s", REPORT_PATH)
    except Exception as exc:
        log.error("Backtest failed: %s", exc, exc_info=True)
        execute(
            "update model_runs set status='failed', finished_at=now() where id=%s",
            (model_run_id,),
        )
        return 1

    return 0


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


if __name__ == "__main__":
    sys.exit(main())
