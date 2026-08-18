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
import http.client
import json
import time
from decimal import Decimal

import cfbd
import pytest
import urllib3

from worker.adapters.cfbd.cache import ResponseCache, _stable_key
from worker.adapters.cfbd.client import (
    RETRYABLE_STATUSES,
    CfbdClient,
    _backoff_seconds,
    _retry_after_seconds,
    _to_rows,
)
from worker.adapters.cfbd.quota import (
    AccountStatus,
    QuotaError,
    fetch_account_status,
    require_capacity,
)


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


def test_a_non_positive_retry_after_is_treated_as_absent():
    """CFBD's 429 really does send `Retry-After: 0`.

    It arrives inside a boilerplate no-cache header block (`no-store,
    post-check=0, pre-check=0`, `Expires: Thu, 01 Jan 1970`) while the body of
    the same response says "wait a few seconds and retry", so it is not an
    instruction about anything. Reading it literally is what made the morning's
    retry fix fail again the same evening.
    """
    assert _retry_after_seconds(FakeApiError(429, {"Retry-After": "0"})) is None
    assert _retry_after_seconds(FakeApiError(429, {"Retry-After": "-30"})) is None


def test_rate_limits_wait_far_longer_than_transient_5xx():
    """A 429 is a penalty box; a 502 is a hiccup. They do not deserve one curve.

    Measured 2026-08-17: the limiter was still hot 74 seconds after the previous
    job's last call, so the 5xx curve's ~35s over five attempts cannot clear it.
    """
    assert 2.5 <= _backoff_seconds(1, FakeApiError(429), 429) <= 7.5
    assert 0.75 <= _backoff_seconds(1, FakeApiError(503), 503) <= 2.25


def test_retry_after_can_lengthen_a_wait_but_never_shorten_one():
    """Deferring to a longer request is politeness; accepting a shorter one is
    letting a rate limiter talk us into hammering it."""
    assert _backoff_seconds(1, FakeApiError(503, {"Retry-After": "45"}), 503) == 45.0
    # Capped: a server asking for ten minutes still gets retried at MAX_BACKOFF.
    assert _backoff_seconds(1, FakeApiError(503, {"Retry-After": "600"}), 503) == 60.0
    # And the zero cannot pull the 429 curve down to nothing.
    assert _backoff_seconds(1, FakeApiError(429, {"Retry-After": "0"}), 429) >= 2.5


def test_the_real_cfbd_exception_is_read_correctly():
    """Against the REAL exception class and a REAL HTTPHeaderDict, not our fake.

    This is the check the morning's fix did not have. `FakeApiError` carries a
    plain dict; production carries `urllib3.HTTPHeaderDict` built from
    `http_resp.getheaders()`, and a fake that gets that shape wrong is exactly
    how a green suite ships an outage. Verified live against CFBD on 2026-08-18:
    the 429 body is "Too many requests in a short period" and the header block
    really does say `Retry-After: 0`.
    """

    class FakeHttpResponse:
        status = 429
        reason = "Too Many Requests"
        data = b'{"error":{"code":429}}'

        def getheaders(self):
            return urllib3.HTTPHeaderDict(
                {
                    "Retry-After": "0",
                    "Cache-Control": "private, max-age=0, no-store, no-cache",
                }
            )

    exc = cfbd.exceptions.ApiException(http_resp=FakeHttpResponse())

    assert exc.status == 429
    assert _retry_after_seconds(exc) is None, "the zero was taken at face value"
    assert _backoff_seconds(1, exc, exc.status) >= 2.5


def test_a_rate_limited_run_actually_waits(monkeypatch):
    """THE REGRESSION, 2026-08-17 evening. Retries that do not wait are not retries.

    `d0c221e` gave the account preflight the retry loop it had been missing, and
    the re-triggered ingest failed at 20:12 with the identical uncaught HTTP 429
    — every log line on the same second. The loop was running; it honoured
    `Retry-After: 0` five times over, so all five attempts fired inside a second
    into a limiter that needed more than a minute.

    This asserts the wait, not just the attempt count, because the attempt count
    was already right when the job died.
    """
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)

    client = CfbdClient.__new__(CfbdClient)
    client.min_interval = 0
    client.max_retries = 5
    client.call_count = 0
    client._last_call_at = 0.0

    def always_limited(**_params):
        # The exact header block CFBD sent on 2026-08-17.
        raise FakeApiError(
            429,
            {
                "Retry-After": "0",
                "Cache-Control": "private, max-age=0, no-store, no-cache, "
                "must-revalidate, post-check=0, pre-check=0",
                "Expires": "Thu, 01 Jan 1970 00:00:01 GMT",
            },
        )

    with pytest.raises(FakeApiError):
        client._call_with_retry(always_limited, {}, "/info")

    assert len(waits) == 5, "five retries, five waits"
    assert min(waits) >= 2.5, "a retry fired without waiting"
    assert sum(waits) > 60, "gave up inside the burst penalty seen in production"


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


# ------------------------------------------------------- the account preflight
#
# `fetch_account_status` is the first CFBD call every ingest job makes, so
# whatever protection it has is the protection each job's opening move has. It
# had none: it went through a bare `api()` helper that skipped pacing, backoff
# and retry, and nothing here covered it.
#
# On 2026-08-17 that ended the Sunday chain. The five jobs run back-to-back
# under one `&&`, so `ingest_stats` opened on a rate limiter `ingest_reference`
# had just left hot, drew a 429 on this call, and exited 1 — taking
# `ingest_ratings`, `ingest_rankings` and `build_splits` down with it.


def _preflight_client(**overrides):
    """A CfbdClient with the retry machinery live and the network absent."""
    client = CfbdClient.__new__(CfbdClient)  # bypass __init__/auth
    client.min_interval = 0
    client.max_retries = 5
    client.call_count = 0
    client._last_call_at = 0.0
    client._api_client = object()  # only ever handed to the API class
    client.cache = overrides.get("cache")
    return client


def _fake_info_api(responder):
    """Build a stand-in for cfbd.InfoApi whose get_user_info runs `responder`."""

    class FakeInfoApi:
        def __init__(self, _api_client):
            pass

        def get_user_info(self, **_params):
            return responder()

    return FakeInfoApi


def test_preflight_survives_a_rate_limit(monkeypatch):
    """THE REGRESSION. A 429 on /info must cost a retry, not the whole chain."""
    monkeypatch.setattr(time, "sleep", lambda _: None)

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            # WITH THE HEADER PRODUCTION ACTUALLY SENDS. This test passed while
            # the job kept failing, because a headerless 429 never exercised the
            # `Retry-After: 0` path. Stubbed sleep means the count is all this
            # one can see — `test_a_rate_limited_run_actually_waits` checks the
            # waits themselves.
            raise FakeApiError(429, {"Retry-After": "0"})
        return FakeModel(
            tierName="Tier 2", monthlyLimit=30_000, remainingCalls=29_000
        )

    monkeypatch.setattr(cfbd, "InfoApi", _fake_info_api(flaky))

    status = fetch_account_status(_preflight_client())

    assert attempts["n"] == 3, "the preflight did not retry"
    assert status.tier_name == "Tier 2"
    assert status.remaining_calls == 29_000


def test_preflight_still_fails_fast_on_a_bad_key(monkeypatch):
    """Retrying must not mask the one failure that will never come good.

    A 401 means the key is wrong. Backing off five times before saying so turns
    an instant, clear answer into a minute of silence in a cron log.
    """
    monkeypatch.setattr(time, "sleep", lambda _: None)

    attempts = {"n": 0}

    def denied():
        attempts["n"] += 1
        raise FakeApiError(401)

    monkeypatch.setattr(cfbd, "InfoApi", _fake_info_api(denied))

    with pytest.raises(FakeApiError):
        fetch_account_status(_preflight_client())
    assert attempts["n"] == 1


def test_preflight_is_never_served_from_cache(monkeypatch, tmp_path):
    """A cached quota reading is worse than none.

    /info reports remaining calls. Cache it and the guard reads yesterday's
    headroom as today's, then waves through a job that cannot afford to run —
    the failure `require_capacity` exists to prevent.
    """
    monkeypatch.setattr(time, "sleep", lambda _: None)
    cache = ResponseCache(tmp_path)

    monkeypatch.setattr(
        cfbd,
        "InfoApi",
        _fake_info_api(lambda: FakeModel(tierName="Tier 2", remainingCalls=5)),
    )

    fetch_account_status(_preflight_client(cache=cache))

    assert cache.get("/info", {}, None) is None, "the preflight wrote to the cache"


def test_retryable_statuses_cover_rate_limit_and_5xx():
    assert 429 in RETRYABLE_STATUSES
    assert {500, 502, 503, 504} <= RETRYABLE_STATUSES
    assert 401 not in RETRYABLE_STATUSES


# ------------------------------------------------------- transport-level failures
#
# `cfbd/rest.py` converts exactly ONE transport failure into an ApiException
# (SSLError, as status 0). A read timeout, a reset connection, a DNS failure or
# urllib3's MaxRetryError arrive as themselves with no `status` attribute, so a
# status-only retry test declines to retry them and the Sunday chain dies on a
# blip. Nothing in production had exercised this; the tests come first here.


@pytest.mark.parametrize(
    "error",
    [
        urllib3.exceptions.ProtocolError("Connection aborted"),
        urllib3.exceptions.ReadTimeoutError(None, "/info", "read timed out"),
        ConnectionResetError("reset by peer"),
        http.client.RemoteDisconnected("closed without response"),
        FakeApiError(0),  # how cfbd reports a TLS failure
    ],
)
def test_transport_failures_are_retried(monkeypatch, error):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    client = CfbdClient.__new__(CfbdClient)
    client.min_interval = 0
    client.max_retries = 3
    client.call_count = 0
    client._last_call_at = 0.0

    attempts = {"n": 0}

    def flaky(**_params):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise error
        return [FakeModel(ok=True)]

    assert _to_rows(client._call_with_retry(flaky, {}, "/test")) == [{"ok": True}]
    assert attempts["n"] == 3, f"{type(error).__name__} was not retried"


def test_our_own_bugs_are_not_retried(monkeypatch):
    """A TypeError is a defect here, not weather. Retrying it wastes a minute
    and buries the traceback under five warnings."""
    monkeypatch.setattr(time, "sleep", lambda _: None)
    client = CfbdClient.__new__(CfbdClient)
    client.min_interval = 0
    client.max_retries = 5
    client.call_count = 0
    client._last_call_at = 0.0

    calls = {"n": 0}

    def broken(**_params):
        calls["n"] += 1
        raise TypeError("get_games() got an unexpected keyword argument")

    with pytest.raises(TypeError):
        client._call_with_retry(broken, {}, "/test")
    assert calls["n"] == 1


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
