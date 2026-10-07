"""Sleeper's public API: the NFL player dump, for injury designations.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). Chosen 2026-10-07 by the user after
the nflverse injury file was measured a week behind: built from the NFL's own
API twice a day, it still had only weeks 1-4 the day after week 5 ended, and a
game page needs THIS week's report. Sleeper carries each player's current
designation (Out, Doubtful, Questionable, IR, PUP...) and the body part.

NO KEY, NO QUOTA, BUT ONE CALL A DAY. Sleeper's docs ask that
`/players/nfl` (~15 MB) be called at most once a day and saved. The client
therefore keeps the payload on disk and serves it from there for
`MIN_REFETCH_SECONDS`, so a re-run of the job, or a second job reading it,
does not call again.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from worker.config import REPO_ROOT
from worker.logging_setup import get_logger

log = get_logger(__name__)

PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
DEFAULT_CACHE_DIR = REPO_ROOT / "worker" / ".cache" / "sleeper"
USER_AGENT = "cfb-props-worker (+https://github.com/lgdsbrand)"
TIMEOUT_SECONDS = 120
DOWNLOAD_ATTEMPTS = 3
RETRY_BASE_SECONDS = 5.0

#: Sleeper asks for at most one players call a day. 20 hours rather than 24
#: so the daily cron, which drifts by minutes, never finds yesterday's copy
#: just inside the window and skips a day.
MIN_REFETCH_SECONDS = 20 * 3600


class SleeperError(RuntimeError):
    """The player dump could not be fetched, or was not what it claimed."""


class SleeperClient:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.downloads = 0

    @property
    def _path(self) -> Path:
        return self.cache_dir / "players_nfl.json"

    def players(self) -> dict[str, dict[str, Any]]:
        """Sleeper player_id -> player record, at most one download a day."""
        path = self._path
        if path.exists() and time.time() - path.stat().st_mtime < MIN_REFETCH_SECONDS:
            log.info("sleeper: players from today's copy (%s)", path)
            return json.loads(path.read_bytes())
        payload = self._download()
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise SleeperError(f"{PLAYERS_URL} returned {type(data).__name__}, not an object")
        os.makedirs(path.parent, exist_ok=True)
        path.write_bytes(payload)
        return data

    def _download(self) -> bytes:
        request = urllib.request.Request(PLAYERS_URL, headers={"User-Agent": USER_AGENT})
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    payload = response.read()
                self.downloads += 1
                log.info("sleeper: fetched %s (%.1f MB)", PLAYERS_URL, len(payload) / 1_048_576)
                return payload
            except urllib.error.HTTPError as exc:
                # Not retried: a status is an answer, and retrying an answer
                # spends the one call a day for nothing.
                raise SleeperError(f"{PLAYERS_URL} -> HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise SleeperError(
                        f"{PLAYERS_URL} -> {exc} after {DOWNLOAD_ATTEMPTS} attempts"
                    ) from None
                delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                log.warning("sleeper: %s (attempt %d), retrying in %.0fs", exc, attempt, delay)
                time.sleep(delay)
        raise SleeperError(PLAYERS_URL)  # pragma: no cover - loop returns or raises
