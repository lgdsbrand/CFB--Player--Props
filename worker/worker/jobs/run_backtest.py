"""Phase 3f: run the walk-forward backtest and write the calibration report.

    python -m worker.jobs.run_backtest
    python -m worker.jobs.run_backtest --seasons 2024 --max-week 8
    python -m worker.jobs.run_backtest --persist-predictions
    python -m worker.jobs.run_backtest --render-only     # no walk, from stored

This is the Phase 3 deliverable and the client review gate (CLAUDE.md §8). It
produces docs/calibration-report.html.

ON RE-RENDERING. Every run stores its headline metrics in `backtest_metrics`
and its curves in `calibration_bins`, so `--render-only` rebuilds the report
from a finished run without walking two seasons again. Before that existed,
changing one sentence of the report cost forty minutes, because the numbers
lived nowhere but inside the HTML.

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

from worker.config import ConfigError, get_settings
from worker.core import report as report_module
from worker.core.backtest import (
    MIN_USAGE_FRACTION_OF_BASELINE,
    CalibrationBin,
    Metrics,
    Prediction,
    compute_metrics,
    group_metrics,
    market_catalogue,
    season_phase,
    walk_forward,
)
from worker.core.calibration import Calibration
from worker.core.models import can_rescale
from worker.db import execute, fetch_all, fetch_one, get_config_value, pipeline_run
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


def _lines_per_projection(predictions: list[Prediction]) -> float:
    """Average number of lines each projection was graded at.

    Recorded into the run's config so the report can state it without the
    predictions in hand — they are not persisted by default, and the caveat
    about correlated observations is not optional.
    """
    per_game: dict[tuple[int, int, str], int] = {}
    for prediction in predictions:
        key = (prediction.player_id, prediction.game_id, prediction.market_key)
        per_game[key] = per_game.get(key, 0) + 1
    return sum(per_game.values()) / len(per_game) if per_game else 0.0


def _stored_caveats(config: dict[str, Any], seasons: list[int]) -> list[str]:
    """Caveats for a run being re-rendered from the database."""
    return _caveats(float(config.get("lines_per_projection") or 0.0), seasons)


def _caveats(lines_per_projection: float, seasons: list[int]) -> list[str]:
    """Everything a reader needs in order not to over-read the numbers."""
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


def _load_run(backtest_id: uuid.UUID | None) -> dict[str, Any] | None:
    """Rebuild a finished run's report inputs from the database.

    The reason `backtest_metrics` exists. Before it, changing a sentence in the
    report meant re-walking two seasons to regenerate the HTML, because the
    numbers lived only inside that HTML. Now the run is reconstructible: the
    headline metrics come from `backtest_metrics`, the curves from
    `calibration_bins`, and the caveats from the config recorded alongside.
    """
    row = fetch_one(
        """
        select b.id, b.seasons, b.config, b.created_at
          from backtests b
         where (%s::uuid is null or b.id = %s::uuid)
           and exists (select 1 from backtest_metrics m where m.backtest_id = b.id)
         order by b.created_at desc
         limit 1
        """,
        (backtest_id, backtest_id),
    )
    if row is None:
        return None

    bins: dict[str | None, list[CalibrationBin]] = {}
    for entry in fetch_all(
        "select market_key, bin_lower, bin_upper, n, mean_predicted_probability, "
        "       observed_rate from calibration_bins where backtest_id = %s "
        " order by market_key nulls first, bin_lower",
        (row["id"],),
    ):
        bins.setdefault(entry["market_key"], []).append(
            CalibrationBin(
                lower=float(entry["bin_lower"]),
                upper=float(entry["bin_upper"]),
                count=int(entry["n"]),
                mean_predicted=float(entry["mean_predicted_probability"]),
                observed_rate=float(entry["observed_rate"]),
            )
        )

    groups: dict[str, dict[str, Metrics]] = {}
    overall: Metrics | None = None
    for entry in fetch_all(
        "select group_kind, group_key, n, base_rate, brier, brier_skill, "
        "       log_loss, ece, sharpness from backtest_metrics "
        " where backtest_id = %s order by group_kind, group_key",
        (row["id"],),
    ):
        metrics = Metrics(
            n=int(entry["n"]),
            base_rate=float(entry["base_rate"]),
            brier=float(entry["brier"]),
            brier_skill=float(entry["brier_skill"]),
            log_loss=float(entry["log_loss"]),
            ece=float(entry["ece"]),
            sharpness=float(entry["sharpness"]),
            bins=bins.get(
                entry["group_key"] if entry["group_kind"] == "market" else None, []
            ),
        )
        if entry["group_kind"] == "overall":
            overall = metrics
        else:
            groups.setdefault(entry["group_kind"], {})[entry["group_key"]] = metrics

    if overall is None:
        return None
    return {
        "overall": overall,
        "groups": groups,
        "seasons": [int(s) for s in row["seasons"]],
        "config": row["config"] or {},
    }


def _store_metrics(
    backtest_id: uuid.UUID,
    overall: Metrics,
    groups: dict[str, dict[str, Metrics]],
) -> int:
    """Persist every headline number this run produced.

    Without this the only record of a run's results is the rendered HTML, so
    telling whether a model change helped means diffing a report — and
    regenerating one costs a full walk.
    """
    rows: list[tuple[Any, ...]] = [
        (backtest_id, "overall", "", overall.n, overall.base_rate, overall.brier,
         overall.brier_skill, overall.log_loss, overall.ece, overall.sharpness)
    ]
    for kind, entries in groups.items():
        for key, metrics in entries.items():
            rows.append(
                (backtest_id, kind, str(key), metrics.n, metrics.base_rate,
                 metrics.brier, metrics.brier_skill, metrics.log_loss,
                 metrics.ece, metrics.sharpness)
            )

    values = ",".join(["(" + ",".join(["%s"] * 10) + ")"] * len(rows))
    params: list[Any] = []
    for row in rows:
        params.extend(row)
    execute(
        "insert into backtest_metrics "
        "(backtest_id, group_kind, group_key, n, base_rate, brier, "
        " brier_skill, log_loss, ece, sharpness) values " + values,
        tuple(params),
    )
    return len(rows)


def _log_comparison(backtest_id: uuid.UUID) -> None:
    """Log this run against the previous one, metric by metric.

    The point of storing metrics at all. `skill` and `ece` move in opposite
    directions when they improve, so the arrows are computed per metric rather
    than assumed — reading a table of raw deltas and mentally flipping the sign
    on half of them is how a regression gets called an improvement.
    """
    previous = fetch_all(
        """
        select group_kind, group_key, n, brier_skill, log_loss, ece, sharpness
          from backtest_metrics
         where backtest_id = (
           select b.id from backtests b
             join backtest_metrics m on m.backtest_id = b.id
            where b.id <> %s
            group by b.id, b.created_at
            order by b.created_at desc
            limit 1)
        """,
        (backtest_id,),
    )
    if not previous:
        log.info("No earlier run with stored metrics — nothing to compare against.")
        return

    current = {
        (r["group_kind"], r["group_key"]): r
        for r in fetch_all(
            "select group_kind, group_key, n, brier_skill, log_loss, ece, "
            "       sharpness from backtest_metrics where backtest_id = %s",
            (backtest_id,),
        )
    }

    log.info("vs previous run (+ = better):")
    for row in sorted(previous, key=lambda r: (r["group_kind"] != "overall",
                                               r["group_kind"], r["group_key"])):
        key = (row["group_kind"], row["group_key"])
        now = current.get(key)
        if now is None:
            continue
        if row["group_kind"] not in ("overall", "market"):
            continue
        skill = float(now["brier_skill"]) - float(row["brier_skill"])
        # Lower is better for these two, so negate to make + mean improvement.
        ece = float(row["ece"]) - float(now["ece"])
        loss = float(row["log_loss"]) - float(now["log_loss"])
        label = row["group_key"] or "OVERALL"
        log.info(
            "  %-18s skill %+.4f   ece %+.4f   logloss %+.4f%s",
            label, skill, ece, loss,
            "" if int(now["n"]) == int(row["n"]) else
            f"   (n {row['n']:,} -> {now['n']:,})",
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
    parser.add_argument(
        "--no-calibration", "--no-variance-calibration",
        dest="no_variance_calibration", action="store_true",
        help="Grade with raw projections, skipping the point-in-time bias and "
             "width corrections. This is the before side of the comparison.",
    )
    parser.add_argument(
        "--render-only", nargs="?", const="latest", metavar="BACKTEST_ID",
        help="Re-render the report from a stored run instead of walking. "
             "Defaults to the most recent run that has stored metrics.",
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)

    if args.render_only:
        target = None if args.render_only == "latest" else uuid.UUID(args.render_only)
        stored = _load_run(target)
        if stored is None:
            log.error(
                "No stored run to render. Run the backtest once first — only "
                "runs that wrote backtest_metrics can be re-rendered."
            )
            return 1
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            report_module.render(
                overall=stored["overall"],
                by_market=stored["groups"].get("market", {}),
                by_position=stored["groups"].get("position", {}),
                by_phase=stored["groups"].get("phase", {}),
                by_season=stored["groups"].get("season", {}),
                seasons=stored["seasons"],
                config=stored["config"],
                caveats=_stored_caveats(stored["config"], stored["seasons"]),
            ),
            encoding="utf-8",
        )
        log.info("Rendered %s from stored run (no walk)", REPORT_PATH)
        return 0

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
            calibration = None if args.no_variance_calibration else Calibration()
            predictions = walk_forward(
                seasons,
                max_week=args.max_week,
                prior_season_weight_max=prior_ceiling,
                calibration=calibration,
            )
            if not predictions:
                log.error("No predictions produced.")
                return 1

            if calibration is not None:
                # A family whose variance is pinned to its mean can be measured
                # but not corrected, so say which those are rather than printing
                # a scale that was never applied. The MEAN correction has no
                # such limit — every family can be moved.
                fixed_width = {
                    m["market_key"]
                    for m in market_catalogue()
                    if not can_rescale(m["distribution_family"])
                }
                learned = calibration.snapshot()
                for market, entry in learned["width"].items():
                    if market.split("@")[0].split(":")[0] in fixed_width:
                        entry["applied"] = False
                        entry["reason"] = "family has no free width parameter"
                config["calibration"] = learned

                for kind, field in (("mean", "multiplier"), ("width", "scale")):
                    for market, entry in sorted(learned[kind].items()):
                        note = ""
                        if not entry["applied"]:
                            reason = entry.get("reason", "still learning")
                            note = f", NOT applied ({reason})"
                        log.info(
                            "  %-5s %-28s x%.3f  (n=%s%s)",
                            kind, market, entry[field], f"{entry['n']:,}", note,
                        )

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

            # Recorded so a later --render-only can state the correlated-
            # observation caveat without the predictions, which are not
            # persisted by default.
            config["lines_per_projection"] = round(
                _lines_per_projection(predictions), 3
            )

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

            written = _store_metrics(
                backtest_id,
                overall,
                {
                    "market": by_market,
                    "position": by_position,
                    "phase": by_phase,
                    "season": {str(k): v for k, v in by_season.items()},
                },
            )
            log.info("stored %d metric rows", written)
            _log_comparison(backtest_id)

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
                        caveats=_caveats(
                            _lines_per_projection(predictions), seasons
                        ),
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
