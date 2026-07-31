"""Phase 1 deploy proof for the worker.

Run on Render's cron schedule, and by hand during first-time setup. It verifies,
against live services, that:

  1. the worker's environment is wired (config loads without error),
  2. it can reach Postgres and the migrations have actually been applied,
  3. it can WRITE — the pipeline_runs row is inserted by the same code path
     every later ingest job will use,
  4. the CFBD API key authenticates AND carries the paid tier we depend on.

Every check runs even if an earlier one fails, so first-time setup gets a full
readout ("database OK, CFBD key missing") instead of stopping at the first
problem. The job still exits non-zero and marks the pipeline_run failed if
anything failed, which is what the cron canary needs.

Makes exactly one CFBD call: this runs on a schedule and there is no reason to
spend quota proving the same thing repeatedly.

    python -m worker.jobs.healthcheck
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from worker.config import ConfigError, get_settings
from worker.db import fetch_one, pipeline_run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "healthcheck"

# Tables that must exist for the schema to be considered applied.
EXPECTED_TABLES = (
    "conferences",
    "teams",
    "team_seasons",
    "venues",
    "games",
    "players",
    "player_team_seasons",
    "player_game_stats",
    "plays",
    "play_player_stats",
    "defense_position_game_splits",
    "defense_position_ratings",
    "team_rating_snapshots",
    "game_weather",
    "markets",
    "market_positions",
    "sportsbooks",
    "player_prop_lines",
    "model_runs",
    "pipeline_runs",
    "projections",
    "picks",
    "ai_reads",
    "backtests",
    "backtest_predictions",
    "calibration_bins",
    "app_config",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str

    def render(self) -> str:
        return f"  [{'PASS' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


def check_schema_applied() -> CheckResult:
    """Confirm every expected table exists."""
    try:
        row = fetch_one(
            """
            select coalesce(
                     array_agg(expected.name order by expected.name)
                     filter (where t.tablename is null),
                     '{}'
                   ) as missing
              from unnest(%s::text[]) as expected(name)
              left join pg_tables t
                     on t.schemaname = 'public'
                    and t.tablename = expected.name
            """,
            (list(EXPECTED_TABLES),),
        )
    except Exception as exc:
        return CheckResult("schema applied", False, f"query failed: {exc}")

    missing = (row or {}).get("missing") or []
    if missing:
        return CheckResult(
            "schema applied",
            False,
            f"{len(missing)} table(s) missing: {', '.join(missing)} — run `supabase db push`",
        )
    return CheckResult(
        "schema applied", True, f"all {len(EXPECTED_TABLES)} expected tables present"
    )


def check_seed_data() -> CheckResult:
    """Confirm the seed migration ran."""
    try:
        row = fetch_one(
            """
            select (select count(*) from markets)          as markets,
                   (select count(*) from market_positions) as market_positions,
                   (select count(*) from conferences)      as conferences,
                   (select count(*) from app_config)       as app_config
            """
        )
    except Exception as exc:
        return CheckResult("seed data", False, f"query failed: {exc}")

    assert row is not None
    counts = {k: int(v) for k, v in row.items()}
    if counts["markets"] == 0 or counts["app_config"] == 0:
        return CheckResult(
            "seed data", False, f"seed migration did not run: {counts}"
        )
    return CheckResult("seed data", True, str(counts))


def check_row_counts() -> CheckResult:
    """Report ingest volume. Zero is expected until Phase 2."""
    try:
        row = fetch_one(
            """
            select (select count(*) from teams)             as teams,
                   (select count(*) from games)             as games,
                   (select count(*) from players)           as players,
                   (select count(*) from player_game_stats) as player_game_stats,
                   (select count(*) from projections)       as projections
            """
        )
    except Exception as exc:
        return CheckResult("row counts", False, f"query failed: {exc}")

    assert row is not None
    return CheckResult(
        "row counts", True, str({k: int(v) for k, v in row.items()})
    )


# Warn when the monthly allowance drops below this fraction. A two-season
# backfill is a few hundred calls, so 15% of 30k is still comfortable headroom —
# this is an early warning, not a red line.
_QUOTA_WARN_FRACTION = 0.15


def check_cfbd() -> CheckResult:
    """Confirm the key authenticates, carries the entitlements we need, and has
    quota left.

    Uses /info rather than exercising a paid endpoint. Two earlier versions of
    this check were weaker:

      * `get_conferences()` proved only that the key was accepted — the FREE
        tier serves it, so the check went green on a key with no entitlement to
        weather, which CLAUDE.md §4 requires.
      * A weather call proved entitlement, but only by inference from a
        successful response, and it needed a hardcoded historical season/week
        as a probe target that would eventually go stale.

    /info states the tier and the per-feature entitlements outright, AND returns
    the remaining monthly call count — so the canary can warn before a backfill
    runs into the ceiling rather than after. Still exactly one call.
    """
    try:
        from worker.adapters.cfbd.client import CfbdClient
        from worker.adapters.cfbd.quota import fetch_account_status

        with CfbdClient() as client:
            status = fetch_account_status(client)
    except ConfigError as exc:
        return CheckResult("cfbd account", False, str(exc))
    except Exception as exc:
        http_status = getattr(exc, "status", None)
        if http_status in (401, 403):
            return CheckResult(
                "cfbd account",
                False,
                f"HTTP {http_status} on /info — CFBD is rejecting this key "
                "outright (typo, truncated paste, or revoked). Re-copy it from "
                "https://collegefootballdata.com/key",
            )
        return CheckResult("cfbd account", False, f"{type(exc).__name__}: {exc}")

    missing = status.missing_required_features
    if missing:
        return CheckResult(
            "cfbd account",
            False,
            f"{status.summary()} — but required feature(s) {missing} are NOT "
            "enabled on this key. The paid tier is inactive, lapsed, or "
            "downgraded; ingest cannot proceed without them.",
        )

    if (
        status.remaining_calls is not None
        and status.monthly_limit
        and status.remaining_calls < status.monthly_limit * _QUOTA_WARN_FRACTION
    ):
        return CheckResult(
            "cfbd account",
            False,
            f"{status.summary()} — below {_QUOTA_WARN_FRACTION:.0%} of the "
            "monthly allowance. Backfills should wait for the reset.",
        )

    detail = status.summary()
    if status.missing_optional_features:
        detail += f" (optional features unavailable: {status.missing_optional_features})"
    return CheckResult("cfbd account", True, detail)


def run_checks() -> list[CheckResult]:
    return [
        check_schema_applied(),
        check_seed_data(),
        check_row_counts(),
        check_cfbd(),
    ]


def main() -> int:
    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)
    log.info("Starting healthcheck (%s)", settings)

    try:
        with pipeline_run(JOB_NAME) as run_id:
            results = run_checks()
            log.info("Healthcheck results:\n%s", "\n".join(r.render() for r in results))

            failed = [r for r in results if not r.ok]
            if failed:
                raise RuntimeError(
                    f"{len(failed)} check(s) failed: "
                    + "; ".join(r.name for r in failed)
                )
            log.info("Healthcheck passed (run %s)", run_id)
    except Exception as exc:
        log.error("Healthcheck failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
