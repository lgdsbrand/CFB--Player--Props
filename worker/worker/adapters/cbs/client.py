"""CBS Sports' weekly fantasy projection pages, for the fantasy game model.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The client's Google Sheet reads these
pages with IMPORTHTML; this reads the same pages, one per position:

    https://www.cbssports.com/fantasy/football/stats/{QB|RB|WR|TE}/{season}/{week}/projections/ppr/

THE WEEK IS ALWAYS NAMED, NEVER `tp`. The sheet's `tp` ("this period") URL
follows CBS's own calendar; asking for the week we mean, and checking the page
says it is that week (projections.py `check_week`), keeps the capture on our
week axis.

CBS KEEPS NO HISTORY. Measured 2026-10-14: the URLs for 2026 week 1, 2025
week 10 and 2024 week 10 all answer 200 with the CURRENT week's table under a
"Week 6" title. A request for any week but the current or next one therefore
returns the wrong week silently, which is what `check_week` refuses.

Four small pages a run (~0.7 MB each), a second between them. robots.txt
allows these paths; it disallows `?sortcol=` / `?sortdir=`, which the sheet's
WR URL carried and this never sends.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from worker.logging_setup import get_logger

log = get_logger(__name__)

BASE_URL = "https://www.cbssports.com/fantasy/football/stats"
USER_AGENT = "cfb-props-worker (+https://github.com/lgdsbrand)"
TIMEOUT_SECONDS = 60
DOWNLOAD_ATTEMPTS = 3
RETRY_BASE_SECONDS = 5.0
#: Pause between pages, so a run reads like one person paging through positions.
PAUSE_SECONDS = 1.0


class CbsError(RuntimeError):
    """A projection page could not be fetched, or was not the page asked for."""


def page_url(position: str, season: int, week: int) -> str:
    return f"{BASE_URL}/{position}/{season}/{week}/projections/ppr/"


class CbsClient:
    def __init__(self, pause_seconds: float = PAUSE_SECONDS) -> None:
        self.pause_seconds = pause_seconds
        self.downloads = 0

    def projection_page(self, position: str, season: int, week: int) -> str:
        """The HTML of one position's projection page for one week."""
        if self.downloads:
            time.sleep(self.pause_seconds)
        url = page_url(position, season, week)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    payload = response.read()
                self.downloads += 1
                log.info("cbs: fetched %s (%.0f KB)", url, len(payload) / 1024)
                return payload.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code < 500 or attempt == DOWNLOAD_ATTEMPTS:
                    raise CbsError(f"{url} -> HTTP {exc.code}") from None
                reason: object = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise CbsError(f"{url} -> {exc} after {DOWNLOAD_ATTEMPTS} attempts") from None
                reason = exc
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            log.warning("cbs: %s (attempt %d), retrying in %.0fs", reason, attempt, delay)
            time.sleep(delay)
        raise CbsError(url)  # pragma: no cover - loop returns or raises
