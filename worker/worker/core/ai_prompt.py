"""Build the weekly read's prompt, and the digest that decides when to redo it.

Pure and database-free, for the same reason `name_match` is: the only
interesting property of a prompt is what it does and does not permit the model
to say, and that has to be assertable in a test rather than judged by reading
generated samples.

THE GOVERNING CONSTRAINT. The read sits on the player page beside the board's
own numbers. If it makes its own call, it will eventually disagree with the
OVER/UNDER pill directly above it, and a reader has no way to know which one the
product means. So the model is never asked what it thinks — it is handed the
call, the confidence and the inputs behind them, and asked to explain them in
prose. That is a narrower job than it sounds, and it is the whole job.

WHAT MAY GO IN. Only facts the application already displays: the call, the
confidence, the line, the projected range, the opponent's rank against this
position and what that rank was built from, and recent form. Nothing is fetched
specially for the read, so a read can never reference something a reader cannot
go and check.

THE DIGEST. `ai_reads.input_digest` is a hash of exactly these inputs. It is
what lets the weekly job skip a player whose situation has not moved and redo
one whose line or projection has, without either regenerating everything or
serving something stale. It therefore has to be computed from the SAME structure
the prompt is built from — a digest over different inputs than the prompt would
silently authorise a stale read.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

__all__ = [
    "PROMPT_VERSION",
    "MarketLine",
    "PromptInputs",
    "build_prompt",
    "describe_matchup",
    "input_digest",
]

# Bump when the wording or the rules change. Stored on every row, so a read
# generated under old instructions is identifiable and re-generatable. It is NOT
# part of the digest: the digest answers "have the facts moved", this answers
# "have we changed what we ask for", and the job checks both.
# v2 (2026-08-03): forbade back-inferring history from a confidence. v1 produced
# "Brown recording zero touchdowns over his last five games" and "he has not
# scored in his recent games" — in two of the first three real reads — from
# nothing but a high UNDER confidence on anytime touchdown. Both claims may even
# have been true, which is what makes it dangerous: it reads as data, and a
# reader has no way to tell it was inferred.
#
# v3 (2026-08-03): the v2 prohibition DID NOT HOLD — both inventions came back
# on the very next run. Two changes, neither of them a firmer instruction:
# touchdown history is now SUPPLIED, so the claim the model insists on making
# is grounded and checkable; and binary markets no longer carry a projection,
# after v2 rendered anytime TD as "an expected touchdown projection of -0.0".
#
# The lesson is the one this project keeps relearning: when a model reaches for
# something, giving it the true value beats forbidding the reach.
PROMPT_VERSION = "v3"

# The reads are two or three sentences. The ceiling is generous relative to that
# because a truncated read is refused outright rather than stored, and the cost
# of a slightly larger ceiling is nothing next to the cost of losing a row.
MAX_OUTPUT_TOKENS = 400


@dataclass(frozen=True)
class MarketLine:
    """One market's call for this player, exactly as the board shows it."""

    market_label: str
    side: str                      # OVER or UNDER
    confidence: float              # probability mass on the called side
    line: float | None
    has_book_line: bool
    projected_median: float | None = None
    projected_p10: float | None = None
    projected_p90: float | None = None
    edge: float | None = None

    # Anytime touchdown is a PROBABILITY OF SCORING, never a projected count
    # (CLAUDE.md §1, §6). Its stored median is an internal quantity that means
    # nothing to a reader, and v1 of this prompt handed it over: Gemini wrote
    # "an expected touchdown projection of -0.0". A negative zero, on a page
    # whose whole claim is that it does not show projected counts for this
    # market. Binary markets therefore surrender their projection entirely and
    # are rendered as the probability they are.
    is_binary: bool = False


@dataclass(frozen=True)
class PromptInputs:
    """Everything the read is allowed to know."""

    player_name: str
    position_group: str
    team: str
    opponent: str
    season: int
    week: int
    is_home: bool
    neutral_site: bool = False

    markets: tuple[MarketLine, ...] = ()

    # Opponent strength against this position. `rank_basis_label` names the stat
    # the rank was built from, because a rank with no stated basis invites the
    # model to assume it covers everything.
    opponent_rank: int | None = None
    ranked_defenses: int | None = None
    rank_basis_label: str | None = None
    rank_caveat: str | None = None

    recent_stat_label: str | None = None
    recent_values: tuple[float, ...] = ()

    # Touchdowns scored in recent games. Supplied SEPARATELY because the model
    # kept reaching for it: told only that anytime-TD confidence was 94% UNDER,
    # v1 and v2 both wrote "recording zero touchdowns over his last five games"
    # from nothing. Forbidding the claim twice did not stop it. Giving it the
    # real series does — and it is a fact the player page can show, so the
    # prose stays checkable.
    recent_td_counts: tuple[float, ...] = ()

    prior_weight: float | None = None


def build_prompt(inputs: PromptInputs) -> str:
    """Render the prompt. Deterministic — same inputs, same string."""
    venue = (
        "at a neutral site"
        if inputs.neutral_site
        else ("at home" if inputs.is_home else "on the road")
    )

    lines: list[str] = [
        "You are writing a short analyst's note for a college football player "
        "prop tool. It appears directly beneath the numbers below, which the "
        "reader can already see.",
        "",
        "RULES:",
        "- Explain the model's call. Do NOT make your own call, and never "
        "contradict the OVER/UNDER or the confidence given below.",
        "- Use only the facts provided. Do not invent statistics, injuries, "
        "depth-chart news, weather or history. You have no information beyond "
        "this message.",
        "- The RECENT lines below are the ONLY past performance you have. "
        "Never state or imply any other historical fact - not games missed, "
        "not last season, not career numbers, and no statistic that is not "
        "printed below.",
        "- A confidence is the model's ESTIMATE of what will happen, never "
        "evidence about what already has. Do not turn a confidence back into "
        "a history: cite touchdowns only from the RECENT TOUCHDOWNS line, and "
        "if that line is absent, say nothing about touchdowns scored.",
        "- 2-3 sentences, plain prose, no headings, no bullet points, no "
        "preamble, no markdown.",
        "- Write about the matchup and the usage, not about betting. Never "
        "advise staking, bankroll or units.",
        "- Refer to a confidence as a percentage, not as a certainty.",
        "",
        f"PLAYER: {inputs.player_name} ({inputs.position_group}), "
        f"{inputs.team}, facing {inputs.opponent} {venue} "
        f"in week {inputs.week} of {inputs.season}.",
    ]

    if inputs.markets:
        lines.append("")
        lines.append("THE MODEL'S CALLS:")
        for market in inputs.markets:
            lines.append(f"- {_render_market(market)}")

    if inputs.opponent_rank is not None:
        basis = inputs.rank_basis_label or "this position group"
        lines.append("")
        lines.append(
            f"OPPONENT DEFENCE: against {basis}, {inputs.opponent} is "
            f"{describe_matchup(inputs.opponent_rank, inputs.ranked_defenses)}."
        )
        if inputs.rank_caveat:
            lines.append(f"  Caveat you must respect: {inputs.rank_caveat}")

    if inputs.recent_values:
        shown = ", ".join(_number(v) for v in inputs.recent_values)
        label = inputs.recent_stat_label or "recent production"
        lines.append("")
        lines.append(f"RECENT FORM ({label}, most recent last): {shown}")

    if inputs.recent_td_counts:
        tds = ", ".join(_number(v) for v in inputs.recent_td_counts)
        total = int(sum(inputs.recent_td_counts))
        lines.append(
            f"RECENT TOUCHDOWNS (most recent last): {tds} "
            f"- {total} in these {len(inputs.recent_td_counts)} game(s)"
        )

    # Early-season projections lean on prior-year data that often happened at
    # another school (CLAUDE.md §6). Saying so is more honest than a confident
    # read built on three games.
    if inputs.prior_weight is not None and inputs.prior_weight >= 0.25:
        lines.append("")
        lines.append(
            f"UNCERTAINTY: {inputs.prior_weight:.0%} of this projection still "
            "comes from prior-season data, which in college football often "
            "happened at a different school. Say the projection is early and "
            "uncertain."
        )

    lines.append("")
    lines.append("Write the note now.")
    return "\n".join(lines)


def describe_matchup(rank: int, ranked_defenses: int | None) -> str:
    """State what a defensive rank MEANS, so the model never has to work it out.

    `rank_vs_position` now counts **1 = the BEST defence**, the conventional
    reading. It previously counted the other way, and handing a model the raw
    number under that convention produced "a favorable ground matchup against
    an Ohio State defense that ranks 118th of 136" — exactly backwards, with the
    rule spelled out in capitals immediately above.

    Fixing the convention removes most of that risk, but this function stays,
    because the residual risk is the same shape: turning a rank into a verdict
    is an inference, and an inference a model makes is one it can make wrongly
    while reading perfectly fluently. We do the arithmetic; the model repeats a
    conclusion.

    The raw rank is still included, because the page shows it and the prose
    should agree with the page.
    """
    if not ranked_defenses or ranked_defenses < 2:
        return f"ranked {rank} against this position"

    # Share of defences that give up LESS than this one. Rank 1 gives up the
    # least, so a high rank means a lot of defences are stingier.
    stingier = (rank - 1) / (ranked_defenses - 1)
    if stingier >= 2 / 3:
        verdict = (
            "a SOFT matchup - it gives up more than most defences do, so the "
            "matchup argues FOR production here"
        )
    elif stingier <= 1 / 3:
        verdict = (
            "a HARD matchup - it gives up less than most defences do, so the "
            "matchup argues AGAINST production here"
        )
    else:
        verdict = "an AVERAGE matchup - it gives up about what a typical defence does"

    return (
        f"{verdict} (national rank {rank} of {ranked_defenses}, where 1 is the "
        f"BEST defence against this position)"
    )


def _render_market(market: MarketLine) -> str:
    if market.is_binary:
        # Stated as the probability it is. "OVER 0.5 touchdowns" is the
        # internal encoding, not the claim the product makes.
        scores = market.side.upper() == "OVER"
        chance = market.confidence if scores else 1.0 - market.confidence
        parts = [
            f"{market.market_label}: {chance:.0%} chance to score, so the call "
            f"is {'YES' if scores else 'NO'} at {market.confidence:.0%} "
            f"confidence"
        ]
        if market.has_book_line and market.edge is not None:
            parts.append(f"- edge {market.edge:+.1%} vs the book's implied price")
        elif not market.has_book_line:
            parts.append("- NO BOOK HAS POSTED THIS YET; model lean only")
        return " ".join(parts)

    parts = [f"{market.market_label}: {market.side}"]
    if market.line is not None:
        parts.append(f"{_number(market.line)}")
    parts.append(f"({market.confidence:.0%} confidence)")

    if not market.has_book_line:
        # The board shows model leans before books post (CLAUDE.md §7). The read
        # must not imply a market exists when none does.
        parts.append(
            "- NO BOOK HAS POSTED THIS LINE YET; this is the model's lean "
            "against a reference number, so do not call it a market price"
        )
    elif market.edge is not None:
        parts.append(f"- edge {market.edge:+.1%} vs the book's implied price")

    if market.projected_median is not None:
        span = ""
        if market.projected_p10 is not None and market.projected_p90 is not None:
            span = (
                f", likely range {_number(market.projected_p10)}"
                f"-{_number(market.projected_p90)}"
            )
        parts.append(f"- projected {_number(market.projected_median)}{span}")

    return " ".join(parts)


def _number(value: float) -> str:
    """Render a number the way the app does, so prose and page agree."""
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def input_digest(inputs: PromptInputs) -> str:
    """Stable hash of the facts a read was generated from.

    Canonical JSON with sorted keys, and floats rounded before hashing: a
    projection that moves by 1e-12 between pipeline runs is not a change worth
    spending a generation on, and without rounding it would look like one every
    single week.
    """
    payload = _canonical(asdict(inputs))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical(value: object) -> object:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value
