"""On-disk cache of raw CFBD responses.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3).

Why this exists: the two-season all-FBS backfill is several hundred calls, and
the position-split engine (§5) will be re-run many times as play attribution is
refined. Without a cache, every one of those iterations re-spends quota on data
that cannot have changed — completed games are immutable. With it, only the
first run costs anything and the rest are free and fast.

Design notes:

  * Entries are plain JSON, one file per request, with the endpoint and params
    stored alongside the payload. Debuggable with a text editor, which matters
    when a split looks wrong and the question is "what did the API actually
    say?".

  * Freshness is the CALLER's decision, via max_age. Completed seasons are
    immutable and cache forever; live in-week data must not be served stale.
    There is deliberately no global default TTL, because one number cannot be
    right for both and a wrong default would silently serve a Saturday refresh
    from Tuesday's data.

  * The cache is a development and backfill-iteration tool. Render's filesystem
    is ephemeral, so a scheduled production run starts cold — that is fine and
    intended; correctness never depends on a cache hit.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker.config import REPO_ROOT
from worker.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_CACHE_DIR = REPO_ROOT / "worker" / ".cache" / "cfbd"

# Set CFBD_CACHE=off to bypass entirely (forces live calls; still writes).
_DISABLED_VALUES = {"0", "off", "false", "no"}


def cache_enabled() -> bool:
    return os.environ.get("CFBD_CACHE", "on").strip().lower() not in _DISABLED_VALUES


def _stable_key(endpoint: str, params: dict[str, Any]) -> str:
    """Hash endpoint + params into a filename-safe key.

    Params are sorted and JSON-encoded with default=str so enums and dates
    hash consistently; an unstable key would silently produce a permanent miss
    and quietly defeat the whole point of the cache.
    """
    payload = json.dumps(
        {"endpoint": endpoint, "params": params},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    safe_endpoint = endpoint.strip("/").replace("/", "_") or "root"
    return f"{safe_endpoint}__{digest}"


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    bypassed: int = 0

    @property
    def calls_saved(self) -> int:
        return self.hits

    def summary(self) -> str:
        total = self.hits + self.misses
        rate = (100.0 * self.hits / total) if total else 0.0
        return (
            f"cache: {self.hits} hit / {self.misses} miss ({rate:.0f}% hit rate), "
            f"{self.writes} written, {self.bypassed} bypassed"
        )


class ResponseCache:
    """JSON file cache keyed on endpoint + params."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory else DEFAULT_CACHE_DIR
        self.stats = CacheStats()

    def path_for(self, endpoint: str, params: dict[str, Any]) -> Path:
        return self.directory / f"{_stable_key(endpoint, params)}.json"

    def get(
        self,
        endpoint: str,
        params: dict[str, Any],
        max_age: float | None,
    ) -> list[dict[str, Any]] | None:
        """Return cached rows, or None on miss/expiry/disabled.

        max_age is in seconds; None means "never expires", appropriate for
        completed seasons whose data cannot change.
        """
        if not cache_enabled():
            self.stats.bypassed += 1
            return None

        path = self.path_for(endpoint, params)
        if not path.exists():
            self.stats.misses += 1
            return None

        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt entry must never be fatal: treat it as a miss and let
            # the live call overwrite it.
            log.warning("Discarding unreadable cache entry %s: %s", path.name, exc)
            self.stats.misses += 1
            return None

        if max_age is not None:
            age = time.time() - float(entry.get("fetched_at", 0))
            if age > max_age:
                log.debug("Cache entry %s expired (%.0fs old)", path.name, age)
                self.stats.misses += 1
                return None

        self.stats.hits += 1
        return entry.get("rows", [])

    def put(
        self,
        endpoint: str,
        params: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> None:
        path = self.path_for(endpoint, params)
        path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "endpoint": endpoint,
            "params": params,
            "fetched_at": time.time(),
            "row_count": len(rows),
            "rows": rows,
        }

        # Write to a temp file then replace, so an interrupted run cannot leave
        # a half-written entry that later reads as valid JSON.
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(entry, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            log.warning("Could not write cache entry %s: %s", path.name, exc)
            tmp.unlink(missing_ok=True)
            return

        self.stats.writes += 1

    def clear(self) -> int:
        """Delete every cached entry. Returns the number of files removed."""
        if not self.directory.exists():
            return 0
        removed = 0
        for path in self.directory.glob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
        log.info("Cleared %d cache entries from %s", removed, self.directory)
        return removed
