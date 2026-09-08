"""How the NFL ingest jobs decide which season they are working on.

THE BUG THIS GUARDS AGAINST HAS SHIPPED ONCE ALREADY, on the college side. Every
scheduled job resolved its seasons from `app_config.backfill_seasons`, which
scopes the HISTORICAL backfill and was `[2024, 2025]` sixteen days before the
2026 kickoff. The daily lines cron rewrote 2024 and 2025 every morning, had
never once fetched the season about to be played, and reported success the whole
time. See 20260813140000_in_season_scope.sql.

The NFL jobs avoided that by refusing to default a season at all — but the daily
results cron then needed a year from somewhere, and writing `--seasons 2026` in
`render.yaml` would reproduce the same bug in a file no test reads. `--current`
is the fix, and these are the assertions that keep it wired to the one config key
both sports share.

All offline. No database, no network.
"""

from __future__ import annotations

import inspect

import pytest

from worker.jobs import (
    nfl_ingest_plays,
    nfl_ingest_reference,
    nfl_ingest_stats,
)

JOBS = (nfl_ingest_reference, nfl_ingest_stats, nfl_ingest_plays)


class TestSeasonScope:
    @pytest.mark.parametrize("job", JOBS, ids=lambda j: j.JOB_NAME)
    def test_omitting_both_seasons_and_current_is_an_error(self, job) -> None:
        """Not a fallthrough to `backfill_seasons`, which is the whole point.

        argparse exits 2 on a missing required group. A job that instead picked
        a default would run to completion against the wrong year and report
        success, which is exactly what happened in August.
        """
        with pytest.raises(SystemExit) as exc:
            job.main([])
        assert exc.value.code == 2

    @pytest.mark.parametrize("job", JOBS, ids=lambda j: j.JOB_NAME)
    def test_seasons_and_current_cannot_both_be_given(self, job) -> None:
        # Two answers to one question. Silently preferring either would make the
        # cron's behaviour depend on which branch was written first.
        with pytest.raises(SystemExit) as exc:
            job.main(["--seasons", "2026", "--current"])
        assert exc.value.code == 2

    @pytest.mark.parametrize("job", JOBS, ids=lambda j: j.JOB_NAME)
    def test_the_season_comes_from_the_shared_resolver(self, job) -> None:
        """Read as source: `current_season` is ONE key for both sports.

        A private copy of this logic is how the college side ended up with six
        near-identical `resolve_seasons` functions all reading the wrong key.
        """
        source = inspect.getsource(job.main)
        assert "resolve_seasons(args.seasons, current=args.current)" in source
        assert "sorted(set(args.seasons))" not in source


class TestLiveCacheFreshness:
    """`--current` must also mark the season live, or the cache serves last week.

    `nfl_ingest_stats` and `nfl_ingest_plays` read nflverse through a client
    whose cache never expires for a season it believes is finished. The 2026
    roster blocker was exactly this: a cached zero read as "the data has not
    landed yet" for days. A cron that says "work on the current season" has
    already asserted which season is live; making it say so twice is how the two
    drift apart.
    """

    @pytest.mark.parametrize(
        "job", (nfl_ingest_stats, nfl_ingest_plays), ids=lambda j: j.JOB_NAME
    )
    def test_current_implies_current_season(self, job) -> None:
        source = inspect.getsource(job.main)
        assert "seasons[0] if args.current else args.current_season" in source
        # And the cache decision must read the derived value, not the raw flag.
        assert "season == current_season" in source
        assert "season == args.current_season" not in source
