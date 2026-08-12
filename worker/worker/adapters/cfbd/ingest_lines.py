"""Game spreads, totals and moneylines from CFBD `/lines`.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The NFL build replaces this file; the
`game_lines` table and the consensus view above it are sport-agnostic.

COSTS NO ODDS API CREDITS. CFBD serves game lines on the tier already paid for.
That is worth stating in the module that spends the calls, because the project's
tightest constraint is a DIFFERENT provider's quota, metered per market, used
for PLAYER props. Nothing here touches it.

DISPLAY ONLY. Nothing in the model reads `game_lines`, and adding a feature that
does needs a deliberate decision rather than an import: a game line is the
market's opinion about the same game the model is predicting, so feeding it in
would launder that opinion into our projection and make the resulting edge
partly a comparison of the book against itself.

FETCHED PER WEEK, NOT PER SEASON, AND THAT IS NOT A STYLE CHOICE. A whole season
is roughly 1,700 games, and this API truncates silently at 2,000 rows (see the
Phase 2 finding in `cache.py`). One season of one sport happens to fit today,
which is exactly the kind of margin that disappears without anything raising.
Per week is ~110 rows and can never approach it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cfbd

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.mapping import bigint_or_none, week_for_api
from worker.db import fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

# Completed weeks never change, so their responses are cached forever. The
# CURRENT week's lines move all week, which is why the job takes a max_age.
IMMUTABLE = None


@dataclass
class LineCounts:
    rows: int = 0
    games_seen: int = 0
    games_with_lines: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    #: RAW provider strings, before canonicalisation. Logged so a new book or a
    #: new spelling of an existing one is visible rather than silently merged.
    providers_seen: set[str] = field(default_factory=set)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _number(value: Any) -> float | None:
    """A spread or total, or None. Rejects the strings CFBD occasionally sends."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# CFBD spells the same book two ways. MEASURED on the 2025 season: 805 rows say
# "DraftKings" and 56 say "Draft Kings", and 56 GAMES carry BOTH — concentrated
# in weeks 15, 16 and 21, i.e. the postseason.
#
# WHY THIS IS NOT COSMETIC. The provider is half the conflict key, so the two
# spellings are two rows, and `v_game_line_consensus` takes a median across
# rows. On those 56 games DraftKings votes twice and pulls the consensus toward
# itself. Nothing about that is visible downstream: the card still shows a
# plausible spread, just one weighted 2:1 toward a single book.
#
# The lookup key ignores case and spaces; the OUTPUT is the canonical spelling.
# An unrecognised provider passes through unchanged rather than being dropped,
# so a book we have never seen still gets stored — and `providers_seen` below
# puts the raw names in the log, which is how the next alias gets noticed.
_CANONICAL_PROVIDERS = {
    "draftkings": "DraftKings",
    "espnbet": "ESPN Bet",
    "bovada": "Bovada",
}


def canonical_provider(raw: Any) -> str | None:
    """Canonical book name, or None when there is nothing usable."""
    name = str(raw).strip() if raw is not None else ""
    if not name:
        return None
    return _CANONICAL_PROVIDERS.get(name.casefold().replace(" ", ""), name)


def ingest_game_lines(
    client: CfbdClient,
    season: int,
    *,
    max_age: float | None = IMMUTABLE,
    weeks: list[int] | None = None,
) -> LineCounts:
    """Upsert every provider's line for each game in a season.

    One row per game per provider. Providers disagree — on 2025 week 8 Bovada
    had a game at -11 where two others had -10.5, and its OPENING number was -8
    against DraftKings' -10.5 — so all of them are kept and
    `v_game_line_consensus` takes the median. Storing a single "the" line would
    make the board's number depend on which book happened to be present.
    """
    counts = LineCounts()

    game_ids = {
        r["cfbd_id"]: r["id"]
        for r in fetch_all("select id, cfbd_id from games where season = %s", (season,))
    }
    if not game_ids:
        log.warning("lines %d: no games ingested for this season, nothing to match", season)
        return counts

    slices = fetch_all(
        """
        select distinct season_type::text as season_type, week
          from games where season = %s order by season_type, week
        """,
        (season,),
    )
    if weeks is not None:
        wanted = set(weeks)
        slices = [s for s in slices if s["week"] in wanted]

    payload: list[dict[str, Any]] = []
    for s in slices:
        # `games.week` carries postseason weeks offset onto a monotone season
        # axis so nothing can compare a bowl to a September game; the endpoint
        # wants its own numbering back. Getting this wrong is what stored bowl
        # games as week 1 in Phase 4.
        rows = client.fetch(
            "/lines",
            cfbd.BettingApi,
            "get_lines",
            year=season,
            week=week_for_api(s["week"], s["season_type"]),
            season_type=s["season_type"],
            max_age=max_age,
        )

        for r in rows:
            counts.games_seen += 1
            game_id = game_ids.get(bigint_or_none(r.get("id")))
            if game_id is None:
                # Expected and not a fault: /lines covers games we do not
                # ingest, e.g. an FBS team hosting an FCS opponent.
                counts.skip("unknown game")
                continue

            lines = r.get("lines") or []
            if not lines:
                counts.skip("game carried no line")
                continue
            counts.games_with_lines += 1

            for ln in lines:
                provider = canonical_provider(ln.get("provider"))
                if not provider:
                    counts.skip("line with no provider")
                    continue
                counts.providers_seen.add(str(ln.get("provider")))

                payload.append(
                    {
                        "game_id": game_id,
                        "provider": provider,
                        # Home-team perspective; negative means home favoured.
                        # Verified against `formattedSpread` across the whole
                        # 2025 week-8 slate, 228 of 228 agreeing.
                        "spread": _number(ln.get("spread")),
                        "spread_open": _number(ln.get("spreadOpen")),
                        "formatted_spread": ln.get("formattedSpread"),
                        "over_under": _number(ln.get("overUnder")),
                        "over_under_open": _number(ln.get("overUnderOpen")),
                        "home_moneyline": ln.get("homeMoneyline"),
                        "away_moneyline": ln.get("awayMoneyline"),
                    }
                )

    # Deduplicate before writing: a single upsert batch cannot contain the same
    # conflict key twice ("ON CONFLICT DO UPDATE command cannot affect row a
    # second time"), and a provider appearing twice for one game is the
    # provider's business, not an error worth failing the run over.
    #
    # This is also where the two DraftKings spellings collapse, now that both
    # canonicalise to one name. Last one wins; they are the same book quoting
    # the same game, so the values agree or differ by a refresh.
    deduped = {(row["game_id"], row["provider"]): row for row in payload}

    n = upsert(
        "game_lines",
        list(deduped.values()),
        conflict_columns=["game_id", "provider"],
    )
    counts.rows = n

    log.info(
        "lines %d: %d rows from %d games (%d carried a line); providers %s; skipped %s",
        season,
        n,
        counts.games_seen,
        counts.games_with_lines,
        sorted(counts.providers_seen) or "none",
        counts.skipped or "nothing",
    )
    return counts


def estimate_calls(season: int) -> int:
    """One call per week of the season."""
    rows = fetch_all(
        "select count(distinct (season_type, week)) as n from games where season = %s",
        (season,),
    )
    return int(rows[0]["n"]) if rows else 0
