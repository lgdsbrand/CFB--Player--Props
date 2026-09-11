"""Watch the pipeline and say something when it stops working.

CLAUDE.md §8 Phase 5 asks for monitoring and alerting on the pipeline. The thing
being monitored is `pipeline_runs`, which every job writes through
`worker.db.pipeline_run`, plus the data those jobs are supposed to produce.

    python -m worker.jobs.monitor_pipeline
    python -m worker.jobs.monitor_pipeline --dry-run     # evaluate, send nothing

FOUR CHECKS, AND THE ORDER THEY ARE IN IS THE POINT.

1. **Stuck runs.** `pipeline_run` marks a row `failed` only when Python catches
   the exception. An OOM kill, a Render deploy restart, or a hard timeout leaves
   the row `running` forever — and a `running` row has a RECENT `started_at`, so
   any freshness check keyed on when a job last STARTED reads a dead job as a
   healthy one. That is why check 3 keys on `finished_at where status =
   'succeeded'` and nothing else, and why this check exists separately.

2. **The latest run failed.** Cheap, and the one people expect.

3. **Staleness.** A job that has not SUCCEEDED within its expected interval.
   Expectations live in `MONITORED_JOBS` beside the cron that produces them.

4. **Data freshness.** A job can succeed and write nothing — the provider
   returned an empty list, the week resolved to the wrong number, the filter
   excluded everyone. Checks 1-3 all go green on that. This is the check that
   asks whether the board a reader opens actually has anything on it, and it is
   the one this project has needed most often.

TWO THINGS DELIBERATELY NOT BUILT.

*No alert deduplication.* A condition that persists is re-sent on every run.
Suppression needs state, and a suppression bug is silent by construction — the
symptom is an alert that never arrives, which is indistinguishable from a
healthy pipeline. The cadence in `render.yaml` is the throttle instead.

*No alerting on the monitor's own liveness.* Nothing here can detect that this
job stopped being scheduled; that is the last link and it has to be checked from
outside. Render's cron-failure notification is the backstop, which is why a
critical finding exits non-zero even though the run itself succeeded.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from worker.adapters.alerts import (
    Alert,
    AlertAdapterError,
    Severity,
    get_adapter,
)
from worker.config import ConfigError, get_settings
from worker.core.schedule import Slate, current_slate
from worker.db import fetch_all, fetch_one, get_config_value, pipeline_run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "monitor_pipeline"

#: The sports whose slate is resolved each run. Every `JobExpectation.sport`
#: must be in here — an expectation naming a sport nobody looks up would get
#: `slates.get(...) -> None`, read as "out of season", and be skipped forever
#: without a word. `tests/test_monitor.py` asserts the two agree.
MONITORED_SPORTS: tuple[str, ...] = ("cfb", "nfl")

# How long a run may sit `running` before we call it dead rather than slow. The
# longest scheduled job is the weekly projection pass; a multi-season backfill
# takes longer than this but is run by hand, not on a schedule, so a false
# positive there costs one log line during work someone is already watching.
STUCK_AFTER_HOURS = 6.0


@dataclass(frozen=True)
class JobExpectation:
    """How often a scheduled job is supposed to succeed.

    `max_age_hours` must exceed the job's cron period with room for one missed
    run — alerting the first time a job is an hour late trains people to ignore
    it. Each entry names the `render.yaml` schedule it belongs to so the two
    cannot drift apart unnoticed; `tests/test_monitor.py` asserts they agree.
    """

    name: str
    max_age_hours: float
    severity: Severity = "warning"
    #: Skip out of season, when idleness is correct rather than broken.
    in_season_only: bool = True
    #: WHICH SPORT'S SEASON `in_season_only` MEANS. The two calendars overlap
    #: for about four months and diverge for the rest: in January the college
    #: season is over while the NFL is in its playoffs, and gating an NFL job on
    #: the college slate would stop monitoring it in the one stretch where it is
    #: the only sport playing. Sport-agnostic is not sport-blind, which is the
    #: recurring mistake of this build rather than a one-off — see
    #: `web/lib/core/sport.ts` for the read side of the same discipline.
    sport: str = "cfb"
    #: WHETHER BOTH SPORTS WRITE THIS `job_name`. `build_splits`,
    #: `run_projections` and `ingest_odds` are one module run twice with
    #: `--sport`, so they log under a single name and the staleness query has to
    #: separate them on `metadata->>'sport'` or the newer run answers for both.
    #: Measured on production 2026-09-09: NFL `build_splits` last succeeded
    #: 09-06 23:43 and college's 09-08 08:02, so the shared check reported the
    #: pair healthy while the NFL side had been dead for nearly three days.
    #:
    #: Jobs with a name of their own (`nfl_ingest_stats`, `ingest_reference`)
    #: leave this False: filtering them by sport would buy nothing and would
    #: break on the ones that log no sport at all.
    sport_scoped: bool = False
    #: `app_config` key that must not read "none" for this job to be expected.
    enabled_key: str | None = None
    note: str = ""

    @property
    def label(self) -> str:
        """How this expectation is named in an alert.

        Sport-scoped jobs MUST carry it: two expectations share `name`, so a key
        of `stale:build_splits` would collide and read as one finding about a
        job that is actually two.
        """
        return f"{self.name} ({self.sport})" if self.sport_scoped else self.name


MONITORED_JOBS: tuple[JobExpectation, ...] = (
    JobExpectation(
        name="healthcheck",
        max_age_hours=30,
        severity="critical",
        in_season_only=False,
        note="daily 12:00 UTC — the year-round canary, so no in-season gate",
    ),
    JobExpectation(
        name="ingest_reference",
        max_age_hours=36,
        note="daily 08:00 UTC, chained ahead of ingest_stats (also Sunday 09:00)",
    ),
    JobExpectation(
        name="ingest_stats",
        max_age_hours=36,
        severity="critical",
        note="daily 08:00 UTC — everything downstream is built from these rows",
    ),
    JobExpectation(
        name="ingest_ratings",
        max_age_hours=200,
        note="Sunday 09:00 UTC, chained after ingest_stats",
    ),
    JobExpectation(
        name="build_splits",
        max_age_hours=36,
        severity="critical",
        sport_scoped=True,
        note="daily 08:00 UTC — the position-split engine, CLAUDE.md §5",
    ),
    JobExpectation(
        name="run_projections",
        max_age_hours=36,
        severity="critical",
        sport_scoped=True,
        note="daily 12:30 UTC (--current-week) plus Tuesday 09:00 (--all-weeks) "
             "— without this the board has nothing on it, and since 2026-09-04 "
             "it is also the only thing that turns a captured line into a "
             "priced pick. Was 200h when the Tuesday run was the only one; 36h "
             "allows one missed daily run and no more.",
    ),
    JobExpectation(
        name="ingest_odds",
        max_age_hours=18,
        sport_scoped=True,
        enabled_key="odds_adapter",
        note="every 6h — books post late, often Thu/Fri (CLAUDE.md §7). Was 3h "
             "until 2026-08-12; this is the only job spending metered credits "
             "and the cadence is its budget. 18h tolerates three missed runs, "
             "which matters more here than elsewhere: a silent stop means the "
             "board keeps showing model leans and looks entirely healthy.",
    ),
    JobExpectation(
        name="ingest_game_lines",
        max_age_hours=48,
        note="daily 10:00 UTC — CFBD spreads and totals, costs no odds credits",
    ),
    JobExpectation(
        name="ingest_rankings",
        max_age_hours=200,
        note="Sunday 09:00 UTC, chained — polls publish Sunday, one call/season",
    ),
    JobExpectation(
        name="ingest_weather",
        max_age_hours=48,
        note="daily 11:00 UTC — Open-Meteo forecasts, unauthenticated and free. "
             "A warning rather than a critical: a stale forecast costs the "
             "panel its detail and the model a small feature, while the board, "
             "the calls and the confidences all stand without it.",
    ),
    JobExpectation(
        name="generate_ai_reads",
        max_age_hours=200,
        enabled_key="ai_adapter",
        note="Wednesday 14:00 UTC — one read per player per week, cached",
    ),
    JobExpectation(
        name="audit_data",
        max_age_hours=30,
        in_season_only=False,
        note="daily 13:00 UTC — the data-integrity canary",
    ),
    # The three jobs BOTH sports run under one module name. Each is listed
    # twice — once per sport — and both entries carry `sport_scoped=True`, so
    # the staleness query separates them on `metadata->>'sport'`.
    #
    # THIS CLOSED A GAP THAT WAS ALREADY HIDING A DEAD JOB. Measured on
    # production 2026-09-09: NFL `build_splits` last succeeded 09-06 23:43,
    # college's 09-08 08:02. The shared check read the newer college run and
    # called the pair healthy while the NFL splits had been frozen for nearly
    # three days — the exact failure `nfl_ingest_plays`'s note predicted.
    JobExpectation(
        name="build_splits",
        max_age_hours=36,
        severity="critical",
        sport="nfl",
        sport_scoped=True,
        note="daily 08:20 UTC, last step of the NFL results chain — the NFL "
             "position splits, and the thing that was silently stale",
    ),
    JobExpectation(
        name="run_projections",
        max_age_hours=36,
        severity="critical",
        sport="nfl",
        sport_scoped=True,
        note="daily 12:50 UTC (--current-week --sport nfl) — without it the NFL "
             "board keeps showing last run's picks and looks entirely healthy",
    ),
    JobExpectation(
        name="ingest_odds",
        max_age_hours=18,
        sport="nfl",
        sport_scoped=True,
        enabled_key="odds_adapter",
        note="every 6h at :20 (--sport nfl) — the NFL half of the metered odds "
             "spend, and a silent stop looks like a quiet market",
    ),
    # NFL jobs with names of their own, separable without any sport filter.
    JobExpectation(
        name="nfl_ingest_reference",
        max_age_hours=36,
        sport="nfl",
        note="daily 08:20 UTC, chained ahead of nfl_ingest_stats — `completed` "
             "and the final score come from here, and nothing downstream asks "
             "for a game's stats until it is marked finished",
    ),
    JobExpectation(
        name="nfl_ingest_stats",
        max_age_hours=36,
        severity="critical",
        sport="nfl",
        note="daily 08:20 UTC — box scores and snap counts, the rows every hit "
             "rate and game log on the NFL board is computed from",
    ),
    JobExpectation(
        name="nfl_ingest_plays",
        max_age_hours=36,
        severity="critical",
        sport="nfl",
        note="daily 08:20 UTC — play-by-play, and so the only source of the "
             "position splits (CLAUDE.md §5). Critical rather than a warning "
             "because `build_splits` shares its job name with college: if this "
             "stops, the splits quietly freeze while the splits job itself "
             "keeps reporting success from the college run.",
    ),
    JobExpectation(
        name="build_quarter_stats",
        max_age_hours=36,
        sport="nfl",
        note="daily 08:20 UTC, LAST in the NFL results chain — the first-quarter "
             "actuals behind the Q1 markets. A warning: a missed day leaves Q1 "
             "hit rates one game behind while every full-game surface stands.",
    ),
)


@dataclass
class MonitorReport:
    alerts: list[Alert] = field(default_factory=list)
    checks_run: int = 0
    skipped: list[str] = field(default_factory=list)

    def add(self, severity: Severity, key: str, title: str, detail: str) -> None:
        self.alerts.append(Alert(severity=severity, key=key, title=title, detail=detail))

    @property
    def critical(self) -> list[Alert]:
        return [a for a in self.alerts if a.severity == "critical"]

    def summary(self) -> str:
        if not self.alerts:
            return f"{self.checks_run} checks, everything healthy"
        counts: dict[str, int] = {}
        for alert in self.alerts:
            counts[alert.severity] = counts.get(alert.severity, 0) + 1
        parts = ", ".join(f"{n} {sev}" for sev, n in sorted(counts.items()))
        return f"{self.checks_run} checks, {parts}"


# -----------------------------------------------------------------------------
# Checks
# -----------------------------------------------------------------------------
def check_stuck_runs(report: MonitorReport, *, hours: float = STUCK_AFTER_HOURS) -> None:
    """Runs that opened and never closed.

    See the module docstring: these are the ones that make a dead job look
    fresh, so they are checked on their own rather than inferred from staleness.
    """
    report.checks_run += 1
    rows = fetch_all(
        """
        select job_name,
               count(*)                                          as stuck,
               max(started_at)                                    as latest,
               round(extract(epoch from (now() - min(started_at))) / 3600.0, 1)
                                                                  as oldest_hours
          from pipeline_runs
         where status = 'running'
           and started_at < now() - make_interval(mins => %s)
         group by job_name
         order by job_name
        """,
        (int(hours * 60),),
    )
    for row in rows:
        report.add(
            "critical",
            f"stuck:{row['job_name']}",
            f"{row['job_name']} has {row['stuck']} run(s) stuck in 'running'",
            f"Oldest has been running {row['oldest_hours']}h (threshold {hours}h). "
            "A row stays 'running' when the process died without Python catching "
            "it — an OOM kill, a deploy restart, a hard timeout. Note that these "
            "rows have a recent started_at, so any freshness view keyed on when "
            "a job last STARTED would report this job as healthy.",
        )


def check_latest_run_failed(report: MonitorReport) -> None:
    """Jobs whose most recent run failed, and how many in a row.

    PER (JOB, SPORT), NOT PER JOB. `build_splits`, `run_projections` and
    `ingest_odds` are one module run once per sport, so a bare
    `distinct on (job_name)` returns whichever sport ran LAST — and college runs
    later in the day than the NFL for two of the three. An NFL failure at 12:50
    followed by a college success is not "the latest run", so the failure simply
    disappeared. The sport is its own bucket here rather than something the
    expectations table drives, because a failure is worth reporting whether or
    not anyone wrote an expectation for it.

    `metadata->>'sport'` is used RAW rather than coalesced: NULL is its own
    bucket (Postgres treats NULLs as equal in DISTINCT ON), which keeps jobs
    that log no sport at all — `nfl_ingest_stats` among them — reporting under
    their own name instead of being mislabelled as college.
    """
    report.checks_run += 1
    rows = fetch_all(
        """
        with runs as (
          select job_name,
                 metadata ->> 'sport' as sport,
                 status, started_at, error
            from pipeline_runs
        ),
        latest as (
          select distinct on (job_name, sport)
                 job_name, sport, status, started_at, error
            from runs
           order by job_name, sport, started_at desc
        ),
        streak as (
          select p.job_name, p.sport, count(*) as consecutive
            from runs p
            join latest l on l.job_name = p.job_name
                         and l.sport is not distinct from p.sport
           where p.status = 'failed'
             and p.started_at > coalesce(
                   (select max(s.started_at) from runs s
                     where s.job_name = p.job_name
                       and s.sport is not distinct from p.sport
                       and s.status = 'succeeded'),
                   '-infinity'::timestamptz)
           group by p.job_name, p.sport
        )
        select l.job_name,
               l.sport,
               l.started_at,
               coalesce(l.error, '(no error recorded)') as error,
               coalesce(s.consecutive, 1)               as consecutive
          from latest l
          left join streak s on s.job_name = l.job_name
                            and s.sport is not distinct from l.sport
         where l.status = 'failed'
         order by l.job_name, l.sport
        """
    )
    for row in rows:
        label = row["job_name"] + (f" ({row['sport']})" if row["sport"] else "")
        # The monitor's own failures are reported by the NEXT monitor run, which
        # is why a critical finding here must not fail this run — see main().
        report.add(
            "critical",
            f"failed:{label}",
            f"{label} last run FAILED"
            + (
                f" ({row['consecutive']} consecutive)"
                if int(row["consecutive"]) > 1
                else ""
            ),
            f"Started {row['started_at']:%Y-%m-%d %H:%M UTC}. {row['error']}",
        )


def check_staleness(
    report: MonitorReport, slates: Mapping[str, Slate | None]
) -> None:
    """Jobs that have not SUCCEEDED recently enough.

    Takes a slate PER SPORT rather than one slate: see `JobExpectation.sport`
    for why a single "is it the season" answer is wrong once two leagues share
    the pipeline.
    """
    for expectation in MONITORED_JOBS:
        slate = slates.get(expectation.sport)
        if expectation.in_season_only and (slate is None or not slate.in_season):
            report.skipped.append(
                f"{expectation.name} ({expectation.sport} out of season)"
            )
            continue

        if expectation.enabled_key:
            configured = get_config_value(expectation.enabled_key)
            if configured is None or str(configured) == "none":
                report.skipped.append(
                    f"{expectation.label} ({expectation.enabled_key} is 'none')"
                )
                continue

        report.checks_run += 1
        # `finished_at where succeeded` — NOT started_at, and not the latest run
        # of any status. Both of the obvious alternatives report a job that is
        # reliably crashing, or reliably hanging, as fresh.
        #
        # A NULL `sport` IN METADATA MEANS COLLEGE, and that is history rather
        # than a guess: this pipeline was college-only until 2026-09-06, and
        # nothing wrote the key before then. `coalesce(..., 'cfb')` therefore
        # counts those old rows toward the college expectation and — because the
        # comparison is to the sport being checked — excludes them from the NFL
        # one. Matching `metadata->>'sport' = 'cfb'` strictly instead would make
        # college look stale for every window that reaches back past the change.
        if expectation.sport_scoped:
            row = fetch_one(
                """
                select max(finished_at) as last_success,
                       round(extract(epoch from (now() - max(finished_at))) / 3600.0, 1)
                         as age_hours
                  from pipeline_runs
                 where job_name = %s
                   and status = 'succeeded'
                   and coalesce(metadata ->> 'sport', 'cfb') = %s
                """,
                (expectation.name, expectation.sport),
            )
        else:
            row = fetch_one(
                """
                select max(finished_at) as last_success,
                       round(extract(epoch from (now() - max(finished_at))) / 3600.0, 1)
                         as age_hours
                  from pipeline_runs
                 where job_name = %s
                   and status = 'succeeded'
                """,
                (expectation.name,),
            )
        last_success = (row or {}).get("last_success")

        if last_success is None:
            report.add(
                expectation.severity,
                f"never-succeeded:{expectation.label}",
                f"{expectation.label} has never recorded a successful run",
                f"Expected at least every {expectation.max_age_hours:.0f}h "
                f"({expectation.note}). Either the cron is not deployed or it "
                "has never completed.",
            )
            continue

        age = float(row["age_hours"])  # type: ignore[index]
        if age > expectation.max_age_hours:
            report.add(
                expectation.severity,
                f"stale:{expectation.label}",
                f"{expectation.label} has not succeeded in {age:.0f}h",
                f"Last success {last_success:%Y-%m-%d %H:%M UTC}; expected every "
                f"{expectation.max_age_hours:.0f}h ({expectation.note}).",
            )


def check_data_freshness(report: MonitorReport, slate: Slate | None) -> None:
    """Whether the current slate actually has anything on it.

    THE CHECK FOR THE FAILURE THAT RETURNS SUCCESS. Every check above can pass
    while the board is empty: a job that ran, exited 0, and wrote no rows looks
    identical to one that had nothing to do.

    Each comparison is against SOMETHING THAT ALREADY PRODUCED rather than
    against a fixed threshold, which makes it self-calibrating and survives a
    rule change about which weeks are projectable — a hardcoded floor would
    quietly go wrong.

    WEEK 1 NEEDED ITS OWN REFERENCE, and the lack of one was the exact hole
    Phase 6 exists to close. The original check asked whether an EARLIER WEEK OF
    THE SAME SEASON had produced, on the reasoning that the opening weeks
    legitimately have nothing. Week 1 has no earlier week, so an empty opening
    board could never trip it — and an empty opening board is precisely what the
    client rejected, sitting behind a `run_projections` that exits 0 because it
    genuinely succeeded at projecting nobody. Since Phase 6c the opening weeks
    are published, so the prior SEASON's same week is the reference that makes
    week 1 checkable at all.
    """
    if slate is None or not slate.in_season:
        report.skipped.append("data freshness (out of season)")
        return

    report.checks_run += 1
    row = fetch_one(
        """
        select (select count(*) from projections
                 where season = %(season)s and week = %(week)s) as this_week,
               (select count(*) from projections
                 where season = %(season)s and week < %(week)s) as earlier_weeks,
               (select count(*) from projections
                 where season = %(season)s - 1 and week = %(week)s) as last_season
        """,
        {"season": slate.season, "week": slate.week},  # type: ignore[arg-type]
    )
    assert row is not None
    if int(row["this_week"]) == 0:
        if int(row["earlier_weeks"]) > 0:
            report.add(
                "critical",
                "empty-board",
                f"No projections for {slate.season} week {slate.week}",
                f"{row['earlier_weeks']} projections exist for earlier weeks of "
                "this season, so the pipeline has been producing and has "
                "stopped. The board renders empty for every reader in this "
                "state, and no job has necessarily failed to cause it.",
            )
        elif int(row["last_season"]) > 0:
            report.add(
                "critical",
                "empty-board",
                f"No projections for {slate.season} week {slate.week}, the "
                "first week of the season",
                f"{row['last_season']} projections existed for week "
                f"{slate.week} of {slate.season - 1}, so this week is "
                "projectable in principle and something specific to this season "
                "is missing — most likely the roster, which no amount of "
                "modelling substitutes for. Nothing else would report this: "
                "there is no earlier week of this season to compare against, "
                "and run_projections exits 0 having genuinely succeeded at "
                "projecting nobody.",
            )

    if str(get_config_value("odds_adapter") or "none") != "none":
        report.checks_run += 1
        row = fetch_one(
            """
            select (select count(*) from player_prop_lines l
                      join games g on g.id = l.game_id
                     where g.season = %(season)s and g.week = %(week)s) as this_week
            """,
            {"season": slate.season, "week": slate.week},  # type: ignore[arg-type]
        )
        assert row is not None
        if int(row["this_week"]) == 0 and slate.complete:
            # Only once the slate has kicked off. Before that, no lines is the
            # normal state — college books post props Thursday or Friday for
            # Saturday games (CLAUDE.md §7), so an empty Tuesday is not news.
            report.add(
                "warning",
                "no-lines",
                f"No book lines for {slate.season} week {slate.week}",
                "The odds adapter is configured and the slate has started, but "
                "no quotes were stored. Every pick on the board is showing a "
                "model lean with no line beside it.",
            )


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
def run_checks(slates: Mapping[str, Slate | None]) -> MonitorReport:
    report = MonitorReport()
    check_stuck_runs(report)
    check_latest_run_failed(report)
    check_staleness(report, slates)
    # COLLEGE ONLY, deliberately, and it is a known gap. This check counts
    # `projections` rows for the slate week and compares them against earlier
    # weeks and the prior season — a comparison the NFL cannot make yet, having
    # been projected for the first time on 2026-09-08 with no season of its own
    # behind it. Running it against an NFL slate today would alert on every run
    # for a reason that is true and useless.
    check_data_freshness(report, slates.get("cfb"))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate every check and print the alerts without sending them.",
    )
    parser.add_argument("--adapter", help="Override app_config.alert_adapter.")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)

    delivery_failed: str | None = None
    report = MonitorReport()

    try:
        with pipeline_run(JOB_NAME) as run_id:
            slates = {sport: current_slate(sport=sport) for sport in MONITORED_SPORTS}
            for sport, slate in slates.items():
                if slate is None:
                    log.warning(
                        "%s: no games ingested, so there is no slate to monitor. "
                        "Run the reference ingest before expecting this to mean "
                        "anything.",
                        sport,
                    )
                else:
                    log.info(
                        "Monitoring %s %s week %s (in_season=%s, complete=%s)",
                        sport,
                        slate.season,
                        slate.week,
                        slate.in_season,
                        slate.complete,
                    )

            report = run_checks(slates)
            log.info("%s (run %s)", report.summary(), run_id)
            for skipped in report.skipped:
                log.info("  skipped: %s", skipped)

            if args.dry_run:
                for alert in report.alerts:
                    log.info("WOULD SEND:\n%s", alert.render())
            else:
                delivery_failed = _deliver(report, args.adapter)

            _record(run_id, report, delivery_failed)
    except Exception as exc:
        # A crash here is the monitor itself failing, which the pipeline_run
        # contextmanager has already recorded as `failed` — so the NEXT run of
        # this job reports it through check_latest_run_failed. That is the only
        # self-monitoring available, and it is why this job runs often.
        log.error("Monitor failed: %s", exc)
        return 1

    if delivery_failed:
        log.error("Alert delivery failed: %s", delivery_failed)
        return 1

    # Exit non-zero on a critical finding so RENDER marks the cron failed and
    # sends its own notification. This is the backstop channel, and it works
    # even when ours is misconfigured.
    #
    # The pipeline_run row above is still `succeeded`, and that is not a
    # contradiction: the monitor did its job. Marking it failed would make the
    # next run alert on this run, and every run after that, forever.
    if report.critical:
        log.error(
            "%d critical finding(s) — exiting non-zero so the cron is marked failed.",
            len(report.critical),
        )
        return 1

    return 0


def _deliver(report: MonitorReport, override: str | None) -> str | None:
    """Send every alert. Returns an error string if any could not be delivered."""
    if not report.alerts:
        return None

    name = override or str(get_config_value("alert_adapter") or "log")
    try:
        adapter = get_adapter(name)
    except (AlertAdapterError, ConfigError) as exc:
        return str(exc)

    log.info("Sending %d alert(s) via %s", len(report.alerts), adapter.name)
    for alert in report.alerts:
        try:
            adapter.send(alert)
        except (AlertAdapterError, ConfigError) as exc:
            # Stop at the first failure. Continuing would spend minutes of retry
            # backoff per remaining alert against a channel already known to be
            # down, and the outcome is the same either way: nobody was told.
            return f"{alert.key}: {exc}"
    return None


def _record(run_id: Any, report: MonitorReport, delivery_failed: str | None) -> None:
    """Store what this run found, so alert history survives the log retention."""
    import psycopg

    from worker.db import connect

    metadata = {
        "checks_run": report.checks_run,
        "skipped": report.skipped,
        "alerts": [
            {"severity": a.severity, "key": a.key, "title": a.title}
            for a in report.alerts
        ],
        "delivery_failed": delivery_failed,
    }
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "update pipeline_runs set metadata = %s::jsonb where id = %s",
            (psycopg.types.json.Json(metadata), run_id),
        )


if __name__ == "__main__":
    sys.exit(main())
