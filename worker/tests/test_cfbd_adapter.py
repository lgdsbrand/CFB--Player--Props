"""Tests for the CFBD adapter's pacing, backoff, caching and quota guard.

These are the parts of Phase 2a that protect the API key, and they are exactly
the parts that cannot be verified by reading a green log line: the retry path
only runs when CFBD is unhappy, and the quota guard only runs when the budget is
nearly gone. So they are driven here with fakes instead.

No network access and no database.
"""

from __future__ import annotations

import datetime
import enum
import json
import time
from decimal import Decimal

import pytest

from worker.adapters.cfbd.cache import ResponseCache, _stable_key
from worker.adapters.cfbd.client import (
    RETRYABLE_STATUSES,
    CfbdClient,
    _retry_after_seconds,
    _to_rows,
)
from worker.adapters.cfbd.quota import AccountStatus, QuotaError, require_capacity


class FakeApiError(Exception):
    """Mimics cfbd's ApiException: carries .status and optional .headers."""

    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.headers = headers or {}


class FakeModel:
    """Mimics a cfbd model object, which exposes to_dict()."""

    def __init__(self, **fields):
        self._fields = fields

    def to_dict(self):
        return dict(self._fields)


# ---------------------------------------------------------------- cache keys


def test_cache_key_is_order_independent():
    """Param order must not change the key, or every re-run would miss."""
    a = _stable_key("/games", {"year": 2024, "week": 3})
    b = _stable_key("/games", {"week": 3, "year": 2024})
    assert a == b


def test_cache_key_distinguishes_params_and_endpoints():
    assert _stable_key("/games", {"year": 2024}) != _stable_key("/games", {"year": 2025})
    assert _stable_key("/games", {"year": 2024}) != _stable_key("/plays", {"year": 2024})


def test_cache_key_is_filename_safe():
    key = _stable_key("/games/weather", {"year": 2024})
    assert "/" not in key and "\\" not in key


# -------------------------------------------------------------- cache behaviour


def test_cache_roundtrip(tmp_path):
    cache = ResponseCache(tmp_path)
    rows = [{"id": 1, "team": "Alabama"}]

    assert cache.get("/teams", {"year": 2024}, None) is None
    cache.put("/teams", {"year": 2024}, rows)
    assert cache.get("/teams", {"year": 2024}, None) == rows

    assert cache.stats.hits == 1
    assert cache.stats.misses == 1
    assert cache.stats.writes == 1


def test_cache_respects_max_age(tmp_path):
    """Live in-week data must not be served stale."""
    cache = ResponseCache(tmp_path)
    cache.put("/lines", {"week": 5}, [{"line": -3.5}])

    # Backdate the entry well past any plausible freshness window.
    path = cache.path_for("/lines", {"week": 5})
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["fetched_at"] = time.time() - 3600
    path.write_text(json.dumps(entry), encoding="utf-8")

    assert cache.get("/lines", {"week": 5}, max_age=60) is None  # expired
    assert cache.get("/lines", {"week": 5}, max_age=None) is not None  # immutable


def test_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path):
    """A truncated file must degrade to a live call, never kill a backfill."""
    cache = ResponseCache(tmp_path)
    cache.put("/games", {"year": 2024}, [{"id": 1}])
    cache.path_for("/games", {"year": 2024}).write_text("{not json", encoding="utf-8")

    assert cache.get("/games", {"year": 2024}, None) is None


def test_cache_disabled_by_env(tmp_path, monkeypatch):
    cache = ResponseCache(tmp_path)
    cache.put("/teams", {"year": 2024}, [{"id": 1}])

    monkeypatch.setenv("CFBD_CACHE", "off")
    assert cache.get("/teams", {"year": 2024}, None) is None
    assert cache.stats.bypassed == 1


def test_cache_clear(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("/a", {}, [{"x": 1}])
    cache.put("/b", {}, [{"x": 2}])
    assert cache.clear() == 2
    assert cache.get("/a", {}, None) is None


# ------------------------------------------------------------------ row coercion


def test_to_rows_handles_models_lists_and_singletons():
    assert _to_rows(None) == []
    assert _to_rows([{"a": 1}]) == [{"a": 1}]
    assert _to_rows(FakeModel(a=1)) == [{"a": 1}]
    assert _to_rows([FakeModel(a=1), FakeModel(a=2)]) == [{"a": 1}, {"a": 2}]


class FakeSeasonType(enum.Enum):
    REGULAR = "regular"


def test_to_rows_unwraps_enums_and_datetimes():
    """CFBD's to_dict() leaves enums and datetimes as Python objects.

    If they reach the JSON cache as-is they stringify to 'FakeSeasonType.REGULAR'
    and a cached read then disagrees with a live read. Normalizing at the
    boundary is what keeps the two identical.
    """
    row = _to_rows(
        FakeModel(
            seasonType=FakeSeasonType.REGULAR,
            startDate=datetime.datetime(2024, 8, 24, 16, 0, tzinfo=datetime.UTC),
            nested={"kind": FakeSeasonType.REGULAR},
            items=[FakeSeasonType.REGULAR],
            amount=Decimal("3.5"),
        )
    )[0]

    assert row["seasonType"] == "regular"
    assert row["startDate"] == "2024-08-24T16:00:00+00:00"
    assert row["nested"]["kind"] == "regular"
    assert row["items"] == ["regular"]
    assert row["amount"] == 3.5


def test_live_and_cached_rows_are_identical(tmp_path):
    """The regression this guards: same call, different type depending on cache.

    A JSON round-trip must be a no-op. If it is not, the first run and every
    later run see different data.
    """
    rows = _to_rows(
        FakeModel(
            seasonType=FakeSeasonType.REGULAR,
            startDate=datetime.datetime(2024, 8, 24, 16, 0, tzinfo=datetime.UTC),
        )
    )

    cache = ResponseCache(tmp_path)
    cache.put("/games", {"year": 2024}, rows)
    assert cache.get("/games", {"year": 2024}, None) == rows


# ---------------------------------------------------------------------- backoff


def test_retry_after_header_is_honoured():
    assert _retry_after_seconds(FakeApiError(429, {"Retry-After": "7"})) == 7.0
    assert _retry_after_seconds(FakeApiError(429)) is None
    assert _retry_after_seconds(FakeApiError(429, {"Retry-After": "soon"})) is None


def test_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)  # no real waiting
    client = CfbdClient.__new__(CfbdClient)  # bypass __init__/auth
    client.min_interval = 0
    client.max_retries = 5
    client.call_count = 0
    client._last_call_at = 0.0

    attempts = {"n": 0}

    def flaky(**_params):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise FakeApiError(429)
        return [FakeModel(ok=True)]

    result = client._call_with_retry(flaky, {}, "/test")
    assert attempts["n"] == 3
    assert _to_rows(result) == [{"ok": True}]
    assert client.call_count == 3  # failed attempts consume quota too


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    client = CfbdClient.__new__(CfbdClient)
    client.min_interval = 0
    client.max_retries = 2
    client.call_count = 0
    client._last_call_at = 0.0

    def always_503(**_params):
        raise FakeApiError(503)

    with pytest.raises(FakeApiError):
        client._call_with_retry(always_503, {}, "/test")
    assert client.call_count == 3  # initial + 2 retries


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(monkeypatch, status):
    """A bad key must fail fast, not after a minute of pointless backoff."""
    monkeypatch.setattr(time, "sleep", lambda _: None)
    client = CfbdClient.__new__(CfbdClient)
    client.min_interval = 0
    client.max_retries = 5
    client.call_count = 0
    client._last_call_at = 0.0

    calls = {"n": 0}

    def denied(**_params):
        calls["n"] += 1
        raise FakeApiError(status)

    with pytest.raises(FakeApiError):
        client._call_with_retry(denied, {}, "/test")
    assert calls["n"] == 1


def test_retryable_statuses_cover_rate_limit_and_5xx():
    assert 429 in RETRYABLE_STATUSES
    assert {500, 502, 503, 504} <= RETRYABLE_STATUSES
    assert 401 not in RETRYABLE_STATUSES


# ------------------------------------------------------------------ quota guard


def _status(remaining: int, limit: int = 30000, **kwargs) -> AccountStatus:
    defaults = dict(
        tier_name="Tier 2",
        patron_level=2,
        monthly_limit=limit,
        remaining_calls=remaining,
        used_calls=limit - remaining,
        reset_at="2026-08-01T00:00:00Z",
        shared_pool=True,
        products=["cfb", "cbb"],
        features={"weather": True, "livePlayByPlay": True, "adjustedMetrics": True},
    )
    defaults.update(kwargs)
    return AccountStatus(**defaults)


def test_capacity_allows_comfortable_budget():
    require_capacity(_status(remaining=29000), estimated_calls=600)


def test_capacity_refuses_when_short():
    with pytest.raises(QuotaError, match="Refusing to start"):
        require_capacity(_status(remaining=500), estimated_calls=600)


def test_capacity_applies_safety_margin():
    """600 calls with a 1.25x margin needs 750; 700 remaining is not enough."""
    with pytest.raises(QuotaError):
        require_capacity(_status(remaining=700), estimated_calls=600)
    require_capacity(_status(remaining=800), estimated_calls=600)


def test_capacity_proceeds_when_quota_unknown():
    """Unknown quota must not block work; it warns and continues."""
    require_capacity(_status(remaining=0, remaining_calls=None), estimated_calls=600)


def test_missing_required_feature_is_detected():
    degraded = _status(remaining=29000, features={"weather": False})
    assert degraded.missing_required_features == ["weather"]
    assert _status(remaining=29000).missing_required_features == []


def test_summary_reports_tier_and_remaining():
    text = _status(remaining=29990).summary()
    assert "Tier 2" in text
    assert "29,990" in text
    assert "30,000" in text
