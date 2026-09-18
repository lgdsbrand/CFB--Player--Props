"""Usage share: fill `player_game_stats.target_share` and `.rush_share`.

    python -m worker.jobs.build_usage_shares --current --sport nfl
    python -m worker.jobs.build_usage_shares --sport cfb --seasons 2024 2025

Reads only the database -- no API calls -- so it can be re-run freely. Run it
after the box-score ingest for that sport, never inside it: the denominator is a
sum over every row of a team-game, so it is not knowable until the last of them
has landed.

`--sport` IS REQUIRED. The two leagues differ in what this can publish -- NFL
target attribution is effectively complete while college loses about one
team-game in seven to the completeness guard -- and a default would make one of
them the silent path. `build_quarter_stats` requires it for the same reason.

`snap_share` IS NOT WRITTEN HERE. It is nflverse's own `offense_pct`, written by
the snap adapter alongside `snaps`; migration 0071 records why it cannot be
derived from this table.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from worker.config import ConfigError, get_settings
from worker.core.usage import (
    MIN_TARGET_ATTRIBUTION,
    build_usage_shares,
    season_summary,
)
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "build_usage_shares"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only. What the daily cron passes.",
    )
    parser.add_argument(
        "--sport", choices=("cfb", "nfl"), required=True,
        help="Which league. Required: the two differ in how much of the target "
             "attribution survives the completeness guard.",
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
        for season in seasons:
            with pipeline_run(
                JOB_NAME, metadata={"season": season, "sport": args.sport}
            ) as run_id:
                changed = build_usage_shares(season, args.sport)
                set_rows_written(run_id, changed)
            log.info(
                "%s %d: %d row(s) changed\n%s",
                args.sport, season, changed,
                _render_summary(season_summary(season, args.sport)),
            )
    except Exception as exc:
        log.error("Usage share build failed: %s", exc, exc_info=True)
        return 1
    return 0


def _render_summary(summary: dict[str, Any]) -> str:
    player_games = summary.get("player_games") or 0
    withheld = summary.get("withheld_targets") or 0
    with_targets = summary.get("with_targets") or 0
    mean_wr = summary.get("mean_wr_target_share")

    def share(part: Any) -> str:
        part = part or 0
        return f"{part:,} of {player_games:,}" + (
            f" ({part / player_games:.1%})" if player_games else ""
        )

    lines = [
        f"  target share  {share(summary.get('with_target_share'))}",
        f"  rush share    {share(summary.get('with_rush_share'))}",
        f"  snap share    {share(summary.get('with_snap_share'))}"
        "   (nflverse offense_pct; college has no source)",
        f"  withheld      {withheld:,} row(s) had targets but no share"
        + (f" ({withheld / with_targets:.1%})" if with_targets else "")
        + f" -- team attribution under {MIN_TARGET_ATTRIBUTION:.0%} of pass attempts",
    ]
    if mean_wr is not None:
        lines.append(f"  mean WR share {float(mean_wr):.1%}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
