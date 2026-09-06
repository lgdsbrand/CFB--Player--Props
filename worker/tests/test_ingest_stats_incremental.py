"""Tests for the daily results ingest.

WHY THIS EXISTS. On 2026-09-05 production held 8 games' worth of player stats
while 22 week-1 games had been played, because `ingest_stats` only ran in the
Sunday chain. Everything downstream reads those rows — hit rates, game logs,
defence splits, and the closing-line grading, which cannot start until a week's
actuals exist.

The fix is a daily cron, and the only thing that made a daily cadence
unaffordable was the per-game `/plays/stats` fan-out: 888 calls for a full 2026
season against a 30,000/month CFBD quota is fine weekly and is 27,000 a month
daily.

THE DANGEROUS HALF IS THE WRITE, NOT THE FETCH. Both `plays` and
`play_player_stats` load by clearing the season and COPYing it back, which is
exactly right when the very next step rewrites the whole season and catastrophic
when it does not: `plays.id` is `generated always as identity` and
`play_player_stats.play_id` references it `on delete cascade`, so a season-wide
delete takes every attribution row with it and reissues every id. A partial
reload has to be scoped to the games it is actually reloading.
"""

from __future__ import annotations

from typing import Any

from worker.adapters.cfbd import ingest_stats as adapter
from worker.adapters.cfbd.ingest_stats import SeasonContext, StatsCounts


def _ctx(*cfbd_ids: int) -> SeasonContext:
    return SeasonContext(
        season=2026,
        games_by_cfbd={
            i: {"id": i * 10, "cfbd_id": i, "season": 2026, "week": 1,
                "home_team_id": 1, "away_team_id": 2}
            for i in cfbd_ids
        },
        game_id_by_cfbd={i: i * 10 for i in cfbd_ids},
        team_id_by_school={},
        player_id_by_athlete={},
        position_by_player={},
    )


class RecordingClient:
    """Records which games the per-game fan-out actually paid for."""

    def __init__(self) -> None:
        self.asked: list[int] = []

    def fetch(self, endpoint: str, api: Any, method: str, **params: Any) -> list:
        self.asked.append(params["game_id"])
        return []


class CapturingExecute:
    """Records the DELETE statements a load issues, with their parameters."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, sql: str, params: Any = None) -> int:
        self.calls.append((" ".join(sql.split()), params))
        return 0


class TestIncrementalFanOut:
    @staticmethod
    def _run(monkeypatch, *, only_games: list[int] | None) -> list[int]:
        client = RecordingClient()
        monkeypatch.setattr(adapter, "fetch_all", lambda *a, **k: [])
        monkeypatch.setattr(adapter, "execute", CapturingExecute())
        monkeypatch.setattr(adapter, "copy_into", lambda *a, **k: 0)
        adapter.ingest_play_player_stats(
            client, _ctx(1, 2, 3), StatsCounts(), only_games=only_games
        )
        return client.asked

    def test_it_asks_only_for_the_games_it_is_missing(self, monkeypatch):
        # THE POINT OF THE CHANGE. Three games in the season, one missing, one
        # call — not three.
        assert self._run(monkeypatch, only_games=[2]) == [2]

    def test_nothing_missing_spends_nothing(self, monkeypatch):
        # The ordinary weekday case: no new games since yesterday.
        assert self._run(monkeypatch, only_games=[]) == []

    def test_a_backfill_still_asks_for_every_game(self, monkeypatch):
        # Guards the historical load path. `--seasons 2024` must refetch
        # everything, because a repair re-run is exactly the case where the
        # rows we already have are the ones under suspicion.
        assert self._run(monkeypatch, only_games=None) == [1, 2, 3]


class TestThePartialReloadIsScoped:
    """The cascade trap: a season-wide DELETE would wipe what it did not reload.

    `delete from plays where season = X` cascades into `play_player_stats` and
    hands every surviving play a new id. The weekly full run repairs that by
    rewriting the season; a daily partial run would simply lose it.
    """

    @staticmethod
    def _deletes(monkeypatch, *, only_games: list[int] | None) -> list:
        calls = CapturingExecute()
        monkeypatch.setattr(adapter, "execute", calls)
        monkeypatch.setattr(adapter, "copy_into", lambda *a, **k: 0)
        monkeypatch.setattr(adapter, "fetch_all", lambda *a, **k: [])
        adapter.ingest_play_player_stats(
            RecordingClient(), _ctx(1, 2, 3), StatsCounts(), only_games=only_games
        )
        return calls.calls

    def test_a_partial_reload_deletes_only_its_own_games(self, monkeypatch):
        calls = self._deletes(monkeypatch, only_games=[2])

        assert len(calls) == 1
        sql, params = calls[0]
        assert "game_id = any(%s)" in sql
        # Our surrogate id, not the CFBD one.
        assert params == (2026, [20])

    def test_a_full_reload_still_clears_the_season(self, monkeypatch):
        calls = self._deletes(monkeypatch, only_games=None)

        sql, params = calls[0]
        assert "game_id" not in sql
        assert params == (2026,)

    def test_an_empty_reload_deletes_nothing_at_all(self, monkeypatch):
        # The most dangerous case: an ordinary quiet weekday. A season-wide
        # delete here followed by a COPY of zero rows empties the table.
        assert self._deletes(monkeypatch, only_games=[]) == []

    def test_plays_is_scoped_the_same_way(self, monkeypatch):
        calls = CapturingExecute()
        monkeypatch.setattr(adapter, "execute", calls)
        monkeypatch.setattr(adapter, "copy_into", lambda *a, **k: 0)
        monkeypatch.setattr(adapter, "fetch_all", lambda *a, **k: [])
        monkeypatch.setattr(adapter, "week_slices", lambda season: [("regular", 1)])
        monkeypatch.setattr(adapter, "unsettled_slices", lambda season: set())

        adapter.ingest_plays(
            RecordingSliceClient(), _ctx(1, 2, 3), StatsCounts(), only_games=[2]
        )

        sql, params = calls.calls[0]
        assert "game_id = any(%s)" in sql
        assert params == (2026, [20])

    def test_plays_skips_the_week_slices_when_there_is_nothing_to_reload(
        self, monkeypatch
    ):
        client = RecordingSliceClient()
        monkeypatch.setattr(adapter, "execute", CapturingExecute())
        monkeypatch.setattr(adapter, "copy_into", lambda *a, **k: 0)
        monkeypatch.setattr(adapter, "fetch_all", lambda *a, **k: [])
        monkeypatch.setattr(adapter, "week_slices", lambda season: [("regular", 1)])
        monkeypatch.setattr(adapter, "unsettled_slices", lambda season: set())

        adapter.ingest_plays(client, _ctx(1, 2, 3), StatsCounts(), only_games=[])

        assert client.slices == []


class RecordingSliceClient:
    def __init__(self) -> None:
        self.slices: list[tuple[str, int]] = []

    def fetch(self, endpoint: str, api: Any, method: str, **params: Any) -> list:
        self.slices.append((params["season_type"], params["week"]))
        return []


class TestEstimateMatchesTheRun:
    """The estimate feeds `require_capacity`, so it has to describe THIS run.

    An estimate of 888 games against a run that fetches 14 would refuse to
    start on a quota it was never going to spend — a preflight that blocks the
    job it is protecting.
    """

    @staticmethod
    def _estimate(monkeypatch, *, incremental: bool) -> int:
        monkeypatch.setattr(
            adapter, "week_slices", lambda season: [("regular", 1), ("regular", 2)]
        )
        monkeypatch.setattr(
            adapter, "games_missing_play_stats", lambda season: [7, 8]
        )
        monkeypatch.setattr(
            adapter, "fetch_all", lambda *a, **k: [{"id": i} for i in range(888)]
        )
        return adapter.estimate_calls(2026, incremental=incremental)

    def test_incremental_counts_only_the_missing_games(self, monkeypatch):
        # 2 slices x 2 endpoints + 2 missing games
        assert self._estimate(monkeypatch, incremental=True) == 6

    def test_a_full_run_counts_the_whole_season(self, monkeypatch):
        assert self._estimate(monkeypatch, incremental=False) == 892


class TestSettledWeeksDecideCacheLifetime:
    """`IMMUTABLE` is right for a finished week and wrong for the current one.

    A week-1 slice fetched on the Sunday when 8 of 99 games had been played
    would otherwise be replayed from disk all week, and the other 91 games
    would never land. Render's filesystem is ephemeral so a scheduled run
    always starts cold — but a by-hand run does not, and this job is now run
    daily and by hand.
    """

    def test_a_week_with_an_unplayed_game_is_unsettled(self, monkeypatch):
        monkeypatch.setattr(
            adapter, "fetch_all",
            lambda *a, **k: [{"season_type": "regular", "week": 1}],
        )
        assert ("regular", 1) in adapter.unsettled_slices(2026)

    def test_a_fully_played_season_has_no_unsettled_slices(self, monkeypatch):
        monkeypatch.setattr(adapter, "fetch_all", lambda *a, **k: [])
        assert adapter.unsettled_slices(2026) == set()
