"""Walk-forward backtest of the game models, and the report (CLAUDE.md §11, G3).

    python -m worker.jobs.run_game_backtest
    python -m worker.jobs.run_game_backtest --test-seasons 2023 2024 2025 --train-from 2022

Writes docs/game-model-backtest.html. Reads the database only; no API calls.
The current season's completed games are scored as an extra out-of-sample
block, trained on every earlier season.
"""

from __future__ import annotations

import argparse
import sys

import polars as pl

from worker.config import REPO_ROOT, ConfigError, get_settings
from worker.core.game_backtest import summarise_runs, walk_forward
from worker.core.game_data import load_games
from worker.core.game_report import render
from worker.db import get_config_value
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

REPORT_PATH = REPO_ROOT / "docs" / "game-model-backtest.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    parser.add_argument("--train-from", type=int, default=2022)
    parser.add_argument("--sport", default="cfb", choices=("cfb",))
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    current = int(get_config_value("current_season") or max(args.test_seasons) + 1)
    seasons = list(range(args.train_from, current + 1))
    games = load_games(seasons, args.sport)
    log.info("Loaded %d games across %s", games.height, seasons)

    runs = walk_forward(games, args.test_seasons)
    summary = summarise_runs(runs)

    extra = None
    if current not in args.test_seasons and games.filter(
        (pl.col("season") == current) & pl.col("completed")
    ).height:
        extra = summarise_runs(walk_forward(games, [current]))

    REPORT_PATH.write_text(
        render(summary, seasons=args.test_seasons, extra=extra), encoding="utf-8"
    )
    log.info("Wrote %s", REPORT_PATH)
    for model, entry in summary.items():
        a = entry["all"]
        log.info(
            "%s: margin %.2f vs market %.2f | total %.2f vs %.2f | spread picks %d hit %.3f "
            "| total picks %d hit %.3f",
            model, a["margin_mae"], a["margin_mae_market"], a["total_mae"],
            a["total_mae_market"], a["cover"]["picks"], a["cover"]["pick_hit"],
            a["over"]["picks"], a["over"]["pick_hit"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
