"""Grade the model against the real closing lines `backfill_odds` bought.

    python -m worker.jobs.grade_vs_book --season 2025 --weeks 8
    python -m worker.jobs.grade_vs_book --season 2025 --weeks 6-8 --threshold 0.05

THE QUESTION THIS ANSWERS, WHICH NOTHING ELSE DOES. The calibration report says
the model is well calibrated: a stated 60% happens about 60% of the time. It was
measured against SYNTHETIC lines — each player's own trailing average — because
no real ones existed. Beating a trailing average is a far easier test than
beating a price a bookmaker set, and every edge % the board displays is a claim
about the second thing.

So: calibrated and profitable are different claims, this job tests the second,
and a negative answer here is a real finding rather than a failure of the run.
Report it either way.

WHAT IT DOES NOT DO. It does not re-run the model. It reads `projections` rows
that already exist for the week, joins them to `player_prop_lines` rows written
by `backfill_odds`, and settles them against `player_game_stats`. Nothing here
can change a projection, which is the point: the projection was made before
kickoff and this is scoring it after.

POINT-IN-TIME. Only projections whose `as_of_week` equals the week being graded
are used. A projection carrying a later cutoff knows results it is being asked
to predict, and it would score beautifully.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from worker.config import ConfigError, get_settings
from worker.core.book_grading import (
    BookBet,
    BookPriceRow,
    by_market,
    confidence_bands,
    edge_thresholds,
    grade_bet,
    median_edge,
    summarise,
)
from worker.db import fetch_all, pipeline_run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "grade_vs_book"

# The client's pitcher model uses 5% (CLAUDE.md §6). 0 is included because
# "every bet the model would take" is the honest denominator: reporting only
# the filtered subset invites picking whichever threshold looked best.
DEFAULT_THRESHOLDS = (0.0, 0.02, 0.05, 0.10)


def load_gradeable(season: int, week: int, adapter: str) -> list[dict[str, Any]]:
    """Every (projection, real two-way line, actual) triple for one week.

    Grouped in SQL down to one row per (player, game, market, line) with the
    per-book prices aggregated, because that tuple IS the bet — two books
    quoting the same player at the same number are two prices on one wager, not
    two wagers.
    """
    return fetch_all(
        """
        with priced as (
            select l.player_id, l.game_id, l.market_key, l.line,
                   json_agg(json_build_object(
                       'sportsbook_key', b.key,
                       'over_price',  l.over_price,
                       'under_price', l.under_price
                   )) as prices
              from player_prop_lines l
              join sportsbooks b on b.id = l.sportsbook_id
             where l.season = %(season)s and l.week = %(week)s
               and l.source_adapter = %(adapter)s
               and l.is_closing
             group by l.player_id, l.game_id, l.market_key, l.line
        )
        select pr.player_id, pr.game_id, pr.market_key, pr.line, pr.prices,
               p.distribution, p.params, p.as_of_week,
               -- Cast before defaulting: position_group is an ENUM, so a
               -- string fallback has to leave the enum's domain first.
               coalesce(pts.position_group::text, 'UNK') as position_group,
               m.stat_column,
               s.pass_yards, s.pass_tds, s.pass_attempts, s.pass_completions,
               s.rush_yards, s.rush_attempts, s.receptions, s.rec_yards,
               s.offensive_tds
          from priced pr
          join projections p
            on p.player_id = pr.player_id and p.game_id = pr.game_id
           and p.market_key = pr.market_key
           -- POINT-IN-TIME: the projection must not be from a later cutoff.
           and p.as_of_week = %(week)s
          join markets m on m.key = pr.market_key
          -- An INNER join on the box score on purpose. A player with no row did
          -- not record a stat line, and scoring a missing row as zero would
          -- make every inactive player an UNDER hit.
          join player_game_stats s
            on s.player_id = pr.player_id and s.game_id = pr.game_id
          left join player_team_seasons pts
            on pts.player_id = pr.player_id and pts.season = %(season)s
        """,
        {"season": season, "week": week, "adapter": adapter},
    )


def to_bets(rows: list[dict[str, Any]], season: int, week: int) -> list[BookBet]:
    """Grade each row, dropping the ones that are not really gradeable."""
    bets: list[BookBet] = []
    skipped_one_sided = 0

    for row in rows:
        prices = [
            BookPriceRow(
                sportsbook_key=str(p.get("sportsbook_key") or ""),
                over_price=p.get("over_price"),
                under_price=p.get("under_price"),
            )
            for p in (row.get("prices") or [])
        ]
        actual = row.get(row["stat_column"])
        bet = grade_bet(
            player_id=int(row["player_id"]),
            game_id=int(row["game_id"]),
            market_key=str(row["market_key"]),
            position_group=str(row["position_group"]),
            season=season,
            week=week,
            line=float(row["line"]),
            distribution=str(row["distribution"]),
            params=row["params"],
            prices=prices,
            actual_value=None if actual is None else float(actual),
        )
        if bet is None:
            skipped_one_sided += 1
            continue
        bets.append(bet)

    if skipped_one_sided:
        log.info(
            "%d row(s) not gradeable (one-sided price or no box-score row)",
            skipped_one_sided,
        )
    return bets


def render(bets: list[BookBet], thresholds: tuple[float, ...]) -> str:
    """The report. Every win rate carries its break-even, deliberately."""
    if not bets:
        return (
            "  NO GRADEABLE BETS. This is UNRESOLVED, not a verdict: it means "
            "no week has both real two-way closing lines and projections at a "
            "matching as_of_week. Run backfill_odds first."
        )

    lines = [
        f"  {len(bets):,} gradeable bets, "
        f"{len({b.player_id for b in bets}):,} players, "
        f"{len({b.game_id for b in bets}):,} games",
        f"  median edge: {median_edge(bets):+.1%}",
        "",
        "  BY EDGE THRESHOLD — win rate is meaningless without its break-even",
    ]
    for result in edge_thresholds(bets, thresholds):
        lines.append("    " + result.summary())

    headline = summarise(bets, thresholds[0])
    lines.append("")
    lines.append(f"  BY MARKET at edge >= {thresholds[0]:.0%}")
    for market, result in sorted(by_market(bets, thresholds[0]).items()):
        lines.append(f"    {market:<15} {result.summary()}")

    lines.append("")
    lines.append(
        "  CONFIDENCE vs REALITY vs THE BOOK — is the model overconfident, or "
        "just not better priced?"
    )
    lines.append(
        f"    {'band':<12} {'n':>5}  {'model':>7} {'book':>7} {'actual':>7}  "
        f"{'model err':>9} {'book err':>9}"
    )
    for band in confidence_bands(bets):
        lines.append(
            f"    {band.lower:.2f}-{band.upper:.2f}  {band.n:>5}  "
            f"{band.mean_model:>6.1%} {band.mean_book:>6.1%} "
            f"{band.observed:>6.1%}  {band.model_error:>+8.1%} "
            f"{band.book_error:>+8.1%}"
        )
    lines.append(
        "    A POSITIVE model error is overconfidence: it claimed more than it "
        "delivered. Compare it against the book's error on the same rows."
    )

    lines.append("")
    lines.append("  READ THIS BEFORE QUOTING ANY OF IT")
    lines.append(
        "    * Correlated observations. A player appears at several lines and "
        "several markets, all settled by one performance, so the effective "
        "sample is smaller than n."
    )
    lines.append(
        "    * Closing lines are the HARDEST price to beat — they carry every "
        "bet the market made. Beating an opening line is easier and is not "
        "what this measures."
    )
    lines.append(
        "    * ROI at the median price is the headline. The best-price figure "
        "assumes shopping every book and always getting filled."
    )
    if headline.decided and headline.win_rate is not None:
        verdict = (
            "AHEAD of break-even"
            if headline.win_rate > (headline.breakeven or 1.0)
            else "BEHIND break-even"
        )
        lines.append(f"    * Overall the model is {verdict} on this sample.")
    return "\n".join(lines)


def run(
    *, season: int, weeks: list[int], adapter: str, thresholds: tuple[float, ...]
) -> list[BookBet]:
    bets: list[BookBet] = []
    for week in weeks:
        rows = load_gradeable(season, week, adapter)
        got = to_bets(rows, season, week)
        log.info("%s week %s: %d gradeable bet(s)", season, week, len(got))
        bets.extend(got)
    return bets


def parse_weeks(raw: str) -> list[int]:
    weeks: list[int] = []
    for part in raw.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            weeks.extend(range(int(start), int(end) + 1))
        else:
            weeks.append(int(chunk))
    return sorted(set(weeks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--weeks", required=True, help="'8', '6,7,8' or '6-8'.")
    parser.add_argument(
        "--adapter", default="theoddsapi",
        help="Which source_adapter's lines to grade against. Never 'synthetic' "
             "— that is the thing this job exists to stop relying on.",
    )
    parser.add_argument(
        "--threshold", type=float, action="append",
        help="Edge threshold to report. Repeatable. Defaults to "
             f"{DEFAULT_THRESHOLDS}.",
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    if args.adapter == "synthetic":
        log.error(
            "Refusing to grade against 'synthetic' lines. Those are the "
            "player's own trailing average, which is what the calibration "
            "report already uses and what this job exists to move past."
        )
        return 2

    weeks = parse_weeks(args.weeks)
    if not weeks:
        log.error("No weeks parsed from %r.", args.weeks)
        return 2

    thresholds = tuple(args.threshold) if args.threshold else DEFAULT_THRESHOLDS

    try:
        with pipeline_run(
            JOB_NAME, metadata={"season": args.season, "weeks": weeks}
        ):
            bets = run(
                season=args.season,
                weeks=weeks,
                adapter=args.adapter,
                thresholds=thresholds,
            )
            log.info(
                "Model vs %s closing lines, %s week(s) %s:\n%s",
                args.adapter, args.season, weeks, render(bets, thresholds),
            )
    except Exception as exc:
        log.error("Grading failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
