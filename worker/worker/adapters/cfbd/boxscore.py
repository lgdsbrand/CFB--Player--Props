"""Parsing CFBD box scores into flat player-game stat rows.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3).

`/games/players` returns a four-level nesting — game -> teams -> categories ->
types -> athletes — with every value a STRING, including composites like
"19/30" for completions/attempts. This module flattens that into the columns
`player_game_stats` holds.

This matters more than most parsing code: `player_game_stats` is the single home
for actuals, so every hit rate, every backtest grade and every calibration
number resolves against what comes out of here. A category name silently
unhandled would not raise — it would quietly zero out a market.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from worker.logging_setup import get_logger

log = get_logger(__name__)

# (category, type) -> column in player_game_stats.
#
# Type labels are ESPN-derived and terse. Values not listed here are ignored on
# purpose (AVG, LONG, QBR are derivable or not modelled), but anything
# unrecognised is counted and reported so a new label cannot silently vanish.
STAT_MAP: dict[tuple[str, str], str] = {
    ("passing", "YDS"): "pass_yards",
    ("passing", "TD"): "pass_tds",
    ("passing", "INT"): "interceptions",
    ("rushing", "CAR"): "rush_attempts",
    ("rushing", "YDS"): "rush_yards",
    ("rushing", "TD"): "rush_tds",
    ("receiving", "REC"): "receptions",
    ("receiving", "YDS"): "rec_yards",
    ("receiving", "TD"): "rec_tds",
}

# Deliberately ignored: derivable, cosmetic, or not modelled.
IGNORED_TYPES: frozenset[tuple[str, str]] = frozenset(
    {
        ("passing", "AVG"), ("passing", "QBR"), ("passing", "LONG"),
        ("rushing", "AVG"), ("rushing", "LONG"),
        ("receiving", "AVG"), ("receiving", "LONG"),
    }
)

# Categories with no player_game_stats columns. Defensive stats are not modelled
# (no defensive markets in CLAUDE.md §6); returns are tracked separately and
# deliberately excluded from offensive_tds.
IGNORED_CATEGORIES = frozenset(
    {"defensive", "kickReturns", "puntReturns", "punting", "kicking",
     "interceptions", "fumbles"}
)


def _to_number(raw: Any) -> float | None:
    """Parse a box-score value, which arrives as a string.

    Handles the negatives NCAA rushing produces (sacks are charged as rushing
    losses) and the commas long yardage totals sometimes carry.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(raw: Any) -> int | None:
    value = _to_number(raw)
    return int(value) if value is not None else None


def parse_completions_attempts(raw: Any) -> tuple[int | None, int | None]:
    """Split the "C/ATT" composite, e.g. "19/30" -> (19, 30)."""
    if raw is None:
        return None, None
    text = str(raw).strip()
    if "/" not in text:
        return None, None
    left, _, right = text.partition("/")
    return _to_int(left), _to_int(right)


class BoxScoreParser:
    """Flattens box-score payloads, tracking anything it does not recognise."""

    def __init__(self) -> None:
        self.unknown_types: Counter[str] = Counter()
        self.unknown_categories: Counter[str] = Counter()

    def parse_game(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Return one flat dict per athlete appearing in this game.

        Each carries `cfbd_athlete_id`, `team`, `home_away` and whichever stat
        columns the box score supplied. Resolving those to database ids is the
        caller's job, since it needs id maps this module should not know about.
        """
        out: dict[tuple[int, str], dict[str, Any]] = {}

        for team_block in payload.get("teams") or []:
            team_name = team_block.get("team")
            home_away = team_block.get("homeAway")

            for category in team_block.get("categories") or []:
                cat_name = category.get("name")
                if cat_name in IGNORED_CATEGORIES:
                    continue

                for type_block in category.get("types") or []:
                    type_name = type_block.get("name")
                    key = (cat_name, type_name)

                    is_cmp_att = key == ("passing", "C/ATT")
                    column = STAT_MAP.get(key)

                    if column is None and not is_cmp_att:
                        if key not in IGNORED_TYPES:
                            self.unknown_types[f"{cat_name}.{type_name}"] += 1
                        continue

                    for athlete in type_block.get("athletes") or []:
                        athlete_id = athlete.get("id")
                        if athlete_id is None:
                            continue
                        try:
                            athlete_id = int(athlete_id)
                        except (TypeError, ValueError):
                            continue

                        row_key = (athlete_id, team_name or "")
                        row = out.setdefault(
                            row_key,
                            {
                                "cfbd_athlete_id": athlete_id,
                                "athlete_name": athlete.get("name"),
                                "team": team_name,
                                "home_away": home_away,
                            },
                        )

                        if is_cmp_att:
                            comps, atts = parse_completions_attempts(
                                athlete.get("stat")
                            )
                            row["pass_completions"] = comps
                            row["pass_attempts"] = atts
                        else:
                            row[column] = _to_int(athlete.get("stat"))

        return list(out.values())

    def report(self) -> None:
        if self.unknown_categories:
            log.warning(
                "Box score categories not handled: %s",
                dict(self.unknown_categories.most_common(10)),
            )
        if self.unknown_types:
            log.info(
                "Box score stat types not mapped (ignored by design unless new): %s",
                dict(self.unknown_types.most_common(12)),
            )
