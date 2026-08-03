"""Which week the pipeline is currently working on.

SPORT-AGNOSTIC CORE (CLAUDE.md §3). Nothing here knows about conferences, CFBD,
or how a week is numbered beyond "weeks have kickoffs and kickoffs have times".

WHY THIS EXISTS: A CRON CANNOT BE TOLD THE WEEK. Every job so far has taken
`--season` and `--week` from a human at a terminal, which is why `render.yaml`
still carried commented-out placeholders instead of schedules — `ingest_odds`
and `generate_ai_reads` both *require* those arguments and a cron has nobody to
supply them. Scheduling the pipeline means the pipeline has to answer "what week
is it" for itself.

The decision is separated from the query on purpose, the same way
`worker/core/name_match.py` is: "which week is current" is a rule with awkward
edges — Tuesday MACtion, a Friday game, a slate still in progress at midnight
UTC, and eight months of offseason — and rules with awkward edges should be
testable without a database.

THE OFFSEASON IS THE EDGE THAT MATTERS. From January to August there is nothing
to ingest and every job is legitimately idle. A monitor that does not know this
sends a stale-data alert every day for seven months, and the people receiving it
learn to ignore it — so by the time the season starts, the alerting is worse
than none at all. `Slate.in_season` is what stops that, and it is the reason
this module returns more than a number.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# A slate stays "current" for a while after its last kickoff: games run about
# four hours, stats settle later still, and a job firing at 04:00 UTC on Sunday
# is working on Saturday's games, not next week's.
TRAILING_WINDOW = timedelta(hours=18)

# How far from the nearest kickoff we still consider the pipeline in season.
# Wide on both sides deliberately — week 1 ingest starts before week 1 kicks
# off, and bowl season trails the regular season by a month.
IN_SEASON_WINDOW = timedelta(days=21)


@dataclass(frozen=True)
class SlateWeek:
    """One week's kickoff window. The shape this module needs, nothing more."""

    season: int
    week: int
    first_kickoff: datetime
    last_kickoff: datetime


@dataclass(frozen=True)
class Slate:
    season: int
    week: int
    #: True when the pipeline is expected to be doing work at all.
    in_season: bool
    #: Whether this week's games have all kicked off (plus the trailing window).
    complete: bool


def pick_slate(weeks: Sequence[SlateWeek], now: datetime | None = None) -> Slate | None:
    """Decide which season and week the pipeline should be working on.

    Three cases, in order:

      1. A slate is UNDERWAY — `now` is between its first kickoff and its last
         plus the trailing window. The latest such week wins, because the
         windows genuinely overlap: college plays Tuesday and Wednesday games,
         so week N+1 can kick off before week N has finished settling.
      2. A slate is UPCOMING — the earliest week whose first kickoff is still
         ahead. This is the ordinary weekday case and the one the crons live in.
      3. Neither, so we are past the last game on record. Returns the final
         week with `in_season` False. It does NOT return None, because "the
         season is over" and "no schedule is loaded" need different responses
         and only the second is a problem.

    Returns None only when there are no weeks at all, which means nothing has
    been ingested.
    """
    if not weeks:
        return None

    moment = _as_utc(now) if now else datetime.now(UTC)
    ordered = sorted(weeks, key=lambda w: (w.first_kickoff, w.season, w.week))

    nearest = min(
        min(
            abs(moment - _as_utc(w.first_kickoff)),
            abs(moment - _as_utc(w.last_kickoff)),
        )
        for w in ordered
    )
    in_season = nearest <= IN_SEASON_WINDOW

    underway = [
        w
        for w in ordered
        if _as_utc(w.first_kickoff) <= moment <= _as_utc(w.last_kickoff) + TRAILING_WINDOW
    ]
    if underway:
        chosen = underway[-1]
        return Slate(
            season=chosen.season,
            week=chosen.week,
            in_season=in_season,
            complete=moment > _as_utc(chosen.last_kickoff),
        )

    upcoming = [w for w in ordered if _as_utc(w.first_kickoff) > moment]
    if upcoming:
        chosen = upcoming[0]
        return Slate(
            season=chosen.season, week=chosen.week, in_season=in_season, complete=False
        )

    chosen = ordered[-1]
    return Slate(
        season=chosen.season, week=chosen.week, in_season=in_season, complete=True
    )


def _as_utc(value: datetime) -> datetime:
    """Treat a naive timestamp as UTC rather than as local time.

    Postgres hands back tz-aware values for `timestamptz`, so this is defensive
    — but comparing a naive datetime to an aware one raises, and a monitor that
    crashes on a timezone is a monitor that is not monitoring.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def load_slate_weeks() -> list[SlateWeek]:
    """Read every week's kickoff window from the games table."""
    # Imported here so the decision logic above stays importable without a
    # database, which is what makes it testable.
    from worker.db import fetch_all

    return [
        SlateWeek(
            season=int(row["season"]),
            week=int(row["week"]),
            first_kickoff=row["first_kickoff"],
            last_kickoff=row["last_kickoff"],
        )
        for row in fetch_all(
            """
            select season,
                   week,
                   min(start_date) as first_kickoff,
                   max(start_date) as last_kickoff
              from games
             where start_date is not null
             group by season, week
             order by season, week
            """
        )
    ]


def current_slate(now: datetime | None = None) -> Slate | None:
    """The season and week the pipeline should be working on, from the database."""
    return pick_slate(load_slate_weeks(), now)


def resolve_slate_args(
    season: int | None, week: int | None, *, now: datetime | None = None
) -> tuple[int, int]:
    """Fill in whichever of season/week a caller left out.

    Explicit arguments always win — a human at a terminal debugging week 4 must
    not have their week silently replaced. This only supplies what was omitted,
    which is the case a cron is in.

    Raises `ConfigError` rather than guessing when there is no schedule to read.
    A job that quietly picked "this calendar year, week 1" would run against an
    empty slate, succeed, write nothing, and look healthy — which is the shape
    of bug this pipeline keeps producing.
    """
    from worker.config import ConfigError

    if season is not None and week is not None:
        return season, week

    slate = current_slate(now)
    if slate is None:
        raise ConfigError(
            "No games are ingested, so the current season and week cannot be "
            "resolved. Pass --season and --week explicitly, or run "
            "`python -m worker.jobs.ingest_reference` first."
        )
    return (season if season is not None else slate.season,
            week if week is not None else slate.week)
