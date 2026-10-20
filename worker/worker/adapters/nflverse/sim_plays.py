"""nflverse play-by-play turned into what the game simulator learns from (phase S1).

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The simulator (`core/play_sim.py`) is a
football engine that knows nothing about nflverse: it learns from a handful of
plain tables whose columns `core/play_sim.py` defines (`SimEvents`). This file
reads nflverse's public play-by-play and writes those tables. A college
version would read CFBD's plays into the same tables.

WHY NOT THE `plays` TABLE. It holds offensive snaps only (the N4 adapter keeps
plays with a skill player), so it has no punts, kickoffs, field goals or extra
points, which a game simulation cannot do without. nflverse's file has every
play, and costs nothing: a static file per season on a CDN, cached here.

HOW EACH PLAY IS READ. Every outcome is taken from what happened NEXT, not
from the play's own summary columns, because the next snap is the truth about
field position, downs and possession after penalties, laterals and replays:

  * a run or pass that the same team snaps after: yards advanced = its
    yardline before minus the next snap's (`dy`), first down = the next snap
    is a 1st down (`fd`);
  * a 4th down the other team snaps after: the same `dy`, measured into the
    new offense's frame, and the simulator turns the failed 4th into the
    change of possession itself;
  * a turnover or punt: where the other team's next snap is, relative to the
    old line of scrimmage seen from their side (`delta`);
  * the clock: seconds until the next snap in the same quarter, or to the end
    of the quarter when the play ended it.

Scores are taken BEFORE each play (`posteam_score`, `defteam_score`), as the
live backtest does (core/live_backtest.py header).
"""

from __future__ import annotations

import shutil
import time
import urllib.request
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import polars as pl

from worker.adapters.nflverse.assets import RELEASE_BASE
from worker.adapters.nflverse.client import DEFAULT_CACHE_DIR
from worker.core.play_sim import (
    KICK_ONSIDE,
    KICK_RECEIVED,
    KICK_RETURN_TD,
    KICK_TEAM_TD,
    KIND_CONT,
    KIND_SAFETY,
    KIND_TD_DEF,
    KIND_TD_OFF,
    KIND_TURNOVER,
    SimEvents,
    clock_state,
)
from worker.logging_setup import get_logger

log = get_logger(__name__)

COLUMNS = [
    "game_id",
    "play_id",
    "season",
    "season_type",
    "week",
    "home_team",
    "away_team",
    "posteam",
    "defteam",
    "qtr",
    "quarter_seconds_remaining",
    "half_seconds_remaining",
    "game_seconds_remaining",
    "down",
    "ydstogo",
    "yardline_100",
    "play_type",
    "yards_gained",
    "touchdown",
    "td_team",
    "interception",
    "fumble_lost",
    "safety",
    "pass",
    "rush",
    "epa",
    "field_goal_result",
    "kick_distance",
    "extra_point_result",
    "two_point_attempt",
    "two_point_conv_result",
    "posteam_score",
    "defteam_score",
    "total_home_score",
    "total_away_score",
]

#: Rows that are a snap or a kick. Timeouts, quarter ends and the like are not.
REAL = (
    "run",
    "pass",
    "no_play",
    "punt",
    "field_goal",
    "kickoff",
    "extra_point",
    "qb_kneel",
    "qb_spike",
)
#: Rows a "next snap" can be: everything real except tries after a touchdown.
SNAP = ("run", "pass", "no_play", "punt", "field_goal", "kickoff", "qb_kneel", "qb_spike")
SCRIMMAGE = ("run", "pass", "no_play", "punt", "field_goal", "qb_kneel", "qb_spike")

#: A completed season never changes; the one being played does, every week.
CURRENT_SEASON_MAX_AGE = 12 * 3600

#: Pre-snap penalties carry no run/pass flag. They are dealt to the run and
#: pass pools by a fixed hash of the play id in this ratio (2025: 57% of
#: flagged plays are passes), so the penalty rate per pool stays the real one
#: rather than doubling by putting each in both.
PRESNAP_PASS_SHARE = 0.57


def pbp(
    season: int, *, cache_dir: Path = DEFAULT_CACHE_DIR, max_age: float | None = None
) -> pl.DataFrame:
    """One season of nflverse play-by-play (parquet), cached on disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"pbp_{season}.parquet"
    fresh = path.exists() and (max_age is None or time.time() - path.stat().st_mtime < max_age)
    if not fresh:
        url = f"{RELEASE_BASE}/pbp/play_by_play_{season}.parquet"
        log.info("Downloading %s", url)
        partial = path.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=300) as r, partial.open("wb") as fh:
            shutil.copyfileobj(r, fh)
        partial.replace(path)
    frame = pl.read_parquet(path, columns=COLUMNS)
    return frame.sort("game_id", "play_id")


def load_seasons(seasons: Iterable[int], current: int | None = None) -> pl.DataFrame:
    frames = [
        pbp(s, max_age=CURRENT_SEASON_MAX_AGE if s == current else None) for s in sorted(seasons)
    ]
    return pl.concat(frames, how="vertical_relaxed")


def events_by_season(
    seasons: Iterable[int], current: int | None = None
) -> tuple[SimEvents, pl.DataFrame]:
    """`sim_events` and `period_scores` for several seasons, one season in memory at a time.

    The same tables as `sim_events(load_seasons(...))`, built for the 512 MB
    Render instance: all five seasons at once peaked at ~900 MB (measured
    2026-10-20), one at a time well under half that. A play's next snap never
    crosses a season, so nothing is lost.
    """
    parts: list[SimEvents] = []
    scores: list[pl.DataFrame] = []
    for s in sorted(seasons):
        frame = pbp(s, max_age=CURRENT_SEASON_MAX_AGE if s == current else None)
        parts.append(sim_events(frame))
        scores.append(period_scores(frame.filter(pl.col("season_type") == "REG")))
        del frame
    names = ("decisions", "scrimmage", "punts", "field_goals", "kickoffs", "pats")
    joined = SimEvents(
        *(pl.concat([getattr(p, n) for p in parts], how="vertical_relaxed") for n in names)
    )
    return joined, pl.concat(scores, how="vertical_relaxed")


def _with_next(frame: pl.DataFrame) -> pl.DataFrame:
    """Each real row with the next snap's columns (same game) beside it."""
    real = frame.filter(pl.col("play_type").is_in(REAL)).with_columns(
        pl.col("two_point_attempt").fill_null(0)
    )
    snaps = (
        real.filter(pl.col("play_type").is_in(SNAP) & (pl.col("two_point_attempt") == 0))
        .select(
            "game_id",
            pl.col("play_id").alias("n_play_id"),
            pl.col("play_type").alias("n_type"),
            pl.col("posteam").alias("n_pos"),
            pl.col("qtr").alias("n_qtr"),
            pl.col("quarter_seconds_remaining").alias("n_qsr"),
            pl.col("down").alias("n_down"),
            pl.col("yardline_100").alias("n_ytg"),
        )
        .sort("game_id", "n_play_id")
    )
    # The first snap strictly after each row, in the same game.
    joined = real.sort("game_id", "play_id").join_asof(
        snaps,
        left_on="play_id",
        right_on="n_play_id",
        by="game_id",
        strategy="forward",
        allow_exact_matches=False,
    )
    secs = (
        pl.when(pl.col("n_qtr") == pl.col("qtr"))
        .then(pl.col("quarter_seconds_remaining") - pl.col("n_qsr"))
        .otherwise(pl.col("quarter_seconds_remaining"))
        .clip(0, 120)
        .fill_null(pl.col("quarter_seconds_remaining").clip(0, 120))
    )
    diff = (pl.col("posteam_score") - pl.col("defteam_score")).fill_null(0)
    return joined.with_columns(secs=secs, diff=diff)


def _clock(frame: pl.DataFrame) -> np.ndarray:
    return clock_state(
        frame["qtr"].to_numpy(),
        frame["half_seconds_remaining"].fill_null(900).to_numpy(),
        frame["game_seconds_remaining"].fill_null(900).to_numpy(),
        frame["diff"].to_numpy(),
    )


def _presnap_is_pass(play_id: np.ndarray) -> np.ndarray:
    h = (play_id.astype(np.int64) * 2654435761) % 1000
    return h < int(PRESNAP_PASS_SHARE * 1000)


def sim_events(frame: pl.DataFrame) -> SimEvents:
    """The simulator's training tables from regular-season play-by-play.

    Regulation only for decisions and outcomes: overtime is rare and played
    under rules that changed in 2022 and 2025, and the simulator plays it with
    the same play model.
    """
    reg = frame.filter((pl.col("season_type") == "REG") & (pl.col("qtr") <= 4))
    rows = _with_next(reg)
    rows = rows.with_columns(clk=pl.Series(_clock(rows)))

    # ---- decisions: what the offense chose on a down --------------------------
    dec = rows.filter(
        pl.col("play_type").is_in(SCRIMMAGE)
        & (pl.col("two_point_attempt") == 0)
        & pl.col("down").is_not_null()
        & pl.col("yardline_100").is_not_null()
    )
    label = (
        pl.when(pl.col("play_type") == "punt")
        .then(pl.lit("punt"))
        .when(pl.col("play_type") == "field_goal")
        .then(pl.lit("fg"))
        .when(pl.col("play_type") == "qb_kneel")
        .then(pl.lit("kneel"))
        .when(pl.col("play_type") == "qb_spike")
        .then(pl.lit("spike"))
        .when((pl.col("play_type") == "pass") | (pl.col("pass") == 1))
        .then(pl.lit("pass"))
        .when((pl.col("play_type") == "run") | (pl.col("rush") == 1))
        .then(pl.lit("run"))
        .otherwise(pl.lit(None))
    )
    dec = dec.with_columns(label=label)
    decisions = dec.filter(pl.col("label").is_not_null()).select(
        pl.col("down").cast(pl.Int8),
        pl.col("ydstogo").cast(pl.Float64).alias("togo"),
        pl.col("yardline_100").cast(pl.Float64).alias("ytg"),
        pl.col("half_seconds_remaining").cast(pl.Float64).alias("half_secs"),
        pl.col("game_seconds_remaining").cast(pl.Float64).alias("game_secs"),
        pl.col("qtr").cast(pl.Int8),
        pl.col("diff").cast(pl.Float64),
        "label",
    )

    # ---- run / pass / kneel / spike outcomes ---------------------------------
    scr = dec.filter(
        pl.col("label").is_in(["run", "pass", "kneel", "spike"]) | pl.col("label").is_null()
    )
    presnap = scr["label"].is_null().to_numpy()
    cls = scr["label"].to_numpy().astype(object)
    cls[presnap] = np.where(_presnap_is_pass(scr["play_id"].to_numpy()[presnap]), "pass", "run")
    scrimmage = _scrimmage_outcomes(scr.with_columns(cls=pl.Series(cls.astype(str))))

    # ---- punts ----------------------------------------------------------------
    punts = _scrimmage_outcomes(
        rows.filter(
            (pl.col("play_type") == "punt") & pl.col("yardline_100").is_not_null()
        ).with_columns(cls=pl.lit("punt"))
    ).select("ytg", "kind", "dy", "fd", "delta", "secs")

    # ---- field goals ------------------------------------------------------------
    fgs = rows.filter(pl.col("play_type") == "field_goal").select(
        (pl.col("yardline_100") + 18).cast(pl.Float64).alias("distance"),
        (pl.col("field_goal_result") == "made").cast(pl.Int8).alias("made"),
    )

    # ---- kickoffs: the receiving team is the row's posteam ------------------------
    ko = rows.filter(pl.col("play_type") == "kickoff")
    td = ko["touchdown"].fill_null(0).to_numpy() == 1
    recv_td = td & (ko["td_team"].to_numpy() == ko["posteam"].to_numpy())
    kick_td = td & ~recv_td
    onside = (~td) & (ko["n_pos"].to_numpy() == ko["defteam"].to_numpy())
    kind = np.where(
        recv_td,
        KICK_RETURN_TD,
        np.where(kick_td, KICK_TEAM_TD, np.where(onside, KICK_ONSIDE, KICK_RECEIVED)),
    )
    ytg = ko["n_ytg"].fill_null(70).to_numpy().astype(float)
    kickoffs = pl.DataFrame(
        {
            "season": ko["season"].to_numpy().astype(int),
            "week": ko["week"].to_numpy().astype(int),
            "kind": kind.astype(np.int8),
            "ytg": np.clip(ytg, 1, 99),
            "secs": ko["secs"].to_numpy().astype(float),
        }
    )

    # ---- tries after a touchdown, per season ----------------------------------
    tries = reg.with_columns(pl.col("two_point_attempt").fill_null(0))
    xp = tries.filter(pl.col("play_type") == "extra_point")
    two = tries.filter(pl.col("two_point_attempt") == 1)
    pats = (
        xp.group_by("season")
        .agg(
            pl.len().alias("xp_att"),
            (pl.col("extra_point_result") == "good").sum().alias("xp_good"),
        )
        .join(
            two.group_by("season").agg(
                pl.len().alias("two_att"),
                (pl.col("two_point_conv_result") == "success").sum().alias("two_good"),
            ),
            on="season",
            how="full",
            coalesce=True,
        )
        .fill_null(0)
        .sort("season")
    )
    return SimEvents(decisions, scrimmage, punts, fgs, kickoffs, pats)


def _scrimmage_outcomes(rows: pl.DataFrame) -> pl.DataFrame:
    """kind / dy / fd / delta for each run, pass, kneel, spike or punt, from the next snap."""
    n = rows.height
    pos = rows["posteam"].to_numpy()
    dfn = rows["defteam"].to_numpy()
    ytg = rows["yardline_100"].to_numpy().astype(float)
    n_pos = rows["n_pos"].to_numpy()
    n_ytg = rows["n_ytg"].to_numpy().astype(float)
    n_type = rows["n_type"].to_numpy()
    n_down = rows["n_down"].to_numpy().astype(float)
    gained = rows["yards_gained"].fill_null(0).to_numpy().astype(float)
    togo = rows["ydstogo"].fill_null(10).to_numpy().astype(float)
    td = rows["touchdown"].fill_null(0).to_numpy() == 1
    td_team = rows["td_team"].to_numpy()
    safety = rows["safety"].fill_null(0).to_numpy() == 1
    lost = (rows["interception"].fill_null(0).to_numpy() == 1) | (
        rows["fumble_lost"].fill_null(0).to_numpy() == 1
    )
    is_punt = rows["play_type"].to_numpy() == "punt"

    has_next = (n_type != None) & (n_type != "kickoff") & ~np.isnan(n_ytg)  # noqa: E711
    same = has_next & (n_pos == pos)
    other = has_next & (n_pos == dfn)

    kind = np.full(n, KIND_CONT, dtype=np.int8)
    dy = np.where(same, ytg - n_ytg, np.where(other, ytg - (100 - n_ytg), gained))
    fd = np.where(same, n_down == 1, gained >= togo)
    # The other side's next snap relative to the old line of scrimmage, seen
    # from their end: a 40-yard net punt from the punter's own 30 is +40.
    delta = np.where(other, n_ytg - (100 - ytg), 0.0)

    kind[td & (td_team == pos)] = KIND_TD_OFF
    kind[td & (td_team != pos)] = KIND_TD_DEF
    kind[~td & safety] = KIND_SAFETY
    change = ~td & ~safety & (lost | (is_punt & ~same))
    kind[change] = KIND_TURNOVER
    # A punt with no next snap (the half ran out) still changes possession,
    # at the league's typical net of 40 yards; a turnover then stays at the spot.
    delta = np.where(change & ~other & is_punt, 40.0, delta)

    return pl.DataFrame(
        {
            "cls": rows["cls"].to_numpy().astype(str),
            "down": rows["down"].fill_null(1).to_numpy().astype(np.int8),
            "togo": togo,
            "ytg": ytg,
            "clk": rows["clk"].to_numpy().astype(np.int8),
            "kind": kind,
            "dy": dy,
            "fd": fd.astype(bool),
            "delta": delta,
            "secs": rows["secs"].to_numpy().astype(float),
            "epa": rows["epa"].fill_null(0).to_numpy().astype(float),
        }
    )


# =============================================================================
# The backtest's states and the scores it is graded against
# =============================================================================


def snap_states(frame: pl.DataFrame) -> pl.DataFrame:
    """Every regulation snap as a simulator start state, home-oriented.

    `recv2` is the team that receives the second-half kickoff (+1 home): the
    one that did NOT receive the opening kickoff.
    """
    first_ko = (
        frame.filter(pl.col("play_type") == "kickoff")
        .group_by("game_id")
        .agg(pl.col("posteam").sort_by("play_id").first().alias("opening_recv"))
    )
    snaps = frame.filter(
        pl.col("play_type").is_in(SCRIMMAGE)
        & (pl.col("qtr") <= 4)
        & pl.col("down").is_not_null()
        & pl.col("yardline_100").is_not_null()
        & (pl.col("two_point_attempt").fill_null(0) == 0)
    ).join(first_ko, on="game_id", how="left")
    home_ball = snaps["posteam"] == snaps["home_team"]
    return pl.DataFrame(
        {
            "game_id": snaps["game_id"],
            "play_id": snaps["play_id"].cast(pl.Int64),
            "gsr": snaps["game_seconds_remaining"].cast(pl.Float64),
            "pos": np.where(home_ball.to_numpy(), 1, -1).astype(np.int8),
            "down": snaps["down"].cast(pl.Int8),
            "togo": snaps["ydstogo"].cast(pl.Float64),
            "ytg": snaps["yardline_100"].cast(pl.Float64),
            "home_before": np.where(
                home_ball.to_numpy(),
                snaps["posteam_score"].to_numpy(),
                snaps["defteam_score"].to_numpy(),
            ),
            "away_before": np.where(
                home_ball.to_numpy(),
                snaps["defteam_score"].to_numpy(),
                snaps["posteam_score"].to_numpy(),
            ),
            "recv2": np.where(
                (snaps["opening_recv"] == snaps["home_team"]).to_numpy(), -1, 1
            ).astype(np.int8),
        }
    )


def period_scores(frame: pl.DataFrame) -> pl.DataFrame:
    """Home and away points at the end of Q1, Q2 (the half), Q3 and regulation, per game."""
    out = []
    for q in (1, 2, 3, 4):
        last = (
            frame.filter(pl.col("qtr") <= q)
            .group_by("game_id")
            .agg(
                pl.col("total_home_score").sort_by("play_id").last().alias(f"h{q}"),
                pl.col("total_away_score").sort_by("play_id").last().alias(f"a{q}"),
            )
        )
        out.append(last)
    merged = out[0]
    for o in out[1:]:
        merged = merged.join(o, on="game_id", how="full", coalesce=True)
    return merged
