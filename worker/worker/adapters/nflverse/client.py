"""Download nflverse release assets, cached on disk, as polars frames.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3).

WHAT THIS DELIBERATELY DOES NOT HAVE, AND WHY THAT IS NOT AN OVERSIGHT.
`CfbdClient` owns pacing, backoff and a quota preflight, because a
several-hundred-call CFBD backfill against a metered key is one bad loop away
from a suspension. nflverse is static files on a CDN: no key, no quota, no rate
limit, and one request per season per dataset rather than 888. Reproducing that
apparatus here would be ceremony around a `GET`.

WHAT IT KEEPS IS THE CACHE, AND FOR A DIFFERENT REASON THAN CFBD'S. There, the
cache saved quota. Here it saves *time and bandwidth* — a season of play-by-play
is tens of megabytes and the split engine is re-run repeatedly while attribution
is refined. The freshness rule is the same one, and it is the caller's decision:
`max_age=None` for a completed season, which is immutable, and a bounded value
for the season being played.

THE STALENESS RULE THAT BIT WITHIN AN HOUR OF WRITING THIS. A cache hit is not
evidence the upstream file still says what it said. That is not a theoretical
worry in this repo: on the same afternoon this was written, a CFBD ingest
reported "cache: 6 hit / 0 miss" and a clean exit while replaying a response
captured before the games were played, and wrote `completed = false` back over
eight finished games. Live-season reads must pass a bounded `max_age`, and
`fetch` refuses to guess one for them.
"""

from __future__ import annotations

import csv
import io
import time
import urllib.error
import urllib.request
from pathlib import Path

import polars as pl

from worker.adapters.nflverse.assets import (
    REQUIRE_SEASON_PRESENT,
    SEASON_IN_URL,
    asset_url,
)
from worker.config import REPO_ROOT
from worker.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_CACHE_DIR = REPO_ROOT / "worker" / ".cache" / "nflverse"

#: Set NFLVERSE_CACHE=off to bypass entirely, mirroring CFBD_CACHE.
_DISABLED_VALUES = {"0", "off", "false", "no"}

USER_AGENT = "cfb-props-worker (+https://github.com/lgdsbrand)"

#: Generous: these are tens-of-megabyte files from a CDN, not an API call.
TIMEOUT_SECONDS = 180


class NflverseError(RuntimeError):
    """A release asset could not be fetched, or was not what it claimed."""


def cache_enabled() -> bool:
    import os

    return os.environ.get("NFLVERSE_CACHE", "on").strip().lower() not in _DISABLED_VALUES


class NflverseClient:
    """Reads nflverse release assets. No key, no quota, no pacing."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.downloads = 0
        self.cache_hits = 0

    # -- plumbing ------------------------------------------------------------

    def _cache_path(self, asset: str, season: int | None) -> Path:
        # Keyed on what the URL actually varies by. An all-seasons file asked
        # for 2025 and then 2026 is ONE download, and caching it twice under two
        # names would serve the second read a copy that is a season out of date
        # the moment the first one goes stale.
        if asset in SEASON_IN_URL and season is not None:
            return self.cache_dir / f"{asset}_{season}.csv"
        return self.cache_dir / f"{asset}.csv"

    def _download(self, url: str, dest: Path) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise NflverseError(f"{url} -> HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise NflverseError(f"{url} -> {exc.reason}") from None

        if cache_enabled():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
        self.downloads += 1
        log.info("nflverse: fetched %s (%.1f MB)", url, len(payload) / 1_048_576)
        return payload

    # -- the one method callers use ------------------------------------------

    def fetch(
        self, asset: str, season: int | None = None, *, max_age: float | None,
    ) -> pl.DataFrame:
        """One release asset as a polars frame.

        `max_age` is REQUIRED and has no default, deliberately. A default would
        have to be either None (never expires, wrong for the live season) or a
        number (wrong for the 26 completed seasons this reads for priors), and
        the wrong one is silent in both directions. Making the caller say which
        it is means the decision is visible at every call site.
        """
        url = asset_url(asset, season)
        path = self._cache_path(asset, season)

        payload: bytes | None = None
        if cache_enabled() and path.exists():
            age = time.time() - path.stat().st_mtime
            if max_age is None or age <= max_age:
                self.cache_hits += 1
                log.debug("nflverse cache hit %s (age %.0fs)", path.name, age)
                payload = path.read_bytes()

        if payload is None:
            payload = self._download(url, path)

        frame = pl.read_csv(
            io.BytesIO(payload),
            infer_schema_length=10_000,
            null_values=["", "NA"],
        )
        self._assert_season_present(asset, season, frame, url)
        return frame

    # -- the guard the module docstring in assets.py is about -----------------

    @staticmethod
    def _assert_season_present(
        asset: str, season: int | None, frame: pl.DataFrame, url: str
    ) -> None:
        """Refuse an asset that answered 200 with the wrong season in it.

        nflverse keeps a frozen legacy weekly-stats file alongside the current
        one, and it still returns 200 with 134,470 well-formed rows that stop at
        2024. Nothing about that response is malformed; it is simply two seasons
        stale. Without this, a 2026 model would train on it and look fine.
        """
        if asset not in REQUIRE_SEASON_PRESENT:
            return
        if season is None or "season" not in frame.columns:
            return

        seasons = set(frame["season"].unique().to_list())
        if season not in seasons:
            newest = max(seasons) if seasons else None
            raise NflverseError(
                f"{url} returned 200 but contains no rows for season {season} "
                f"(newest present: {newest}). This is the frozen-legacy-asset "
                f"trap in assets.py — check the release tag before trusting it."
            )


def sniff_columns(payload: bytes, limit: int = 1) -> list[str]:
    """The header row of a CSV payload, without parsing the whole file.

    For probing a release asset's shape cheaply — a season of play-by-play is
    large enough that `read_csv` to inspect column names is a real cost.
    """
    text = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8", errors="replace")
    reader = csv.reader(text)
    for row in reader:
        return row
    return []
