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
    band_replication,
    by_market,
    confidence_bands,
    edge_thresholds,
    fixed_side,
    gap_sign_flips,
    grade_bet,
    median_edge,
    over_base_rate,
    side_lift,
    summarise,
    week_results,
)
from worker.db import fetch_all, pipeline_run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "grade_vs_book"

# The client's pitcher model uses 5% (CLAUDE.md §6). 0 is included because
# "every bet the model would take" is the honest denominator: reporting only
# the filtered subset invites picking whichever threshold looked best.
DEFAULT_THRESHOLDS = (0.0, 0.02, 0.05, 0.10)


def load_gradeable(
    season: int, week: int, adapter: str, *, closing_only: bool = True
) -> list[dict[str, Any]]:
    """Every (projection, real two-way line, actual) triple for one week.

    Grouped in SQL down to one row per (player, game, market, line) with the
    per-book prices aggregated, because that tuple IS the bet — two books
    quoting the same player at the same number are two prices on one wager, not
    two wagers.

    `closing_only=False` grades the LAST PRE-KICKOFF SNAPSHOT instead of a true
    closing line — see `--include-non-closing` in `main` for what that costs and
    why the column is not simply flipped.
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
               and (%(closing_only)s is false or l.is_closing)
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
        {
            "season": season,
            "week": week,
            "adapter": adapter,
            "closing_only": closing_only,
        },
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


def render_replication(
    bets: list[BookBet], thresholds: tuple[float, ...]
) -> list[str]:
    """Every headline again, one week at a time, and a shout when they differ.

    Nothing here is a new statistic. It is the same ROI, the same blind-under
    benchmark and the same reliability bands the sections above print, refused
    permission to pool. See `core/book_grading.py` for what pooling cost on
    2026-08-10: a dispersion "defect" that lived entirely in week 8, and a
    model-beats-blind-under result that changes sign between the two weeks.
    """
    if len({b.week for b in bets}) < 2:
        return []

    shown = sorted({thresholds[0], 0.05})
    results = week_results(bets, shown)

    lines = [
        "",
        "  DOES IT REPLICATE? — pooling weeks can manufacture an effect "
        "neither week has",
        f"    {'week':>4} {'n':>6} {'over%':>7} {'at edge':>8} "
        f"{'MODEL':>8} {'blind under':>12} {'model - blind':>14}",
    ]
    for r in results:
        base = f"{r.over_base:.1%}" if r.over_base is not None else "—"
        model = f"{r.model_roi:+.1%}" if r.model_roi is not None else "—"
        blind = f"{r.blind_under_roi:+.1%}" if r.blind_under_roi is not None else "—"
        gap = f"{r.gap:+.1%}" if r.gap is not None else "—"
        lines.append(
            f"    {r.week:>4} {r.n:>6,} {base:>7} {r.threshold:>7.0%} "
            f"{model:>8} {blind:>12} {gap:>14}"
        )

    for threshold in shown:
        if not gap_sign_flips(results, threshold):
            continue
        detail = ", ".join(
            f"wk{r.week} {r.gap:+.1%}"
            for r in results
            if r.threshold == threshold and r.gap is not None
        )
        lines.append(
            f"    ! at edge >= {threshold:.0%} the model beats blind under in "
            f"one week and loses in another ({detail}). The sign flips, so the "
            "pooled figure is an average of two different answers."
        )

    bands = band_replication(bets)
    if bands:
        weeks = sorted({w for b in bands for w in b.errors})
        header = "".join(f"{'wk' + str(w):>9}" for w in weeks)
        lines.append("")
        lines.append(f"    model error by band and week{'':<3}{header}")
        for band in bands:
            cells = "".join(
                f"{band.errors[w]:>+8.1%} " if w in band.errors else f"{'—':>9}"
                for w in weeks
            )
            note = ""
            if band.thin:
                ns = "/".join(str(band.counts[w]) for w in weeks if w in band.counts)
                note = f"  (thin: n={ns}, ignore)"
            elif band.disagrees:
                note = f"  ! spread {band.spread:.1%} — does NOT replicate"
            lines.append(f"    {band.lower:.2f}-{band.upper:.2f}{'':<17}{cells}{note}")

    lines.append(
        "    An effect present in one week and absent in another is a fact "
        "about that week. Fitting to it fits the slate, not the model."
    )
    return lines


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
        "  vs THE NULL HYPOTHESIS — does the model beat ignoring the model?"
    )
    base = over_base_rate(bets)
    if base is not None:
        shade = (
            "A market shade this size is the thing to beat."
            if abs(base - 0.5) > 0.03
            else "Close to centred."
        )
        lines.append(
            f"    base rate: OVER landed {base:.1%} of decided lines. {shade}"
        )
    for threshold in (thresholds[0], 0.05):
        model = summarise(bets, threshold)
        if not model.decided or model.roi_median is None:
            continue
        lines.append(f"    at edge >= {threshold:.0%}:")
        lines.append(f"      MODEL        ROI {model.roi_median:>+7.1%}")
        for side in ("over", "under"):
            mark = fixed_side(bets, side, threshold)  # type: ignore[arg-type]
            if mark.roi_median is None:
                continue
            gap = model.roi_median - mark.roi_median
            lines.append(
                f"      {mark.label:<12} ROI {mark.roi_median:>+7.1%}"
                f"   (model {gap:+.1%} vs this)"
            )
    lines.append(
        "    If a blind side beats the model, the model's return is that "
        "market shade, not its player selection."
    )

    lines.append("")
    lines.append(
        "  PLAYER-LEVEL SKILL — hit rate above the base rate for the same side"
    )
    for lift in side_lift(bets, thresholds[0]):
        lines.append(
            f"    called {lift.side:<6} n={lift.n:>5}  win {lift.win_rate:.1%} "
            f"vs base {lift.base_rate:.1%}  lift {lift.lift:+.1%}  "
            f"breakeven {lift.breakeven:.1%}  "
            f"{'CLEARS THE VIG' if lift.clears_vig else 'does not clear the vig'}"
        )
    lines.append(
        "    Positive lift is real discrimination between players; it only "
        "becomes money once it clears the vig."
    )

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

    lines.extend(render_replication(bets, thresholds))

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
    *,
    season: int,
    weeks: list[int],
    adapter: str,
    thresholds: tuple[float, ...],
    closing_only: bool = True,
) -> list[BookBet]:
    bets: list[BookBet] = []
    for week in weeks:
        rows = load_gradeable(season, week, adapter, closing_only=closing_only)
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
        "--include-non-closing", action="store_true",
        help="Also grade lines that are not flagged closing — the last "
             "pre-kickoff snapshot `ingest_odds` captured. Weaker evidence "
             "than a closing line, and labelled as such in the output.",
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
                closing_only=not args.include_non_closing,
            )
            # THE BASIS IS PART OF THE RESULT. A number from a pre-kickoff
            # snapshot and a number from a closing line are different claims,
            # and the second is the one this project has been waiting on (see
            # `docs/runbook.md`). Printing both under the same words is how the
            # weaker one ends up quoted as the stronger.
            basis = (
                "LAST PRE-KICKOFF lines (NOT closing, weaker evidence)"
                if args.include_non_closing
                else "closing lines"
            )
            log.info(
                "Model vs %s %s, %s week(s) %s:\n%s",
                args.adapter, basis, args.season, weeks,
                render(bets, thresholds),
            )
            if args.include_non_closing:
                log.warning(
                    "Graded against lines captured before kickoff, not at "
                    "close. A book moves its number right up to kickoff, so "
                    "this scores the model against a price that was still "
                    "available rather than against the market's final word. "
                    "Indicative only; never quote it as the closing-line "
                    "result."
                )
    except Exception as exc:
        log.error("Grading failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
