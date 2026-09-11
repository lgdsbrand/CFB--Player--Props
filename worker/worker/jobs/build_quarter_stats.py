"""First-quarter actuals: fill `player_game_stats.q1_*` from play-by-play.

    python -m worker.jobs.build_quarter_stats --current --sport nfl
    python -m worker.jobs.build_quarter_stats --sport nfl --seasons 2023 2024 2025
    python -m worker.jobs.build_quarter_stats --sport nfl --seasons 2025 --reconcile

Reads only the database -- no API calls -- so it can be re-run freely. Run it
after `nfl_ingest_plays`, which writes the plays it derives from.

`--sport` IS REQUIRED, unlike `build_splits`, where it defaults to college. The
derivation has been checked against NFL box scores only (see
core/quarter_stats.py), and a default would make the one sport it has never
been checked on the silent path. Reconcile a sport before building it.

`--reconcile` WRITES NOTHING. It runs the same derivation over every quarter,
compares it with the box score, and exits 1 if any stat matches on fewer than
99% of player-games. It records no pipeline run, so a check can never pass for
the scheduled build in the monitor's eyes.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from worker.config import ConfigError, get_settings
from worker.core.quarter_stats import (
    RECONCILE_MIN_EXACT_SHARE,
    STATS,
    build_first_quarter_stats,
    failing_stats,
    reconcile,
    season_summary,
)
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "build_quarter_stats"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only. What the daily cron passes.",
    )
    parser.add_argument(
        "--sport", choices=("cfb", "nfl"), required=True,
        help="Which league. Required: the derivation is verified per sport, "
             "and has been verified on the NFL only.",
    )
    parser.add_argument(
        "--reconcile", action="store_true",
        help="Compare the all-quarter derivation with the box score and write "
             "nothing. Exits 1 if any stat falls below the match threshold.",
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)

    try:
        seasons = resolve_seasons(args.seasons, current=args.current)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        if args.reconcile:
            return _reconcile(seasons, args.sport)

        for season in seasons:
            with pipeline_run(
                JOB_NAME, metadata={"season": season, "sport": args.sport}
            ) as run_id:
                changed = build_first_quarter_stats(season, args.sport)
                set_rows_written(run_id, changed)
            log.info(
                "%s %d: %d row(s) changed\n%s",
                args.sport, season, changed,
                _render_summary(season_summary(season, args.sport)),
            )
    except Exception as exc:
        log.error("Quarter stats build failed: %s", exc, exc_info=True)
        return 1
    return 0


def _reconcile(seasons: list[int], sport: str) -> int:
    failed = False
    for season in seasons:
        rows = reconcile(season, sport)
        log.info(
            "Reconcile %s %d -- all quarters against the box score:\n%s",
            sport, season, _render_reconcile(rows),
        )
        bad = failing_stats(rows)
        if bad:
            failed = True
            log.error(
                "%s %d: %s below %.0f%% exact, or nothing to compare. Do not "
                "trust first-quarter figures for this season.",
                sport, season, ", ".join(bad), RECONCILE_MIN_EXACT_SHARE * 100,
            )
    return 1 if failed else 0


def _render_reconcile(rows: list[dict[str, Any]]) -> str:
    lines = [f"  {'stat':<14} {'rows':>7} {'box':>9} {'derived':>9} {'exact':>8} {'worst':>6}"]
    for row in rows:
        share = row.get("exact_share")
        lines.append(
            f"  {row['stat']:<14} {row['player_games']:>7,} "
            f"{int(row['box_total'] or 0):>9,} {int(row['derived_total'] or 0):>9,} "
            f"{'—' if share is None else f'{float(share):.2%}':>8} "
            f"{int(row['worst_gap'] or 0):>6}"
        )
    return "\n".join(lines)


def _render_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"  {summary.get('derived', 0):,} of {summary.get('player_games', 0):,} "
        "player-games derived (the rest have no play-by-play)"
    ]
    for stat in STATS:
        q1 = summary.get(f"q1_{stat}")
        full = summary.get(f"full_{stat}")
        if q1 is None or not full:
            continue
        lines.append(
            f"  {stat:<14} Q1 {int(q1):>7,} of {int(full):>8,}  ({int(q1) / int(full):.1%})"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
