"""The game model, live: fair lines for the site and frozen shadow picks.

    python -m worker.jobs.run_game_model
    python -m worker.jobs.run_game_model --dry-run     # fit and print, write nothing

G4 of the game model (CLAUDE.md §11). Each run:

  1. fits the RATINGS model (the G3 winner; boosting lost everywhere) on every
     completed game from `FIRST_TRAINING_SEASON` to now;
  2. projects every game of the current season that has not kicked off and
     whose week's team strength exists, and writes `game_projections` — the
     labelled fair line the site shows, with no call attached;
  3. prices each projected game's full-game moneyline, spread and total
     against one book's CURRENT captured price (core/game_picks.py) and writes
     a `game_picks` row where the edge clears 5%. Those rows are the SHADOW
     TEST: frozen at kickoff by the database, readable by no visitor
     (migration 0078), graded by `grade_game_picks`.

THE MARKET STILL NEVER FEEDS THE MODEL. Prices are read in step 3, after the
projection is fixed, to decide whether it disagrees with a book by enough to
record. Nothing in steps 1-2 reads `game_odds` or `game_lines`.

A GAME WHOSE WEEK HAS NO TEAM STRENGTH IS NOT PROJECTED. Next week's rows
appear only when this week's games are in (`build_team_strength` runs daily
after the results ingest). Projecting before then would read every team as
having played no games, a projection from last season alone presented as a
current one.

PICKS NEED FRESH PRICES. The capture writes only on change, so an old row is
indistinguishable from an unchanged price, and a book that pulled a game
leaves its last price behind. If the last successful capture is older than
`STALE_ODDS_HOURS`, the run writes projections and no picks.

Costs no API calls.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from worker.config import ConfigError, get_settings
from worker.core.game_data import load_games
from worker.core.game_model import TARGETS, RatingsModel, evidence_phase
from worker.core.game_picks import Pick, Quote, choose_quote, evaluate, is_new
from worker.db import connect, fetch_all, get_config_value, pipeline_run, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "run_game_model"
MODEL_VERSION = "ratings-v1"
FIRST_TRAINING_SEASON = 2022
STALE_ODDS_HOURS = 36.0
# A pick written in the last minutes before kickoff races the freeze trigger;
# a game this close is skipped rather than failing the whole batch.
KICKOFF_MARGIN = timedelta(minutes=5)
PERIOD = "full"
PICK_MARKETS = ("h2h", "spreads", "totals")


@dataclass
class ModelRunReport:
    trained_games: int = 0
    shrink_k: float | None = None
    upcoming: int = 0
    unprojectable: int = 0
    projected: int = 0
    odds_age_hours: float | None = None
    priced: int = 0
    picks: list[Pick] = field(default_factory=list)
    picks_written: int = 0
    picks_standing: int = 0

    def render(self) -> str:
        age = "none" if self.odds_age_hours is None else f"{self.odds_age_hours:.1f}h"
        by_market = {m: sum(p.market == m for p in self.picks) for m in PICK_MARKETS}
        return "\n".join([
            f"trained on        {self.trained_games} games (k = {self.shrink_k})",
            f"upcoming games    {self.upcoming}",
            f"  no strength yet {self.unprojectable}",
            f"  projected       {self.projected}",
            f"last odds capture {age} ago",
            f"games with prices {self.priced}",
            "picks at edge     "
            + ", ".join(f"{m} {n}" for m, n in by_market.items()),
            f"  new rows        {self.picks_written}",
            f"  already standing {self.picks_standing}",
        ])


def _float(df: pl.DataFrame, c: str) -> np.ndarray:
    return df[c].cast(pl.Float64).fill_null(np.nan).to_numpy()


def projectable() -> pl.Expr:
    """True where the game's week has team strength, or it is week 1 (nothing to have)."""
    return pl.col("league_points").is_not_null() | (pl.col("week") <= 1)


def project(model: RatingsModel, upcoming: pl.DataFrame) -> tuple[list[dict], dict]:
    """game_projections rows, plus the full-game samples picks are priced from."""
    means, lo, hi, samples = {}, {}, {}, {}
    for target in TARGETS:
        s = model.outcome_samples(upcoming, target)
        samples[target] = s
        means[target] = model.predict_mean(upcoming, target)
        lo[target] = np.quantile(s, 0.10, axis=1)
        hi[target] = np.quantile(s, 0.90, axis=1)

    margin = samples["margin"]
    # No ties in college football: split the simulated zero mass evenly.
    p_win = (margin > 0).mean(axis=1) + (margin == 0).mean(axis=1) / 2
    p_win = np.clip(p_win, 1e-5, 1 - 1e-5)
    phase = evidence_phase(_float(upcoming, "h_games"), _float(upcoming, "a_games"))

    rows = []
    for i, game_id in enumerate(upcoming["game_id"].to_list()):
        row = {
            "game_id": int(game_id),
            "model_version": MODEL_VERSION,
            "evidence_phase": str(phase[i]),
            "p_home_win": round(float(p_win[i]), 5),
            "made_at": datetime.now(UTC),
        }
        for target in TARGETS:
            row[f"{target}_mean"] = round(float(means[target][i]), 1)
            row[f"{target}_p10"] = round(float(lo[target][i]), 1)
            row[f"{target}_p90"] = round(float(hi[target][i]), 1)
        rows.append(row)
    return rows, {"margin": margin, "total": samples["total"], "p_home_win": p_win}


def last_capture_age_hours(now: datetime) -> float | None:
    rows = fetch_all(
        """
        select max(finished_at) as at from pipeline_runs
         where job_name = 'ingest_game_odds' and status = 'succeeded'
        """
    )
    at = rows[0]["at"] if rows else None
    return None if at is None else (now - at).total_seconds() / 3600.0


def load_quotes(game_ids: list[int]) -> dict[int, list[Quote]]:
    rows = fetch_all(
        """
        select distinct on (o.game_id, o.sportsbook_id, o.market)
               o.game_id, o.sportsbook_id, sb.key, o.market, o.line,
               o.home_price, o.away_price, o.over_price, o.under_price
          from game_odds o
          join sportsbooks sb on sb.id = o.sportsbook_id
         where o.game_id = any(%s) and o.period = %s
         order by o.game_id, o.sportsbook_id, o.market, o.captured_at desc
        """,
        (game_ids, PERIOD),
    )
    out: dict[int, list[Quote]] = {}
    for r in rows:
        out.setdefault(int(r["game_id"]), []).append(
            Quote(
                sportsbook_id=int(r["sportsbook_id"]),
                sportsbook_key=r["key"],
                market=r["market"],
                line=None if r["line"] is None else float(r["line"]),
                home_price=r["home_price"],
                away_price=r["away_price"],
                over_price=r["over_price"],
                under_price=r["under_price"],
            )
        )
    return out


def standing_sides(game_ids: list[int]) -> dict[tuple[int, str], str]:
    rows = fetch_all(
        """
        select distinct on (game_id, market) game_id, market, side
          from game_picks
         where game_id = any(%s) and period = %s
         order by game_id, market, made_at desc, id desc
        """,
        (game_ids, PERIOD),
    )
    return {(int(r["game_id"]), r["market"]): r["side"] for r in rows}


def write(projections: list[dict], picks: list[Pick]) -> int:
    """Both writes in ONE transaction: a pick never lands without its projection."""
    columns = list(projections[0]) if projections else []
    updates = [c for c in columns if c not in ("game_id", "model_version")]
    with connect() as conn, conn.cursor() as cur:
        if projections:
            cur.executemany(
                f"""
                insert into game_projections ({", ".join(columns)})
                values ({", ".join(f"%({c})s" for c in columns)})
                on conflict (game_id, model_version) do update set
                  {", ".join(f"{c} = excluded.{c}" for c in updates)}
                """,
                projections,
            )
        if picks:
            cur.executemany(
                """
                insert into game_picks
                  (game_id, period, market, side, line, price, sportsbook_id,
                   model_prob, model_version)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (p.game_id, PERIOD, p.market, p.side, p.line, p.price,
                     p.sportsbook_id, round(p.model_prob, 5), MODEL_VERSION)
                    for p in picks
                ],
            )
        conn.commit()
    return len(projections) + len(picks)


def run(*, season: int, sport: str = "cfb", dry_run: bool = False,
        now: datetime | None = None) -> ModelRunReport:
    report = ModelRunReport()
    now = now or datetime.now(UTC)

    games = load_games(list(range(FIRST_TRAINING_SEASON, season + 1)), sport)
    train = games.filter(pl.col("completed"))
    report.trained_games = train.height
    model = RatingsModel().fit(train)
    report.shrink_k = model.k

    upcoming = games.filter(
        (pl.col("season") == season)
        & ~pl.col("completed")
        & pl.col("start_date").is_not_null()
        & (pl.col("start_date") > now + KICKOFF_MARGIN)
    )
    report.upcoming = upcoming.height
    ready = upcoming.filter(projectable())
    report.unprojectable = upcoming.height - ready.height
    report.projected = ready.height
    if ready.is_empty():
        return report

    projections, dist = project(model, ready)
    game_ids = [int(g) for g in ready["game_id"].to_list()]

    report.odds_age_hours = last_capture_age_hours(now)
    fresh = report.odds_age_hours is not None and report.odds_age_hours <= STALE_ODDS_HOURS
    new_picks: list[Pick] = []
    if not fresh:
        log.warning(
            "Last successful game-odds capture was %s; writing projections and NO picks.",
            "never" if report.odds_age_hours is None else f"{report.odds_age_hours:.1f}h ago",
        )
    else:
        quotes = load_quotes(game_ids)
        report.priced = len(quotes)
        standing = standing_sides(game_ids)
        for i, game_id in enumerate(game_ids):
            for market in PICK_MARKETS:
                quote = choose_quote(quotes.get(game_id, []), market)
                if quote is None:
                    continue
                pick = evaluate(
                    game_id, quote, dist["margin"][i], dist["total"][i],
                    float(dist["p_home_win"][i]),
                )
                if pick is None:
                    continue
                report.picks.append(pick)
                if is_new(pick, standing.get((game_id, market))):
                    new_picks.append(pick)
                else:
                    report.picks_standing += 1

    if not dry_run:
        write(projections, new_picks)
    report.picks_written = len(new_picks)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", default="cfb", choices=("cfb",))
    parser.add_argument("--season", type=int, help="default: app_config.current_season")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    season = args.season or get_config_value("current_season")
    if season is None:
        log.error("app_config.current_season is unset and no --season given.")
        return 2
    season = int(season)

    try:
        if args.dry_run:
            report = run(season=season, sport=args.sport, dry_run=True)
        else:
            with pipeline_run(
                JOB_NAME, metadata={"season": season, "sport": args.sport,
                                    "model_version": MODEL_VERSION}
            ) as run_id:
                report = run(season=season, sport=args.sport)
                set_rows_written(run_id, report.projected + report.picks_written)
    except Exception as exc:
        log.error("Game model run failed: %s", exc, exc_info=True)
        return 1
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
