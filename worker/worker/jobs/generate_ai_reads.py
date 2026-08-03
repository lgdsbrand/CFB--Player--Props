"""Phase 5b job: write the weekly cached AI read, one row per player.

    python -m worker.jobs.generate_ai_reads --season 2025 --week 11
    python -m worker.jobs.generate_ai_reads --season 2025 --week 11 --dry-run
    python -m worker.jobs.generate_ai_reads --season 2025 --week 11 --limit 3

THE CACHE IS THE SCHEMA. `ai_reads` is unique on (player_id, season, week), and
the application only ever SELECTs from it (CLAUDE.md §2, §10 — per-page-view LLM
calls are out of scope). This job is the only writer.

WHAT IT REFUSES TO SPEND. A busy week has ~1,700 players with projections, and
this runs against the client's account, so:

  * `app_config.ai_reads_max_per_run` is a hard ceiling. Stopping early is
    recoverable; a surprise invoice is not.
  * A player whose inputs have not moved is SKIPPED, not regenerated.
    `ai_reads.input_digest` is what makes that decidable — same facts and same
    prompt version means the stored read is still the right read.
  * `--dry-run` builds every prompt and reports what it would spend without
    calling the provider once.

WHAT IT REFUSES TO STORE. A truncated read, an empty read, or one the provider
declined. Each is recorded as a failure the next run retries. The unique key
means a bad row would sit in front of readers for a week, so writing nothing is
strictly better than writing a fragment.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from worker.adapters.ai import (
    AiAdapterError,
    AiAuthError,
    AiRateLimitError,
    AiSafetyRefusal,
    get_adapter,
)
from worker.adapters.ai.gemini import ADAPTER_NAME as GEMINI_ADAPTER_NAME
from worker.adapters.ai.grok import ADAPTER_NAME as GROK_ADAPTER_NAME
from worker.adapters.ai.null import ADAPTER_NAME as NULL_ADAPTER_NAME
from worker.config import ConfigError, get_settings
from worker.core.ai_prompt import (
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    MarketLine,
    PromptInputs,
    build_prompt,
    input_digest,
)
from worker.core.splits import RANK_METRICS
from worker.db import connect, get_config_value, pipeline_run, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "generate_ai_reads"

# How many past games of the headline stat to show. Matches the L5 window the
# board's hit-rate row already uses, so the prose and the page cite the same
# games (CLAUDE.md §7).
RECENT_GAMES = 5

# What each position's defensive rank is BUILT from. Mirrors RANK_METRICS in
# splits.py and rankBasis() in web/lib/core/defense-view.ts — three statements
# of one fact, which is two too many, but the alternative is the web layer
# importing Python.
_BASIS_LABEL = {
    "adj_rush_yards_allowed_pg": "opponent-adjusted rushing yards allowed",
    "adj_rec_yards_allowed_pg": "opponent-adjusted receiving yards allowed",
}

# The quarterback rank is rushing BY NECESSITY: the QB is the only passer, so
# "pass yards allowed to QBs" is just team pass defence — the single number the
# position split exists to break apart. A read that leans on a QB rank to
# explain a passing market is therefore citing an irrelevant number, so the
# caveat travels with the rank.
QB_RANK_CAVEAT = (
    "this rank is rushing only - the position split cannot measure passing, so "
    "it says nothing about pass yards, completions or attempts"
)


@dataclass
class ReadReport:
    players: int = 0
    generated: int = 0
    skipped_cached: int = 0
    skipped_low_confidence: int = 0
    failed: Counter = field(default_factory=Counter)
    tokens_in: int = 0
    tokens_out: int = 0
    stopped_at_cap: bool = False
    remaining: int = 0

    def render(self) -> str:
        lines = [
            f"  players considered: {self.players}",
            f"  generated: {self.generated}   "
            f"skipped (inputs unchanged): {self.skipped_cached}   "
            f"skipped (below confidence floor): {self.skipped_low_confidence}",
            f"  tokens: in={self.tokens_in:,} out={self.tokens_out:,}",
        ]
        if self.failed:
            lines.append(f"  FAILED: {dict(self.failed)}")
        if self.stopped_at_cap:
            lines.append(
                f"  STOPPED AT THE PER-RUN CAP with {self.remaining} player(s) "
                "left. Re-run to continue — cached players are skipped, so a "
                "second run costs only what it generates."
            )
        return "\n".join(lines)


def _resolve_adapter_name(explicit: str | None) -> str:
    if explicit:
        return explicit
    configured = get_config_value("ai_adapter")
    return str(configured) if configured else NULL_ADAPTER_NAME


def _adapter_kwargs(name: str, settings) -> dict:
    """Pick the key for the chosen provider, and say so plainly if it is unset."""
    if name == GEMINI_ADAPTER_NAME:
        if not settings.gemini_api_key:
            raise ConfigError(
                "app_config.ai_adapter is 'gemini' but GEMINI_API_KEY is unset. "
                "Add it to .env — never to app_config, which is world-readable."
            )
        return {"api_key": settings.gemini_api_key}
    if name == GROK_ADAPTER_NAME:
        if not settings.grok_api_key:
            raise ConfigError(
                "app_config.ai_adapter is 'grok' but GROK_API_KEY is unset. "
                "Add it to .env — never to app_config, which is world-readable."
            )
        return {"api_key": settings.grok_api_key}
    return {}


def load_board(conn, season: int, week: int) -> dict[int, list[dict]]:
    """Every board row for the week, grouped by player."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select b.*, m.stat_column
              from v_board_rows b
              join markets m on m.key = b.market_key
             where b.season = %s and b.week = %s
             order by b.player_id, b.confidence desc nulls last
            """,
            (season, week),
        )
        grouped: dict[int, list[dict]] = defaultdict(list)
        for row in cur.fetchall():
            grouped[int(row["player_id"])].append(row)
        return grouped


def load_rank_field_sizes(conn, season: int, week: int) -> dict[str, int]:
    """How many defences carry a rank for each position at this cutoff.

    Needed so the prompt can say "of N" — a rank with no field size cannot be
    turned into a matchup verdict, and the verdict is the whole point.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select position_group, max(rank_vs_position) as field
              from defense_position_ratings
             where season = %s and as_of_week = %s
             group by position_group
            """,
            (season, week),
        )
        return {r["position_group"]: int(r["field"]) for r in cur.fetchall()}


def load_recent(conn, season: int, week: int, stat: str, player_ids: list[int]):
    """Last N values of one stat per player, strictly before this week."""
    if not player_ids or not stat.isidentifier():
        return {}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            select player_id, {stat} as value
              from player_game_stats
             where season = %s and week < %s and player_id = any(%s)
               and {stat} is not null
             order by player_id, week
            """,
            (season, week, player_ids),
        )
        out: dict[int, list[float]] = defaultdict(list)
        for row in cur.fetchall():
            out[int(row["player_id"])].append(float(row["value"]))
        return {pid: vals[-RECENT_GAMES:] for pid, vals in out.items()}


def build_inputs(rows: list[dict], field_size: int | None, recent: list[float],
                 recent_tds: list[float] | None = None):
    """Turn a player's board rows into the facts a read may use."""
    head = rows[0]
    position = str(head.get("position_group") or "")
    metric = RANK_METRICS.get(position)
    basis = _BASIS_LABEL.get(metric or "")
    if basis:
        basis = f"{basis} to {position}s"

    markets = tuple(
        MarketLine(
            market_label=str(r.get("market_label") or r.get("market_name") or ""),
            side=str(r.get("side") or ""),
            confidence=float(r["confidence"]),
            line=float(r["line"]) if r.get("line") is not None else None,
            has_book_line=bool(r.get("has_book_line")),
            projected_median=_f(r.get("projected_median")),
            projected_p10=_f(r.get("projected_p10")),
            projected_p90=_f(r.get("projected_p90")),
            edge=_f(r.get("edge")),
            is_binary=bool(r.get("is_binary")),
        )
        for r in rows
        if r.get("confidence") is not None and r.get("side")
    )

    return PromptInputs(
        player_name=str(head.get("player_name") or ""),
        position_group=position,
        team=str(head.get("team_school") or ""),
        opponent=str(head.get("opponent_school") or ""),
        season=int(head["season"]),
        week=int(head["week"]),
        is_home=bool(head.get("is_home")),
        neutral_site=bool(head.get("neutral_site")),
        markets=markets,
        opponent_rank=(
            int(head["opponent_rank_vs_position"])
            if head.get("opponent_rank_vs_position") is not None
            else None
        ),
        ranked_defenses=field_size,
        rank_basis_label=basis,
        rank_caveat=QB_RANK_CAVEAT if position == "QB" else None,
        recent_stat_label=str(head.get("market_label") or "").lower() or None,
        recent_values=tuple(recent),
        recent_td_counts=tuple(recent_tds or ()),
        prior_weight=_f(head.get("prior_weight")),
    )


def _f(value) -> float | None:
    return None if value is None else float(value)


def run(*, season: int, week: int, adapter_name: str, dry_run: bool,
        limit: int | None) -> ReadReport:
    report = ReadReport()
    settings = get_settings()
    adapter = get_adapter(adapter_name, **_adapter_kwargs(adapter_name, settings))

    cap = limit or int(get_config_value("ai_reads_max_per_run") or 400)
    floor = float(get_config_value("ai_reads_min_confidence") or 0.0)

    with connect() as conn:
        board = load_board(conn, season, week)
        if not board:
            log.warning(
                "No board rows for %s week %s — run run_projections first.",
                season, week,
            )
            return report
        report.players = len(board)

        field_sizes = load_rank_field_sizes(conn, season, week)

        # Recent form is keyed on each player's HEADLINE market, so it is
        # fetched per stat column rather than per player.
        by_stat: dict[str, list[int]] = defaultdict(list)
        for player_id, rows in board.items():
            by_stat[str(rows[0]["stat_column"])].append(player_id)
        recent: dict[int, list[float]] = {}
        for stat, ids in by_stat.items():
            recent.update(load_recent(conn, season, week, stat, ids))

        # Touchdown history for anyone carrying an anytime-TD call. Fetched
        # because the model will describe touchdowns whether or not we supply
        # them (it did, twice, from nothing), so the only real choice is
        # between a grounded number and an invented one.
        td_players = [
            pid for pid, rows in board.items()
            if any(r["market_key"] == "anytime_td" for r in rows)
        ]
        recent_tds = load_recent(
            conn, season, week, "offensive_tds", td_players
        )

        with conn.cursor() as cur:
            cur.execute(
                "select player_id, input_digest, prompt_version from ai_reads "
                " where season = %s and week = %s",
                (season, week),
            )
            existing = {
                int(r["player_id"]): (r["input_digest"], r["prompt_version"])
                for r in cur.fetchall()
            }

        pending = sorted(board)
        for index, player_id in enumerate(pending):
            rows = board[player_id]
            best = max(
                (float(r["confidence"]) for r in rows
                 if r.get("confidence") is not None),
                default=0.0,
            )
            if best < floor:
                report.skipped_low_confidence += 1
                continue

            inputs = build_inputs(
                rows, field_sizes.get(str(rows[0].get("position_group") or "")),
                recent.get(player_id, []), recent_tds.get(player_id, []),
            )
            if not inputs.markets:
                continue

            digest = input_digest(inputs)
            was = existing.get(player_id)
            # BOTH have to match. The digest says the facts are unchanged; the
            # version says we have not changed what we ask for. Either moving is
            # a reason to redo the read.
            if was and was[0] == digest and was[1] == PROMPT_VERSION:
                report.skipped_cached += 1
                continue

            if report.generated >= cap:
                report.stopped_at_cap = True
                report.remaining = len(pending) - index
                break

            prompt = build_prompt(inputs)
            if dry_run:
                report.generated += 1
                report.tokens_in += len(prompt) // 4  # rough, for budgeting only
                continue

            try:
                result = adapter.generate(prompt, max_output_tokens=MAX_OUTPUT_TOKENS)
            except AiRateLimitError as exc:
                log.error("Rate limited after %d read(s); stopping. %s",
                          report.generated, exc)
                report.stopped_at_cap = True
                report.remaining = len(pending) - index
                break
            except AiAuthError as exc:
                log.error("%s", exc)
                raise
            except AiSafetyRefusal as exc:
                report.failed["safety_refusal"] += 1
                log.warning("Player %s: %s", player_id, exc)
                continue
            except AiAdapterError as exc:
                report.failed["provider_error"] += 1
                log.warning("Player %s: %s", player_id, exc)
                continue

            if result.is_empty:
                report.failed["empty"] += 1
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into ai_reads
                      (player_id, season, week, content, model, prompt_version,
                       input_digest, tokens_in, tokens_out)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (player_id, season, week) do update set
                      content = excluded.content,
                      model = excluded.model,
                      prompt_version = excluded.prompt_version,
                      input_digest = excluded.input_digest,
                      tokens_in = excluded.tokens_in,
                      tokens_out = excluded.tokens_out,
                      generated_at = now()
                    """,
                    (player_id, season, week, result.text, result.model,
                     PROMPT_VERSION, digest, result.tokens_in, result.tokens_out),
                )
            conn.commit()
            report.generated += 1
            report.tokens_in += result.tokens_in or 0
            report.tokens_out += result.tokens_out or 0

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--adapter", help="Override app_config.ai_adapter.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build every prompt and report the plan without calling anyone.",
    )
    parser.add_argument(
        "--limit", type=int,
        help="Generate at most N reads this run (below the configured cap).",
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    adapter_name = _resolve_adapter_name(args.adapter)
    if adapter_name == NULL_ADAPTER_NAME:
        # A DECISION, NOT A FAILURE — exit 0 so the weekly cron stays green
        # while the reads are deliberately switched off.
        log.info(
            "app_config.ai_adapter is %r — no reads will be generated and the "
            "player page shows its empty read slot. Set it to %r to generate.",
            NULL_ADAPTER_NAME, GEMINI_ADAPTER_NAME,
        )
        return 0

    try:
        with pipeline_run(
            JOB_NAME, metadata={"season": args.season, "week": args.week}
        ) as run_id:
            report = run(
                season=args.season, week=args.week, adapter_name=adapter_name,
                dry_run=args.dry_run, limit=args.limit,
            )
            log.info(
                "AI reads (%s%s):\n%s",
                adapter_name, ", DRY RUN" if args.dry_run else "", report.render(),
            )
            set_rows_written(run_id, report.generated)
    except (ConfigError, AiAdapterError) as exc:
        log.error("%s", exc)
        return 1
    except Exception as exc:
        log.error("AI read generation failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
