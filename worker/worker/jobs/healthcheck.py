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


# A season/week known to have completed games, used only as a probe target.
# Deliberately historical rather than "current season": in the offseason a
# current-season call returns an empty list, which is indistinguishable from a
# broken key and would make this check flap twice a year.
_TIER_PROBE_SEASON = 2024
_TIER_PROBE_WEEK = 5


def _diagnose_cfbd_denial(status: int) -> CheckResult:
    """Tell a rejected key apart from an unentitled one after a 401/403.

    Retries against a free-tier endpoint. If that works, the credential itself
    is fine and the subscription is the problem; if it fails too, the key is
    being rejected outright.
    """
    import cfbd

    from worker.adapters.cfbd.client import CfbdClient

    try:
        with CfbdClient() as client:
            client.api(cfbd.ConferencesApi).get_conferences()
    except Exception:
        return CheckResult(
            "cfbd api key (paid tier)",
            False,
            f"HTTP {status} — CFBD rejects this key on free-tier endpoints too, "
            "so the key itself is wrong (typo, truncated paste, or revoked) "
            "rather than the subscription. Re-copy it from "
            "https://collegefootballdata.com/key",
        )

    return CheckResult(
        "cfbd api key (paid tier)",
        False,
        f"HTTP {status} on weather, but free-tier endpoints work — the key is "
        "VALID and simply not entitled to weather. The paid tier is inactive or "
        "lapsed; check the CFBD Patreon subscription (CLAUDE.md §4 requires "
        "weather, so ingest cannot proceed on the free tier).",
    )


def check_cfbd() -> CheckResult:
    """Confirm the CFBD key authenticates AND carries the paid tier.

    Probes the weather endpoint specifically, not a generic one. The free tier
    serves conferences, teams and games perfectly happily, so a check built on
    those goes green on a free key — and weather is exactly the feature the paid
    tier was bought for (CLAUDE.md §4 requires venue/weather). A green light that
    does not cover the entitlement we depend on is worse than no light at all,
    because the gap would surface partway through a Phase 2 weather ingest.

    Still exactly one CFBD call, so this costs no more quota than the auth-only
    check it replaces.
    """
    try:
        import cfbd

        from worker.adapters.cfbd.client import CfbdClient

        with CfbdClient() as client:
            rows = client.api(cfbd.GamesApi).get_weather(
                year=_TIER_PROBE_SEASON, week=_TIER_PROBE_WEEK
            )
    except ConfigError as exc:
        return CheckResult("cfbd api key (paid tier)", False, str(exc))
    except Exception as exc:
        status = getattr(exc, "status", None)
        if status in (401, 403):
            # 401/403 is ambiguous on its own: a bad key and a valid-but-
            # unentitled key look identical here. Disambiguate with one extra
            # call to a FREE-tier endpoint, because "fix your key" and "renew
            # your subscription" send you to completely different places. Only
            # the failure path pays this second call.
            return _diagnose_cfbd_denial(status)
        return CheckResult(
            "cfbd api key (paid tier)", False, f"{type(exc).__name__}: {exc}"
        )

    if not rows:
        # 200 with an empty body means entitled but no data for that week —
        # the probe target is stale, not the credentials.
        return CheckResult(
            "cfbd api key (paid tier)",
            False,
            f"weather endpoint returned no rows for {_TIER_PROBE_SEASON} "
            f"week {_TIER_PROBE_WEEK}; the probe target needs updating",
        )

    return CheckResult(
        "cfbd api key (paid tier)",
        True,
        f"weather accessible — {len(rows)} rows for "
        f"{_TIER_PROBE_SEASON} week {_TIER_PROBE_WEEK}",
    )


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
